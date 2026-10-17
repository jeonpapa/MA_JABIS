"""해외약가 주사 per-vial 강도·투여경로 회귀 (2026-10-17 Winrevair/Keytruda FR/Prevymis CA 감사).

근거 (운영 /api/foreign/cached, 2026-10-17):
- IT Winrevair "WINREVAIR*1 flaconcino SC polv 45 mg + 1 sir preriempita solv 1 ml 50 mg/ml" → unit_strength 50 (정답 45),
  "... polv 60 mg + 1 sir preriempita solv 1,3 ml 50 mg/ml" → 150 (정답 60). 원인: 농도(용해 후 50 mg/ml) × 용제 부피를
  per-vial 로 썼고, 부피 정규식이 소수점 쉼표를 몰라 "1,3 ml" 을 3 ml 로 읽었다.
- UK MIMS "…; 1-vial kit=£x; 2-vial kit=£y" 분할 잔여 "; 2-vial kit" 행 → 강도 없음 → "; 2-vial kit (주사)" 가짜 탭,
  pack_count 단서 없음(default 1).
- form_type unknown 행이 무조건 oral 로 그룹핑 → 주사 전용 Winrevair 에 "45mg 경구"/"60mg 경구" 탭.
- CA Ontario EAP Prevymis "20 mg/mL" + "Inj Sol-240 mL Vial Pk" → 20×240 = 4,800 mg/vial (실제 바이알 240 mg/12 mL).
  출처 표기가 모호하므로 추정하지 않고 per-vial mg=None + A8 집계 제외(injection_unit_ambiguous).

stdlib unittest 로 작성 — pytest 없이도 실행된다:
    python3 -m unittest tests.test_foreign_strength_regressions -v
    .venv/bin/python -m pytest tests/test_foreign_strength_regressions.py -q
agents.foreign_price_agent 의 무거운 의존성(pandas·스크레이퍼)이 없는 환경에서는 격리된 namespace 에 stub 을 깔고
모듈 파일만 로드한다(다른 테스트의 agents.* 모듈 그래프는 건드리지 않음).
"""
from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_agent_module():
    try:
        import agents.foreign_price_agent as mod   # 정식 환경 (Linux CI / .venv)
        return mod
    except ImportError:
        pass
    stubs: dict[str, types.ModuleType] = {}

    def _stub(name: str, **attrs):
        m = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        stubs[name] = m
        return m

    class _Dummy:   # noqa: D401 — 스크레이퍼/DB 자리표시자
        def __init__(self, *a, **k):
            pass

    _stub("agents.db", DrugPriceDB=_Dummy)
    _stub("agents.exchange_rate", ExchangeRateFetcher=_Dummy, PriceCalculator=_Dummy)
    _stub("agents.scrapers.base", ScraperConfigError=type("ScraperConfigError", (Exception,), {}),
          ScraperLoginError=type("ScraperLoginError", (Exception,), {}), load_credentials=lambda *a, **k: {})
    for mod_name, cls in [("jp_mhlw", "JpMhlwScraper"), ("it_aifa", "ItAifaScraper"),
                          ("it_aifa_tnf", "ItAifaTnfScraper"), ("fr_bdpm", "FrBdpmScraper"),
                          ("fr_vidal", "FrVidalScraper"), ("ch_compendium", "ChCompendiumScraper"),
                          ("uk_mims", "UkMimsScraper"), ("de_rote_liste", "DeRoteListeScraper"),
                          ("ca_ontario", "CaOntarioScraper"), ("us_micromedex", "UsMicromedexScraper")]:
        _stub(f"agents.scrapers.{mod_name}", **{cls: _Dummy})
    spec = importlib.util.spec_from_file_location("_fsr_isolated_foreign_price_agent",
                                                  ROOT / "agents" / "foreign_price_agent.py")
    mod = importlib.util.module_from_spec(spec)
    import agents.scrapers  # noqa: F401 — 패키지 자체는 가볍다(__init__)
    with patch.dict(sys.modules, stubs):
        spec.loader.exec_module(mod)
    return mod


