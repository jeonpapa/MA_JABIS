"""
환율 조회 모듈
- 출처: KEB하나은행 평균환율 조회 페이지
  https://www.kebhana.com/cont/mall/mall15/mall1502/index.jsp
- 조회 조건: 기간평균, 직접입력 (36개월), 고시회차 최종
- Playwright로 폼 입력 → 엑셀 다운로드 → 파싱
- HIRA 조정가 공식 계산기 포함
"""

import asyncio
import logging
import os
import re
from datetime import date, timedelta
from pathlib import Path

from dateutil.relativedelta import relativedelta

logger = logging.getLogger(__name__)

KEB_PAGE_URL = "https://www.kebhana.com/cont/mall/mall15/mall1502/index.jsp"

# 국가코드 → 통화코드
COUNTRY_CURRENCY = {
    "US": "USD", "UK": "GBP", "DE": "EUR",
    "FR": "EUR", "IT": "EUR", "CH": "CHF",
    "JP": "JPY", "CA": "CAD",
}

# 엑셀 파일 내 통화명 → 표준 통화코드 매핑
KEB_CURRENCY_NAME_MAP = {
    "USD": "USD", "미국": "USD", "달러": "USD",
    "EUR": "EUR", "유로": "EUR",
    "JPY": "JPY", "일본": "JPY", "엔": "JPY",
    "GBP": "GBP", "영국": "GBP", "파운드": "GBP",
    "CHF": "CHF", "스위스": "CHF", "프랑": "CHF",
    "CAD": "CAD", "캐나다": "CAD",
    "CNH": "CNH", "중국": "CNH", "위안": "CNH",
    "AUD": "AUD", "호주": "AUD",
}


def per_unit_rate(currency: str | None, rate: float | None) -> float | None:
    """환율을 **통화 1단위당 KRW** 로 정규화한다 — 유일한 정규화 지점.

    KEB하나은행은 JPY 를 '100엔당 KRW' 로 고시한다(예: 923.21 = ¥100). 과거 parse 가 라벨에 "100" 이 있을 때만
    나눴고 현재 라벨은 "일본 JPY" 라 100엔당 값이 그대로 저장된 행이 있다(2026-10 감사). 그래서 소스(parse)·계산기·
    리포트·월간 재계산(recompute_foreign_fx)이 모두 이 함수를 거친다: JPY 이고 100 을 넘으면 /100, 나머지는 그대로.
    (¥1 = ₩100 을 넘는 날은 오지 않는다 — 넘으면 이 휴리스틱이 아니라 저장 규약을 바꿔야 한다.)
    """
    if rate is None:
        return None
    if (currency or "").upper() == "JPY" and rate > 100:
        return rate / 100.0
    return rate


def _calc_date_range(ref_date: date = None) -> tuple[str, str]:
    """
    HIRA 기준 36개월 조회 기간 계산.
    - End: 기준일 전월 말일
    - Start: End 기준 36개월 전 + 3일 (KEB 입력 관례)
    반환: (start_str: YYYYMMDD, end_str: YYYYMMDD)
    """
    if ref_date is None:
        ref_date = date.today()

    # 전월 말일
    first_of_this_month = ref_date.replace(day=1)
    end_dt = first_of_this_month - timedelta(days=1)

    # 36개월 전 + 3일
    start_dt = end_dt - relativedelta(months=36) + timedelta(days=3)

    return start_dt.strftime("%Y%m%d"), end_dt.strftime("%Y%m%d")


# 캐시 파일이 '유효' 하려면 반드시 있어야 하는 통화 (A8 조정가 8개국 중 CAD 제외 핵심 5종).
CORE_CURRENCIES = ("USD", "EUR", "JPY", "GBP", "CHF")
_PREVIEW_BYTES = 300


class InvalidKebFileError(ValueError):
    """KEB 다운로드(또는 캐시 파일)가 유효한 환율 TSV 가 아니다 — 빈 본문·오류 페이지·핵심 통화 누락.

    다운로드 단계에서 이 예외가 나면 파일은 최종 캐시 이름으로 내려앉지 않는다(임시 파일 삭제). 메시지에 바이트 크기와
    받은 내용의 앞부분을 넣어 다음 장애가 '무엇을 받았는지' 말하게 한다 (2026-10-02 는 0 바이트였는데 로그는 '헤더 행을
    찾을 수 없음' 뿐이었다).
    """


