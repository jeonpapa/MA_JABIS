"""
해외 약가 스크레이퍼 베이스 클래스
- 모든 국가별 스크레이퍼는 이 클래스를 상속
- 로그인 → 검색 → 결과 파싱 → 로그아웃 인터페이스 정의
- Playwright async 기반
"""

import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime, date
from pathlib import Path

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

from .formulation import detect_form, normalize_form_type

logger = logging.getLogger(__name__)

# 국가 → config/.env 자격증명 키 이름 (load_credentials 와 설정 프로브(agents/agent_tasks/config_probe.py)가 함께 쓴다 — 이름만, 값 금지).
ENV_KEY_MAP: dict[str, tuple[str, str]] = {
    "UK": ("MIMS_UK_USERNAME",       "MIMS_UK_PASSWORD"),
    "US": ("MICROMEDEX_US_USERNAME", "MICROMEDEX_US_PASSWORD"),
    "DE": ("ROTE_LISTE_DE_USERNAME", "ROTE_LISTE_DE_PASSWORD"),
    "FR": ("VIDAL_FR_USERNAME",      "VIDAL_FR_PASSWORD"),       # Vidal 은 비로그인 모드로도 동작(LOGIN_OPTIONAL)
    "CH": ("COMPENDIUM_CH_USERNAME", "COMPENDIUM_CH_PASSWORD"),  # 공개 접근, 로그인 불필요
}


class ScraperConfigError(RuntimeError):
    """설정 문제로 수집을 시작할 수 없다 — 자격증명/프록시 키 미설정 등. 분류: config (foreign_config_probe 가 다음에 볼 곳).
    빈 결과로 돌려주면 화면이 '구독 필요' 정책 문구로 감싸 설정 문제가 보이지 않는다(P1.6) — 반드시 올린다."""


class ScraperLoginError(RuntimeError):
    """로그인했는데 로그인벽이 남았다/세션이 깨졌다 — 분류: login. 제도상 공백이 아니라 수집 실패다."""


