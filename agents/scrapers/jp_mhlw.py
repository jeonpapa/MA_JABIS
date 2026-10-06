"""
일본 후생노동성(MHLW) 약가 스크레이퍼

대상 페이지: 薬価基準収載品目リスト (/topics/YYYY/MM/tpYYYYMMDD-01.html — 공시마다 새 페이지)
  탐색 순서(_candidate_page_urls): env JP_MHLW_PAGE_URL → 캐시 파일명에서 유도한 페이지 → MHLW_PAGE_URL 상수
다운로드 파일:
  _01.xlsx → 内用薬 (내용약 / 경구약)
  _02.xlsx → 注射薬 (주사약)
  _03.xlsx → 外用薬 (외용약)

파일명 날짜 부분(예: tp20260318)은 업데이트마다 변경되므로
페이지에서 동적으로 _01~_03 링크를 탐색하고(가장 최신 날짜), 캐시는 카테고리별 최신 1본만 병합한다.
캐시 TTL 90일(XLSX_CACHE_TTL_DAYS) — 초과 시 갱신 시도, 실패/불가는 stale_cache 경고(BaseScraper.warnings).

검색 방법:
  - 대쉬보드에서 영문 약제명(브랜드 또는 INN) 입력 → _resolve_jp_name 이 product_alias_map 의 PMDA 표기,
    EN_JP_NAME_MAP(alias 역조회 포함) 순으로 일본어 브랜드 표기로 변환 후 검색 (pembrolizumab → キイトルーダ)
  - 매핑 없으면 영문 그대로 부분 일치 시도
  - 회사명에 MSD 패턴이 포함된 제품만 필터 (선택적)

로그인 불필요 (공개 사이트)
"""

import logging
import os
import re
import time
import unicodedata
from pathlib import Path

import pandas as pd
from playwright.async_api import Page

from agents.db import drug_aliases

from .base import BaseScraper
from ._proxy import playwright_proxy, proxy_from_env, requests_proxies

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────────────────
# 상수
# ────────────────────────────────────────────────────────────────────────────

MHLW_BASE_URL   = "https://www.mhlw.go.jp"
# 마지막 수단 상수. 실제 탐색 순서는 _candidate_page_urls(): env JP_MHLW_PAGE_URL → 캐시 파일명(tpYYYYMMDD)에서 유도한
# 같은 패턴의 페이지(/topics/YYYY/MM/tpYYYYMMDD-01.html) → 이 상수. 2026-10 감사: 2025/04 고정인데 캐시는 2026-03 파일.
MHLW_PAGE_URL   = "https://www.mhlw.go.jp/topics/2025/04/tp20250401-01.html"
_TP_DATE = re.compile(r"tp(\d{4})(\d{2})(\d{2})-01")

# 약제 카테고리별 파일 suffix 패턴
CATEGORY_SUFFIX = {
    "内用薬": "_01.xlsx",
    "注射薬": "_02.xlsx",
    "外用薬": "_03.xlsx",
}

# MSD 관련 회사명 패턴 (일본어 표기 포함)
# 실제 MHLW 엑셀의 メーカー名 컬럼에는 전각 "ＭＳＤ" 가 사용됨
MSD_COMPANY_PATTERNS = [
    "ＭＳＤ",          # 전각 (실제 엑셀 표기)
    "MSD",             # 반각
    "エムエスディ", "萬有製薬", "万有制药",
    "Merck Sharp", "MSD株式会社",
]

