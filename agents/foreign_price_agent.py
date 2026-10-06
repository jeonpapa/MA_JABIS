"""
해외 약가 조회 에이전트 (Agent 2)
- 대쉬보드 검색 요청을 받아 국가별 스크레이퍼 실행
- 환율 계산 → 조정가 산출 → DB 저장
- 국가별 스크레이퍼는 순차적으로 추가 예정
"""

import asyncio
import json
import logging
import re
from datetime import date
from pathlib import Path
from typing import Optional

from agents.db import DrugPriceDB
from agents.exchange_rate import ExchangeRateFetcher, PriceCalculator
from agents.scrapers.base import load_credentials

logger = logging.getLogger(__name__)

# 구현된 스크레이퍼 등록 (국가 추가 시 여기에 import + 등록)
from agents.scrapers.jp_mhlw import JpMhlwScraper
from agents.scrapers.it_aifa import ItAifaScraper
from agents.scrapers.it_aifa_tnf import ItAifaTnfScraper
from agents.scrapers.fr_bdpm import FrBdpmScraper
from agents.scrapers.fr_vidal import FrVidalScraper
from agents.scrapers.ch_compendium import ChCompendiumScraper
from agents.scrapers.uk_mims import UkMimsScraper
from agents.scrapers.de_rote_liste import DeRoteListeScraper
from agents.scrapers.ca_ontario import CaOntarioScraper
from agents.scrapers.us_micromedex import UsMicromedexScraper

AVAILABLE_COUNTRIES = ["JP", "IT", "FR", "CH", "UK", "DE", "CA", "US"]  # 구현 완료된 국가 목록

# Pack-pricing 국가: scraper 가 pack 단위 가격을 반환 → pack_count 필수
PACK_PRICING_COUNTRIES = {"UK", "US", "DE", "FR", "IT", "CH"}

# Per-unit pricing 국가: scraper 가 per-tablet/per-vial 가격을 반환 → pack_count=1
PER_UNIT_COUNTRIES = {"JP", "CA"}

