"""
이탈리아 AIFA(Agenzia Italiana del Farmaco) 약가 스크레이퍼

대상 페이지: https://www.aifa.gov.it/en/liste-farmaci-a-h
다운로드 파일: List of Class H medicinal products by trade name (CSV)

- 파일명에 날짜가 포함되어 업데이트마다 변경 → 페이지에서 동적 탐색
- 로그인 불필요 (공개 데이터)
- 이미 Prezzo Ex-factory(공장도출하가) 컬럼을 직접 제공
  → HIRA 조정가 계산 시 Prezzo Ex-factory를 factory_price 로 직접 사용
     (별도 비율 적용 불필요)
- 통화: EUR, VAT 10%

컬럼 구조:
  Principio Attivo           : 성분명 (활성 성분)
  Descrizione Gruppo         : 그룹 설명
  Denominazione e Confezione : 제품명 + 포장 (trade name + packaging)
  Prezzo al pubblico €       : 소비자가
  Prezzo Ex-factory €        : 공장도출하가 ← 이것을 local_price로 사용
  Prezzo massimo di cessione €: 최대 양도가
  Titolare AIC               : 허가 보유자 (제조사/마케팅 보유자)
  Codice AIC                 : 허가 코드
  Codice Gruppo Equivalenza  : 동등성 그룹 코드
  Metri cubi ossigeno        : 산소 세제곱미터 (대부분 공란)
"""

import csv
import io
import logging
import re
from pathlib import Path
from typing import Optional

import requests
from playwright.async_api import Page

from .base import BaseScraper

logger = logging.getLogger(__name__)

AIFA_PAGE_URL = "https://www.aifa.gov.it/en/liste-farmaci-a-h"
AIFA_DOC_BASE = "https://www.aifa.gov.it"

# Titolare AIC 컬럼에서 MSD 관련 패턴
MSD_COMPANY_PATTERNS = [
    "MSD", "MERCK SHARP", "MSDs",
    "MSD ITALIA", "MSD ANIMAL",
]


