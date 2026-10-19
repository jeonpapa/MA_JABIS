"""약가유연계약 신청서 A8 비교표 서식(flex) — 칸 배치·최저수재 선택·집계 제외·증빙 시트."""
import io
import json

from agents import foreign_report as fr


def _row(country, price, pack, adj, cur="EUR", eligible=True, form="Tablets", unit="28 tablets", **kw):
    return {"country": country, "local_price": price, "pack_count": pack, "per_unit_local": price / pack,
            "currency": cur, "exchange_rate": 1500.0, "exchange_rate_from": "20231003", "exchange_rate_to": "20260930",
            "krw_converted": price / pack * 1500.0, "adjusted_price_krw": adj, "a8_aggregate_eligible": eligible,
            "product_name": "PREVYMIS", "dosage_strength": "240 mg", "dosage_form": form, "package_unit": unit,
            "form_type": "oral", "unit_strength_mg": 240, "source_label": f"{country} list", "source_url": f"https://{country.lower()}.example/x",
            "searched_at": "2026-10-19T05:00:00", "raw_data": json.dumps({"price_tier": "ex_factory", "vat_status": "excluded",
                                                                         "effective_date": "2026-10-01", "evidence_ref": "ev.png",
                                                                         "observed_at": "2026-10-19T14:00:00+09:00"}), **kw}


def test_flex_model_picks_lowest_listing_and_max():
    cached = {"US": [_row("US", 7875.0, 28, 400000, cur="USD")],
              "FR": [_row("FR", 4324.31, 28, 230000), _row("FR", 9000.0, 28, 480000)],    # 최저 수재 = 230000
              "DE": [_row("DE", 5000.0, 28, 300000, eligible=False, a8_exclude_reason="pack_count_unverified")]}
    m = fr.build_flex_model(cached)
    by = {c["country"]: c for c in m["cols"]}
    assert [c["country"] for c in m["cols"]] == fr.FLEX_ORDER                     # 서식 열 순서 고정 (미수집 포함)
    assert by["FR"]["adjusted"] == 230000 and by["FR"]["unit"] == "1정 (240 mg)"
    assert by["DE"]["status"] == "excluded" and "포장수량" in by["DE"]["note"]        # 틀린 단위가를 싣지 않는다
    assert by["UK"]["status"] == "missing"
    assert m["max"]["country"] == "US" and m["max"]["adjusted"] == 400000


def test_flex_unit_word_for_granules_and_vials():
    assert fr.unit_word({"form_type": "oral", "dosage_form": "Granules in packets", "package_unit": "30 packets"}) == "포"
    assert fr.unit_word({"form_type": "injection", "dosage_form": "Solution for infusion", "package_unit": "1 vial"}) == "바이알"
    assert fr.unit_word({"form_type": "oral"}) == "정"


def test_flex_xlsx_layout_and_evidence_sheet():
    from openpyxl import load_workbook
    cached = {"US": [_row("US", 7875.0, 28, 400000, cur="USD")], "IT": [_row("IT", 4653.74, 28, 260000)]}
    data, mime, name = fr.generate("prevymis", "flex", cached, applied_display=150000, product_label="PREVYMIS 240mg 경구")
    assert name.startswith("prevymis_A8_약가유연계약서식_") and name.endswith(".xlsx")
    wb = load_workbook(io.BytesIO(data))
    ws = wb["A8 비교표"]
    assert ws["A1"].value == "○ 제품명: PREVYMIS 240mg 경구"
    assert "150,000원" in ws["A2"].value
    header = [ws.cell(row=4, column=j).value for j in range(1, 11)]
    assert header[0] == "국가" and header[2:] == ["미국", "영국", "독일", "프랑스", "이탈리아", "스위스", "일본", "캐나다"]
    assert ws.cell(row=12, column=3).value == 400000                                  # 미국 조정가
    assert "400,000원 (미국)" in ws.cell(row=13, column=3).value                      # A8 조정최고가
    ev = wb["자료원 증빙"]
    rows = {ev.cell(row=i, column=1).value: [ev.cell(row=i, column=j).value for j in range(1, 11)] for i in range(2, 10)}
    assert rows["미국"][5] == "https://us.example/x" and rows["미국"][7] == "2026-10-01" and rows["미국"][8] == "ev.png"
    assert rows["영국"][2] == "-"                                                     # 미수집 국가도 행은 남긴다


def test_flex_without_any_price_raises():
    try:
        fr.generate("x", "flex", {"US": []})
    except ValueError:
        return
    raise AssertionError("가격 없는 서식은 만들지 않는다")
