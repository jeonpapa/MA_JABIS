"""agents/hira_criteria 항암 트랙 — 파서·정규화·최신 공고 대조·본 DB 반영 계약.

네트워크·실제 DB 미접근(임시 sqlite). python3 -m unittest tests.test_hira_criteria_onco
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from agents.hira_criteria import onco_kcd, onco_parser as P, store  # noqa: E402
from agents.hira_criteria.onco_pipeline import latest_notice_check  # noqa: E402
from agents.hira_criteria.schema import ensure_schema  # noqa: E402

MINI_MD = """CONTENTS
1. 소세포폐암 \t13 2. 비소세포폐암 \t15 3. 위암 \t25 4. 식도암 \t30 5. 갑상선암 \t34 6. 췌장암 \t35 7-1. 간암 \t37 7-2. 담도암 \t40 8. 직결장암 \t43 9. 유방암 \t48 10. 난소암/난관암/일차복막암 \t 65 11. 자궁경부암 \t70 12. 자궁암 \t72 13. 신장암 \t75 14. 요로상피암 \t77 15. 전립선암 \t81 16. 두경부암 \t85 17-1. 신경내분비신생물 \t90 17-2. 메르켈세포암 \t92 18. 피부암 \t93 19. 골암 \t94

1\t소세포폐암(Small Cell Lung Cancer)

1. 수술후보조요법(adjuvant)

| 연번 | 항암요법 |
| --- | --- |
| 1 | etoposide + cisplatin (제2006-3호: 2006.4.1.) |

2\t비소세포폐암(Non-Small Cell Lung Cancer)

4. 고식적요법(palliative)
- 가. 투여단계: 1차(first-line)

<table>
<tr><th>연번</th><th>항암요법</th><th>투여대상</th></tr>
<tr><td rowspan="2">4</td><td>pembrolizumab주1</td><td rowspan="2">PD-L1 발현 양성(발현 비율 ≧ 50%주3)이면서,<br>EGFR 또는 ALK 변이가 없는<br>진행성 (stage Ⅳ)<br>※ 관해공고요법으로 durvalumab 치료 실패 시 급여 불가함</td></tr>
<tr><td>(제2022-38호: 2022.3.1.,<br>개정 제2026-88호: 2026.4.2.)</td></tr>
</table>

- 다. 투여단계: 2차 이상

<table>
<tr><th>연번</th><th>항암요법</th><th>투여대상</th></tr>
<tr><td rowspan="2">4</td><td>pembrolizumab주1</td><td rowspan="2">이전 백금기반 화학요법에 경험이 있는 환자로 질병<br>진행이 확인된 환자 (stage ⅢB 이상)</td></tr>
<tr><td>(제2017-184호: 2017.8.21.)</td></tr>
</table>

3\t위암(Gastric Cancer)

2. 고식적요법(palliative)

<table>
<tr><th>연번</th><th>항암요법</th><th>투여대상</th><th>투여단계</th></tr>
<tr><td rowspan="3">29</td><td rowspan="2">bevacizumab + paclitaxel + carboplatin</td><td>가. 1차 대상</td><td>1차</td></tr>
<tr><td>나. 재발 대상</td><td>2차</td></tr>
<tr><td>(제2014-96호: 2014.6.1.)</td><td></td><td></td></tr>
<tr><td rowspan="4">24<br>25</td><td>pembrolizumab + paclitaxel</td><td rowspan="4">전이성</td><td rowspan="4">1차 이상</td></tr>
<tr><td>(제2025-133호: 2025.6.5.)</td></tr>
<tr><td>pembrolizumab + gemcitabine + carboplatin</td></tr>
<tr><td>(제2025-133호: 2025.6.5.)</td></tr>
</table>