class BaseScraper(ABC):
    """
    국가별 약가 스크레이퍼 추상 베이스 클래스.

    서브클래스는 다음을 구현해야 함:
      - COUNTRY: 국가코드 (US/UK/DE/FR/IT/CH/JP/CA)
      - CURRENCY: 통화코드 (USD/GBP/EUR/CHF/JPY/CAD)
      - SOURCE_LABEL: 자료원 명칭 (예: Redbook, MIMS)
      - REQUIRES_LOGIN: 로그인 필요 여부
      - search(query, page): 검색 실행 → 결과 리스트 반환
      - login(page): 로그인 (REQUIRES_LOGIN=True인 경우)
      - logout(page): 로그아웃 (REQUIRES_LOGIN=True인 경우)
    """

    COUNTRY: str = ""
    CURRENCY: str = ""
    SOURCE_LABEL: str = ""
    REQUIRES_LOGIN: bool = False
    # REQUIRES_LOGIN 이어도 자격증명 없이 동작하는 소스(FR Vidal 비로그인 모드) — 설정 프로브가 자격증명 부재를 문제로 보지 않는다.
    LOGIN_OPTIONAL: bool = False
    # 이 소스가 읽는 프록시 env 키(대안 순서). 비면 직접 접근. 설정 프로브는 하나라도 있으면 충족으로 본다.
    PROXY_ENV_KEYS: tuple[str, ...] = ()
    # brand 검색이 0건일 때 identity.inn 으로 1회 재시도할지 (기본 off).
    # 소스의 검색 모드가 제품명 위주여서 INN 입력을 놓치는 경우만 opt-in.
    FALLBACK_TO_INN = False

    def __init__(self, credentials: dict = None, headless: bool = True):
        """
        credentials: {username, password} 딕셔너리
        headless: 브라우저 headless 모드 여부 (디버깅 시 False)
        """
        self.credentials = credentials or {}
        self.headless = headless
        self._browser: Browser = None
        self._context: BrowserContext = None
        # 결과는 돌려줬지만 사람이 알아야 할 사실 — {"warning_class": str, "channel": str, "message": str}.
        # 예: 캐시 갱신 실패로 구 파일을 썼다(stale_cache). 에이전트(search_one_country)가 읽어 SearchResults.warnings 와
        # 원장(foreign_search:{국가}:{분류})으로 올린다. 조용히 로그에만 남기지 않는다(P1.6 수집 가능성).
        self.warnings: list[dict] = []

    # ──────────────────────────────────────────────────────────────────────
    # 서브클래스에서 반드시 구현해야 하는 메서드
    # ──────────────────────────────────────────────────────────────────────

    @abstractmethod
    async def search(self, query: str, page: Page) -> list[dict]:
        """
        약제명으로 검색해 결과 리스트 반환.

        반환 형식 (각 항목):
        {
            "product_name": str,       # 해당국 제품명
            "ingredient": str,         # 성분명
            "dosage_strength": str,    # 함량
            "dosage_form": str,        # 제형
            "package_unit": str,       # 포장단위
            "local_price": float,      # 현지 가격 (해당국 통화)
            "source_url": str,         # 자료 출처 URL
            "extra": dict,             # 기타 원본 데이터
        }
        """
        raise NotImplementedError

    async def login(self, page: Page) -> None:
        """로그인 (REQUIRES_LOGIN=True인 서브클래스에서 구현)."""
        pass

    async def logout(self, page: Page) -> None:
        """로그아웃 (REQUIRES_LOGIN=True인 서브클래스에서 구현)."""
        pass

    # ──────────────────────────────────────────────────────────────────────
    # 제형 판정 — 스크레이퍼가 item["form_type"] 를 직접 넣어줬으면 그대로,
    # 아니면 제품명·제형·raw_data 를 모아 detect_form() 으로 추론.
    # ──────────────────────────────────────────────────────────────────────

    def _resolve_form_type(self, item: dict) -> str:
        explicit = item.get("form_type")
        if explicit:
            return normalize_form_type(explicit)
        extra = item.get("extra") or {}
        extra_blob = " ".join(
            str(v) for v in extra.values() if isinstance(v, (str, int, float))
        )
        result = detect_form(
            extra_blob,
            item.get("package_unit") or "",
            item.get("dosage_strength") or "",
            dosage_form=item.get("dosage_form"),
            product_name=item.get("product_name"),
        )
        return result["form_type"]

    def _browser_proxy(self):
        """Playwright context 에 적용할 proxy dict(또는 None).

        기본 None(직접 접근). residential proxy 가 필요한 스크레이퍼(JP 등)만 오버라이드.
        """
        return None

    # ──────────────────────────────────────────────────────────────────────
    # 공통 실행 메서드 (서브클래스에서 오버라이드 불필요)
    # ──────────────────────────────────────────────────────────────────────

    async def run(self, query: str) -> list[dict]:
        """
        로그인 → 검색 → 로그아웃 전체 파이프라인 실행.
        결과는 DB 저장 형식의 dict 리스트로 반환.
        """
        logger.info("[%s] 검색 시작: '%s'", self.COUNTRY, query)
        results = []

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self.headless)
            ctx_kw = dict(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                accept_downloads=True,
            )
            # 서브클래스가 residential proxy 를 요구하면 context 에 주입(기본 None).
            bproxy = self._browser_proxy()
            if bproxy:
                ctx_kw["proxy"] = bproxy
                logger.info("[%s] 브라우저 proxy 사용 (데이터센터 IP 우회)", self.COUNTRY)
            context = await browser.new_context(**ctx_kw)
            page = await context.new_page()

            try:
                if self.REQUIRES_LOGIN:
                    logger.info("[%s] 로그인 중...", self.COUNTRY)
                    await self.login(page)
                    logger.info("[%s] 로그인 완료", self.COUNTRY)

                raw_results = await self.search(query, page)
                logger.info("[%s] 검색 결과: %d건", self.COUNTRY, len(raw_results))

                # 결과를 DB 저장 형식으로 변환
                searched_at = datetime.now().isoformat()
                for item in raw_results:
                    form_type = self._resolve_form_type(item)
                    rec = {
                        "searched_at": searched_at,
                        "query_name": query,
                        "country": self.COUNTRY,
                        "product_name": item.get("product_name"),
                        "ingredient": item.get("ingredient"),
                        "dosage_strength": item.get("dosage_strength"),
                        "dosage_form": item.get("dosage_form"),
                        "package_unit": item.get("package_unit"),
                        "local_price": item.get("local_price"),
                        "currency": self.CURRENCY,
                        "exchange_rate": None,        # ForeignPriceAgent에서 채움
                        "exchange_rate_from": None,
                        "exchange_rate_to": None,
                        "factory_price_krw": None,    # ForeignPriceAgent에서 채움
                        "vat_rate": None,
                        "distribution_margin": None,
                        "adjusted_price_krw": None,
                        "source_url": item.get("source_url", ""),
                        # 행이 자기 자료원/가격 종류를 선언하면 그것이 우선 (IT CSV Class A = 공중가, Class H = ex-factory).
                        # 클래스 기본은 선언 없는 소스의 fallback. factory_ratio 는 에이전트가 resolve_item_source_type 로 정한다.
                        "source_label": item.get("source_label") or self.SOURCE_LABEL,
                        "raw_data": json.dumps(item.get("extra", {}), ensure_ascii=False),
                        "form_type": form_type,
                    }
                    if "source_type" in item:
                        rec["source_type"] = item["source_type"]    # DB 컬럼은 아니다 — save 는 무시, 에이전트가 읽는다
                    results.append(rec)

            except Exception as e:
                logger.error("[%s] 검색 중 오류: %s", self.COUNTRY, e, exc_info=True)
                raise
            finally:
                if self.REQUIRES_LOGIN:
                    try:
                        logger.info("[%s] 로그아웃 중...", self.COUNTRY)
                        await self.logout(page)
                        logger.info("[%s] 로그아웃 완료", self.COUNTRY)
                    except Exception as e:
                        logger.warning("[%s] 로그아웃 실패: %s", self.COUNTRY, e)
                await context.close()
                await browser.close()

        return results


def load_credentials(config_path: Path, country: str) -> dict:
    """
    자격증명 로드 우선순위:
    1) config/.env 파일 (로컬 보안 저장)
    2) config/foreign_credentials.json (fallback)

    .env 키 규칙:
      UK  → MIMS_UK_USERNAME / MIMS_UK_PASSWORD
      US  → MICROMEDEX_US_USERNAME / MICROMEDEX_US_PASSWORD
      DE  → ROTE_LISTE_DE_USERNAME / ROTE_LISTE_DE_PASSWORD
    """
    import os

    # 1) .env 로드 (python-dotenv가 설치된 경우)
    env_path = config_path.parent / ".env"
    if env_path.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_path, override=False)
        except ImportError:
            pass  # python-dotenv 미설치 시 os.environ만 사용

    if country in ENV_KEY_MAP:
        u_key, p_key = ENV_KEY_MAP[country]
        username = os.environ.get(u_key, "")
        password = os.environ.get(p_key, "")
        if username or password:
            return {"username": username, "password": password}

    # 2) foreign_credentials.json fallback
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            creds = json.load(f)
        return creds.get(country, {})

    return {}