class ItAifaScraper(BaseScraper):
    COUNTRY       = "IT"
    CURRENCY      = "EUR"
    SOURCE_LABEL  = "AIFA Lista Classe H (Prezzo Ex-factory)"
    REQUIRES_LOGIN = False

    def __init__(
        self,
        cache_dir: Path = None,
        msd_only: bool = True,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.cache_dir = cache_dir or Path("data/foreign/it")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.msd_only = msd_only
        self._rows_cache: list[dict] = None   # 전체 CSV 행 캐시

    # ────────────────────────────────────────────────────────────────────────
    # 1) 페이지에서 최신 Class H by trade name CSV URL 탐색
    # ────────────────────────────────────────────────────────────────────────

    def _find_csv_url_requests(self) -> str:
        """
        AIFA 페이지 HTML을 requests로 직접 가져와 CSV URL 탐색.
        Playwright 불필요 (정적 HTML에 링크가 포함돼 있음).
        """
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        }
        resp = requests.get(AIFA_PAGE_URL, headers=headers, timeout=30)
        resp.raise_for_status()
        content = resp.text

        matches = re.findall(
            r'href="(/documents/[^"]+Classe_H_per_nome_commerciale[^"]+\.csv)"',
            content,
        )
        if not matches:
            matches = re.findall(
                r'"(https://www\.aifa\.gov\.it/documents/[^"]+Classe_H_per_nome_commerciale[^"]+\.csv)"',
                content,
            )
        if not matches:
            raise RuntimeError(
                "AIFA 페이지에서 Class H CSV 링크를 찾지 못했습니다.\n"
                f"페이지 URL: {AIFA_PAGE_URL}"
            )

        url = (AIFA_DOC_BASE + sorted(matches)[-1]
               if matches[0].startswith("/") else sorted(matches)[-1])
        logger.info("[IT] CSV URL: %s", url)
        return url

    async def _find_csv_url(self, _page: Page) -> str:
        """_find_csv_url_requests() 래퍼 (BaseScraper Page 인터페이스 호환)."""
        return self._find_csv_url_requests()

    # ────────────────────────────────────────────────────────────────────────
    # 2) CSV 다운로드
    # ────────────────────────────────────────────────────────────────────────

    def _download_csv(self, url: str) -> Path:
        """AIFA CSV를 requests로 직접 다운로드해 캐시에 저장."""
        fname = url.split("/")[-1].split("?")[0] or "classe_h_trade.csv"
        save_path = self.cache_dir / fname

        logger.info("[IT] CSV 다운로드: %s", url)
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": AIFA_PAGE_URL,
            "Accept": "text/csv,application/octet-stream,*/*",
        }
        resp = requests.get(url, headers=headers, timeout=60, stream=True)
        resp.raise_for_status()
        with open(save_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)

        logger.info("[IT] 저장: %s (%d bytes)", save_path.name, save_path.stat().st_size)
        return save_path

    def _load_latest_cache(self) -> Optional[Path]:
        """캐시 디렉터리에서 가장 최신 CSV 파일 경로 반환."""
        files = sorted(self.cache_dir.glob("Classe_H_per_nome_commerciale*.csv"), reverse=True)
        return files[0] if files else None

    # ────────────────────────────────────────────────────────────────────────
    # 3) CSV 파싱
    # ────────────────────────────────────────────────────────────────────────

    def _parse_csv(self, path: Path) -> list[dict]:
        """AIFA CSV → dict 리스트 반환. 인코딩은 UTF-8 우선, 실패 시 Latin-1."""
        for enc in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                raw = path.read_bytes().decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValueError(f"인코딩 판별 실패: {path.name}")

        # 구분자 자동 감지 (세미콜론 or 콤마)
        sample = raw[:2048]
        delimiter = ";" if sample.count(";") > sample.count(",") else ","

        reader = csv.DictReader(io.StringIO(raw), delimiter=delimiter)
        rows = [row for row in reader]
        logger.info("[IT] CSV 파싱: %d행 (구분자='%s', 인코딩=%s)", len(rows), delimiter, enc)
        return rows

    # ────────────────────────────────────────────────────────────────────────
    # 4) 검색 로직
    # ────────────────────────────────────────────────────────────────────────

    def _normalize(self, text: str) -> str:
        """소문자 + 공백/구두점 제거."""
        return re.sub(r"[\s\-\(\)\.\,]", "", str(text)).lower()

    def _filter_msd(self, rows: list[dict]) -> list[dict]:
        """Titolare AIC 컬럼에서 MSD 패턴 필터링."""
        # 컬럼명 유연 탐색 (헤더 공백 차이 대응)
        titolare_col = next(
            (k for k in (rows[0] if rows else {}) if "Titolare" in k or "titolare" in k),
            None,
        )
        if not titolare_col:
            logger.warning("[IT] Titolare AIC 컬럼을 찾지 못해 MSD 필터 생략")
            return rows

        filtered = [
            r for r in rows
            if any(pat.upper() in str(r.get(titolare_col, "")).upper()
                   for pat in MSD_COMPANY_PATTERNS)
        ]
        logger.info("[IT] MSD 필터 후: %d건 / 전체 %d건", len(filtered), len(rows))
        return filtered

    def _search_rows(self, rows: list[dict], query: str) -> list[dict]:
        """Denominazione e Confezione 컬럼에서 query 부분 일치 검색."""
        norm_q = self._normalize(query)

        # 컬럼명 유연 탐색
        denom_col = next(
            (k for k in (rows[0] if rows else {}) if "Denominazione" in k or "denominazione" in k),
            None,
        )
        ing_col = next(
            (k for k in (rows[0] if rows else {}) if "Principio" in k or "principio" in k),
            None,
        )

        search_keys = [c for c in [denom_col, ing_col] if c]
        if not search_keys:
            search_keys = list(rows[0].keys())[:3]

        matched = [
            r for r in rows
            if any(norm_q in self._normalize(r.get(k, "")) for k in search_keys)
        ]
        logger.info("[IT] '%s' 검색 결과: %d건", query, len(matched))
        return matched

    # ────────────────────────────────────────────────────────────────────────
    # 5) 결과 변환
    # ────────────────────────────────────────────────────────────────────────

    def _to_results(self, rows: list[dict]) -> list[dict]:
        """CSV 행 → 표준 결과 dict 리스트."""
        # 컬럼명 탐색 (헤더 공백·대소문자 차이 대응)
        def find_col(rows, *keywords):
            if not rows:
                return None
            return next(
                (k for k in rows[0] if any(kw.lower() in k.lower() for kw in keywords)),
                None,
            )

        denom_col    = find_col(rows, "Denominazione")
        principio_col = find_col(rows, "Principio")
        exfactory_col = find_col(rows, "Ex-factory", "Ex factory")
        titolare_col  = find_col(rows, "Titolare")
        codice_col    = find_col(rows, "Codice AIC")
        gruppo_col    = find_col(rows, "Descrizione Gruppo", "Gruppo")

        results = []
        for row in rows:
            # 공장도출하가 파싱 (예: "54,44" or "54.44")
            raw_price = str(row.get(exfactory_col, "") if exfactory_col else "")
            raw_price = raw_price.replace(",", ".")
            try:
                local_price = float(raw_price) if raw_price.replace(".", "").isdigit() else None
            except ValueError:
                local_price = None

            # Denominazione e Confezione → product_name + dosage_strength 분리
            denom_raw = str(row.get(denom_col, "") if denom_col else "")

            results.append({
                "product_name":    denom_raw,
                "ingredient":      str(row.get(principio_col, "") if principio_col else ""),
                "dosage_strength": denom_raw,   # 포장 규격 포함된 전체 문자열
                "dosage_form":     str(row.get(gruppo_col, "") if gruppo_col else ""),
                "package_unit":    "",
                "local_price":     local_price,
                "source_url":      AIFA_PAGE_URL,
                "extra": {
                    "company":    str(row.get(titolare_col, "") if titolare_col else ""),
                    "codice_aic": str(row.get(codice_col, "") if codice_col else ""),
                    "raw":        dict(row),
                },
            })
        return results

    # ────────────────────────────────────────────────────────────────────────
    # 6) BaseScraper 인터페이스 구현
    # ────────────────────────────────────────────────────────────────────────

    async def search(self, query: str, page: Page) -> list[dict]:
        """
        AIFA Class H CSV에서 약제명으로 검색.
        1. 캐시된 CSV가 있으면 재다운로드 없이 사용
        2. 캐시 없으면 자동 다운로드
        3. msd_only=True이면 MSD 제품만 반환
        """
        if self._rows_cache is None:
            cached = self._load_latest_cache()
            if cached:
                logger.info("[IT] 캐시 로드: %s", cached.name)
                self._rows_cache = self._parse_csv(cached)
            else:
                logger.info("[IT] 캐시 없음 — CSV 다운로드 시작")
                url = await self._find_csv_url(page)
                csv_path = self._download_csv(url)
                self._rows_cache = self._parse_csv(csv_path)

        rows = self._rows_cache

        if self.msd_only:
            rows = self._filter_msd(rows)
            if not rows:
                logger.warning("[IT] MSD 제품이 없습니다.")
                return []

        matched = self._search_rows(rows, query)
        if not matched:
            logger.info("[IT] '%s' 검색 결과 없음", query)
            return []

        return self._to_results(matched)

    async def refresh(self, page: Page) -> None:
        """최신 CSV를 재다운로드해 캐시 갱신."""
        url = await self._find_csv_url(page)
        csv_path = self._download_csv(url)
        self._rows_cache = self._parse_csv(csv_path)
        logger.info("[IT] CSV 캐시 갱신 완료")
