"""Durable Aside price queue. SQLite transactions fence workers by expiring leases.
No browser runs in the web process. Each country commits atomically with its prices.
Notifications use an outbox handshake: ready -> sending -> sent; ambiguous sends
must be reconciled, never blindly retried. Schema additions only, no old rows changed.
"""
from __future__ import annotations
import hashlib
import json
import secrets
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from contextlib import contextmanager
from urllib.parse import urlencode

from agents.price_request_catalog import CATALOG


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


class Conflict(ValueError):
    pass


class PriceRequestStore:
    def __init__(self, db_path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS price_requests (
              id TEXT PRIMARY KEY, query TEXT NOT NULL, scope TEXT NOT NULL,
              countries TEXT NOT NULL, identity TEXT, requester TEXT NOT NULL,
              notify_email TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              owner TEXT, lease_hash TEXT, lease_until TEXT, attempts INTEGER DEFAULT 0,
              verified_at TEXT, verification_digest TEXT, verification_evidence TEXT,
              notification_status TEXT NOT NULL DEFAULT 'pending',
              notification_receipt TEXT, notified_at TEXT
            );
            CREATE TABLE IF NOT EXISTS price_request_countries (
              request_id TEXT NOT NULL REFERENCES price_requests(id), country TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending', reason TEXT, row_ids TEXT NOT NULL DEFAULT '[]',
              payload_hash TEXT, updated_at TEXT NOT NULL,
              PRIMARY KEY(request_id, country)
            );
            CREATE TABLE IF NOT EXISTS price_request_events (
              id INTEGER PRIMARY KEY, request_id TEXT NOT NULL, at TEXT NOT NULL,
              actor TEXT NOT NULL, event TEXT NOT NULL, detail TEXT
            );
            CREATE TABLE IF NOT EXISTS oecd_drug_prices (
              id INTEGER PRIMARY KEY, request_id TEXT NOT NULL,
              query_name TEXT NOT NULL, country TEXT NOT NULL, observed_at TEXT NOT NULL,
              payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS price_request_price_snapshots (
              request_id TEXT NOT NULL REFERENCES price_requests(id), country TEXT NOT NULL,
              row_id INTEGER NOT NULL, payload TEXT NOT NULL, captured_at TEXT NOT NULL,
              PRIMARY KEY(request_id,country,row_id)
            );
            CREATE TABLE IF NOT EXISTS price_request_export_snapshots (
              request_id TEXT PRIMARY KEY REFERENCES price_requests(id),
              payload TEXT NOT NULL, captured_at TEXT NOT NULL
            );
            -- Last time any price_agent polled the queue. The worker is an external Aside routine
            -- (it can be paused or the laptop can sleep), so the UI must not promise processing
            -- when nobody has polled recently. One row per worker account; no request data.
            CREATE TABLE IF NOT EXISTS price_worker_seen (
              actor TEXT PRIMARY KEY, last_seen_at TEXT NOT NULL, last_action TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS price_req_queue ON price_requests(status,created_at);
            CREATE INDEX IF NOT EXISTS oecd_price_query ON oecd_drug_prices(query_name,country,observed_at);
            """)

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.db_path, timeout=20)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        try:
            with c:
                yield c
        finally:
            c.close()

    def event(self, c, rid, actor, event, detail=None):
        c.execute("INSERT INTO price_request_events(request_id,at,actor,event,detail) VALUES(?,?,?,?,?)",
                  (rid, now(), actor, event, detail))

    def _detail(self, c, rid):
        row = c.execute("SELECT * FROM price_requests WHERE id=?", (rid,)).fetchone()
        if not row:
            raise KeyError(rid)
        out = dict(row)
        for k in ("lease_hash", "lease_until", "owner"):
            out.pop(k, None)
        for k in ("countries", "identity"):
            out[k] = json.loads(out[k]) if out[k] else None
        out["country_results"] = []
        for r in c.execute("SELECT country,status,reason,row_ids,updated_at FROM price_request_countries WHERE request_id=? ORDER BY country", (rid,)):
            d = dict(r)
            d["row_ids"] = json.loads(d["row_ids"])
            out["country_results"].append(d)
        out["dashboard_url"] = "/international-pricing?" + urlencode({"q": out["query"], "scope": out["scope"], "request": rid})
        return out

    def detail(self, rid):
        with self.connect() as c:
            return self._detail(c, rid)

    # Worker liveness. 10-minute routine + slack: 25 minutes without a poll = not running.
    WORKER_STALE_MINUTES = 25

    def touch_worker(self, actor, action):
        with self.connect() as c:
            c.execute("INSERT INTO price_worker_seen(actor,last_seen_at,last_action) VALUES(?,?,?) "
                      "ON CONFLICT(actor) DO UPDATE SET last_seen_at=excluded.last_seen_at, last_action=excluded.last_action",
                      (actor, now(), action))

    def worker_status(self, at=None):
        """Public summary only: no worker identity, no request content."""
        with self.connect() as c:
            row = c.execute("SELECT MAX(last_seen_at) AS seen FROM price_worker_seen").fetchone()
            waiting = c.execute("SELECT COUNT(*) FROM price_requests WHERE status IN ('queued','processing','awaiting_verification')").fetchone()[0]
        seen = row["seen"] if row else None
        current = at or datetime.now(timezone.utc)
        if not seen:
            state = "never"
        else:
            age = current - datetime.fromisoformat(seen)
            state = "active" if age <= timedelta(minutes=self.WORKER_STALE_MINUTES) else "stale"
        return {"state": state, "last_seen_at": seen, "stale_after_minutes": self.WORKER_STALE_MINUTES,
                "open_requests": waiting}

    def create(self, query, scope, countries, email, actor, identity=None):
        if scope not in CATALOG or scope == "note":
            raise ValueError("invalid scope")
        allowed = {x["code"] for x in CATALOG[scope]}
        if not countries or not isinstance(countries, list) or any(x not in allowed for x in countries):
            raise ValueError("unsupported countries for scope")
        query = query.strip().lower()
        if not query or len(query) > 160:
            raise ValueError("query must be 1..160 characters")
        import re
        if not isinstance(email, str) or len(email) > 254 or not re.fullmatch(r"[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+", email):
            raise ValueError("valid completion email required")
        countries = sorted(set(countries))
        ts, rid = now(), uuid.uuid4().hex
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            # Same requester/product/scope/countries already in flight: idempotent UI retry.
            existing = c.execute("SELECT id FROM price_requests WHERE requester=? AND query=? AND scope=? AND countries=? AND status IN ('queued','processing','awaiting_verification','verified') AND notify_email=? ORDER BY created_at LIMIT 1",
                                 (actor, query, scope, dump(countries), email)).fetchone()
            if existing:
                return self._detail(c, existing["id"])
            c.execute("INSERT INTO price_requests(id,query,scope,countries,identity,requester,notify_email,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                      (rid, query, scope, dump(countries), dump(identity) if identity else None, actor, email, ts, ts))
            c.executemany("INSERT INTO price_request_countries(request_id,country,updated_at) VALUES(?,?,?)", [(rid, cc, ts) for cc in countries])
            self.event(c, rid, actor, "requested")
            return self._detail(c, rid)

    def list(self, actor=None, scope=None, query=None, queue=False):
        where, args = [], []
        if actor:
            where.append("requester=?"); args.append(actor)
        if scope:
            where.append("scope=?"); args.append(scope)
        if query:
            where.append("query=?"); args.append(query.strip().lower())
        if queue:
            where.append("(status IN ('queued','awaiting_verification') OR (status='verified' AND notification_status IN ('pending','sending')) OR (status='processing' AND lease_until<?))")
            args.append(now())
        sql = "SELECT id FROM price_requests" + (" WHERE " + " AND ".join(where) if where else "")
        sql += " ORDER BY created_at " + ("ASC" if queue else "DESC") + " LIMIT 50"
        with self.connect() as c:
            return [self._detail(c, x["id"]) for x in c.execute(sql, args).fetchall()]

    def claim(self, rid, actor):
        ts = now()
        token = secrets.token_urlsafe(32)
        until = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(timespec="seconds")
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            r = c.execute("SELECT * FROM price_requests WHERE id=?", (rid,)).fetchone()
            if not r:
                raise KeyError(rid)
            if r["status"] in ("completed", "partial_completed", "blocked"):
                raise Conflict("request is terminal")
            if r["lease_until"] and r["lease_until"] > ts:
                raise Conflict("request already leased")
            # Bound work globally: ten-minute triggers cannot pile up browser sessions.
            active = c.execute("SELECT id FROM price_requests WHERE lease_until>? AND id<>? LIMIT 1", (ts, rid)).fetchone()
            if active:
                raise Conflict("another price request is active")
            if r["notification_status"] == "sending":
                c.execute("UPDATE price_requests SET notification_status='uncertain' WHERE id=?", (rid,))
            status = "processing" if r["status"] in ("queued", "processing") else r["status"]
            c.execute("UPDATE price_requests SET status=?,owner=?,lease_hash=?,lease_until=?,attempts=attempts+1,updated_at=? WHERE id=?",
                      (status, actor, hashlib.sha256(token.encode()).hexdigest(), until, ts, rid))
            self.event(c, rid, actor, "claimed")
            out = self._detail(c, rid)
            return {"request": out, "lease_token": token, "lease_until": until}

    def owned(self, c, rid, actor, token):
        r = c.execute("SELECT * FROM price_requests WHERE id=?", (rid,)).fetchone()
        if not r:
            raise KeyError(rid)
        digest = hashlib.sha256((token or "").encode()).hexdigest()
        if r["owner"] != actor or not secrets.compare_digest(r["lease_hash"] or "", digest) or not r["lease_until"] or r["lease_until"] <= now():
            raise Conflict("expired or wrong lease")
        return r

    def renew(self, rid, actor, token):
        until = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(timespec="seconds")
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            self.owned(c, rid, actor, token)
            c.execute("UPDATE price_requests SET lease_until=? WHERE id=?", (until, rid))
        return {"lease_until": until}

    def accept(self, rid, cc, actor, token, status, reason, records, payload_hash):
        if status not in ("priced", "unconfirmed", "failed"):
            raise ValueError("invalid country status")
        if status == "priced" and not records:
            raise ValueError("priced requires nonempty validated records")
        if status != "priced" and (records or not reason):
            raise ValueError("unconfirmed/failed require reason and no price rows")
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            r = self.owned(c, rid, actor, token)
            cr = c.execute("SELECT * FROM price_request_countries WHERE request_id=? AND country=?", (rid, cc)).fetchone()
            if not cr:
                raise ValueError("country not requested")
            if cr["payload_hash"] == payload_hash:
                return self._detail(c, rid)
            if r["status"] not in ("processing", "awaiting_verification"):
                raise Conflict("request no longer accepts results")
            # Refuse conflicting replay: do not duplicate a committed priced country.
            if cr["status"] == "priced":
                raise Conflict("country already committed; create a fresh request to replace")
            ids = []
            for record in records:
                if r["scope"] == "a8":
                    allowed = {x["name"] for x in c.execute("PRAGMA table_info(foreign_drug_prices)")}
                    item = {k: v for k, v in record.items() if k in allowed and k != "id"}
                    keys = list(item)
                    sql = "INSERT INTO foreign_drug_prices (" + ",".join(keys) + ") VALUES (" + ",".join("?" for _ in keys) + ")"
                    cur = c.execute(sql, [item[k] for k in keys])
                else:
                    item = dict(record)
                    session_at = item.pop("_session_at", item["observed_at"])
                    cur = c.execute("INSERT INTO oecd_drug_prices(request_id,query_name,country,observed_at,payload) VALUES(?,?,?,?,?)",
                                    (rid, r["query"], cc, session_at, dump(item)))
                row_id = cur.lastrowid
                ids.append(row_id)
                snapshot = {**item, "id": row_id, "country": cc}
                c.execute("INSERT INTO price_request_price_snapshots(request_id,country,row_id,payload,captured_at) VALUES(?,?,?,?,?)",
                          (rid, cc, row_id, dump(snapshot), now()))
            ts = now()
            c.execute("UPDATE price_request_countries SET status=?,reason=?,row_ids=?,payload_hash=?,updated_at=? WHERE request_id=? AND country=?",
                      (status, reason, dump(ids), payload_hash, ts, rid, cc))
            pending = c.execute("SELECT COUNT(*) FROM price_request_countries WHERE request_id=? AND status='pending'", (rid,)).fetchone()[0]
            c.execute("UPDATE price_requests SET status=?,updated_at=?,verified_at=NULL,verification_digest=NULL WHERE id=?",
                      ("processing" if pending else "awaiting_verification", ts, rid))
            self.event(c, rid, actor, "country_" + status, cc)
            return self._detail(c, rid)

    def oecd_prices(self, query):
        with self.connect() as c:
            rows = c.execute("""SELECT p.* FROM oecd_drug_prices p JOIN (
              SELECT country,MAX(observed_at) AS latest FROM oecd_drug_prices
              WHERE query_name=? GROUP BY country
            ) t ON p.country=t.country AND p.observed_at=t.latest
            WHERE p.query_name=? ORDER BY p.country,p.id""", (query.lower(), query.lower())).fetchall()
            return [{**json.loads(r["payload"]), "id": r["id"], "country": r["country"]} for r in rows]

    def export_data(self, rid):
        """Historical request only. Never look up a product's current country cache.

        New uploads have independent archives. For pre-archive requests, only exact
        original row IDs may be recovered; missing rows are reported, never replaced
        with another search's prices. Verified readback is frozen once.
        """
        with self.connect() as c:
            r = self._detail(c, rid)
            frozen = c.execute("SELECT payload,captured_at FROM price_request_export_snapshots WHERE request_id=?", (rid,)).fetchone()
            if frozen:
                data = json.loads(frozen["payload"])
                return {"request": r, **data, "snapshot_kind": "verified", "snapshot_captured_at": frozen["captured_at"]}
            rows, missing = [], []
            for country in r["country_results"]:
                for row_id in country["row_ids"]:
                    archived = c.execute("SELECT payload FROM price_request_price_snapshots WHERE request_id=? AND country=? AND row_id=?", (rid, country["country"], row_id)).fetchone()
                    if archived:
                        rows.append(json.loads(archived["payload"]))
                        continue
                    if r["scope"] == "a8":
                        old = c.execute("SELECT * FROM foreign_drug_prices WHERE id=? AND country=?", (row_id, country["country"])).fetchone()
                        item = dict(old) if old else None
                    else:
                        old = c.execute("SELECT payload FROM oecd_drug_prices WHERE id=? AND request_id=? AND country=?", (row_id, rid, country["country"])).fetchone()
                        item = {**json.loads(old["payload"]), "id": row_id, "country": country["country"]} if old else None
                    if item is None:
                        missing.append(row_id)
                    else:
                        rows.append(item)
            return {"request": r, "rows": rows, "country_results": r["country_results"], "missing_row_ids": missing,
                    "snapshot_kind": "uploaded", "snapshot_captured_at": None}

    def verify(self, rid, actor, token, visible_ids, evidence, dashboard_url, snapshot_rows=None):
        if not evidence or not isinstance(evidence, str) or len(evidence) > 1000:
            raise ValueError("dashboard screenshot evidence reference required")
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            r = self.owned(c, rid, actor, token)
            detail = self._detail(c, rid)
            if r["status"] not in ("awaiting_verification", "verified"):
                raise Conflict("all requested countries must be checked before verification")
            if dashboard_url != detail["dashboard_url"]:
                raise ValueError("wrong dashboard URL")
            expected = sorted(i for x in detail["country_results"] for i in x["row_ids"])
            if sorted(visible_ids) != expected:
                raise Conflict("new price row IDs are missing from dashboard readback")
            digest = hashlib.sha256(dump({"rows": expected, "results": detail["country_results"]}).encode()).hexdigest()
            c.execute("UPDATE price_requests SET status='verified',verified_at=?,verification_digest=?,verification_evidence=?,updated_at=? WHERE id=?",
                      (now(), digest, evidence, now(), rid))
            if snapshot_rows is not None:
                # Retain exactly the newly uploaded rows seen through the dashboard.
                by_id = {row.get("id"): row for row in snapshot_rows}
                if sorted(by_id) != expected:
                    raise Conflict("export snapshot does not match verified rows")
                frozen = {"rows": [by_id[i] for i in expected], "country_results": detail["country_results"], "missing_row_ids": []}
                c.execute("INSERT OR IGNORE INTO price_request_export_snapshots(request_id,payload,captured_at) VALUES(?,?,?)",
                          (rid, dump(frozen), now()))
            self.event(c, rid, actor, "dashboard_verified", evidence)
            return self._detail(c, rid)

    def notification(self, rid, actor, token, phase, receipt=None):
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            r = self.owned(c, rid, actor, token)
            if r["status"] != "verified":
                raise Conflict("dashboard must be verified before notification")
            if phase == "begin":
                if r["notification_status"] != "pending":
                    raise Conflict("notification already sent/in-flight/uncertain; reconcile first")
                c.execute("UPDATE price_requests SET notification_status='sending' WHERE id=?", (rid,))
            elif phase == "sent":
                if (r["notification_status"] not in ("sending", "uncertain")
                        or not isinstance(receipt, str)
                        or not re.fullmatch(r"gmail:(?:msg-[af]:[0-9]+|[0-9a-f]{16,64})", receipt)):
                    raise Conflict("actual Gmail sent-message ID required; dry-run or SMTP acknowledgements are not accepted")
                partial = c.execute("SELECT COUNT(*) FROM price_request_countries WHERE request_id=? AND status<>'priced'", (rid,)).fetchone()[0]
                c.execute("UPDATE price_requests SET status=?,notification_status='sent',notification_receipt=?,notified_at=?,updated_at=?,lease_until=NULL,lease_hash=NULL WHERE id=?",
                          ("partial_completed" if partial else "completed", receipt, now(), now(), rid))
            elif phase == "uncertain":
                if r["notification_status"] != "sending":
                    raise Conflict("notification not in sending state")
                c.execute("UPDATE price_requests SET notification_status='uncertain' WHERE id=?", (rid,))
            elif phase == "not_sent":
                # Only after operator confirmation + Sent-folder reconciliation evidence.
                if r["notification_status"] != "uncertain" or not isinstance(receipt, str) or not receipt.startswith("gmail-search:") or not 20 <= len(receipt) <= 1000:
                    raise Conflict("uncertain send requires confirmed-not-sent reconciliation evidence")
                c.execute("UPDATE price_requests SET notification_status='pending' WHERE id=?", (rid,))
            else:
                raise ValueError("phase must be begin, sent, uncertain or not_sent")
            self.event(c, rid, actor, "notification_" + phase, receipt if phase == "not_sent" else None)
            return self._detail(c, rid)
