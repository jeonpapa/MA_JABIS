#!/usr/bin/env python3
"""등재 아날로그 중복 병합·표기 정규화 실행기(agents.analog.dedupe). 기본 dry-run.

  python -m scripts.dedupe_analog --db data/db/drug_prices.db                      # 계획만 출력
  python -m scripts.dedupe_analog --db … --plan-out plan.json                      # 계획 JSON 저장(운영 적용용)
  python -m scripts.dedupe_analog --db … --apply [--plan-in plan.json]             # 백업 후 적용(plan-in 은 파일명이 같을 때만 병합)
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.analog import dedupe  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/db/drug_prices.db")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--plan-in", default=None)
    ap.add_argument("--plan-out", default=None)
    ap.add_argument("--no-backup", action="store_true", help="볼륨 스냅샷을 따로 만든 운영 환경용(DB 전체 복사 생략)")
    a = ap.parse_args()
    conn = sqlite3.connect(a.db) if a.apply else sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    pl = json.loads(Path(a.plan_in).read_text(encoding="utf-8")) if a.plan_in else dedupe.plan(conn)
    print(json.dumps({"mode": "apply" if a.apply else "dry-run", "merges": len(pl["merges"]), "renames": len(pl["renames"])}, ensure_ascii=False))
    for m in pl["merges"][:40]:
        print(f"  병합 keep {m['keep']} {m['keep_brand'][:20]} ← drop {m['drop']} {m['drop_brand'][:22]} ({m['session']})")
    if a.plan_out:
        Path(a.plan_out).write_text(json.dumps(pl, ensure_ascii=False, default=str), encoding="utf-8")
    if a.apply:
        if not a.no_backup:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            bk = Path(a.db).with_name(f"{Path(a.db).stem}.pre_dedupe.{stamp}.db")
            conn.execute("VACUUM INTO ?", (str(bk),))
            print("백업", bk)
        print(json.dumps(dedupe.apply_plan(conn, pl), ensure_ascii=False))


if __name__ == "__main__":
    main()
