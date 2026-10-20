#!/usr/bin/env python3
"""등재 아날로그 심의결과 재판정 — '비급여하되 평가금액 이하 수용 시 급여 적정'을 CONDITIONAL_PRICE 로.

배경(2026-10-20): HIRA 게시 결과 '조건부비급여' 가 REJECTED(급여 불인정)로 저장돼 빔젤릭스 등 실제 등재된 약제가 '급여 불인정'으로 보였고,
같은 판정문(울토미리스 등 수용 후 확정)은 '급여 적정'으로 보여 결과가 일관되지 않았다.
규칙: 약평위 판정문(가. 평가 결과)이 '수용 시 급여의 적정성이 있음'·'조건부 비급여' 이면 CONDITIONAL_PRICE. 메타의 급여/비급여는 수용 여부를
반영한 결과라 result_meta 에 보존하고, 실제 수용·등재는 최초 등재일로 구분한다.

기본은 dry-run. --apply 는 compare-and-set(기록된 이전 값과 같을 때만) + DB 백업. --patch-out 은 운영 적용용 패치 JSON.
사용: PDFTOTEXT=… python -m scripts.relabel_analog_results --db data/db/drug_prices.db [--apply] [--patch-out p.json]
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sqlite3
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.analog import pdf_parser as pp  # noqa: E402
from scripts.backfill_analog_gaps import read_pages  # noqa: E402

NEW = "CONDITIONAL_PRICE"


def judge(pages: list[str]) -> str:
    """파서와 같은 흐름: 가. 평가 결과 단락(없으면 앞 4000자)에서 심의결과 판정."""
    full = "\n".join(pages)
    m = pp._RE_SEC_GA.search(full)
    ga = m.group(1).strip() if m else ""
    if not ga:
        ga = full[:4000]
    return pp._extract_review_result(ga, full)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/db/drug_prices.db")
    ap.add_argument("--pdf-dir", default="DREC Raw")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--patch-out", default=None)
    a = ap.parse_args()
    byname = {unicodedata.normalize("NFC", p.name): p for p in Path(a.pdf_dir).rglob("*.pdf")}
    conn = sqlite3.connect(a.db) if a.apply else sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id, brand_name, file_name, session_date, result_meta, review_result, review_result_pdf, result_source FROM analog_reports").fetchall()
    patch, stat = [], collections.Counter()
    for r in rows:
        meta_cp = r["result_meta"] == "조건부비급여"
        pdf = None
        p = byname.get(unicodedata.normalize("NFC", r["file_name"]))
        if p:
            pages = read_pages(p)
            pdf = judge(pages) if pages else None
        if pdf == NEW or meta_cp:
            if r["review_result"] != NEW or r["review_result_pdf"] != (NEW if pdf == NEW else r["review_result_pdf"]):
                old = {"review_result": r["review_result"], "review_result_pdf": r["review_result_pdf"], "result_source": r["result_source"]}
                new = {"review_result": NEW, "review_result_pdf": NEW if pdf == NEW else r["review_result_pdf"], "result_source": "pdf" if pdf == NEW else "meta"}
                patch.append({"id": r["id"], "brand": r["brand_name"], "meta": r["result_meta"], "old": old, "new": new})
                stat[(r["result_meta"], r["review_result"])] += 1
    print(json.dumps({"mode": "apply" if a.apply else "dry-run", "relabel": len(patch),
                      "변경 전 분포(메타, 기존 결과)": {f"{k[0]}→{k[1]}": v for k, v in stat.items()}}, ensure_ascii=False, indent=1))
    meta_fix = [dict(blt_no=x[0]) for x in conn.execute("SELECT blt_no FROM yakpyungwi_meta WHERE result_raw='조건부비급여' AND result_norm!=?", (NEW,))]
    print("yakpyungwi_meta 조건부비급여 정정", len(meta_fix))
    if a.apply:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        bk = Path(a.db).with_name(f"{Path(a.db).stem}.pre_relabel.{stamp}.db")
        conn.execute("VACUUM INTO ?", (str(bk),))
        with conn:
            for x in patch:
                conn.execute("UPDATE analog_reports SET review_result=?, review_result_pdf=?, result_source=? WHERE id=? AND review_result IS ?",
                             (x["new"]["review_result"], x["new"]["review_result_pdf"], x["new"]["result_source"], x["id"], x["old"]["review_result"]))
            conn.execute("UPDATE yakpyungwi_meta SET result_norm=? WHERE result_raw='조건부비급여'", (NEW,))
        print("백업", bk)
    if a.patch_out:
        Path(a.patch_out).write_text(json.dumps({"rows": patch, "meta_blt": [m["blt_no"] for m in meta_fix]}, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
