"""해외약가 pack_count 단서 파싱 — form-aware 공용 추출기 (`ForeignPriceAgent._extract_unit_count`).

문자열은 전부 실제 foreign_drug_prices 행(2026-07 수집)에서 가져왔다. 원칙(foreign_agent_rules §최소단위):
- injection 단서: "(N x M)"·"N x M ml"·"N vial/flacon/flaconcino/瓶/ampoule"·US "4 ml 2s"·UK "in vial, 2". mg ratio 금지.
- oral 단서: IT "*28 cpr"·"56 UNITA'"·FR "de 30 comprimé(s)"·"28 plaquette(s) … de 1 comprimé"·DE/CH "Stk/St"·UK ", 90"·US "90s ea".
- 단서가 없으면 None — 호출측이 dosing fallback(oral) 또는 1(default) 로 처리하고 **출처를 기록**한다.

실행: .venv/bin/python -m pytest tests/test_foreign_pack_count_parsing.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.foreign_price_agent import ForeignPriceAgent as A

INJECTION_CASES = [
    # (dosage_strength, package_unit, product_name, expected)
    ("100mg/4ml conc for soln for inf in vial, 2", "", "Keytruda", 2),                       # UK #1300
    ("395mg/2.4ml soln for inj in vial, 1", "", "Keytruda", 1),                              # UK #1301
    ("25 mg/ml - concentrato per soluzione per infusione - uso endovenoso - flaconcino (vetro) 4 ml - 2 flaconcini",
     "2", "KEYTRUDA", 2),                                                                     # IT TNF #1496
    ("25 mg/ml - concentrato per soluzione per infusione - uso endovenoso - flaconcino (vetro) - 4 ml” 1 flaconcino",
     "1", "KEYTRUDA", 1),                                                                     # IT TNF #1495
    ("25 mg", "1 flacon(s) en verre de 4 ml", "KEYTRUDA 25 mg/mL, solution à diluer pour perfusion", 1),   # FR BDPM #1502
    ("25 mg", "2 flacon(s) en verre de 4 ml", "KEYTRUDA 25 mg/mL, solution à diluer pour perfusion", 2),   # FR BDPM #1503
    ("PREVYMIS*1 flaconcino EV infus 240 mg 12 ml", "", "PREVYMIS*1 flaconcino EV infus 240 mg 12 ml", 1),  # IT CSV #1483
    ("20 mg/1 ml", "4 ml 2s", "Prevymis", 2),                                                 # US Redbook 형식
    ("100 mg/4 ml", "2 x 4 ml", "Keytruda", 2),                                               # "N x M ml" → N 바이알
    ("１００ｍｇ４ｍＬ１瓶", "", "キイトルーダ点滴静注１００ｍｇ", 1),                           # JP #1497
    # 단서 없음 → None (농도·부피 숫자를 count 로 오인하면 안 된다)
    ("KEYTRUDA Inf Konz 100 mg/4ml", "", "KEYTRUDA Inf Konz 100 mg/4ml", None),              # CH
    ("25 mg/ml", "", "KEYTRUDA® 25 mg/ml Konzentrat zur Herstellung einer Infusionslösung", None),   # DE #1470
    ("25 mg", "", "KEYTRUDA 25 mg/ml sol diluer p perf", None),                              # FR Vidal #1504
    ("240 mg", "", "PREVYMIS® 240 mg/-480 mg Konzentrat zur Herstellung einer Infusionslösung", None),  # DE #1485
]

ORAL_CASES = [
    # (dosage_strength, package_unit, dosage_form, expected)
    ("JANUVIA*28 cpr rivestite 100 mg", "", "SITAGLIPTIN 100MG 28 UNITA' USO ORALE", 28),             # IT CSV #1311
    ("DIESMIT*56 cpr 100 mg + 1.000 mg rilascio modificato", "", "SITAGLIPTIN+METFORMINA 100+1.000MG 56 UNITA' USO ORALE RM", 56),
    ("KISPLYX*30 cps 4 mg", "", "LENVATINIB 4MG 30 UNITA' USO ORALE", 30),                            # IT CSV #1471
    ("PREVYMIS*28x1 cpr rivestite 240 mg", "", "LETERMOVIR 240MG 28 UNITA' USO ORALE", 28),            # IT CSV #1481
    ("LYNPARZA*56 cpr riv 100 mg", "", "OLAPARIB 100MG 56 UNITA' USO ORALE", 56),                      # IT CSV #1292
    ("", "", "SITAGLIPTIN 25MG 28 UNITA' USO ORALE", 28),                                              # dosage_form 만 있어도
    ("40 mg - Compressa rivestita con film - Uso orale - Blister (Al/Al)” 90 (3 x 30) compresse", "90", "", 90),   # IT TNF #1280
    ("50 mg/850 mg compresse rivestite con film” 56 compresse in blister PVC/PE/PVDC/AL", "56", "", 56),           # IT TNF #1381
    ("100 mg", "plaquette(s) PVC polyéthylène PVDC aluminium de 30 comprimé(s)", "comprimé pelliculé", 30),        # FR #1383
    ("100 mg", "plaquette(s) PVC PVDC aluminium de 28 comprimé(s)", "comprimé pelliculé", 28),                     # FR #1385
    ("100 mg", "28 plaquette(s) OPA : polyamide orienté aluminium PVC-Aluminium de 1 comprimé(s)", "comprimé pelliculé", 28),   # FR #1407
    ("50 mg", "60 plaquettes PVC polyéthylène PVDC aluminium de 1 comprimé", "comprimé pelliculé", 60),            # FR #1417
    ("50 mg", "plaquettes PVC PVDC aluminium de 60 comprimés", "comprimé pelliculé", 60),                          # FR #1428
    ("40mg blue oval f-c tab marked 177, 90", "", "", 90),                                             # UK #1291 (177 은 각인)
    ("240mg f-c tab, 28", "", "", 28),                                                                 # UK
    ("120 mg", "30s ea", "", 30),                                                                      # US Redbook
    ("240 mg", "28s ea", "", 28),
    ("SITAGLIPTIN NOBEL Filmtabl 100 mg 28 Stk", "", "", 28),                                          # CH (수량 노출 시)
    ("40 mg Filmtabletten 90 Stück", "", "", 90),                                                      # DE Rote Liste
    ("Filmtabl 98 St", "", "", 98),
    ("２４０ｍｇ１錠", "", "", 1),                                                                      # JP (per-unit)
    # 단서 없음 → None
    ("SITAGLIPTIN NOBEL Filmtabl 25 mg", "", "", None),                                                # CH #1445
    ("SITAGLIPTIN Metformin NOBEL 50/1000 mg", "", "", None),                                          # CH #1432
    ("50 mg", "", "", None),                                                                           # DE #1466 ("1000 mg Filmtabletten" 은 수량 아님)
    ("40 mg", "", "", None),                                                                           # DE #1487 (dosing fallback 대상)
]


@pytest.mark.parametrize("ds,pu,pn,expected", INJECTION_CASES)
def test_injection_unit_count(ds, pu, pn, expected):
    assert A._extract_unit_count("injection", ds, pu, product_name=pn) == expected


@pytest.mark.parametrize("ds,pu,df,expected", ORAL_CASES)
def test_oral_unit_count(ds, pu, df, expected):
    assert A._extract_unit_count("oral", ds, pu, dosage_form=df) == expected


def test_de_product_name_strength_list_is_not_a_count():
    pn = "Sitagliptin/Metforminhydrochlorid Mylan 50 mg/850 mg/-50 mg/1000 mg Filmtabletten"
    assert A._extract_unit_count("oral", "50 mg", "", product_name=pn) is None


def test_total_pkg_mg_is_per_unit_times_count():
    assert A._extract_total_pkg_mg("oral", "40mg blue oval f-c tab marked 177, 90") == 3600.0
    assert A._extract_total_pkg_mg("injection", "100mg/4ml conc for soln for inf in vial, 2") == 200.0
    assert A._extract_total_pkg_mg("injection", "25 mg/ml", "") is None          # per-vial 불명 → None


# ── _resolve_pack_count: 출처(pack_count_source) 기록 ──────────────────────────

def _agent():
    return ForeignPriceAgent_new()


def ForeignPriceAgent_new():
    a = A.__new__(A)
    return a


DOSING_90 = {"ingredient_key": "belzutifan", "dose_per_cycle_mg": 120.0, "cycle_days": 1, "default_pack_count": 90}


def test_scraper_pack_count_is_trusted_for_fresh_items_only():
    a = _agent()
    fresh = {"dosage_strength": "x", "pack_count": 3, "form_type": "injection"}       # IT TNF 처럼 스크레이퍼 명시
    assert a._resolve_pack_count("IT", fresh, None) == 3 and fresh["pack_count_source"] == "scraper"
    stored = {"id": 1300, "dosage_strength": "100mg/4ml conc for soln for inf in vial, 2",
              "pack_count": 1, "form_type": "injection"}                              # DB 행의 1 은 에이전트 산물
    assert a._resolve_pack_count("UK", stored, None) == 2 and stored["pack_count_source"] == "parsed"
    kept = {"id": 9, "dosage_strength": "x", "pack_count": 3, "pack_count_source": "scraper", "form_type": "oral"}
    assert a._resolve_pack_count("IT", kept, None) == 3 and kept["pack_count_source"] == "scraper"


def test_per_unit_countries_are_always_one():
    a = _agent()
    jp = {"dosage_strength": "２４０ｍｇ１錠", "form_type": "oral"}
    assert a._resolve_pack_count("JP", jp, DOSING_90) == 1 and jp["pack_count_source"] == "per_unit"


def test_oral_pack_country_falls_back_to_dosing_then_default():
    a = _agent()
    de = {"id": 1487, "dosage_strength": "40 mg", "form_type": "oral", "pack_count": 90}
    assert a._resolve_pack_count("DE", de, DOSING_90) == 90 and de["pack_count_source"] == "dosing"
    ch = {"id": 1445, "dosage_strength": "SITAGLIPTIN NOBEL Filmtabl 25 mg", "form_type": "oral", "pack_count": 1}
    assert a._resolve_pack_count("CH", ch, None) == 1 and ch["pack_count_source"] == "default"


def test_injection_without_clue_defaults_to_one_vial_and_never_ratio():
    a = _agent()
    ch = {"dosage_strength": "KEYTRUDA Inf Konz 100 mg/4ml", "form_type": "injection"}
    assert a._resolve_pack_count("CH", ch, None) == 1 and ch["pack_count_source"] == "default"
    # dosing.default_pack_count 는 oral 전용 — injection 에는 적용하지 않는다
    de = {"dosage_strength": "25 mg/ml", "form_type": "injection"}
    assert a._resolve_pack_count("DE", de, DOSING_90) == 1 and de["pack_count_source"] == "default"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
