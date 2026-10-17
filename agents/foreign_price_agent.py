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
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from agents.db import DrugPriceDB
from agents.exchange_rate import ExchangeRateFetcher, PriceCalculator
from agents.scrapers.base import ScraperConfigError, ScraperLoginError, load_credentials

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

# ── 제형별 용법 오버라이드 (2026-10-17 사용자 테스트) ─────────────────────────────
# foreign_drug_dosing 은 성분당 1행(대표 용법)이라 Keytruda SC 395mg/790mg 바이알에도 IV '200mg Q3W' 라벨·분모가
# 붙었다 (UK #1301/#1302 daily = 200/21 × adj/395 — SC 실제 용법 대비 ~2× 과소). 같은 성분이라도 제형(투여경로×
# 바이알 강도)마다 허가 용법이 다르면 여기서 그 제형의 용법으로 바꾼다. 매칭은 **form-aware per-unit mg**
# (`_extract_per_unit_mg`) 가 표의 강도와 같을 때만 — 농도(per-mL)나 텍스트 추정으로 매칭하지 않는다.
# 키: foreign_drug_dosing.ingredient_key (성분/브랜드 alias 둘 다). 출처: KEYTRUDA SC 허가 용법
# (EU SmPC / US Keytruda Qlex) — 395 mg 3주 간격 또는 790 mg 6주 간격 피하주사, 바이알 1개 = 1회 투여량.
_PEMBRO_SC = (
    {"route": "sc", "unit_mg": 395.0, "dose_per_cycle_mg": 395.0, "cycle_days": 21,
     "schedule_label": "395 mg Q3W SC"},
    {"route": "sc", "unit_mg": 790.0, "dose_per_cycle_mg": 790.0, "cycle_days": 42,
     "schedule_label": "790 mg Q6W SC"},
)
FORMULATION_DOSING_OVERRIDES: dict[str, tuple] = {
    "pembrolizumab": _PEMBRO_SC,
    "keytruda": _PEMBRO_SC,
}

# ── 라이브 검색 국가별 실패 분류 (P1.6 관측) ───────────────────────────────────
# search_all 이 국가별 예외를 삼키면 "로그인이 깨진 것" 이 화면에서 "제도상 비공개" 처럼 보이고 원장에 아무것도 남지 않는다.
# 분류는 원장 키(foreign_search:{국가}:{분류})와 화면 라벨 양쪽이 쓴다 — 라벨은 여기 한 곳(서버가 error_label 로 내려준다).
SCRAPE_ERROR_CLASSES = ("config", "login", "http_403", "dependency", "timeout", "parse", "other")
SCRAPE_ERROR_LABEL_KR = {
    "config": "설정 오류", "login": "로그인 오류", "http_403": "접근 차단(403)", "dependency": "의존성 오류",
    "timeout": "시간 초과", "parse": "파싱 오류", "other": "오류",
}
# 결과는 있지만 사람이 알아야 할 사실(경고) — 실패(errors)와 분리해 status 는 priced 로 두고 warnings 로 내린다.
#   stale_cache    : 캐시 갱신 실패로 구 파일을 썼다 (IT AIFA CSV TTL — "현 시점의 가격" 이 아닐 수 있다)
#   channel_failed : 다채널 국가(IT CSV+TNF, FR BDPM+Vidal)에서 한 채널이 실패했다 (message 에 분류 포함)
SCRAPE_WARNING_LABEL_KR = {"stale_cache": "캐시 만료(갱신 실패)", "channel_failed": "채널 일부 실패"}
_LOGIN_HINTS = ("login", "로그인", "credential", "자격증명", "password", "license limit", "please login")
_FORBIDDEN_HINTS = ("403", "forbidden", "cloudflare", "access denied", "차단")
_PARSE_HINTS = ("parse", "파싱", "decode", "unexpected token", "expecting value")


def classify_scrape_error(exc: BaseException) -> str:
    """예외 → SCRAPE_ERROR_CLASSES 중 하나. 타입(ScraperConfigError·ScraperLoginError·ImportError·Timeout)이 먼저, 그다음 메시지 힌트."""
    if isinstance(exc, ScraperConfigError):
        return "config"
    if isinstance(exc, ScraperLoginError):
        return "login"
    if isinstance(exc, ImportError):
        return "dependency"
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or "timeout" in name or "timed out" in msg or "timeout" in msg:
        return "timeout"
    if any(h in msg for h in _LOGIN_HINTS):
        return "login"
    if any(h in msg for h in _FORBIDDEN_HINTS):
        return "http_403"
    if isinstance(exc, (json.JSONDecodeError, KeyError, ValueError)) or any(h in msg for h in _PARSE_HINTS):
        return "parse"
    return "other"


