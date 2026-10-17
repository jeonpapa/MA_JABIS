"""수동 정정(raw_source='manual_correction') 허가일은 자동 upsert 가 덮어쓰지 않는다."""
import sqlite3
import tempfile
import unittest
from pathlib import Path



class _DB:
    def __init__(self, path):
        self.path = path

    def _connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


class ManualCorrectionGuardTests(unittest.TestCase):
    def setUp(self):
        import importlib.util  # agents.db 패키지 __init__ 은 pandas 를 요구하므로 파일을 직접 로드
        spec = importlib.util.spec_from_file_location(
            "_indications_mod", Path(__file__).resolve().parent.parent / "agents" / "db" / "indications.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mixin = next(v for v in vars(mod).values() if isinstance(v, type) and hasattr(v, "upsert_indication_agency"))
        self.tmp = tempfile.TemporaryDirectory()
        path = Path(self.tmp.name) / "t.db"
        with sqlite3.connect(path) as c:
            c.execute("""CREATE TABLE indications_by_agency (id INTEGER PRIMARY KEY, indication_id TEXT, agency TEXT,
                biomarker_label TEXT, combination_label TEXT, approval_date TEXT, label_excerpt TEXT, label_url TEXT,
                restriction_note TEXT, raw_source TEXT, fetched_at TEXT, date_source TEXT, label_full_text TEXT,
                UNIQUE(indication_id, agency))""")
        self.db = type("DB", (mixin, _DB), {})(path)
        self.path = path

    def tearDown(self):
        self.tmp.cleanup()

    def row(self):
        with sqlite3.connect(self.path) as c:
            return c.execute("SELECT approval_date, raw_source, restriction_note FROM indications_by_agency").fetchone()

    def test_auto_upsert_updates_normal_rows(self):
        self.db.upsert_indication_agency({"indication_id": "k_ev", "agency": "MFDS", "approval_date": "2018-01-24"})
        self.db.upsert_indication_agency({"indication_id": "k_ev", "agency": "MFDS", "approval_date": "2019-01-01"})
        self.assertEqual(self.row()[0], "2019-01-01")

    def test_manual_correction_is_preserved(self):
        self.db.upsert_indication_agency({"indication_id": "k_ev", "agency": "MFDS", "approval_date": "2024-07-25",
                                          "raw_source": "manual_correction", "restriction_note": "근거 메모"})
        self.db.upsert_indication_agency({"indication_id": "k_ev", "agency": "MFDS", "approval_date": "2018-01-24",
                                          "raw_source": "mfds_segment", "restriction_note": None})
        self.assertEqual(self.row(), ("2024-07-25", "manual_correction", "근거 메모"))


if __name__ == "__main__":
    unittest.main()
