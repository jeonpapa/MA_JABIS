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
from agents.scrapers.fr_bdpm import FrBdpmScraper
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
        dosing_map = self._load_dosing_map(query, ingredients)

        # 3) 조정가 계산 + DB 저장
        saved = []
        for item in raw_results:
            dosing = self._match_dosing(query, item.get("ingredient", ""), dosing_map)
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
        """pack_count 결정 — **form_type 우선 원칙**.

        최소단위는 제형에 따라 다르다:
          - oral      → 최소단위 = 1 tablet/capsule
          - injection → 최소단위 = 1 vial/ampoule/syringe
          - unknown   → 1 로 fallback (per-unit 으로 안전 가정)

        우선순위:
          1) scraper 가 item 에 직접 제공 (pack_count > 0) — 가장 신뢰.
          2) **oral 전용**: dosage_strength × package_unit 에서 total/unit_mg ratio
             (예: 40mg tab × 90 pack → 3600/40 = 90). injection 은 total=conc×vol
             이라 이 ratio 가 pack count 가 아니라 volume → **금지**.
          3) pack-pricing 국가에서 dosing.default_pack_count fallback.
          4) 기본 1 (per-unit).
        """
        pc = item.get("pack_count")
        if isinstance(pc, (int, float)) and pc > 0:
            return int(pc)

        form_type = (item.get("form_type") or "unknown").lower()

        if form_type == "oral":
            total_mg = self._extract_total_pkg_mg(
                item.get("dosage_strength", "") or "",
                item.get("package_unit", "") or "",
            )
            unit_mg = self._extract_mg(item.get("dosage_strength", "") or "")
            if total_mg and unit_mg and unit_mg > 0:
                ratio = total_mg / unit_mg
                if ratio >= 1 and abs(ratio - round(ratio)) < 0.01:
                    inferred = int(round(ratio))
                    if inferred >= 2 or country in PER_UNIT_COUNTRIES:
                        return inferred

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
            return int(dosing["default_pack_count"])
        return 1

    def _populate_daily_cost(self, item: dict, dosing: dict) -> None:
        """item 에 daily_dose_mg / daily_cost_krw / total_pkg_mg / daily_cost_note 채움.

        공식: daily_cost_krw = daily_dose_mg × (adjusted_price_krw / unit_mg)
             단, unit_mg = 최소단위(tablet/vial) 당 mg — **form_type 으로 결정**.
             - oral      → per-tablet mg        (예: Welireg 40 mg/tab)
             - injection → per-vial total mg    (예: Keytruda 100 mg/vial, NOT 25 mg/mL)
             adjusted_price_krw 는 per-unit 이므로 분모도 per-unit 이어야 일관.
        """
        item.setdefault("daily_dose_mg", None)
        item.setdefault("daily_cost_krw", None)
        item.setdefault("daily_cost_note", None)
        item.setdefault("total_pkg_mg", None)

        adj = item.get("adjusted_price_krw")
        form_type = (item.get("form_type") or "unknown").lower()
        unit_mg = self._extract_per_unit_mg(
            form_type,
            item.get("dosage_strength", "") or "",
            item.get("package_unit", "") or "",
        )
        pack_count = item.get("pack_count") or 1
        # total_pkg_mg (참고용): oral 은 per-tab × pack, injection 은 per-vial × vial_count
        total_mg = (unit_mg * pack_count) if (unit_mg and pack_count) else None
        if total_mg:
            item["total_pkg_mg"] = round(total_mg, 2)

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
                             package_unit: str = "") -> Optional[float]:
        """최소단위(tablet/vial) 1개당 mg — **form_type 기반**.

        - oral: dosage_strength 의 "N mg" 값이 per-tablet. _extract_mg 와 동일.
        - injection: per-vial 총량. 농도(25 mg/mL) 단독이면 per-mL 이 아닌 per-vial
          이므로 package_unit 의 volume 과 곱해 per-vial 총량을 복원해야 함.
          _extract_total_pkg_mg 의 Step 1 (per_unit_mg 계산) 로직을 재사용.
        - unknown: oral 로 가정 (가장 보수적으로 per-tablet).
        """
        if not dosage_strength:
            return None
        if form_type == "oral" or form_type == "unknown":
            return cls._extract_mg(dosage_strength)
        # injection: per-vial total 복원
        trans = str.maketrans(
            "０１２３４５６７８９．ｍｇｋｍｌＭＧＫＬ",
            "0123456789.mgkmlMGKL",
        )
        s = dosage_strength.translate(trans)
        p = (package_unit or "").translate(trans)
        # "N mg/M ml" (M>1) → 그 바이알 총량 N mg
        m = re.search(r"(\d+(?:\.\d+)?)\s*mg\s*/\s*(\d+(?:\.\d+)?)\s*m[lL]", s, re.IGNORECASE)
        if m and float(m.group(2)) > 1.0:
            return float(m.group(1))
        # 농도 단독 (N mg/ml or N mg/1 ml) + 외부 volume 탐색
        m_conc = (
            m
            or re.search(r"(\d+(?:\.\d+)?)\s*mg\s*/\s*m[lL]", s, re.IGNORECASE)
        )
        if m_conc:
            conc = float(m_conc.group(1))
            m_vol = re.search(r"(\d+(?:\.\d+)?)\s*m[lL](?!\s*/)", p, re.IGNORECASE)
            if not m_vol:
                m_vol = re.search(r"(\d+(?:\.\d+)?)\s*m[lL](?!\s*/)", s, re.IGNORECASE)
            if m_vol:
                return conc * float(m_vol.group(1))
            # volume 미확보 — 농도만 있으면 per-vial 복원 불가. None 반환 (daily_cost 계산 skip)
            return None
        # fallback: 단독 mg 수치
        return cls._extract_mg(s)

    @staticmethod
    def _extract_total_pkg_mg(dosage_strength: str, package_unit: str = "") -> Optional[float]:
        """포장 단위당 총 mg 추출 — 일일 투약비용의 정확한 분모.

        핵심: price_per_mg = adj_price / **total_pkg_mg** (단위강도 X, 포장 총량 O)
        dosage_strength 에 단위강도만 있는 경우 (예: "40mg") 패키지 단위 수량을
        package_unit 또는 dosage_strength 꼬리에서 검색해 곱해준다.

        케이스:
          A) "100 mg/4 ml"           → 100  (바이알당 총 100mg)
          B) "25 mg/ml" + "4 ml"     → 25 × 4 = 100
          C) "40mg ... tab ... , 90" → 40 × 90 = 3600 (pack of 90)
          D) "40 mg" + "de 90 (3x30)"→ 40 × 90 = 3600
          E) "40mg 1錠" (JP MHLW)     → 40 (per-unit 가격 체계)
          F) "25 mg/ml" 단독 (DE Rote Liste) → 25 (volume 미제공 → concentration 만)

        반환: 총 mg, 실패 시 None.
        """
        if not dosage_strength:
            return None
        trans = str.maketrans(
            "０１２３４５６７８９．ｍｇｋｍｌＭＧＫＬ",
            "0123456789.mgkmlMGKL",
        )
        s = dosage_strength.translate(trans)
        p = (package_unit or "").translate(trans)
        combined = f"{s} | {p}".strip(" |")

        # Step 1: per-unit (vial/tablet) mg
        # "N mg/M ml" 에서 M>1: 그 바이알의 총량 = N mg (예: "100 mg/4 ml" = 100mg vial)
        # M=1: 농도 (25 mg per 1 ml) — package_unit 에서 별도 volume 필요
        m = re.search(r"(\d+(?:\.\d+)?)\s*mg\s*/\s*(\d+(?:\.\d+)?)\s*m[lL]", s, re.IGNORECASE)
        if m and float(m.group(2)) > 1.0:
            per_unit_mg = float(m.group(1))
        else:
            m_conc = (
                m  # "N mg/1 ml" 로 매칭된 경우 → 농도
                or re.search(r"(\d+(?:\.\d+)?)\s*mg\s*/\s*m[lL]", s, re.IGNORECASE)
            )
            if m_conc:
                conc = float(m_conc.group(1))
                # package_unit 또는 s 의 나머지에서 volume (mL) 탐색. 분모 mL 자기 자신 제외.
                m_vol = re.search(r"(\d+(?:\.\d+)?)\s*m[lL](?!\s*/)", p, re.IGNORECASE)
                if not m_vol:
                    m_vol = re.search(r"(\d+(?:\.\d+)?)\s*m[lL](?!\s*/)", s, re.IGNORECASE)
                per_unit_mg = conc * float(m_vol.group(1)) if m_vol else conc
            else:
                m_mg = re.search(r"(\d+(?:\.\d+)?)\s*mg", s, re.IGNORECASE)
                if m_mg:
                    per_unit_mg = float(m_mg.group(1))
                else:
                    m_mcg = re.search(r"(\d+(?:\.\d+)?)\s*mcg", s, re.IGNORECASE)
                    if m_mcg:
                        per_unit_mg = float(m_mcg.group(1)) / 1000.0
                    else:
                        return None

        unit_count = 1
        m_mul = re.search(r"\((\d+)\s*[x×]\s*(\d+)\)", combined)
        if m_mul:
            unit_count = int(m_mul.group(1)) * int(m_mul.group(2))
        else:
            patterns = [
                r"\bde\s+(\d+)\s",
                r"(\d+)\s*(?:compress?é|comprim|tablet|capsul|caps\b|tabs?\b|vial|flacon|瓶|錠|정)",
                r"(?:vial|tablet|comprim|caps|瓶|錠|정|flacon|tab)[^,]*?,\s*(\d+)\b",
                r"m[lL]\s+(\d+)s\b",         # US Redbook "4 ml 2s" → 2 바이알 (s 접미사 필수)
                r"\b(\d+)s\s+ea\b",          # US Redbook "90s ea" → 90 tablets each pack
                r",\s*(\d{1,3})\b\s*$",
            ]
            for pat in patterns:
                m_c = re.search(pat, combined, re.IGNORECASE)
                if m_c:
                    cnt = int(m_c.group(1))
                    if 2 <= cnt <= 500:
                        unit_count = cnt
                        break
        return per_unit_mg * unit_count

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
        rows = self.db.get_foreign_prices(query)
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
            else:
                row["factory_price_krw"] = None
                row["adjusted_price_krw"] = None
                row["vat_rate"] = None
                row["distribution_margin"] = None

            row["dosage_strength_mg"] = self._extract_mg(row.get("dosage_strength", ""))
            row["dosing_schedule_label"] = dosing["schedule_label"] if dosing else None

            # daily_cost 재계산 (규칙 변경 반영)
            self._populate_daily_cost(row, dosing)

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
