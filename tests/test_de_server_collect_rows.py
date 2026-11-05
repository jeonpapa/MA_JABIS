"""독일 서버 수집(2026-11-06): Rote Liste 수집기 포장 행 → 워커 upload 형식. 저장은 기존 upload 검증 경로로만."""
from api.price_request_ingest import common, de_rows_from_scraper_items


def _item(price, pc, column="AVP (EB)/FB", tier_text="AVP (EB)/FB — EB (Rote Liste Packungsangaben)"):
    return {"product_name": "Welireg 40 mg Filmtabletten", "ingredient": "Belzutifan", "dosage_strength": "40 mg",
            "dosage_form": "Filmtbl.", "package_unit": "90 Filmtbl.", "source_url": "https://www.rote-liste.de/suche/praep/1/welireg",
            "local_price": price, "pack_count": pc,
            "extra": {"pzn": "123", "price_column": column, "eb_label": "EB", "avp_uvp_reference": 17831.08,
                      "price_tier": "pharmacy_retail", "price_tier_original": tier_text, "vat_status": "included",
                      "price_as_of": "2026-10-15"}}


def test_eb_rows_map_to_upload_format_and_pass_common_checks():
    rows, skipped = de_rows_from_scraper_items([_item(4570.97, 90), _item(None, 90), _item(100.0, None)])
    assert len(rows) == 1 and len(skipped) == 2
    r = rows[0]
    assert r["currency"] == "EUR" and r["pack_count"] == 90 and r["extra"]["avp_eb_fb_reference"] == 4570.97
    assert "서버 수집" in r["source_label"]
    common(r, "DE")                       # 통화·URL·포장 수·관찰시각·단계·VAT 검증 통과


def test_uvp_fallback_row_has_no_eb_reference():
    rows, _ = de_rows_from_scraper_items([_item(17831.08, 90, column="AVP/UVP", tier_text="AVP/UVP (Rote Liste; AVP (EB)/FB 미표시 — 대체)")])
    assert "avp_eb_fb_reference" not in rows[0]["extra"] and "대체" in rows[0]["source_label"]
