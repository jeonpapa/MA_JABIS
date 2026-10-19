#!/usr/bin/env python3
"""약평위 보고서 PDF 1페이지 '▢ 효능·효과' 원문을 analog_reports.report_effect_text 에 채운다(빈 칸만).

배경: 질환 분류(enrich_disease)가 식약처 효능 텍스트(mfds_effect_text)만 읽어, API 미조회 약제는 질환이 비어 있었다.
화면 노출 53건 중 52건은 PDF 헤더에 효능·효과가 있었다. 이 스크립트는 원천 텍스트를 확보하고, 분류는 enrich_disease 가 이어받는다.

dry-run 기본(DB 무변경, 읽기 전용 연결). --apply 시 빈 칸만 채우고 compare-and-set 용 패치 JSON(--patch-out)을 함께 낸다.
사용: python -m scripts.backfill_analog_effect_text --db data/db/drug_prices.db [--apply --patch-out p.json]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.analog.header_extract import extract_effect_text  # noqa: E402
from scripts.backfill_analog_gaps import read_pages  # noqa: E402


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
    cols = [r[1] for r in conn.execute("PRAGMA table_info(analog_reports)")]
    has_col = "report_effect_text" in cols
    if a.apply and not has_col:
        conn.execute("ALTER TABLE analog_reports ADD COLUMN report_effect_text TEXT")
        has_col = True
    sel = "id, file_name, brand_name, pdf_extractable" + (", report_effect_text" if has_col else "")
    rows = conn.execute(f"SELECT {sel} FROM analog_reports ORDER BY id").fetchall()
    patch, found, blank, nopdf = [], 0, 0, 0
    for r in rows:
        if has_col and (r["report_effect_text"] or "").strip():
            continue
        p = byname.get(unicodedata.normalize("NFC", r["file_name"]))
        if not p:
            nopdf += 1
            continue
        pages = read_pages(p, 1, 1)
        txt = extract_effect_text(pages[0]) if pages else None
        if txt:
            found += 1
            patch.append({"id": r["id"], "old": {"report_effect_text": None}, "new": {"report_effect_text": txt}})
        else:
            blank += 1
    print(json.dumps({"mode": "apply" if a.apply else "dry-run", "rows": len(rows), "추출됨": found, "원문비어있음": blank, "pdf없음": nopdf}, ensure_ascii=False))
    if a.apply:
        with conn:
            for x in patch:
                conn.execute("UPDATE analog_reports SET report_effect_text=? WHERE id=? AND report_effect_text IS NULL",
                             (x["new"]["report_effect_text"], x["id"]))
    if a.patch_out:
        Path(a.patch_out).write_text(json.dumps(patch, ensure_ascii=False), encoding="utf-8")
    else:
        for x in patch[:6]:
            print(x["id"], x["new"]["report_effect_text"][:110])


if __name__ == "__main__":
    main()
