"""End-to-end builder: FDA 라벨 → 5-anchor 구조화 → SQLite 적재.

사용법:
    .venv/bin/python scripts/build_indications.py pembrolizumab keytruda
    .venv/bin/python scripts/build_indications.py pembrolizumab keytruda --limit 5
    .venv/bin/python scripts/build_indications.py pembrolizumab keytruda --dry-run

인자:
    drug          openFDA 검색어 (generic 우선, brand 폴백)
    product_slug  DB에 저장될 product slug (예: keytruda)

옵션:
    --limit N     처음 N개 적응증만 처리 (테스트용)
    --dry-run     LLM 호출 + 콘솔 출력만, DB 저장 생략
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
from agents.hta_scrapers.us_fda import USFDAScraper
from agents.research.indication_structurer import structure_indication


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("drug")
    ap.add_argument("product_slug")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--codes", default=None,
                    help="처리할 1.x 코드 목록 (예: 1.4,1.8,1.18). 지정 시 limit 무시")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--db", default=str(ROOT / "data" / "db" / "drug_prices.db"))
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    log = logging.getLogger("build_indications")

    log.info("FDA fetch: %s", args.drug)
    records = USFDAScraper().search(args.drug)
    if not records:
        log.error("FDA 결과 없음")
        return 1

    rec = records[0]  # 첫 번째 라벨 사용 (가장 최신/주된 라벨)
    brand = rec.brand_names[0] if rec.brand_names else args.product_slug
    log.info(
        "라벨: brand=%s | effective=%s | indications=%d건",
        brand, rec.effective_time, len(rec.indications),
    )
    if len(records) > 1:
        log.info("(보조 라벨 %d개 있음 — 이번엔 첫 번째만 사용)", len(records) - 1)

    indications = [i for i in rec.indications if i.body]
    skipped_empty = len(rec.indications) - len(indications)
    if skipped_empty:
        log.info("본문 없는 적응증 %d건 skip", skipped_empty)
    if args.codes:
        wanted = {c.strip() for c in args.codes.split(",") if c.strip()}
        before = len(indications)
        indications = [i for i in indications if i.code in wanted]
        log.info("--codes %s 적용 → %d/%d건만 처리",
                 sorted(wanted), len(indications), before)
        missing = wanted - {i.code for i in indications}
        if missing:
            log.warning("요청된 코드 중 라벨에 없음: %s", sorted(missing))
    elif args.limit:
        indications = indications[: args.limit]
        log.info("--limit %d 적용 → %d건만 처리", args.limit, len(indications))

    db = DrugPriceDB(Path(args.db)) if not args.dry_run else None

    ok = fail = 0
    t0 = time.time()
    for i, ind in enumerate(indications, 1):
        elapsed = time.time() - t0
        log.info("[%d/%d] %s %s (elapsed=%.1fs)",
                 i, len(indications), ind.code, (ind.label or "")[:60], elapsed)

        try:
            result = structure_indication(
                product=args.product_slug,
                brand=brand,
                indication=ind,
                label_url=rec.label_url,
                effective_time=rec.effective_time,
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
        log.info(
            "  → %s | trial=%s | dx=%s | LoT=%s | bio=%s (%s)",
            m["indication_id"], m["pivotal_trial"], m["disease"],
            m["line_of_therapy"], m["biomarker_class"], a["biomarker_label"],
        )

        if db:
            db.upsert_indication_master(m)
            db.upsert_indication_agency(a)
        ok += 1

    total = time.time() - t0
    log.info("완료: 성공 %d / 실패 %d / 총 시간 %.1fs", ok, fail, total)

    if db:
        rows = db.get_indications(args.product_slug)
        log.info("DB 확인: product=%s 적응증 %d건 적재됨", args.product_slug, len(rows))
        for r in rows[:5]:
            print(f"  {r['indication_id']}  | agencies={[a['agency'] for a in r['agencies']]}")
        if len(rows) > 5:
            print(f"  ... 외 {len(rows) - 5}건")

    return 0 if fail == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
