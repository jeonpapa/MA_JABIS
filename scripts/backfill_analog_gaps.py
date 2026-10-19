#!/usr/bin/env python3
"""등재 아날로그 누락 보강 — 약평위 평가결과 PDF 헤더(제형·성분·함량, 브랜드·제조사, 정확한 심의일)로 빈 칸을 채운다.

배경(2026-10-19 검증): 보고서 1페이지에 있는 용량·성분·제조사·정확한 심의일을 파서가 읽지 않아 비어 있었다.
원칙
  - 기본은 dry-run(DB 무변경): 채울 수 있는 건수·예시·충돌만 JSON 으로 낸다.
  - --apply 는 '빈 칸만' 채운다(기존 값 덮어쓰기 없음). 예외는 약평위 날짜의 자리표시값('YYYY-01-01')뿐이며, 같은 연도의
    원문 날짜일 때만 교체한다. 원문이 비어 있으면(2007~2009 공개 제외 등) 값을 지어내지 않는다.
  - 적용 전 DB 전체를 VACUUM INTO 로 백업하고, 바꾼 칸의 이전 값을 JSON 으로 남긴다(되돌리기용).
  - pdf_extractable(화면 노출 여부)은 --include-visibility 를 줄 때만 바꾼다 — 275건이 기본 검색에 새로 들어오므로 별도 결정.

사용: python -m scripts.backfill_analog_gaps --db data/db/drug_prices.db --pdf-dir "DREC Raw" [--apply] [--report out.json]
PDF 텍스트: pdftotext(PATH 또는 PDFTOTEXT 환경변수) 우선, 없으면 pdfplumber.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.analog.header_extract import extract_header_fields, korean_ingredient_from_name, looks_extractable  # noqa: E402

PLACEHOLDER = "-01-01"


def _pdftotext() -> str | None:
    return os.environ.get("PDFTOTEXT") or shutil.which("pdftotext")


def read_pages(path: Path, first: int | None = None, last: int | None = None) -> list[str]:
    """페이지별 텍스트. first/last 를 주면 그 범위만(1-base)."""
    exe = _pdftotext()
    if exe:
        cmd = [exe, "-layout" if False else "-raw"] if False else [exe]
        if first:
            cmd += ["-f", str(first), "-l", str(last or first)]
        out = subprocess.run(cmd + [str(path), "-"], capture_output=True, text=True, timeout=60).stdout
        return [unicodedata.normalize("NFC", p) for p in out.split("\f") if p.strip() or first]
    import pdfplumber  # 폴백 — 운영 파서와 같은 라이브러리
    with pdfplumber.open(str(path)) as pdf:
        pages = pdf.pages[(first or 1) - 1:(last or len(pdf.pages))]
        return [unicodedata.normalize("NFC", pg.extract_text() or "") for pg in pages]


def pick_session_date(hdr_dates: list[dict], row: sqlite3.Row) -> str | None:
    """헤더의 약평위 날짜 중 이 행(연도·차수)에 해당하는 것. 못 고르면 None(추정하지 않는다)."""
    if not hdr_dates:
        return None
    year, ordinal = row["session_year"], row["ordinal"]
    cands = [d for d in hdr_dates if (ordinal is None or ordinal in d["ordinals"]) and (year is None or d["year"] == year)]
    if len(cands) == 1:
        return cands[0]["date"]
    if not cands and len({d["date"] for d in hdr_dates}) == 1:
        return hdr_dates[0]["date"]
    return None


def plan_row(row: sqlite3.Row, pages: list[str]) -> dict:
    """한 행의 보강 제안 {칸: 새 값} + 판정 정보. DB 는 건드리지 않는다."""
    page1 = pages[0] if pages else ""
    hdr = extract_header_fields(page1)
    ko = korean_ingredient_from_name(row["brand_name_raw"], row["file_name"])
    fills: dict[str, str] = {}
    empty = lambda v: v is None or not str(v).strip()  # noqa: E731
    if empty(row["dosage"]) and hdr["strength"]:
        fills["dosage"] = hdr["strength"]
    if empty(row["generic_name_en"]) and hdr["inn_en"]:
        fills["generic_name_en"] = hdr["inn_en"]
    if empty(row["generic_name"]) and ko:
        fills["generic_name"] = ko
    if empty(row["manufacturer"]) and hdr["manufacturer"]:
        fills["manufacturer"] = hdr["manufacturer"]
    conflict = None
    sd = row["session_date"] or ""
    if not sd or sd.endswith(PLACEHOLDER):
        picked = pick_session_date(hdr["session_dates"], row)
        if picked and (not sd or picked[:4] == sd[:4]):
            fills["session_date"] = picked
        elif picked:
            conflict = f"session_date 연도 불일치: DB {sd} / 원문 {picked}"
    return {"fills": fills, "conflict": conflict, "extractable_now": looks_extractable(pages[:3]) if pages else False,
            "header_empty": not (hdr["strength"] or hdr["inn_en"] or hdr["manufacturer"])}


def run(db_path: Path, pdf_dir: Path, apply: bool, include_visibility: bool, report_path: Path | None) -> dict:
    byname = {unicodedata.normalize("NFC", p.name): p for p in pdf_dir.rglob("*.pdf")}
    # dry-run 은 읽기 전용으로 연다 — 실수로도 DB 가 바뀌지 않게.
    conn = sqlite3.connect(db_path) if apply else sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM analog_reports ORDER BY id").fetchall()
    stats = {"visible": collections.Counter(), "hidden": collections.Counter()}
    examples: dict[str, list] = collections.defaultdict(list)
    plans: list[tuple[int, dict]] = []
    missing_pdf = []
    conflicts = []
    flip_ids = []
    for r in rows:
        pop = "visible" if (r["pdf_extractable"] in (None, 1)) else "hidden"
        stats[pop]["rows"] += 1
        p = byname.get(unicodedata.normalize("NFC", r["file_name"]))
        if not p:
            missing_pdf.append(r["file_name"])
            stats[pop]["pdf_not_found"] += 1
            continue
        pages = read_pages(p, 1, 3)
        plan = plan_row(r, pages)
        if plan["conflict"]:
            conflicts.append({"id": r["id"], "file": r["file_name"][:50], "why": plan["conflict"]})
            stats[pop]["conflict"] += 1
        for col in plan["fills"]:
            stats[pop][f"fill_{col}"] += 1
            if len(examples[col]) < 4:
                examples[col].append({"id": r["id"], "brand": r["brand_name"], "old": r[col], "new": plan["fills"][col]})
        if plan["header_empty"]:
            stats[pop]["header_empty_in_source"] += 1
        if pop == "hidden" and plan["extractable_now"]:
            stats[pop]["would_be_visible"] += 1
            flip_ids.append(r["id"])
        if plan["fills"]:
            plans.append((r["id"], plan["fills"]))
    summary = {"mode": "apply" if apply else "dry-run", "db": str(db_path), "rows": len(rows),
               "stats": {k: dict(v) for k, v in stats.items()}, "rows_with_fills": len(plans),
               "conflicts": conflicts[:20], "pdf_not_found": missing_pdf[:10], "examples": examples,
               "visibility_flip_candidates": len(flip_ids)}
    if apply:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = db_path.with_name(f"{db_path.stem}.pre_analog_gaps.{stamp}.db")
        conn.execute("VACUUM INTO ?", (str(backup),))
        before: list[dict] = []
        with conn:
            for rid, fills in plans:
                old = conn.execute(f"SELECT {', '.join(fills)} FROM analog_reports WHERE id=?", (rid,)).fetchone()
                before.append({"id": rid, "old": dict(old), "new": fills})
                conn.execute(f"UPDATE analog_reports SET {', '.join(f'{c}=?' for c in fills)} WHERE id=?", [*fills.values(), rid])
            if include_visibility and flip_ids:
                conn.executemany("UPDATE analog_reports SET pdf_extractable=1 WHERE id=?", [(i,) for i in flip_ids])
                summary["visibility_flipped"] = len(flip_ids)
        try:
            conn.execute("INSERT INTO analog_fts(analog_fts) VALUES('rebuild')")
            conn.commit()
        except sqlite3.Error as e:
            summary["fts_rebuild_error"] = str(e)
        undo = backup.with_suffix(".undo.json")
        undo.write_text(json.dumps(before, ensure_ascii=False), encoding="utf-8")
        summary.update({"backup_db": str(backup), "undo_json": str(undo)})
    if report_path:
        report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="data/db/drug_prices.db")
    ap.add_argument("--pdf-dir", default="DREC Raw")
    ap.add_argument("--apply", action="store_true", help="빈 칸만 채워 DB 에 반영(백업·되돌리기 JSON 생성)")
    ap.add_argument("--include-visibility", action="store_true", help="텍스트 PDF 로 확인된 '추출 불가' 행을 기본 검색에 노출")
    ap.add_argument("--report", default=None)
    a = ap.parse_args()
    res = run(Path(a.db), Path(a.pdf_dir), a.apply, a.include_visibility, Path(a.report) if a.report else None)
    print(json.dumps(res, ensure_ascii=False, indent=1)[:6000])


if __name__ == "__main__":
    main()
