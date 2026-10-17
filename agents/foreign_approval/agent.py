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


def coverage_gaps(db_path: Path | str) -> list[str]:
    """foreign_drug_prices 에 있지만 indications_master 에 없는 product slug (LOWER(query_name) 기준).

    ForeignApprovalAgent 를 만들지 않고도 부를 수 있게 모듈 함수 — 신선도 판정기(agents/agent_tasks/freshness.py)가 쓴다.
    __init__ 은 스크레이퍼 6개를 생성하므로 판정 목적에는 과하다. db_path 는 `file:...?mode=ro` URI 도 받는다(읽기 전용).
    """
    p = str(db_path)
    with sqlite3.connect(p, uri=p.startswith("file:")) as c:
        price_slugs = {(r[0] or "").strip().lower() for r in c.execute(
            "SELECT DISTINCT query_name FROM foreign_drug_prices WHERE query_name IS NOT NULL").fetchall() if r[0]}
        approval_slugs = {(r[0] or "").strip().lower() for r in c.execute(
            "SELECT DISTINCT product FROM indications_master").fetchall() if r[0]}
    # brand ↔ generic 쌍 alias 처리 (예: keytruda ↔ pembrolizumab)
    aliases: dict[str, str] = {
        "pembrolizumab": "keytruda", "belzutifan": "welireg", "olaparib": "lynparza",
        "lenvatinib": "lenvima", "sitagliptin": "januvia", "letermovir": "prevymis",
    }
    return sorted({aliases.get(s, s) for s in price_slugs} - approval_slugs)


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

        protected: list[dict] = []
        if wipe:
            protected = self._snapshot_manual_corrections(product_slug)
            self._wipe_product(product_slug)
            logger.info("[%s] 기존 indication row 전부 삭제 (manual_correction %d행은 재빌드 후 복원)",
                        product_slug, len(protected))

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

        if protected:
            self._restore_manual_corrections(product_slug, protected)
        return BuildSummary(product=product_slug, agencies=results, wiped=wipe)

    # raw_source='manual_correction' 는 사람이 근거로 고친 허가일(data_contracts.md §2). wipe 재빌드가 지우면 자동 매칭
    # 값(2026-10-17 EV 병용 1차 = 제품 최초 허가일 오매칭)이 되돌아온다 → wipe 전 스냅샷, 빌드 후 같은 (indication_id, agency)에 복원.
    _PROTECTED_COLS = ("approval_date", "label_url", "restriction_note", "raw_source")

    def _snapshot_manual_corrections(self, product_slug: str) -> list[dict]:
        with sqlite3.connect(str(self.db_path)) as c:
            c.row_factory = sqlite3.Row
            rows = c.execute(
                "SELECT a.* FROM indications_by_agency a JOIN indications_master m ON m.indication_id = a.indication_id "
                "WHERE m.product = ? AND a.raw_source = 'manual_correction'", (product_slug,)).fetchall()
        return [dict(r) for r in rows]

    def _restore_manual_corrections(self, product_slug: str, rows: list[dict]) -> int:
        restored = 0
        with sqlite3.connect(str(self.db_path)) as c:
            for r in rows:
                cur = c.execute(
                    "UPDATE indications_by_agency SET approval_date=?, label_url=COALESCE(?, label_url), "
                    "restriction_note=?, raw_source='manual_correction' WHERE indication_id=? AND agency=?",
                    (r.get("approval_date"), r.get("label_url"), r.get("restriction_note"),
                     r["indication_id"], r["agency"]))
                ok = cur.rowcount > 0
                if not ok and c.execute("SELECT 1 FROM indications_master WHERE indication_id=?",
                                        (r["indication_id"],)).fetchone():
                    cols = [k for k in r if k != "id"]
                    c.execute(f"INSERT INTO indications_by_agency ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                              [r[k] for k in cols])
                    ok = True
                if ok:
                    restored += 1
                else:
                    logger.warning("[%s] manual_correction 복원 실패 — 재빌드 후 %s 마스터 없음 (%s %s) — 수동 확인",
                                   product_slug, r["indication_id"], r["agency"], r.get("approval_date"))
            c.commit()
        return restored

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

    # ──────────────────────────────────────────────────────────
    # Auto-sync: 가격 파이프라인(query-driven) ↔ 허가 파이프라인(list-driven) 비대칭 해소
    # ──────────────────────────────────────────────────────────
    def list_coverage_gaps(self) -> list[str]:
        """foreign_drug_prices 에 있지만 indications_master 에 없는 product slug — 모듈 함수 coverage_gaps 에 위임."""
        return coverage_gaps(self.db_path)

    def sync_from_prices(
        self,
        *,
        wipe: bool = False,
        agencies: tuple[str, ...] | list[str] = SUPPORTED,
    ) -> dict:
        """가격 DB 에 있는 모든 drug 에 대해 허가 pipeline 을 자동 실행.

        실패(ID 미확인 등) 는 수집 후 반환 — 호출자가 deviation_log 에 기록 가능.
        반환: {"built": [slugs], "failed": [{slug, reason}], "skipped": [slugs]}
        """
        gaps = self.list_coverage_gaps()
        logger.info("[auto-sync] 허가 커버리지 gap: %d건 (%s)", len(gaps), gaps)
        out = {"built": [], "failed": [], "skipped": []}

        # slug → (drug 검색어, brand_slug) 매핑. product_alias_map 우선, 미등록 slug 은 그대로.
        # 2026-04-27: hardcoded SLUG_HINTS 폐기, product_alias_map 의 INN 을 drug 으로 사용.
        def _resolve_hint(slug: str) -> dict:
            try:
                row = self.db.get_product_alias(slug)
            except Exception:
                row = None
            inn = (row or {}).get("inn") or slug
            return {"drug": inn, "brand_slug": slug}

        for slug in gaps:
            hint = _resolve_hint(slug)
            logger.info("[auto-sync] build 시작: %s (drug=%s)", slug, hint["drug"])
            try:
                summary = self.build(
                    drug=hint["drug"],
                    product_slug=slug,
                    brand_slug=hint.get("brand_slug"),
                    agencies=agencies,
                    wipe=wipe,
                )
                total_indications = sum(a.ok for a in summary.agencies)
                if total_indications == 0:
                    out["failed"].append({
                        "slug": slug,
                        "reason": "모든 기관에서 indication 0건 — ID/검색어 확인 필요",
                        "errors": [
                            {"agency": a.agency, "err": e}
                            for a in summary.agencies
                            for e in (a.errors or [])
                        ],
                    })
                else:
                    out["built"].append({"slug": slug, "indications": total_indications})
                    logger.info("[auto-sync] ✓ %s — %d indications", slug, total_indications)
            except Exception as e:
                logger.exception("[auto-sync] %s build 예외", slug)
                out["failed"].append({"slug": slug, "reason": str(e)})

        logger.info(
            "[auto-sync] 완료 — built=%d failed=%d skipped=%d",
            len(out["built"]), len(out["failed"]), len(out["skipped"]),
        )
        return out
