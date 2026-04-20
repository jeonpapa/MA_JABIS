"""
프랑스 BDPM 공개 약가 스크레이퍼 (공개 DB 다운로드 방식)

대상:
  https://base-donnees-publique.medicaments.gouv.fr/telechargement
    - CIS_bdpm.txt      — 제품 메타데이터 (CIS 코드, 제품명, 제형 원문)
    - CIS_CIP_bdpm.txt  — 포장 단위·가격 (CIP13, libellé, 급여율, 가격)

로그인: 불필요 (공개 다운로드)
인코딩: ISO-8859-1 (Latin-1)

배경:
  프랑스 처방약 가격 체계:
  - PFHT (Prix Fabricant Hors Taxe)     : 비공개 (CEPS 협상)
  - PPC  (Prix Public Communautaire)    : 공개 소비자가 (PFHT + 유통마진 + TVA 2.1/5.5/10%)
  - PPH  (Prix Public Hospitalier)      : 병원 공급가 — 대부분 항암제가 이 범주이나 공개되지 않음
  - CIS_CIP 파일의 컬럼 10 = 약국 소매가 (honoraires 제외)
  -                   11 = 약국 소매가 (honoraires 포함, TTC 최종 소비자가)
  - 병원 전용 약제는 가격 컬럼이 비어있음 (Prevymis 주사제 등)

조회 흐름:
  1) 캐시된 CIS_bdpm.txt / CIS_CIP_bdpm.txt 사용 (24h TTL)
  2) 제품명 부분일치로 CIS 목록 추출
  3) CIS 별로 CIP 포장 단위 + 가격 lookup
  4) 병원 전용 (가격 공란) 은 local_price=None, 공개가 있는 경구제/외래약만 price 반환

HIRA 조정가:
  source_type="vidal" → factory_ratio = 0.65 (공개 소매가 기반)
"""

import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

from .base import BaseScraper

logger = logging.getLogger(__name__)

BDPM_BASE        = "https://base-donnees-publique.medicaments.gouv.fr"
BDPM_FILE_CIS    = BDPM_BASE + "/download/file/CIS_bdpm.txt"
BDPM_FILE_CISCIP = BDPM_BASE + "/download/file/CIS_CIP_bdpm.txt"

CACHE_TTL_HOURS = 24

REQUESTS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/plain,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}


def _parse_fr_price(raw: str) -> Optional[float]:
    """
    프랑스 BDPM 가격 문자열 파서.
    단일 콤마: 소수점 (e.g. "24,34" → 24.34).
    복수 콤마: 마지막 콤마 = 소수점, 앞 콤마 = 천단위 (e.g. "4,324,31" → 4324.31).
    """
    if not raw:
        return None
    s = raw.strip().replace("\xa0", "").replace(" ", "")
    if not s:
        return None
    if "," in s:
        head, _, tail = s.rpartition(",")
        head_clean = head.replace(",", "").replace(".", "")
        try:
            return float(f"{head_clean}.{tail}")
        except ValueError:
            return None
    try:
        return float(s)
    except ValueError:
        return None


