"""항암 트랙: 최신 통합본(공고책자) + 허가초과 xlsx → staging/onco.db + md/onco.

최신성 원칙
- 통합본 파일명 날짜(공고책자_YYYYMMDD)와 공고 게시판 최신 공고의 시행일을 비교한다.
  통합본이 최신 공고보다 오래되면 status='stale' 로 보고하고(덮어쓰지 않음), 최신 공고의 신설 요법이
  파싱 결과에 있는지 대조한다.
"""
from __future__ import annotations

import json
import logging
import re

from . import onco_kcd, onco_offlabel, onco_parser, sources
from .http import download
from .kordoc_runner import convert
from .store import MD_DIR, RAW_DIR, connect_staging, record_source

log = logging.getLogger(__name__)

REG_COLS = ("regimen_key", "source_sha", "cancer_code", "cancer_name_kr", "cancer_name_en", "setting_code",
            "setting_raw", "subgroup_raw", "line_code", "line_raw", "line_source", "regimen_no", "regimen_text",
            "drugs_json", "target_text", "prior_therapy", "biomarker_text", "stage_text", "self_pay", "footnotes",
            "notice_history", "last_notice_no", "last_effective", "extra_json", "md_path", "parse_flags")


def _nreg(s: str) -> str:
    s = re.sub(r"\[[^\]]*\]|\([^)]*\)", "", s.lower())
    return re.sub(r"[\s‘’'\"]+", "", s)


def latest_notice_check(rows: list[dict], notice: dict, consolidated_date: str | None) -> dict:
    """최신 공고 요약의 <신설>/<변경> 요법은 '있어야', <삭제> 요법은 '없어야' 정상."""
    summ = notice.get("summary", "")
    parts = re.split(r"<(신설|변경|삭제)>", summ)
    blocks = {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}
    regs = {_nreg(r["regimen_text"]) for r in rows}
    found, missing, deleted_ok, deleted_still = [], [], [], []
    for kind, body in blocks.items():
        for it in re.findall(r"[‘'](.+?)[’']", body):
            n = _nreg(it)
            present = any(n == g or (len(n) > 6 and n in g) for g in regs)
            if kind == "삭제":
                (deleted_still if any(n == g for g in regs) else deleted_ok).append(it)
            else:
                (found if present else missing).append(f"[{kind}] {it}")
    status = "current"
    if consolidated_date and notice.get("effective_date") and notice["effective_date"] > consolidated_date:
        status = "stale"
    return {"latest_notice": notice.get("notice_no"), "latest_effective": notice.get("effective_date"),
            "consolidated_date": consolidated_date, "status": status,
            "notice_items_found": found, "notice_items_missing": missing,
            "deleted_absent_ok": deleted_ok, "deleted_but_present": deleted_still}


