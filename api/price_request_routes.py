"""Dashboard queue and narrowly scoped Aside worker API.
Importing this module never runs a scraper or sends mail. Register explicitly.
"""
from __future__ import annotations
from functools import wraps
import hashlib
import json
from datetime import datetime
from flask import Blueprint, jsonify, request, send_file
from io import BytesIO
from api.auth import require_auth
from api.price_request_ingest import prepare_a8, prepare_oecd
from agents.price_request_catalog import CATALOG
from agents.price_request_store import PriceRequestStore, Conflict, dump


def build_price_request_blueprint(db_path, foreign_agent):
    bp = Blueprint("price_requests", __name__)
    store = PriceRequestStore(db_path)

    def guarded(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except KeyError:
                return jsonify(error="request not found"), 404
            except Conflict as e:
                return jsonify(error=str(e)), 409
            except (ValueError, TypeError) as e:
                return jsonify(error=str(e)), 400
        return wrapped

    def actor():
        return request.user["sub"]

    def body():
        if request.content_length and request.content_length > 2_000_000:
            raise ValueError("payload too large")
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError("JSON object required")
        return data

    def lease():
        # A short-lived fencing token, distinct from the login JWT.
        return request.headers.get("X-Price-Lease", "")

    def authorized_detail(rid):
        r = store.detail(rid)
        if request.user["role"] != "admin" and r["requester"] != actor():
            raise KeyError(rid)
        return r

    def worker_detail(rid):
        with store.connect() as c:
            store.owned(c, rid, actor(), lease())
        return store.detail(rid)

    def readback(r):
        if r["scope"] == "a8":
            # Same DB/cache reader as the dashboard, not a second shadow store.
            by_country = foreign_agent.get_cached_results(r["query"])
            rows = [x for country in by_country.values() for x in country]
        else:
            rows = store.oecd_prices(r["query"])
        ids = {x.get("id") for x in rows}
        expected = {i for country in r["country_results"] for i in country["row_ids"]}
        return rows, sorted(ids & expected), sorted(expected - ids)

    @bp.get("/api/price-requests/catalog")
    @require_auth()
    def catalog():
        return jsonify(CATALOG)

    @bp.get("/api/price-requests/worker-status")
    @require_auth()
    def worker_status():
        # The dashboard only queues work. Show whether an Aside worker is actually polling.
        return jsonify(worker=store.worker_status())

    @bp.post("/api/price-requests")
    @require_auth()
    @guarded
    def create():
        b = body()
        scope = b.get("scope", "a8")
        identity = b.get("identity")
        if identity is not None:
            if not isinstance(identity, dict) or any(not isinstance(identity.get(k), str) or len(identity[k]) > 200 for k in ("slug", "brand", "inn")):
                raise ValueError("invalid identity")
        query = b.get("query", "")
        if not isinstance(query, str):
            raise ValueError("invalid query")
        if scope == "a8":
            from agents.drug_identity import derive_identity_offline
            ident = derive_identity_offline(query)
            # Only server-owned alias resolution changes the canonical cache key.
            if ident:
                query, identity = ident["slug"], ident
        r = store.create(query, scope, b.get("countries"), b.get("notify_email"), actor(), identity)
        return jsonify(request=r), 201

    @bp.get("/api/price-requests")
    @require_auth()
    @guarded
    def listing():
        return jsonify(requests=store.list(actor=None if request.user["role"] == "admin" else actor(), scope=request.args.get("scope"), query=request.args.get("q")))

    @bp.get("/api/price-requests/<rid>")
    @require_auth()
    @guarded
    def detail(rid):
        return jsonify(request=authorized_detail(rid))

    def excel_response(data):
        from agents.price_request_export import generate_xlsx, MIME
        content, filename = generate_xlsx(data)
        response = send_file(BytesIO(content), mimetype=MIME, as_attachment=True, download_name=filename, max_age=0)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @bp.get("/api/price-requests/<rid>/export.xlsx")
    @require_auth()
    @guarded
    def request_export(rid):
        authorized_detail(rid)
        data = store.export_data(rid)
        if not any(x["status"] != "pending" for x in data["country_results"]):
            raise Conflict("no country has been checked yet")
        return excel_response(data)

    @bp.get("/api/price-history/export.xlsx")
    @require_auth()
    @guarded
    def history_export():
        scope, q = request.args.get("scope", "a8"), request.args.get("q", "").strip().lower()
        if scope not in ("a8", "oecd") or not q or len(q) > 160:
            raise ValueError("valid scope and query q required")
        if scope == "a8":
            by_country = foreign_agent.get_cached_results(q)
            rows = [row for group in by_country.values() for row in group]
        else:
            rows = store.oecd_prices(q)
        if not rows:
            return jsonify(error="no saved price rows for this search"), 404
        countries = []
        for cc in sorted({row["country"] for row in rows}):
            group = [row for row in rows if row["country"] == cc]
            countries.append({"country": cc, "status": "priced" if any(row.get("local_price") is not None and row["local_price"] > 0 for row in group) else "unconfirmed",
                              "reason": "현재 저장된 검색 캐시입니다. 특정 과거 요청 당시 결과는 해당 요청의 Excel을 사용하세요.",
                              "row_ids": [row["id"] for row in group], "updated_at": None})
        data = {"request": {"query": q, "scope": scope}, "rows": rows, "country_results": countries,
                "snapshot_kind": "current_cache", "missing_row_ids": []}
        return excel_response(data)

    @bp.get("/api/oecd/prices")
    @require_auth()
    @guarded
    def oecd_prices():
        q = request.args.get("q", "").strip().lower()
        if not q or len(q) > 160:
            raise ValueError("query q required")
        return jsonify(query=q, results=store.oecd_prices(q))

    @bp.get("/api/price-agent/requests")
    @require_auth(role="price_agent")
    def queue():
        store.touch_worker(actor(), "queue")
        return jsonify(requests=store.list(queue=True))

    @bp.post("/api/price-agent/requests/<rid>/claim")
    @require_auth(role="price_agent")
    @guarded
    def claim(rid):
        store.touch_worker(actor(), "claim")
        return jsonify(store.claim(rid, actor()))

    @bp.post("/api/price-agent/requests/<rid>/heartbeat")
    @require_auth(role="price_agent")
    @guarded
    def heartbeat(rid):
        store.touch_worker(actor(), "heartbeat")
        return jsonify(store.renew(rid, actor(), lease()))

    @bp.get("/api/price-agent/requests/<rid>/readback")
    @require_auth(role="price_agent")
    @guarded
    def get_readback(rid):
        r = worker_detail(rid)
        rows, ids, missing = readback(r)
        return jsonify(request=r, results=rows, visible_row_ids=ids, missing_row_ids=missing)

    @bp.put("/api/price-agent/requests/<rid>/countries/<cc>")
    @require_auth(role="price_agent")
    @guarded
    def upload(rid, cc):
        r, b = worker_detail(rid), body()
        if cc not in r["countries"]:
            raise ValueError("country not requested")
        rows = b.get("rows", [])
        if not isinstance(rows, list) or len(rows) > 250:
            raise ValueError("rows must be a list with <=250 prices")
        status = b.get("status")
        reason = b.get("reason")
        if reason is not None and (not isinstance(reason, str) or len(reason) > 4000):
            raise ValueError("invalid reason")
        payload_hash = hashlib.sha256(dump(b).encode()).hexdigest()
        # Return replay before FX recalculation (e.g. a retry across midnight).
        with store.connect() as c:
            cr = c.execute("SELECT payload_hash FROM price_request_countries WHERE request_id=? AND country=?", (rid, cc)).fetchone()
            if cr and cr[0] == payload_hash:
                return jsonify(request=r)
        prepared = []
        stamp = datetime.now().isoformat(timespec="microseconds")
        for row in rows:
            if r["scope"] == "a8":
                item = prepare_a8(row, cc, r["query"], rid, foreign_agent)
                item["searched_at"] = stamp  # all packs/channels one country = one session
            else:
                item = prepare_oecd(row, cc)
                # Retrieval session and actual source observation remain distinct.
                item["_session_at"] = stamp
            prepared.append(item)
        out = store.accept(rid, cc, actor(), lease(), status, reason, prepared, payload_hash)
        return jsonify(request=out)

    @bp.post("/api/price-agent/requests/<rid>/verify")
    @require_auth(role="price_agent")
    @guarded
    def verify(rid):
        r, b = worker_detail(rid), body()
        rows, ids, missing = readback(r)
        if missing:
            raise Conflict("uploaded prices are not visible through dashboard cache reader")
        if b.get("visible_row_ids") != ids:
            raise Conflict("UI verification IDs do not match current dashboard readback")
        if b.get("ui_checked") is not True:
            raise ValueError("visual dashboard check required, API readback alone is insufficient")
        return jsonify(request=store.verify(rid, actor(), lease(), ids, b.get("evidence_ref"), b.get("dashboard_url"),
                                           snapshot_rows=[row for row in rows if row.get("id") in set(ids)]))

    @bp.post("/api/price-agent/requests/<rid>/notification")
    @require_auth(role="price_agent")
    @guarded
    def notification(rid):
        b = body()
        r = worker_detail(rid)
        if b.get("phase") == "not_sent" and b.get("human_confirmed") is not True:
            raise ValueError("operator confirmation required before retrying an ambiguous send")
        if b.get("phase") == "begin":
            _, _, missing = readback(r)
            if missing:
                raise Conflict("dashboard changed after verification; email blocked")
        return jsonify(request=store.notification(rid, actor(), lease(), b.get("phase"), b.get("receipt")))

    return bp
