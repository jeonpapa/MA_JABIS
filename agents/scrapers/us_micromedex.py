"""
미국 Micromedex (Red Book) 약가 스크레이퍼.

대상: https://www.micromedexsolutions.com/  (Red Book — AWP/WAC pricing)
로그인: 필수 (MSDKOREA 계정, 동시접속 1명 제한 추정)

핵심 제약:
  - 동시접속 라이선스 한도가 매우 작음. "Maximum number of users currently
    logged in" System Status 페이지가 뜨면 세션 반환 실패 상태.
  - 따라서 반드시 모든 실행 경로에서 logout을 보장해야 함.

4중 로그아웃 안전장치 (본 파일 내에서 모두 구현):
  1) try/finally 블록 — 정상/예외 모두 logout
  2) signal 핸들러 (SIGINT/SIGTERM) — Ctrl+C 등에서 logout
  3) atexit 훅 — 인터프리터 종료 시 best-effort logout
  4) asyncio.Lock (클래스 레벨) — US 쿼리 직렬화, seat 1개 보장

검색 흐름 (2026-04-20 probe 확인):
  1) https://www.micromedexsolutions.com/ → /home/dispatch/ (login page)
  2) POST /home/dispatch/PFDefaultActionId/pf.LoginAction
     - login.username_index_0 / login.password_index_0 / Submit
  3) 성공 시 /micromedex2/librarian/ (Red Book search home)
     - "System Status" 타이틀 + maximum users 메시지면 실패
  4) 검색 UI: input#WordWheel_SearchTerm_index_0 + input#rbSubmitBtn
     ("결과보기" 버튼, JS: doRedBook_Search())
  5) 결과 페이지에서 NDC/Strength/Package/AWP/WAC 파싱
  6) Logout: header button[aria-label='Log out'] 또는 data-logout-url 직접 GET
"""
import asyncio
import atexit
import json
import logging
import re
import signal
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

from playwright.async_api import Page, async_playwright, Browser, BrowserContext

from .base import BaseScraper

logger = logging.getLogger(__name__)

BASE_URL       = "https://www.micromedexsolutions.com"
HOME_URL       = f"{BASE_URL}/"
DISPATCH_URL   = f"{BASE_URL}/home/dispatch/"
REDBOOK_URL    = (
    f"{BASE_URL}/micromedex2/librarian/PFActionId/"
    "redbook.FindRedBook?navitem=topRedBook&isToolPage=true"
)

SEL_LOGIN_USER   = "input#login\\.username_index_0"
SEL_LOGIN_PASS   = "input#login\\.password_index_0"
SEL_LOGIN_SUBMIT = "button#Submit"

SEL_RB_INPUT     = "input#WordWheel_SearchTerm_index_0"
SEL_RB_SUBMIT    = "input#rbSubmitBtn"

SEL_LOGOUT_BTN   = "button[aria-label='Log out']"

# System Status (license-limit) 페이지 감지 문구
LICENSE_LIMIT_PATTERNS = (
    "maximum number of users",
    "currently logged in with this license",
)


