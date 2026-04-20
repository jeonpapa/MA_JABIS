"""
이탈리아 AIFA(Agenzia Italiana del Farmaco) 약가 스크레이퍼

대상 페이지: https://www.aifa.gov.it/en/liste-farmaci-a-h
다운로드 파일:
  - Classe_A_per_nome_commerciale_*.csv   — Class A (외래 급여 소매약)
  - Classe_H_per_nome_commerciale_*.csv   — Class H (병원 전용)

AIFA 분류 체계:
  - Class A : 외래 환자 처방약, SSN 급여. Descrizione Gruppo 에 대부분 "USO ORALE"
              → 경구제 위주. Prezzo al pubblico(소비자가) 만 공개, Ex-factory 비공개.
  - Class H : 병원 전용 (주사제·항암제 등). Descrizione Gruppo 에 "USO PARENTERALE"
              → 주사제 위주. Prezzo Ex-factory(공장도가) 공개.
  - Class C : 비급여 (본 스크레이퍼 범위 밖).

배경:
  - 로그인 불필요 (공개 데이터)
  - 파일명에 날짜가 포함되어 업데이트마다 변경 → 페이지에서 동적 탐색
  - 통화: EUR, VAT 10%
  - 제형(form_type)은 detect_form() 이 Descrizione Gruppo + Denominazione 텍스트로
    oral/injection 을 판정

가격 선택 규칙:
  - Class H 행 → Prezzo Ex-factory 사용, SOURCE_TYPE="aifa_exfactory"
                 (HIRA factory_ratio=1.0, ex-factory 직접 사용)
  - Class A 행 → Prezzo al pubblico 사용, SOURCE_TYPE=None
                 (HIRA 기본 factory_ratio + VAT + 유통마진 공식)

컬럼 구조 (Class H):
  Principio Attivo / Descrizione Gruppo / Denominazione e Confezione /
  Prezzo al pubblico € / Prezzo Ex-factory € / Prezzo massimo di cessione € /
  Titolare AIC / Codice AIC / Codice Gruppo Equivalenza / Metri cubi ossigeno

컬럼 구조 (Class A — Ex-factory 컬럼 없음):
  Principio Attivo / Descrizione Gruppo / Denominazione e Confezione /
  Prezzo al pubblico € / Titolare AIC / AIC / Codice Gruppo / ...
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
    SOURCE_TYPE   = "aifa_exfactory"
    SOURCE_LABEL  = "AIFA Lista Classe H (Prezzo Ex-factory)"
    REQUIRES_LOGIN = False

    def __init__(
        self,
        cache_dir: Path = None,
        msd_only: bool = False,
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

    def _find_csv_urls_requests(self) -> dict[str, str]:
        """
        AIFA 페이지 HTML을 requests로 가져와 Class A / Class H CSV URL 탐색.
        반환: {"A": url, "H": url} (각각 가장 최신 파일)
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

        out: dict[str, str] = {}
        for cls in ("A", "H"):
            pat = rf'href="(/documents/[^"]+Classe_{cls}_per_nome_commerciale[^"]+\.csv)"'
            matches = re.findall(pat, content)
            if not matches:
                matches = re.findall(
                    rf'"(https://www\.aifa\.gov\.it/documents/[^"]+Classe_{cls}_per_nome_commerciale[^"]+\.csv)"',
                    content,
                )
            if not matches:
                logger.warning("[IT] Class %s CSV 링크 미발견", cls)
                continue
            best = sorted(matches)[-1]
            url = AIFA_DOC_BASE + best if best.startswith("/") else best
            out[cls] = url
            logger.info("[IT] Class %s CSV URL: %s", cls, url)

        if not out:
            raise RuntimeError(
                "AIFA 페이지에서 Class A/H CSV 링크를 모두 찾지 못했습니다.\n"
                f"페이지 URL: {AIFA_PAGE_URL}"
            )
        return out

    async def _find_csv_urls(self, _page: Page) -> dict[str, str]:
        return self._find_csv_urls_requests()

    # ────────────────────────────────────────────────────────────────────────
    # 2) CSV 다운로드
    # ────────────────────────────────────────────────────────────────────────

    def _download_csv(self, url: str) -> Path:
        """AIFA CSV를 requests로 직접 다운로드해 캐시에 저장."""
        fname = url.split("/")[-1].split("?")[0] or "classe_trade.csv"
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

    def _load_latest_cache(self, cls: str) -> Optional[Path]:
        """캐시 디렉터리에서 지정 Class(A|H)의 가장 최신 CSV 파일 반환."""
        files = sorted(
            self.cache_dir.glob(f"Classe_{cls}_per_nome_commerciale*.csv"),
            reverse=True,
        )
        return files[0] if files else None

    # ────────────────────────────────────────────────────────────────────────
    # 3) CSV 파싱
    # ────────────────────────────────────────────────────────────────────────

    def _parse_csv(self, path: Path, cls: str) -> list[dict]:
        """AIFA CSV → dict 리스트 반환. 각 행에 `_class`(A|H) 태깅."""
        for enc in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                raw = path.read_bytes().decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValueError(f"인코딩 판별 실패: {path.name}")

        sample = raw[:2048]
        delimiter = ";" if sample.count(";") > sample.count(",") else ","

        reader = csv.DictReader(io.StringIO(raw), delimiter=delimiter)
        rows = []
        for row in reader:
            row["_class"] = cls
            rows.append(row)
        logger.info("[IT] CSV 파싱 (%s): %d행 (구분자='%s', 인코딩=%s)",
                    cls, len(rows), delimiter, enc)
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
        """CSV 행 → 표준 결과 dict 리스트. Class H → Ex-factory, Class A → Prezzo al pubblico."""
        def find_col(sample_row: dict, *keywords) -> Optional[str]:
            return next(
                (k for k in sample_row if any(kw.lower() in k.lower() for kw in keywords)),
                None,
            )

        def parse_price(raw: str) -> Optional[float]:
            s = str(raw or "").strip().replace("\xa0", "").replace(" ", "")
            if not s or s == "-":
                return None
            # IT: comma = decimal. 간혹 혼용도 있으나 단일 컴마는 소수점.
            s = s.replace(".", "").replace(",", ".") if s.count(",") == 1 else s.replace(",", ".")
            try:
                v = float(s)
                return v if v > 0 else None
            except ValueError:
                return None

        results = []
        for row in rows:
            cls = row.get("_class", "H")
            denom_col     = find_col(row, "Denominazione")
            principio_col = find_col(row, "Principio")
            exfactory_col = find_col(row, "Ex-factory", "Ex factory")
            pubblico_col  = find_col(row, "Prezzo al pubblico")
            titolare_col  = find_col(row, "Titolare")
            codice_col    = find_col(row, "Codice AIC", "AIC")
            gruppo_col    = find_col(row, "Descrizione Gruppo", "Gruppo")

            denom_raw = str(row.get(denom_col, "") if denom_col else "")
            gruppo_raw = str(row.get(gruppo_col, "") if gruppo_col else "")

            # Class H → Ex-factory (공장도가, HIRA 직접 사용)
            # Class A → Prezzo al pubblico (소비자가, HIRA 공식 환산 필요)
            if cls == "H" and exfactory_col:
                local_price = parse_price(row.get(exfactory_col))
                src_type = "aifa_exfactory"
                price_kind = "ex-factory"
            else:
                local_price = parse_price(row.get(pubblico_col)) if pubblico_col else None
                src_type = None
                price_kind = "prezzo al pubblico"

            results.append({
                "product_name":    denom_raw,
                "ingredient":      str(row.get(principio_col, "") if principio_col else ""),
                "dosage_strength": denom_raw,
                "dosage_form":     gruppo_raw,
                "package_unit":    "",
                "local_price":     local_price,
                "source_type":     src_type,
                "source_url":      AIFA_PAGE_URL,
                "extra": {
                    "company":      str(row.get(titolare_col, "") if titolare_col else ""),
                    "codice_aic":   str(row.get(codice_col, "") if codice_col else ""),
                    "aifa_class":   cls,
                    "price_kind":   price_kind,
                    "descrizione":  gruppo_raw,
                    "source_type":  src_type,
                    "raw":          {k: v for k, v in row.items() if not k.startswith("_")},
                },
            })
        return results

    # ────────────────────────────────────────────────────────────────────────
    # 6) BaseScraper 인터페이스 구현
    # ────────────────────────────────────────────────────────────────────────

    def _ensure_class_rows(self, cls: str, page: Optional[Page] = None) -> list[dict]:
        """Class A 또는 H CSV 를 로드 (캐시 우선, 없으면 다운로드)."""
        cached = self._load_latest_cache(cls)
        if cached:
            logger.info("[IT] 캐시 로드 (Class %s): %s", cls, cached.name)
            return self._parse_csv(cached, cls)
        logger.info("[IT] 캐시 없음 (Class %s) — CSV 다운로드", cls)
        urls = self._find_csv_urls_requests()
        if cls not in urls:
            logger.warning("[IT] Class %s URL 없음 — skip", cls)
            return []
        csv_path = self._download_csv(urls[cls])
        return self._parse_csv(csv_path, cls)

    async def search(self, query: str, page: Page) -> list[dict]:
        """
        AIFA Class A + Class H CSV에서 약제명으로 검색.
        캐시 없을 시 자동 다운로드. msd_only=True 이면 MSD 제품만.
        """
        if self._rows_cache is None:
            rows_all: list[dict] = []
            for cls in ("A", "H"):
                rows_all.extend(self._ensure_class_rows(cls, page))
            self._rows_cache = rows_all
            logger.info("[IT] 전체 캐시 로드: %d행 (A+H 병합)", len(self._rows_cache))

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
        """최신 Class A/H CSV 를 모두 재다운로드해 캐시 갱신."""
        urls = self._find_csv_urls_requests()
        rows_all: list[dict] = []
        for cls, url in urls.items():
            csv_path = self._download_csv(url)
            rows_all.extend(self._parse_csv(csv_path, cls))
        self._rows_cache = rows_all
        logger.info("[IT] CSV 캐시 갱신 완료 (%d행)", len(rows_all))
