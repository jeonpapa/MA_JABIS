"""실효 factory_ratio 고정 — 스크레이퍼 SOURCE_TYPE ↔ PriceCalculator 오버라이드 키 drift 탐지.

2026-10 감사: SOURCE_OVERRIDES 의 "ch_compendium"/"fr_vidal"(0.65) 는 스크레이퍼 SOURCE_TYPE("compendium"/"vidal")
과 한 번도 일치한 적이 없어 실효 비율은 CH 0.73 / FR 0.77 이었다. 사용자 결정: **코드가 현재 시점의 진실** — 0.65 를
살리지 않고 저장값도 재계산하지 않는다. 죽은 항목은 지우고, 실효 비율을 여기서 고정해 다음 drift 를 잡는다.

실행: .venv/bin/python -m pytest tests/test_foreign_factory_ratio_effective.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.exchange_rate import PriceCalculator
from agents.scrapers.ca_ontario import CaOntarioScraper
from agents.scrapers.ch_compendium import ChCompendiumScraper
from agents.scrapers.de_rote_liste import DeRoteListeScraper
from agents.scrapers.fr_bdpm import FrBdpmScraper
from agents.scrapers.fr_vidal import FrVidalScraper
from agents.scrapers.it_aifa import ItAifaScraper
from agents.scrapers.it_aifa_tnf import ItAifaTnfScraper
from agents.scrapers.jp_mhlw import JpMhlwScraper
from agents.scrapers.uk_mims import UkMimsScraper
from agents.scrapers.us_micromedex import UsMicromedexScraper

# (country, scraper class, 실효 ratio) — 스크레이퍼가 선언한 SOURCE_TYPE 으로 계산기가 실제 적용하는 값
EFFECTIVE = [
    ("US", UsMicromedexScraper, 0.74),      # "redbook_wac" — 오버라이드 없음 → 국가 기본
    ("UK", UkMimsScraper, 0.73),
    ("DE", DeRoteListeScraper, 0.6955),
    ("FR", FrBdpmScraper, 0.77),            # "vidal" — 오버라이드 없음(과거 문서의 0.65 는 발효된 적 없음)
    ("FR", FrVidalScraper, 0.77),
    ("IT", ItAifaScraper, 1.0),             # "aifa_exfactory" — 이미 ex-factory
    ("IT", ItAifaTnfScraper, 1.0),
    ("CH", ChCompendiumScraper, 0.73),      # "compendium" — 오버라이드 없음(과거 문서의 0.65 는 발효된 적 없음)
    ("JP", JpMhlwScraper, 0.79),
    ("CA", CaOntarioScraper, 0.81),
]


@pytest.mark.parametrize("country,scraper_cls,ratio", EFFECTIVE)
def test_effective_ratio_for_each_scraper(country, scraper_cls, ratio):
    src = getattr(scraper_cls, "SOURCE_TYPE", None)
    assert PriceCalculator().resolve_factory_ratio(country, src) == ratio, (country, src)


def test_every_override_key_is_a_declared_source_type():
    """오버라이드 키가 어떤 스크레이퍼의 SOURCE_TYPE 과도 안 맞으면 죽은 항목 — 문서와 코드가 또 갈라진다."""
    declared = {getattr(c, "SOURCE_TYPE", None) for _, c, _ in EFFECTIVE} - {None}
    dead = set(PriceCalculator.SOURCE_OVERRIDES) - declared
    assert not dead, f"스크레이퍼가 쓰지 않는 SOURCE_OVERRIDES 키: {dead}"
    assert "ch_compendium" not in PriceCalculator.SOURCE_OVERRIDES
    assert "fr_vidal" not in PriceCalculator.SOURCE_OVERRIDES


def test_keytruda_fr_ch_rows_use_country_default_ratio():
    """실제 DB 의 FR Vidal 행(#1504)·CH compendium 행이 쓰는 비율 — 저장 factory_price_krw 와 일치(2379302 = ×0.77)."""
    c = PriceCalculator()
    fr = c.calculate_adjusted_price("FR", 2000.74, 1544.43, pack_count=1, source_type=FrVidalScraper.SOURCE_TYPE)
    assert fr["factory_ratio"] == 0.77 and fr["factory_price_krw"] == 2_379_302
    assert fr["factory_ratio_label"] == "FR factory_ratio=0.77"
    ch = c.calculate_adjusted_price("CH", 31.75, 1700.0, pack_count=1, source_type=ChCompendiumScraper.SOURCE_TYPE)
    assert ch["factory_ratio"] == 0.73


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