class UsMicromedexScraper(BaseScraper):
    COUNTRY        = "US"
    CURRENCY       = "USD"
    SOURCE_LABEL   = "Micromedex Red Book (WAC)"
    SOURCE_TYPE    = "redbook_wac"     # WAC 기준 (AWP 는 유통마크업 포함 — 재정영향분석과 불일치)
    REQUIRES_LOGIN = True

    PLAYWRIGHT_ARGS = [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
    ]

    # 동시접속 1 seat — 클래스 레벨 락으로 직렬화 (lazy init: 이벤트 루프에 바인딩)
    _seat_lock: Optional["asyncio.Lock"] = None

    @classmethod
    def _get_seat_lock(cls) -> "asyncio.Lock":
        if cls._seat_lock is None:
            cls._seat_lock = asyncio.Lock()
        return cls._seat_lock

    def __init__(
        self,
        cache_dir: Path = None,
        msd_only: bool = False,
        storage_state_path: Path = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.cache_dir = cache_dir or Path("data/foreign/us")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.msd_only = msd_only
        # storage_state 를 저장해 세션 재사용 (매 실행마다 로그인 seat 소비 방지)
        self.storage_state_path = (
            storage_state_path
            if storage_state_path is not None
            else self.cache_dir / "storage_state.json"
        )
        # 로그아웃 URL은 로그인 후 페이지에서 추출 (CS/DUPLICATIONSHIELDSYNC 포함)
        self._logout_url: Optional[str] = None
        # 비상 로그아웃을 위해 context/browser 보관
        self._ctx: Optional[BrowserContext] = None
        self._browser: Optional[Browser] = None
        self._page: Optional[Page] = None

    # ──────────────────────────────────────────────────────────────────────
    # 로그인
    # ──────────────────────────────────────────────────────────────────────

    async def login(self, page: Page) -> None:
        """
        홈 접속 → 로그인 폼 감지 시 자격증명 입력. 이미 로그인이면 스킵.

        성공 판정 규칙:
          - title 이 "Please Login" / "System Status" 이면 실패
          - body 에 license-limit 문구 있으면 실패
          - react-header-root 의 data-logout-url 있으면 성공 (확정 신호)
        """
        await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(3_000)

        # 이미 로그인된 세션이면 data-logout-url 이 있어야 함
        logout_url = None
        try:
            hdr = page.locator("#react-header-root").first
            if await hdr.count() > 0:
                logout_url = await hdr.get_attribute("data-logout-url")
        except Exception:
            pass

        has_login = await page.locator(SEL_LOGIN_USER).count() > 0

        if logout_url and not has_login:
            self._logout_url = (
                logout_url if logout_url.startswith("http") else urljoin(BASE_URL, logout_url)
            )
            logger.info("[US] 기존 세션 유효 — 로그인 스킵")
            return

        if not has_login:
            # 로그인 폼도 없고 logout URL 도 없는 이상 상태 → stale cookie
            logger.warning("[US] 예상치 못한 페이지 상태 — 쿠키 초기화 후 재시도 필요")
            raise RuntimeError(
                "[US] unexpected landing page (no login form, no logout URL) — "
                "stale storage_state. delete data/foreign/us/storage_state.json"
            )

        user = self.credentials.get("username", "")
        pw   = self.credentials.get("password", "")
        if not user or not pw:
            raise RuntimeError("[US] MICROMEDEX_US_USERNAME/PASSWORD 가 설정되지 않았습니다")

        logger.info("[US] 로그인 시도: %s", user)
        await page.fill(SEL_LOGIN_USER, user)
        await page.fill(SEL_LOGIN_PASS, pw)
        await page.click(SEL_LOGIN_SUBMIT)
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=30_000)
        except Exception:
            pass
        await page.wait_for_timeout(4_000)

        title = (await page.title()).lower()
        body  = (await page.inner_text("body")).lower()

        # System Status (license limit)
        if "system status" in title or any(p in body for p in LICENSE_LIMIT_PATTERNS):
            logger.error("[US] 라이선스 동시접속 한도 초과")
            raise RuntimeError(
                "Micromedex license limit reached: maximum concurrent users logged in"
            )

        # Please Login — 인증 실패 (credentials 또는 stale session)
        if "please login" in title:
            logger.error("[US] 로그인 실패 — 'Please Login' 페이지로 리턴됨 (credentials 확인 필요)")
            raise RuntimeError(
                "Micromedex login failed: redirected to 'Please Login' page"
            )

        # 성공 확증 — data-logout-url 확보
        await self._capture_logout_url(page)
        if not self._logout_url:
            logger.warning(
                "[US] 로그인 후 logout URL 미확보 — title=%s url=%s",
                title, page.url,
            )
            # 확정 신호가 없으면 로그인 실패로 간주 (seat 점유 방지)
            raise RuntimeError(
                "Micromedex login uncertain: logout URL not exposed after submit"
            )
        logger.info("[US] 로그인 성공 (logout URL 확보)")

    async def _capture_logout_url(self, page: Page) -> None:
        """header의 data-logout-url 속성에서 정확한 CS/DUPLICATIONSHIELDSYNC 포함 URL 추출."""
        try:
            url = await page.locator("#react-header-root").first.get_attribute("data-logout-url")
            if url:
                self._logout_url = url if url.startswith("http") else urljoin(BASE_URL, url)
                logger.debug("[US] logout URL 캡처: %s", self._logout_url)
        except Exception as e:
            logger.debug("[US] logout URL 추출 실패: %s", e)

    # ──────────────────────────────────────────────────────────────────────
    # 로그아웃 — 4중 안전장치 중 1단계 (try/finally 경로에서 호출)
    # ──────────────────────────────────────────────────────────────────────

    async def logout(self, page: Page) -> None:
        """
        로그아웃 3-step fallback:
          1) 헤더 'Log out' 버튼 클릭
          2) 저장해둔 data-logout-url 직접 GET
          3) 표준 logout 엔드포인트 (/home/dispatch/PFActionId/pf.LogoutPage) 시도
        어느 경로든 성공 시 System Status / login 페이지로 이동해야 seat 반환.
        """
        # 1) 버튼 클릭
        try:
            btn = page.locator(SEL_LOGOUT_BTN).first
            if await btn.count() > 0 and await btn.is_visible():
                await btn.click()
                await page.wait_for_load_state("domcontentloaded", timeout=15_000)
                await page.wait_for_timeout(1_500)
                logger.info("[US] 로그아웃 버튼 클릭 완료")
                return
        except Exception as e:
            logger.warning("[US] 로그아웃 버튼 클릭 실패 → URL fallback: %s", e)

        # 2) URL 직접 이동 (캡처된 data-logout-url)
        if self._logout_url:
            try:
                await page.goto(self._logout_url, wait_until="domcontentloaded", timeout=15_000)
                await page.wait_for_timeout(1_000)
                logger.info("[US] 로그아웃 URL 이동 완료: %s", self._logout_url)
                return
            except Exception as e:
                logger.error("[US] 로그아웃 URL 이동 실패: %s", e)

        # 3) 표준 logout 엔드포인트 — data-logout-url 미캡처 시 최후 수단
        for fallback_url in (
            f"{BASE_URL}/home/dispatch/PFActionId/pf.LogoutPage",
            f"{BASE_URL}/home/dispatch/PFActionId/pf.Logout",
        ):
            try:
                await page.goto(fallback_url, wait_until="domcontentloaded", timeout=15_000)
                await page.wait_for_timeout(1_000)
                body_l = (await page.inner_text("body")).lower()
                if "logged out" in body_l or "log in" in body_l or "system status" in body_l:
                    logger.info("[US] fallback logout 성공: %s", fallback_url)
                    return
                logger.debug("[US] fallback logout 시도 무응답: %s", fallback_url)
            except Exception as e:
                logger.debug("[US] fallback logout 실패 %s: %s", fallback_url, e)

        logger.error("[US] 로그아웃 경로 모두 실패 — seat 점유 상태일 수 있음")

    # ──────────────────────────────────────────────────────────────────────
    # 검색
    # ──────────────────────────────────────────────────────────────────────

    async def search(self, query: str, page: Page) -> list[dict]:
        """
        Red Book 검색 흐름:
          a) 검색어 타이핑 → Dojo WordWheel autocomplete 리스트 등장
          b) 리스트 첫 항목 클릭 (WordWheel_SearchTermId/ItemId hidden field 채움)
          c) doRedBook_Search() or form.submit() 로 제출
          d) 결과 페이지 파싱

        알려진 문제:
          - form name="$formNAME" (Velocity template unresolved) — doRedBook_Search()
            가 document.forms["$formNAME"] 로 찾지 못하면 form.submit() fallback.
          - rbSubmitBtn 은 기본 disabled + display:none. 항목 선택 후 활성화.
        """
        logger.info("[US] Red Book 검색 시작: %s", query)
        await page.goto(REDBOOK_URL, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(3_500)

        # 현재 페이지 덤프 (진단용) — 검색 입력이 없으면 어느 페이지로 갔는지 확인
        landed_url = page.url
        landed_title = await page.title()
        logger.info("[US] REDBOOK_URL 이동 후: url=%s title=%s", landed_url, landed_title)

        if await page.locator(SEL_RB_INPUT).count() == 0:
            # 진단 덤프
            try:
                dbg_landing = self.cache_dir / "debug_landing_after_redbook_goto.html"
                dbg_landing.write_text(await page.content(), encoding="utf-8")
                logger.info("[US] landing HTML 덤프: %s", dbg_landing)
            except Exception:
                pass
            # 메뉴 링크 fallback
            for sel in [
                "a:has-text('RED BOOK')", "a[href*='redbook.FindRedBook']",
                "a#topRedBook", "a[title*='RED BOOK']",
            ]:
                lnk = page.locator(sel).first
                if await lnk.count() > 0:
                    try:
                        await lnk.click()
                        await page.wait_for_load_state("domcontentloaded", timeout=20_000)
                        await page.wait_for_timeout(3_000)
                        logger.info("[US] 메뉴 fallback 클릭: %s → %s", sel, page.url)
                        if await page.locator(SEL_RB_INPUT).count() > 0:
                            break
                    except Exception as e:
                        logger.debug("[US] 메뉴 fallback 실패 %s: %s", sel, e)

        if await page.locator(SEL_RB_INPUT).count() == 0:
            logger.warning(
                "[US] Red Book 검색 입력창 없음 — 구독 미포함 or 세션 만료 (url=%s)",
                page.url,
            )
            return []

        # (a) 제품 이름 radio 명시적 클릭 (onClickRunRedBook 실행 → rbSearchType 세팅)
        try:
            await page.evaluate(
                "if (typeof onClickRunRedBook === 'function') "
                "onClickRunRedBook('redbookProductName');"
            )
        except Exception as e:
            logger.debug("[US] onClickRunRedBook 호출 실패 (무시): %s", e)

        # (b) 검색어 타이핑 — keyup 이 Dojo WordWheel xhr 트리거
        await page.click(SEL_RB_INPUT)
        await page.locator(SEL_RB_INPUT).fill("")
        await page.locator(SEL_RB_INPUT).type(query, delay=60)
        await page.wait_for_timeout(2_000)

        # (c) autocomplete 리스트 대기 — #WordWheel_list 에 자식 노드 등장
        picked = False
        try:
            await page.wait_for_function(
                """() => {
                    const l = document.getElementById('WordWheel_list');
                    return l && l.children && l.children.length > 0;
                }""",
                timeout=10_000,
            )
            # 첫 항목 클릭 (selectOptionFromClick → hidden field 채움)
            first_item = page.locator("#WordWheel_list > *").first
            if await first_item.count() > 0:
                await first_item.click()
                await page.wait_for_timeout(1_500)
                picked = True
                logger.info("[US] WordWheel autocomplete 첫 항목 선택 완료")
        except Exception as e:
            logger.debug("[US] autocomplete 대기/선택 실패: %s", e)

        # (d) 제출 — 우선순위: doRedBook_Search → rbSubmitBtn 클릭 → form.submit()
        submitted_path = None
        try:
            # doRedBook_Search 는 내부에서 form.submit() 호출. $formNAME 때문에
            # 실패할 수 있으므로 결과 변경 여부를 URL 으로 확인.
            await page.evaluate(
                "if (typeof doRedBook_Search === 'function') doRedBook_Search();"
            )
            submitted_path = "doRedBook_Search"
        except Exception as e:
            logger.debug("[US] doRedBook_Search 호출 실패: %s", e)

        # navigation 대기 — 못 떠났으면 fallback
        try:
            await page.wait_for_url(
                re.compile(r"ShowProductSearchResults|ShowProductsForMfr"),
                timeout=8_000,
            )
        except Exception:
            logger.info("[US] doRedBook_Search 제출 미확인 → form.submit fallback")
            try:
                await page.evaluate(
                    """(q) => {
                        const form = document.querySelector(
                            'form[action*="redbook.ShowProductSearchResults"]'
                        );
                        if (!form) return 'no-form';
                        const t = document.getElementById('WordWheel_SearchTerm_index_0');
                        if (t) t.value = q;
                        form.submit();
                        return 'ok';
                    }""",
                    query,
                )
                submitted_path = "form.submit"
                await page.wait_for_load_state("domcontentloaded", timeout=20_000)
            except Exception as e:
                logger.warning("[US] form.submit fallback 실패: %s", e)

        # 결과 렌더링 대기
        try:
            await page.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:
            pass
        await page.wait_for_timeout(3_000)

        for sel in [
            "#rbResultsTable", "table.searchResults", "table#resultsTable",
            "table#productTable", ".resultHeader", "table.productTable",
            "div.searchResults",
        ]:
            try:
                await page.wait_for_selector(sel, timeout=3_000)
                logger.info("[US] 결과 selector 감지: %s", sel)
                break
            except Exception:
                continue

        # 디버그 저장
        try:
            dbg = self.cache_dir / f"debug_{re.sub(r'[^a-z0-9]+', '_', query.lower())}_result.html"
            dbg.write_text(await page.content(), encoding="utf-8")
            logger.info(
                "[US] 디버그 HTML 저장: %s (%d bytes, picked=%s, path=%s, url=%s)",
                dbg, dbg.stat().st_size, picked, submitted_path, page.url,
            )
        except Exception:
            pass

        return await self._parse_results(page, query)

    # ──────────────────────────────────────────────────────────────────────
    # 결과 파싱 — 실제 결과 페이지 구조 확인 전의 방어적 구현.
    # 첫 실행 후 debug_*.html 로 구조 파악하여 이 메서드만 정제하면 됨.
    # ──────────────────────────────────────────────────────────────────────

    async def _parse_results(self, page: Page, query: str) -> list[dict]:
        """
        Red Book 결과 테이블 파싱.

        각 데이터 행 (tr.rowBeige / tr.rowWhite) 은 td.rbProductCell 19개로 구성:
          [0]  checkbox
          [1]  dollar indicator (가격변동)
          [2]  reserved
          [3]  Product name (KEYTRUDA)
          [4]  Generic / ingredient (pembrolizumab)
          [5]  Manufacturer (MERCK SHARP & DOHME LLC)
          [6]  Obsolete (N/Y)
          [7]  OTC (N/Y)
          [8]  Therapeutic class
          [9]  Code type (NDC / HRI / UPC)
          [10] Code value (00006-3026-02)
          [11] Form (SOL / TAB / CAP / INJ …)
          [12] Strength (25 mg/1 ml)
          [13] Route (INTRAVENOUS)
          [14] Package size (4 ml / 4 ml 2s …)
          [15] Repackager (N/Y)
          [16] WAC package price ← primary local_price (ex-manufacturer)
          [17] AWP package price (fallback — 유통 마크업 포함)
          [18] AWP unit price

        가격 "--" 인 행은 alternate NDC / inactive 로 skip.
        """
        items: list[dict] = []

        rows = page.locator("tr.rowBeige, tr.rowWhite")
        n_rows = await rows.count()
        logger.info("[US] 결과 후보 행: %d", n_rows)

        for i in range(n_rows):
            row = rows.nth(i)
            try:
                cells = row.locator("td.rbProductCell")
                ncells = await cells.count()
                if ncells < 18:
                    logger.debug("[US] 행 [%d] 컬럼 부족 (%d) — skip", i, ncells)
                    continue

                texts: list[str] = []
                for j in range(ncells):
                    t = (await cells.nth(j).inner_text()).strip()
                    texts.append(t)

                product_name  = texts[3]
                # cell[4] 는 <a>generic</a> + <span class="dijitTooltipData"> 툴팁
                # 이 붙어 있어 inner_text 가 합쳐짐. <a> 만 뽑아낸다.
                ingredient = texts[4]
                try:
                    a = cells.nth(4).locator("a").first
                    if await a.count() > 0:
                        ingredient = (await a.inner_text()).strip()
                except Exception:
                    pass
                manufacturer  = texts[5]
                try:
                    a = cells.nth(5).locator("a").first
                    if await a.count() > 0:
                        manufacturer = (await a.inner_text()).strip()
                except Exception:
                    pass
                code_type     = texts[9].strip()
                code_value    = texts[10].strip()
                dosage_form   = texts[11]
                strength      = texts[12]
                route         = texts[13]
                package_size  = texts[14]
                wac_pkg       = texts[16]
                awp_pkg       = texts[17]
                awp_unit      = texts[18] if ncells >= 19 else ""

                def _money(s: str):
                    s = (s or "").strip().replace(",", "")
                    if not s or s == "--":
                        return None
                    try:
                        return float(s)
                    except ValueError:
                        return None

                awp_pkg_val = _money(awp_pkg)
                wac_pkg_val = _money(wac_pkg)
                awp_unit_val = _money(awp_unit)

                # WAC 우선 (재정영향분석 표준: ex-manufacturer price).
                # AWP 는 유통 마크업 포함이라 factory_ratio 0.74 와 중복 — WAC 없을 때만 fallback.
                local_price = wac_pkg_val if wac_pkg_val is not None else awp_pkg_val
                if local_price is None:
                    logger.debug(
                        "[US] 행 [%d] %s / %s — 가격 없음 (alternate NDC), skip",
                        i, product_name, code_value,
                    )
                    continue

                items.append({
                    "product_name":    product_name or query,
                    "ingredient":      ingredient,
                    "dosage_strength": strength,
                    "dosage_form":     dosage_form,
                    "package_unit":    package_size,
                    "local_price":     local_price,
                    "source_url":      page.url,
                    "extra": {
                        "source_type":   self.SOURCE_TYPE,
                        "ndc":           code_value if code_type.upper() == "NDC" else None,
                        "code_type":     code_type,
                        "code_value":    code_value,
                        "manufacturer":  manufacturer,
                        "route":         route,
                        "awp_package":   awp_pkg_val,
                        "wac_package":   wac_pkg_val,
                        "awp_unit":      awp_unit_val,
                        "price_basis":   "WAC_package" if wac_pkg_val is not None else "AWP_package",
                    },
                })
            except Exception as e:
                logger.debug("[US] 행 파싱 실패 [%d]: %s", i, e)
                continue

        logger.info("[US] '%s' 파싱된 결과: %d건", query, len(items))
        return items

    # ──────────────────────────────────────────────────────────────────────
    # run() 오버라이드 — 4중 안전장치 통합
    # ──────────────────────────────────────────────────────────────────────

    async def run(self, query: str) -> list[dict]:
        """
        seat 직렬화 + signal/atexit 등록 + try/finally 이중 안전망으로
        어떤 종료 경로에서도 logout 을 호출한다.
        """
        logger.info("[US] 검색 요청 대기 (seat lock): %s", query)
        async with self.__class__._get_seat_lock():
            logger.info("[US] seat lock 획득: %s", query)

            # atexit — best effort (이벤트 루프 없는 상태에서는 동기 fetch 로 fallback)
            atexit.register(self._emergency_logout_sync)

            # signal handler — SIGINT/SIGTERM
            loop = asyncio.get_running_loop()
            prior_handlers = {}
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    prior_handlers[sig] = signal.getsignal(sig)
                    loop.add_signal_handler(
                        sig, lambda s=sig: asyncio.create_task(self._signal_logout(s))
                    )
                except (NotImplementedError, RuntimeError):
                    # Windows / 일부 환경에서는 add_signal_handler 미지원
                    pass

            results = []
            try:
                async with async_playwright() as pw:
                    self._browser = await pw.chromium.launch(
                        headless=self.headless, args=self.PLAYWRIGHT_ARGS
                    )
                    ctx_kwargs = {
                        "user_agent": (
                            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/124.0.0.0 Safari/537.36"
                        ),
                    }
                    # storage_state 재사용 (로그인 seat 절약)
                    if self.storage_state_path and self.storage_state_path.exists():
                        ctx_kwargs["storage_state"] = str(self.storage_state_path)
                        logger.info("[US] storage_state 재사용: %s", self.storage_state_path)

                    self._ctx = await self._browser.new_context(**ctx_kwargs)
                    self._page = await self._ctx.new_page()

                    try:
                        await self.login(self._page)

                        # 로그인 성공 시 storage_state 저장
                        try:
                            await self._ctx.storage_state(path=str(self.storage_state_path))
                        except Exception as e:
                            logger.debug("[US] storage_state 저장 실패: %s", e)

                        raw = await self.search(query, self._page)
                        searched_at = datetime.now().isoformat()
                        for item in raw:
                            form_type = self._resolve_form_type(item)
                            results.append({
                                "searched_at":         searched_at,
                                "query_name":          query,
                                "country":             self.COUNTRY,
                                "product_name":        item.get("product_name"),
                                "ingredient":          item.get("ingredient"),
                                "dosage_strength":     item.get("dosage_strength"),
                                "dosage_form":         item.get("dosage_form"),
                                "package_unit":        item.get("package_unit"),
                                "local_price":         item.get("local_price"),
                                "currency":            self.CURRENCY,
                                "exchange_rate":       None,
                                "exchange_rate_from":  None,
                                "exchange_rate_to":    None,
                                "factory_price_krw":   None,
                                "vat_rate":            None,
                                "distribution_margin": None,
                                "adjusted_price_krw":  None,
                                "source_url":          item.get("source_url", ""),
                                "source_label":        self.SOURCE_LABEL,
                                "raw_data":            json.dumps(
                                    item.get("extra", {}), ensure_ascii=False
                                ),
                                "form_type":           form_type,
                            })
                    finally:
                        # 반드시 로그아웃 — 예외여도 실행
                        try:
                            await self.logout(self._page)
                        except Exception as e:
                            logger.error("[US] logout 중 예외: %s", e)
                        try:
                            await self._ctx.close()
                        except Exception:
                            pass
                        try:
                            await self._browser.close()
                        except Exception:
                            pass
                        self._ctx = None
                        self._browser = None
                        self._page = None
            finally:
                # signal handler 복구
                for sig, prior in prior_handlers.items():
                    try:
                        loop.remove_signal_handler(sig)
                    except (NotImplementedError, RuntimeError):
                        pass
                    try:
                        signal.signal(sig, prior)
                    except Exception:
                        pass
                # atexit 중복 방지
                try:
                    atexit.unregister(self._emergency_logout_sync)
                except Exception:
                    pass

            logger.info("[US] seat lock 해제: %s (결과 %d건)", query, len(results))
            return results

    # ──────────────────────────────────────────────────────────────────────
    # 비상 로그아웃 — signal/atexit 경로에서 호출
    # ──────────────────────────────────────────────────────────────────────

    async def _signal_logout(self, sig) -> None:
        """SIGINT/SIGTERM 수신 시 현재 페이지로 logout 시도."""
        logger.warning("[US] 신호 %s 수신 — 비상 로그아웃 시도", sig)
        if self._page is not None:
            try:
                await asyncio.wait_for(self.logout(self._page), timeout=10)
            except Exception as e:
                logger.error("[US] 비상 로그아웃 실패: %s", e)
        # 프로세스 정상 종료 경로로 돌려보내기
        try:
            asyncio.get_running_loop().stop()
        except Exception:
            pass

    def _emergency_logout_sync(self) -> None:
        """atexit — 동기 requests 로 logout URL 호출 (cookies 동기화는 best-effort)."""
        if not self._logout_url:
            return
        try:
            import requests
            cookies = {}
            if self._ctx is not None:
                # 이벤트 루프가 살아있지 않으면 cookies 조회가 불가능 — skip
                pass
            requests.get(self._logout_url, cookies=cookies, timeout=10)
            logger.info("[US] atexit logout 호출")
        except Exception as e:
            logger.debug("[US] atexit logout 실패: %s", e)
