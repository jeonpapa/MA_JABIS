"""scripts/seed_onco_regimens.py — 항암 레지멘 정본 시드의 안전성/멱등성.

임시 sqlite DB 만 사용 (실제 data/db/drug_prices.db 미접근). 가능하면 프로젝트 DrugPriceDB 로 스키마를
초기화하고, 의존성(pandas 등) 부재 시 schema.py/onco.py 를 파일 단위로 로드해 최소 핸들을 만든다.
python -m unittest tests.test_seed_onco_regimens  /  pytest 둘 다 동작.
"""
from __future__ import annotations

import importlib.util
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SEED_PATH = REPO / "seed" / "onco_regimen" / "onco_regimens.json"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


seeder = _load("_seed_onco_regimens", REPO / "scripts" / "seed_onco_regimens.py")


def _make_db(db_path: Path):
    """프로젝트 DB 핸들 (full init) — 의존성 없으면 schema + _OncoMixin 최소 핸들."""
    try:
        from agents.db import DrugPriceDB  # noqa: WPS433
        return DrugPriceDB(db_path)
    except ImportError:
        schema = _load("_schema_for_seed_test", REPO / "agents" / "db" / "schema.py")
        onco = _load("_onco_for_seed_test", REPO / "agents" / "db" / "onco.py")

        class _MiniDB(onco._OncoMixin):
            def __init__(self, path):
                self.db_path = path
                with self._connect() as conn:
                    conn.executescript(schema.DB_SCHEMA)
                    conn.execute(
                        "CREATE TABLE IF NOT EXISTS regimen_comparisons (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                        "name TEXT NOT NULL, owner_email TEXT, payload_json TEXT NOT NULL, created_at TEXT, "
                        "updated_at TEXT)")

            def _connect(self):
                conn = sqlite3.connect(self.db_path)
                conn.row_factory = sqlite3.Row
                return conn

        return _MiniDB(db_path)


USER_TABLES = ("custom_regimen", "regimen_comparisons", "user_drug_dosing")