def _preview(data: bytes, limit: int = _PREVIEW_BYTES) -> str:
    text = data[:limit].decode("euc-kr", errors="replace")
    lines = [ln for ln in text.splitlines() if ln.strip()][:5]
    return " | ".join(ln.strip() for ln in lines) + (" …" if len(data) > limit else "")


def validate_keb_file(path: Path) -> dict[str, float]:
    """캐시/다운로드 파일의 유효성 검사 → 환율 dict. 쓰기(다운로드)와 읽기(캐시 로드)가 같은 기준을 쓴다.

    유효 = 0 바이트가 아니고, parse_keb_excel 이 성공하고, CORE_CURRENCIES 가 모두 양수로 있다.
    """
    size = path.stat().st_size if path.exists() else -1
    if size <= 0:
        raise InvalidKebFileError(f"{path.name}: 빈 다운로드 ({max(size, 0)} bytes)")
    data = path.read_bytes()
    try:
        rates = parse_keb_excel(path)
    except ValueError as e:
        raise InvalidKebFileError(f"{path.name}: 파싱 실패 ({size} bytes) — {e}; 수신 내용: {_preview(data)!r}") from e
    missing = [c for c in CORE_CURRENCIES if not (rates.get(c) or 0) > 0]
    if missing:
        raise InvalidKebFileError(
            f"{path.name}: 핵심 통화 누락/비양수 {missing} ({size} bytes, 파싱 {len(rates)}개 통화); "
            f"수신 내용: {_preview(data)!r}")
    return rates


def cache_file_name(start_str: str, end_str: str) -> str:
    return f"keb_avg_rate_{start_str}_{end_str}.xlsx"


def list_cache_files(cache_dir: Path, *, skip_empty: bool = True) -> list[Path]:
    """캐시 파일을 최신 창 → 과거 창 순으로. 진행 중 임시 파일(`.…​.part`)은 glob 에 걸리지 않는다.

    skip_empty=True 면 0 바이트 파일은 경고와 함께 뺀다 — 로더와 신선도 판정(freshness.check_exchange_rate_cache)이 같은
    목록을 봐야 빈 파일이 '최신 창' 을 가장하지 못한다.
    """
    files = sorted(cache_dir.glob("keb_avg_rate_*.xlsx"), reverse=True)
    if not skip_empty:
        return files
    out = []
    for f in files:
        if f.stat().st_size == 0:
            logger.warning("[환율] 0 바이트 캐시 파일 무시: %s", f.name)
            continue
        out.append(f)
    return out


def _window_meta(path: Path) -> dict:
    m = re.search(r"keb_avg_rate_(\d{8})_(\d{8})", path.stem)
    return {"from": m.group(1), "to": m.group(2)} if m else {}


