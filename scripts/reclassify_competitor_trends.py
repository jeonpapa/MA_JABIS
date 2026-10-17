#!/usr/bin/env python3
"""저장된 경쟁사 동향 카드(competitor_trend) badge/회사 결정론 재분류.

agents/competitor_trend_quality.py 의 가드(해외 승인 · 급여 추진 · 가격 변동 · 브랜드→회사 교정)를
기존 행에 적용한다. 기본은 **dry-run** (DB 무변경, 변경 예정 목록만 출력).

  # dry-run (기본) — 변경 예정 건수/목록
  PYTHONPATH=. python3 scripts/reclassify_competitor_trends.py
  # JSON 리포트로
  PYTHONPATH=. python3 scripts/reclassify_competitor_trends.py --json > /tmp/reclassify.json
  # 실제 반영 (badge/badge_color/company/logo/color/updated_at 만 UPDATE, 행 삭제 없음)
  PYTHONPATH=. python3 scripts/reclassify_competitor_trends.py --apply

원칙:
  - source_type='manual' 카드는 절대 수정하지 않는다 (admin 편집 보존). 플래그만 리포트.
  - 중복 카드는 지우지 않는다 — 묶음은 GET /api/competitor-trends 읽기 시점 처리.
  - 요약(detail)에 타사 브랜드가 주어로 등장하는 카드는 low-confidence 로 리포트만 (내용 수정 X).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from agents.competitor_trend_quality import (  # noqa: E402
    BADGE_COLOR,
    build_brand_map,
    guard_card,
)

DEFAULT_DB = BASE_DIR / "data" / "db" / "drug_prices.db"
_COLS = ("id", "company", "logo", "color", "badge", "badge_color", "headline", "detail",
         "date", "source_type")


def _tracked_brands(conn: sqlite3.Connection) -> list[dict] | None:
    """competitor_brand(active=1) — 없으면 None(상수 폴백)."""
    try:
        rows = conn.execute(
            "SELECT query, company, logo, color FROM competitor_brand WHERE active = 1 ORDER BY id"
        ).fetchall()
        items = [dict(zip(("query", "company", "logo", "color"), r)) for r in rows]
        return items or None
    except sqlite3.Error:
        return None


def plan_changes(rows: list[dict], brand_map: dict) -> tuple[list[dict], list[dict]]:
    """순수 함수 — (changes, flagged). changes: 실제 UPDATE 대상(auto 카드만)."""
    changes: list[dict] = []
    flagged: list[dict] = []
    for r in rows:
        g = guard_card(r, brand_map)
        if g["flags"]:
            flagged.append({"id": r["id"], "date": r.get("date"), "company": r.get("company"),
                            "headline": r.get("headline"), "flags": g["flags"],
                            "source_type": r.get("source_type")})
        if (r.get("source_type") or "") == "manual":
            continue
        upd: dict = {}
        if g["badge"] and g["badge"] != r.get("badge"):
            upd["badge"] = g["badge"]
            upd["badge_color"] = BADGE_COLOR.get(g["badge"], r.get("badge_color"))
        if g["company"] and g["company"] != r.get("company"):
            upd["company"] = g["company"]
            meta = g.get("meta") or {}
            if meta.get("logo"):
                upd["logo"] = meta["logo"]
            if meta.get("color"):
                upd["color"] = meta["color"]
        if upd:
            changes.append({
                "id": r["id"], "date": r.get("date"), "headline": r.get("headline"),
                "before": {k: r.get(k) for k in upd},
                "after": upd,
                "reasons": [x for x in (g["badge_reason"], g["company_reason"]) if x],
            })
    return changes, flagged


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="competitor_trend badge/회사 결정론 재분류 (기본 dry-run)")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="sqlite 경로 (기본 data/db/drug_prices.db)")
    ap.add_argument("--apply", action="store_true", help="실제 UPDATE (미지정 시 dry-run)")
    ap.add_argument("--json", action="store_true", help="JSON 리포트 출력")
    args = ap.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"DB 없음: {db_path}", file=sys.stderr)
        return 2
    # dry-run 은 read-only URI 로 연다
    uri = f"file:{db_path}?mode=ro" if not args.apply else f"file:{db_path}"
    conn = sqlite3.connect(uri, uri=True)
    try:
        rows = [dict(zip(_COLS, r)) for r in conn.execute(
            f"SELECT {', '.join(_COLS)} FROM competitor_trend ORDER BY date DESC, id DESC"
        ).fetchall()]
        brand_map = build_brand_map(_tracked_brands(conn))
        changes, flagged = plan_changes(rows, brand_map)

        if args.apply and changes:
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            for c in changes:
                fields = dict(c["after"])
                fields["updated_at"] = now
                sets = ", ".join(f"{k} = ?" for k in fields)
                # manual 은 WHERE 로 한 번 더 차단
                conn.execute(
                    f"UPDATE competitor_trend SET {sets} WHERE id = ? AND COALESCE(source_type,'') != 'manual'",
                    [*fields.values(), c["id"]],
                )
            conn.commit()
    finally:
        conn.close()

    summary = {
        "mode": "apply" if args.apply else "dry-run",
        "db": str(db_path),
        "total": len(rows),
        "changes": len(changes),
        "badge_changes": sum(1 for c in changes if "badge" in c["after"]),
        "company_changes": sum(1 for c in changes if "company" in c["after"]),
        "low_confidence": len(flagged),
    }
    if args.json:
        print(json.dumps({"summary": summary, "changes": changes, "flagged": flagged},
                         ensure_ascii=False, indent=2))
    else:
        for c in changes:
            diff = ", ".join(f"{k}: {c['before'].get(k)!r} → {v!r}" for k, v in c["after"].items()
                             if k in ("badge", "company"))
            print(f"[{c['id']}] {c['date']} {c['headline']} | {diff} | {';'.join(c['reasons'])}")
        if flagged:
            print("\n-- low-confidence (수정 없음, 검토 필요) --")
            for f in flagged:
                print(f"[{f['id']}] {f['date']} {f['company']} | {f['headline']} | {';'.join(f['flags'])}")
        print("\n" + json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
