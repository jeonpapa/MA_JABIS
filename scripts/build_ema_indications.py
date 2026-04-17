"""EMA SmPC section 4.1 → 6-anchor 구조화 → SQLite agency variant 적재.

FDA 가 먼저 적재된 상태를 전제로, EMA 적응증을 (1) LLM 구조화,
(2) 기존 FDA master 와 anchor 매칭, (3) 매칭되면 그 indication_id 로
agency='EMA' row 만 추가, (4) 매칭 안 되면 EMA 전용 master + agency 추가.

사용법:
    .venv/bin/python scripts/build_ema_indications.py keytruda
    .venv/bin/python scripts/build_ema_indications.py keytruda --limit 3
    .venv/bin/python scripts/build_ema_indications.py keytruda --codes ema_1,ema_5
    .venv/bin/python scripts/build_ema_indications.py keytruda --dry-run

인자:
    product_slug  EMA EPAR 슬러그 & DB product slug 공통 (예: keytruda).
                  brand 와 다르면 --brand-slug 지정.

옵션:
    --brand-slug  EMA EPAR URL 용 슬러그 (기본: product_slug)
    --limit N     처음 N개 적응증만 처리
    --codes       처리할 ema_N 코드 콤마 목록
    --dry-run     구조화 결과 출력만, DB 저장 생략
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.db import DrugPriceDB
from agents.hta_scrapers.eu_ema import EUEMAScraper
from agents.research.indication_structurer import (
    make_indication_id,
    structure_indication,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("product_slug")
    ap.add_argument("--brand-slug", default=None,
                    help="EMA EPAR URL 슬러그 (기본: product_slug)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--codes", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--db", default=str(ROOT / "data" / "db" / "drug_prices.db"))
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    log = logging.getLogger("build_ema_indications")

    brand_slug = args.brand_slug or args.product_slug
    log.info("EMA fetch: product=%s brand_slug=%s", args.product_slug, brand_slug)

    records = EUEMAScraper().search(args.product_slug, brand_slug=brand_slug)
    if not records:
        log.error("EMA 결과 없음")
        return 1

    rec = records[0]
    log.info(
        "라벨: brand=%s | auth=%s | PDF=%s | indications=%d건",
        rec.brand, rec.authorization_date,
        Path(rec.pi_pdf_local).name if rec.pi_pdf_local else "(없음)",
        len(rec.indications),
    )

    indications = rec.indications
    if args.codes:
        wanted = {c.strip() for c in args.codes.split(",") if c.strip()}
        before = len(indications)
        indications = [i for i in indications if i.code in wanted]
        log.info("--codes %s 적용 → %d/%d건", sorted(wanted), len(indications), before)
    elif args.limit:
        indications = indications[: args.limit]
        log.info("--limit %d 적용 → %d건만 처리", args.limit, len(indications))

    db = None if args.dry_run else DrugPriceDB(Path(args.db))

    ok = fail = matched = new_master = 0
    t0 = time.time()
    for i, ind in enumerate(indications, 1):
        elapsed = time.time() - t0
        log.info("[%d/%d] %s %s (elapsed=%.1fs)",
                 i, len(indications), ind.code, (ind.label or "")[:60], elapsed)

        try:
            result = structure_indication(
                product=args.product_slug,
                brand=rec.brand,
                indication=ind,
                label_url=rec.pi_pdf_url,
                effective_time=rec.authorization_date,
                agency="EMA",
            )
        except Exception as e:
            log.exception("구조화 예외: %s", e)
            fail += 1
            continue

        if not result:
            log.warning("  → 구조화 실패 (skip)")
            fail += 1
            continue

        m = result["master"]
        a = result["agency"]

        # EMA 가 제안한 slug
        ema_preferred_id = m["indication_id"]

        # 기존 master 와 anchor 매칭 시도
        anchor = {
            "disease":         m["disease"],
            "stage":           m["stage"],
            "line_of_therapy": m["line_of_therapy"],
            "biomarker_class": m["biomarker_class"],
        }
        matched_id = None
        if db:
            matched_id = db.find_matching_indication(args.product_slug, anchor)

        if matched_id:
            # 기존 (FDA) master 재사용 — agency row 만 추가
            a["indication_id"] = matched_id
            log.info(
                "  → MATCH %s | %s | dx=%s | LoT=%s | bio=%s",
                matched_id, a["combination_label"] or "(None)",
                m["disease"], m["line_of_therapy"], m["biomarker_class"],
            )
            if db:
                db.upsert_indication_agency(a)
            matched += 1
        else:
            # EMA 전용 신규 master + agency
            log.info(
                "  → NEW   %s | trial=%s | dx=%s | LoT=%s | bio=%s",
                ema_preferred_id, m["pivotal_trial"],
                m["disease"], m["line_of_therapy"], m["biomarker_class"],
            )
            if db:
                db.upsert_indication_master(m)
                db.upsert_indication_agency(a)
            new_master += 1
        ok += 1

    total = time.time() - t0
    log.info("완료: OK %d (match=%d, new=%d) / 실패 %d / 총 %.1fs",
             ok, matched, new_master, fail, total)

    if db:
        rows = db.get_indications(args.product_slug)
        agencies_count = {}
        for r in rows:
            for a in r.get("agencies") or []:
                agencies_count[a["agency"]] = agencies_count.get(a["agency"], 0) + 1
        log.info("DB 확인: product=%s 적응증 %d건 / agency 분포=%s",
                 args.product_slug, len(rows), agencies_count)

    return 0 if fail == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
