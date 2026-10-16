"""Integration using Flask test client, isolated DB + fake calculation/cache adapter.
Does not import api.server (no production DB/network side effects).
"""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from flask import Flask
from api.auth import _issue_token
from api.price_request_routes import build_price_request_blueprint
from agents.price_request_store import PriceRequestStore
from tests.test_price_request_store import observed


class FakeAgent:
    def __init__(self, path):
        self.path = path
        self.rate_fetcher = type("Rates", (), {"get_36m_average": lambda *a: {"rate": 1500, "from_month": "202301", "to_month": "202512"}})()
        self.calculator = type("Calculator", (), {"calculate_adjusted_price": lambda _, **kw: dict(factory_price_krw=kw["listed_price"] / kw["pack_count"] * kw["exchange_rate"], vat_rate=.1, distribution_margin=.0869, adjusted_price_krw=kw["listed_price"] / kw["pack_count"] * kw["exchange_rate"] * 1.1 * 1.0869, pack_count=kw["pack_count"])})()
        self.hidden = False
    def get_cached_results(self, query):
        if self.hidden:
            return {}
        with sqlite3.connect(self.path) as c:
            c.row_factory = sqlite3.Row
            rows = [dict(x) for x in c.execute("SELECT * FROM foreign_drug_prices WHERE query_name=?", (query,))]
        out = {}
        for r in rows:
            out.setdefault(r["country"], []).append(r)
        return out
    def _extract_per_unit_mg(self, *a, **kw):
        return 100
    def _load_dosing_map(self, *a):
        return {}
    def _match_dosing(self, *a):
        return None
    def _populate_daily_cost(self, *a):
        return None


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "test.db"
        # Minimal schema includes all normalizer keys, same types as production.
        from agents.db.schema import DB_SCHEMA
        with sqlite3.connect(self.path) as c:
            c.executescript(DB_SCHEMA)
            existing = {x[1] for x in c.execute("PRAGMA table_info(foreign_drug_prices)")}
            for key in ("unit_strength_mg", "reference_strength_mg", "dose_norm_factor", "adjusted_price_krw_normalized", "dose_norm_note", "formulation_key", "formulation_label", "canonical_strength_mg", "route", "formulation_source", "is_us_listed"):
                if key not in existing:
                    c.execute(f"ALTER TABLE foreign_drug_prices ADD COLUMN {key}")
        self.agent = FakeAgent(self.path)
        app = Flask(__name__)
        app.testing = True
        app.register_blueprint(build_price_request_blueprint(self.path, self.agent))
        self.client = app.test_client()
        self.user = {"Authorization": "Bearer " + _issue_token("user@example.org", "user")}
        self.other = {"Authorization": "Bearer " + _issue_token("other@example.org", "user")}
        self.worker = {"Authorization": "Bearer " + _issue_token("worker@example.org", "price_agent")}
        self.agent_role = {"Authorization": "Bearer " + _issue_token("general@example.org", "agent")}
        self.alias_patch = patch("agents.drug_identity.derive_identity_offline", return_value=None)
        self.alias_patch.start()

    def tearDown(self):
        self.alias_patch.stop()
        self.tmp.cleanup()

    def create(self, countries=None):
        r = self.client.post("/api/price-requests", headers=self.user, json=dict(query="keytruda", scope="a8", countries=countries or ["IT"], notify_email="user@example.org"))
        self.assertEqual(r.status_code, 201, r.json)
        rid = r.json["request"]["id"]
        r = self.client.post(f"/api/price-agent/requests/{rid}/claim", headers=self.worker)
        self.assertEqual(r.status_code, 200, r.json)
        self.headers = {**self.worker, "X-Price-Lease": r.json["lease_token"]}
        self.rid = rid
        return rid

    def test_auth_role_isolation(self):
        self.assertEqual(self.client.get("/api/price-requests/catalog").status_code, 401)
        self.assertEqual(self.client.get("/api/price-agent/requests", headers=self.user).status_code, 403)
        self.assertEqual(self.client.get("/api/price-agent/requests", headers=self.agent_role).status_code, 403)
        self.assertEqual(self.client.get("/api/price-requests/catalog", headers=self.worker).status_code, 403)
        self.assertEqual(self.client.get("/api/price-agent/requests", headers=self.worker).status_code, 200)

    def test_other_requester_cannot_read(self):
        rid = self.create()
        self.assertEqual(self.client.get(f"/api/price-requests/{rid}", headers=self.other).status_code, 404)
        self.assertEqual(self.client.get("/api/price-requests", headers=self.other).json["requests"], [])

    def upload(self):
        rid = self.create()
        rows = [observed(), {**observed(), "package_unit": "1 vial", "pack_count": 1, "local_price": 3798.34}]
        r = self.client.put(f"/api/price-agent/requests/{rid}/countries/IT", headers=self.headers, json=dict(status="priced", rows=rows))
        self.assertEqual(r.status_code, 200, r.json)
        return rid, r.json["request"]

    def test_upload_atomic_session_and_server_calculation(self):
        rid, r = self.upload()
        self.assertEqual(r["status"], "awaiting_verification")
        with sqlite3.connect(self.path) as c:
            self.assertEqual(c.execute("SELECT COUNT(DISTINCT searched_at) FROM foreign_drug_prices").fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM foreign_drug_prices").fetchone()[0], 2)
        # Same payload replay cannot insert duplicates.
        rows = [observed(), {**observed(), "package_unit": "1 vial", "pack_count": 1, "local_price": 3798.34}]
        self.assertEqual(self.client.put(f"/api/price-agent/requests/{rid}/countries/IT", headers=self.headers, json=dict(status="priced", rows=rows)).status_code, 200)
        self.assertEqual(len(self.agent.get_cached_results("keytruda")["IT"]), 2)

    def test_reflection_and_ui_check_gate_email(self):
        rid, r = self.upload()
        self.assertEqual(self.client.post(f"/api/price-agent/requests/{rid}/notification", headers=self.headers, json={"phase":"begin"}).status_code, 409)
        readback = self.client.get(f"/api/price-agent/requests/{rid}/readback", headers=self.headers).json
        b = dict(visible_row_ids=readback["visible_row_ids"], evidence_ref="proof/screenshot.png", dashboard_url=r["dashboard_url"])
        self.assertEqual(self.client.post(f"/api/price-agent/requests/{rid}/verify", headers=self.headers, json=b).status_code, 400)
        b["ui_checked"] = True
        self.agent.hidden = True
        self.assertEqual(self.client.post(f"/api/price-agent/requests/{rid}/verify", headers=self.headers, json=b).status_code, 409)
        self.agent.hidden = False
        self.assertEqual(self.client.post(f"/api/price-agent/requests/{rid}/verify", headers=self.headers, json=b).status_code, 200)
        self.assertEqual(self.client.post(f"/api/price-agent/requests/{rid}/notification", headers=self.headers, json={"phase":"begin"}).status_code, 200)
        self.assertEqual(self.client.post(f"/api/price-agent/requests/{rid}/notification", headers=self.headers, json={"phase":"sent","receipt":"gmail:msg-f:345678"}).json["request"]["status"], "completed")

    def test_bad_row_rolls_back(self):
        rid = self.create()
        r = self.client.put(f"/api/price-agent/requests/{rid}/countries/IT", headers=self.headers, json={"status":"priced", "rows":[observed(), {**observed(),"local_price":0}]})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.agent.get_cached_results("keytruda"), {})


if __name__ == "__main__":
    unittest.main()
