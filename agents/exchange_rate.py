"""
환율 조회 모듈
- 출처: KEB하나은행 평균환율 조회 페이지
  https://www.kebhana.com/cont/mall/mall15/mall1502/index.jsp
- 조회 조건: 기간평균, 직접입력 (36개월), 고시회차 최종
- Playwright로 폼 입력 → 엑셀 다운로드 → 파싱
- HIRA 조정가 공식 계산기 포함
"""

import logging
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


async def fetch_keb_excel(
    cache_dir: Path = None,
    ref_date: date = None,
    headless: bool = True,
) -> Path:
    """
    KEB하나은행 평균환율 페이지에서 엑셀을 다운로드해 저장 경로 반환.
    Playwright 비동기로 실행해야 함.
    """
    from playwright.async_api import async_playwright

    start_str, end_str = _calc_date_range(ref_date)
    logger.info("[환율] 조회 기간: %s ~ %s", start_str, end_str)

    if cache_dir is None:
        cache_dir = Path("data/foreign/exchange_rate")
    cache_dir.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        context = await browser.new_context(accept_downloads=True)
        page = await context.new_page()

        await page.goto(KEB_PAGE_URL, wait_until="networkidle", timeout=30_000)

        # iframe 찾기
        target_frame = None
        for frame in page.frames:
            if "wpfxd651" in frame.url:
                target_frame = frame
                break

        if target_frame is None:
            raise RuntimeError("KEB 평균환율 iframe을 찾지 못했습니다.")

        start_display = f"{start_str[:4]}-{start_str[4:6]}-{start_str[6:]}"
        end_display   = f"{end_str[:4]}-{end_str[4:6]}-{end_str[6:]}"

        # JavaScript로 폼 값을 한 번에 설정하고 조회 실행
        await target_frame.evaluate(f"""() => {{
            // 1) 조회구분: 기간평균 (value=4)
            const rdoPeriod = document.getElementById('inqDvCd_p');
            if (rdoPeriod) {{ rdoPeriod.checked = true; rdoPeriod.click(); }}

            // 2) 고시회차: 최종 (value=1)
            const rdoFinal = document.getElementById('tmpPbldDvCd_1');
            if (rdoFinal) {{ rdoFinal.checked = true; }}

            // 3) 조회기간 설정
            const setVal = (id, v) => {{ const el = document.getElementById(id); if(el) el.value = v; }};
            setVal('inqStrDt',    '{start_str}');
            setVal('inqEndDt',    '{end_str}');
            setVal('tmpInqStrDt_p', '{start_display}');
            setVal('tmpInqEndDt_p', '{end_display}');
            setVal('pbldDvCd', '1');
        }}""")
        await page.wait_for_timeout(500)
        logger.info("[환율] 폼 설정 완료: %s ~ %s", start_display, end_display)

        # ── 4) 조회 실행
        await target_frame.evaluate(
            "pbk.foreign.rate.pbld.avg.search(document.forms['inqFrm'])"
        )
        await page.wait_for_timeout(4000)
        logger.info("[환율] 조회 완료")

        # ── 5) 엑셀 다운로드
        save_path = cache_dir / f"keb_avg_rate_{start_str}_{end_str}.xlsx"
        async with page.expect_download(timeout=30_000) as dl_info:
            await target_frame.evaluate(
                "pbk.foreign.rate.pbld.avg.doExcelDown('Y')"
            )
        download = await dl_info.value
        await download.save_as(str(save_path))
        logger.info("[환율] 엑셀 저장: %s (%d bytes)", save_path.name, save_path.stat().st_size)

        await context.close()
        await browser.close()

    return save_path


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
                rates[cur] = rate
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

    def _load_latest_cache(self) -> bool:
        """캐시 디렉터리에서 가장 최신 엑셀 파일을 로드."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        files = sorted(self.cache_dir.glob("keb_avg_rate_*.xlsx"), reverse=True)
        if not files:
            return False
        logger.info("[환율] 캐시 파일 로드: %s", files[0].name)
        self._rates = parse_keb_excel(files[0])
        m = re.search(r"keb_avg_rate_(\d{8})_(\d{8})", files[0].stem)
        if m:
            self._rate_meta = {"from": m.group(1), "to": m.group(2)}
        return bool(self._rates)

    async def refresh(self, ref_date: date = None, headless: bool = True) -> dict:
        """KEB에서 최신 평균환율 엑셀을 다운로드하고 파싱."""
        excel_path = await fetch_keb_excel(self.cache_dir, ref_date, headless)
        self._rates = parse_keb_excel(excel_path)
        m = re.search(r"keb_avg_rate_(\d{8})_(\d{8})", excel_path.stem)
        if m:
            self._rate_meta = {"from": m.group(1), "to": m.group(2)}
        return self._rates

    def get_rate(self, currency: str) -> dict:
        """특정 통화 환율 반환. 캐시가 없으면 오류 발생 (refresh() 먼저 호출 필요)."""
        if not self._rates:
            if not self._load_latest_cache():
                raise RuntimeError(
                    "환율 데이터가 없습니다. await fetcher.refresh()를 먼저 실행하세요."
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
    """HIRA 기준 해외약가 조정가 계산기."""

    FACTORY_RATIO = {
        "US": 0.74, "UK": 0.73, "DE": None,
        "FR": 0.77, "IT": 0.93, "CH": 0.73,
        "JP": 0.79, "CA": 0.81,
    }
    VAT_RATE = {
        "US": 0.00, "UK": 0.00, "DE": 0.19,
        "FR": 0.055, "IT": 0.10, "CH": 0.077,
        "JP": 0.10, "CA": 0.00,
    }
    DISTRIBUTION_MARGIN = {k: 0.0 for k in ["US","UK","DE","FR","IT","CH","JP","CA"]}
    CURRENCY = COUNTRY_CURRENCY

    def calculate_factory_price(self, country: str, listed_price: float,
                                source_type: str = None) -> float:
        ratio = self.FACTORY_RATIO.get(country)
        if ratio is None:   # 독일 특수 계산
            factory = listed_price / (1.19 * 1.0315) - 8.35 - 0.7
            return max(factory * (1 - 0.07), 0)
        if country == "CH" and source_type == "compendium":
            ratio = 0.65
        elif country == "FR" and source_type == "vidal":
            ratio = 0.65
        return listed_price * ratio

    def calculate_adjusted_price(self, country: str, listed_price: float,
                                  exchange_rate: float, source_type: str = None) -> dict:
        factory_price = self.calculate_factory_price(country, listed_price, source_type)
        vat    = self.VAT_RATE.get(country, 0)
        margin = self.DISTRIBUTION_MARGIN.get(country, 0)
        converted = int(factory_price * exchange_rate)
        adjusted  = int(converted * (1 + vat) * (1 + margin))
        return {
            "factory_price":      factory_price,
            "factory_price_krw":  converted,
            "vat_rate":           vat,
            "distribution_margin": margin,
            "adjusted_price_krw": adjusted,
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