# 항구토제
"""


class NormalizeTest(unittest.TestCase):
    def test_line_code(self):
        cases = {"1차(first-line)": "1L", "2차 이상": "2L+", "1차 또는 1차 이상": "1L_or_1L+",
                 "2차<br>또는<br>3차": "2L_or_3L", "-": "NA", "": "NA", "1차 이상 / 2차 이상": "1L+|2L+",
                 "3차 이상": "3L+"}
        for raw, want in cases.items():
            self.assertEqual(P.line_code(raw), want, raw)

    def test_setting_code(self):
        self.assertEqual(P.setting_code("N, A, P"), "neoadjuvant|adjuvant|palliative")
        self.assertEqual(P.setting_code("P, S"), "palliative|salvage")
        self.assertEqual(P.setting_code("선행화학요법(neoadjuvant)"), "neoadjuvant")
        self.assertEqual(P.setting_code("수술후보조요법(adjuvant 요법)"), "adjuvant")
        self.assertEqual(P.setting_code("동시항암화학방사선요법(concurrent chemoradiotherapy)"), "ccrt")
        self.assertEqual(P.setting_code("-"), "NA")

    def test_line_from_text_only_parenthesized(self):
        self.assertEqual(P.line_from_text("전이성 (1차 이상, 고식적요법)"), "1L+")
        # 서술 속 '1차 백금 기반' 은 투여단계가 아니다 (유지요법 오판 회귀 방지)
        self.assertIsNone(P.line_from_text("1차 백금 기반 항암화학요법에 반응(CR 또는 PR)한"))

    def test_split_drugs(self):
        self.assertEqual(P.split_drugs("bevacizumab(100/100) + paclitaxel + carboplatin"),
                         ["bevacizumab", "paclitaxel", "carboplatin"])
        self.assertEqual(P.split_drugs("[Neoadjuvant] nivolumab + platinum → nivolumab"), ["nivolumab", "platinum"])


class ParseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.recs, cls.stats = P.parse(MINI_MD)

    def test_cancers_located(self):
        # 목차 20개 중 본문에 있는 3개만 찾고, 없는 암종은 조용히 버리지 않고 missing 으로 보고한다
        self.assertEqual(self.stats["located_cancers"], 3)
        self.assertIn("4", self.stats["missing_cancers"])
        self.assertEqual({r.cancer_code for r in self.recs}, {"1", "2", "3"})

    def test_nsclc_lines_and_history(self):
        nsclc = [r for r in self.recs if r.cancer_code == "2"]
        self.assertEqual({(r.regimen_text, r.line_code) for r in nsclc}, {("pembrolizumab", "1L"), ("pembrolizumab", "2L+")})
        first = next(r for r in nsclc if r.line_code == "1L")
        self.assertEqual(first.setting_code, "palliative")
        self.assertEqual((first.last_notice_no, first.last_effective), ("제2026-88호", "2026-04-02"))
        self.assertIn("durvalumab", first.prior_therapy)
        self.assertEqual(first.footnotes, "주1")
        self.assertNotIn("주3", first.biomarker_text)

    def test_rowspan_variants_and_merged_numbers(self):
        g = [r for r in self.recs if r.cancer_code == "3"]
        bev = [r for r in g if r.regimen_text.startswith("bevacizumab")]
        self.assertEqual([r.line_code for r in bev], ["1L", "2L"])        # 한 연번 안 투여단계 변형 2개
        self.assertTrue(all(r.last_notice_no == "제2014-96호" for r in bev))
        nos = {r.regimen_text: r.regimen_no for r in g if r.regimen_text.startswith("pembrolizumab")}
        self.assertEqual(nos, {"pembrolizumab + paclitaxel": "24", "pembrolizumab + gemcitabine + carboplatin": "25"})

    def test_pipe_table_and_inline_notice(self):
        sc = [r for r in self.recs if r.cancer_code == "1"]
        self.assertEqual(len(sc), 1)
        self.assertEqual(sc[0].regimen_text, "etoposide + cisplatin")
        self.assertEqual(sc[0].last_notice_no, "제2006-3호")
        self.assertEqual(sc[0].setting_code, "adjuvant")


class LatestNoticeTest(unittest.TestCase):
    def test_new_must_exist_deleted_must_not(self):
        rows = [{"cancer_name_kr": "담도암", "regimen_text": "ivosidenib", "extra_json": "{}"}]
        notice = {"notice_no": "공고 제2026-232호", "effective_date": "2026-11-01",
                  "summary": "<신설>- 담도암에 ‘Ivosidenib’ 단독요법<변경>- ‘Cabozantinib’<삭제> ㆍ 난소암: ‘Bevacizumab + Olaparib’"}
        out = latest_notice_check(rows, notice, "2026-11-01")
        self.assertEqual(out["status"], "current")
        self.assertIn("[신설] Ivosidenib", out["notice_items_found"])
        self.assertIn("[변경] Cabozantinib", out["notice_items_missing"])
        self.assertEqual(out["deleted_absent_ok"], ["Bevacizumab + Olaparib"])
        self.assertEqual(latest_notice_check(rows, notice, "2026-10-01")["status"], "stale")


class KcdMapTest(unittest.TestCase):
    def test_row_level_mapping(self):
        m = onco_kcd.kcd_for_regimen({"cancer_code": "40", "cancer_name_kr": "기타", "target_text": "",
                                      "extra": {"row_cancer": "비소세포폐암"}})
        self.assertEqual([x["kcd_code"] for x in m], ["C34"])
        m = onco_kcd.kcd_for_regimen({"cancer_code": "40", "cancer_name_kr": "기타", "target_text": "",
                                      "extra": {"label": "nivolumab 절제 가능한 비소세포폐암 환자의"}})
        self.assertEqual([x["kcd_code"] for x in m], ["C34"])
        m = onco_kcd.kcd_for_regimen({"cancer_code": "38", "cancer_name_kr": "전처치", "target_text": "", "extra": {}})
        self.assertEqual(m[0]["note"], "not_applicable")

    def test_low_confidence_needs_review(self):
        m = onco_kcd.kcd_for_regimen({"cancer_code": "17-1", "cancer_name_kr": "신경내분비", "target_text": "", "extra": {}})
        self.assertTrue(all(x["review_status"] == "needs_review" for x in m))

    def test_dictionary_codes_exist_in_staging_kcd(self):
        common = store.staging_path("common")
        if not common.exists():
            self.skipTest("staging common.db 없음")
        c = sqlite3.connect(":memory:")
        c.execute("ATTACH DATABASE ? AS common", (str(common),))
        self.assertEqual(onco_kcd.validate(c), [])


class ApplyContractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.p = mock.patch.object(store, "STAGING_DIR", d / "staging")
        self.p.start()
        self.main = d / "main.db"

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def _staging(self, n_rows: int):
        c = sqlite3.connect(store.staging_path("onco"))
        ensure_schema(c)
        for i in range(n_rows):
            c.execute("INSERT INTO onco_criteria_regimen(regimen_key, regimen_text) VALUES (?, 'x')", (f"k{i}",))
            c.execute("INSERT INTO onco_criteria_regimen_drug VALUES (?,1,'x')", (f"k{i}",))
            c.execute("INSERT INTO criteria_disease_kcd(subject_type,subject_key,kcd_code,method) VALUES ('onco_regimen',?, 'C34','curated')", (f"k{i}",))
        c.execute("INSERT INTO onco_offlabel_regimen(regimen_code) VALUES ('1001')")
        c.execute("INSERT INTO onco_offlabel_rejected(seq) VALUES (1)")
        c.commit()
        c.close()

    def test_manual_rows_preserved(self):
        m = sqlite3.connect(self.main)
        ensure_schema(m)
        m.execute("INSERT INTO criteria_disease_kcd(subject_type,subject_key,kcd_code,method) VALUES ('onco_regimen','human','C50','manual')")
        m.execute("INSERT INTO criteria_disease_kcd(subject_type,subject_key,kcd_code,method) VALUES ('onco_regimen','old','C16','curated')")
        m.commit()
        m.close()
        self._staging(3)
        rep = store.apply_to_main(["onco"], self.main)
        self.assertEqual(rep["onco_criteria_regimen"], 3)
        m = sqlite3.connect(self.main)
        keys = {r[0] for r in m.execute("SELECT subject_key FROM criteria_disease_kcd")}
        self.assertIn("human", keys)
        self.assertNotIn("old", keys)

    def test_empty_staging_refused(self):
        self._staging(0)
        m = sqlite3.connect(self.main)
        ensure_schema(m)
        m.execute("INSERT INTO onco_criteria_regimen(regimen_key) VALUES ('keep')")
        m.commit()
        m.close()
        with self.assertRaises(RuntimeError):
            store.apply_to_main(["onco"], self.main)
        m = sqlite3.connect(self.main)
        self.assertEqual(m.execute("SELECT COUNT(*) FROM onco_criteria_regimen").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
