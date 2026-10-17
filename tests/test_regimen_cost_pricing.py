"""투약비용비교(/regimen-cost) 가격·비용 공용 로직 단위 테스트.

agents/regimen_export.py 의 순수 함수(DB/flask/pandas 비의존)와
agents/db/onco.py 의 search_regimen_drugs(인메모리 sqlite)를 검증한다.
실행: python3 -m unittest tests.test_regimen_cost_pricing
"""
from __future__ import annotations

import importlib.util
import sqlite3
import unittest
from pathlib import Path

from agents import regimen_export as rx

ROOT = Path(__file__).resolve().parents[1]


def _load_onco_mixin():
    # agents.db 패키지 __init__ 은 무거운 의존성을 끌어오므로 onco.py 만 직접 로드
    spec = importlib.util.spec_from_file_location("_onco_mod", ROOT / "agents" / "db" / "onco.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._OncoMixin


class FriendlyReasonTest(unittest.TestCase):
    def test_env_var_name_never_shown(self):
        out = rx.friendly_price_reason("INGREDIENT_WAP_KEY 미설정 — 주성분 가중평균 비활성")
        self.assertNotIn("INGREDIENT_WAP_KEY", out)
        self.assertNotIn("_KEY", out)
        self.assertIn("연결 전", out)

    def test_http_error_is_generic(self):
        self.assertIn("일시 오류", rx.friendly_price_reason("WAP API HTTP 502"))

    def test_empty_reason(self):
        self.assertEqual(rx.friendly_price_reason(None), "국내 약가 매칭 없음")

    def test_plain_reason_kept(self):
        self.assertEqual(rx.friendly_price_reason("해당 기준일 국내 상한금액 없음"), "해당 기준일 국내 상한금액 없음")


class PriceKeyTest(unittest.TestCase):
    def test_parenthetical_qualifier_stripped(self):
        self.assertIn("Fluorouracil", rx.price_lookup_keys("Fluorouracil(bolus)"))
        self.assertIn("Methotrexate", rx.price_lookup_keys("Methotrexate(HD)"))
        self.assertIn("Octreotide", rx.price_lookup_keys("Octreotide LAR"))

    def test_alias_first(self):
        self.assertEqual(rx.price_lookup_keys("nab-Paclitaxel")[0], "paclitaxel(albumin-bound)")

    def test_combo_first_component(self):
        self.assertIn("Trifluridine", rx.price_lookup_keys("Trifluridine/Tipiracil"))

    def test_plain_inn(self):
        self.assertEqual(rx.price_lookup_keys("Pembrolizumab"), ["Pembrolizumab"])

    def test_name_part_and_inn(self):
        self.assertEqual(rx.ingredient_name_part("pembrolizumab   0.1g(25mg/mL)"), "pembrolizumab")
        self.assertEqual(rx.inn_from_ingredient_text("doxorubicin hydrochloride   10mg(2mg/mL)"), "doxorubicin")
        self.assertEqual(rx.inn_from_ingredient_text("pembrolizumab   0.1g(25mg/mL)"), "pembrolizumab")


class RankCandidatesTest(unittest.TestCase):
    def test_conventional_paclitaxel_before_albumin_bound(self):
        cands = [
            {"product_name_kr": "아브락산주(파클리탁셀)_(0.1g/1병)", "ingredient": "paclitaxel(albumin-bound)   0.1g"},
            {"product_name_kr": "탁솔주(파클리탁셀)_(30mg/5mL)", "ingredient": "paclitaxel   30mg(6mg/mL)"},
        ]
        out = rx.rank_domestic_candidates(cands, "Paclitaxel", "IV")
        self.assertTrue(out[0]["product_name_kr"].startswith("탁솔주"))

    def test_oral_route_prefers_tablet_and_drops_eye_drops(self):
        cands = [
            {"product_name_kr": "프레테솔점안액(프레드니솔론아세테이트)_(0.1g/10mL)", "ingredient": "prednisolone acetate   0.1g(10mg/mL)"},
            {"product_name_kr": "피알디현탁시럽0.1%(프레드니솔론)_(0.5g/500mL)", "ingredient": "prednisolone   0.5g(1mg/mL)"},
            {"product_name_kr": "소론도정(프레드니솔론)_(5mg/1정)", "ingredient": "prednisolone   5mg"},
        ]
        out = rx.rank_domestic_candidates(cands, "Prednisolone", "PO")
        self.assertTrue(out[0]["product_name_kr"].startswith("소론도정"))
        self.assertFalse(any("점안" in c["product_name_kr"] for c in out))

    def test_salt_counts_as_exact(self):
        cands = [
            {"product_name_kr": "아비테론듀오정(아비라테론,프레드니솔론)_(1정)", "ingredient": "abiraterone acetate   0.5g, prednisolone   2.5mg"},
            {"product_name_kr": "자이티가정500밀리그램_(0.5g/1정)", "ingredient": "abiraterone acetate   0.5g"},
        ]
        out = rx.rank_domestic_candidates(cands, "Abiraterone", "PO")
        self.assertTrue(out[0]["product_name_kr"].startswith("자이티가"))

    def test_order_preserved_within_same_score(self):
        cands = [{"product_name_kr": f"도세탁셀주{i}_(80mg/4mL)", "ingredient": "docetaxel   80mg(20mg/mL)"} for i in range(3)]
        self.assertEqual(rx.rank_domestic_candidates(cands, "Docetaxel", "IV"), cands)


PEMBRO_PM = {"available": True, "source": "domestic", "unit_price": 2100556, "content_mg": 100.0,
             "price_per_mg": 21005.56}


class ComputeDrugCostTest(unittest.TestCase):
    def test_pembrolizumab_flat_dose(self):
        d = {"dose_value": 200, "unit": "mg", "cycle_total_mg": 200.0, "cycle_days": 21, "total_cycles": None}
        res = rx.compute_drug_cost(d, PEMBRO_PM)
        self.assertEqual(res["cost"]["cycle"], 4201112)
        self.assertEqual(res["cost"]["monthly"], round(4201112 * 30 / 21))
        self.assertIsNone(res["cost"]["course"])          # 총사이클 미입력 → 코스 미산출 (₩0 금지)
        self.assertEqual(res["course_reason"], "총사이클 미입력")
        self.assertIsNone(res["missing_reason"])

    def test_course_when_total_cycles(self):
        d = {"dose_value": 200, "unit": "mg", "cycle_total_mg": 200.0, "cycle_days": 21, "total_cycles": 35}
        self.assertEqual(rx.compute_drug_cost(d, PEMBRO_PM)["cost"]["course"], round(4201112.0 * 35))

    def test_price_unavailable_gives_reason_not_zero(self):
        d = {"dose_value": 200, "unit": "mg", "cycle_total_mg": 200.0, "cycle_days": 21}
        res = rx.compute_drug_cost(d, {"available": False, "reason": "INGREDIENT_WAP_KEY 미설정"})
        self.assertTrue(all(v is None for v in res["cost"].values()))
        self.assertNotIn("INGREDIENT_WAP_KEY", res["missing_reason"])

    def test_missing_dose(self):
        d = {"dose_value": None, "unit": "mg", "cycle_total_mg": None, "cycle_days": 1}
        self.assertEqual(rx.compute_drug_cost(d, PEMBRO_PM)["missing_reason"], "용량 정보 없음")

    def test_unsupported_unit(self):
        d = {"dose_value": 5, "unit": "MBq", "cycle_total_mg": None, "cycle_days": 28}
        self.assertIn("단위", rx.compute_drug_cost(d, PEMBRO_PM)["missing_reason"])

    def test_count_unit(self):
        d = {"dose_value": 2, "unit": "정", "per_cycle": 1, "cycle_total_mg": None, "cycle_days": 1}
        res = rx.compute_drug_cost(d, {"available": True, "unit_price": 1000, "content_mg": 500, "price_per_mg": 2})
        self.assertEqual(res["cost"]["daily"], 2000)

    def test_missing_cycle_days(self):
        d = {"dose_value": 200, "unit": "mg", "cycle_total_mg": 200.0, "cycle_days": None}
        self.assertEqual(rx.compute_drug_cost(d, PEMBRO_PM)["missing_reason"], "주기(일) 미입력")


class SumTotalsTest(unittest.TestCase):
    def test_all_missing_is_none_not_zero(self):
        empty = {"cycle": None, "course": None, "monthly": None, "yearly": None, "daily": None}
        t = rx.sum_regimen_totals([empty])
        for k in ("cycle", "course", "monthly", "yearly", "daily"):
            self.assertIsNone(t[k])
        self.assertTrue(t["hasMissing"])
        self.assertEqual(t["missingCount"], 1)

    def test_partial(self):
        a = {"cycle": 100, "course": 400, "monthly": 140, "yearly": 1700, "daily": 5}
        b = {"cycle": None, "course": None, "monthly": None, "yearly": None, "daily": None}
        t = rx.sum_regimen_totals([a, b])
        self.assertEqual(t["cycle"], 100)
        self.assertTrue(t["partial"]["cycle"])
        self.assertIsNone(t["course"])               # 코스는 부분합 금지
        self.assertEqual(t["computedCount"], 1)

    def test_full(self):
        a = {"cycle": 100, "course": 400, "monthly": 140, "yearly": 1700, "daily": 5}
        t = rx.sum_regimen_totals([a, dict(a)])
        self.assertEqual(t["course"], 800)
        self.assertFalse(t["hasMissing"])


class SearchRegimenDrugsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Mixin = _load_onco_mixin()
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("""CREATE TABLE drug_latest (insurance_code TEXT PRIMARY KEY, apply_date TEXT,
            product_name_kr TEXT, product_name_en TEXT, company TEXT, ingredient TEXT,
            dosage_strength TEXT, dosage_form TEXT, package_unit TEXT, max_price INTEGER,
            coverage_start TEXT, remark TEXT)""")
        conn.executemany(
            "INSERT INTO drug_latest (insurance_code, apply_date, product_name_kr, ingredient, max_price) VALUES (?,?,?,?,?)",
            [("655501901", "2026.07.01", "키트루다주(펨브롤리주맙,유전자재조합)_(0.1g/4mL)", "pembrolizumab   0.1g(25mg/mL)", 2100556),
             ("649806891", "2026.07.01", "어코드도세탁셀주(도세탁셀무수물)_(80mg/4mL)", "docetaxel   80mg(20mg/mL)", 419018),
             ("111111111", "2026.07.01", "에이디마이신주사액(독소루비신염산염)_(10mg/5mL)", "doxorubicin hydrochloride   10mg(2mg/mL)", 6190)])

        class _Db(Mixin):
            def _connect(self_inner):
                class _Ctx:
                    def __enter__(s):
                        return conn

                    def __exit__(s, *a):
                        return False
                return _Ctx()
        cls.db = _Db()

    def test_english_inn(self):
        r = self.db.search_regimen_drugs("pembrolizumab")
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["insurance_code"], "655501901")
        self.assertEqual(r[0]["inn"], "pembrolizumab")

    def test_english_inn_case_insensitive(self):
        self.assertEqual(self.db.search_regimen_drugs("Pembrolizumab")[0]["insurance_code"], "655501901")

    def test_korean_brand(self):
        self.assertEqual(self.db.search_regimen_drugs("키트루다")[0]["inn"], "pembrolizumab")

    def test_korean_ingredient_in_product_name(self):
        self.assertEqual(self.db.search_regimen_drugs("펨브롤리주맙")[0]["insurance_code"], "655501901")

    def test_salt_removed_from_inn(self):
        self.assertEqual(self.db.search_regimen_drugs("doxorubicin")[0]["inn"], "doxorubicin")

    def test_english_inn_from_kr(self):
        self.assertEqual(self.db.english_inn_from_kr("키트루다주"), "pembrolizumab")


