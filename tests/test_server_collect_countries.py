"""2026-11-10 서버 수집 국가 확대: FR BdM_IT 파서(오프라인 고정 HTML) · JP/US/CA 수집기 레코드 → 업로드 행 변환."""
import json
from datetime import date

import pytest

from agents.foreign_sources import fr_bdm_it as f

CIP_FICHE = """<html><body>Fiche CIP : 3400930309032 Nature du médicament : A Numéro A.M.M : 303 090-3
Désignation : WELIREG 40 MG (BELZUTIFAN) Conditionnement : COMPRIMÉS PELLICULÉS (B/3X30) Grand Conditionnement : Non
Historique pour les assurés Historique Remboursement : Prix Fabricant HT € Prix Public TTC € Taux de remboursement Date d'application Date d'opposabilité PPTTC Date JO
13755.550 € 14177.30 € 100 % 03/07/2026 18/07/2026 03/07/2026
14000.000 € 14400.00 € 100 % 01/12/2026 15/12/2026 20/11/2026
Historique pour les collectivités Homologation Collectivité</body></html>"""

UCD_FICHE = """<html><body>Fiche UCD : 9419723 Numéro A.M.M : 941 972-3 Désignation : KEYTRUDA 100 MG SOL INJ PERF FL KEYTRUDA 100 MG SOL INJ PERF FL Observations : Voir JO
Historique Rétrocession Historique Remboursement : Date Application Top Fact. Prix HT Prix TTC Marge TTC TVA UCD Date source Source documentaire 25/06/2026 O - - 22 € - 01/08/2026 CNAMTS
Historique pour la TAA MCO Historique Remboursement : Date Application Prix HT Prix TTC TVA UCD Date source Source documentaire
03/04/2026 2000.740 € 2042.76 € 2.1 % 31/03/2026 JO 10/07/2025 2191.980 € 2238.01 € 2.1 % 11/06/2025 JO
Historique pour la TAA SSR</body></html>"""


def test_pack_count_variants():
    assert f.pack_count("COMPRIMÉS PELLICULÉS (B/3X30)") == 90
    assert f.pack_count("GRANULÉS EN SACHET (B/30)") == 30
    assert f.pack_count("1 BOITE DE 28, COMPRIMÉS PELLICULÉS EN PLAQUETTE UNITAIRE") == 28
    assert f.pack_count("POUDRE ET SOLVANT, 2 FLACONS DE 45 MG DE POUDRE ET2 SERINGUES PRÉREMPLIES") == 2
    assert f.pack_count("SOLUTION À DILUER") is None                       # 추정하지 않는다
    assert f.ucd13("9419723") == "3400894197232"


def test_cip_fiche_uses_application_date_and_ignores_future(monkeypatch):
    monkeypatch.setattr(f, "_fetch", lambda url, data=None, timeout=30: (CIP_FICHE, url))
    c = f.cip_fiche("3400930309032", today=date(2026, 11, 10))
    assert c["pfht"] == 13755.55 and c["date_application"] == "2026-07-03"       # 시행일 = 적용일(사용자 결정)
    assert c["date_opposabilite"] == "2026-07-18" and c["date_jo"] == "2026-07-03"
    assert c["next"] == {"pfht": 14000.0, "date_application": "2026-12-01"}     # 예정 가격은 별도 보관
    assert c["designation"] == "WELIREG 40 MG (BELZUTIFAN)"


def test_ucd_fiche_reads_taa_mco_not_retrocession(monkeypatch):
    monkeypatch.setattr(f, "_fetch", lambda url, data=None, timeout=30: (UCD_FICHE, url))
    u = f.ucd_fiche("9419723", today=date(2026, 11, 10))
    assert u["prix_ht"] == 2000.74 and u["date_application"] == "2026-04-03" and u["date_source"] == "2026-03-31"
    assert u["designation"] == "KEYTRUDA 100 MG SOL INJ PERF FL" and u["ucd13"] == "3400894197232"


def test_rows_for_single_result_redirect_and_unavailable(monkeypatch):
    def fake(url, data=None, timeout=30):
        if "test_nb_enreg" in url:
            if b"p_cip=cip" in data:   # 1건이면 상세 화면으로 바로 넘어간다 — 최종 URL 에 CIP
                return CIP_FICHE, "http://x/fiche/index_fic_medisoc.php?p_code_cip=3400930309032&p_site=AMELI"
            return "<html>0</html>", url
        return CIP_FICHE, url
    monkeypatch.setattr(f, "_fetch", fake)
    rows, skipped = f.rows_for("welireg", "belzutifan")
    assert len(rows) == 1
    r = rows[0]
    assert (r["local_price"], r["pack_count"], r["price_tier"], r["vat_status"], r["form_type"]) == (13755.55, 90, "ex_factory", "excluded", "oral")
    assert r["effective_date"] == "2026-07-03" and r["extra"]["cip13"] == "3400930309032"

    def down(url, data=None, timeout=30):
        raise f.BdmItUnavailable("timeout")
    monkeypatch.setattr(f, "_fetch", down)
    with pytest.raises(f.BdmItUnavailable):
        f.rows_for("welireg")


def test_fr_rows_pass_upload_validation(monkeypatch):
    pytest.importorskip("flask")
    from api.price_request_ingest import common
    monkeypatch.setattr(f, "_fetch", lambda url, data=None, timeout=30: (CIP_FICHE, "http://x?p_code_cip=3400930309032") if "test_nb" in url and b"p_cip" in data else (("<html/>", url) if "test_nb" in url else (CIP_FICHE, url)))
    rows, _ = f.rows_for("welireg", "belzutifan")
    out = common(rows[0], "FR")
    assert out["local_price"] == 13755.55 and out["pack_count"] == 90


