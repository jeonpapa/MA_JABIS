"""일반약제 트랙: 최신 세부사항(약제) 책자 → 항목 구조화 → 개정 고시로 최신화 → KCD 매핑 → staging/drug.db + md/drug.

  python3 -m agents.hira_criteria.pipeline drug          (pipeline.py 가 run() 호출)
  python3 -m agents.hira_criteria.drug_pipeline [--no-llm] [--include-guidance] [--force]

본 DB 에는 쓰지 않는다(staging 까지). 반영은 pipeline apply drug.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from . import drug_booklet as dbk
from . import drug_disease as dd
from . import drug_latest as dl
from . import sources
from .http import download
from .kordoc_runner import convert
from .store import DATA_DIR, MD_DIR, RAW_DIR, connect_staging, record_source

log = logging.getLogger("hira_criteria.drug")
CHUNK = 50
REPORT_PATH = DATA_DIR / "drug_report.json"
MD_DRUG = MD_DIR / "drug"
ATTRIBUTION = "출처: 건강보험심사평가원 「요양급여의 적용기준 및 방법에 관한 세부사항(약제)」 및 보험인정기준(고시) — 공공누리 제4유형(출처표시·상업적 이용금지·변경금지)."


def _page_count(pdf: Path) -> int:
    r = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, timeout=60)
    m = re.search(r"Pages:\s+(\d+)", r.stdout)
    if not m:
        raise RuntimeError("pdfinfo 페이지 수 실패")
    return int(m.group(1))


def convert_chunks(pdf: Path, force: bool = False) -> tuple[str, list[dict]]:
    n = _page_count(pdf)
    parts, log_rows = [], []
    for a in range(1, n + 1, CHUNK):
        b = min(a + CHUNK - 1, n)
        err = None
        for attempt in range(2):
            try:
                p = convert(pdf, pages=f"{a}-{b}", timeout=1800, force=force and attempt == 0)
                parts.append(p.read_text(encoding="utf-8"))
                log_rows.append({"pages": f"{a}-{b}", "ok": True, "md": str(p)})
                err = None
                break
            except Exception as e:  # noqa: BLE001
                err = str(e)[:300]
        if err:
            log_rows.append({"pages": f"{a}-{b}", "ok": False, "error": err})
            raise RuntimeError(f"kordoc 청크 실패 {a}-{b}: {err}")
    return "\n".join(parts), log_rows


def slug(s: str, n: int = 60) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = re.sub(r"\(\s*품\s*명.*$", "", s)
    s = re.sub(r"[^0-9A-Za-z가-힣]+", "-", s).strip("-").lower()
    return s[:n].strip("-") or "item"


def items_to_rows(items: list[dbk.Item], source_sha: str) -> list[dict]:
    rows, seen = [], {}
    for it in items:
        text = it.criteria_text
        g, eff = dbk.latest_gosi(text)
        key = f"{it.class_no}|{dbk.norm_key(it.title)}"
        if key in seen:
            seen[key] += 1
            key = f"{key}#{seen[key]}"
        else:
            seen[key] = 1
        flags = list(dict.fromkeys(it.flags))
        if not g:
            flags.append("no_gosi_marker")
        rows.append({"item_key": key, "source_sha": source_sha, "class_no": it.class_no, "title": it.title,
                     "ingredient": it.ingredient, "dosage_form": it.dosage_form, "brand_text": it.brand_text,
                     "criteria_text": text, "gosi_no": g, "effective_date": eff, "latest_basis": "booklet",
                     "latest_url": None, "md_path": None, "parse_flags": ",".join(flags)})
    return rows


def reconcile_index(rows: list[dict], index: list[dict]) -> dict:
    """색인(분류번호순) ↔ 파싱 항목. 분류+성분키 앞부분으로 매칭."""
    def k(cls, title):
        return cls, dl.ingredient_key(title)[:14]
    parsed = {}
    for r in rows:
        parsed.setdefault(k(r["class_no"], r["title"]), []).append(r)
    unmatched = []
    hit = 0
    for e in index:
        key = k(e["class_no"], e["title"])
        cand = parsed.get(key)
        if not cand:  # 색인 제목 앞에 품명 조각이 붙는 경우 — 같은 분류·같은 고시번호로 대체 매칭
            cand = [r for r in rows if r["class_no"] == e["class_no"] and r["gosi_no"] == e["gosi_no"]
                    and dl.ingredient_key(r["title"])[:8] in dl.match_key(e["title"])]
        if cand:
            hit += 1
        else:
            unmatched.append({"class_no": e["class_no"], "title": e["title"], "gosi": e["gosi_no"], "page": e["page"]})
    idx_keys = {k(e["class_no"], e["title"]) for e in index}
    extra = [r["title"] for r in rows if r["class_no"] != "한약제제" and k(r["class_no"], r["title"]) not in idx_keys]
    # 색인의 고시가 본문 표식보다 새로우면 색인 값을 채택(본문 표식 누락·합쳐진 행 분할 오차 보정)
    gosi_mismatch = []
    for e in index:
        for r in parsed.get(k(e["class_no"], e["title"]), [])[:1]:
            if r["gosi_no"] == e["gosi_no"]:
                continue
            gosi_mismatch.append({"title": r["title"], "index_gosi": e["gosi_no"], "parsed_gosi": r["gosi_no"]})
            if dl.gosi_tuple(e["gosi_no"]) > dl.gosi_tuple(r["gosi_no"]):
                r["gosi_no"], r["effective_date"] = e["gosi_no"], e["effective_date"]
                r["parse_flags"] = ",".join([x for x in r["parse_flags"].split(",") if x] + ["gosi_from_index"])
    return {"index_entries": len(index), "parsed_items_drug": sum(1 for r in rows if r["class_no"] != "한약제제"),
            "index_matched": hit, "index_unmatched": unmatched, "parsed_not_in_index_count": len(extra),
            "parsed_not_in_index_sample": extra[:40], "gosi_mismatch_vs_index": gosi_mismatch}


def write_md(row: dict, maps: list[dict], booklet: dict) -> Path:
    MD_DRUG.mkdir(parents=True, exist_ok=True)
    fn = MD_DRUG / f"{slug(row['class_no'], 12)}_{slug(row['ingredient'] or row['title'])}.md"
    i = 2
    while fn.exists() and fn.read_text(encoding="utf-8").find(f"item_key: `{row['item_key']}`") < 0:
        fn = MD_DRUG / f"{slug(row['class_no'], 12)}_{slug(row['ingredient'] or row['title'])}-{i}.md"
        i += 1
    basis = ("개정 고시(책자 이후) — " + (row.get("latest_url") or "")) if row["latest_basis"] != "booklet" else \
        f"{booklet['title']} (게시 {booklet['posted_date']}) — {booklet['url']}"
    lines = [f"# {row['title']}", "",
             f"- 분류번호: {row['class_no']}",
             f"- 성분 / 제형: {row['ingredient'] or '-'} / {row['dosage_form'] or '-'}",
             f"- 품명: {row['brand_text'] or '-'}",
             f"- 최신 고시: {row['gosi_no'] or '(표식 없음)'} · 시행 {row['effective_date'] or '-'}",
             f"- 최신 근거: {basis}",
             f"- 최신 검증: {row.get('_latest_status') or '-'} (보험인정기준 고시 목록 대조)",
             f"- item_key: `{row['item_key']}`"]
    if row.get("parse_flags"):
        lines.append(f"- 플래그: {row['parse_flags']}")
    lines += ["", "## 급여 대상 질환 (KCD-9, 심평원 상병마스터)", ""]
    if "onco_delegated" in (row.get("parse_flags") or ""):
        lines.append("> 항암요법 투여는 「암환자에게 처방·투여하는 약제에 대한 요양급여의 적용기준 및 방법에 관한 세부사항」(항암 공고) 범위를 따른다.\n")
    if maps:
        lines += ["| KCD | 상병명 | 원문 표현 | 방법 | 신뢰도 | 검토 |", "|---|---|---|---|---|---|"]
        for m in maps:
            lines.append(f"| {m['kcd_code']} | {m.get('kcd_name','')} | {m['disease_label']} | {m['method']} | "
                         f"{m['confidence']:.2f} | {m['review_status']} |")
    else:
        lines.append("(매핑된 질환 없음 — 행정 기준이거나 질환 표현을 찾지 못함)")
    lines += ["", "## 세부인정기준 및 방법 (원문)", "", row["criteria_text"] or "", "", "---", ATTRIBUTION, ""]
    fn.write_text("\n".join(lines), encoding="utf-8")
    return fn


def run(force: bool = False, use_llm: bool = True, include_guidance: bool = False, workers: int = 8) -> dict:
    t0 = datetime.now()
    booklet = sources.drug_booklet_latest()
    f = download(booklet["url"], RAW_DIR / "drug", expect=("pdf",), name_hint="세부사항_약제.pdf")
    md_all, chunk_log = convert_chunks(f["path"], force=force)

    items, pstats = dbk.parse_items(dbk.body_slice(md_all))
    herbal = dbk.parse_herbal(dbk.herbal_slice(md_all))
    rows = items_to_rows(items + herbal, f["sha256"])
    index = dbk.parse_index(f["path"])
    recon = reconcile_index(rows, index)

    # 최신 검증 (개정 고시) — 한약제제는 대상 아님
    drug_rows = [r for r in rows if r["class_no"] != "한약제제"]
    latest = dl.apply_latest(drug_rows)
    for r in drug_rows:
        if r.get("latest_basis") == "gosi_new" and "item_key" not in r:
            r.update(item_key=f"{r['class_no']}|{dbk.norm_key(r['title'])}", source_sha=None,
                     ingredient=dbk.parse_title(r["title"])[0], dosage_form=dbk.parse_title(r["title"])[1],
                     brand_text=dbk.parse_title(r["title"])[2], md_path=None)
    rows = drug_rows + [r for r in rows if r["class_no"] == "한약제제"]
    # 깨진 표(세로 머리행·첨부 참고) → 고시 원본 HWP 로 복원
    from .drug_tables import recover_items
    tables = recover_items(rows)

    # KCD 매핑
    conn = connect_staging("drug")
    idx = dd.KcdIndex(conn, "common")
    llm = dd.LLM() if use_llm else None
    targets = [r for r in rows if r["class_no"] != "한약제제"]

    # sqlite 연결은 스레드 간 공유 불가 → 이름검색용 연결은 스레드마다 따로
    import sqlite3, threading
    local = threading.local()
    from .store import staging_path

    def work_threaded(r):
        if not hasattr(local, "idx"):
            c = sqlite3.connect(staging_path("drug"))
            c.execute("ATTACH DATABASE ? AS common", (str(staging_path("common")),))
            li = dd.KcdIndex.__new__(dd.KcdIndex)
            li.conn, li.schema, li.names, li.code_name = c, "common", idx.names, idx.code_name
            local.idx = li
        try:
            return (r, *dd.map_item(r, local.idx, llm))
        except Exception as e:  # noqa: BLE001
            return r, [], [f"map_error:{str(e)[:60]}"]
    with ThreadPoolExecutor(max_workers=workers if llm else 1) as ex:
        results = list(ex.map(work_threaded, targets))
    maps_by_key = {}
    for r, maps, flags in results:
        if flags:
            r["parse_flags"] = ",".join([x for x in (r.get("parse_flags") or "").split(",") if x] + flags)
        for m in maps:
            m["kcd_name"] = idx.name(m["kcd_code"])
        maps_by_key[r["item_key"]] = maps

    # md
    if MD_DRUG.exists():
        for old in MD_DRUG.glob("*.md"):
            old.unlink()
    for r in rows:
        r["md_path"] = str(write_md(r, maps_by_key.get(r["item_key"], []), booklet).relative_to(DATA_DIR.parents[1]))
    gs = dbk.guidance_slice(md_all)
    if gs:
        (MD_DIR / "drug_guidance_full.md").write_text(gs, encoding="utf-8")

    # staging
    cols = ["item_key", "source_sha", "class_no", "title", "ingredient", "dosage_form", "brand_text", "criteria_text",
            "gosi_no", "effective_date", "latest_basis", "latest_url", "md_path", "parse_flags"]
    with conn:
        record_source(conn, kind="drug_booklet", sha256=f["sha256"], title=booklet["title"],
                      posted_date=booklet["posted_date"], url=booklet["url"], file_path=str(f["path"]))
        for rep in latest["replaced"] + latest["added"]:
            record_source(conn, kind="drug_gosi_item", sha256=f"url:{rep['url']}", title=rep["title"],
                          notice_no=rep.get("to") or rep.get("gosi"), effective_date=rep.get("effective"), url=rep["url"])
        conn.execute("DELETE FROM drug_criteria_item")
        conn.execute("DELETE FROM criteria_disease_kcd WHERE subject_type='drug_item' AND method!='manual'")
        for r in rows:
            conn.execute(f"INSERT INTO drug_criteria_item({','.join(cols)}) VALUES ({','.join('?'*len(cols))})",
                         [r.get(c) for c in cols])
            for m in maps_by_key.get(r["item_key"], []):
                if not idx.exists(m["kcd_code"]):
                    continue  # 존재하지 않는 코드 저장 금지 (이중 확인)
                conn.execute("INSERT OR IGNORE INTO criteria_disease_kcd(subject_type,subject_key,kcd_code,disease_label,"
                             "method,confidence,review_status,note) VALUES ('drug_item',?,?,?,?,?,?,?)",
                             (r["item_key"], m["kcd_code"], m["disease_label"], m["method"], m["confidence"],
                              m["review_status"], None))
        from .drug_sections import refine
        sec_stats = refine(conn, "common")       # 문단 기준 질환 정리(긴 표현 우선·언급/대상 구분)
    n_items = conn.execute("SELECT COUNT(*) FROM drug_criteria_item").fetchone()[0]
    n_map = conn.execute("SELECT COUNT(*) FROM criteria_disease_kcd WHERE subject_type='drug_item'").fetchone()[0]
    n_mapped_items = conn.execute("SELECT COUNT(DISTINCT subject_key) FROM criteria_disease_kcd WHERE subject_type='drug_item'").fetchone()[0]
    n_review = conn.execute("SELECT COUNT(*) FROM criteria_disease_kcd WHERE subject_type='drug_item' AND review_status='needs_review'").fetchone()[0]
    conn.close()

    onco_del = sum(1 for r in rows if "onco_delegated" in (r.get("parse_flags") or ""))
    report = {
        "booklet": booklet, "booklet_sha256": f["sha256"], "kordoc_chunks": len(chunk_log),
        "parse_stats": pstats, "items_total": n_items, "herbal_items": len(herbal), "index": recon,
        "latest": latest, "tables": tables,
        "kcd": {"mapping_rows": n_map, "items_with_mapping": n_mapped_items,
                "items_target": len(targets), "mapping_rate": round(n_mapped_items / max(len(targets), 1), 3),
                "needs_review_rows": n_review, "onco_delegated_items": onco_del,
                "llm_calls": llm.calls if llm else 0, "llm_errors": llm.errors if llm else 0,
                "llm_model": dd.MODEL if llm else None},
        "sections": {k: v for k, v in sec_stats.items() if k != "changes"}, "section_changes": sec_stats["changes"],
        "flags": _flag_counts(rows), "elapsed_sec": (datetime.now() - t0).seconds,
        "guidance_md": bool(gs),
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return report


def _flag_counts(rows):
    from collections import Counter
    return dict(Counter(f for r in rows for f in (r.get("parse_flags") or "").split(",") if f))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--include-guidance", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    out = run(force=a.force, use_llm=not a.no_llm, include_guidance=a.include_guidance)
    s = {k: v for k, v in out.items() if k not in ("index", "latest")}
    s["index_summary"] = {k: (v if not isinstance(v, list) else len(v)) for k, v in out["index"].items()}
    s["latest_summary"] = {k: (v if not isinstance(v, list) else len(v)) for k, v in out["latest"].items()}
    print(json.dumps(s, ensure_ascii=False, indent=2, default=str))


def run_incremental(use_llm: bool = True) -> dict:
    """책자·최신 고시·표 복원을 다시 계산해 스테이징과 비교 → 바뀐/새 항목만 갱신하고 그 항목만 KCD 재매핑(LLM).
    월별 갱신·매칭 규칙 수정 뒤 전체 LLM 재실행 없이 반영할 때 쓴다. 이어서 문단·질환 정리(refine)."""
    from .drug_sections import refine
    from .drug_tables import recover_items
    booklet = sources.drug_booklet_latest()
    f = download(booklet["url"], RAW_DIR / "drug", expect=("pdf",), name_hint="세부사항_약제.pdf")
    md_all, _ = convert_chunks(f["path"])
    items, _ = dbk.parse_items(dbk.body_slice(md_all))
    herbal = dbk.parse_herbal(dbk.herbal_slice(md_all))
    rows = items_to_rows(items + herbal, f["sha256"])
    reconcile_index(rows, dbk.parse_index(f["path"]))        # 책자 색인의 고시번호(전체 실행과 같은 순서)
    drug_rows = [r for r in rows if r["class_no"] != "한약제제"]
    latest = dl.apply_latest(drug_rows)
    for r in drug_rows:
        if r.get("latest_basis") == "gosi_new" and "item_key" not in r:
            r.update(item_key=f"{r['class_no']}|{dbk.norm_key(r['title'])}", source_sha=None,
                     ingredient=dbk.parse_title(r["title"])[0], dosage_form=dbk.parse_title(r["title"])[1],
                     brand_text=dbk.parse_title(r["title"])[2], md_path=None)
    rows = drug_rows + [r for r in rows if r["class_no"] == "한약제제"]
    tables = recover_items(rows)
    conn = connect_staging("drug")
    old = {r["item_key"]: dict(r) for r in conn.execute("SELECT * FROM drug_criteria_item")}
    new = {r["item_key"]: r for r in rows}
    changed = [k for k in new if k in old and (new[k].get("criteria_text") != old[k]["criteria_text"]
                                                or new[k].get("gosi_no") != old[k]["gosi_no"])]
    added = [k for k in new if k not in old]
    removed = [k for k in old if k not in new]
    if not new:
        raise RuntimeError("새 항목이 비어 있음 — 반영 거부")
    idx = dd.KcdIndex(conn, "common")
    llm = dd.LLM() if use_llm else None
    cols = ["item_key", "source_sha", "class_no", "title", "ingredient", "dosage_form", "brand_text", "criteria_text",
            "gosi_no", "effective_date", "latest_basis", "latest_url", "md_path", "parse_flags"]
    with conn:
        for k in removed:
            conn.execute("DELETE FROM drug_criteria_item WHERE item_key=?", (k,))
            conn.execute("DELETE FROM criteria_disease_kcd WHERE subject_type='drug_item' AND subject_key=? AND method!='manual'", (k,))
        for k in changed + added:
            r = new[k]
            r.setdefault("md_path", (old.get(k) or {}).get("md_path"))
            conn.execute(f"INSERT OR REPLACE INTO drug_criteria_item({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                         [r.get(c) for c in cols])
            if r["class_no"] == "한약제제":
                continue
            maps, _flags = dd.map_item(r, idx, llm)
            conn.execute("DELETE FROM criteria_disease_kcd WHERE subject_type='drug_item' AND subject_key=? AND method!='manual'", (k,))
            for m in maps:
                if idx.exists(m["kcd_code"]):
                    conn.execute("INSERT OR IGNORE INTO criteria_disease_kcd(subject_type,subject_key,kcd_code,disease_label,"
                                 "method,confidence,review_status,note) VALUES ('drug_item',?,?,?,?,?,?,?)",
                                 (k, m["kcd_code"], m["disease_label"], m["method"], m["confidence"], m["review_status"], None))
        st = refine(conn, "common")
    conn.close()
    return {"changed": [new[k]["title"] for k in changed], "added": [new[k]["title"] for k in added],
            "removed": [old[k]["title"] for k in removed], "tables": tables,
            "latest": {k: v for k, v in latest.items() if k in ("replaced", "added", "deleted", "errors")},
            "refine": {k: v for k, v in st.items() if k != "changes"}}