def _load_server_pricing(fake_db, wap_configured: bool):
    """api/server.py 의 가격 헬퍼만 AST 로 추출해 실행 (flask/pandas import 없이)."""
    import ast
    import re
    src = (ROOT / "api" / "server.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    funcs = {"_total_content_mg", "_latest_row_by_code", "_domestic_unit_price", "_onco_unit_price",
             "_onco_wap_unit_price"}
    consts = {"_CONC_PAREN_STRIP_RE", "_AMOUNT_RE"}
    parts = []
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name in funcs:
            parts.append(ast.get_source_segment(src, n))
        elif isinstance(n, ast.Assign) and any(getattr(t, "id", "") in consts for t in n.targets):
            parts.append(ast.get_source_segment(src, n))
    ns = {"re": re, "db": fake_db}
    exec("\n\n".join(parts), ns)
    ns["_wap_configured"] = lambda: wap_configured
    ns["_latest_row_by_code"] = lambda code: fake_db.latest.get(code, {})
    return ns


class _FakePriceDb:
    latest = {"655501901": {"insurance_code": "655501901", "product_name_kr": "키트루다주(펨브롤리주맙,유전자재조합)_(0.1g/4mL)",
                            "ingredient": "pembrolizumab   0.1g(25mg/mL)", "dosage_strength": "4", "max_price": 2100556}}

    def find_by_ingredient_strength(self, inn, limit=60):
        return [v for v in self.latest.values() if v["ingredient"].lower().startswith(inn.strip().lower())]

    def get_price_at_date(self, code, as_of):
        v = self.latest.get(code)
        if not v or as_of < "2026.01.01":
            return None
        return {**v, "apply_date": "2026.10.01", "ingredient": "", "dosage_strength": "4"}

    def english_inn_from_kr(self, name):
        return "pembrolizumab" if "키트루다" in name else None