def _snapshot(db_path: Path, table: str) -> list[tuple]:
    conn = sqlite3.connect(db_path)
    try:
        return [tuple(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY 1")]
    finally:
        conn.close()


def _count(db_path: Path, table: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def _quiet(*a, **k):
    return None


class SeedArtifactTest(unittest.TestCase):
    def test_seed_file_integrity(self):
        self.assertTrue(SEED_PATH.exists(), "seed JSON 이 data/ 밖(seed/onco_regimen/)에 있어야 이미지에 포함됨")
        self.assertLess(SEED_PATH.stat().st_size, 5 * 1024 * 1024)
        regs, drugs, meta = seeder.load_seed(SEED_PATH)
        self.assertEqual(meta["counts"], {"onco_regimen": len(regs), "onco_regimen_drug": len(drugs)})
        self.assertGreaterEqual(len(regs), 100)
        self.assertGreaterEqual(len(drugs), len(regs))
        text = SEED_PATH.read_text(encoding="utf-8")
        # 사용자 데이터/자격증명 유입 방지
        self.assertNotIn("@", text)
        for banned in ("owner_email", "rows_json", "payload_json", "API_KEY", "password"):
            self.assertNotIn(banned, text)
        self.assertEqual(set(json.loads(text).keys()) & {"custom_regimen", "regimen_comparisons"}, set())

    def test_seed_not_dockerignored(self):
        lines = [ln.strip() for ln in (REPO / ".dockerignore").read_text(encoding="utf-8").splitlines()]
        active = [ln for ln in lines if ln and not ln.startswith("#")]
        for pat in ("seed", "seed/", "seed/*", "seed/**", "*.json", "**/*.json"):
            self.assertNotIn(pat, active)


class SeedRunTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "drug_prices.db"
        self.db = _make_db(self.db_path)
        conn = sqlite3.connect(self.db_path)
        with conn:
            conn.execute("INSERT INTO custom_regimen (name, cancer, owner_email, rows_json, created_at, updated_at) "
                         "VALUES ('my KP', 'NSCLC', 'u@example.com', '[{\"ingredient\":\"Pembrolizumab\"}]', 't', 't')")
            conn.execute("INSERT INTO regimen_comparisons (name, owner_email, payload_json, created_at, updated_at) "
                         "VALUES ('cmp', 'u@example.com', '{}', 't', 't')")
            conn.execute("INSERT INTO user_drug_dosing (drug_key, ingredient, dose_value, unit, updated_at) "
                         "VALUES ('pembrolizumab', 'Pembrolizumab', 400, 'mg', 't')")
        conn.close()
        self.user_before = {t: _snapshot(self.db_path, t) for t in USER_TABLES}
        self.regs, self.drugs, _ = seeder.load_seed(SEED_PATH)

    def tearDown(self):
        self._tmp.cleanup()

    def _assert_user_tables_untouched(self):
        for t in USER_TABLES:
            self.assertEqual(_snapshot(self.db_path, t), self.user_before[t], t)

    def test_dry_run_writes_nothing(self):
        res = seeder.run(self.db_path, SEED_PATH, apply=False, log=_quiet)
        self.assertEqual(res["action"], "dry-run")
        self.assertEqual(res["would"], "seed (empty)")
        self.assertEqual(_count(self.db_path, "onco_regimen"), 0)
        self.assertEqual(_count(self.db_path, "onco_regimen_drug"), 0)
        self._assert_user_tables_untouched()

    def test_cli_default_is_dry_run(self):
        with redirect_stdout(io.StringIO()):
            rc = seeder.main(["--db", str(self.db_path), "--seed", str(SEED_PATH)])
        self.assertEqual(rc, 0)
        self.assertEqual(_count(self.db_path, "onco_regimen"), 0)

    def test_seeds_when_empty_then_noop(self):
        with redirect_stdout(io.StringIO()):
            rc = seeder.main(["--db", str(self.db_path), "--seed", str(SEED_PATH), "--apply", "--if-empty"])
        self.assertEqual(rc, 0)
        self.assertEqual(_count(self.db_path, "onco_regimen"), len(self.regs))
        self.assertEqual(_count(self.db_path, "onco_regimen_drug"), len(self.drugs))
        self._assert_user_tables_untouched()

        # 운영 중 수정된 행은 --if-empty 재실행이 덮어쓰지 않음 (no-op)
        conn = sqlite3.connect(self.db_path)
        with conn:
            conn.execute("UPDATE onco_regimen SET regimen_name='EDITED' WHERE ref=1")
        conn.close()
        res = seeder.run(self.db_path, SEED_PATH, apply=True, if_empty=True, log=_quiet)
        self.assertEqual(res["action"], "skipped")
        self.assertEqual(_snapshot(self.db_path, "onco_regimen")[0][4], "EDITED")
        self.assertEqual(_count(self.db_path, "onco_regimen"), len(self.regs))
        self._assert_user_tables_untouched()

    def test_noop_when_populated_with_other_rows(self):
        conn = sqlite3.connect(self.db_path)
        with conn:
            conn.execute("INSERT INTO onco_regimen (ref, regimen_id, cancer, regimen_name) "
                         "VALUES (9999, 'x', 'x', 'local only')")
        conn.close()
        res = seeder.run(self.db_path, SEED_PATH, apply=True, if_empty=True, log=_quiet)
        self.assertEqual(res["action"], "skipped")
        self.assertEqual(_count(self.db_path, "onco_regimen"), 1)
        self.assertEqual(_count(self.db_path, "onco_regimen_drug"), 0)

    def test_upsert_never_deletes_and_restores(self):
        seeder.run(self.db_path, SEED_PATH, apply=True, if_empty=True, log=_quiet)
        conn = sqlite3.connect(self.db_path)
        with conn:
            conn.execute("UPDATE onco_regimen SET regimen_name='EDITED' WHERE ref=1")
            conn.execute("INSERT INTO onco_regimen (ref, regimen_id, cancer, regimen_name) "
                         "VALUES (9999, 'x', 'x', 'local only')")
        conn.close()
        res = seeder.run(self.db_path, SEED_PATH, apply=True, if_empty=False, log=_quiet)
        self.assertEqual(res["action"], "upserted")
        self.assertEqual(res["reg_changed"], 1)
        self.assertEqual(res["reg_new"], 0)
        self.assertEqual(_count(self.db_path, "onco_regimen"), len(self.regs) + 1)  # 9999 보존
        self.assertNotEqual(_snapshot(self.db_path, "onco_regimen")[0][4], "EDITED")
        # 두 번째 upsert 는 변경 0 (멱등)
        res2 = seeder.run(self.db_path, SEED_PATH, apply=True, log=_quiet)
        self.assertEqual((res2["reg_new"], res2["reg_changed"], res2["drug_new"], res2["drug_changed"]),
                         (0, 0, 0, 0))
        self._assert_user_tables_untouched()

    def test_creates_tables_on_fresh_db(self):
        fresh = Path(self._tmp.name) / "fresh" / "drug_prices.db"
        res = seeder.run(fresh, SEED_PATH, apply=True, if_empty=True, log=_quiet)
        self.assertEqual(res["action"], "seeded")
        self.assertEqual(_count(fresh, "onco_regimen"), len(self.regs))

    def test_search_after_seed(self):
        self.assertEqual(self.db.search_onco_regimens("pembrolizumab"), [])
        seeder.run(self.db_path, SEED_PATH, apply=True, if_empty=True, log=_quiet)
        hits = self.db.search_onco_regimens("pembrolizumab")
        self.assertTrue(hits)
        self.assertTrue(any("Pembrolizumab" in h["drug_names"] for h in hits))
        # 한글 암종명 검색
        self.assertTrue(self.db.search_onco_regimens("비소세포폐암"))
        self.assertTrue(self.db.onco_regimens_with_drug("Pembrolizumab"))
        self.assertIsNotNone(self.db.onco_drug_default("Pembrolizumab"))
        # 참고: 정본에는 한글 브랜드명(예: 키트루다)이 없어 search_onco_regimens('키트루다') 는 빈 결과.
        # 브랜드→INN alias 는 검색 계층 책임 (english_inn_from_kr 등) — 여기선 단언하지 않음.
        self._assert_user_tables_untouched()


if __name__ == "__main__":
    unittest.main()
