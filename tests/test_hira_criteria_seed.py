"""운영 시드 적재(scripts/seed_hira_criteria.py) 안전장치 테스트 — 임시 DB 만 쓴다(실제 운영·로컬 DB 미접근).

실행: python3 -m unittest tests.test_hira_criteria_seed
"""
import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, BASE / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


builder = _load("build_hira_criteria_seed")
loader = _load("seed_hira_criteria")
from agents.hira_criteria.schema import ensure_schema  # noqa: E402


def _make_source(path: Path, tag: str = "v1") -> None:
    c = sqlite3.connect(path)
    ensure_schema(c)
    c.execute("INSERT INTO hira_criteria_source(kind,sha256,title) VALUES ('kcd_master',?, 'kcd')", (tag,))
    c.execute("INSERT INTO kcd_code(code,name_kr) VALUES ('C34','폐암'), ('L20','아토피')")
    c.execute("INSERT INTO kcd_alias(code,name_kr) VALUES ('C34','폐암')")
    c.execute("INSERT INTO onco_criteria_regimen(regimen_key,cancer_code,cancer_name_kr,regimen_text) VALUES (?,'2','비소세포폐암','pembrolizumab')", (f"k-{tag}",))
    c.execute("INSERT INTO onco_criteria_regimen_drug(regimen_key,seq,ingredient) VALUES (?,1,'pembrolizumab')", (f"k-{tag}",))
    c.execute("INSERT INTO drug_criteria_item(item_key,title,criteria_text) VALUES (?,'Dupilumab','본문')", (f"d-{tag}",))
    c.execute("INSERT INTO criteria_section_kcd(subject_key,heading,level,kcd_code) VALUES (?, '1. 아토피',1,'L20')", (f"d-{tag}",))
    c.execute("INSERT INTO criteria_disease_kcd(subject_type,subject_key,kcd_code,method) VALUES ('drug_item',?,'L20','llm'), "
              "('onco_offlabel','x','C34','curated')", (f"d-{tag}",))
    c.commit()
    c.close()


class SeedLoaderTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)
        (self.root / "data" / "db").mkdir(parents=True)
        self.db = self.root / "data" / "db" / "drug_prices.db"
        # 운영 DB 모양: 급여 기준과 무관한 기존 테이블만
        c = sqlite3.connect(self.db)
        c.execute("CREATE TABLE other_t(a INTEGER)")
        c.execute("INSERT INTO other_t VALUES (1),(2),(3)")
        c.commit()
        c.close()
        self.src = self.root / "src.db"
        _make_source(self.src)
        meta = builder.build(self.src, self.root / "seed")
        loader.SEED_FILE = self.root / "seed" / builder.SEED_FILE.name
        loader.VERSION_FILE = self.root / "seed" / builder.VERSION_FILE.name
        self.meta = meta

    def tearDown(self):
        self.td.cleanup()

    def _count(self, t):
        c = sqlite3.connect(self.db)
        try:
            return c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        finally:
            c.close()

    def test_seed_excludes_offlabel_links(self):
        self.assertEqual(self.meta["counts"]["criteria_disease_kcd"], 1)        # onco_offlabel 연결은 시드에 없다

    def test_dry_run_changes_nothing(self):
        out = loader.apply(self.db, dry_run=True, if_changed=False)
        self.assertEqual(out["action"], "dry-run")
        c = sqlite3.connect(self.db)
        self.assertEqual([r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")], ["other_t"])

    def test_apply_creates_only_new_tables_and_is_idempotent(self):
        out = loader.apply(self.db, dry_run=False, if_changed=True)
        self.assertEqual(out["action"], "apply")
        self.assertEqual(self._count("kcd_code"), 2)
        self.assertEqual(self._count("other_t"), 3)                              # 기존 테이블 그대로
        self.assertEqual(loader.apply(self.db, dry_run=False, if_changed=True)["action"], "skip")
        self.assertTrue(Path(out["undo"]).exists())
        self.assertIn("kcd_code", json.loads(Path(out["undo"]).read_text())["UNDO_DROP_if_first_apply"])

    def test_manual_rows_survive_and_win(self):
        loader.apply(self.db, dry_run=False, if_changed=False)
        c = sqlite3.connect(self.db)
        c.execute("DELETE FROM criteria_disease_kcd")
        c.execute("INSERT INTO criteria_disease_kcd(subject_type,subject_key,kcd_code,method,note) "
                  "VALUES ('drug_item','d-v1','L20','manual','사람 확인'), ('drug_item','d-v1','Z99','llm','자동')")
        c.commit()
        c.close()
        loader.apply(self.db, dry_run=False, if_changed=False)                    # 재적용
        c = sqlite3.connect(self.db)
        rows = c.execute("SELECT kcd_code,method,note FROM criteria_disease_kcd ORDER BY kcd_code").fetchall()
        self.assertEqual(rows, [("L20", "manual", "사람 확인")])                   # manual 보존·우선, 자동 행은 시드로 교체

    def test_new_seed_version_reapplies_with_backup(self):
        loader.apply(self.db, dry_run=False, if_changed=True)
        _make_source(self.root / "src2.db", "v2")
        meta2 = builder.build(self.root / "src2.db", self.root / "seed")
        self.assertNotEqual(meta2["version"], self.meta["version"])
        out = loader.apply(self.db, dry_run=False, if_changed=True)
        self.assertEqual(out["action"], "apply")
        self.assertTrue(out["backup"] and Path(out["backup"]).exists())
        self.assertEqual(self._count("drug_criteria_item"), 1)

    def test_refuses_when_same_name_table_has_other_columns(self):
        c = sqlite3.connect(self.db)
        c.execute("CREATE TABLE kcd_code(x TEXT)")
        c.commit()
        c.close()
        out = loader.apply(self.db, dry_run=False, if_changed=False)
        self.assertEqual(out["action"], "refused")
        self.assertEqual(self._count("kcd_code"), 0)

    def test_failure_rolls_back_everything(self):
        c = sqlite3.connect(self.db)
        ensure_schema(c)
        c.execute("CREATE TRIGGER boom BEFORE INSERT ON drug_criteria_item BEGIN SELECT RAISE(ABORT,'boom'); END")
        c.execute("INSERT INTO kcd_code(code,name_kr) VALUES ('OLD','기존')")
        c.commit()
        c.close()
        with self.assertRaises(sqlite3.Error):
            loader.apply(self.db, dry_run=False, if_changed=False)
        c = sqlite3.connect(self.db)
        self.assertEqual(c.execute("SELECT code FROM kcd_code").fetchall(), [("OLD",)])     # 중간 실패 → 롤백
        self.assertFalse(c.execute("SELECT 1 FROM sqlite_master WHERE name='hira_criteria_seed_meta'").fetchone())


if __name__ == "__main__":
    unittest.main()