# 영문 약제명 → 일본어 카타카나 매핑 테이블
# 사용자가 약제 추가 시 여기에 등록하거나, runtime에 add_name_mapping() 사용
EN_JP_NAME_MAP: dict[str, str] = {
    # ── MSD Japan 제품 (MHLW 엑셀 내 確認된 품명) ──────────────────────────
    "keytruda":     "キイトルーダ",   # ペムブロリズマブ, 注射薬
    "januvia":      "ジャヌビア",     # シタグリプチン, 内用薬
    "janumet":      "ジャヌメット",   # シタグリプチン+メトホルミン, 内用薬
    "gardasil9":    "ガーダシル9",    # HPVワクチン, 注射薬
    "lynparza":     "リムパーザ",     # オラパリブ, 内用薬
    "lagevrio":     "ラゲブリオ",     # モルヌピラビル, 内用薬
    "welireg":      "ウェリレグ",     # ベルズチファン, 内用薬
    "winrevair":    "ウィンレバイル", # ソタテルセプト, 注射薬
    "cozaar":       "ニューロタン",   # ロサルタン, 内用薬
    "hyzaar":       "プレミネント",   # ロサルタン+ヒドロクロロチアジド, 内用薬
    "singulair":    "シングレア",     # モンテルカスト, 内用薬
    "proscar":      "プロスカー",     # フィナステリド, 内用薬
    "propecia":     "プロペシア",     # フィナステリド, 内用薬
    "maxalt":       "マクサルト",     # リザトリプタン, 内用薬
    "arcoxia":      "アルコキシア",   # エトリコキシブ, 内用薬
    "emend":        "イメンド",       # アプレピタント, 内用薬
    "zafgen":       "ザフゲン",
    "bridion":      "ブリディオン",   # スガマデクス, 注射薬
    "zepatier":     "ジェパタ",       # エルバスビル+グラゾプレビル, 内用薬
    "isentress":    "アイセントレス", # ラルテグラビル, 内用薬
    "victrelis":    "ビクトレリス",   # ボセプレビル, 内用薬
    "noxafil":      "ノクサフィル",   # ポサコナゾール, 内用薬
    "cancidas":     "カンサイダス",   # カスポファンギン, 注射薬
    "invanz":       "インバンツ",     # エルタペネム, 注射薬
    "primaxin":     "チエナム",       # イミペネム+シラスタチン, 注射薬
    "atozet":       "アトーゼット",   # エゼチミブ+アトルバスタチン, 内用薬
    "zetia":        "ゼチーア",       # エゼチミブ, 内用薬
    "prevymis":     "プレバイミス",   # レテルモビル, 内用薬 + 注射薬
}

# 컬럼명 후보 (MHLW 엑셀 버전마다 다를 수 있음)
COL_CANDIDATES = {
    "product_name":    ["品名", "製品名", "薬品名"],
    "dosage_strength": ["規格単位", "規格・単位", "規格", "単位"],
    "local_price":     ["薬価", "薬価（円）", "価格（円）", "価格"],
    "company":         ["会社名", "製造会社", "メーカー名", "メーカー", "製造販売業者"],   # 2026-03 공시 실측: "メーカー名"
    "ingredient":      ["成分名", "一般名", "成分・規格"],
    "category":        ["種別", "分類"],
}


