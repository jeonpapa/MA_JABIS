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
                # KEB 는 JPY 를 "100엔당" 으로 고시 (예: 924.75 = 100 JPY 기준 KRW).
                # local_price * rate 가 KRW 가 되려면 per-1-unit 로 정규화 필요.
                # raw_cur 에 "100" 이 있으면 divisor=100 적용.
                per_unit_divisor = 100.0 if "100" in raw_cur else 1.0
                rates[cur] = rate / per_unit_divisor
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

    # source-specific 오버라이드 (공시가 체계가 다른 경우)
    SOURCE_OVERRIDES = {
        "aifa_exfactory": 1.0,    # IT Class H: 이미 ex-factory
        "ch_compendium":  0.65,   # CH Compendium
        "fr_vidal":       0.65,   # FR Vidal (BDPM 사용 시는 SOURCE_TYPE=None → 기본 0.77)
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
        # JPY per-100 safeguard (KEB 레거시 캐시 호환)
        if country == "JP" and exchange_rate and exchange_rate > 100:
            exchange_rate = exchange_rate / 100.0

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