def run(force: bool = False) -> dict:
    files = sources.onco_form_files()
    notice = sources.onco_latest_notice()
    raw = RAW_DIR / "onco"
    cons = download(files["consolidated"]["url"], raw, expect=("pdf",), name_hint="공고책자.pdf")
    cons_date = sources.date_from_filename(cons["filename"])
    log.info("통합본 %s (%s) → kordoc", cons["filename"], cons_date)
    md_path = convert(cons["path"], force=force)
    md = md_path.read_text(encoding="utf-8")
    recs, stats = onco_parser.parse(md)
    rows = onco_parser.to_rows(recs)

    # 원문 전문 .md 보관
    MD_DIR.joinpath("onco").mkdir(parents=True, exist_ok=True)
    full_md = MD_DIR / "onco" / f"_전문_{cons['filename'].rsplit('.', 1)[0]}.md"
    full_md.write_text(f"<!-- 출처: 건강보험심사평가원 {files['consolidated']['title']} ({cons['filename']}), "
                       f"{cons['url']} · 공공누리 제4유형 -->\n\n" + md, encoding="utf-8")

    off = None
    if "offlabel" in files:
        off_f = download(files["offlabel"]["url"], raw, expect=("zip",), name_hint="허가초과.xlsx")
        off = onco_offlabel.parse(off_f["path"])

    conn = connect_staging("onco")
    missing_kcd = onco_kcd.validate(conn)
    if missing_kcd:
        raise RuntimeError(f"KCD 사전 코드가 상병마스터에 없음: {missing_kcd}")
    with conn:
        for t in ("onco_criteria_regimen", "onco_criteria_regimen_drug", "onco_offlabel_regimen",
                  "onco_offlabel_rejected"):
            conn.execute(f"DELETE FROM {t}")
        conn.execute("DELETE FROM criteria_disease_kcd WHERE subject_type IN ('onco_regimen','onco_offlabel')")
        record_source(conn, kind="onco_consolidated", sha256=cons["sha256"],
                      title=f"{files['consolidated']['title']} ({cons['filename']})",
                      effective_date=cons_date, url=cons["url"], file_path=str(cons["path"]), md_path=str(full_md))
        record_source(conn, kind="onco_notice", sha256=f"notice:{notice['notice_no']}", title=notice["notice_no"],
                      notice_no=notice["notice_no"], effective_date=notice["effective_date"],
                      posted_date=notice["posted_date"], url=notice["url"])
        kcd_stats = {"mapped": 0, "needs_review": 0, "unmapped": 0}
        for r, rec in zip(rows, recs):
            r["source_sha"] = cons["sha256"]
            r["md_path"] = ""
            conn.execute(f"INSERT INTO onco_criteria_regimen({','.join(REG_COLS)}) VALUES ({','.join('?'*len(REG_COLS))})",
                         [r.get(c) for c in REG_COLS])
            for i, d in enumerate(rec.drugs, 1):
                conn.execute("INSERT OR IGNORE INTO onco_criteria_regimen_drug VALUES (?,?,?)", (r["regimen_key"], i, d))
            for m in onco_kcd.kcd_for_regimen({**r, "extra": rec.extra}):
                if not m["kcd_code"]:
                    k = m["note"] if m["note"] in ("not_applicable", "tumor_agnostic") else "unmapped"
                    kcd_stats[k] = kcd_stats.get(k, 0) + 1
                    if k == "unmapped":
                        kcd_stats.setdefault("unmapped_samples", []).append(
                            f"{r['cancer_code']}|{r['regimen_no']}|{m['disease_label'][:40]}")
                    continue
                conn.execute("INSERT OR IGNORE INTO criteria_disease_kcd(subject_type,subject_key,kcd_code,disease_label,"
                             "method,confidence,review_status,note) VALUES ('onco_regimen',?,?,?,?,?,?,?)",
                             (r["regimen_key"], m["kcd_code"], m["disease_label"], m["method"], m["confidence"],
                              m["review_status"], m["note"]))
                kcd_stats["mapped"] += 1
                kcd_stats["needs_review"] += m["review_status"] == "needs_review"
        off_stats = {}
        if off:
            record_source(conn, kind="onco_offlabel", sha256=off_f["sha256"], title=off_f["filename"],
                          effective_date=sources.date_from_filename("20" + (re.search(r"_(\d{6})", off_f["filename"]) or [None, "000000"])[1]),
                          url=off_f["url"], file_path=str(off_f["path"]))
            seen_codes: dict[str, int] = {}
            for a in off["approved"]:
                n = seen_codes.get(a["regimen_code"], 0) + 1
                seen_codes[a["regimen_code"]] = n
                if n > 1:  # 같은 요법코드가 여러 행(투여대상 변형) — 덮어쓰지 않고 접미사
                    a = {**a, "regimen_code": f"{a['regimen_code']}-{n}"}
                conn.execute("INSERT OR REPLACE INTO onco_offlabel_regimen(regimen_code,source_sha,cancer,subcancer,regimen_text,"
                             "target_text,line_raw,line_code,setting_raw,cost_note,dosing_note) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                             (a["regimen_code"], off_f["sha256"], a["cancer"], a["subcancer"], a["regimen_text"],
                              a["target_text"], a["line_raw"], a["line_code"], a["setting_raw"], a["cost_note"],
                              a["dosing_note"]))
                cc = onco_kcd.cancer_of_text(a["cancer"] + a["subcancer"])
                if cc and onco_kcd.CANCER_KCD[cc][0]:
                    codes, conf, note = onco_kcd.CANCER_KCD[cc]
                    for c in codes:
                        conn.execute("INSERT OR IGNORE INTO criteria_disease_kcd(subject_type,subject_key,kcd_code,disease_label,"
                                     "method,confidence,review_status,note) VALUES ('onco_offlabel',?,?,?,?,?,?,?)",
                                     (a["regimen_code"], c, a["cancer"], "curated", conf,
                                      "auto" if conf >= 0.8 else "needs_review", note))
            for j in off["rejected"]:
                conn.execute("INSERT INTO onco_offlabel_rejected(seq,source_sha,cancer,regimen_text,reason,note) VALUES (?,?,?,?,?,?)",
                             (j["seq"], off_f["sha256"], j["cancer"], j["regimen_text"], j["reason"], j["note"]))
            off_stats = {"approved": len(off["approved"]), "distinct_codes": len(seen_codes),
                         "rejected": len(off["rejected"])}
    latest = latest_notice_check(rows, notice, cons_date)
    conn.close()

    from .md_export import export_onco
    md_files = export_onco()
    return {"consolidated": cons["filename"], "consolidated_date": cons_date, "kordoc_md": str(md_path),
            "parse": stats, "regimen_rows": len(rows), "kcd": kcd_stats, "offlabel": off_stats,
            "latest_check": latest, "md_files": len(md_files)}
