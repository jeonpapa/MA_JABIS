"""
해외 약가 조회 에이전트 (Agent 2)
- 대쉬보드 검색 요청을 받아 국가별 스크레이퍼 실행
- 환율 계산 → 조정가 산출 → DB 저장
- 국가별 스크레이퍼는 순차적으로 추가 예정
"""

import asyncio
import json
import logging
from datetime import date
from pathlib import Path

from agents.db import DrugPriceDB
from agents.exchange_rate import ExchangeRateFetcher, PriceCalculator
from agents.scrapers.base import load_credentials

logger = logging.getLogger(__name__)

# 구현된 스크레이퍼 등록 (국가 추가 시 여기에 import + 등록)
from agents.scrapers.jp_mhlw import JpMhlwScraper
from agents.scrapers.it_aifa import ItAifaScraper
from agents.scrapers.fr_bdpm import FrBdpmScraper
from agents.scrapers.ch_compendium import ChCompendiumScraper
from agents.scrapers.uk_mims import UkMimsScraper
from agents.scrapers.de_rote_liste import DeRoteListeScraper
from agents.scrapers.ca_ontario import CaOntarioScraper
# from agents.scrapers.us_micromedex import UsMicromedexScraper   # 추후 추가

AVAILABLE_COUNTRIES = ["JP", "IT", "FR", "CH", "UK", "DE", "CA"]  # 구현 완료된 국가 목록