class FrBdpmScraper(BaseScraper):
    COUNTRY        = "FR"
    CURRENCY       = "EUR"
    SOURCE_LABEL   = "BDPM/ANSM (프랑스 공개 약품 DB)"
    SOURCE_TYPE    = "vidal"    # HIRA factory_ratio = 0.65
    REQUIRES_LOGIN = False

    def __init__(self, cache_dir: Path = None, msd_only: bool = False, **kwargs):
        super().__init__(**kwargs)
        self.cache_dir = cache_dir or Path("data/foreign/fr")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.msd_only = msd_only
        self._session = requests.Session()
        self._session.headers.update(REQUESTS_HEADERS)

    async def login(self, page=None) -> None:
        pass

    async def logout(self, page=None) -> None:
        pass

    # ────────────────────────────────────────────────────────────────────────
    # 1) BDPM 공개 파일 다운로드 · 캐시
    # ────────────────────────────────────────────────────────────────────────

    def _cache_path(self, name: str) -> Path:
        return self.cache_dir / name

    def _ensure_cached(self, url: str, name: str) -> Optional[Path]:
        """
        공개 BDPM txt 파일을 캐시. TTL 초과 또는 미존재 시 재다운로드.
        반환: 로컬 파일 경로 (실패 시 None).
        """
        path = self._cache_path(name)
        if path.exists():
            age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
            if age < timedelta(hours=CACHE_TTL_HOURS):
                return path
        try:
            r = self._session.get(url, timeout=60)
            r.raise_for_status()
            path.write_bytes(r.content)
            logger.info("[FR] BDPM 파일 갱신: %s (%d bytes)", name, len(r.content))
            return path
        except Exception as e:
            logger.warning("[FR] BDPM 파일 다운로드 실패 (%s): %s", name, e)
            if path.exists():
                return path   # 오래된 캐시라도 사용
            return None

    def _load_lines(self, path: Path) -> list[list[str]]:
        """
        탭 구분 BDPM 파일 → 행별 필드 리스트.
        BDPM 내 파일마다 인코딩이 혼재 (CIS_bdpm.txt=latin-1, CIS_CIP_bdpm.txt=UTF-8).
        라인 단위로 UTF-8 우선 디코드, 실패 시 latin-1 폴백.
        """
        out = []
        with open(path, "rb") as f:
            for raw in f:
                try:
                    line = raw.decode("utf-8").rstrip("\r\n")
                except UnicodeDecodeError:
                    try:
                        line = raw.decode("latin-1").rstrip("\r\n")
                    except UnicodeDecodeError:
                        continue
                if not line:
                    continue
                out.append(line.split("\t"))
        return out

    # ────────────────────────────────────────────────────────────────────────
    # 2) 제품명 → CIS → CIP 가격 lookup
    # ────────────────────────────────────────────────────────────────────────

    def _find_matching_cis(self, query: str, cis_rows: list[list[str]]) -> list[dict]:
        """CIS_bdpm.txt 에서 제품명 부분일치 CIS 리스트 추출."""
        q_lower = query.lower().strip()
        matches = []
        seen_cis = set()
        for row in cis_rows:
            if len(row) < 11:
                continue
            cis = row[0]
            name = row[1]
            dosage_form = row[2] if len(row) > 2 else ""
            route = row[3] if len(row) > 3 else ""
            company = row[10] if len(row) > 10 else ""
            ema_code = row[9] if len(row) > 9 else ""

            if cis in seen_cis:
                continue
            if q_lower not in name.lower():
                continue
            seen_cis.add(cis)
            matches.append({
                "cis":          cis,
                "name":         name,
                "dosage_form":  dosage_form,
                "route":        route,
                "company":      company,
                "ema_code":     ema_code,
            })
        return matches

    def _find_packages(self, cis: str, cis_cip_rows: list[list[str]]) -> list[dict]:
        """CIS_CIP_bdpm.txt 에서 해당 CIS 의 포장 단위 + 가격 추출."""
        pkgs = []
        for row in cis_cip_rows:
            if len(row) < 11:
                continue
            if row[0] != cis:
                continue
            cip13      = row[6] if len(row) > 6 else ""
            libelle    = row[2] if len(row) > 2 else ""
            taux       = row[8] if len(row) > 8 else ""
            prix_sans  = row[9] if len(row) > 9 else ""
            prix_ttc   = row[10] if len(row) > 10 else ""
            # TTC (컬럼 11) 우선, 없으면 HT (컬럼 10)
            price = _parse_fr_price(prix_ttc) or _parse_fr_price(prix_sans)
            pkgs.append({
                "cip13":     cip13,
                "libelle":   libelle,
                "taux":      taux,
                "price":     price,
            })
        return pkgs

    # ────────────────────────────────────────────────────────────────────────
    # 3) 결과 변환
    # ────────────────────────────────────────────────────────────────────────

    def _extract_dosage(self, name: str) -> str:
        """제품명에서 함량 추출 ('PREVYMIS 240 mg, comprimé' → '240 mg')."""
        m = re.search(
            r"(\d+(?:[.,]\d+)?\s*(?:mg|g|µg|mcg|UI|U\.I\.|IU)(?:/\s*\d+(?:[.,]\d+)?\s*m[lL])?)",
            name,
            re.IGNORECASE,
        )
        return m.group(1).strip() if m else ""

    def _extract_ingredient(self, name: str) -> str:
        """
        제품명 앞부분이 브랜드명. BDPM 에는 DCI 별도 컬럼이 없으므로
        CIS_COMPO_bdpm.txt 를 추가로 읽어야 정확 — 본 스크레이퍼에서는
        생략하고 브랜드명만 기록 (ingredient enrichment 는 DrugEnrichmentAgent 가 담당).
        """
        # 대문자 첫 단어 (브랜드명) 만 추출
        m = re.match(r"^([A-ZÀ-ÿ][A-ZÀ-ÿ0-9\-]+)", name.strip())
        return m.group(1) if m else ""

    # ────────────────────────────────────────────────────────────────────────
    # 4) BaseScraper 인터페이스
    # ────────────────────────────────────────────────────────────────────────

    async def search(self, query: str, page=None) -> list[dict]:
        cis_path    = self._ensure_cached(BDPM_FILE_CIS,    "CIS_bdpm.txt")
        ciscip_path = self._ensure_cached(BDPM_FILE_CISCIP, "CIS_CIP_bdpm.txt")
        if not cis_path or not ciscip_path:
            logger.warning("[FR] BDPM 캐시 파일 없음 — 검색 skip")
            return []

        cis_rows    = self._load_lines(cis_path)
        ciscip_rows = self._load_lines(ciscip_path)
        logger.info("[FR] BDPM 캐시 로드: CIS=%d, CIS_CIP=%d",
                    len(cis_rows), len(ciscip_rows))

        matches = self._find_matching_cis(query, cis_rows)
        if not matches:
            logger.info("[FR] '%s' BDPM 제품명 매칭 없음", query)
            return []
        logger.info("[FR] '%s' CIS 매칭 %d건", query, len(matches))

        results = []
        for meta in matches:
            pkgs = self._find_packages(meta["cis"], ciscip_rows)
            if not pkgs:
                # CIP 없음 — 제품명/제형만 기록 (가격 비공개 병원 전용 가능성)
                results.append(self._build_record(meta, pkg=None))
                continue
            # 가격 있는 포장 우선 정렬 (None 은 뒤로)
            pkgs.sort(key=lambda p: (p["price"] is None, p["price"] or 0))
            for pkg in pkgs:
                results.append(self._build_record(meta, pkg))

        return results

    def _build_record(self, meta: dict, pkg: Optional[dict]) -> dict:
        price = pkg["price"] if pkg else None
        taux  = pkg["taux"]  if pkg else ""
        libelle = pkg["libelle"] if pkg else ""

        price_note = ""
        if price is None:
            price_note = "PPH 비공개 (병원 전용 또는 미상업)"

        return {
            "product_name":    meta["name"],
            "ingredient":      self._extract_ingredient(meta["name"]),
            "dosage_strength": self._extract_dosage(meta["name"]),
            "dosage_form":     meta["dosage_form"],
            "package_unit":    libelle,
            "local_price":     price,
            "source_url":      f"{BDPM_BASE}/extrait.php?specid={meta['cis']}",
            "extra": {
                "company":      (meta["company"] or "").strip(),
                "cis":          meta["cis"],
                "cip13":        pkg["cip13"] if pkg else "",
                "route":        meta["route"],
                "ema_code":     meta["ema_code"],
                "taux_remboursement": (taux or "").strip(),
                "price_note":   price_note,
                "source_type":  self.SOURCE_TYPE,
            },
        }

    async def refresh(self, _page=None) -> None:
        logger.info("[FR] BDPM 공개 파일 캐시 기반")

    async def run(self, query: str) -> list[dict]:
        """Playwright 없이 requests + 파일 캐시로 실행."""
        logger.info("[FR] BDPM 검색 시작: '%s'", query)
        searched_at = datetime.now().isoformat()

        raw_results = await self.search(query)
        logger.info("[FR] 결과: %d건", len(raw_results))
        if not raw_results:
            return []

        results = []
        for item in raw_results:
            form_type = self._resolve_form_type(item)
            results.append({
                "searched_at":         searched_at,
                "query_name":          query,
                "country":             self.COUNTRY,
                "product_name":        item.get("product_name", ""),
                "ingredient":          item.get("ingredient", ""),
                "dosage_strength":     item.get("dosage_strength", ""),
                "dosage_form":         item.get("dosage_form", ""),
                "package_unit":        item.get("package_unit", ""),
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
                "raw_data":            json.dumps(item.get("extra", {}), ensure_ascii=False),
                "form_type":           form_type,
            })

        return results