class JpMhlwScraper(BaseScraper):
    COUNTRY      = "JP"
    CURRENCY     = "JPY"
    SOURCE_LABEL = "後生労働省 薬価基準収載品目リスト"
    REQUIRES_LOGIN = False
    PROXY_ENV_KEYS = ("JP_MHLW_PROXY", "FOREIGN_PROXY")   # fly 데이터센터 IP 간헐 차단 — residential proxy(설정 프로브가 확인)
    # 캐시 xlsx 유효기간(일). 薬価基準 은 4월 전면개정 + 분기 신약 수재(2·5·8·11월) — 90일이면 한 수재 주기 이상 뒤처지지
    # 않는다. 초과 시 search() 가 갱신을 시도하고, 불가(page 없음)·실패면 구 파일로 답하되 warnings 에 stale_cache 를 남긴다.
    XLSX_CACHE_TTL_DAYS = 90

    def __init__(
        self,
        page_url: str = MHLW_PAGE_URL,
        cache_dir: Path = None,
        msd_only: bool = False,
        **kwargs,
    ):
        """
        page_url: MHLW 약가 수재품목 페이지 URL
        cache_dir: 다운로드된 엑셀 저장 경로
        msd_only: True이면 MSD 제품만 반환
        """
        super().__init__(**kwargs)
        self.page_url = page_url
        self.cache_dir = cache_dir or Path("data/foreign/jp")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.msd_only = msd_only
        self._df_cache: pd.DataFrame = None   # 전체 병합 DataFrame 캐시

    def _browser_proxy(self):
        """mhlw.go.jp 페이지 로드용 residential proxy(JP_MHLW_PROXY, 없으면 FOREIGN_PROXY)."""
        return playwright_proxy(proxy_from_env(*self.PROXY_ENV_KEYS))

    # ────────────────────────────────────────────────────────────────────────
    # 1) 페이지에서 _01/_02/_03 엑셀 링크 동적 탐색
    # ────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _pick_latest_xlsx_urls(content: str) -> dict[str, str]:
        """페이지 HTML → 카테고리별 **가장 최신 날짜(tpYYYYMMDD)** 의 xlsx 절대 URL. 링크 순서가 아니라 날짜로 고른다."""
        all_xlsx = re.findall(r'href="(/topics/[^"]+\.xlsx)"', content)
        result: dict[str, str] = {}
        for category, suffix in CATEGORY_SUFFIX.items():
            matched = [u for u in all_xlsx if u.endswith(suffix)]
            if not matched:
                continue

            def _date(u: str):
                m = _TP_DATE.search(u)
                return (m.group(1), m.group(2), m.group(3)) if m else ("", "", "")
            best = sorted(matched, key=lambda u: (_date(u), u))[-1]
            result[category] = MHLW_BASE_URL + best
        return result

    def _page_url_from_cache(self) -> str | None:
        """캐시 최신 파일명 tpYYYYMMDD-01_0N.xlsx → 같은 공시의 페이지 /topics/YYYY/MM/tpYYYYMMDD-01.html (MHLW 관례)."""
        latest = self._latest_files_by_category()
        if not latest:
            return None
        names = sorted(p.name for p in latest.values())
        m = _TP_DATE.search(names[-1])
        if not m:
            return None
        y, mo, d = m.groups()
        return f"{MHLW_BASE_URL}/topics/{y}/{mo}/tp{y}{mo}{d}-01.html"

    def _candidate_page_urls(self) -> list[str]:
        """탐색 순서: env JP_MHLW_PAGE_URL(운영자가 최신 공시 페이지를 안다) → 캐시에서 유도 → 생성자/상수. 중복 제거."""
        out: list[str] = []
        for u in (os.environ.get("JP_MHLW_PAGE_URL", "").strip() or None, self._page_url_from_cache(), self.page_url):
            if u and u not in out:
                out.append(u)
        return out

    async def _find_excel_urls(self, page: Page) -> dict[str, str]:
        """
        MHLW 페이지에서 内用薬/注射薬/外用薬 엑셀 URL을 동적으로 탐색 — 후보 페이지를 차례로 열어 처음 링크가 나오는 곳을 쓴다.
        반환: {"内用薬": url, "注射薬": url, "外用薬": url}. 어느 후보에도 없으면 RuntimeError (빈 결과로 위장하지 않는다).
        """
        last_err: Exception | None = None
        for url in self._candidate_page_urls():
            try:
                await page.goto(url, wait_until="networkidle", timeout=30_000)
                content = await page.content()
            except Exception as e:
                last_err = e
                logger.warning("[JP] 페이지 열기 실패 %s: %s", url, e)
                continue
            result = self._pick_latest_xlsx_urls(content)
            logger.info("[JP] %s → xlsx 링크 %d개(카테고리 %d)", url, content.count(".xlsx"), len(result))
            if result:
                for category, u in result.items():
                    logger.info("[JP] %s → %s", category, u)
                return result
        raise RuntimeError(f"MHLW 약가 페이지에서 xlsx 링크를 찾지 못함 (후보 {self._candidate_page_urls()})"
                           + (f" — 마지막 오류 {type(last_err).__name__}: {last_err}" if last_err else ""))

    # ────────────────────────────────────────────────────────────────────────
    # 2) 엑셀 다운로드
    # ────────────────────────────────────────────────────────────────────────

    def _download_excel_direct(self, url: str, category: str) -> Path:
        """
        직접 파일 URL을 requests로 다운로드 (xlsx 직링크용).
        Playwright page.goto()는 파일 다운로드 URL에서 오류를 일으키므로
        공개 파일은 requests로 직접 받는다.
        """
        import requests

        logger.info("[JP] 다운로드: %s (%s)", url, category)
        fname = url.split("/")[-1] or f"mhlw_{category}.xlsx"
        save_path = self.cache_dir / fname

        # fly 데이터센터 IP 는 mhlw 다운로드가 간헐 차단/스로틀됨 → residential proxy + 재시도.
        # 재시도마다 새 exit IP(sticky sessid 재생성)로 차단 IP 회피.
        last_exc = None
        for attempt in range(3):
            proxies = requests_proxies(proxy_from_env(*self.PROXY_ENV_KEYS))
            try:
                resp = requests.get(url, timeout=60, stream=True, proxies=proxies)
                resp.raise_for_status()
                with open(save_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=65536):
                        f.write(chunk)
                break
            except Exception as e:
                last_exc = e
                logger.warning("[JP] 다운로드 실패(%d/3) %s: %s", attempt + 1, url, e)
        else:
            raise last_exc if last_exc else RuntimeError(f"[JP] 다운로드 실패: {url}")

        logger.info("[JP] 저장 완료: %s (%s bytes)", save_path.name, save_path.stat().st_size)
        return save_path

    async def download_all(self, page: Page) -> dict[str, Path]:
        """内用薬/注射薬/外用薬 3개 파일을 모두 다운로드."""
        urls = await self._find_excel_urls(page)
        paths = {}
        for category, url in urls.items():
            paths[category] = self._download_excel_direct(url, category)
        return paths

    # ────────────────────────────────────────────────────────────────────────
    # 3) 엑셀 파싱
    # ────────────────────────────────────────────────────────────────────────

    def _parse_excel(self, path: Path, category: str) -> pd.DataFrame:
        """단일 엑셀 파일을 파싱해 정규화된 DataFrame 반환."""
        logger.info("[JP] 파싱: %s", path.name)
        xl = pd.ExcelFile(path)

        for sheet in xl.sheet_names:
            for skip in range(8):  # 헤더가 최대 7행까지 내려올 수 있음
                try:
                    df = pd.read_excel(
                        path, sheet_name=sheet, header=skip, dtype=str
                    )
                    df.columns = [str(c).strip() for c in df.columns]
                    cols = " ".join(df.columns)
                    # 최소 식별 조건: 薬価 또는 品名 컬럼 존재
                    if "薬価" in cols or "品名" in cols:
                        df = df.dropna(how="all").fillna("")
                        df["_category"] = category   # 약제 분류 추가
                        logger.info(
                            "[JP] 유효 시트 '%s' (헤더=%d행, 데이터=%d행)",
                            sheet, skip, len(df),
                        )
                        return df
                except Exception:
                    continue

        raise ValueError(f"유효한 시트를 찾지 못함: {path.name}")

    def _map_columns(self, df: pd.DataFrame) -> dict[str, str]:
        """DataFrame 컬럼명을 내부 키로 매핑."""
        mapping = {}
        for key, candidates in COL_CANDIDATES.items():
            for cand in candidates:
                if cand in df.columns:
                    mapping[key] = cand
                    break
        missing = [k for k in COL_CANDIDATES if k not in mapping]
        if missing:
            logger.debug("[JP] 컬럼 매핑 실패: %s", missing)
        return mapping

    def _latest_files_by_category(self) -> dict[str, Path]:
        """캐시 디렉터리의 카테고리(_01/_02/_03)별 **최신 공시본 1개** — 파일명 날짜(tpYYYYMMDD) 순, 같으면 mtime.
        갱신 뒤 구 공시본이 남아도 병합되지 않는다(이전엔 전부 concat → 같은 품목이 구 가격으로 중복)."""
        out: dict[str, Path] = {}
        for category, suffix in CATEGORY_SUFFIX.items():
            files = [p for p in self.cache_dir.glob(f"*{suffix}") if p.is_file()]
            if not files:
                continue

            def _key(p: Path):
                m = _TP_DATE.search(p.name)
                return ((m.group(1), m.group(2), m.group(3)) if m else ("", "", ""), p.stat().st_mtime)
            out[category] = sorted(files, key=_key)[-1]
        return out

    def _cache_age_days(self) -> float | None:
        """최신 공시본 중 가장 오래된 파일의 마지막 다운로드 이후 경과일. 캐시 없으면 None."""
        latest = self._latest_files_by_category()
        if not latest:
            return None
        return max(0.0, max((time.time() - p.stat().st_mtime) / 86400.0 for p in latest.values()))

    def _load_all_from_cache(self) -> pd.DataFrame:
        """캐시된 엑셀(카테고리별 최신 1본)을 읽어 병합 DataFrame 반환."""
        latest = self._latest_files_by_category()
        if not latest:
            raise FileNotFoundError(
                f"캐시된 엑셀이 없습니다: {self.cache_dir}\n"
                "먼저 download_all()을 실행하세요."
            )

        dfs = []
        for category, path in latest.items():
            try:
                df = self._parse_excel(path, category)
                dfs.append(df)
            except Exception as e:
                logger.warning("[JP] 파싱 실패 (%s): %s", path.name, e)

        if not dfs:
            raise ValueError("파싱 가능한 엑셀 파일이 없습니다.")

        merged = pd.concat(dfs, ignore_index=True)
        logger.info("[JP] 전체 병합 데이터: %d행", len(merged))
        return merged

    # ────────────────────────────────────────────────────────────────────────
    # 4) 검색 로직
    # ────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _normalize(text: str) -> str:
        """비교용 정규화: 소문자, 전각→반각, 공백·구두점 제거."""
        text = unicodedata.normalize("NFKC", str(text))
        text = re.sub(r"[\s・\-・]", "", text)
        return text.lower()

    def _resolve_jp_name(self, query: str) -> list[str]:
        """
        영문 검색어(브랜드 **또는 INN**)에 대응하는 일본어 검색 키워드 목록 반환 — 두 번째 손관리 표 없이 기존 자원으로 푼다.
        1) product_alias_map.agency_brand_overrides["PMDA"] (drug_aliases.agency_brand) — 권위 표기
        2) EN_JP_NAME_MAP — 입력 자체 + drug_aliases.aliases(query)(slug·inn·brand_aliases, 없으면 BRAND_TO_MOLECULE 역조회)
           로 역조회. pembrolizumab → {keytruda, pembrolizumab} → キイトルーダ
        3) 항상 원본 영문 검색어도 포함 (영문 표기가 있을 수 있음)
        2026-10 감사: 1) 2) 없이 브랜드 키만 있어 INN 검색(백필 잡의 'alias inn 우선')이 39/39 0건이었다.
        """
        keywords: list[str] = []

        def add(k):
            k = (k or "").strip()
            if k and k not in keywords:
                keywords.append(k)

        try:
            add(drug_aliases.agency_brand(query, "PMDA"))
        except Exception as e:                       # alias DB 문제가 검색을 막으면 안 된다
            logger.debug("[JP] PMDA 표기 조회 실패: %s", e)
        try:
            names = [query] + list(drug_aliases.aliases(query))
        except Exception as e:
            logger.debug("[JP] alias 조회 실패: %s", e)
            names = [query]
        for n in names:
            add(EN_JP_NAME_MAP.get(self._normalize(n)))
        add(query)   # 영문 원본
        return keywords

    def _search_df(self, df: pd.DataFrame, query: str) -> pd.DataFrame:
        """DataFrame에서 약제명으로 행 검색."""
        col_map = self._map_columns(df)
        name_col = col_map.get("product_name")
        ingredient_col = col_map.get("ingredient")

        keywords = self._resolve_jp_name(query)
        logger.info("[JP] 검색 키워드: %s", keywords)

        search_cols = []
        if name_col:
            search_cols.append(name_col)
        if ingredient_col:
            search_cols.append(ingredient_col)
        if not search_cols:
            # 컬럼 특정 불가 → 전체 텍스트 검색
            search_cols = list(df.columns[:5])

        def row_matches(row) -> bool:
            row_str = self._normalize(" ".join(str(row[c]) for c in search_cols if c in row.index))
            return any(self._normalize(kw) in row_str for kw in keywords)

        mask = df.apply(row_matches, axis=1)
        return df[mask].copy()

    def _filter_msd(self, df: pd.DataFrame) -> pd.DataFrame:
        """MSD 제품만 필터링."""
        col_map = self._map_columns(df)
        company_col = col_map.get("company")

        if company_col:
            mask = df[company_col].apply(
                lambda v: any(pat.upper() in str(v).upper() for pat in MSD_COMPANY_PATTERNS)
            )
        else:
            # 회사명 컬럼 불명확 → 전체 행 텍스트에서 검색
            mask = df.apply(
                lambda row: any(
                    pat.upper() in " ".join(str(v) for v in row.values).upper()
                    for pat in MSD_COMPANY_PATTERNS
                ),
                axis=1,
            )
        filtered = df[mask].copy()
        logger.info("[JP] MSD 필터 후: %d건", len(filtered))
        return filtered

    # ────────────────────────────────────────────────────────────────────────
    # 5) 결과 변환
    # ────────────────────────────────────────────────────────────────────────

    def _to_results(self, df: pd.DataFrame) -> list[dict]:
        """검색된 DataFrame을 결과 dict 리스트로 변환."""
        col_map = self._map_columns(df)

        def g(row, key):
            col = col_map.get(key, "")
            return str(row.get(col, "")).strip() if col else ""

        results = []
        for _, row in df.iterrows():
            price_str = g(row, "local_price")
            try:
                # "1,234.56" 형식 처리
                local_price = float(re.sub(r"[^\d.]", "", price_str)) if price_str else None
            except ValueError:
                local_price = None

            results.append({
                "product_name":    g(row, "product_name"),
                "ingredient":      g(row, "ingredient"),
                "dosage_strength": g(row, "dosage_strength"),  # 용량/규격 포함
                "dosage_form":     row.get("_category", ""),   # 内用薬/注射薬/外用薬
                "package_unit":    "",
                "local_price":     local_price,
                "source_url":      self.page_url,
                "extra": {
                    "company":  g(row, "company"),
                    "category": row.get("_category", ""),
                    "raw":      {k: str(v) for k, v in row.items()
                                 if not k.startswith("_")},
                },
            })
        return results

    # ────────────────────────────────────────────────────────────────────────
    # 6) BaseScraper 인터페이스 구현
    # ────────────────────────────────────────────────────────────────────────

    async def search(self, query: str, page: Page) -> list[dict]:
        """
        영문 약제명으로 MHLW 약가 데이터에서 검색.
        1. 캐시된 엑셀이 있으면 재다운로드 없이 사용
        2. 캐시가 없으면 자동 다운로드
        3. msd_only=True이면 MSD 제품만 반환
        """
        # 캐시 로드 (또는 다운로드). TTL 초과면 갱신 시도 — 실패/불가(page 없음)는 구 파일로 답하되 stale_cache 경고.
        if self._df_cache is None:
            age = self._cache_age_days()
            if age is None:
                logger.info("[JP] 캐시 없음 — 엑셀 다운로드 시작")
                await self.download_all(page)
            elif age > self.XLSX_CACHE_TTL_DAYS:
                await self._refresh_stale(page, age)
            self._df_cache = self._load_all_from_cache()

        df = self._df_cache

        # MSD 필터 (선택)
        if self.msd_only:
            df = self._filter_msd(df)
            if df.empty:
                logger.warning("[JP] MSD 제품이 없습니다.")
                return []

        # 약제명 검색
        matched = self._search_df(df, query)
        if matched.empty:
            logger.info("[JP] '%s' 검색 결과 없음", query)
            return []

        return self._to_results(matched)

    async def _refresh_stale(self, page: Page | None, age: float) -> None:
        """TTL 을 넘긴 캐시 갱신 시도. page 가 없으면(오프라인 호출) 시도 없이 경고만, 실패도 경고 — 구 파일로 계속 답한다.
        갱신이 성공하면 _latest_files_by_category 가 새 공시본을 고른다(구 파일은 병합되지 않는다)."""
        latest = ", ".join(sorted(p.name for p in self._latest_files_by_category().values()))
        if page is None:
            msg = (f"MHLW xlsx 캐시 {age:.0f}일 경과(TTL {self.XLSX_CACHE_TTL_DAYS}일) — 브라우저 페이지 없이 호출돼 갱신 불가, "
                   f"구 파일 사용: {latest}")
            logger.warning("[JP] %s", msg)
            self.warnings.append({"warning_class": "stale_cache", "channel": "mhlw_xlsx", "message": msg})
            return
        logger.info("[JP] 캐시 만료(%.0f일 > TTL %d일) — 최신 공시 다운로드 시도", age, self.XLSX_CACHE_TTL_DAYS)
        try:
            await self.download_all(page)
        except Exception as e:
            msg = (f"MHLW xlsx 갱신 실패({type(e).__name__}: {str(e)[:160]}) — {age:.0f}일 된 구 파일 사용: {latest}")
            logger.warning("[JP] %s", msg)
            self.warnings.append({"warning_class": "stale_cache", "channel": "mhlw_xlsx", "message": msg})

    async def refresh(self, page: Page) -> None:
        """최신 엑셀을 재다운로드해 캐시 갱신."""
        await self.download_all(page)
        self._df_cache = self._load_all_from_cache()
        logger.info("[JP] 엑셀 캐시 갱신 완료")

    # ────────────────────────────────────────────────────────────────────────
    # 유틸
    # ────────────────────────────────────────────────────────────────────────

    @staticmethod
    def add_name_mapping(en_name: str, jp_name: str) -> None:
        """영문→일본어 이름 매핑을 전역 테이블에 추가."""
        EN_JP_NAME_MAP[en_name.lower().strip()] = jp_name
        logger.info("[JP] 이름 매핑 추가: '%s' → '%s'", en_name, jp_name)
