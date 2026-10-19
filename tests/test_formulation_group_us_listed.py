"""제형 그룹의 US 등재 여부 — 그룹 안 어느 행이든 US canonical 이면 등재 (2026-10-19 PREVYMIS 검증 차단 회귀).

첫 행(JP/CH 등 foreign_ref, is_us_listed=0)의 값만 그룹에 쓰면 US WAC 카드가 있는데도 'US 미등재' 배지가 떴다.
"""
from agents.foreign_price_agent import ForeignPriceAgent


def _row(country, us, price=100.0):
    return {"country": country, "formulation_key": "20mg|oral", "formulation_label": "20mg 경구",
            "canonical_strength_mg": 20, "route": "oral", "is_us_listed": 1 if us else 0,
            "formulation_source": "us_canonical" if us else "foreign_ref", "adjusted_price_krw": price}


def _groups(by_country):
    agent = object.__new__(ForeignPriceAgent)          # by_country 를 주면 DB·스크레이퍼를 쓰지 않는다
    return agent.get_formulation_groups("prevymis", by_country=by_country)


def test_group_is_us_listed_when_us_row_comes_after_foreign_rows():
    g = _groups({"JP": [_row("JP", False)], "CH": [_row("CH", False)], "US": [_row("US", True)]})["20mg|oral"]
    assert g["is_us_listed"] == 1 and g["source"] == "us_canonical"
    assert set(g["countries"]) == {"JP", "CH", "US"}


def test_group_without_us_row_stays_unlisted():
    g = _groups({"JP": [_row("JP", False)], "CH": [_row("CH", False)]})["20mg|oral"]
    assert g["is_us_listed"] == 0 and g["source"] == "foreign_ref"
