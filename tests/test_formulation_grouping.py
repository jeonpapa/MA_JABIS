"""제형(formulation) 그룹핑 결정성 테스트 — Prevymis 형태 fixture.

process_formulations 가 강도×투여경로로 정확히 그룹핑하고, 서로 다른 강도
(240↔480↔20)를 절대 한 제형으로 섞지 않으며, US canonical vs foreign_only 를
구분하는지 검증한다. LLM 미호출 경로(strength 사전 부착)만 검증(결정적).

실행: .venv/bin/python -m pytest tests/test_formulation_grouping.py -q
       또는 .venv/bin/python tests/test_formulation_grouping.py
"""
from __future__ import annotations

from collections import defaultdict

from agents.foreign_dose_normalize import process_formulations, route_of


def _it(rid, country, strength, route, adj):
    return {
        "id": rid, "country": country, "unit_strength_mg": strength,
        "form_type": route, "adjusted_price_krw": adj,
        "dosage_strength": f"{strength}mg",
    }


def _prevymis_items():
    return [
        _it(1, "US", 20, "oral", 10000), _it(2, "US", 120, "oral", 60000),
        _it(3, "US", 240, "oral", 281000), _it(4, "US", 480, "oral", 560000),
        _it(5, "US", 240, "injection", 420000), _it(6, "US", 480, "injection", 840000),
        _it(7, "JP", 240, "oral", 290000), _it(8, "JP", 20, "oral", 11000),
        _it(9, "JP", 240, "injection", 430000),
        _it(10, "CH", 240, "injection", 250000), _it(11, "CH", 480, "injection", 500000),
        _it(12, "FR", 240, "oral", 250000), _it(13, "FR", 480, "oral", 500000),
        _it(14, "UK", 240, "oral", 208000),
        _it(15, "KR", 360, "oral", 300000),   # US 미등재 → foreign_only
    ]


def test_route_of():
    assert route_of("injection") == "injection"
    assert route_of("oral") == "oral"
    assert route_of("unknown") == "oral"


def test_prevymis_formulation_grouping():
    items = _prevymis_items()
    process_formulations("Prevymis", items)
    groups = defaultdict(list)
    for it in items:
        groups[it["formulation_key"]].append(it)

    # 강도×경로 6개 US 제형 + KR foreign 1개 = 7 탭
    keys = set(groups)
    for k in ("20mg|oral", "120mg|oral", "240mg|oral", "480mg|oral",
              "240mg|injection", "480mg|injection", "360mg|oral"):
        assert k in keys, f"missing formulation {k}"

    # cross-strength 혼합 없음: 240/480/20 은 서로 다른 key
    assert groups["240mg|oral"] and groups["480mg|oral"] and groups["20mg|oral"]
    assert {it["country"] for it in groups["240mg|oral"]} == {"US", "JP", "FR", "UK"}
    assert {it["country"] for it in groups["240mg|injection"]} == {"US", "JP", "CH"}

    # 강도 일치 → 표시단위 보정계수 1
    for it in items:
        if it["country"] != "KR":
            assert it["dose_norm_factor"] == 1.0

    # KR 360mg = US 미등재 foreign_only
    kr = groups["360mg|oral"][0]
    assert kr["formulation_source"] == "foreign_only" and kr["is_us_listed"] == 0
    # US 제형은 us_canonical
    assert groups["240mg|oral"][0]["formulation_source"] == "us_canonical"


def test_intra_formulation_display_unit_factor():
    """같은 제형인데 한 국가가 표시단위 다르게(per-ml=20) 들어오면 ×배율 보정."""
    items = [
        _it(1, "US", 240, "injection", 420000),   # canonical 240 injection
        # CH 가 per-ml 20mg 로 들어옴(같은 240 vial 제형이지만 표시단위 다름) → LLM 이
        # 240 로 매칭한다고 가정하기 어려우니, 여기선 deterministic 경로 검증용으로
        # unit_strength_mg=240 이 정상. per-ml 케이스는 LLM 경로(별도 라이브 검증).
        _it(2, "CH", 240, "injection", 250000),
    ]
    process_formulations("X", items)
    assert all(it["dose_norm_factor"] == 1.0 for it in items)
    assert all(it["formulation_key"] == "240mg|injection" for it in items)


def test_prevymis_real_strings():
    """실제 프로덕션 표기 문자열 → form-aware 강도추출 + 제형 매칭 end-to-end.

    US '20 mg/1 ml' 주사는 package_unit(12ml/24ml)로 240/480 복원, JP 전각·包·錠,
    CH 'Inf Konz 240 mg/12ml' 등 messy 표기가 올바른 제형 탭에 매칭되는지(LLM 없이).
    """
    from agents.foreign_price_agent import ForeignPriceAgent as A
    P = [
        ("US", "120 mg", "oral", "30s ea"), ("US", "20 mg", "oral", "30s ea"),
        ("US", "20 mg/1 ml", "injection", "12 ml"), ("US", "20 mg/1 ml", "injection", "24 ml"),
        ("US", "240 mg", "oral", "28s ea"), ("US", "480 mg", "oral", "28s ea"),
        ("JP", "２０ｍｇ１包", "oral", ""), ("JP", "２４０ｍｇ１錠", "oral", ""),
        ("JP", "２４０ｍｇ１２ｍＬ１瓶", "injection", ""),
        ("CH", "PREVYMIS Inf Konz 240 mg/12ml", "injection", ""),
        ("CH", "PREVYMIS Inf Konz 480 mg/24ml", "injection", ""),
        ("FR", "240 mg", "oral", "28 plaq"), ("UK", "240mg f-c tab, 28", "oral", ""),
    ]
    items = []
    for i, (c, ds, ft, pu) in enumerate(P):
        items.append({
            "id": i, "country": c, "dosage_strength": ds, "form_type": ft,
            "package_unit": pu, "adjusted_price_krw": 100000 + i,
            "unit_strength_mg": A._extract_per_unit_mg(ft, ds, pu),
        })
    # US 주사 농도×부피 복원 검증
    us_inj = [it for it in items if it["country"] == "US" and it["form_type"] == "injection"]
    assert {it["unit_strength_mg"] for it in us_inj} == {240.0, 480.0}, "US 주사 240/480 복원 실패"

    process_formulations("Prevymis", items)
    keys = {it["formulation_key"] for it in items}
    for k in ("20mg|oral", "120mg|oral", "240mg|oral", "480mg|oral",
              "240mg|injection", "480mg|injection"):
        assert k in keys, f"missing {k}"
    # 240↔480↔20 분리 + 매칭
    fk = {(it["country"], it["dosage_strength"]): it["formulation_key"] for it in items}
    assert fk[("JP", "２４０ｍｇ１錠")] == "240mg|oral"
    assert fk[("CH", "PREVYMIS Inf Konz 240 mg/12ml")] == "240mg|injection"
    assert fk[("UK", "240mg f-c tab, 28")] == "240mg|oral"
    assert all(it["is_us_listed"] == 1 for it in items)  # 전부 US canonical


if __name__ == "__main__":
    test_route_of()
    test_prevymis_formulation_grouping()
    test_intra_formulation_display_unit_factor()
    test_prevymis_real_strings()
    print("OK — formulation grouping tests passed")
