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
from api.price_request_ingest import prepare_a8, prepare_oecd, de_rows_from_scraper_items, rows_from_scraper_records
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

    @bp.get("/api/foreign/price-history")
    @require_auth()
    @guarded
    def foreign_price_history():
        """제품의 국가별 가격 수집 이력(현재+보관 세션, 변경 유형 포함). 읽기 전용 — 2026-11-03."""
        q = request.args.get("q", "").strip().lower()
        if not q or len(q) > 160:
            raise ValueError("query q required")
        return jsonify(foreign_agent.db.get_foreign_price_history(q))

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

    # ── 독일 서버 수집 (2026-11-06 사용자 결정) ─────────────────────────────────────────────────────────────
    # 워커 브라우저에서 DocCheck/Rote Liste 가 막히면(이 PC 네트워크 차단 이력) 운영 서버의 Rote Liste 수집기를 돌린다.
    # 이 API 는 저장하지 않는다 — 업로드용 행을 돌려줄 뿐이고, 워커가 확인 후 기존 upload 로 올린다(같은 검증 경로).
    # lease 필수, DE 만 허용. 수집은 백그라운드 스레드(로그인 포함 수십 초) → POST 로 시작, GET 으로 결과 조회.
    _collect_jobs: dict = {}

    def _run_de_collect(query, job):
        import asyncio
        scraper = None
        try:
            scraper = foreign_agent._build_scraper("DE")

            async def go():
                await scraper._login_playwright()
                try:
                    return await scraper.search(query)
                finally:
                    try:
                        await scraper.logout()          # 좌석 제한 — 조회 후 로그아웃
                    except Exception:
                        pass
            items = asyncio.run(go())
            rows, skipped = de_rows_from_scraper_items(items or [])
            job.update(status="done", rows=rows, skipped=skipped, finished_at=datetime.utcnow().isoformat() + "Z")
        except Exception as e:  # 자격증명·네트워크·파싱 실패는 사유만 남긴다(비밀값 미포함)
            job.update(status="error", error=f"{type(e).__name__}: {str(e)[:300]}", finished_at=datetime.utcnow().isoformat() + "Z")

    def _run_ch_collect(r, job):
        """스위스 BAG SL 공개 API(로그인 없음) — 2026-11-10 모니터링 B안(공개 자료 국가 서버 수집)."""
        import argparse
        import importlib.util
        from pathlib import Path as _P
        try:
            spec = importlib.util.spec_from_file_location("a8_rows", _P(__file__).resolve().parent.parent / "scripts" / "a8_rows.py")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            ident = r.get("identity") or {}
            q = r["query"]
            brand = (ident.get("brand") or q.split("/")[0]).strip()
            inn = (ident.get("inn") or (q.split("/")[1] if "/" in q else q)).strip()
            ns = argparse.Namespace(trademark=brand, ingredient=inn, form="unknown", dosage_form="", strength="",
                                    observed_at=None, evidence=f"server-api:bag_sl:{brand}", json=None)
            rows, skipped = mod.build_ch(ns)
            import re as _re
            for row in rows:   # 제형은 SL 표기에서 — 주사(바이알) vs 경구(정·캡슐). 모르면 unknown 그대로(워커 확인)
                txt = f"{row.get('product_name') or ''} {row.get('package_unit') or ''}"
                if _re.search(r"Inj|Infus|Durchstf|Fertigspr|Amp|Pen\b|Trockensub|Lösung zur", txt, _re.I):
                    row["form_type"] = "injection"
                elif _re.search(r"Tabl|Kaps|Filmtabl|Dragée|Granul|Sirup|Susp zum Einnehmen", txt, _re.I):
                    row["form_type"] = "oral"
            job.update(status="done", rows=rows, skipped=skipped, finished_at=datetime.utcnow().isoformat() + "Z")
        except Exception as e:
            job.update(status="error", error=f"{type(e).__name__}: {str(e)[:300]}", finished_at=datetime.utcnow().isoformat() + "Z")

    # 2026-11-10 사용자 결정: 서버 수집 = DE·CH·JP·US·CA·FR(BdM_IT). 영국·이탈리아는 브라우저 유지.
    # 브라우저(Chromium)를 띄우는 수집기는 메모리 1GB 서버에서 한 번에 하나만 돈다(US 는 좌석 제한 — 동시 로그인 방지).
    import threading as _th
    _collect_lock = _th.Lock()

    def _brand_inn(r):
        ident = r.get("identity") or {}
        q = r["query"]
        brand = (ident.get("brand") or q.split("/")[0]).strip()
        inn = (ident.get("inn") or (q.split("/")[1] if "/" in q else "")).strip()
        return brand, inn

    def _run_scraper_collect(cc, r, job):
        import asyncio
        try:
            import time as _time
            with _collect_lock:
                job["phase"] = "running"
                brand, inn = _brand_inn(r)
                records, errors = [], []
                # 수집기는 접속 오류(도메인 조회 실패 등)를 0건으로 돌려주기도 한다 — 0건이면 3번까지 다시 시도하고,
                # 그래도 0건이면 '가격 없음'이 아니라 '확인 필요'로 넘긴다(워커가 브라우저·판정).
                for attempt in range(3):
                    for q in [x for x in (brand, inn) if x]:
                        try:
                            records = asyncio.run(foreign_agent._build_scraper(cc).run(q)) or []
                        except Exception as e:
                            errors.append(f"{q}: {type(e).__name__}: {str(e)[:120]}")
                            records = []
                        if records:
                            job["searched"] = q
                            break
                    if records:
                        break
                    _time.sleep(5 * (attempt + 1))
                job["attempts"] = attempt + 1
            rows, skipped = rows_from_scraper_records(cc, records, foreign_agent)
            if not records:
                skipped.append({"reason": "3회 시도 모두 0건 — 접속 오류일 수 있음. 가격 없음으로 판단하지 말고 브라우저로 확인",
                                "errors": errors[-3:]})
            job.update(status="done", rows=rows, skipped=skipped, finished_at=datetime.utcnow().isoformat() + "Z")
        except Exception as e:
            job.update(status="error", error=f"{type(e).__name__}: {str(e)[:300]}", finished_at=datetime.utcnow().isoformat() + "Z")

    def _run_fr_collect(r, job):
        """프랑스 BdM_IT(관보 PFHT·UCD tarif HT, 시행일=적용일). 안 열리면 error → 워커가 브라우저로 관보(Légifrance)."""
        from agents.foreign_sources import fr_bdm_it
        try:
            brand, inn = _brand_inn(r)
            rows, skipped = fr_bdm_it.rows_for(brand, inn)
            job.update(status="done", rows=rows, skipped=skipped, finished_at=datetime.utcnow().isoformat() + "Z")
        except fr_bdm_it.BdmItUnavailable as e:
            job.update(status="error", error=f"BdM_IT unavailable — use browser Journal officiel: {str(e)[:200]}",
                       finished_at=datetime.utcnow().isoformat() + "Z")
        except Exception as e:
            job.update(status="error", error=f"{type(e).__name__}: {str(e)[:300]}", finished_at=datetime.utcnow().isoformat() + "Z")

    SERVER_COLLECT = ("DE", "CH", "JP", "US", "CA", "FR")

    @bp.post("/api/price-agent/requests/<rid>/server-collect/<cc>")
    @require_auth(role="price_agent")
    @guarded
    def server_collect_start(rid, cc):
        import threading
        r = worker_detail(rid)
        if cc not in SERVER_COLLECT:
            raise ValueError("server collection is allowed for " + ", ".join(SERVER_COLLECT) + " only")
        if cc not in r["countries"]:
            raise ValueError("country not requested")
        store.touch_worker(actor(), "server-collect")
        key = (rid, cc)
        job = _collect_jobs.get(key)
        if job and job["status"] == "running":
            return jsonify(job), 202
        job = {"status": "running", "query": r["query"], "started_at": datetime.utcnow().isoformat() + "Z"}
        _collect_jobs[key] = job
        if cc == "DE":
            def _de_locked(q, j):
                with _collect_lock:
                    _run_de_collect(q, j)
            threading.Thread(target=_de_locked, args=(r["query"], job), daemon=True).start()
        elif cc == "CH":
            threading.Thread(target=_run_ch_collect, args=(r, job), daemon=True).start()
        elif cc == "FR":
            threading.Thread(target=_run_fr_collect, args=(r, job), daemon=True).start()
        else:
            threading.Thread(target=_run_scraper_collect, args=(cc, r, job), daemon=True).start()
        return jsonify(job), 202

    @bp.get("/api/price-agent/requests/<rid>/server-collect/<cc>")
    @require_auth(role="price_agent")
    @guarded
    def server_collect_result(rid, cc):
        worker_detail(rid)
        job = _collect_jobs.get((rid, cc))
        if not job:
            raise KeyError(rid)
        return jsonify(job)

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
        out = store.accept(rid, cc, actor(), lease(), status, reason, prepared, payload_hash,
                           b.get("verdict"), b.get("verdict_sources"))
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

    # 분석 요청(A안, 2026-11-02) — server.py 를 건드리지 않도록 여기서 중첩 등록한다. URL 은 /api/analysis-requests…
    from api.analysis_request_routes import build_analysis_request_blueprint
    bp.register_blueprint(build_analysis_request_blueprint(db_path))
    # 등재 아날로그 목록 엑셀·AI 분석 이력(2026-11-09) — 같은 이유로 중첩 등록
    from api.analog_extra_routes import build_analog_extra_blueprint
    bp.register_blueprint(build_analog_extra_blueprint())
    # 해외약가 모니터링(2026-11-10)
    from api.price_monitor_routes import build_price_monitor_blueprint
    bp.register_blueprint(build_price_monitor_blueprint(db_path))
    return bp
