"""MFDS 공식 변경이력 기반으로 indications_by_agency.approval_date 를 교체.

실행:
  python -m scripts.apply_mfds_official_dates --product keytruda            # dry-run
  python -m scripts.apply_mfds_official_dates --product keytruda --apply    # 실제 반영
  python -m scripts.apply_mfds_official_dates --all --apply                 # 4개 약물 모두

변경 내역:
  - indications_by_agency 에 `date_source` 컬럼이 없으면 ALTER TABLE 로 추가
  - 매핑 성공 레코드: approval_date ← official_date, date_source='mfds_official',
    label_url ← MFDS 변경이력 상세 URL, raw_source 에 confidence/excerpt 기록
  - 매핑 실패 레코드: date_source='unverified_estimate' (기존 값 유지)

재사용 가능 API:
  `apply_official_dates(product_slug, item_seq, apply=True)` — ForeignApprovalAgent
  `_build_mfds` 에서 현행 라벨 upsert 완료 후 호출한다.
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime, timezone

from agents.hta_scrapers.kr_mfds import MFDS_ITEM_SEQ, resolve_item_seq
from agents.hta_scrapers.kr_mfds_indication_mapper import (
    DB_PATH,
    MappingResult,
    find_missing_disease_kr,
    map_indications,
)

logger = logging.getLogger(__name__)

MFDS_HIST_URL_FMT = (
    "https://nedrug.mfds.go.kr/pbp/CCBBB01/getItemChangeHistInfo"
    "?itemSeq={item_seq}&docType=EE"
)


def _ensure_date_source_column(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(indications_by_agency)").fetchall()}
    if "date_source" not in cols:
        print("[schema] adding indications_by_agency.date_source TEXT")
        conn.execute("ALTER TABLE indications_by_agency ADD COLUMN date_source TEXT")


def _apply_product(conn: sqlite3.Connection, item_seq: str,
                   results: list[MappingResult], apply: bool) -> dict:
    url = MFDS_HIST_URL_FMT.format(item_seq=item_seq)
    stats = {"matched": 0, "unmatched": 0, "updated": 0, "unchanged": 0}

    for r in results:
        if r.official_date:
            stats["matched"] += 1
            cur = conn.execute(
                "SELECT approval_date, date_source FROM indications_by_agency "
                "WHERE agency='MFDS' AND indication_id=?",
                (r.indication_id,),
            ).fetchone()
            if not cur:
                continue
            old_date, _ = cur
            raw = json.dumps({
                "date_source": "mfds_official",
                "confidence": r.confidence,
                "matched_excerpt": r.matched_excerpt,
                "mapped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "item_seq": item_seq,
            }, ensure_ascii=False)
            if apply:
                conn.execute(
                    "UPDATE indications_by_agency "
                    "SET approval_date=?, date_source='mfds_official', "
                    "    label_url=?, raw_source=? "
                    "WHERE agency='MFDS' AND indication_id=?",
                    (r.official_date, url, raw, r.indication_id),
                )
            if old_date != r.official_date:
                stats["updated"] += 1
            else:
                stats["unchanged"] += 1
        else:
            stats["unmatched"] += 1
            if apply:
                conn.execute(
                    "UPDATE indications_by_agency "
                    "SET date_source='unverified_estimate' "
                    "WHERE agency='MFDS' AND indication_id=? "
                    "  AND (date_source IS NULL OR date_source='')",
                    (r.indication_id,),
                )
    return stats


def apply_official_dates(product_slug: str, item_seq: str | None = None,
                         apply: bool = True) -> dict:
    """현행 라벨 upsert 완료 후 MFDS 변경이력 기반 공식일로 교체.

    Args:
        product_slug: keytruda / welireg / lynparza / lenvima 등
        item_seq: MFDS itemSeq. None 이면 MFDS_ITEM_SEQ 에서 조회
        apply: False 면 dry-run (DB 변경 없음)

    Returns:
        {'matched': int, 'unmatched': int, 'updated': int, 'unchanged': int,
         'results': list[MappingResult], 'skipped': str | None}
        `skipped` 에 사유가 담기면 itemSeq 미설정 등으로 건너뜀.
    """
    seq = item_seq or resolve_item_seq(product_slug)
    if not seq:
        return {"matched": 0, "unmatched": 0, "updated": 0, "unchanged": 0,
                "results": [], "missing_disease_kr": [],
                "skipped": f"MFDS itemSeq 자동조회 실패 — '{product_slug}'"}

    results = map_indications(seq, product=product_slug)
    with sqlite3.connect(DB_PATH) as conn:
        _ensure_date_source_column(conn)
        stats = _apply_product(conn, seq, results, apply)
        if apply:
            conn.commit()
    stats["results"] = results
    stats["missing_disease_kr"] = find_missing_disease_kr(product_slug)
    stats["skipped"] = None
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--product", help="product slug (keytruda/welireg/lynparza/lenvima)")
    ap.add_argument("--all", action="store_true", help="run for all 4 products")
    ap.add_argument("--apply", action="store_true", help="write changes to DB (default: dry-run)")
    args = ap.parse_args()

    if args.all:
        targets = list(MFDS_ITEM_SEQ.items())
    elif args.product:
        seq = MFDS_ITEM_SEQ.get(args.product)
        if not seq:
            print(f"unknown product: {args.product}", file=sys.stderr)
            sys.exit(2)
        targets = [(args.product, seq)]
    else:
        ap.error("specify --product or --all")

    with sqlite3.connect(DB_PATH) as conn:
        _ensure_date_source_column(conn)
        total = {"matched": 0, "unmatched": 0, "updated": 0, "unchanged": 0}
        for product, item_seq in targets:
            print(f"\n=== {product} (itemSeq={item_seq}) ===")
            results = map_indications(item_seq, product=product)
            for r in results:
                status = "OK " if r.official_date else "MISS"
                print(f"  [{status}] {r.indication_id:<60}  {r.disease_area:<6}  "
                      f"{str(r.line_of_therapy or '-'):<14}  "
                      f"{str(r.official_date or '-'):<12}  conf={r.confidence}")
            stats = _apply_product(conn, item_seq, results, args.apply)
            print(f"  → matched={stats['matched']}  unmatched={stats['unmatched']}  "
                  f"updated={stats['updated']}  unchanged={stats['unchanged']}")
            for k in total:
                total[k] += stats[k]
        if args.apply:
            conn.commit()
            print(f"\n[APPLIED] total {total}")
        else:
            print(f"\n[DRY-RUN] total {total}  (rerun with --apply to write)")


if __name__ == "__main__":
    main()