fpa = _load_agent_module()
A = fpa.ForeignPriceAgent
from agents.foreign_dose_normalize import process_formulations, route_of, route_of_item  # noqa: E402

WIN45_1 = "WINREVAIR*1 flaconcino SC polv 45 mg + 1 sir preriempita solv 1 ml 50 mg/ml"
WIN45_2 = "WINREVAIR*2 flaconcini SC polv 45 mg + 2 sir preriempite solv 1 ml 50 mg/ml"
WIN60_1 = "WINREVAIR*1 flaconcino SC polv 60 mg + 1 sir preriempita solv 1,3 ml 50 mg/ml"
WIN60_2 = "WINREVAIR*2 flaconcini SC polv 60 mg + 2 sir preriempite solv 1,3 ml 50 mg/ml"


def per_vial(ds, pu="", pn="", df=""):
    return A._extract_per_unit_mg("injection", ds, pu, product_name=pn, dosage_form=df)


class WinrevairItalyPowderKit(unittest.TestCase):
    """분말 바이알 함량이 용제(용해 후 농도 × 시린지 부피)를 이긴다."""

    def test_45mg_kits(self):
        for s in (WIN45_1, WIN45_2):
            self.assertEqual(per_vial(s, pn=s), 45.0, s)            # IT CSV: dosage_strength == product_name
            self.assertEqual(per_vial("", pn=s), 45.0, s)           # 문맥(product_name)에만 있어도

    def test_60mg_kits_decimal_comma(self):
        for s in (WIN60_1, WIN60_2):
            self.assertEqual(per_vial(s, pn=s), 60.0, s)            # 예전 150 (= 50 × "3" ml)

    def test_pack_counts(self):
        self.assertEqual(A._extract_unit_count("injection", WIN45_1, "", product_name=WIN45_1), 1)
        self.assertEqual(A._extract_unit_count("injection", WIN45_2, "", product_name=WIN45_2), 2)
        self.assertEqual(A._extract_unit_count("injection", WIN60_2, "", product_name=WIN60_2), 2)

    def test_decimal_comma_volume(self):
        self.assertEqual(A._find_volume_ml("solv 1,3 ml"), 1.3)
        self.assertEqual(A._find_volume_ml("flaconcino (vetro) 2,4 ml (165 mg/ml)"), 2.4)
        self.assertEqual(A._find_volume_ml("4,8ml"), 4.8)
        self.assertEqual(A._find_volume_ml("1 ml 50 mg/ml"), 1.0)

    def test_keytruda_sc_explicit_total_with_decimal_comma(self):
        # IT TNF SC: 165 mg/ml × 2,4 ml = 396 ≈ 라벨 395 → 395 (예전 165 × 4 = 660)
        self.assertEqual(per_vial("395 mg - soluzione iniettabile - uso sottocutaneo - flaconcino (vetro) 2,4 ml (165 mg/ml) 1 flaconcino"), 395.0)
        self.assertEqual(per_vial("790 mg - soluzione iniettabile - uso sottocutaneo - flaconcino (vetro) 4,8ml (165 mg/ml) 1 flaconcino"), 790.0)

    def test_quality_guard_finding_clears(self):
        """QG injection_concentration_as_unit: 저장 unit_strength 가 농도와 같거나 재계산과 다르면 ERROR."""
        for s, want in ((WIN45_1, 45.0), (WIN60_1, 60.0)):
            derived = per_vial(s, pn=s)
            conc = float(A._MG_PER_ML.search(s).group(1))
            self.assertEqual(derived, want)
            self.assertNotEqual(derived, conc)


