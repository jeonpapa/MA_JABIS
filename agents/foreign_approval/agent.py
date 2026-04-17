"""ForeignApprovalAgent — 6개 mixin 을 통합한 메인 클래스."""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from agents.db import DrugPriceDB
from agents.hta_scrapers.au_tga import AUTGAScraper
from agents.hta_scrapers.eu_ema import EUEMAScraper
from agents.hta_scrapers.jp_pmda import JPPMDAScraper
from agents.hta_scrapers.kr_mfds import KRMFDSScraper
from agents.hta_scrapers.uk_mhra import UKMHRAScraper
from agents.hta_scrapers.us_fda import USFDAScraper

from .builders import _BuildersMixin
from .matrix import _MatrixMixin
from .merger import _MergerMixin
from .models import AgencyResult, BuildSummary

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "data" / "db" / "drug_prices.db"


class ForeignApprovalAgent(_BuildersMixin, _MergerMixin, _MatrixMixin):
    """FDA + EMA 등 해외 규제기관의 indication 단위 허가사항을 통합 적재.

    - 기관 단위 master 가 아닌, anchor(disease/LoT/stage/biomarker/combination/trial)
      매칭으로 동일 indication 은 한 master row 에 여러 agency variant 로 적재.
    - 동일 anchor + 다른 병용약은 별개 indication 으로 처리 (slug 에 combination 포함).
    - LLM = Gemini 2.5-pro grounded. 파싱 실패는 재시도 후 skip.
    """

    SUPPORTED = ("FDA", "EMA", "PMDA", "MFDS", "MHRA", "TGA")

    def __init__(self, db_path: Path | None = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB
        self.db = DrugPriceDB(self.db_path)
        self._fda = USFDAScraper()
        self._ema = EUEMAScraper()
        self._pmda = JPPMDAScraper()
        self._mfds = KRMFDSScraper()
        self._mhra = UKMHRAScraper()
        self._tga = AUTGAScraper()

    # ──────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────
    def build(
        self,
        drug: str,
        product_slug: str,
        brand_slug: str | None = None,
        agencies: tuple[str, ...] | list[str] = SUPPORTED,
        wipe: bool = False,
        limit: int | None = None,
        codes: list[str] | None = None,
    ) -> BuildSummary:
        """단일 product 에 대해 지정 기관들의 허가사항을 수집·적재.

        Args:
            drug:         스크레이퍼 검색어 (FDA: openFDA query, EMA: EPAR slug)
            product_slug: DB 에 저장될 product slug (예: "keytruda")
            brand_slug:   EMA EPAR URL slug (없으면 product_slug 사용)
            agencies:     SUPPORTED 의 부분집합
            wipe:         True 시 product_slug 에 해당하는 모든 기존 indication row 삭제
            limit:        각 기관별 처음 N개만 처리 (테스트용)
            codes:        특정 ind code 목록만 처리 (예: ["1.4_a", "ema_5"])
        """
        agencies = [a.upper() for a in agencies]
        for a in agencies:
            if a not in self.SUPPORTED:
                raise ValueError(f"Unsupported agency: {a} (지원: {self.SUPPORTED})")

        if wipe:
            self._wipe_product(product_slug)
            logger.info("[%s] 기존 indication row 전부 삭제", product_slug)

        results: list[AgencyResult] = []
        if "FDA" in agencies:
            results.append(self._build_fda(drug, product_slug, limit, codes))
        if "EMA" in agencies:
            results.append(self._build_ema(brand_slug or product_slug, product_slug, limit, codes))
        if "PMDA" in agencies:
            results.append(self._build_pmda(product_slug, limit, codes))
        if "MFDS" in agencies:
            results.append(self._build_mfds(product_slug, limit, codes))
        if "MHRA" in agencies:
            results.append(self._build_mhra(product_slug, limit, codes))
        if "TGA" in agencies:
            results.append(self._build_tga(product_slug, limit, codes))

        return BuildSummary(product=product_slug, agencies=results, wiped=wipe)

    # ──────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────
    @staticmethod
    def _filter(indications: list, limit: int | None, codes: list[str] | None) -> list:
        if codes:
            wanted = {c.strip() for c in codes if c.strip()}
            return [i for i in indications if i.code in wanted]
        if limit:
            return indications[:limit]
        return indications

    def _wipe_product(self, product_slug: str) -> None:
        with sqlite3.connect(str(self.db_path)) as c:
            c.execute(
                "DELETE FROM indications_by_agency WHERE indication_id IN "
                "(SELECT indication_id FROM indications_master WHERE product=?)",
                (product_slug,),
            )
            c.execute("DELETE FROM indications_master WHERE product=?", (product_slug,))
            c.commit()
