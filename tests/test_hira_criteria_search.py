"""agents/hira_criteria/search.py — 급여 기준 화면 로직 (임시 sqlite, 네트워크·실제 LLM 미사용).

python3 -m unittest tests.test_hira_criteria_search
"""
from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.hira_criteria import search as S  # noqa: E402
from agents.hira_criteria.schema import ensure_schema  # noqa: E402


def _db() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    ensure_schema(c)
    c.execute("CREATE TABLE product_alias_map (product_slug TEXT, inn TEXT, brand_aliases_json TEXT)")
    c.execute("CREATE TABLE drug_latest (product_name_kr TEXT, ingredient TEXT)")
    c.execute("INSERT INTO product_alias_map VALUES ('keytruda','pembrolizumab','[\"키트루다\",\"Keytruda\"]')")
    for name, ing in [("옵디보주(니볼루맙,유전자재조합)_(0.1g/10mL)", "nivolumab   0.1g"),
                      ("가브스정50밀리그램(빌다글립틴)", "vildagliptin   50mg"),
                      ("가브스메트정50/500밀리그램", "vildagliptin + metformin   50mg")]:
        c.execute("INSERT INTO drug_latest VALUES (?,?)", (name, ing))
    for code, name in [("C34", "기관지 및 폐의 악성 신생물"), ("C16", "위의 악성 신생물"), ("L20", "아토피성 피부염"),
                       ("M05", "혈청검사양성 류마티스관절염")]:
        c.execute("INSERT INTO kcd_code(code,name_kr) VALUES (?,?)", (code, name))
        c.execute("INSERT INTO kcd_alias(code,name_kr) VALUES (?,?)", (code, name))
    rows = [  # key, cancer_code, cancer, setting, line, regimen, target, extra
        ("k1", "2", "비소세포폐암", "palliative", "2L+", "pembrolizumab", "PD-L1 발현 비율 50% 이상, 이전 백금기반 화학요법 실패", "{}"),
        ("k2", "2", "비소세포폐암", "palliative", "2L+", "nivolumab", "PD-L1 발현 비율 10% 이상, 이전 백금기반 화학요법 실패", "{}"),
        ("k3", "2", "비소세포폐암", "palliative", "1L", "pembrolizumab", "PD-L1 50% 이상 EGFR 또는 ALK 변이가 없는 진행성", "{}"),
        ("k4", "3", "위암", "palliative", "1L", "trastuzumab + capecitabine + cisplatin", "HER2 양성 전이성 위 선암", "{}"),
        ("k5", "3", "위암", "palliative", "1L+", "capecitabine + oxaliplatin", "진행성 위암", "{}"),
        ("k6", "3", "위암", "palliative", "1L", "nivolumab + capecitabine + oxaliplatin", "HER2 음성 위암", "{}"),
        ("k7", "40", "기타 항암제 병용요법", "neoadjuvant", "NA", "pembrolizumab + paclitaxel", "stage Ⅱ∼Ⅲ",
         '{"row_cancer": "유방암", "label": "pembrolizumab 고위험 조기 삼중음성 유방암", "base_regimen": "AC → paclitaxel"}'),
        ("k8", "9", "유방암", "palliative", "1L", "paclitaxel", "전이성 유방암", "{}"),
    ]
    for k, cc, cn, st, ln, reg, tg, ex in rows:
        c.execute("INSERT INTO onco_criteria_regimen(regimen_key,cancer_code,cancer_name_kr,setting_code,line_code,regimen_no,"
                  "regimen_text,target_text,self_pay,subgroup_raw,last_notice_no,last_effective,extra_json,drugs_json) "
                  "VALUES (?,?,?,?,?,'1',?,?,'','','제2026-88호','2026-04-02',?,'[]')", (k, cc, cn, st, ln, reg, tg, ex))
        for i, d in enumerate(reg.split(" + "), 1):
            c.execute("INSERT INTO onco_criteria_regimen_drug VALUES (?,?,?)", (k, i, d.strip()))
    c.execute("INSERT INTO drug_criteria_item(item_key,class_no,title,ingredient,criteria_text,gosi_no,effective_date) VALUES "
              "('d1','142','Dupilumab 주사제','Dupilumab','만 18세 이상 중증 아토피피부염 요양급여를 인정','고시 제2026-159호','2026-08-01')")
    c.execute("INSERT INTO criteria_disease_kcd(subject_type,subject_key,kcd_code,method,review_status) VALUES ('drug_item','d1','L20','llm','auto')")
    c.execute("INSERT INTO drug_criteria_item(item_key,class_no,title,ingredient,criteria_text,gosi_no,effective_date) VALUES "
              "('d2','396','당뇨병용제','', '가. 2제요법: metformin + vildagliptin 인정','고시 제2026-117호','2026-06-01')")
    c.execute("INSERT INTO drug_criteria_item(item_key,class_no,title,ingredient,criteria_text,gosi_no,effective_date) VALUES "
              "('d3','396','Metformin 경구제','Metformin','다낭성난소증후군 허가초과','고시 제2017-180호','2017-10-01')")
    S._CANCER_NAMES.clear()
    S._load_cancer_names(c)
    return c