class UkMimsVialKits(unittest.TestCase):
    ONE = "45mg powder and solvent for soln for inj; 1-vial kit"
    TWO = "; 2-vial kit"

    def test_strength_and_count(self):
        self.assertEqual(per_vial(self.ONE, pn="Winrevair"), 45.0)
        self.assertEqual(per_vial("60mg powder and solvent for soln for inj: 1-vial kit", pn="Winrevair"), 60.0)
        self.assertEqual(A._extract_unit_count("injection", self.ONE), 1)
        self.assertEqual(A._extract_unit_count("injection", self.TWO), 2)
        self.assertEqual(A._extract_unit_count("injection", "45mg powder and solvent for soln for inj: 2-vial kit"), 2)

    def test_continuation_fragment_inherits_previous_strength(self):
        base = dict(country="UK", product_name="Winrevair", source_url="u", searched_at="t", form_type="injection")
        items = [dict(base, id=10, dosage_strength=self.ONE, unit_strength_mg=45.0),
                 dict(base, id=11, dosage_strength=self.TWO, unit_strength_mg=None, unit_strength_unresolved=True),
                 dict(base, id=12, dosage_strength="60mg powder and solvent for soln for inj; 1-vial kit", unit_strength_mg=60.0),
                 dict(base, id=13, dosage_strength=self.TWO, unit_strength_mg=None)]
        self.assertEqual(A._inherit_continuation_strength(items), 2)
        self.assertEqual([it["unit_strength_mg"] for it in items], [45.0, 45.0, 60.0, 60.0])
        self.assertNotIn("unit_strength_unresolved", items[1])

    def test_fragment_with_other_source_not_inherited(self):
        a = dict(country="UK", product_name="Winrevair", source_url="u1", searched_at="t", id=1,
                 dosage_strength=self.ONE, unit_strength_mg=45.0)
        b = dict(country="UK", product_name="Winrevair", source_url="u2", searched_at="t", id=2,
                 dosage_strength=self.TWO, unit_strength_mg=None)
        A._inherit_continuation_strength([a, b])
        self.assertIsNone(b["unit_strength_mg"])


class RouteGrouping(unittest.TestCase):
    def test_route_of_legacy_contract_unchanged(self):
        self.assertEqual(route_of("unknown"), "oral")

    def test_unknown_rows_use_text_or_consensus(self):
        self.assertEqual(route_of_item({"form_type": "unknown", "dosage_strength": "45 mg", "dosage_form": "KIT",
                                        "raw_data": '{"route": "SUBCUTANEOUS"}'}), "injection")
        self.assertEqual(route_of_item({"form_type": "unknown", "dosage_strength": "45 mg", "dosage_form": "KIT"},
                                       "injection"), "injection")
        self.assertEqual(route_of_item({"form_type": "unknown", "dosage_strength": "45 mg"}), "oral")
        self.assertEqual(route_of_item({"form_type": "unknown", "dosage_strength": "100 mg f-c tab"}, "injection"), "oral")
        self.assertEqual(route_of_item({"form_type": "oral", "dosage_strength": "kit vial"}), "oral")   # 명시 우선

    def test_winrevair_groups_as_two_injection_formulations(self):
        def row(i, c, ft, ds, us, adj=1000, **kw):
            return dict(id=i, country=c, form_type=ft, dosage_strength=ds, unit_strength_mg=us,
                        adjusted_price_krw=adj, **kw)
        items = [
            row(1, "US", "unknown", "45 mg", 45.0, dosage_form="KIT"),
            row(2, "US", "unknown", "60 mg", 60.0, dosage_form="KIT"),
            row(3, "IT", "injection", WIN45_1, per_vial(WIN45_1, pn=WIN45_1), product_name=WIN45_1),
            row(4, "IT", "injection", WIN60_1, per_vial(WIN60_1, pn=WIN60_1), product_name=WIN60_1),
            row(5, "UK", "injection", UkMimsVialKits.ONE, 45.0),
            row(6, "UK", "injection", "; 2-vial kit", 45.0),        # 상속 후 상태
            row(7, "DE", "injection", "45 mg", 45.0),
            row(8, "JP", "injection", "45mg1キット", 45.0),
        ]
        process_formulations("winrevair", items)
        keys = {it["formulation_key"] for it in items}
        self.assertEqual(keys, {"45mg|injection", "60mg|injection"})
        labels = {it["formulation_label"] for it in items}
        self.assertEqual(labels, {"45mg 주사", "60mg 주사"})
        self.assertTrue(all(it["dose_norm_factor"] == 1.0 for it in items))