async def fetch_keb_excel(
    cache_dir: Path = None,
    ref_date: date = None,
    headless: bool = True,
    *,
    downloader=None,
) -> Path:
    """
    KEB하나은행 평균환율 엑셀을 **임시 파일로** 받아 검증한 뒤에만 최종 캐시 이름으로 원자적 교체.

    - 임시 경로는 같은 디렉터리의 `.keb_avg_rate_{from}_{to}.xlsx.part` — 캐시 glob 에 걸리지 않는다.
    - 검증(validate_keb_file) 실패·예외 시 임시 파일을 지우고 InvalidKebFileError 를 올린다. 직전 유효 캐시는 그대로다.
    - downloader(start_str, end_str, save_path, headless): 기본은 Playwright(_download_keb_excel). 테스트가 대체한다.
    """
    start_str, end_str = _calc_date_range(ref_date)
    logger.info("[환율] 조회 기간: %s ~ %s", start_str, end_str)

    if cache_dir is None:
        cache_dir = Path("data/foreign/exchange_rate")
    cache_dir.mkdir(parents=True, exist_ok=True)

    final_path = cache_dir / cache_file_name(start_str, end_str)
    tmp_path = cache_dir / f".{final_path.name}.part"
    tmp_path.unlink(missing_ok=True)
    download = downloader or _download_keb_excel
    try:
        await download(start_str, end_str, tmp_path, headless)
        rates = validate_keb_file(tmp_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    os.replace(tmp_path, final_path)
    logger.info("[환율] 캐시 갱신: %s (%d bytes, %d개 통화)", final_path.name, final_path.stat().st_size, len(rates))
    return final_path


async def _download_keb_excel(start_str: str, end_str: str, save_path: Path, headless: bool = True) -> None:
    """KEB 페이지 폼 입력 → 엑셀 다운로드를 save_path 에 저장 (Playwright). 검증은 호출자(fetch_keb_excel)가 한다."""
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        context = await browser.new_context(accept_downloads=True)
        page = await context.new_page()

        # 다이얼로그(경고창 등) 자동 닫기
        page.on("dialog", lambda d: asyncio.create_task(d.dismiss()))

        await page.goto(KEB_PAGE_URL, wait_until="networkidle", timeout=30_000)

        # iframe 찾기
        target_frame = None
        for frame in page.frames:
            if "wpfxd651" in frame.url:
                target_frame = frame
                break

        if target_frame is None:
            raise RuntimeError("KEB 평균환율 iframe을 찾지 못했습니다.")

        end_display = f"{end_str[:4]}-{end_str[4:6]}-{end_str[6:]}"

        # 시작일이 휴일/공휴일(예: 10월 3일 개천절)인 경우 KEB는 '해당 입력일자가 적절하지 않습니다' 에러(OCOM23466)를
        # 반환하고 엑셀 다운로드가 0바이트가 된다. 유효한 영업일 데이터를 받을 때까지 최대 5일까지 하루씩 미뤄가며 재시도한다.
        cur_start = date(int(start_str[:4]), int(start_str[4:6]), int(start_str[6:]))
        search_ok = False
        final_start_display = f"{start_str[:4]}-{start_str[4:6]}-{start_str[6:]}"

        for _ in range(6):
            s_str = cur_start.strftime("%Y%m%d")
            s_display = cur_start.strftime("%Y-%m-%d")

            # JavaScript로 폼 값을 한 번에 설정하고 조회 실행
            await target_frame.evaluate(f"""() => {{
                // 1) 조회구분: 기간평균 (value=4)
                const rdoPeriod = document.getElementById('inqDvCd_p');
                if (rdoPeriod) {{ rdoPeriod.checked = true; rdoPeriod.click(); }}

                // 2) 고시회차: 최종 (tmpPbldDvCd_2, value=0).
                //    주의: tmpPbldDvCd_1(value=1)=최초, tmpPbldDvCd_2(value=0)=최종.
                //    inputCheck() 가 제출 시 radiobox.getCheckedValue(tmpPbldDvCd) 로
                //    pbldDvCd hidden 을 재생성하므로 *라디오 체크가 결정적* (hidden 수동
                //    설정값은 덮어써짐). 반드시 tmpPbldDvCd_2 를 체크해야 최종값이 조회됨.
                document.getElementsByName('tmpPbldDvCd').forEach(
                    r => {{ r.checked = (r.id === 'tmpPbldDvCd_2'); }}
                );

                // 3) 조회기간 설정
                const setVal = (id, v) => {{ const el = document.getElementById(id); if(el) el.value = v; }};
                setVal('inqStrDt',    '{s_str}');
                setVal('inqEndDt',    '{end_str}');
                setVal('tmpInqStrDt_p', '{s_display}');
                setVal('tmpInqEndDt_p', '{end_display}');
                setVal('pbldDvCd', '0');  // 최종 (inputCheck 가 라디오값으로 재생성하나 일관성 위해 명시)
            }}""")
            await page.wait_for_timeout(300)

            # ── 4) 조회 실행 및 응답 검증
            async with page.expect_response(
                lambda r: "wpfxd651_06i_01.do" in r.url and "excel" not in r.url,
                timeout=15_000,
            ) as resp_info:
                await target_frame.evaluate(
                    "pbk.foreign.rate.pbld.avg.search(document.forms['inqFrm'])"
                )
            resp = await resp_info.value
            body = await resp.text()

            if "errorCode" not in body:
                final_start_display = s_display
                search_ok = True
                logger.info("[환율] 폼 설정 및 조회 완료: %s ~ %s", s_display, end_display)
                break

            logger.warning("[환율] 조회 실패 (영업일 아님 의심: %s) — 다음 영업일 시도", s_display)
            cur_start += timedelta(days=1)

        if not search_ok:
            raise RuntimeError(f"KEB 평균환율 조회 실패: 유효한 영업일 시작일을 찾지 못했습니다 ({start_str} ~ {end_str})")

        await page.wait_for_timeout(2000)

        # ── 5) 엑셀 다운로드 (임시 경로 — 검증 통과 후 fetch_keb_excel 이 최종 이름으로 바꾼다)
        async with page.expect_download(timeout=30_000) as dl_info:
            await target_frame.evaluate(
                "pbk.foreign.rate.pbld.avg.doExcelDown('Y')"
            )
        download = await dl_info.value
        await download.save_as(str(save_path))
        logger.info("[환율] 엑셀 수신 (%s ~ %s): %s (%d bytes)", final_start_display, end_display, save_path.name, save_path.stat().st_size)

        await context.close()
        await browser.close()


def parse_keb_excel(excel_path: Path) -> dict[str, float]:
    """
    KEB 평균환율 다운로드 파일 파싱 → {통화코드: 매매기준율} 반환.
    실제 파일은 EUC-KR 인코딩 TSV(탭 구분) 형식.
    컬럼 구조: 통화 | 현찰사실때 | 현찰파실때 | 송금보내실때 | 송금받으실때
              | T/C사실때 | 외화수표파실때 | 매매기준율 | 환가료율 | 미화환산율
    """
    raw = excel_path.read_bytes().decode("euc-kr", errors="replace")
    lines = raw.splitlines()

    # 헤더 행 찾기 ('통화' 포함 행)
    header_idx = next(
        (i for i, line in enumerate(lines) if "통화" in line and "매매기준율" in line),
        None,
    )
    if header_idx is None:
        raise ValueError(f"헤더 행을 찾을 수 없음: {excel_path.name}")

    headers = [h.strip() for h in lines[header_idx].split("\t")]
    currency_col_idx = next((i for i, h in enumerate(headers) if "통화" in h), 0)
    rate_col_idx     = next((i for i, h in enumerate(headers) if "매매기준율" in h), 6)

    logger.debug("[환율] 헤더(%d행): %s", header_idx, headers)

    rates = {}
    for line in lines[header_idx + 1:]:
        cols = [c.strip() for c in line.split("\t")]
        if len(cols) <= rate_col_idx:
            continue

        raw_cur  = cols[currency_col_idx]
        raw_rate = cols[rate_col_idx]

        # "미국 USD" → "USD" 추출
        cur_match = re.search(r"\b([A-Z]{3})\b", raw_cur)
        if not cur_match:
            continue
        cur = cur_match.group(1)

        try:
            rate = float(raw_rate.replace(",", ""))
            if rate > 0:
                # KEB 는 JPY 를 "100엔당" 으로 고시 (예: 939.36 = ¥100 당 KRW). 라벨 "(100)" 유무와 무관하게
                # per-1 로 정규화해 저장한다 — per_unit_rate 가 단일 정규화 지점 (라벨에 100 이 있으면 먼저 나눈다).
                per_unit_divisor = 100.0 if "100" in raw_cur else 1.0
                rates[cur] = per_unit_rate(cur, rate / per_unit_divisor)
        except ValueError:
            continue

    logger.info("[환율] 파싱 완료: %d개 통화", len(rates))
    return rates


class ExchangeRateFetcher:
    """
    KEB하나은행 평균환율 (36개월 기간평균, 매매기준율) 조회.
    최초 실행 시 Playwright로 엑셀 다운로드 후 캐시, 이후 캐시 재사용.
    """

    def __init__(self, cache_dir: Path = None, **kwargs):
        self.cache_dir = cache_dir or Path("data/foreign/exchange_rate")
        self._rates: dict[str, float] = {}   # {통화코드: 환율}
        self._rate_meta: dict = {}
        self.active_cache_file: Path | None = None
        # 로더가 건너뛴 (활성 파일보다 **새로운**) 무효 캐시 파일 — 호출자가 "과거 창을 쓰고 있다" 를 드러낼 수 있게 남긴다.
        self.skipped_cache_files: list[dict] = []

    def _load_latest_cache(self) -> bool:
        """캐시 디렉터리를 최신 창 → 과거 창 순으로 돌며 **첫 유효 파일**을 로드.

        무효(0 바이트·파싱 실패·핵심 통화 누락) 파일은 경고(파일명·사유)와 함께 건너뛰고 skipped_cache_files 에 남긴다.
        예전에는 이름순 최신 파일 하나만 파싱해 2026-10-02 의 0 바이트 파일이 모든 get_rate() 를 죽였다.
        """
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.skipped_cache_files = []
        for f in list_cache_files(self.cache_dir, skip_empty=False):
            try:
                rates = validate_keb_file(f)
            except InvalidKebFileError as e:
                logger.warning("[환율] 무효 캐시 파일 건너뜀: %s — %s", f.name, e)
                self.skipped_cache_files.append({"file": f.name, "reason": str(e)})
                continue
            self._rates = rates
            self._rate_meta = _window_meta(f)
            self.active_cache_file = f
            if self.skipped_cache_files:
                logger.warning("[환율] 최신 캐시 %d개가 무효라 과거 창 %s 를 사용한다: %s — 런북 exchange_rate_refresh "
                               "(`scheduler.py --fx-refresh-now`) 로 재다운로드",
                               len(self.skipped_cache_files), self._rate_meta.get("to"),
                               [s["file"] for s in self.skipped_cache_files])
            else:
                logger.info("[환율] 캐시 파일 로드: %s", f.name)
            return True
        self._rates, self._rate_meta, self.active_cache_file = {}, {}, None
        return False

    def cache_status(self) -> dict:
        """활성 캐시 파일·창·건너뛴 신규 무효 파일 — 잡 counts / 진단용."""
        return {
            "active_file": self.active_cache_file.name if self.active_cache_file else None,
            "from": self._rate_meta.get("from"),
            "to": self._rate_meta.get("to"),
            "skipped_newer": list(self.skipped_cache_files),
        }

    async def refresh(self, ref_date: date = None, headless: bool = True, *, downloader=None) -> dict:
        """KEB에서 최신 평균환율 엑셀을 다운로드(검증·원자적 교체)하고 파싱."""
        excel_path = await fetch_keb_excel(self.cache_dir, ref_date, headless, downloader=downloader)
        self._rates = parse_keb_excel(excel_path)
        self._rate_meta = _window_meta(excel_path)
        self.active_cache_file = excel_path
        self.skipped_cache_files = []
        return self._rates

    def get_rate(self, currency: str) -> dict:
        """특정 통화 환율 반환. 캐시가 없으면 오류 발생 (refresh() 먼저 호출 필요)."""
        if not self._rates:
            if not self._load_latest_cache():
                skipped = ", ".join(f"{s['file']} ({s['reason']})" for s in self.skipped_cache_files)
                raise RuntimeError(
                    "환율 데이터가 없습니다. await fetcher.refresh()를 먼저 실행하세요."
                    + (f" 무효 캐시 파일: {skipped}" if skipped else "")
                )
        currency = currency.upper()
        rate = self._rates.get(currency)
        if rate is None:
            raise ValueError(f"환율 데이터 없음: {currency} (보유: {list(self._rates.keys())})")
        return {
            "currency": currency,
            "rate": rate,
            "from_month": self._rate_meta.get("from", ""),
            "to_month":   self._rate_meta.get("to", ""),
            "data_points": 1,
        }

    def get_36m_average(self, currency: str, _reference_date=None) -> dict:
        """get_rate() 호환 인터페이스."""
        return self.get_rate(currency)

    def get_all_rates(self, _reference_date=None) -> dict:
        """8개국 통화 환율 반환."""
        result = {}
        for country, currency in COUNTRY_CURRENCY.items():
            try:
                result[currency] = self.get_rate(currency)
            except Exception as e:
                logger.warning("%s(%s) 환율 없음: %s", country, currency, e)
                result[currency] = None
        return result


class PriceCalculator:
    """A8 조정가 계산기 — 한국 재정영향분석 표준 (per-tablet/per-vial KRW).

    공식 (2025.3 기준, MSD 재정영향분석서 표준):
        A8_adj_per_unit_KRW =
            per_unit_local                          ← listed_price / pack_count
            × exchange_rate                         ← KEB 36mo 평균 (JPY per-1 정규화)
            × factory_ratio(country, source_type)   ← 국가별 공장도 출하 비율
            × (1 + KR_VAT = 0.10)                   ← 한국 부가가치세 10% 가산
            × (1 + KR_DIST_MARGIN = 0.0869)         ← 한국 유통거래폭 8.69% 가산

    **한국 VAT/유통거래폭은 국가별이 아닌 Korean A8 기준 상수** — foreign ex-factory 를
    한국 retail 등가로 환산하기 위한 uplift. 국가별 VAT(DE 19%, JP 10% 등) 과 혼동 금지.

    반환 adjusted_price_krw 는 **per-unit (tablet/vial) KRW** — pack 단위 아님.
    """

    # Korean A8 상수 (모든 국가 공통 uplift)
    KR_VAT = 0.10
    KR_DIST_MARGIN = 0.0869

    # 국가별 factory_ratio (local retail → 해당국 ex-factory, 한국 재정영향분석 표준)
    FACTORY_RATIO = {
        "US": 0.74, "UK": 0.73, "DE": 0.6955,
        "FR": 0.77, "IT": 0.93, "CH": 0.73,
        "JP": 0.79, "CA": 0.81,
    }

    # source-specific 오버라이드 (공시가 체계가 다른 경우). 키는 스크레이퍼의 SOURCE_TYPE 과 **정확히** 같아야 한다 —
    # tests/test_foreign_factory_ratio_effective.py 가 스크레이퍼 선언값으로 실효 비율을 고정한다.
    # 2026-10: "ch_compendium"/"fr_vidal" 0.65 항목 삭제 — 스크레이퍼 SOURCE_TYPE 은 "compendium"/"vidal" 이라 한 번도
    # 발효된 적 없고 실효 비율은 CH 0.73 / FR 0.77(국가 기본) 이었다. 사용자 결정: 코드가 현재 시점의 진실, 저장값 재계산 없음.
    SOURCE_OVERRIDES = {
        "aifa_exfactory": 1.0,    # IT Class H / TrovaNorme: 이미 ex-factory
    }

    CURRENCY = COUNTRY_CURRENCY

    def resolve_factory_ratio(self, country: str, source_type: str = None):
        """source_type 우선, 없으면 국가별 기본값."""
        if source_type and source_type in self.SOURCE_OVERRIDES:
            return self.SOURCE_OVERRIDES[source_type]
        return self.FACTORY_RATIO.get(country)

    def calculate_adjusted_price(
        self,
        country: str,
        listed_price: float,
        exchange_rate: float,
        pack_count: int = None,
        source_type: str = None,
    ) -> dict:
        """A8 조정가 계산. 반환 `adjusted_price_krw` 는 per-unit KRW.

        pack_count: listed_price 가 pack 가격이면 tablet/vial 수. None/1 이면 per-unit 취급.
        """
        # JPY per-100 → per-1 (저장된 레거시 행 923.21 호환) — 단일 정규화 지점 per_unit_rate
        exchange_rate = per_unit_rate(self.CURRENCY.get(country), exchange_rate)

        if pack_count and pack_count > 1:
            per_unit_listed = listed_price / pack_count
        else:
            per_unit_listed = listed_price
            pack_count = 1

        ratio = self.resolve_factory_ratio(country, source_type)
        if ratio is None:
            raise ValueError(
                f"factory_ratio 정의 없음: country={country} source_type={source_type}"
            )

        per_unit_raw_krw     = per_unit_listed * exchange_rate
        per_unit_factory_krw = per_unit_raw_krw * ratio
        per_unit_vat_krw     = per_unit_factory_krw * (1 + self.KR_VAT)
        per_unit_adj_krw     = per_unit_vat_krw * (1 + self.KR_DIST_MARGIN)

        ratio_label = (
            f"source:{source_type}={ratio}"
            if source_type and source_type in self.SOURCE_OVERRIDES
            else f"{country} factory_ratio={ratio}"
        )

        return {
            "listed_price":        listed_price,
            "pack_count":          pack_count,
            "per_unit_listed":     round(per_unit_listed, 4),
            "factory_ratio":       ratio,
            "factory_ratio_label": ratio_label,
            "factory_price":       round(per_unit_listed * ratio, 4),
            "exchange_rate":       exchange_rate,
            "krw_converted":       int(per_unit_raw_krw),
            "factory_price_krw":   int(per_unit_factory_krw),
            "vat_rate":            self.KR_VAT,
            "vat_applied_krw":     int(per_unit_vat_krw),
            "distribution_margin": self.KR_DIST_MARGIN,
            "adjusted_price_krw":  int(per_unit_adj_krw),   # per-unit KRW
        }


# ── 단독 실행 테스트 ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import asyncio, json
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    async def main():
        fetcher = ExchangeRateFetcher()
        print("KEB하나은행 평균환율 다운로드 중...")
        rates = await fetcher.refresh()
        print(json.dumps(rates, ensure_ascii=False, indent=2))

    asyncio.run(main())