# Daily cost sanity cap (Welireg UK ₩46M/day 회귀 방지)
DAILY_COST_SANITY_CAP_KRW = 10_000_000


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
        elif country == "US":
            return UsMicromedexScraper(
                credentials=creds,
                cache_dir=self.foreign_data_dir / "us",
                msd_only=False,
            )
        else:
            raise ValueError(f"지원하지 않는 국가: {country} (구현된 국가: {AVAILABLE_COUNTRIES})")

    async def search_one_country(
        self, query: str, country: str, reference_date: date = None,
        identity: dict = None
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
        # identity 있으면 brand 로 검색(대부분 소스가 브랜드 인덱스), 저장/집계는 slug 로 통일.
        ident = identity or {}
        search_term = ident.get("brand") or query
        canonical_name = ident.get("slug") or query
        fallback_inn = ident.get("inn")
        try:
            raw_results = await scraper.run(search_term)
        except Exception as e:
            # IT 는 2채널(CSV+TNF)이라 CSV 실패가 TNF 를 막으면 안 됨 (예: AIFA SSL 오류).
            # 다른 국가는 단일 채널이므로 기존대로 전파(search_all 이 국가별로 catch).
            if country == "IT":
                logger.warning("[IT] CSV 채널 실패(TNF 로 계속): %s", e)
                raw_results = []
            else:
                raise
        # brand 0건 & 스크레이퍼 opt-in & INN 존재 → INN 으로 1회 재시도
        if (not raw_results and getattr(scraper, "FALLBACK_TO_INN", False)
                and fallback_inn and fallback_inn.lower() != str(search_term).lower()):
            logger.info("[%s] brand 0건 → INN 폴백 검색: %s", country, fallback_inn)
            try:
                raw_results = await scraper.run(fallback_inn)
            except Exception as e:
                logger.warning("[%s] INN 폴백 실패: %s", country, e)

        # IT: TrovaNorme 결정문 채널을 기존 CSV 채널과 **병행** (둘 다 aifa_exfactory).
        # 결정문(determina)에서 성분·제형·ex-factory 가격을 직접 확인 — CSV 미등재/신규
        # 협상 약제를 보완. 실패해도 CSV 결과는 유지.
        if country == "IT":
            raw_results = list(raw_results or [])
            try:
                tnf = ItAifaTnfScraper(
                    cache_dir=self.foreign_data_dir / "it", msd_only=False,
                )
                tnf_rows = await tnf.run(search_term)
                if tnf_rows:
                    raw_results.extend(tnf_rows)
                    logger.info("[IT] TrovaNorme 채널 +%d건 (총 %d건)",
                                len(tnf_rows), len(raw_results))
            except Exception as e:
                logger.warning("[IT] TrovaNorme 채널 실패(CSV 결과 유지): %s", e)

        # FR: Vidal 채널을 기존 BDPM 과 **병행** (둘 다 source_type=vidal, ratio 0.65).
        # 병원용 주사제(keytruda 등)는 BDPM 공시가가 없고 Vidal UCD(HT) 에만 공개 →
        # 큐레이션 URL 맵으로 안정 조회. 실패해도 BDPM 결과는 유지.
        if country == "FR":
            raw_results = list(raw_results or [])
            try:
                vidal = FrVidalScraper(
                    cache_dir=self.foreign_data_dir / "fr", msd_only=False,
                )
                vidal_rows = await vidal.run(search_term)
                if vidal_rows:
                    raw_results.extend(vidal_rows)
                    logger.info("[FR] Vidal 채널 +%d건 (총 %d건)",
                                len(vidal_rows), len(raw_results))
            except Exception as e:
                logger.warning("[FR] Vidal 채널 실패(BDPM 결과 유지): %s", e)

        if not raw_results:
            logger.info("[%s] 검색 결과 없음", country)
            return []

        # 브랜드/성분 어느 입력이든 같은 캐시 레코드로 집계 → query_name = canonical slug
        for item in raw_results:
            item["query_name"] = canonical_name

        # 방어적 fallback: 신규/기존 스크레이퍼가 form_type 을 빠뜨려도
        # 파이프라인 단에서 반드시 채워넣는다 (최소단위 원칙 파이프라인 보장).
        for item in raw_results:
            if not item.get("form_type"):
                item["form_type"] = scraper._resolve_form_type(item)

        # 2) 환율 조회 (HIRA 기준 36개월 평균)
        currency = PriceCalculator.CURRENCY.get(country)
        try:
            rate_info = self.rate_fetcher.get_36m_average(currency, reference_date)
        except Exception as e:
            logger.warning("[%s] 환율 조회 실패, 조정가 미계산: %s", country, e)
            rate_info = None

        # dosing 조회 (pack_count fallback + daily_cost 계산용)
        ingredients = [r.get("ingredient", "") for r in raw_results]
        dosing_map = self._load_dosing_map(canonical_name, ingredients)

        # 3) 조정가 계산 + DB 저장
        saved = []
        for item in raw_results:
            dosing = self._match_dosing(canonical_name, item.get("ingredient", ""), dosing_map)
            pack_count = self._resolve_pack_count(country, item, dosing)

            if rate_info and item.get("local_price") is not None:
                calc = self.calculator.calculate_adjusted_price(
                    country=country,
                    listed_price=item["local_price"],
                    exchange_rate=rate_info["rate"],
                    pack_count=pack_count,
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
                    "adjusted_price_krw": calc["adjusted_price_krw"],   # per-unit KRW
                    "pack_count": calc["pack_count"],
                    "per_unit_local": calc["per_unit_listed"],
                    "source_type": source_type,
                })

            # Daily cost 계산 (per-unit adj × (daily_dose / single_unit_mg))
            self._populate_daily_cost(item, dosing)

            row_id = self.db.save_foreign_price(item)
            item["id"] = row_id
            saved.append(item)

        logger.info("[%s] DB 저장 완료: %d건", country, len(saved))
        return saved

    def _resolve_pack_count(self, country: str, item: dict, dosing: dict) -> int:
        """pack_count 결정 — **form_type 우선 원칙**. 결정 근거를 `item["pack_count_source"]` 에 남긴다.

        최소단위는 제형에 따라 다르다:
          - oral      → 최소단위 = 1 tablet/capsule
          - injection → 최소단위 = 1 vial/ampoule/syringe
          - unknown   → 1 로 fallback (per-unit 으로 안전 가정)

        우선순위 (pack_count_source):
          1) `scraper`  — 스크레이퍼가 item 에 직접 넣은 pack_count (IT TNF 등). 스크레이퍼 직후 item
             (id 없음) 이거나 저장 시 source='scraper' 였던 행만. **DB 행의 pack_count 는 에이전트
             산물이라 그대로 믿지 않고 다시 결정한다** (과거 default=1 이 영구화되는 것 방지).
          2) `per_unit` — JP/CA 는 per-unit 가격 체계 → 항상 1.
          3) `parsed`   — 포장 문구의 수량 단서 (`_extract_unit_count`, form-aware). injection 도
             "in vial, 2"·"2 flaconcini"·"4 ml 2s" 같은 단서를 읽는다. **mg ratio 는 쓰지 않는다**
             (injection 은 농도×volume/농도 = volume 이라 pack count 가 아님 — CLAUDE.md 절대 금지).
          4) `dosing`   — oral × pack-pricing 국가에서 dosing.default_pack_count.
          5) `default`  — 1. oral/unknown 이면 '팩가가 per-unit 으로 저장된' 미확인 행이므로
             get_cached_results 가 A8 집계에서 제외한다 (injection 은 1 vial 보수 fallback 이 규칙).
        """
        pc = item.get("pack_count")
        has_pc = isinstance(pc, (int, float)) and pc > 0
        src = item.get("pack_count_source")
        if has_pc and (src == "scraper" or (src is None and "id" not in item)):
            item["pack_count_source"] = "scraper"
            return int(pc)

        form_type = (item.get("form_type") or "unknown").lower()

        if country in PER_UNIT_COUNTRIES:
            item["pack_count_source"] = "per_unit"
            return 1

        parsed = self._extract_unit_count(
            form_type,
            item.get("dosage_strength", "") or "",
            item.get("package_unit", "") or "",
            product_name=item.get("product_name", "") or "",
            dosage_form=item.get("dosage_form", "") or "",
        )
        if parsed:
            item["pack_count_source"] = "parsed"
            return parsed

        # dosing.default_pack_count 는 oral 전용 (typical 28/30 blister).
        # injection 은 1 vial 로 보수 fallback (마커 없으면 단일 vial 가정).
        if (
            form_type == "oral"
            and country in PACK_PRICING_COUNTRIES
            and dosing
            and dosing.get("default_pack_count")
        ):
            logger.info(
                "[PackCount] %s %s fallback → dosing.default_pack_count=%d (form=oral)",
                item.get("query_name") or item.get("ingredient"),
                country, dosing["default_pack_count"],
            )
            item["pack_count_source"] = "dosing"
            return int(dosing["default_pack_count"])
        item["pack_count_source"] = "default"
        return 1

    def _populate_daily_cost(self, item: dict, dosing: dict) -> None:
        """item 에 daily_dose_mg / daily_cost_krw / total_pkg_mg / daily_cost_note 채움.

        공식: daily_cost_krw = daily_dose_mg × (adjusted_price_krw / unit_mg)
             단, unit_mg = 최소단위(tablet/vial) 당 mg — **form_type 으로 결정**.
             - oral      → per-tablet mg        (예: Welireg 40 mg/tab)
             - injection → per-vial total mg    (예: Keytruda 100 mg/vial, NOT 25 mg/mL)
             adjusted_price_krw 는 per-unit 이므로 분모도 per-unit 이어야 일관.
        """
        # 재계산이다 — 캐시 행의 저장값을 먼저 비운다 (분모 불명으로 skip 할 때 과거 값이 남으면 안 된다:
        # FR Vidal #1504 의 저장 daily_cost ₩1,083,683 이 그렇게 살아남았다).
        item["daily_dose_mg"] = None
        item["daily_cost_krw"] = None
        item["daily_cost_note"] = None
        item["total_pkg_mg"] = None

        adj = item.get("adjusted_price_krw")
        form_type = (item.get("form_type") or "unknown").lower()
        unit_mg = self._extract_per_unit_mg(
            form_type,
            item.get("dosage_strength", "") or "",
            item.get("package_unit", "") or "",
            product_name=item.get("product_name", "") or "",
            dosage_form=item.get("dosage_form", "") or "",
        )
        pack_count = item.get("pack_count") or 1
        # total_pkg_mg (참고용): oral 은 per-tab × pack, injection 은 per-vial × vial_count
        total_mg = (unit_mg * pack_count) if (unit_mg and pack_count) else None
        if total_mg:
            item["total_pkg_mg"] = round(total_mg, 2)
        elif form_type == "injection" and (item.get("dosage_strength") or item.get("product_name")):
            # 농도만 있고 volume 을 모른다 — per-vial mg 불명. 분모를 추정하지 않고 skip 을 기록한다
            # (제형 보정 레이어도 이 플래그를 보고 regex fallback 으로 농도를 강도로 쓰지 않는다).
            item["unit_strength_unresolved"] = True
            item["total_pkg_mg"] = None
            if item.get("daily_cost_note") != "suspicious_outlier":
                item["daily_cost_note"] = "unit_mg_unresolved"

        if not dosing or adj is None or not unit_mg or not dosing.get("cycle_days"):
            return
        if dosing["cycle_days"] <= 0:
            return

        daily_dose = dosing["dose_per_cycle_mg"] / dosing["cycle_days"]
        item["daily_dose_mg"] = round(daily_dose, 3)

        daily_cost = daily_dose * (adj / unit_mg)   # adj 는 per-unit, unit_mg 는 per-unit
        if daily_cost > DAILY_COST_SANITY_CAP_KRW:
            logger.warning(
                "[DailyCost] %s %s 이상치 스킵: %.0f KRW (adj=%s, unit_mg=%s, pack_count=%s) "
                "dosage=%r package=%r",
                item.get("query_name"), item.get("country"), daily_cost,
                adj, unit_mg, pack_count,
                item.get("dosage_strength"), item.get("package_unit"),
            )
            item["daily_cost_note"] = "suspicious_outlier"
            return
        item["daily_cost_krw"] = int(round(daily_cost))

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
        self, query: str, countries: list[str] = None,
        reference_date: date = None, identity: dict = None
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
                    query, country, reference_date, identity=identity
                )
            except Exception as e:
                logger.error("[%s] 조회 실패: %s", country, e)
                results[country] = []

        # 데이터 손실 검증
        self._validate_data_integrity(query, results)

        # 국가간 용량(strength) 정규화 후처리 — per-unit 비교 공정성.
        # 전 국가 priced 행을 모아 strength 불일치 감지(regex) → 불일치 시 LLM 보정.
        try:
            self._normalize_doses_across_countries(query, results)
        except Exception as e:
            logger.warning("[DoseNorm] %s 정규화 후처리 실패(원본가 유지): %s", query, e)

        # 압축: 약제당 국가별 '최신 1세션'만 유지 (append 누적 방지).
        # dose-norm UPDATE 이후에 실행해야 최신 세션의 보정값이 보존된다.
        try:
            removed = self.db.compact_foreign_prices(query)
            if removed:
                logger.info(
                    "[Compact] %s: 과거 누적 %d행 정리 (약제당 국가별 최신 1세션 유지)",
                    query, removed,
                )
        except Exception as e:
            logger.warning("[Compact] %s 압축 실패(누적 유지): %s", query, e)

        return results

    def _normalize_doses_across_countries(self, query: str, results: dict) -> None:
        """search_all 결과의 제형(formulation)별 매칭 + 제형 내 표시단위 보정 후처리."""
        all_items = [it for items in results.values() for it in (items or [])]
        self._assign_formulations_and_persist(query, all_items)

    def _assign_formulations_and_persist(self, query: str, all_items: list) -> None:
        """priced 행에 form-aware unit_strength_mg 부착 → process_formulations(제형 매칭
        + 제형 내 표시단위 보정) → 결과를 DB(기존 행)에 UPDATE. US canonical 기준."""
        from agents.foreign_dose_normalize import process_formulations

        if not all_items:
            return
        # form-aware per-unit 활성성분 mg 부착 (주사 농도×부피 복원) — 가격 유무 무관 전 행.
        # 제형 강도 판정 기준. 미가격 행도 같은 제형 탭에 묶기 위함.
        for it in all_items:
            try:
                derived = self._extract_per_unit_mg(
                    (it.get("form_type") or "unknown"),
                    it.get("dosage_strength") or "",
                    it.get("package_unit") or "",
                    product_name=it.get("product_name") or "",
                    dosage_form=it.get("dosage_form") or "",
                )
            except Exception:
                derived = None
            if derived is not None:
                if not it.get("unit_strength_mg"):
                    it["unit_strength_mg"] = derived
            elif (it.get("form_type") or "").lower() == "injection" and (
                    it.get("dosage_strength") or it.get("product_name")):
                # 농도만 있고 volume 불명 — 저장된 값(예: 25 = per-mL 농도)을 강도로 쓰면 보정계수가 4× 틀어진다.
                it["unit_strength_mg"] = None
                it["unit_strength_unresolved"] = True
            elif not it.get("unit_strength_mg"):
                it["unit_strength_mg"] = None
        updates = process_formulations(query, all_items)
        for u in updates:
            if u.get("id"):
                self.db.update_foreign_dose_norm(u["id"], u)
        if updates:
            nkeys = len({u.get("formulation_key") for u in updates})
            logger.info("[Formulation] %s: %d행 → %d제형 배정", query, len(updates), nkeys)

    def _load_dosing_map(self, query: str, ingredients: list[str]) -> dict:
        """query_name 또는 ingredient 로 foreign_drug_dosing 조회.
        반환: {ingredient_key(lowercase): {dose_per_cycle_mg, cycle_days, schedule_label, display_name}}
        """
        import sqlite3
        dosing_map = {}
        try:
            conn = sqlite3.connect(self.db.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            # query 자체와 ingredient 목록 — 모두 소문자 정규화
            keys = {query.strip().lower()}
            for ing in ingredients:
                if ing:
                    # 복수 단어 중 첫 영문 토큰만 사용 (예: "Pembrolizumab Rote Liste..." → "pembrolizumab")
                    first_word = re.split(r"[\s,;()/]", ing.strip())[0].lower()
                    if first_word:
                        keys.add(first_word)
            for k in keys:
                cur.execute(
                    "SELECT * FROM foreign_drug_dosing WHERE ingredient_key = ?", (k,)
                )
                row = cur.fetchone()
                if row:
                    dosing_map[k] = dict(row)
            conn.close()
        except Exception as e:
            logger.debug("[DailyCost] dosing 조회 실패: %s", e)
        return dosing_map

    @staticmethod
    def _extract_mg(text: str) -> float:
        """dosage_strength 에서 단위강도 mg 수치 추출 (정·병 1개당).
        예: '240 mg', '240mg/12ml', '100 mg/4 ml' → 240 / 240 / 100
        일본 MHLW zenkaku 포맷도 지원: '１００ｍｇ４ｍＬ１瓶' → 100
        실패 시 None.
        """
        if not text:
            return None
        text = text.translate(str.maketrans(
            "０１２３４５６７８９．ｍｇｋＭＧＫ",
            "0123456789.mgkMGK",
        ))
        m = re.search(r"(\d+(?:\.\d+)?)\s*mg", text, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                return None
        m = re.search(r"(\d+(?:\.\d+)?)\s*mcg", text, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1)) / 1000.0
            except ValueError:
                return None
        return None

    @classmethod
    def _extract_per_unit_mg(cls, form_type: str, dosage_strength: str,
                             package_unit: str = "", product_name: str = "",
                             dosage_form: str = "") -> Optional[float]:
        """최소단위(tablet/vial) 1개당 mg — **form_type 기반**.

        - oral: dosage_strength 의 "N mg" 값이 per-tablet. _extract_mg 와 동일.
        - injection: **per-vial 총량**만 돌려준다. 복원 경로는 둘뿐:
            1) "N mg/M ml" (M>1) → N  (예: "100 mg/4 ml" → 100)
            2) 농도 "N mg/ml"·"N mg/1 ml" (dosage_strength 또는 product_name/dosage_form 어디든) × volume
               (dosage_strength·package_unit·문맥의 "V ml", 농도 분모 제외) → N×V  (예: "25 mg" + "1 flacon(s) de 4 ml" → 100)
          농도 표기가 있는데 volume 을 못 찾으면 **None** — bare "25 mg" 를 per-vial 로 돌려주면 per-mL 농도가
          daily_cost 분모가 돼 4× 왜곡된다(CLAUDE.md 절대 금지; FR Vidal #1504 가 그 사례). 호출측은 계산을 skip 한다.
          농도 표기가 어디에도 없는 bare "N mg" 는 총량이다 (예: DE "240 mg", JP "100mg4mL1瓶").
        - unknown: oral 로 가정 (가장 보수적으로 per-tablet).
        """
        if not dosage_strength and not (product_name or dosage_form):
            return None
        if form_type == "oral" or form_type == "unknown":
            return cls._extract_mg(dosage_strength) if dosage_strength else None
        # injection: per-vial total 복원
        s = (dosage_strength or "").translate(cls._ZENKAKU)
        p = (package_unit or "").translate(cls._ZENKAKU)
        ctx = [t.translate(cls._ZENKAKU) for t in (dosage_form or "", product_name or "") if t]
        # 1) "N mg/M ml" (M>1) → 그 바이알 총량 N mg (강도 문자열 우선, 없으면 문맥)
        for text in (s, *ctx):
            m = cls._MG_PER_VOL.search(text)
            if m and float(m.group(2)) > 1.0:
                return float(m.group(1))
        # 2) 농도 (N mg/ml, N mg/1 ml) 가 어디든 있으면 N 은 per-mL — volume 이 있어야 per-vial
        conc = None
        for text in (s, *ctx):
            m = cls._MG_PER_ML.search(text)
            if m:
                conc = float(m.group(1))
                break
        if conc is not None:
            vol = cls._find_volume_ml(s, p, *ctx)
            return conc * vol if vol else None
        # 3) 농도 표기 없음 → bare "N mg" 는 per-vial 총량
        return cls._extract_mg(s) if s else None

    _MG_PER_VOL = re.compile(r"(\d+(?:\.\d+)?)\s*mg\s*/\s*(\d+(?:\.\d+)?)\s*m[lL]\b", re.IGNORECASE)
    _MG_PER_ML = re.compile(r"(\d+(?:\.\d+)?)\s*mg\s*/\s*(?:1\s*)?m[lL]\b", re.IGNORECASE)
    _CONC_TOKEN = re.compile(r"mg\s*/\s*(?:\d+(?:\.\d+)?\s*)?m[lL]\b", re.IGNORECASE)
    _VOLUME_ML = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*m[lL]\b", re.IGNORECASE)

    @classmethod
    def _find_volume_ml(cls, *texts: str) -> Optional[float]:
        """바이알 부피 "V ml" — 농도 분모("mg/ml", "mg/1 ml") 는 지운 뒤 찾는다. 없으면 None."""
        for t in texts:
            if not t:
                continue
            m = cls._VOLUME_ML.search(cls._CONC_TOKEN.sub("mg/_", t))
            if m:
                v = float(m.group(1))
                if v > 0:
                    return v
        return None

    # ── 포장 수량 단서 (form-aware) ────────────────────────────────────────
    # 전각 숫자·단위 → 반각 (JP MHLW "１００ｍｇ４ｍＬ１瓶")
    _ZENKAKU = str.maketrans(
        "０１２３４５６７８９．ｍｇｋｍｌＭＧＫＬｘＸ×",
        "0123456789.mgkmlMGKLxXx",
    )
    _ORAL_UNIT_WORDS = (
        r"(?:compress[ae]|compresse|comprim[ée]s?|cpr|cps|cp|tablets?|tabs?|capsules?|caps|"
        r"g[ée]lules?|Filmtabl\w*|Tabl\w*|Kaps\w*|Stk|Stück|St|pcs|錠|カプセル|包|정|캡슐|unit[aà]'?|units?)"
    )
    _INJ_UNIT_WORDS = (
        r"(?:vials?|flacons?|flaconcin[oi]|fiale|fiala|ampoules?|amp|ampullen?|syringes?|seringues?|"
        r"siringh?[ea]|pens?|penne|stylos?|Durchstechfl\w*|Fertigspritz\w*|瓶|バイアル|筒|キット|"
        r"바이알|병|unit[aà]'?)"
    )
    _COUNT_PATTERNS = {
        # 공통 — 제형 무관하게 의미가 명확한 단서. (N x M) 은 괄호가 M 바로 뒤에 닫힐 때만 곱한다
        # ("(2 x 4 ml)" 은 2 바이알 × 4 ml 이라 곱하면 안 됨 → 아래 injection "N x M ml" 이 담당).
        "common": [
            (re.compile(r"\((\d{1,3})\s*x\s*(\d{1,3})\)"), "mul"),                      # IT TNF "90 (3 x 30) compresse"
            (re.compile(r"m[lL]\s+(\d{1,4})s\b", re.I), "one"),                           # US Redbook "4 ml 2s"
            (re.compile(r"\b(\d{1,4})s\s+ea\b", re.I), "one"),                            # US Redbook "90s ea"
        ],
        "oral": [
            (re.compile(r"\*\s*(\d{1,4})(?:\s*x\s*(\d{1,3}))?\s*(?:cpr|cps|cp\b|compress|capsul|bust)", re.I), "mul"),   # IT CSV "JANUVIA*28 cpr", "*28x1 cpr"
            (re.compile(r"(\d{1,4})\s*plaquettes?\b.*?\bde\s+(\d{1,4})\s*(?:comprim|g[ée]lule|capsule)", re.I | re.S), "mul"),   # FR "28 plaquette(s) … de 1 comprimé"
            (re.compile(r"\bde\s+(\d{1,4})\s*(?:comprim|g[ée]lule|capsule|sachet)", re.I), "one"),                       # FR "de 30 comprimé(s)"
            (re.compile(rf"(?<![\d.,/])(\d{{1,4}})\s*{_ORAL_UNIT_WORDS}(?![a-zA-Zäöü])", re.I), "one"),                 # "56 compresse", "28 UNITA'", "90 Stück", "1錠"
            (re.compile(rf"{_ORAL_UNIT_WORDS}[^,]*?,\s*(\d{{1,4}})\b", re.I), "one"),                                    # UK MIMS "f-c tab marked 177, 90"
        ],
        "injection": [
            (re.compile(r"\*\s*(\d{1,4})\s*(?:flaconcin|fiale|fiala|siring|penn)", re.I), "one"),                       # IT CSV "PREVYMIS*1 flaconcino"
            (re.compile(r"\bde\s+(\d{1,4})\s*(?:flacon|ampoule|seringue|stylo)", re.I), "one"),                          # FR "de 2 flacon(s)"
            (re.compile(r"(?<![\d.,/])(\d{1,3})\s*x\s*\d+(?:\.\d+)?\s*m[lL]\b", re.I), "one"),                           # "2 x 4 ml" → 2 바이알
            (re.compile(rf"(?<![\d.,/])(\d{{1,4}})\s*{_INJ_UNIT_WORDS}(?![a-zA-Zäöü])", re.I), "one"),                  # "2 flaconcini", "1 flacon(s)", "1瓶"
            (re.compile(rf"{_INJ_UNIT_WORDS}[^,]*?,\s*(\d{{1,4}})\b", re.I), "one"),                                     # UK MIMS "in vial, 2"
        ],
        "tail": [
            (re.compile(r",\s*(\d{1,3})\s*$"), "one"),                                                                  # 꼬리 ", 90"
        ],
    }

    @classmethod
    def _extract_unit_count(cls, form_type: str, dosage_strength: str, package_unit: str = "",
                            product_name: str = "", dosage_form: str = "") -> Optional[int]:
        """포장 문구에서 **최소단위 수량**(정/바이알 수) 단서를 읽는다 — form-aware 공용 추출기.

        `_resolve_pack_count`(pack_count) 와 `_extract_total_pkg_mg`(pack 총 mg) 가 같이 쓴다.
        단서가 없으면 None — 추정하지 않는다 (호출측이 dosing fallback/default 로 처리하고 출처를 기록).
        mg·ml 숫자(강도·농도·부피) 는 수량이 아니다: injection 에서 total_mg/unit_mg ratio 로 수량을
        만들면 volume 이 나온다(절대 금지) — 여기엔 그런 경로가 없다.

        oral 단서: IT "*28 cpr"/"56 UNITA'", FR "de 30 comprimé(s)"/"28 plaquette(s) … de 1 comprimé",
                   DE/CH "90 Stück"/"28 Stk", UK ", 90", US "90s ea", IT TNF "90 (3 x 30) compresse".
        injection 단서: UK "in vial, 2", IT "2 flaconcini", FR "1 flacon(s)", US "4 ml 2s", "2 x 4 ml", JP "1瓶".
        """
        ft = (form_type or "unknown").lower()
        texts = [dosage_strength or "", package_unit or "", dosage_form or "", product_name or ""]
        texts = [t.translate(cls._ZENKAKU) for t in texts if t]
        if not texts:
            return None
        if ft == "oral":
            groups = ("common", "oral", "tail")
        elif ft == "injection":
            groups = ("common", "injection", "tail")
        else:
            groups = ("common", "oral", "injection", "tail")
        for text in texts:
            for g in groups:
                for pat, kind in cls._COUNT_PATTERNS[g]:
                    m = pat.search(text)
                    if not m:
                        continue
                    n = int(m.group(1))
                    if kind == "mul" and m.lastindex and m.lastindex >= 2 and m.group(2):
                        n *= int(m.group(2))
                    if 1 <= n <= 1000:
                        return n
        return None

    @classmethod
    def _extract_total_pkg_mg(cls, form_type: str, dosage_strength: str, package_unit: str = "",
                              product_name: str = "", dosage_form: str = "") -> Optional[float]:
        """포장(pack) 총 mg = 최소단위 mg(`_extract_per_unit_mg`, form-aware) × 수량(`_extract_unit_count`).

        예: "40mg … tab …, 90" → 40 × 90 = 3600 / "100mg/4ml … in vial, 2" → 100 × 2 = 200.
        최소단위 mg 를 복원할 수 없으면(injection 농도만 있고 volume 없음) None.
        수량 단서가 없으면 1 로 본다 (표시용 — pack_count 결정은 `_resolve_pack_count` 가 한다).
        """
        per_unit_mg = cls._extract_per_unit_mg(form_type, dosage_strength, package_unit,
                                               product_name=product_name, dosage_form=dosage_form)
        if per_unit_mg is None:
            return None
        count = cls._extract_unit_count(form_type, dosage_strength, package_unit,
                                        product_name=product_name, dosage_form=dosage_form) or 1
        return per_unit_mg * count

    def _match_dosing(self, query: str, ingredient: str, dosing_map: dict) -> dict:
        """query + ingredient 에서 dosing 정보를 찾아 반환. 없으면 None."""
        # 1) 쿼리 자체
        key = (query or "").strip().lower()
        if key in dosing_map:
            return dosing_map[key]
        # 2) ingredient 첫 토큰
        if ingredient:
            first = re.split(r"[\s,;()/]", ingredient.strip())[0].lower()
            if first in dosing_map:
                return dosing_map[first]
        return None

    def get_cached_results(self, query: str) -> dict:
        """DB에 저장된 최신 검색 결과 반환 (재검색 없이).
        저장값을 신뢰하되, local_price/exchange_rate 가 있으면 규칙 변경 반영을 위해
        현재 calculator 로 재계산. adjusted_price_krw 는 per-unit KRW 기준.
        """
        rows = self.db.get_foreign_presentations(query)   # 국가×제형 전부 (collapse 안 함)
        by_country = {}
        ingredients = [r.get("ingredient", "") for r in rows]
        dosing_map = self._load_dosing_map(query, ingredients)

        scraper_cache: dict[str, str | None] = {}

        for row in rows:
            country = row.get("country")
            if not country:
                continue

            dosing = self._match_dosing(query, row.get("ingredient", ""), dosing_map)
            pack_count = self._resolve_pack_count(country, row, dosing)
            row["pack_count"] = pack_count

            if row.get("local_price") is not None and row.get("exchange_rate"):
                if country not in scraper_cache:
                    try:
                        scr = self._build_scraper(country) if country in AVAILABLE_COUNTRIES else None
                        scraper_cache[country] = getattr(scr, "SOURCE_TYPE", None) if scr else None
                    except Exception:
                        scraper_cache[country] = None
                src_type = scraper_cache[country]
                calc = self.calculator.calculate_adjusted_price(
                    country=country,
                    listed_price=row["local_price"],
                    exchange_rate=row["exchange_rate"],
                    pack_count=pack_count,
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
                row["adjusted_price_krw"] = calc["adjusted_price_krw"]     # per-unit KRW
                row["per_unit_local"] = calc["per_unit_listed"]
                row["source_type"] = src_type
                # daily_cost 재계산 (규칙 변경 반영) — form-aware unit_mg 판정도 여기서 나온다
                self._populate_daily_cost(row, dosing)
                # 용량 정규화가도 재계산된 raw 에 저장된 factor 를 곱해 일관성 유지.
                factor = row.get("dose_norm_factor")
                if row.get("unit_strength_unresolved") and factor and abs(factor - 1.0) > 1e-9:
                    # 저장 factor 는 농도(per-mL)를 강도로 오인한 산물(예: 25→100 ×4) — 보정 미적용으로 표시.
                    # 저장 행은 건드리지 않는다(재수집 대상 — QG 데이터 검사가 매일 보고).
                    row["adjusted_price_krw_normalized"] = calc["adjusted_price_krw"]
                    row["dose_norm_factor"] = None
                    row["unit_strength_mg"] = None
                    row["dose_norm_note"] = "주사 per-vial mg 복원 불가(농도만 기재) — 저장 보정계수 미적용"
                elif factor:
                    row["adjusted_price_krw_normalized"] = int(round(
                        calc["adjusted_price_krw"] * factor))
                else:
                    # factor 미기록(보정 불가/미수행) → 정규화가 = raw (비교 fallback)
                    row["adjusted_price_krw_normalized"] = calc["adjusted_price_krw"]
            else:
                row["factory_price_krw"] = None
                row["adjusted_price_krw"] = None
                row["adjusted_price_krw_normalized"] = None
                row["vat_rate"] = None
                row["distribution_margin"] = None
                self._populate_daily_cost(row, dosing)

            row["dosage_strength_mg"] = self._extract_mg(row.get("dosage_strength", ""))
            row["dosing_schedule_label"] = dosing["schedule_label"] if dosing else None

            by_country.setdefault(country, []).append(row)

        # 제형 배정 lazy — formulation_key 미설정 priced 행이 있으면 1회 배정·영구 저장
        # (캐시-DB-first). 이미 전부 배정됐으면 LLM/재배정 skip(저장값 재사용).
        try:
            all_items = [it for items in by_country.values() for it in (items or [])]
            assignable = [it for it in all_items
                          if it.get("adjusted_price_krw") is not None or it.get("dosage_strength")]
            if assignable and any(not it.get("formulation_key") for it in assignable):
                self._assign_formulations_and_persist(query, all_items)
        except Exception as e:
            logger.warning("[Formulation] %s 캐시 배정 실패(원본 유지): %s", query, e)

        return by_country

    def get_formulation_groups(self, query: str, by_country: dict = None) -> dict:
        """대시보드 제형 탭용 — get_cached_results 를 formulation_key 로 그룹핑.

        반환: { formulation_key: {label, canonical_strength_mg, route, is_us_listed,
                source, countries: {country_code: row}} }.
        US canonical 먼저(강도 오름차순) → foreign_only/unmatched 뒤로 정렬은 호출측.
        by_country 미전달 시 get_cached_results 를 호출(중복 방지: 엔드포인트는 전달).
        """
        if by_country is None:
            by_country = self.get_cached_results(query)
        groups: dict[str, dict] = {}
        for rows in by_country.values():
            for row in rows or []:
                fk = row.get("formulation_key")
                if not fk:
                    fk = f"{(row.get('dosage_strength') or '기타')}|{row.get('form_type') or 'unknown'}"
                g = groups.get(fk)
                if g is None:
                    g = {
                        "formulation_key": fk,
                        "label": row.get("formulation_label") or row.get("dosage_strength") or fk,
                        "canonical_strength_mg": row.get("canonical_strength_mg"),
                        "route": row.get("route") or row.get("form_type"),
                        "is_us_listed": row.get("is_us_listed"),
                        "source": row.get("formulation_source"),
                        "countries": {},
                    }
                    groups[fk] = g
                country = row.get("country")
                prev = g["countries"].get(country)
                # 같은 (제형, 국가) 중복 시 priced 우선
                if prev is None or (prev.get("adjusted_price_krw") is None
                                    and row.get("adjusted_price_krw") is not None):
                    g["countries"][country] = row
        return groups


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