class BasicsTest(unittest.TestCase):
    def test_line_semantics(self):
        self.assertTrue(S.line_matches("1L+", 2))
        self.assertFalse(S.line_matches("1L", 2))
        self.assertTrue(S.line_matches("2L_or_3L", 3))
        self.assertTrue(S.line_matches("NA", 2))

    def test_biomarker_polarity(self):
        v = {m["value"] for m in S.biomarker_status("EGFR 또는 ALK 변이가 없는 진행성, PD-L1 발현 양성")}
        self.assertEqual(v, {"EGFR|-", "ALK|-", "PD-L1|+"})
        self.assertEqual([m["value"] for m in S.biomarker_status("HER2 음성 위암")], ["HER2|-"])
        self.assertEqual(S.biomarker_status("이전 PD-1 inhibitor, PD-L1 inhibitor 치료를 받지 않은"), [])
        self.assertEqual([m["value"] for m in S.biomarker_status("필라델피아 염색체 음성 B세포")], ["필라델피아 염색체|-"])

    def test_marker_window_and_conditional(self):
        lab = [m["label"] for m in S.biomarker_status("gBRCA 변이 HER2-음성 고위험 조기 유방암")]
        self.assertEqual(sorted(lab), ["BRCA 변이", "HER2 음성"])           # '음성' 은 HER2 것
        lab = [m["label"] for m in S.biomarker_status("※ EGFR 또는 ALK 변이가 확인된 환자는 이러한 변이에 대한 승인된 치료제 투여 후")]
        self.assertEqual(lab, [])                                            # 조건부 문장은 요구 조건 아님
        self.assertEqual([m["value"] for m in S.biomarker_status("조혈모세포 이식이 불가능한 환자")], ["조혈모세포이식|-"])

    def test_biomarker_is_not_a_drug(self):
        c = _db()
        self.assertEqual(S.resolve_drug(c, "HER2")["inns"], [])
        self.assertEqual(S._resolve_names(c, ["BRCA", "키트루다"]), [{"name": "키트루다", "inns": ["pembrolizumab"]}])

    def test_punctuation_not_a_difference(self):
        self.assertFalse(S._has_diff(S.text_diff("‘투약일지’를 작성", "'투약일지'를 작성")))

    def test_footnotes_removed_but_weekly_kept(self):
        self.assertEqual(S.clean("PD-L1 ≧ 50%주3 이상, 주1회 투여, 4주기"), "PD-L1 ≧ 50% 이상, 주1회 투여, 4주기")

    def test_diff_ignores_spacing_and_numbering(self):
        d = S.text_diff("가) 요양급여를 인정함\n나) 6개월", "1) 요양 급여를  인정함\n2) 6개월")
        self.assertFalse(S._has_diff(d), d)
        d = S.text_diff("PD-L1 50% 이상", "PD-L1 10% 이상, 2주 간격")
        self.assertTrue(any(x["op"] == "del" and "50%" in x["text"] for x in d))
        self.assertTrue(any(x["op"] == "add" and "2주" in x["text"] for x in d))

    def test_section_alignment(self):
        base = "가. 류마티스관절염\nDMARDs 2종 6개월\n나. 크론병\n기존 치료 실패"
        other = "가. 크론병\n기존 치료 실패\n나. 류마티스관절염\nDMARDs 2종 3개월\n다. 가와사키병\n불응성"
        st = {b["heading"]: b["status"] for b in S.section_diff(base, other)}
        self.assertEqual(st["가. 크론병"], "same")
        self.assertEqual(st["나. 류마티스관절염"], "changed")
        self.assertEqual(st["다. 가와사키병"], "only_this")