class ForeignPriceAgent:
    def __init__(self, base_dir: Path, ecos_api_key: str = "sample"):
        self.base_dir = base_dir
        self.db = DrugPriceDB(base_dir / "data" / "db" / "drug_prices.db")
        self.cred_path = base_dir / "config" / "foreign_credentials.json"
        self.rate_fetcher = ExchangeRateFetcher(api_key=ecos_api_key)
        self.calculator = PriceCalculator()
        self.foreign_data_dir = base_dir / "data" / "foreign"
        self.foreign_data_dir.mkdir(parents=True, exist_ok=True)

    def _build_scraper(self, country: str):
        """국가코드로 적절한 스크레이퍼 인스턴스 생성."""
        creds = load_credentials(self.cred_path, country)

        if country == "JP":
            return JpMhlwScraper(
                credentials=creds,
                cache_dir=self.foreign_data_dir / "jp",
                msd_only=False,
            )
        elif country == "IT":
            return ItAifaScraper(
                credentials=creds,
                cache_dir=self.foreign_data_dir / "it",
                msd_only=False,
            )
        elif country == "FR":
            return FrBdpmScraper(
                credentials=creds,
                cache_dir=self.foreign_data_dir / "fr",
                msd_only=False,
            )
        elif country == "CH":
            return ChCompendiumScraper(
                credentials=creds,
                cache_dir=self.foreign_data_dir / "ch",
                msd_only=False,
            )
        elif country == "UK":
            return UkMimsScraper(
                credentials=creds,
                cache_dir=self.foreign_data_dir / "uk",
                msd_only=False,
            )
        elif country == "DE":
            return DeRoteListeScraper(
                credentials=creds,
                cache_dir=self.foreign_data_dir / "de",
                msd_only=False,
            )
        elif country == "CA":
            return CaOntarioScraper(
                credentials=creds,
                cache_dir=self.foreign_data_dir / "ca",
                msd_only=False,
            )
        # elif country == "US":
        #     return UsMicromedexScraper(credentials=creds)
        else:
            raise ValueError(f"지원하지 않는 국가: {country} (구현된 국가: {AVAILABLE_COUNTRIES})")

    async def search_one_country(
        self, query: str, country: str, reference_date: date = None
    ) -> list[dict]:
        """
        단일 국가의 약가를 조회하고 DB에 저장한다.
        반환: 저장된 결과 리스트 (환율/조정가 포함)
        """
        if country not in AVAILABLE_COUNTRIES:
            raise ValueError(f"아직 구현되지 않은 국가: {country}")

        logger.info("=== [%s] '%s' 해외약가 조회 시작 ===", country, query)

        # 1) 스크레이퍼 실행 (로그인 → 검색 → 로그아웃)
        scraper = self._build_scraper(country)
        # SOURCE_TYPE: CH=compendium(0.65), FR=vidal(0.65), 그 외=None(기본 ratio)
        source_type = getattr(scraper, "SOURCE_TYPE", None)
        raw_results = await scraper.run(query)

        if not raw_results:
            logger.info("[%s] 검색 결과 없음", country)
            return []

        # 2) 환율 조회 (HIRA 기준 36개월 평균)
        currency = PriceCalculator.CURRENCY.get(country)
        try:
            rate_info = self.rate_fetcher.get_36m_average(currency, reference_date)
        except Exception as e:
            logger.warning("[%s] 환율 조회 실패, 조정가 미계산: %s", country, e)
            rate_info = None

        # 3) 조정가 계산 + DB 저장
        saved = []
        for item in raw_results:
            if rate_info and item.get("local_price") is not None:
                calc = self.calculator.calculate_adjusted_price(
                    country=country,
                    listed_price=item["local_price"],
                    exchange_rate=rate_info["rate"],
                    source_type=source_type,
                )
                item.update({
                    "exchange_rate": rate_info["rate"],
                    "exchange_rate_from": rate_info["from_month"],
                    "exchange_rate_to": rate_info["to_month"],
                    "factory_ratio": calc.get("factory_ratio"),
                    "factory_ratio_label": calc.get("factory_ratio_label", ""),
                    "factory_price": calc.get("factory_price"),
                    "factory_price_krw": calc["factory_price_krw"],
                    "krw_converted": calc.get("krw_converted"),
                    "vat_rate": calc["vat_rate"],
                    "vat_applied_krw": calc.get("vat_applied_krw"),
                    "distribution_margin": calc["distribution_margin"],
                    "adjusted_price_krw": calc["adjusted_price_krw"],
                    "source_type": source_type,
                })

            row_id = self.db.save_foreign_price(item)
            item["id"] = row_id
            saved.append(item)

        logger.info("[%s] DB 저장 완료: %d건", country, len(saved))
        return saved

    def _validate_data_integrity(self, query: str, results: dict) -> None:
        """
        스크레이퍼 결과와 API 반환 데이터 간 일치성 검증.
        데이터 손실 감지 시 경고 기록.
        """
        # 스크레이퍼 결과 통계
        scraper_count = {}
        scraper_null_count = {}
        for country, items in results.items():
            scraper_count[country] = len(items)
            scraper_null_count[country] = sum(1 for item in items if item.get("local_price") is None)

        # API 반환 데이터 통계 (DB 조회)
        api_data = self.get_cached_results(query)
        api_count = {}
        api_null_count = {}
        for country, items in api_data.items():
            api_count[country] = len(items)
            api_null_count[country] = sum(1 for item in items if item.get("local_price") is None)

        # 불일치 감지
        for country in set(list(scraper_count.keys()) + list(api_count.keys())):
            s_count = scraper_count.get(country, 0)
            a_count = api_count.get(country, 0)
            s_null = scraper_null_count.get(country, 0)
            a_null = api_null_count.get(country, 0)

            if s_count > a_count:
                logger.warning(
                    "[QualityGuard] '%s' [%s] 데이터 손실 감지: "
                    "스크레이퍼 %d건 → API %d건 (null_price: %d → %d)",
                    query, country, s_count, a_count, s_null, a_null
                )
            elif s_null > a_null:
                logger.warning(
                    "[QualityGuard] '%s' [%s] null_price 데이터 필터링 감지: "
                    "스크레이퍼 %d건 → API %d건",
                    query, country, s_null, a_null
                )

    async def search_all(
        self, query: str, countries: list[str] = None, reference_date: date = None
    ) -> dict:
        """
        여러 국가의 약가를 순차 조회.
        countries: None이면 구현된 전체 국가 조회
        반환: {country: [results, ...], ...}
        """
        targets = countries or AVAILABLE_COUNTRIES
        results = {}
        for country in targets:
            try:
                results[country] = await self.search_one_country(
                    query, country, reference_date
                )
            except Exception as e:
                logger.error("[%s] 조회 실패: %s", country, e)
                results[country] = []

        # 데이터 손실 검증
        self._validate_data_integrity(query, results)
        return results

    def get_cached_results(self, query: str) -> dict:
        """DB에 저장된 최신 검색 결과 반환 (재검색 없이). 조정가 분해 데이터 추가."""
        rows = self.db.get_foreign_prices(query)
        by_country = {}
        for row in rows:
            country = row.get("country")
            if not country:
                continue

            # 가격과 환율이 모두 존재할 때만 조정가 계산
            if row.get("local_price") is not None and row.get("exchange_rate"):
                scraper = self._build_scraper(country) if country in AVAILABLE_COUNTRIES else None
                src_type = getattr(scraper, "SOURCE_TYPE", None) if scraper else None
                calc = self.calculator.calculate_adjusted_price(
                    country=country,
                    listed_price=row["local_price"],
                    exchange_rate=row["exchange_rate"],
                    source_type=src_type,
                )
                row["factory_ratio"] = calc.get("factory_ratio")
                row["factory_ratio_label"] = calc.get("factory_ratio_label", "")
                row["factory_price"] = calc.get("factory_price")
                row["krw_converted"] = calc.get("krw_converted")
                row["factory_price_krw"] = calc["factory_price_krw"]
                row["vat_applied_krw"] = calc.get("vat_applied_krw")
                row["vat_rate"] = calc["vat_rate"]
                row["distribution_margin"] = calc["distribution_margin"]
                row["adjusted_price_krw"] = calc["adjusted_price_krw"]
                row["source_type"] = src_type
            else:
                # 가격 또는 환율 없을 때: 조정가는 계산하지 않지만 원본 데이터는 유지
                row["factory_price_krw"] = None
                row["adjusted_price_krw"] = None
                row["vat_rate"] = None
                row["distribution_margin"] = None

            by_country.setdefault(country, []).append(row)
        return by_country


def load_config(config_path: Path) -> dict:
    with open(config_path, encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    base_dir = Path(__file__).parent.parent
    agent = ForeignPriceAgent(base_dir)

    query = sys.argv[1] if len(sys.argv) > 1 else "Keytruda"
    country = sys.argv[2] if len(sys.argv) > 2 else "JP"

    results = asyncio.run(agent.search_one_country(query, country))
    print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