class KeytrudaFranceAndBaseline(unittest.TestCase):
    def test_fr_bdpm_25mg_per_ml_4ml_is_100mg(self):
        self.assertEqual(per_vial("25 mg", "1 flacon(s) en verre de 4 ml",
                                  pn="KEYTRUDA 25 mg/mL, solution à diluer pour perfusion"), 100.0)
        self.assertEqual(per_vial("25 mg/ml", "4 ml"), 100.0)

    def test_fr_vidal_no_volume_stays_unresolved_but_groups_to_100mg(self):
        pn = "KEYTRUDA 25 mg/ml sol diluer p perf"
        self.assertIsNone(per_vial("25 mg", "", pn=pn, df="solution pour perfusion"))
        self.assertIsNone(A._injection_unit_ambiguity("25 mg", "", pn, "solution pour perfusion"))
        items = [dict(id=1, country="US", form_type="injection", dosage_strength="100 mg/4 ml", unit_strength_mg=100.0,
                      adjusted_price_krw=1),
                 dict(id=2, country="FR", form_type="injection", dosage_strength="25 mg", product_name=pn,
                      unit_strength_mg=None, unit_strength_unresolved=True, adjusted_price_krw=1)]
        process_formulations("keytruda", items)
        fr = items[1]
        self.assertEqual((fr["formulation_key"], fr["unit_strength_mg"], fr["dose_norm_factor"]),
                         ("100mg|injection", 100.0, 1.0))   # 농도 remap → 100mg 탭, 보정 ×1 (예전 저장 ×4)

    def test_keytruda_other_countries_unchanged(self):
        cases = [
            ("100mg/4ml conc for soln for inf in vial, 2", "", "Keytruda", "", 100.0),
            ("395mg/2.4ml soln for inj in vial, 1", "", "Keytruda", "", 395.0),
            ("790mg/4.8ml soln for inj in vial, 1", "", "Keytruda", "", 790.0),
            ("25 mg/ml - concentrato per soluzione per infusione - uso endovenoso - flaconcino (vetro) - 4 ml” 1 flaconcino",
             "1", "KEYTRUDA", "", 100.0),
            ("25 mg/ml - concentrato per soluzione per infusione - uso endovenoso - flaconcino (vetro) 4 ml - 2 flaconcini",
             "2", "KEYTRUDA", "", 100.0),
            ("１００ｍｇ４ｍＬ１瓶", "", "キイトルーダ点滴静注１００ｍｇ", "注射薬", 100.0),
            ("25 mg/ml", "", "KEYTRUDA® 25 mg/ml Konzentrat zur Herstellung einer Infusionslösung", "", None),
            ("KEYTRUDA Inf Konz 100 mg/4ml", "", "", "", 100.0),
            ("100 mg/4 ml", "4 ml 2s", "Keytruda", "", 100.0),
            ("20 mg/1 ml", "12 ml", "Prevymis", "", 240.0),
            ("20 mg/1 ml", "24 ml", "Prevymis", "", 480.0),
            ("PREVYMIS*1 flaconcino EV infus 240 mg 12 ml", "", "", "LETERMOVIR 240MG 12ML 1 UNITA' USO PARENTERALE", 240.0),
            ("240 mg", "", "PREVYMIS® 240 mg/-480 mg Konzentrat zur Herstellung einer Infusionslösung", "", 240.0),
        ]
        for ds, pu, pn, df, want in cases:
            self.assertEqual(per_vial(ds, pu, pn, df), want, ds)
        # pack_count 단서 (Keytruda baseline)
        self.assertEqual(A._extract_unit_count("injection", "100mg/4ml conc for soln for inf in vial, 2"), 2)
        self.assertEqual(A._extract_unit_count("injection", cases[4][0], "2", product_name="KEYTRUDA"), 2)
        self.assertEqual(A._extract_unit_count("injection", "20 mg/1 ml", "4 ml 2s", product_name="Prevymis"), 2)
        self.assertIsNone(A._extract_unit_count("injection", "KEYTRUDA Inf Konz 100 mg/4ml", ""))

    def test_welireg_oral_unchanged(self):
        self.assertEqual(A._extract_per_unit_mg("oral", "40mg blue oval f-c tab marked 177, 90"), 40.0)
        self.assertEqual(A._extract_unit_count("oral", "40mg blue oval f-c tab marked 177, 90"), 90)