class ServerPricingFallbackTest(unittest.TestCase):
    """근본원인 회귀: WAP 키 미설정 → '주성분 가중평균' 행이 ₩0 이 되던 문제."""

    def test_wap_not_configured_falls_back_to_domestic(self):
        ns = _load_server_pricing(_FakePriceDb(), wap_configured=False)
        pm = ns["_onco_unit_price"]("Pembrolizumab", "weighted_avg", "2026-10-17", "2026.10.17")
        self.assertTrue(pm["available"])
        self.assertEqual(pm["source"], "domestic")
        self.assertTrue(pm["fallback"])
        self.assertEqual(pm["note"], rx.WAP_NOT_CONNECTED_NOTE)
        self.assertEqual(pm["content_mg"], 100.0)
        self.assertNotIn("KEY", str(pm))
        cost = rx.compute_drug_cost({"dose_value": 200, "unit": "mg", "cycle_total_mg": 200.0, "cycle_days": 21}, pm)
        self.assertEqual(cost["cost"]["cycle"], 4201112)

    def test_domestic_ref_and_korean_name(self):
        ns = _load_server_pricing(_FakePriceDb(), wap_configured=False)
        self.assertEqual(ns["_onco_unit_price"]("x", "domestic", "2026-10-17", "2026.10.17", ref="655501901")["code"],
                         "655501901")
        self.assertTrue(ns["_onco_unit_price"]("키트루다주", "domestic", "2026-10-17", "2026.10.17")["available"])

    def test_no_match_reason(self):
        ns = _load_server_pricing(_FakePriceDb(), wap_configured=False)
        pm = ns["_onco_unit_price"]("Fluorouracil(bolus)", "domestic", "2026-10-17", "2026.10.17")
        self.assertFalse(pm["available"])
        self.assertEqual(pm["reason"], "국내 약가 매칭 없음")

    def test_no_price_at_date_reason(self):
        ns = _load_server_pricing(_FakePriceDb(), wap_configured=False)
        pm = ns["_onco_unit_price"]("Pembrolizumab", "domestic", "2015-01-01", "2015.01.01")
        self.assertEqual(pm["reason"], "해당 기준일 국내 상한금액 없음")


if __name__ == "__main__":
    unittest.main()
