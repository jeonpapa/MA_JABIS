"""심평원 급여기준 DB 파이프라인 CLI.

  python3 -m agents.hira_criteria.pipeline kcd            # 최신 상병마스터 → staging/common.db
  python3 -m agents.hira_criteria.pipeline onco           # 항암 통합본·허가초과 → staging/onco.db + md/onco
  python3 -m agents.hira_criteria.pipeline drug           # 일반약제 → staging/drug.db + md/drug
  python3 -m agents.hira_criteria.pipeline drug-update    # 일반약제: 바뀐 항목만 갱신·재매핑(월별)
  python3 -m agents.hira_criteria.pipeline tables         # 깨진 표 → 고시 원본 HWP 로 복원 (스테이징)
  python3 -m agents.hira_criteria.pipeline refine         # 일반약제 문단↔질환 정리만 (LLM 재호출 없음)
  python3 -m agents.hira_criteria.pipeline index          # 질환(KCD)별 색인 md/disease
  python3 -m agents.hira_criteria.pipeline apply onco drug common   # 스테이징 → data/db/drug_prices.db

기본은 스테이징까지만(dry-run). 본 DB 반영은 apply 명령으로만 한다.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from . import kcd as kcd_mod
from . import sources
from .http import download
from .store import RAW_DIR, apply_to_main, connect_staging, record_source

log = logging.getLogger("hira_criteria")


def run_kcd() -> dict:
    meta = sources.kcd_master_latest()
    att = next((a for a in meta["attachments"] if "상병마스터" in a["label"]), meta["attachments"][0])
    f = download(att["url"], RAW_DIR / "kcd", expect=("zip",), name_hint="상병마스터.xlsx")
    rows = kcd_mod.load_master_rows(f["path"])
    version = f"{att['label'] or f['filename']} (게시 {meta['posted_date']})"
    conn = connect_staging("common")
    with conn:
        record_source(conn, kind="kcd_master", sha256=f["sha256"], title=meta["title"], posted_date=meta["posted_date"],
                      url=meta["url"], file_path=str(f["path"]))
        stats = kcd_mod.store(conn, rows, version)
    conn.close()
    return {"post": meta["title"], "posted": meta["posted_date"], **stats}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hira_criteria")
    ap.add_argument("cmd", choices=["kcd", "onco", "drug", "drug-update", "tables", "refine", "index", "apply", "check"])
    ap.add_argument("tracks", nargs="*")
    ap.add_argument("--force", action="store_true", help="kordoc 변환 캐시 무시")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if a.cmd == "kcd":
        out = run_kcd()
    elif a.cmd == "onco":
        from .onco_pipeline import run as run_onco
        out = run_onco(force=a.force)
    elif a.cmd == "drug":
        from .drug_pipeline import run as run_drug
        out = run_drug(force=a.force)
    elif a.cmd == "drug-update":                  # 바뀐 항목만 갱신·재매핑(월별 갱신용)
        from .drug_pipeline import run_incremental
        out = run_incremental()
    elif a.cmd == "tables":                       # 깨진 표 → 고시 HWP 로 복원(스테이징), 이어서 문단·질환 정리
        from .drug_tables import recover_items
        from .drug_sections import refine
        c = connect_staging("drug")
        items = [dict(r) for r in c.execute("SELECT * FROM drug_criteria_item")]
        rep = recover_items(items)
        with c:
            for it in items:
                if "table_from_hwp" in (it.get("parse_flags") or ""):
                    c.execute("UPDATE drug_criteria_item SET criteria_text=?, parse_flags=?, latest_url=? WHERE item_key=?",
                              (it["criteria_text"], it["parse_flags"], it["latest_url"], it["item_key"]))
            st = refine(c, "common")
        c.close()
        out = {"tables": rep, "refine": {k: v for k, v in st.items() if k != "changes"}}
    elif a.cmd == "refine":                       # LLM 재호출 없이 일반약제 문단·질환 정리만 다시
        from .drug_sections import refine
        c = connect_staging("drug")
        with c:
            st = refine(c, "common")
        c.close()
        out = {k: v for k, v in st.items() if k != "changes"} | {"changes_sample": st["changes"][:40], "changes_total": len(st["changes"])}
    elif a.cmd == "index":
        from .md_export import build_disease_index
        out = build_disease_index()
    elif a.cmd == "check":
        from .verify import check_latest
        out = check_latest(a.tracks or ["onco", "drug"])
    else:
        if not a.tracks:
            ap.error("apply 에는 트랙(onco drug common)을 지정")
        out = apply_to_main(a.tracks)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
