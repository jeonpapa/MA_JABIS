"""A8 자료원 공용 파서 + 행 생성기 + 업로드 국가별 규칙 (2026-10-27). docs/foreign_price/README.md 의 규칙을 고정한다.

실행: .venv/bin/python -m pytest tests/test_foreign_sources.py -q
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agents.foreign_sources import ch_sl, de_rote_liste_table as de_tbl, fr_jo  # noqa: E402

DE_TXT = """PACKUNGSANGABEN
\t\tPZN\tAVP (EB)/FB\tAVP/UVP
28 Filmtbl. 240 mg\tN1\t13863501\tEB 4.719,49 €\t5.758,22 €
1 Durchstechfl. 480 mg/24 ml\tN1\t13863547\tEB 355,52 €\t473,45 €
2 Vial KIT 60 mg\tN2\t19159306\tEB 11.998,06 €\t20.954,65 €
1 Filmtbl.\t\t12345678\tEB 10,50 €
Preisstand: 15. Oktober 2026"""

JO_CIP = """JORF n°0134 du 10 juin 2026
NOR : SFHS2610602V
N° CIP\tPrésentation\tPFHT\tPPTTC
34009 302 985 3 4\tWINREVAIR 45 mg (sotatercept), 1 flacon de 45 mg de poudre (laboratoires MSD FRANCE)\t6 393,00 €\t6 660,78 €
34009 302 985 4 1\tWINREVAIR 45 mg (sotatercept), 2 flacons de 45 mg de poudre (laboratoires MSD FRANCE)\t12 786,00 €\t13 188,03 €
34009 303 090 3 2\tWELIREG 40 mg (belzutifan), comprimés pelliculés (B/3x30) (laboratoires MSD FRANCE)\t13 755,55 €\t14 177,30 €
Cette décision entre en vigueur à compter du quinzième jour suivant la publication au Journal officiel."""

JO_CIP_DATED = """JORF n°0156 du 3 juillet 2024
les prix ... sont les suivants, à compter du 15 juillet 2024 :
N° CIP\tPrésentation\tPFHT\tPPTTC
34009 301 272 4 7\tPREVYMIS 480 mg (letermovir), comprimés pelliculés, en plaquette unitaire (B/28) (laboratoires MSD FRANCE)\t8 210,44 €\t8 515,74 €"""

JO_UCD = """JORF n°0077 du 31 mars 2026
Code UCD\tLibellé\tLaboratoire exploitant\tTarif de responsabilité HT par UCD (en €)\tPrix limite de vente HT
34008 941 972 3 2\tKEYTRUDA 25MG/ML PERF FL4ML\tMSD FRANCE\t2 000,740\t2 000,740
Cette décision entre en vigueur à compter du 3 avril 2026."""

SL = {"items": [{"trademark": "Winrevair", "medicinalProducts": [
    {"dosageFormAndStrength": "Trockensub 45 mg c Solv", "ingredients": [{"substance": "Sotaterceptum"}],
     "packagedMedicinalProducts": [{"packSize": "Durchstf 1 Stk", "exFactoryPrice": 5895.82, "retailPrice": 6356.9,
                                    "gtin": "7680697870013", "listType": "SL", "lastPriceChange": "2025-12-01"}]}]}]}


# ── DE ────────────────────────────────────────────────────────────────────────────────
def test_de_table_uvp_and_eb_reference():
    rows = de_tbl.parse_packungsangaben(DE_TXT)
    assert [(r["pzn"], r["avp_uvp"], r["avp_eb_fb"]) for r in rows] == [
        ("13863501", 5758.22, 4719.49), ("13863547", 473.45, 355.52), ("19159306", 20954.65, 11998.06), ("12345678", None, 10.50)]
    assert [de_tbl.pack_count(r["pack"]) for r in rows] == [28, 1, 2, 1]
    assert de_tbl.preisstand(DE_TXT) == "2026-10-15"


# ── FR ────────────────────────────────────────────────────────────────────────────────
def test_fr_cip_notice_pfht_and_effective_date():
    n = fr_jo.parse_notice(JO_CIP)
    assert n["kind"] == "cip" and n["publication"]["publication_date"] == "2026-06-10" and n["publication"]["nor"] == "SFHS2610602V"
    assert n["effective_date"] == "2026-06-25"                       # 15e jour suivant la publication
    assert [(r["code"], r["pfht"], r["ppttc"]) for r in n["rows"]] == [
        ("3400930298534", 6393.0, 6660.78), ("3400930298541", 12786.0, 13188.03), ("3400930309032", 13755.55, 14177.30)]
    assert [fr_jo.pack_count(r["presentation"]) for r in n["rows"]] == [1, 2, 90]


def test_fr_explicit_date_and_ucd_notice():
    assert fr_jo.parse_notice(JO_CIP_DATED)["effective_date"] == "2024-07-15"
    u = fr_jo.parse_notice(JO_UCD)
    assert u["kind"] == "ucd" and u["rows"][0]["code"] == "3400894197232" and u["rows"][0]["tarif_ht"] == 2000.74
    assert u["effective_date"] == "2026-04-03"


def test_fr_pack_count_unknown_is_none():
    assert fr_jo.pack_count("PREVYMIS 240 mg/12 ml, solution à diluer") is None


# ── CH ────────────────────────────────────────────────────────────────────────────────
def test_ch_flatten_and_match():
    packs = ch_sl.flatten_packs(SL)
    assert packs[0]["ex_factory_price"] == 5895.82 and packs[0]["substance"] == "Sotaterceptum"
    assert ch_sl.match_pack(packs, 6356.9)["gtin"] == "7680697870013"
    assert ch_sl.pack_unit_count("Durchstf 1 Stk") == 1


# ── 행 생성기 (subprocess — Aside 워커가 실제로 쓰는 방식) ──────────────────────────────────
def _run(args, tmp_path, text=None, name="in.txt"):
    if text is not None:
        (tmp_path / name).write_text(text, encoding="utf-8")
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "a8_rows.py"), *args], capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_rows_de(tmp_path):
    """2026-11-02: 기준 = AVP (EB)/FB. EB/FB 가 비어 있는 포장만 AVP/UVP 대체(+표시)."""
    txt = DE_TXT + "\n30 Btl. 20 mg\tN1\t19471564\t537,04 €"           # EB 칸 없이 AVP/UVP 하나만
    d = _run(["de", "--text", str(tmp_path / "in.txt"), "--url", "https://www.rote-liste.de/rle/detail/1", "--product", "PREVYMIS",
              "--ingredient", "letermovir", "--form", "oral"], tmp_path, txt)
    assert d["status"] == "priced" and len(d["rows"]) == 5
    r = d["rows"][0]
    assert r["local_price"] == 4719.49 and r["pack_count"] == 28 and r["currency"] == "EUR"
    assert "EB" in r["price_tier_original"] and r["extra"]["avp_uvp_reference"] == 5758.22
    assert "price_basis_warning" not in r["extra"]
    eb_only = d["rows"][3]                                                   # EB 만 표시된 포장 → EB 사용
    assert eb_only["local_price"] == 10.50 and "price_basis_warning" not in eb_only["extra"]
    uvp = d["rows"][4]                                                       # EB 미표시 → UVP 대체 + 표시
    assert uvp["local_price"] == 537.04 and "UVP" in uvp["price_tier_original"] and "대체" in uvp["extra"]["price_basis_warning"]


def test_rows_fr(tmp_path):
    d = _run(["fr", "--text", str(tmp_path / "in.txt"), "--url", "https://www.legifrance.gouv.fr/jorf/id/X", "--ingredient", "x",
              "--form", "injection", "--code", "3400930298541"], tmp_path, JO_CIP)
    assert len(d["rows"]) == 1
    r = d["rows"][0]
    assert (r["local_price"], r["pack_count"], r["price_tier"], r["vat_status"]) == (12786.0, 2, "ex_factory", "excluded")
    assert r["effective_date"] == "2026-06-25" and r["extra"]["ppttc_reference"] == 13188.03


def test_rows_ch_from_saved_json(tmp_path):
    (tmp_path / "sl.json").write_text(json.dumps(SL))
    d = _run(["ch", "--trademark", "Winrevair", "--json", str(tmp_path / "sl.json"), "--ingredient", "sotatercept", "--form", "injection"], tmp_path)
    r = d["rows"][0]
    assert (r["local_price"], r["currency"], r["pack_count"], r["price_tier"]) == (5895.82, "CHF", 1, "ex_factory")
    assert r["effective_date"] == "2025-12-01" and r["extra"]["retail_price_reference"] == 6356.9


# ── 업로드 검증(prepare_a8) 국가별 기준 가격 단계 ─────────────────────────────────────────
class _Calc:
    """계산기 호출 인자만 기록 — 실제 계산은 test_foreign_factory_ratio_* 가 고정."""
    def __init__(self):
        self.calls = []

    def calculate_adjusted_price(self, **kw):
        self.calls.append(kw)
        from agents.exchange_rate import PriceCalculator
        return PriceCalculator().calculate_adjusted_price(**kw)


class _Rates:
    def get_36m_average(self, *a, **k):
        return {"rate": 1600.0, "from_month": "2023-10", "to_month": "2026-09"}


class _Agent:
    def __init__(self):
        self.calculator, self.rate_fetcher = _Calc(), _Rates()

    def _extract_per_unit_mg(self, *a, **k):
        return None

    def _load_dosing_map(self, *a, **k):
        return {}

    def _match_dosing(self, *a, **k):
        return None

    def _populate_daily_cost(self, r, dosing):
        r["daily_cost_krw"] = None


def _row(**kw):
    base = dict(product_name="X", ingredient="x", dosage_strength="40 mg", dosage_form="tab", package_unit="28",
                source_url="https://example.org/a", source_label="src", evidence_ref="e.png", price_tier_original="PFHT",
                local_price=1000.0, pack_count=28, price_tier="ex_factory", vat_status="excluded",
                observed_at="2026-10-01T00:00:00+00:00", effective_date=None, form_type="oral")
    base.update(kw)
    return base


def _prep(row, cc):
    from api.price_request_ingest import prepare_a8
    agent = _Agent()
    try:
        import agents.foreign_dose_normalize as fdn
        orig, fdn.process_formulations = fdn.process_formulations, (lambda q, rows: None)
    except Exception:
        fdn = orig = None
    try:
        out = prepare_a8(row, cc, "q", "rid", agent)
    finally:
        if fdn:
            fdn.process_formulations = orig
    return out, agent.calculator.calls[-1]


def test_ingest_de_eb_basis_and_uvp_fallback():
    eb = dict(currency="EUR", price_tier="pharmacy_retail", vat_status="included", local_price=9380.56,
              price_tier_original="AVP (EB)/FB — EB (Rote Liste Packungsangaben)")
    out, call = _prep(_row(**eb), "DE")
    assert call["source_type"] is None and 0.73 < out["factory_price_krw"] / (9380.56 / 28 * 1600) < 0.74   # 별첨5 독일 산식
    assert "price_basis_warning" not in json.loads(out["raw_data"])
    uvp = dict(eb, local_price=11458.02, price_tier_original="AVP/UVP (Rote Liste; AVP (EB)/FB 미표시 — 대체)")
    out, _ = _prep(_row(**uvp), "DE")                                        # EB 가 없을 때만 UVP 허용, 대체 표시
    assert "대체" in json.loads(out["raw_data"])["price_basis_warning"]
    with pytest.raises(ValueError):                                          # EB 가 표시돼 있는데 UVP 를 올리면 거부
        _prep(_row(**uvp, extra={"avp_eb_fb_reference": 9380.56}), "DE")
    with pytest.raises(ValueError):                                          # 가격 열 표기가 없으면 거부
        _prep(_row(**dict(eb, price_tier_original="Rote Liste")), "DE")


def test_ingest_fr_tier_decides_ratio():
    _, call = _prep(_row(currency="EUR"), "FR")
    assert call["source_type"] is None                                     # PFHT × 0.77
    _, call = _prep(_row(currency="EUR", price_tier="pharmacy_retail", vat_status="included", price_tier_original="PPTTC"), "FR")
    assert call["source_type"] == "vidal"                                  # 공중가 → × 0.65
    with pytest.raises(ValueError):
        _prep(_row(currency="EUR", vat_status="included"), "FR")           # 공장도가인데 VAT 포함 표기 — 거부


def test_ingest_ch_fap_or_fallback():
    _, call = _prep(_row(currency="CHF", price_tier_original="FAP (BAG SL)"), "CH")
    assert call["source_type"] is None                                     # SL FAP × 0.73
    _, call = _prep(_row(currency="CHF", price_tier="pharmacy_retail", vat_status="included", price_tier_original="Publikumspreis"), "CH")
    assert call["source_type"] == "compendium" and not call.get("exfactory_price")   # Compendium × 0.65
    _, call = _prep(_row(currency="CHF", price_tier="pharmacy_retail", vat_status="included", price_tier_original="PP",
                         local_price=6356.9, extra={"ex_factory_price": 5895.82}), "CH")
    assert call["exfactory_price"] == 5895.82                              # 판매가 + FAP → FAP × 0.73


def test_a8_aggregate_excludes_non_reference_tiers():
    from agents.foreign_price_agent import a8_aggregate_eligibility
    it_pub = {"adjusted_price_krw": 1, "pack_count_source": "scraper", "form_type": "injection",
              "raw_data": json.dumps({"aifa_class": "A", "price_kind": "prezzo al pubblico"})}
    assert a8_aggregate_eligibility("IT", it_pub, check_injection_unit=False) == (False, "it_public_price_not_ex_factory")
    it_exf = {**it_pub, "raw_data": json.dumps({"price_kind": "Prezzo ex-factory (IVA esclusa)"})}
    assert a8_aggregate_eligibility("IT", it_exf, check_injection_unit=False)[0] is True
    from agents.foreign_price_agent import de_price_basis_warning
    de_eb = {"adjusted_price_krw": 1, "pack_count_source": "scraper", "form_type": "oral",
             "raw_data": json.dumps({"price_tier_original": "AVP (EB)/FB — EB (Erstattungsbetrag)"})}
    assert a8_aggregate_eligibility("DE", de_eb)[0] is True and de_price_basis_warning(de_eb) is None
    de_uvp = {**de_eb, "raw_data": json.dumps({"price_tier_original": "AVP/UVP (Rote Liste Packungsangaben)"})}
    assert a8_aggregate_eligibility("DE", de_uvp)[0] is True                 # 대체도 집계에는 포함
    assert "대체" in de_price_basis_warning(de_uvp)
