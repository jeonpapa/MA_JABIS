"""급여 기준 화면 '정답 세트' 회귀 테스트 — 실제 본 DB(data/db/drug_prices.db) 로 돌린다.

페르소나 테스트(2026-10-29, 3회)에서 사용자가 실제로 물은 질문과, 원문으로 확인한 기대 답.
LLM 은 쓰지 않는다(질환 그룹·필터를 직접 넣는다). 월별 갱신(apply) 뒤 이 테스트가 깨지면
원문이 바뀌었는지(정상) 파싱이 깨졌는지(회귀) 사람이 확인한다.

실행: python3 -m unittest tests.test_hira_criteria_golden
"""
import sqlite3
import unittest
from pathlib import Path

from agents.hira_criteria import search as S

DB = Path(__file__).resolve().parents[1] / "data" / "db" / "drug_prices.db"


def _has_tables() -> bool:
    if not DB.exists():
        return False
    try:
        c = sqlite3.connect(DB)
        ok = c.execute("SELECT COUNT(*) FROM onco_criteria_regimen").fetchone()[0] > 0
        c.close()
        return ok
    except sqlite3.Error:
        return False


@unittest.skipUnless(_has_tables(), "본 DB 에 급여기준 테이블 없음")
class GoldenOnco(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = S.connect(DB)

    def test_head_neck_pembrolizumab_first_line(self):
        r = S.results(self.c, ["onco:16"], line=1, drugs=["pembrolizumab"])
        a = r["answers"][0]
        self.assertEqual(a["status"], "covered")
        self.assertTrue(any("CPS" in x["condition"] for x in a["rows"]))

    def test_her2_negative_gastric_excludes_her2_positive(self):
        r = S.results(self.c, ["onco:3"], line=1, markers=["HER2|-"])
        regs = " ".join(o["regimen"] for o in r["onco"])
        self.assertIn("nivolumab", regs)
        self.assertNotIn("trastuzumab", regs)

    def test_brca_ovarian_matches_parp(self):
        r = S.results(self.c, ["onco:10"], line=2, markers=["BRCA|+"])
        regs = [m["regimen"] for m in r["matches"]]
        self.assertTrue(any("olaparib" in x for x in regs))
        self.assertFalse(any("bevacizumab" in x for x in regs))           # 1차 유지(베바시주맙 병용) 전용은 재발 2차에 맞지 않음
        self.assertTrue(all("2차 이상" in m["condition"] for m in r["matches"]))   # 해당 항목(나. 백금민감성 재발)만 보여준다
        self.assertEqual(r["answers"], [])

    def test_no_offlabel_exposed(self):
        r = S.results(self.c, ["onco:3"], drugs=["pembrolizumab"])
        self.assertNotIn("offlabel", r)
        self.assertNotEqual(r["answers"][0]["status"], "offlabel_only")
        self.assertNotIn("offlabel", S.search_drug(self.c, "키트루다"))

    def test_nsclc_combo_rows_do_not_inherit_lazertinib_condition(self):
        r = S.results(self.c, ["onco:2"], drugs=["durvalumab", "pembrolizumab", "nivolumab"])
        per = [o for o in r["combo"] if o["setting_code"] in ("perioperative", "neoadjuvant")]
        self.assertTrue(per)
        for o in per:
            self.assertNotIn("L858R", o["target"])

    def test_endometrial_split_label_joined(self):
        r = S.results(self.c, ["onco:12"], drugs=["durvalumab"])
        lab = [o["added_condition"] for o in r["combo"] if "olaparib" in o["regimen"]]
        self.assertTrue(lab)
        self.assertIn("올라파립", lab[0])
        self.assertIn("pMMR", lab[0])

    def test_myeloma_transplant_ineligible(self):
        r = S.results(self.c, ["onco:31"], markers=["조혈모세포이식|-"])
        regs = [o["regimen"] for o in r["onco"]]
        self.assertFalse(any(o["setting_code"] == "maintenance" and "이식" in o["target"] for o in r["onco"]))
        self.assertFalse(any("아밀로이드" in o["target"] for o in r["onco"] + r["combo"]))
        combo = [o for o in r["combo"] if "daratumumab" in o["regimen"] and "bortezomib + lenalidomide" in o["regimen"]]
        self.assertTrue(combo and all("적합하지" in o["added_condition"] or "불가" in o["added_condition"] for o in combo))
        self.assertTrue(regs)
        bel = [o for o in r["combo"] if "belantamab" in o["regimen"]]
        self.assertTrue(bel and all("이식 불가" not in o["biomarkers"] for o in bel))

    def test_nsclc_perioperative_combo_both_classified(self):
        r = S.results(self.c, ["onco:2"], drugs=["durvalumab", "pembrolizumab"])
        per = [o for o in r["combo"] if "→" in o["regimen"] and ("durvalumab" in o["regimen"] or "pembrolizumab" in o["regimen"])]
        self.assertTrue(per)
        self.assertTrue(all(o["setting_code"] == "perioperative" for o in per))

    def test_compare_keytruda_opdivo_pdl1(self):
        r = S.compare(self.c, ["키트루다", "옵디보"], 0, "onco:2", 2)
        h = " ".join(r["highlights"])
        self.assertIn("≥10%", h)
        self.assertIn("≥50%", h)


@unittest.skipUnless(_has_tables(), "본 DB 에 급여기준 테이블 없음")
class GoldenGeneral(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = S.connect(DB)

    def test_psoriasis_not_psoriatic_arthritis(self):
        r = S.results(self.c, ["L40"])
        titles = " ".join(g["title"] for g in r["general"])
        self.assertNotIn("Golimumab", titles)          # 심퍼니: 건선성 관절염만
        self.assertNotIn("텍피데라", titles)            # 텍피데라: 다발경화증
        ada = next(g for g in r["general"] if "Adalimumab" in g["title"])
        self.assertIn("건선", ada["excerpt"])
        self.assertNotIn("건선성 관절염", ada["excerpt"])

    def test_diabetes_mentions_separated(self):
        r = S.results(self.c, ["E11"])
        titles = " ".join(g["title"] for g in r["general"])
        self.assertNotIn("Deflazacort", titles)
        self.assertTrue(any("Deflazacort" in m["title"] for m in r["mentioned"]))

    def test_diabetes_two_drug_table_has_header(self):
        t = self.c.execute("SELECT criteria_text FROM drug_criteria_item WHERE item_key='일반원칙|당뇨병용제'").fetchone()[0]
        self.assertRegex(t, r"\| 구 분 \| Metformin \| Sulfonylurea")

    def test_atopic_child_age_clause(self):
        age = S.parse_age("만 8세 아토피")
        r = S.results(self.c, ["L20"], drugs=["dupilumab", "lebrikizumab"], age=age)
        dup = next(g for g in r["general"] if "Dupilumab" in g["title"])
        leb = next(g for g in r["general"] if "Lebrikizumab" in g["title"])
        self.assertEqual(dup["age"]["status"], "match")
        self.assertIn("11세", dup["age"]["label"])
        self.assertEqual(leb["age"]["status"], "outside")

    def test_compare_atopic_weight(self):
        r = S.compare(self.c, ["듀피젠트", "레브리키주맙"], 0, "L20")
        self.assertIn("40kg", " ".join(r["highlights"]))

    def test_tnf_coverage(self):
        r = S.compare(self.c, ["휴미라", "레미케이드", "심퍼니"], 0)
        cov = {c["name"]: c["has"] for c in r["coverage"]}
        self.assertEqual(cov["건선성 관절염"], [True, True, True])
        self.assertEqual(cov["건선"], [True, True, False])

    def test_clarify_keeps_both_drugs_in_order(self):
        r = S.clarify(self.c, "키트루다랑 옵디보, 폐암 2차에서 뭐가 달라?", targets=["onco:2"], llm=lambda *a: ({}, {}))
        self.assertEqual([d["name"] for d in r["drugs"]], ["키트루다", "옵디보"])

    def test_generic_word_is_not_a_drug(self):
        self.assertEqual(S.resolve_drug(self.c, "병용")["inns"], [])
        r = S.clarify(self.c, "삼중음성 유방암 수술 전 키트루다 병용", llm=lambda *a: ({}, {}))
        self.assertEqual([d["name"] for d in r["drugs"]], ["키트루다"])

    def test_none_verdict_lists_where_it_is_covered(self):
        r = S.results(self.c, ["onco:9"], settings=["neoadjuvant"], drugs=["pembrolizumab"])
        a = r["answers"][0]
        self.assertEqual(a["status"], "none")
        self.assertTrue(a["elsewhere"])

    def test_endometrial_marker_per_regimen(self):
        r = S.results(self.c, ["onco:12"], drugs=["durvalumab"])
        by = {o["regimen_no"]: o["biomarkers"] for o in r["combo"] if o["regimen_no"] in ("38", "39")}
        self.assertIn("MSS/pMMR", by.get("39", []))
        self.assertNotIn("MSI-H/dMMR", by.get("39", []))

    def test_crohn_numeric_difference_not_called_same(self):
        r = S.compare(self.c, ["휴미라", "레미케이드"], 0, "kcd:K50")
        h = " ".join(r["highlights"])
        self.assertIn("2주", h)
        self.assertIn("4주", h)
        self.assertNotIn("핵심 수치가 같음", h)

    def test_focus_skips_drug_without_that_disease(self):
        r = S.compare(self.c, ["휴미라", "레미케이드", "심퍼니"], 0, "kcd:K50")
        self.assertEqual([len(x) for x in r["general"]], [1, 1, 0])      # 심퍼니에는 크론병 기준이 없다

    def test_age_clauses_in_document_order(self):
        r = S.results(self.c, ["L20"], age=S.parse_age("아토피 아이"))
        dup = next(g for g in r["general"] if "Dupilumab" in g["title"])
        t = dup["age"]["text"]
        self.assertLess(t.find("2) 만 6세"), t.find("3) 만 6개월"))

    def test_lenvatinib_is_pmmr(self):
        r = S.results(self.c, ["onco:12"], markers=["MSI-H/dMMR|-"])
        self.assertTrue(any("lenvatinib" in o["regimen"] for o in r["onco"]))      # '없는' → pMMR 대상
        self.assertEqual([m["label"] for m in S.biomarker_status("dMMR(mismatch repair deficient) 또는 MSI-H(microsatellite instability- high)가 없는 자궁내막암")],
                         ["MSS/pMMR"])

    def test_llm_cannot_invent_filters(self):
        fake = lambda *a: ({"filters": {"settings": ["adjuvant"], "line": 1, "markers": ["HER2|+"]}, "questions": [], "targets": ["onco:12"], "drugs": []}, {})  # noqa: E731
        r = S.clarify(self.c, "자궁내막암", llm=fake)
        self.assertEqual(r["filters"], {"settings": [], "line": None, "markers": []})

    def test_relapse_wording_keeps_inferred_second_line(self):
        """운영 검증에서 발견한 회귀: '백금 치료 후 재발한' 은 2차 이상 — 차수 숫자가 없어도 LLM 의 추론을 지우지 않는다."""
        fake = lambda *a: ({"filters": {"settings": [], "line": 2, "markers": ["BRCA|+"]}, "questions": [], "targets": ["onco:10"], "drugs": []}, {})  # noqa: E731
        r = S.clarify(self.c, "백금 치료 후 재발한 난소암, BRCA 변이", llm=fake)
        self.assertEqual(r["filters"]["line"], 2)
        self.assertEqual(r["filters"]["markers"], ["BRCA|+"])
        r2 = S.clarify(self.c, "난소암 BRCA 변이", llm=fake)                       # 재발·차수 표현이 없으면 지운다
        self.assertIsNone(r2["filters"]["line"])

    def test_first_line_combo_not_in_second_line(self):
        r = S.results(self.c, ["onco:2"], drugs=["nivolumab"], line=2)
        self.assertFalse(any("ipilimumab" in o["regimen"] for o in r["combo"]))

    def test_remicade_behcet_not_polluted_by_offlabel_dose(self):
        h = " ".join(S.compare(self.c, ["휴미라", "레미케이드"], 0)["highlights"])
        for line in h.split("[")[1:]:
            if "베체트 장염" in line:
                self.assertNotIn("5mg/kg", line)

    def test_atopic_all_diseases_no_false_adult_difference(self):
        h = " ".join(S.compare(self.c, ["듀피젠트", "레브리키주맙"], 0)["highlights"])
        self.assertNotIn("레브리키주맙만 만 18세", h)

    def test_ribociclib_numbers_restored(self):
        rows = self.c.execute("SELECT regimen_no FROM onco_criteria_regimen WHERE cancer_code='40' AND regimen_text LIKE 'ribociclib +%' "
                              "AND regimen_text NOT LIKE '%fulvestrant%'").fetchall()
        self.assertEqual(sorted(int(r[0]) for r in rows), [26, 27, 28, 29])

    def test_ulcerative_colitis_range_kept(self):
        h = " ".join(S.compare(self.c, ["휴미라", "심퍼니"], 0)["highlights"])
        self.assertIn("12~14주", h)

    def test_psa_no_mislabelled_prior_treatment_line(self):
        h = " ".join(S.compare(self.c, ["휴미라", "심퍼니"], 0)["highlights"])
        self.assertNotIn("건선성 관절염 — 이전 치료 기간", h)
        self.assertNotIn("000주", h)

    def test_compare_line_uses_disease_name(self):
        h = " ".join(S.compare(self.c, ["듀피젠트", "레브리키주맙"], 0)["highlights"])
        self.assertIn("아토피성 피부염 —", h)
        self.assertNotIn("해당 질환 기준", h)

    def test_clause_lines(self):
        cl = S.clause_lines("가. 1차 백금기반요법에 반응한 난소암\n나. 2차 이상의 백금기반요법에 반응한 재발성 난소암")
        self.assertEqual([c for _, c in cl], ["1L", "2L+"])

    def test_brand_first(self):
        r = S.search_drug(self.c, "텍피데라")
        g = r["general"][0]
        self.assertIn("텍피데라", g["title"])
        self.assertEqual([k["kcd_code"] for k in g["kcd"]], ["G35"])


if __name__ == "__main__":
    unittest.main()