class FakeAgent:
    def _resolve_pack_count(self, country, item, dosing):
        from agents.foreign_price_agent import ForeignPriceAgent
        return ForeignPriceAgent._resolve_pack_count(self, country, item, dosing)

    def _extract_unit_count(self, form_type, strength, package, product_name="", dosage_form=""):
        import re
        m = re.search(r"(\d+)s\b", package or "")
        return int(m.group(1)) if m else None


def test_scraper_records_to_rows():
    pytest.importorskip("flask")
    pytest.importorskip("pandas")
    from api.price_request_ingest import rows_from_scraper_records, common
    jp = [{"product_name": "キイトルーダ点滴静注100mg", "local_price": 199462.0, "currency": "JPY", "source_label": "厚生労働省 薬価基準",
           "source_url": "https://www.mhlw.go.jp/x.xlsx", "form_type": "injection", "package_unit": "", "raw_data": "{}"}]
    rows, skipped = rows_from_scraper_records("JP", jp, FakeAgent())
    assert rows[0]["pack_count"] == 1 and rows[0]["price_tier"] == "pharmacy_retail" and rows[0]["extra"]["pack_count_source"] == "per_unit"
    assert common(rows[0], "JP")["local_price"] == 199462.0
    us = [{"product_name": "KEYTRUDA", "local_price": 12517.44, "currency": "USD", "source_label": "Micromedex Red Book (WAC)",
           "source_url": "https://www.micromedexsolutions.com/", "form_type": "injection", "package_unit": "4 ml 2s",
           "raw_data": json.dumps({"effective_date": "01/06/2026"})},
          {"product_name": "WELIREG", "local_price": 31162.5, "currency": "USD", "source_label": "RED BOOK", "form_type": "oral",
           "source_url": "https://www.micromedexsolutions.com/", "package_unit": "bottle", "raw_data": "{}"}]
    rows, skipped = rows_from_scraper_records("US", us, FakeAgent())
    assert len(rows) == 1 and rows[0]["pack_count"] == 2 and rows[0]["source_type"] == "redbook_wac"
    assert rows[0]["effective_date"] is None                                   # MM/DD/YYYY 는 넣지 않는다
    assert skipped and "포장 수 근거 없음" in skipped[0]["reason"]              # 경구 포장 수 미확인 → 브라우저
    assert common(rows[0], "US")["pack_count"] == 2


# ── 캐나다 대체 출처 (2026-11-10 사용자 결정) ─────────────────────────────
def _ca_row(**kw):
    row = {"product_name": "KEYTRUDA", "ingredient": "pembrolizumab", "dosage_strength": "100 mg/4 mL", "dosage_form": "Sol. Perf. I.V. 25 mg/mL",
           "package_unit": "1 vial (25 mg/mL, 4 mL)", "pack_count": 1, "local_price": 4400.0, "currency": "CAD",
           "price_tier": "ex_factory", "price_tier_original": "Prix de vente garanti soumis (INESSS Avis 2026-08-27)", "vat_status": "unknown",
           "effective_date": "2026-08-27", "observed_at": "2026-11-01T08:00:00+09:00",
           "source_url": "https://www.inesss.qc.ca/fileadmin/doc/INESSS/Inscription_medicaments/Avis_au_ministre/Septembre_2026/x.pdf",
           "source_label": "INESSS Avis à la ministre (prix soumis)", "evidence_ref": "tmp/inesss.pdf p19", "form_type": "injection",
           "extra": {"ontario_checked": "ODB·EAP 'keytruda'·'pembrolizumab' 0건", "cda_amc_crosscheck": "PC0410 $4,400", "ramq_din": "02456869"}}
    row.update(kw)
    return row


def test_ca_submitted_price_substitute_validation_and_warning():
    pytest.importorskip("flask")
    pytest.importorskip("pandas")
    from api.price_request_ingest import prepare_a8
    from agents.foreign_price_agent import price_basis_warning, CA_SUBMITTED_NOTE
    from tests.test_price_request_routes import FakeAgent as RouteAgent
    agent = RouteAgent(None)
    out = prepare_a8(_ca_row(), "CA", "keytruda", "r1", agent)
    extra = json.loads(out["raw_data"])
    assert extra["price_column"] == "submitted_list_price" and extra["price_basis_warning"] == CA_SUBMITTED_NOTE
    assert out["source_type"] is None                                           # 캐나다 단가와 같은 × 0.81
    assert price_basis_warning("CA", {**out, "extra": extra}) == CA_SUBMITTED_NOTE
    with pytest.raises(ValueError):                                            # 온타리오 확인 기록 필수
        prepare_a8(_ca_row(extra={}), "CA", "keytruda", "r1", agent)
    with pytest.raises(ValueError):                                            # INESSS·CDA-AMC 문서여야 한다
        prepare_a8(_ca_row(source_url="https://example.com/price.pdf"), "CA", "keytruda", "r1", agent)
    ontario = _ca_row(price_tier="reimbursement", price_tier_original="Drug Benefit Price (DBP) per unit, Ontario",
                      source_label="Ontario EAP (Drug Benefit Price)", source_url="https://www.ontario.ca/page/x", extra={})
    o = prepare_a8(ontario, "CA", "welireg", "r1", agent)
    assert "price_column" not in json.loads(o["raw_data"])
    assert price_basis_warning("CA", {**o, "extra": json.loads(o["raw_data"])}) is None
