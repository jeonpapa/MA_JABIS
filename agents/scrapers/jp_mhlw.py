"""
일본 후생노동성(MHLW) 약가 스크레이퍼

대상 페이지: https://www.mhlw.go.jp/topics/2025/04/tp20250401-01.html
다운로드 파일:
  _01.xlsx → 内用薬 (내용약 / 경구약)
  _02.xlsx → 注射薬 (주사약)
  _03.xlsx → 外用薬 (외용약)

파일명 날짜 부분(예: tp20260318)은 업데이트마다 변경되므로
페이지에서 동적으로 _01~_03 링크를 탐색한다.

검색 방법:
  - 대쉬보드에서 영문 약제명 입력 → EN_JP_NAME_MAP으로 일본어 변환 후 검색
  - 매핑 없으면 영문 그대로 부분 일치 시도
  - 회사명에 MSD 패턴이 포함된 제품만 필터 (선택적)

로그인 불필요 (공개 사이트)
"""

import logging
import re
import unicodedata
from pathlib import Path

import pandas as pd
from playwright.async_api import Page

from .base import BaseScraper

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────────────────
# 상수
# ────────────────────────────────────────────────────────────────────────────

MHLW_BASE_URL   = "https://www.mhlw.go.jp"
MHLW_PAGE_URL   = "https://www.mhlw.go.jp/topics/2025/04/tp20250401-01.html"

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
    "company":         ["会社名", "製造会社", "メーカー", "製造販売業者"],
    "ingredient":      ["成分名", "一般名", "成分・規格"],
    "category":        ["種別", "分類"],
}


class JpMhlwScraper(BaseScraper):
    COUNTRY      = "JP"
    CURRENCY     = "JPY"
    SOURCE_LABEL = "後生労働省 薬価基準収載品目リスト"
    REQUIRES_LOGIN = False

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

    # ────────────────────────────────────────────────────────────────────────
    # 1) 페이지에서 _01/_02/_03 엑셀 링크 동적 탐색
    # ────────────────────────────────────────────────────────────────────────

    async def _find_excel_urls(self, page: Page) -> dict[str, str]:
        """
        MHLW 페이지에서 内用薬/注射薬/外用薬 엑셀 URL을 동적으로 탐색.
        반환: {"内用薬": url, "注射薬": url, "外用薬": url}
        """
        await page.goto(self.page_url, wait_until="networkidle", timeout=30_000)
        content = await page.content()

        # 페이지에서 xlsx 링크 전체 추출
        all_xlsx = re.findall(r'href="(/topics/[^"]+\.xlsx)"', content)
        logger.info("[JP] 페이지에서 발견된 xlsx 링크 수: %d", len(all_xlsx))

        result = {}
        for category, suffix in CATEGORY_SUFFIX.items():
            # suffix (_01.xlsx / _02.xlsx / _03.xlsx)로 매칭
            matched = [u for u in all_xlsx if u.endswith(suffix)]
            if not matched:
                logger.warning("[JP] %s 엑셀 링크 없음 (suffix=%s)", category, suffix)
                continue
            # 여러 개면 가장 최신 (URL에 포함된 날짜 기준 정렬 → 마지막)
            matched_sorted = sorted(matched)
            url = MHLW_BASE_URL + matched_sorted[-1]
            result[category] = url
            logger.info("[JP] %s → %s", category, url)

        return result

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

        resp = requests.get(url, timeout=60, stream=True)
        resp.raise_for_status()
        with open(save_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)

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

    def _load_all_from_cache(self) -> pd.DataFrame:
        """캐시된 엑셀 파일들을 모두 읽어 병합 DataFrame 반환."""
        files = sorted(self.cache_dir.glob("*.xlsx"))
        if not files:
            raise FileNotFoundError(
                f"캐시된 엑셀이 없습니다: {self.cache_dir}\n"
                "먼저 download_all()을 실행하세요."
            )

        # _01/_02/_03 카테고리 파일만 사용 (참고자료 _04~_07 제외)
        category_map = {"_01": "内用薬", "_02": "注射薬", "_03": "外用薬"}
        dfs = []
        for path in files:
            cat_key = next(
                (k for k in category_map if path.name.endswith(f"{k}.xlsx")), None
            )
            if cat_key is None:
                continue
            try:
                df = self._parse_excel(path, category_map[cat_key])
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
        영문 검색어에 대응하는 일본어 검색 키워드 목록 반환.
        1) EN_JP_NAME_MAP에 매핑이 있으면 일본어 이름 사용
        2) 항상 원본 영문 검색어도 포함 (영문 표기가 있을 수 있음)
        """
        norm = self._normalize(query)
        keywords = [query]   # 영문 원본
        if norm in EN_JP_NAME_MAP:
            keywords.insert(0, EN_JP_NAME_MAP[norm])
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
        # 캐시 로드 (또는 다운로드)
        if self._df_cache is None:
            try:
                self._df_cache = self._load_all_from_cache()
            except FileNotFoundError:
                logger.info("[JP] 캐시 없음 — 엑셀 다운로드 시작")
                await self.download_all(page)
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