class DrugTest(unittest.TestCase):
    def setUp(self):
        self.c = _db()

    def test_resolve(self):
        self.assertEqual(S.resolve_drug(self.c, "키트루다")["inns"], ["pembrolizumab"])
        self.assertEqual(S.resolve_drug(self.c, "니볼루맙")["inns"], ["nivolumab"])
        self.assertEqual(S.resolve_drug(self.c, "가브스")["inns"], ["vildagliptin"])   # 복합제(가브스메트) 무시

    def test_bundle_mention(self):
        r = S.search_drug(self.c, "가브스")
        self.assertEqual([(g["title"], g["match"]) for g in r["general"]], [("당뇨병용제", "mentioned")])


class DiseaseTest(unittest.TestCase):
    def setUp(self):
        self.c = _db()

    def test_groups(self):
        ids = {g["id"] for g in S.disease_groups(self.c)}
        self.assertIn("onco:2", ids)
        self.assertIn("L20", ids)
        self.assertNotIn("C34", ids)        # 항암 부위 코드는 암종 그룹에 흡수

    def test_her2_negative_keeps_untargeted(self):
        r = S.results(self.c, ["onco:3"], line=1, markers=["HER2|-"])
        keys = {o["key"] for o in r["onco"]}
        self.assertEqual(keys, {"k5", "k6"})     # HER2 양성 요법(k4)만 제외, 조건 없는 k5 유지

    def test_combo_row_belongs_to_real_cancer_and_verdict(self):
        r = S.results(self.c, ["onco:9"], drugs=["pembrolizumab"])
        self.assertEqual([o["key"] for o in r["combo"]], ["k7"])
        self.assertEqual(r["answers"][0]["status"], "combo_only")
        self.assertIn("삼중음성", r["combo"][0]["added_condition"])
        r = S.results(self.c, ["onco:2"], line=2, drugs=["pembrolizumab"])
        self.assertEqual(r["answers"][0]["status"], "covered")
        self.assertIn("50%", r["answers"][0]["rows"][0]["condition"])
        self.assertEqual({o["key"] for o in r["onco"] if o["focus"]}, {"k1"})

    def test_clarify_validates_llm_output(self):
        calls = []

        def fake(system, user, mt):
            calls.append(1)
            if len(calls) == 1:
                return {"interpretation": "폐암 2차 키트루다", "targets": ["onco:99", "onco:2"], "ambiguous": False,
                        "drugs": ["키트루다"]}, {}
            return {"filters": {"settings": ["bogus"], "line": 2, "markers": ["PD-L1|+", "XYZ|+"]},
                    "keywords": ["백금"], "questions": [{"field": "settings", "text": "?", "options": [{"label": "a", "value": "palliative"}]}],
                    "summary": "ok"}, {}
        r = S.clarify(self.c, "PD-L1 양성 폐암 2차 키트루다", llm=fake)
        self.assertEqual([t["id"] for t in r["targets"]], ["onco:2"])
        self.assertEqual(r["filters"], {"settings": [], "line": 2, "markers": ["PD-L1|+"]})
        self.assertEqual(r["questions"], [])             # 차수가 정해지면 투여요법은 묻지 않음
        self.assertEqual(r["drugs"], [{"name": "키트루다", "inns": ["pembrolizumab"]}])

    def test_clarify_tolerates_malformed_llm_output(self):
        calls = []

        def odd(system, user, mt):
            calls.append(1)
            if len(calls) == 1:
                return {"targets": "onco:2", "drugs": "키트루다", "ambiguous": "no"}, {}
            return {"filters": {"line": [2], "settings": "palliative", "markers": None},
                    "questions": "없음", "keywords": "백금"}, {}
        r = S.clarify(self.c, "폐암 2차", llm=odd)
        self.assertEqual(r["filters"]["line"], 2)
        self.assertEqual([t["id"] for t in r["targets"]], ["onco:2"])

    def test_clarify_llm_failure_falls_back(self):
        def broken(*_a):
            raise RuntimeError("down")
        r = S.clarify(self.c, "아토피", llm=broken)
        self.assertEqual([t["id"] for t in r["targets"]], ["L20"])


class CompareTest(unittest.TestCase):
    def test_statuses(self):
        r = S.compare(_db(), ["키트루다", "니볼루맙"], 0)
        cells = {(o["cancer"], o["line_code"]): [e["status"] for e in o["entries"]] for o in r["onco"]}
        self.assertEqual(cells[("비소세포폐암", "2L+")], ["base", "changed"])
        self.assertEqual(cells[("비소세포폐암", "1L")], ["base", "only_base"])
        self.assertEqual(cells[("위암", "1L")], ["none", "only_this"])


if __name__ == "__main__":
    unittest.main()
