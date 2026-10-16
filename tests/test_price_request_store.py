"""Offline queue correctness; isolated temporary DB, no live accounts or emails."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from agents.price_request_store import PriceRequestStore, Conflict
from agents.price_request_catalog import CATALOG
from api.price_request_ingest import common, prepare_oecd, prepare_a8


def observed():
    return dict(product_name="Keytruda", ingredient="pembrolizumab", dosage_strength="100 mg/4 ml",
                dosage_form="infusion", package_unit="2 vials", source_url="https://example.org/list",
                source_label="Official list", evidence_ref="evidence/pdf-p6.png", price_tier_original="Prezzo ex-factory (IVA esclusa)",
                local_price=7596.68, pack_count=2, currency="EUR", price_tier="ex_factory", vat_status="excluded",
                observed_at="2026-01-01T00:00:00+00:00", effective_date="2026-01-01", form_type="injection")


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "test.db"
        self.store = PriceRequestStore(self.path)
        self.r = self.store.create("Keytruda", "oecd", ["AU", "NZ"], "requester@example.org", "u@example.org")
        self.rid = self.r["id"]

    def tearDown(self):
        self.tmp.cleanup()

    def claim(self):
        self.token = self.store.claim(self.rid, "worker")["lease_token"]

    def accept(self, cc="AU", status="priced", records=None):
        if records is None:
            records = [{"observed_at": "2026-01-01T00:00:00Z", "product_name": "Keytruda"}] if status == "priced" else []
        return self.store.accept(self.rid, cc, "worker", self.token, status,
                                 None if status == "priced" else "login restricted", records, cc + status)

    def ready(self, partial=False):
        self.claim()
        self.accept()
        r = self.accept("NZ", "failed" if partial else "priced")
        ids = [i for cr in r["country_results"] for i in cr["row_ids"]]
        return self.store.verify(self.rid, "worker", self.token, sorted(ids), "proof.png", r["dashboard_url"])

    def test_catalog_scope(self):
        self.assertEqual(len(CATALOG["a8"]), 8)
        self.assertEqual(len(CATALOG["oecd"]), 29)
        with self.assertRaises(ValueError):
            self.store.create("x", "a8", ["AU"], "u@example.org", "u")

    def test_deduplicated_create_and_restart(self):
        r = self.store.create("keytruda", "oecd", ["NZ", "AU"], "requester@example.org", "u@example.org")
        self.assertEqual(r["id"], self.rid)
        self.assertEqual(PriceRequestStore(self.path).detail(self.rid)["status"], "queued")

    def test_claim_race_one_winner(self):
        def attempt(_):
            try:
                return bool(self.store.claim(self.rid, "worker"))
            except Conflict:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(attempt, range(2))), 1)

    def test_global_worker_limit(self):
        self.claim()
        second = self.store.create("Welireg", "oecd", ["AU"], "u@example.org", "u")
        with self.assertRaises(Conflict):
            self.store.claim(second["id"], "worker2")

    def test_expired_lease_fenced_and_resumable(self):
        self.claim()
        old = self.token
        self.accept()
        with self.store.connect() as c:
            c.execute("UPDATE price_requests SET lease_until='2000-01-01' WHERE id=?", (self.rid,))
        self.assertEqual(len(self.store.list(queue=True)), 1)
        self.claim()
        with self.assertRaises(Conflict):
            self.store.accept(self.rid, "NZ", "worker", old, "failed", "timeout", [], "x")
        self.assertEqual(self.store.detail(self.rid)["country_results"][0]["status"], "priced")

    def test_country_idempotence(self):
        self.claim()
        self.accept()
        self.accept()
        self.assertEqual(len(self.store.oecd_prices("keytruda")), 1)
        with self.assertRaises(Conflict):
            self.store.accept(self.rid, "AU", "worker", self.token, "priced", None, [{"observed_at": "x"}], "different")

    def test_atomic_batch_rollback(self):
        self.claim()
        with self.assertRaises(KeyError):
            self.accept(records=[{"observed_at": "x"}, {}])
        self.assertEqual(self.store.oecd_prices("keytruda"), [])
        self.assertEqual(self.store.detail(self.rid)["country_results"][0]["status"], "pending")

    def test_no_notification_before_verification(self):
        self.claim()
        with self.assertRaises(Conflict):
            self.store.notification(self.rid, "worker", self.token, "begin")

    def test_missing_rows_or_wrong_page_blocks_verify(self):
        self.claim()
        self.accept()
        r = self.accept("NZ")
        with self.assertRaises(Conflict):
            self.store.verify(self.rid, "worker", self.token, [], "p.png", r["dashboard_url"])
        ids = [i for cr in r["country_results"] for i in cr["row_ids"]]
        with self.assertRaises(ValueError):
            self.store.verify(self.rid, "worker", self.token, ids, "p.png", "/wrong-page")

    def test_all_failure_is_not_success_email(self):
        self.ready(partial=True)
        self.store.notification(self.rid, "worker", self.token, "begin")
        r = self.store.notification(self.rid, "worker", self.token, "sent", "gmail:msg-f:123456")
        self.assertEqual(r["status"], "partial_completed")
        self.assertEqual(r["notification_status"], "sent")

    def test_verified_still_deduplicates_until_email(self):
        self.ready()
        same = self.store.create("keytruda", "oecd", ["AU", "NZ"], "requester@example.org", "u@example.org")
        self.assertEqual(same["id"], self.rid)

    def test_uncertain_not_sent_can_be_reconciled(self):
        self.ready()
        self.store.notification(self.rid, "worker", self.token, "begin")
        with self.assertRaises(Conflict):
            self.store.notification(self.rid, "worker", self.token, "sent", "dry-run")
        self.store.notification(self.rid, "worker", self.token, "uncertain")
        r = self.store.notification(self.rid, "worker", self.token, "not_sent", "gmail-search:evidence/no-sent.png;operator-confirmed")
        self.assertEqual(r["notification_status"], "pending")
        self.store.notification(self.rid, "worker", self.token, "begin")

    def test_actual_receipt_required_and_no_blind_resend(self):
        self.ready()
        self.store.notification(self.rid, "worker", self.token, "begin")
        with self.assertRaises(Conflict):
            self.store.notification(self.rid, "worker", self.token, "begin")
        with self.assertRaises(Conflict):
            self.store.notification(self.rid, "worker", self.token, "sent")
        self.store.notification(self.rid, "worker", self.token, "uncertain")
        with self.assertRaises(Conflict):
            self.store.notification(self.rid, "worker", self.token, "begin")
        r = self.store.notification(self.rid, "worker", self.token, "sent", "gmail:msg-f:234567")
        self.assertEqual(r["status"], "completed")


class ValidationTests(unittest.TestCase):
    def test_reject_zero_nan_wrong_currency_bad_pack_future(self):
        for patch in ({"local_price": 0}, {"local_price": float("nan")}, {"pack_count": True},
                      {"pack_count": 1.2}, {"currency": "USD"}, {"effective_date": "2099-01-01"},
                      {"source_url": "https://user:secret@example.org"}, {"observed_at": "2026-01-01"}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                common({**observed(), **patch}, "IT")

    def test_it_public_price_rejected(self):
        with self.assertRaises(ValueError):
            prepare_a8({**observed(), "price_tier": "pharmacy_retail", "vat_status": "included"}, "IT", "keytruda", "req", None)

    def test_us_awp_rejected(self):
        with self.assertRaises(ValueError):
            prepare_a8({**observed(), "currency": "USD", "price_tier_original": "AWP", "source_type": "redbook_awp"}, "US", "keytruda", "req", None)

    def test_native_tiers_and_normalization(self):
        r = prepare_oecd({**observed(), "total_pkg_mg": 200, "reference_mg": 100, "grade": "A"}, "BE")
        self.assertEqual(r["ex_factory_price"], 7596.68)
        self.assertAlmostEqual(r["normalized_price"], 3798.34)
        self.assertIsNone(r["pharmacy_purchase_price"])
        with self.assertRaises(ValueError):
            prepare_oecd({**observed(), "pharmacy_purchase_price": 7000}, "BE")
        with self.assertRaises(ValueError):
            prepare_oecd({**observed(), "ex_factory_price": 1}, "BE")


if __name__ == "__main__":
    unittest.main()