class SearchResults(dict):
    """search_all 의 반환 — {country: [rows]} 그대로에 국가별 실패 분류를 .errors 로 얹는다(dict 호환).

    errors[country] = {"error_class": str, "message": str}. 서버(_compute_country_results)가 읽어 status=scraper_error 로 내린다.
    warnings[country] = [{"warning_class", "channel", "message"}, ...] — 결과는 있으나 알아야 할 사실(stale_cache 등).
    서버는 status 를 바꾸지 않고 country_results[cc].warnings 로 내린다."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.errors: dict[str, dict] = {}
        self.warnings: dict[str, list[dict]] = {}


def _record_search_failure(query: str, country: str, error_class: str, exc: BaseException) -> None:
    """국가별 수집 실패 → 원장 ops_incident 1건/(국가, 분류). 같은 실패가 이어지면 같은 행(recurred/reopen — record_recurring).

    신호 경계(집안 관례 `_agent_record_*`): 어떤 예외도 밖으로 내보내지 않는다 — 신호가 사용자의 검색을 깨면 안 된다."""
    try:
        from agents.agent_tasks.jobs import resolve_db_path
        from agents.agent_tasks.signals import record_recurring, runbook_line
        label = SCRAPE_ERROR_LABEL_KR.get(error_class, error_class)
        record_recurring(
            kind="ops_incident", source="foreign_search", source_ref=f"{country}:{error_class}",
            title=f"해외약가 수집 실패 — {country} ({label})",
            detail=(f"검색어: {query}\n분류: {error_class}\n오류: {type(exc).__name__}: {str(exc)[:300]}\n"
                    f"{runbook_line(None)} — 로그인·의존성은 foreign_config_probe 결과(config/.env 키 이름·scrapling)를, "
                    f"403·시간 초과는 프록시(PROXY_ENV_KEYS)를 확인한 뒤 해외약가 화면에서 그 국가를 재검색한다"),
            priority=2, actor="foreign_search", db_path=resolve_db_path())
    except Exception as signal_err:
        logger.warning("[%s] 수집 실패 신호 기록 실패: %s", country, signal_err)


def _record_search_warning(query: str, country: str, warning: dict) -> None:
    """국가별 수집 경고(결과는 있음) → 원장 ops_incident 1건/(국가, 분류) — 실패와 같은 키 체계(foreign_search:{국가}:{분류}).

    stale_cache 가 매일 이어지면 같은 행이 recurred 로 쌓여 "구 파일로 버티는 중" 이 원장에서 보인다. 신호 경계 — 예외 삼킴."""
    try:
        from agents.agent_tasks.jobs import resolve_db_path
        from agents.agent_tasks.signals import record_recurring, runbook_line
        wcls = str(warning.get("warning_class") or "other")
        label = SCRAPE_WARNING_LABEL_KR.get(wcls, wcls)
        record_recurring(
            kind="ops_incident", source="foreign_search", source_ref=f"{country}:{wcls}",
            title=f"해외약가 수집 경고 — {country} ({label})",
            detail=(f"검색어: {query}\n분류: {wcls}\n채널: {warning.get('channel') or '-'}\n내용: {str(warning.get('message') or '')[:300]}\n"
                    f"{runbook_line(None)} — 결과는 구 캐시/남은 채널로 돌려줬다. 소스 접근(인증서·프록시)을 확인한 뒤 "
                    f"해외약가 화면에서 그 국가를 재검색하면 갱신을 다시 시도한다"),
            priority=3, actor="foreign_search", db_path=resolve_db_path())
    except Exception as signal_err:
        logger.warning("[%s] 수집 경고 신호 기록 실패: %s", country, signal_err)


def _item_extra(row: dict) -> dict:
    """행의 스크레이퍼 extra — 스크레이퍼 직후 item 은 `extra` dict, DB 행은 `raw_data` JSON 문자열."""
    extra = row.get("extra")
    if isinstance(extra, dict):
        return extra
    raw = row.get("raw_data")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def resolve_item_source_type(row: dict, class_default: Optional[str]) -> Optional[str]:
    """factory_ratio 를 정하는 source_type — **행의 가격 종류가 우선**, 선언이 없을 때만 스크레이퍼 클래스 기본.

    IT CSV 는 한 클래스(SOURCE_TYPE="aifa_exfactory")가 Class H(ex-factory, 1.0) 와 Class A(공중가 → IT 기본 0.93) 를 함께
    돌려준다. 2026-10 감사: 클래스 기본을 모든 행에 써서 Class A 행(sitagliptin·letermovir 경구) 이 +7.5% 과대였다.
    우선순위: ① item 최상위 `source_type` 키(명시적 None 포함) ② extra/raw_data 의 `source_type` 키(명시적 None 포함)
    ③ 클래스 기본. 키가 있는데 None 인 것과 키가 없는 것을 구분한다 — None 선언은 '국가 기본 공식' 이라는 뜻이다."""
    if "source_type" in row:
        return row["source_type"]
    extra = _item_extra(row)
    if "source_type" in extra:
        return extra["source_type"]
    return class_default


_IT_CSV_LABEL_BY_CLASS = {"A": "AIFA Lista Classe A (Prezzo al pubblico)", "H": "AIFA Lista Classe H (Prezzo Ex-factory)"}


def display_source_label(row: dict) -> Optional[str]:
    """읽기 시점 자료원 라벨 — IT CSV 행은 raw_data.aifa_class 가 진실(과거 수집은 Class A 도 "Classe H" 로 저장됐다).
    저장 행은 고치지 않고 화면/리포트에만 바로잡는다."""
    label = row.get("source_label")
    if label and "後生労働省" in label:
        # 2026-10 이전 JP 수집행은 오타 '後生労働省' 로 저장됐다 — 저장 행은 두고 표시만 厚生労働省 로
        label = label.replace("後生労働省", "厚生労働省")
    if row.get("country") == "IT":
        cls = _item_extra(row).get("aifa_class")
        if cls in _IT_CSV_LABEL_BY_CLASS:
            return _IT_CSV_LABEL_BY_CLASS[cls]
    return label


def a8_aggregate_eligibility(country: str, row: dict,
                              check_injection_unit: bool = True) -> tuple[bool, Optional[str]]:
    """A8 min/avg 집계(UI 요약·리포트) 포함 여부 — (eligible, exclude_reason).

    pack-pricing 국가의 oral/unknown 행이 pack_count_source == "default" 면 팩가가 per-unit 으로 저장된
    **미확인** 행이다 (2026-10 감사: sitagliptin per-tablet CH ₩47,326 · DE ₩31,335 vs FR ₩456). 그 숫자를 평균에
    섞으면 안 되므로 제외하고 사유를 남긴다. injection 의 default(1 vial) 는 규칙상 보수 fallback 이라 포함.
    per-unit 국가(JP/CA) 는 pack 개념이 없고, 출처 미기록(레거시 NULL) 은 아직 재결정 전이라 판단을 보류한다.
    """
    if row.get("adjusted_price_krw") is None:
        return False, "no_adjusted_price"
    src = row.get("pack_count_source")
    form = (row.get("form_type") or "unknown").lower()
    if country in PACK_PRICING_COUNTRIES and src == "default" and form != "injection":
        return False, "pack_count_unverified"
    # injection 바이알 단위가 출처 표기상 모호한 행(예: CA EAP Prevymis "20 mg/mL" + "Inj Sol-240 mL Vial Pk") 은
    # 어느 바이알(240 mg? 4,800 mg?) 의 가격인지 확정할 수 없어 평균에서 뺀다 (2026-10-17). 카드 숫자는 남는다.
    if check_injection_unit and form == "injection" and (
            row.get("unit_strength_ambiguous")
            or ForeignPriceAgent._injection_unit_ambiguity(
                row.get("dosage_strength") or "", row.get("package_unit") or "",
                product_name=row.get("product_name") or "", dosage_form=row.get("dosage_form") or "")):
        return False, "injection_unit_ambiguous"
    return True, None


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
        # 국가 검색 1회 = 세션 1개 = searched_at 1개. 채널(IT CSV+TNF, FR BDPM+Vidal)마다 run() 이 찍은 시각은 버린다 —
        # 80ms 차이로 compact_foreign_prices 가 '두 세션' 으로 보고 먼저 찍힌 채널을 지웠고(keytruda IT CSV 행 소실),
        # get_foreign_prices 도 한 채널만 노출했다(2026-10 감사). 압축·조회 SQL 은 그대로 두고 저장 전에 통일한다.
        session_searched_at = datetime.now().isoformat()

        # 1) 스크레이퍼 실행 (로그인 → 검색 → 로그아웃)
        scraper = self._build_scraper(country)
        # SOURCE_TYPE: IT=aifa_exfactory(1.0) 만 오버라이드. CH=compendium / FR=vidal / US=redbook_wac 은 오버라이드
        # 없음 → 국가 기본 ratio (CH 0.73, FR 0.77, US 0.74). tests/test_foreign_factory_ratio_effective.py 가 고정.
        source_type = getattr(scraper, "SOURCE_TYPE", None)
        # identity 있으면 brand 로 검색(대부분 소스가 브랜드 인덱스), 저장/집계는 slug 로 통일.
        # identity 가 없으면(동기 경로·백필 잡·CLI) alias 맵 정확 일치로 오프라인 유도 — INN(pembrolizumab) 이 그대로 가면
        # UK 는 Price 섹션 없는 /drugs/pembrolizumab/ 에, JP 는 0건에 닿았다(2026-10 감사). 네트워크(MFDS) 는 쓰지 않는다.
        ident = identity
        if not ident:
            try:
                from agents.drug_identity import derive_identity_offline
                ident = derive_identity_offline(query)
                if ident:
                    logger.info("[%s] identity 유도: '%s' → brand=%s slug=%s inn=%s", country, query,
                                ident.get("brand"), ident.get("slug"), ident.get("inn"))
            except Exception as e:                      # alias 조회 실패가 검색을 막으면 안 된다
                logger.warning("[%s] identity 유도 실패(원문으로 검색): %s", country, e)
                ident = None
        ident = ident or {}
        search_term = ident.get("brand") or query
        canonical_name = ident.get("slug") or query
        fallback_inn = ident.get("inn")
        channel_scrapers = [scraper]          # 경고 수집 대상 — 다채널 국가는 보조 채널 인스턴스도 넣는다
        # 다채널 국가의 채널 실패는 모아서 판정한다 (Item 6): 결과가 하나도 없으면 첫 실패를 예외로 올리고(search_all 이 분류 →
        # errors[cc] → 원장), 다른 채널 결과가 있으면 channel_failed 경고(분류 포함)로 같은 원장 키에 남긴다. 경고 로그로만
        # 삼키면 TNF/Vidal 도 0건일 때 "검색 결과 없음" → 화면 '제도상 비공개' 로 위장됐다(2026-10 감사).
        MULTI_CHANNEL = {"IT": "aifa_csv", "FR": "bdpm"}
        channel_errors: list[tuple[str, BaseException]] = []
        try:
            raw_results = await scraper.run(search_term)
        except Exception as e:
            if country in MULTI_CHANNEL:
                logger.warning("[%s] %s 채널 실패(보조 채널로 계속): %s", country, MULTI_CHANNEL[country], e)
                channel_errors.append((MULTI_CHANNEL[country], e))
                raw_results = []
            else:
                raise          # 단일 채널 국가는 그대로 전파(search_all 이 국가별로 catch)
        # brand 0건 & 스크레이퍼 opt-in & INN 존재 → INN 으로 1회 재시도
        if (not raw_results and getattr(scraper, "FALLBACK_TO_INN", False)
                and fallback_inn and fallback_inn.lower() != str(search_term).lower()):
            logger.info("[%s] brand 0건 → INN 폴백 검색: %s", country, fallback_inn)
            try:
                raw_results = await scraper.run(fallback_inn)
            except Exception as e:
                logger.warning("[%s] INN 폴백 실패: %s", country, e)

        # IT: TrovaNorme 결정문 채널을 기존 CSV 채널과 **병행** (둘 다 aifa_exfactory).
        # 결정문(determina)에서 성분·제형·ex-factory 가격을 직접 확인 — CSV 미등재/신규 협상 약제를 보완.
        if country == "IT":
            raw_results = list(raw_results or [])
            try:
                tnf = ItAifaTnfScraper(
                    cache_dir=self.foreign_data_dir / "it", msd_only=False,
                )
                channel_scrapers.append(tnf)
                tnf_rows = await tnf.run(search_term)
                if tnf_rows:
                    raw_results.extend(tnf_rows)
                    logger.info("[IT] TrovaNorme 채널 +%d건 (총 %d건)",
                                len(tnf_rows), len(raw_results))
            except Exception as e:
                logger.warning("[IT] TrovaNorme 채널 실패: %s", e)
                channel_errors.append(("tnf", e))

        # FR: Vidal 채널을 기존 BDPM 과 **병행** (둘 다 source_type=vidal → FR 기본 ratio 0.77).
        # 병원용 주사제(keytruda 등)는 BDPM 공시가가 없고 Vidal UCD(HT) 에만 공개 → 큐레이션 URL 맵으로 안정 조회.
        if country == "FR":
            raw_results = list(raw_results or [])
            try:
                vidal = FrVidalScraper(
                    cache_dir=self.foreign_data_dir / "fr", msd_only=False,
                )
                channel_scrapers.append(vidal)
                vidal_rows = await vidal.run(search_term)
                if vidal_rows:
                    raw_results.extend(vidal_rows)
                    logger.info("[FR] Vidal 채널 +%d건 (총 %d건)",
                                len(vidal_rows), len(raw_results))
            except Exception as e:
                logger.warning("[FR] Vidal 채널 실패: %s", e)
                channel_errors.append(("vidal", e))

        # 스크레이퍼 경고(stale_cache 등)는 결과 유무와 무관하게 search_all 로 올린다 — 빈 결과여도 "왜" 가 남아야 한다.
        warnings = [w for s in channel_scrapers for w in (getattr(s, "warnings", None) or [])]
        if channel_errors:
            if not raw_results:
                self._note_country_warnings(country, warnings)
                raise channel_errors[0][1]          # 전 채널이 비었고 실패가 있다 — 빈 결과가 아니라 수집 실패
            for ch, e in channel_errors:
                cls = classify_scrape_error(e)
                warnings.append({"warning_class": "channel_failed", "channel": ch, "error_class": cls, "exc": e,
                                 "message": f"{ch} 채널 실패({cls}: {type(e).__name__}: {str(e)[:160]}) — 나머지 채널 결과만 반환"})
        self._note_country_warnings(country, warnings)

        if not raw_results:
            logger.info("[%s] 검색 결과 없음", country)
            return []

        # 브랜드/성분 어느 입력이든 같은 캐시 레코드로 집계 → query_name = canonical slug.
        # searched_at 은 세션 값으로 통일 (채널별 시각 폐기 — 위 주석).
        for item in raw_results:
            item["query_name"] = canonical_name
            item["searched_at"] = session_searched_at

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

        # dosing 조회 (pack_count fallback + daily_cost 계산용) — identity 의 INN 도 키에 넣는다 (JP 처럼 성분명이 일본어인 소스)
        ingredients = [r.get("ingredient", "") for r in raw_results] + ([fallback_inn] if fallback_inn else [])
        dosing_map = self._load_dosing_map(canonical_name, ingredients)

        # 3) 조정가 계산 + DB 저장
        saved = []
        for item in raw_results:
            dosing = self._match_dosing(canonical_name, item.get("ingredient", ""), dosing_map)
            if not dosing and fallback_inn:
                dosing = dosing_map.get(fallback_inn.strip().lower())
            pack_count = self._resolve_pack_count(country, item, dosing)

            # 행의 가격 종류가 우선 (IT CSV Class A 공중가 → 국가 기본 0.93, Class H/TNF ex-factory → 1.0)
            item_source_type = resolve_item_source_type(item, source_type)
            if rate_info and item.get("local_price") is not None:
                calc = self.calculator.calculate_adjusted_price(
                    country=country,
                    listed_price=item["local_price"],
                    exchange_rate=rate_info["rate"],
                    pack_count=pack_count,
                    source_type=item_source_type,
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
                    "source_type": item_source_type,
                })

            # Daily cost 계산 (per-unit adj × (daily_dose / single_unit_mg))
            self._populate_daily_cost(item, dosing)

            row_id = self.db.save_foreign_price(item)
            item["id"] = row_id
            saved.append(item)

        logger.info("[%s] DB 저장 완료: %d건", country, len(saved))
        return saved

    # ── 국가별 수집 경고 전달 (search_one_country → search_all) ───────────────────────────────────
    # search_one_country 의 반환형(list)을 바꾸지 않으려고 인스턴스에 잠시 둔다. 테스트가 __new__ 로 만든 에이전트도 있어
    # __init__ 에 의존하지 않는다.

    def _note_country_warnings(self, country: str, warnings: list[dict]) -> None:
        store = getattr(self, "_country_warnings", None)
        if store is None:
            store = self._country_warnings = {}
        if warnings:
            store[country] = list(warnings)
        else:
            store.pop(country, None)

    def _pop_country_warnings(self, country: str) -> list[dict]:
        store = getattr(self, "_country_warnings", None) or {}
        return store.pop(country, []) or []

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
        # 제형별 용법 — SC 395/790mg 바이알에 IV 200mg Q3W 를 쓰지 않는다. 라벨도 분모와 같은 용법에서 나온다.
        dosing = self._formulation_dosing(dosing, item, form_type, unit_mg)
        item["dosing_schedule_label"] = (dosing or {}).get("schedule_label") or None
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
            ambiguity = self._injection_unit_ambiguity(
                item.get("dosage_strength") or "", item.get("package_unit") or "",
                product_name=item.get("product_name") or "", dosage_form=item.get("dosage_form") or "")
            if ambiguity:
                # 부피가 바이알로 비현실적 — '모름' 과 구분해 기록 (A8 집계 제외 사유와 짝)
                item["unit_strength_ambiguous"] = ambiguity
                item["daily_cost_note"] = "unit_mg_ambiguous"
            elif item.get("daily_cost_note") != "suspicious_outlier":
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
        반환: {country: [results, ...], ...} (SearchResults — 실패 국가는 [] + .errors[country]={error_class, message})
        """
        targets = countries or AVAILABLE_COUNTRIES
        results = SearchResults()
        for country in targets:
            try:
                results[country] = await self.search_one_country(
                    query, country, reference_date, identity=identity
                )
            except Exception as e:
                # 빈 결과와 실패를 분리한다 — 사용자에게는 [] 이지만 분류를 .errors 에 남기고 원장에 기록(신호 경계가 삼킨다)
                error_class = classify_scrape_error(e)
                logger.error("[%s] 조회 실패(%s): %s", country, error_class, e)
                results[country] = []
                results.errors[country] = {"error_class": error_class, "message": f"{type(e).__name__}: {e}"[:300]}
                _record_search_failure(query, country, error_class, e)
            # 결과는 있으나 알아야 할 사실(구 캐시 사용·채널 일부 실패) — 실패와 같은 원장 키 체계로 남기고 화면에 내린다
            warnings = self._pop_country_warnings(country)
            if warnings:
                results.warnings[country] = warnings
                for w in warnings:
                    logger.warning("[%s] 수집 경고(%s): %s", country, w.get("warning_class"), w.get("message"))
                    exc = w.pop("exc", None)      # 원장용 — 응답에는 싣지 않는다
                    if w.get("warning_class") == "channel_failed" and w.get("error_class"):
                        # 채널 일부 실패는 전체 실패와 **같은 분류 키**(foreign_search:{국가}:{분류})로 — 같은 조건, 같은 행
                        _record_search_failure(query, country, w["error_class"], exc or RuntimeError(w.get("message") or ""))
                    else:
                        _record_search_warning(query, country, w)

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
                # injection 은 form-aware per-vial 파싱이 권위 — 저장된 옛 값(예: IT Winrevair 50 = 용해 후 농도,
                # 150 = 50×"3" ml)이 남아 있어도 덮어쓴다. oral 은 기존처럼 저장값 우선(包↔錠 등 제형 레이어 산물 보존).
                if not it.get("unit_strength_mg") or (it.get("form_type") or "").lower() == "injection":
                    it["unit_strength_mg"] = derived
            elif (it.get("form_type") or "").lower() == "injection" and (
                    it.get("dosage_strength") or it.get("product_name")):
                # 농도만 있고 volume 불명 — 저장된 값(예: 25 = per-mL 농도)을 강도로 쓰면 보정계수가 4× 틀어진다.
                it["unit_strength_mg"] = None
                it["unit_strength_unresolved"] = True
                amb = self._injection_unit_ambiguity(
                    it.get("dosage_strength") or "", it.get("package_unit") or "",
                    product_name=it.get("product_name") or "", dosage_form=it.get("dosage_form") or "")
                if amb:
                    # 바이알 부피 모호 — 제형 레이어가 농도 remap(20 mg/mL→240mg) 으로 억지로 묶지 않게 표시
                    it["unit_strength_ambiguous"] = amb
            elif not it.get("unit_strength_mg"):
                it["unit_strength_mg"] = None
        self._inherit_continuation_strength(all_items)
        updates = process_formulations(query, all_items)
        for u in updates:
            if u.get("id"):
                self.db.update_foreign_dose_norm(u["id"], u)
        if updates:
            nkeys = len({u.get("formulation_key") for u in updates})
            logger.info("[Formulation] %s: %d행 → %d제형 배정", query, len(updates), nkeys)

    # 스크레이퍼 분할 잔여 "연속 조각" — UK MIMS 가격줄 "45mg powder …; 1-vial kit=£x; 2-vial kit=£y" 를 "=£" 로 나눌 때
    # 두 번째 행 dosage_strength 가 "; 2-vial kit" 처럼 강도 없이 구분자로 시작한다. 강도가 없어 'unmatched' 탭
    # "; 2-vial kit (주사)" 가 생겼다(2026-10-17 Winrevair). 같은 출처 페이지·같은 수집 배치의 바로 앞 행 강도를 잇는다.
    _CONTINUATION_FRAGMENT = re.compile(r"^\s*[;,:+&]")

    @classmethod
    def _inherit_continuation_strength(cls, items: list) -> int:
        """강도 없는 '연속 조각' 행에 직전 형제 행의 unit_strength_mg/form_type 을 잇는다. 이은 행 수 반환.

        형제 = 같은 (country, product_name, source_url, searched_at). 순서 = id(있으면) → 입력 순서(스크레이퍼
        파싱 순서). 조각에 mg 숫자가 있거나 직전 형제가 강도 불명이면 잇지 않는다(추정 금지).
        근본 수정은 uk_mims._parse_price_page(가격줄 분할) — 이 보정은 저장된 조각 행을 위한 방어선.
        """
        groups: dict = {}
        for idx, it in enumerate(items):
            key = (it.get("country"), it.get("product_name") or "", it.get("source_url") or "",
                   it.get("searched_at") or "")
            groups.setdefault(key, []).append((it.get("id") or 0, idx, it))
        n = 0
        for members in groups.values():
            members.sort(key=lambda t: (t[0], t[1]))
            prev = None
            for _, _, it in members:
                ds = it.get("dosage_strength") or ""
                is_fragment = bool(cls._CONTINUATION_FRAGMENT.match(ds)) and cls._extract_mg(ds) is None
                if is_fragment and prev is not None and prev.get("unit_strength_mg"):
                    it["unit_strength_mg"] = prev["unit_strength_mg"]
                    it.pop("unit_strength_unresolved", None)
                    if (it.get("form_type") or "unknown").lower() == "unknown" and prev.get("form_type"):
                        it["form_type"] = prev["form_type"]
                    it["strength_inherited_from"] = prev.get("id") or prev.get("dosage_strength")
                    n += 1
                    continue        # 조각은 다음 조각의 기준이 되지 않는다 — 첫 비조각 행이 기준
                if not is_fragment:
                    prev = it
        return n

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
            if m and cls._num(m.group(2)) > 1.0:
                return float(m.group(1))
        # 2) 농도 (N mg/ml, N mg/1 ml) 가 어디든 있으면 N 은 per-mL — volume 이 있어야 per-vial
        conc = None
        for text in (s, *ctx):
            m = cls._MG_PER_ML.search(text)
            if m:
                conc = float(m.group(1))
                break
        if conc is not None:
            # 2a) 명시 바이알 총량 "N mg"(농도 아님) 이 있으면 그것이 라벨상의 per-vial 함량이다 (2026-10-17 감사).
            #     - 분말+용제 키트: IT "WINREVAIR*1 flaconcino SC polv 45 mg + 1 sir preriempita solv 1 ml 50 mg/ml"
            #       → 50 mg/ml 은 **용해 후 농도**, 1 ml/1,3 ml 은 **용제 시린지 부피** — 농도×부피(50·150) 가 아니라 45.
            #     - 용액 바이알: IT TNF "395 mg - … flaconcino 2,4 ml (165 mg/ml)" → 165×2.4=396 ≈ 395 → 라벨 395.
            #     명시값이 농도와 같으면(FR "25 mg" + "25 mg/mL") 그 숫자는 농도이므로 2b 로 간다.
            explicit = cls._explicit_vial_mg(conc, s, *ctx)
            vol = cls._find_volume_ml(s, p, *ctx)
            if explicit is not None:
                if cls._is_powder_kit(s, p, *ctx):
                    return explicit
                if vol and abs(conc * vol - explicit) / explicit <= 0.05:
                    return explicit
            # 2b) 농도 × 바이알 부피. 바이알인데 부피가 비현실적(>100 mL)이면 출처 표기가 모호하다 —
            #     CA Ontario EAP "20 mg/mL" + "Inj Sol-240 mL Vial Pk" 는 20×240=4,800 mg 이 되지만 실제 Prevymis
            #     IV 바이알은 240 mg/12 mL·480 mg/24 mL 다("240 mL" 은 240 mg 의 오기로 보이나 추정 금지). → None.
            if vol and cls._injection_unit_ambiguity(s, p, product_name or "", dosage_form or ""):
                return None
            return conc * vol if vol else None
        # 3) 농도 표기 없음 → bare "N mg" 는 per-vial 총량
        return cls._extract_mg(s) if s else None

    # 분말(동결건조)+용제 키트 표지 — 이 경우 "N mg/ml" 은 용해 후 농도이고 "V ml" 은 용제 부피라 곱하면 안 된다.
    _POWDER_KIT = re.compile(
        r"(?i)(?:\bpolv\w*|\bpowder|\bpoudre|\bpulver|\blyoph\w*|\blyophil\w*|\bliofilizz\w*|\bpd\s+for\s+sol"
        r"|\btrockensub\w*|\bsolvent|\bsolvant|\bsolv\b|l[öo]sungsmittel)"
    )
    # 명시 per-vial 총량 후보 "N mg" — 뒤에 "/ml"·"/N ml" 이 붙은 농도 표기는 제외 ("45 mg/Vial"·"240 mg/-480 mg" 은 총량).
    _BARE_MG = re.compile(
        r"(?<![\d.,])(\d+(?:[.,]\d+)?)\s*mg(?!\s*/\s*(?:\d+(?:[.,]\d+)?\s*)?m[lL]\b)", re.IGNORECASE)
    # 바이알류 용기 표지 — 부피 타당성(>100 mL) 검사는 바이알에만 적용 (수액 bag 은 250 mL 등이 정상).
    _VIAL_WORDS = re.compile(
        r"(?i)(?:vials?\b|\bflacon\w*|\bflaconcin\w*|\bfiala|\bfiale|\bampoule\w*|\bampul\w*|durchstechfl\w*"
        r"|瓶|バイアル|바이알)"
    )
    MAX_PLAUSIBLE_VIAL_ML = 100.0

    @staticmethod
    def _num(s: str) -> float:
        """"1,3" / "2.4" → float (소수점 쉼표 허용 — IT/FR/DE 표기)."""
        return float(s.replace(",", "."))

    @classmethod
    def _is_powder_kit(cls, *texts: str) -> bool:
        return any(t and cls._POWDER_KIT.search(t) for t in texts)

    @classmethod
    def _explicit_vial_mg(cls, conc: Optional[float], *texts: str) -> Optional[float]:
        """농도 표기가 아닌 첫 "N mg" (강도 문자열 우선). 농도 값과 같은 숫자는 농도로 보고 건너뛴다."""
        for t in texts:
            if not t:
                continue
            for m in cls._BARE_MG.finditer(t):
                v = cls._num(m.group(1))
                if v <= 0 or (conc is not None and abs(v - conc) < 1e-9):
                    continue
                return v
        return None

    @classmethod
    def _injection_unit_ambiguity(cls, dosage_strength: str, package_unit: str = "",
                                  product_name: str = "", dosage_form: str = "") -> Optional[str]:
        """injection 행의 per-vial mg 가 **출처 표기상 모호**하면 사유 문자열, 아니면 None.

        현재 규칙: 농도(N mg/ml) × 바이알 부피에서 부피가 MAX_PLAUSIBLE_VIAL_ML(100 mL) 초과이고 용기가 바이알류.
        (CA Ontario EAP Prevymis "Inj Sol-240 mL Vial Pk"/"480 mL" — 실제 바이알 12/24 mL.) 명시 총량 "N mg/M ml" 이나
        명시 "N mg" 이 있으면 모호하지 않다. 호출측: per-vial mg=None + A8 집계 제외(`injection_unit_ambiguous`).
        """
        s = (dosage_strength or "").translate(cls._ZENKAKU)
        p = (package_unit or "").translate(cls._ZENKAKU)
        ctx = [t.translate(cls._ZENKAKU) for t in (dosage_form or "", product_name or "") if t]
        for text in (s, *ctx):
            m = cls._MG_PER_VOL.search(text)
            if m and cls._num(m.group(2)) > 1.0:
                return None
        conc = None
        for text in (s, *ctx):
            m = cls._MG_PER_ML.search(text)
            if m:
                conc = float(m.group(1))
                break
        if conc is None or cls._explicit_vial_mg(conc, s, *ctx) is not None:
            return None
        vol = cls._find_volume_ml(s, p, *ctx)
        if vol and vol > cls.MAX_PLAUSIBLE_VIAL_ML and any(t and cls._VIAL_WORDS.search(t) for t in (s, p, *ctx)):
            return (f"vial_volume_implausible: {conc:g} mg/mL × {vol:g} mL = {conc * vol:g} mg — "
                    f"출처 부피 표기가 바이알 크기로 비현실적(>{cls.MAX_PLAUSIBLE_VIAL_ML:g} mL), per-vial mg 추정 안 함")
        return None

    # 부피 숫자는 소수점 쉼표("1,3 ml"·"2,4 ml")를 허용한다 — 예전 정규식은 "1,3 ml" 의 "3 ml" 만 읽어 3 mL 로
    # 오인했다(IT Winrevair 60 mg 키트 unit_strength 150 = 50 mg/ml × 3). 쉼표 뒤 1~2자리만 소수로 본다
    # ("1,000 ml" 같은 천단위 쉼표는 소수로 읽지 않음 → 그 경우 매치 자체를 피하도록 lookbehind 에 쉼표 포함).
    _MG_PER_VOL = re.compile(r"(\d+(?:\.\d+)?)\s*mg\s*/\s*(\d+(?:\.\d+|,\d{1,2}(?!\d))?)\s*m[lL]\b", re.IGNORECASE)
    _MG_PER_ML = re.compile(r"(\d+(?:\.\d+)?)\s*mg\s*/\s*(?:1\s*)?m[lL]\b", re.IGNORECASE)
    _CONC_TOKEN = re.compile(r"mg\s*/\s*(?:\d+(?:[.,]\d+)?\s*)?m[lL]\b", re.IGNORECASE)
    _VOLUME_ML = re.compile(r"(?<![\d.,])(\d+(?:\.\d+|,\d{1,2}(?!\d))?)\s*m[lL]\b", re.IGNORECASE)

    @classmethod
    def _find_volume_ml(cls, *texts: str) -> Optional[float]:
        """바이알 부피 "V ml" — 농도 분모("mg/ml", "mg/1 ml") 는 지운 뒤 찾는다. 없으면 None."""
        for t in texts:
            if not t:
                continue
            m = cls._VOLUME_ML.search(cls._CONC_TOKEN.sub("mg/_", t))
            if m:
                v = cls._num(m.group(1))
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
            # "-?" — UK MIMS 키트 표기 "1-vial kit"·"2-vial kit" (2026-10-17: "; 2-vial kit" 행이 단서 없음→default 1 이라
            # 2바이알 팩가가 바이알가로 보일 수 있었다)
            (re.compile(rf"(?<![\d.,/])(\d{{1,4}})\s*-?\s*{_INJ_UNIT_WORDS}(?![a-zA-Zäöü])", re.I), "one"),             # "2 flaconcini", "1 flacon(s)", "1瓶", "2-vial kit"
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
        mg·ml 숫자(강도·농도·부피) 는 수량이 아니다: injection 에서 '총 mg ÷ 단위 mg' 비율로 수량을
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

    @staticmethod
    def _formulation_dosing(dosing: Optional[dict], item: dict, form_type: str,
                            unit_mg: Optional[float]) -> Optional[dict]:
        """성분 대표 dosing → 이 행의 제형 용법. FORMULATION_DOSING_OVERRIDES 에 같은 성분 키가 있고
        injection 행의 per-unit mg 가 표의 강도와 일치하면 그 용법(용량·주기·라벨)으로 교체한 사본을 돌려준다.
        일치하지 않으면 원본 그대로 (IV 100mg 바이알은 200mg Q3W 유지). 원본 dict 는 변경하지 않는다."""
        if not dosing or form_type != "injection" or not unit_mg:
            return dosing
        key = str(dosing.get("ingredient_key") or "").strip().lower()
        rules = FORMULATION_DOSING_OVERRIDES.get(key)
        if not rules:
            return dosing
        for rule in rules:
            if abs(float(unit_mg) - rule["unit_mg"]) <= 0.5:
                out = dict(dosing)
                out.update({
                    "dose_per_cycle_mg": rule["dose_per_cycle_mg"],
                    "cycle_days": rule["cycle_days"],
                    "schedule_label": rule["schedule_label"],
                    "route": rule["route"],
                    "formulation_override": True,
                })
                return out
        return dosing

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
            row["source_label"] = display_source_label(row)      # IT CSV Class A 는 저장 라벨이 "Classe H" 로 굳어 있다

            if row.get("local_price") is not None and row.get("exchange_rate"):
                if country not in scraper_cache:
                    try:
                        scr = self._build_scraper(country) if country in AVAILABLE_COUNTRIES else None
                        scraper_cache[country] = getattr(scr, "SOURCE_TYPE", None) if scr else None
                    except Exception:
                        scraper_cache[country] = None
                # 행의 가격 종류가 우선 — 저장 행은 다시 쓰지 않고 읽기-재계산이 바로잡는다 (IT Class A 공중가 → 0.93)
                src_type = resolve_item_source_type(row, scraper_cache[country])
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
            # _populate_daily_cost 가 제형별 용법(SC 395/790mg 등)으로 라벨을 이미 채웠다 — 성분 대표 라벨로 덮지 않는다
            if "dosing_schedule_label" not in row:
                row["dosing_schedule_label"] = dosing["schedule_label"] if dosing else None
            # A8 집계 안전망 — 팩가가 per-unit 으로 저장된 미확인 행(pack_count_source=default) 은 평균에서 뺀다
            row["a8_aggregate_eligible"], row["a8_exclude_reason"] = a8_aggregate_eligibility(country, row)

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