class PrevymisCanadaAmbiguous(unittest.TestCase):
    DS, DF240, DF480 = "20 mg/mL", "Inj Sol-240 mL Vial Pk", "Inj Sol-480 mL Vial Pk"

    def test_per_vial_unresolved_not_4800(self):
        self.assertIsNone(per_vial(self.DS, "", "Prevymis", self.DF240))
        self.assertIsNone(per_vial(self.DS, "", "Prevymis", self.DF480))
        self.assertTrue(A._injection_unit_ambiguity(self.DS, "", "Prevymis", self.DF240))

    def test_a8_ineligible_with_reason(self):
        row = dict(adjusted_price_krw=230_000, form_type="injection", dosage_strength=self.DS,
                   dosage_form=self.DF240, product_name="Prevymis", pack_count_source="per_unit")
        self.assertEqual(fpa.a8_aggregate_eligibility("CA", row), (False, "injection_unit_ambiguous"))
        self.assertEqual(fpa.a8_aggregate_eligibility("CA", row, check_injection_unit=False), (True, None))
        tab = dict(row, form_type="oral", dosage_strength="240 mg", dosage_form="Tab")
        self.assertEqual(fpa.a8_aggregate_eligibility("CA", tab), (True, None))

    def test_daily_cost_skipped_with_ambiguous_note(self):
        a = A.__new__(A)
        item = dict(country="CA", form_type="injection", dosage_strength=self.DS, dosage_form=self.DF240,
                    product_name="Prevymis", package_unit="", adjusted_price_krw=230_000, pack_count=1)
        a._populate_daily_cost(item, {"dose_per_cycle_mg": 480.0, "cycle_days": 1})
        self.assertIsNone(item["daily_cost_krw"])               # 예전 ₩23,502 (= 480 × adj / 4,800)
        self.assertEqual(item["daily_cost_note"], "unit_mg_ambiguous")
        self.assertTrue(item["unit_strength_ambiguous"])

    def test_ambiguous_rows_not_remapped_into_240mg_tab(self):
        items = [dict(id=1, country="US", form_type="injection", dosage_strength="20 mg/1 ml", package_unit="12 ml",
                      unit_strength_mg=240.0, adjusted_price_krw=1),
                 dict(id=2, country="US", form_type="injection", dosage_strength="20 mg/1 ml", package_unit="24 ml",
                      unit_strength_mg=480.0, adjusted_price_krw=1)]
        for i, df in ((3, self.DF240), (4, self.DF480)):
            items.append(dict(id=i, country="CA", form_type="injection", dosage_strength=self.DS, dosage_form=df,
                              unit_strength_mg=None, unit_strength_unresolved=True,
                              unit_strength_ambiguous="vial_volume_implausible", adjusted_price_krw=1))
        process_formulations("letermovir", items)
        ca = [it for it in items if it["country"] == "CA"]
        self.assertTrue(all(it["formulation_source"] == "unmatched" for it in ca))
        self.assertEqual(len({it["formulation_key"] for it in ca}), 2)      # 240 mL / 480 mL 가 섞이지 않음

    def test_large_infusion_bag_is_not_flagged(self):
        # 바이알이 아닌 수액 bag 은 250 mL 이 정상
        self.assertEqual(per_vial("1 mg/ml", "250 ml bag"), 250.0)
        self.assertIsNone(A._injection_unit_ambiguity("1 mg/ml", "250 ml bag"))


if __name__ == "__main__":
    unittest.main()
