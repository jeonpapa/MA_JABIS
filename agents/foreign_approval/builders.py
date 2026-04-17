"""기관별 허가사항 빌드 루프 — 6기관 공통 패턴 단일화.

FDA 는 brand 매칭만 있고 anchor 매칭은 skip (FDA = master 의 기준 anchor).
나머지 5기관(EMA/PMDA/MFDS/MHRA/TGA)은 구조 동일:
    search → 첫 record → filter → 각 indication 구조화 → anchor 매칭 → upsert.

MFDS 는 빌드 루프 종료 후 변경이력 기반 공식일 교체 단계 1회 추가.
"""
from __future__ import annotations

import logging
import time

from agents.research.indication_structurer import structure_indication

from .models import AgencyResult

logger = logging.getLogger(__name__)


class _BuildersMixin:
    # ── 공통 헬퍼 ─────────────────────────────────────────────────
    def _process_indications(
        self,
        res: AgencyResult,
        product_slug: str,
        brand: str,
        indications: list,
        label_url: str | None,
        effective_time: str | None,
        agency: str,
    ) -> None:
        """indication 리스트를 순회하며 구조화·매칭·upsert.

        FDA 는 anchor 매칭 없이 무조건 신규 master 생성.
        그 외 기관은 find_matching_indication 으로 기존 master 에 variant 부착.
        """
        for i, ind in enumerate(indications, 1):
            logger.info("[%s %s] [%d/%d] %s",
                        agency, product_slug, i, len(indications), ind.code)
            try:
                result = structure_indication(
                    product=product_slug, brand=brand, indication=ind,
                    label_url=label_url, effective_time=effective_time,
                    agency=agency,
                )
            except Exception as e:
                logger.exception("[%s %s] 구조화 예외: %s", agency, product_slug, e)
                res.failed += 1
                res.errors.append(f"{ind.code}: {e}")
                continue
            if not result:
                res.failed += 1
                continue

            m = result["master"]
            a = result["agency"]

            if agency == "FDA":
                self.db.upsert_indication_master(m)
                self.db.upsert_indication_agency(a)
                res.new += 1
                res.ok += 1
                continue

            anchor = {
                "disease":         m["disease"],
                "stage":           m["stage"],
                "line_of_therapy": m["line_of_therapy"],
                "biomarker_class": m["biomarker_class"],
            }
            matched_id = self.db.find_matching_indication(product_slug, anchor)
            if matched_id:
                a["indication_id"] = matched_id
                self.db.upsert_indication_agency(a)
                res.matched += 1
            else:
                self.db.upsert_indication_master(m)
                self.db.upsert_indication_agency(a)
                res.new += 1
            res.ok += 1

    # ── FDA ──────────────────────────────────────────────────────
    def _build_fda(
        self,
        drug: str,
        product_slug: str,
        limit: int | None,
        codes: list[str] | None,
    ) -> AgencyResult:
        res = AgencyResult(agency="FDA")
        t0 = time.time()
        records = self._fda.search(drug)
        if not records:
            res.errors.append("FDA: 결과 없음")
            res.elapsed = time.time() - t0
            return res
        target = product_slug.upper().replace("-", "")
        matched = next(
            (r for r in records
             if any(target == (b or "").upper().replace("-", "").replace(" ", "")
                    for b in r.brand_names)),
            None,
        )
        rec = matched or records[0]
        if matched is None:
            logger.warning("[FDA %s] 정확한 brand 매칭 실패 — 첫 레코드(%s) 사용",
                           product_slug, rec.brand_names[:1])
        brand = rec.brand_names[0] if rec.brand_names else product_slug
        logger.info("[FDA %s] brand=%s | indications=%d건",
                    product_slug, brand, len(rec.indications))

        indications = [i for i in rec.indications if i.body]
        indications = self._filter(indications, limit, codes)
        self._process_indications(
            res, product_slug, brand, indications,
            rec.label_url, rec.effective_time, "FDA",
        )
        res.elapsed = time.time() - t0
        return res

    # ── EMA ──────────────────────────────────────────────────────
    def _build_ema(
        self,
        brand_slug: str,
        product_slug: str,
        limit: int | None,
        codes: list[str] | None,
    ) -> AgencyResult:
        res = AgencyResult(agency="EMA")
        t0 = time.time()
        records = self._ema.search(product_slug, brand_slug=brand_slug)
        if not records:
            res.errors.append("EMA: 결과 없음")
            res.elapsed = time.time() - t0
            return res
        rec = records[0]
        logger.info("[EMA %s] brand=%s | indications=%d건",
                    product_slug, rec.brand, len(rec.indications))
        indications = self._filter(rec.indications, limit, codes)
        self._process_indications(
            res, product_slug, rec.brand, indications,
            rec.pi_pdf_url, rec.authorization_date, "EMA",
        )
        res.elapsed = time.time() - t0
        return res

    # ── PMDA ─────────────────────────────────────────────────────
    def _build_pmda(
        self,
        product_slug: str,
        limit: int | None,
        codes: list[str] | None,
    ) -> AgencyResult:
        res = AgencyResult(agency="PMDA")
        t0 = time.time()
        records = self._pmda.search(product_slug)
        if not records:
            res.errors.append("PMDA: 결과 없음 (YJ 코드 또는 URL 미설정 가능성)")
            res.elapsed = time.time() - t0
            return res
        rec = records[0]
        logger.info("[PMDA %s] brand=%s | indications=%d건",
                    product_slug, rec.brand, len(rec.indications))
        indications = self._filter(rec.indications, limit, codes)
        self._process_indications(
            res, product_slug, rec.brand, indications,
            rec.pi_pdf_url, rec.approval_date, "PMDA",
        )
        res.elapsed = time.time() - t0
        return res

    # ── MFDS (식약처) ────────────────────────────────────────────
    def _build_mfds(
        self,
        product_slug: str,
        limit: int | None,
        codes: list[str] | None,
    ) -> AgencyResult:
        res = AgencyResult(agency="MFDS")
        t0 = time.time()
        records = self._mfds.search(product_slug)
        if not records:
            res.errors.append("MFDS: 결과 없음 (MFDS_ITEM_SEQ 에 itemSeq 미설정 가능성)")
            res.elapsed = time.time() - t0
            return res
        rec = records[0]
        logger.info("[MFDS %s] brand=%s | indications=%d건",
                    product_slug, rec.brand, len(rec.indications))
        indications = self._filter(rec.indications, limit, codes)
        self._process_indications(
            res, product_slug, rec.brand, indications,
            rec.detail_url, rec.permit_date, "MFDS",
        )
        self._apply_mfds_official_dates(product_slug, res)
        res.elapsed = time.time() - t0
        return res

    def _apply_mfds_official_dates(self, product_slug: str, res: AgencyResult) -> None:
        """현행 라벨 upsert 이후 MFDS 변경이력 기반 공식 승인일로 교체.

        `MFDS_ITEM_SEQ` 에 미등록 slug 는 조용히 skip 한다 (warning 로그).
        변환 성공 레코드는 `date_source='mfds_official'`, 실패 레코드는
        `'unverified_estimate'` 로 기록된다.
        """
        from scripts.apply_mfds_official_dates import apply_official_dates
        try:
            stats = apply_official_dates(product_slug, apply=True)
        except Exception as e:
            logger.exception("[MFDS %s] 공식일 적용 실패: %s", product_slug, e)
            res.errors.append(f"apply_official_dates: {e}")
            return
        if stats.get("skipped"):
            logger.warning("[MFDS %s] 공식일 skip — %s", product_slug, stats["skipped"])
            res.errors.append(stats["skipped"])
            return
        logger.info(
            "[MFDS %s] 공식일 적용 — matched=%d unmatched=%d updated=%d unchanged=%d",
            product_slug, stats["matched"], stats["unmatched"],
            stats["updated"], stats["unchanged"],
        )
        missing = stats.get("missing_disease_kr") or []
        if missing:
            msg = f"DISEASE_KR 미등록: {', '.join(missing)}"
            logger.warning("[MFDS %s] %s — 라벨 한국어 키워드 추가 필요 "
                           "(agents/hta_scrapers/kr_mfds_indication_mapper.py DISEASE_KR)",
                           product_slug, msg)
            res.errors.append(msg)

    # ── MHRA ─────────────────────────────────────────────────────
    def _build_mhra(
        self,
        product_slug: str,
        limit: int | None,
        codes: list[str] | None,
    ) -> AgencyResult:
        res = AgencyResult(agency="MHRA")
        t0 = time.time()
        records = self._mhra.search(product_slug)
        if not records:
            res.errors.append("MHRA: 결과 없음 (EMC_PRODUCT_IDS 에 product ID 미설정 가능성)")
            res.elapsed = time.time() - t0
            return res
        rec = records[0]
        logger.info("[MHRA %s] brand=%s | indications=%d건",
                    product_slug, rec.brand, len(rec.indications))
        indications = self._filter(rec.indications, limit, codes)
        self._process_indications(
            res, product_slug, rec.brand, indications,
            rec.smpc_url, None, "MHRA",
        )
        res.elapsed = time.time() - t0
        return res

    # ── TGA ──────────────────────────────────────────────────────
    def _build_tga(
        self,
        product_slug: str,
        limit: int | None,
        codes: list[str] | None,
    ) -> AgencyResult:
        res = AgencyResult(agency="TGA")
        t0 = time.time()
        records = self._tga.search(product_slug)
        if not records:
            res.errors.append("TGA: 결과 없음 (TGA_PI_IDS 에 PI ID 미설정 가능성)")
            res.elapsed = time.time() - t0
            return res
        rec = records[0]
        logger.info("[TGA %s] brand=%s | indications=%d건",
                    product_slug, rec.brand, len(rec.indications))
        indications = self._filter(rec.indications, limit, codes)
        tga_url = (f"https://www.ebs.tga.gov.au/ebs/picmi/picmirepository.nsf/"
                   f"pdf?OpenAgent&id={rec.pi_id}") if rec.pi_id else None
        self._process_indications(
            res, product_slug, rec.brand, indications,
            tga_url, None, "TGA",
        )
        res.elapsed = time.time() - t0
        return res
