#!/usr/bin/env python3
"""등재 아날로그 식약처 '최초 허가일' 재조회 — dry-run(DB 무변경). 결과 JSON 은 backfill 적용 단계의 입력이 된다.

분류
  same     — 현재 저장값 = 최초 허가일
  changed  — 저장값이 있으나 최초 허가일과 다름(예: 네시나정 2023-10-30 → 2013-05-31)
  new      — 저장값 없음, 새로 확인됨
  miss     — API 가 답했으나 같은 제품군 품목이 없음(브랜드명 불일치·허가 대상 아님 등). '미허가' 를 뜻하지 않는다.
  error    — API 오류. miss 와 구분해 재시도 대상으로 남긴다.
브랜드 단위로 한 번만 조회하고 같은 브랜드의 모든 행에 적용한다. 체크포인트(--out)에 쌓아 중단 후 재개 가능.

사용: python -m scripts.analog_permit_dryrun --db data/db/drug_prices.db --out /tmp/permit_dry.json [--limit N]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.analog.permit_first import lookup_first_permit  # noqa: E402


def _iso(d: str | None) -> str | None:
    return d.replace(".", "-") if d else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/db/drug_prices.db")
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    out = Path(a.out)
    done: dict = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    conn = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id, brand_name, mfds_permit_date, first_reimbursement_date FROM analog_reports "
                        "WHERE brand_name IS NOT NULL AND brand_name != '' ORDER BY id").fetchall()
    brands: dict[str, list] = {}
    for r in rows:
        brands.setdefault(r["brand_name"], []).append(dict(r))
    todo = [b for b in brands if b not in done]
    if a.limit:
        todo = todo[:a.limit]
    print(f"브랜드 {len(brands)}개 중 남은 {len(todo)}개 조회", flush=True)
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _one(b: str):
        try:
            res = lookup_first_permit(b)
            return b, {"status": "ok" if res else "miss", "res": res}
        except Exception as e:  # API 오류는 miss 로 세지 않는다
            return b, {"status": "error", "err": str(e)[:120]}

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = [ex.submit(_one, b) for b in todo]
        for n, f in enumerate(as_completed(futs), 1):
            b, v = f.result()
            done[b] = v
            if n % 20 == 0:
                out.write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")
                print(f"  {n}/{len(todo)}", flush=True)
    out.write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")

    cls = {"same": 0, "changed": 0, "new": 0, "miss": 0, "error": 0}
    changed, new = [], []
    for b, rs in brands.items():
        d = done.get(b)
        if not d:
            continue
        for r in rs:
            cur = _iso(r["mfds_permit_date"])
            if d["status"] == "error":
                cls["error"] += 1
            elif d["status"] == "miss":
                cls["miss"] += 1
            else:
                nd = _iso(d["res"]["permit_date"])
                if cur and cur == nd:
                    cls["same"] += 1
                elif cur:
                    cls["changed"] += 1
                    changed.append({"id": r["id"], "brand": b, "old": cur, "new": nd, "n_items": d["res"]["n_items"],
                                    "first_reimb": r["first_reimbursement_date"]})
                else:
                    cls["new"] += 1
                    new.append({"id": r["id"], "brand": b, "new": nd})
    summary = {"rows": len(rows), "brands": len(brands), "class": cls, "changed": changed, "new_sample": new[:15]}
    Path(str(out) + ".summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"class": cls, "brands": len(brands)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
