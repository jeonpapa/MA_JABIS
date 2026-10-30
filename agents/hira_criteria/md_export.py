"""Markdown 내보내기.

- md/onco/<code>_<암종>.md : 치료 순서 요약 → 투여요법별 → 투여단계별 요법 표 → 원문(kordoc) 섹션
- md/disease/<KCD3>_<질환명>.md : 이 질환(KCD 3단)에 급여되는 항암요법 + 허가초과 인정요법 + 일반약제 급여기준 항목
- md/disease/README.md : 질환 색인
모두 스테이징 DB 를 읽는다(본 DB 와 같은 내용이 apply 로 들어간다).
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

from .drug_sections import GROUP_NAME, gkey

from .store import MD_DIR, staging_path

SETTING_KR = {
    "neoadjuvant": "선행요법(수술 전, neoadjuvant)", "perioperative": "수술 전후(perioperative)",
    "adjuvant": "수술후보조요법(adjuvant)", "ccrt": "동시항암화학방사선요법(CCRT)", "primary": "일차요법(primary)",
    "induction": "관해유도요법(induction)", "consolidation": "관해공고요법(consolidation)",
    "maintenance": "유지요법(maintenance)", "palliative": "고식적요법(palliative)", "salvage": "구제요법(salvage)",
    "relapsed_refractory": "재발·불응(이전 치료 실패)", "newly_diagnosed": "새로 진단", "intravesical": "방광 내 주입",
    "conditioning": "전처치", "NA": "투여요법 구분 없음", "other": "기타",
}
SETTING_ORDER = list(SETTING_KR)
LINE_KR = {"1L": "1차", "1L_or_1L+": "1차 또는 1차 이상", "1L+": "1차 이상", "2L": "2차", "2L_or_3L": "2차 또는 3차",
           "2L_or_3L+": "2차 또는 3차 이상", "2L+": "2차 이상", "3L": "3차", "3L+": "3차 이상", "NA": "투여단계 구분 없음"}
LINE_ORDER = list(LINE_KR)
SOURCE_NOTE = "출처: 건강보험심사평가원(공공누리 제4유형: 출처표시·상업적 이용금지·변경금지). 원문 우선, 본 파일은 검색용 변환본."


def setting_label(code: str) -> str:
    return " + ".join(SETTING_KR.get(c, c) for c in code.split("|"))


def line_label(code: str) -> str:
    return " / ".join(LINE_KR.get(c, c) for c in code.split("|"))


def _skey(code: str) -> int:
    c = code.split("|")[0]
    return SETTING_ORDER.index(c) if c in SETTING_ORDER else 99


def _lkey(code: str) -> int:
    c = code.split("|")[0]
    return LINE_ORDER.index(c) if c in LINE_ORDER else 99


def _cell(s: str, n: int = 400) -> str:
    s = re.sub(r"\s*\n\s*", "<br>", (s or "").strip())
    s = s.replace("|", "\\|")
    return s if len(s) <= n else s[:n] + "…"


def _slug(s: str) -> str:
    return re.sub(r"[^\w가-힣\-]+", "_", s).strip("_")[:60]


def _conn(track: str) -> sqlite3.Connection:
    c = sqlite3.connect(staging_path(track))
    c.row_factory = sqlite3.Row
    common = staging_path("common")
    if common.exists() and track != "common":
        c.execute("ATTACH DATABASE ? AS common", (str(common),))
    return c


def _cancer_sort(code: str) -> list[int]:
    return [int(p) for p in code.split("-")]


def export_onco() -> list[Path]:
    conn = _conn("onco")
    out_dir = MD_DIR / "onco"
    out_dir.mkdir(parents=True, exist_ok=True)
    src = conn.execute("SELECT * FROM hira_criteria_source WHERE kind='onco_consolidated' ORDER BY source_id DESC LIMIT 1").fetchone()
    notice = conn.execute("SELECT * FROM hira_criteria_source WHERE kind='onco_notice' ORDER BY source_id DESC LIMIT 1").fetchone()
    raw_sections = {}
    if src and src["md_path"] and Path(src["md_path"]).exists():
        from .onco_parser import locate_cancers, toc_cancers
        md = Path(src["md_path"]).read_text(encoding="utf-8")
        lines = md.split("\n")
        start = next(i for i, s in enumerate(lines) if re.match(r"^#*\s*1\s*\t\s*소세포폐암", s.strip()))
        end = next((i for i, s in enumerate(lines) if i > start and re.match(r"^#\s*항구토제", s.strip())), len(lines))
        heads = [h for h in locate_cancers(lines, toc_cancers(md), start) if h[0] >= 0]
        for k, (li, code, *_rest) in enumerate(heads):
            stop = heads[k + 1][0] if k + 1 < len(heads) else end
            raw_sections[code] = "\n".join(lines[li:stop])
    rows = [dict(r) for r in conn.execute("SELECT * FROM onco_criteria_regimen")]
    kcd = defaultdict(list)
    for r in conn.execute("SELECT subject_key, kcd_code, confidence, review_status FROM criteria_disease_kcd WHERE subject_type='onco_regimen'"):
        kcd[r["subject_key"]].append(r)
    by_cancer = defaultdict(list)
    for r in rows:
        by_cancer[r["cancer_code"]].append(r)
    files = []
    head_note = (f"> 기준: {src['title'] if src else ''} "
                 f"(통합본 시행 기준일 {src['effective_date'] if src else '?'}) · 최신 공고 {notice['notice_no'] if notice else '?'} "
                 f"({notice['effective_date'] if notice else '?'} 시행)\n> {SOURCE_NOTE}\n")
    index_rows = []
    for code in sorted(by_cancer, key=_cancer_sort):
        rs = by_cancer[code]
        name, en = rs[0]["cancer_name_kr"], rs[0]["cancer_name_en"]
        kcodes = sorted({k["kcd_code"] for r in rs for k in kcd.get(r["regimen_key"], [])})
        kn = []
        for c in kcodes[:20]:
            row = conn.execute("SELECT name_kr FROM common.kcd_code WHERE code=?", (c,)).fetchone()
            kn.append(f"`{c}` {row[0] if row else ''}")
        L = [f"# {code}. {name}" + (f" ({en})" if en else ""), "", head_note,
             f"**KCD-9**: {', '.join(kn) if kn else '행 단위 매핑(아래 표 참조)'}", "",
             "## 치료 순서 요약", "", "| 투여요법 | 투여단계 | 요법 수 | 요법 |", "|---|---|---|---|"]
        grp = defaultdict(list)
        for r in rs:
            grp[(r["setting_code"], r["line_code"])].append(r)
        for (sc, lc) in sorted(grp, key=lambda k: (_skey(k[0]), _lkey(k[1]))):
            regs = list(dict.fromkeys(x["regimen_text"] for x in grp[(sc, lc)]))
            L.append(f"| {setting_label(sc)} | {line_label(lc)} | {len(regs)} | {_cell('; '.join(regs), 300)} |")
        for sc in sorted({k[0] for k in grp}, key=_skey):
            L += ["", f"## {setting_label(sc)}"]
            for lc in sorted({k[1] for k in grp if k[0] == sc}, key=_lkey):
                L += ["", f"### 투여단계: {line_label(lc)}", "",
                      "| 연번 | 항암요법 | 투여대상 | 이전 치료 조건 | 바이오마커 | 본인부담 | 최근 공고 |",
                      "|---|---|---|---|---|---|---|"]
                for r in sorted(grp[(sc, lc)], key=lambda x: (x["subgroup_raw"] or "", int(re.sub(r"\D.*", "", x["regimen_no"]) or 0), x["regimen_key"])):
                    extra = json.loads(r["extra_json"] or "{}")
                    reg = r["regimen_text"] + (f" ({extra['row_cancer']})" if extra.get("row_cancer") else "")
                    if r["subgroup_raw"]:
                        reg = f"[{r['subgroup_raw']}] " + reg
                    last = f"{r['last_notice_no']} ({r['last_effective']})" if r["last_notice_no"] else ""
                    L.append(f"| {r['regimen_no']} | {_cell(reg, 200)} | {_cell(r['target_text'])} | {_cell(r['prior_therapy'], 250)} | "
                             f"{_cell(r['biomarker_text'], 120)} | {r['self_pay'] or ''} | {last} |")
        # 40. 기타 항암제 병용요법 중 이 암종 행 (교차 연결)
        if code != "40":
            from .onco_kcd import cancer_of_text
            cross = [x for x in by_cancer.get("40", [])
                     if (cancer_of_text(json.loads(x["extra_json"] or "{}").get("row_cancer", ""))
                         or cancer_of_text(json.loads(x["extra_json"] or "{}").get("label", ""))) == code]
            if cross:
                L += ["", "## 기타 항암제 병용요법 (40번 표, 기존 항암요법 본인일부부담) 중 이 암종", "",
                      "> 병용 약제와 함께 쓰더라도 '급여요법'(기존 항암요법) 부분은 본인일부부담으로 인정하는 요법. "
                      "병용 약제 자체의 급여 여부는 위 암종 표(주요 암종별 항암요법)에서 확인.", "",
                      "| 연번 | 항암제 병용요법 | 급여요법 | 급여기준 | 투여요법 | 투여단계 | 최근 공고 |", "|---|---|---|---|---|---|---|"]
                for x in sorted(cross, key=lambda x: int(re.sub(r"\D.*", "", x["regimen_no"]) or 0)):
                    ex = json.loads(x["extra_json"] or "{}")
                    last = f"{x['last_notice_no']} ({x['last_effective']})" if x["last_notice_no"] else ""
                    L.append(f"| {x['regimen_no']} | {_cell(x['regimen_text'], 150)} | {_cell(ex.get('base_regimen', ''), 150)} | "
                             f"{_cell(x['target_text'], 250)} | {setting_label(x['setting_code'])} | {line_label(x['line_code'])} | {last} |")
        if code in raw_sections:
            L += ["", "---", "", "## 원문 (kordoc 변환)", "", raw_sections[code]]
        a, _, b = code.partition("-")
        p = out_dir / f"{int(a):02d}{('_' + b) if b else ''}_{_slug(name)}.md"
        p.write_text("\n".join(L) + "\n", encoding="utf-8")
        files.append(p)
        index_rows.append((code, name, len(rs), p.name, ", ".join(kcodes[:6])))
    # 허가초과 항암요법은 급여가 아니므로 문서에서 제외(원자료 테이블은 DB 에만 보관)
    L = ["# 항암제 급여기준 (암종별)", "", head_note, "| 번호 | 암종 | 요법 행 수 | KCD | 파일 |", "|---|---|---|---|---|"]
    for code, name, n, fname, kc in index_rows:
        L.append(f"| {code} | {name} | {n} | {kc} | [{fname}]({fname}) |")
    (out_dir / "README.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    conn.close()
    return files


def build_disease_index() -> dict:
    """md/disease/<KCD3>_<질환명>.md + README.md. onco/drug 스테이징 중 있는 것만 읽는다."""
    out_dir = MD_DIR / "disease"
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.md"):
        old.unlink()
    common = sqlite3.connect(staging_path("common"))
    def kname(c):
        r = common.execute("SELECT name_kr FROM kcd_code WHERE code=?", (c,)).fetchone()
        return r[0] if r else ""
    by_root: dict[str, dict] = defaultdict(lambda: {"onco": [], "off": [], "drug": [], "codes": set()})
    if staging_path("onco").exists():
        c = _conn("onco")
        for r in c.execute("SELECT m.kcd_code, m.confidence, m.review_status, g.* FROM criteria_disease_kcd m "
                           "JOIN onco_criteria_regimen g ON g.regimen_key=m.subject_key WHERE m.subject_type='onco_regimen'"):
            d = dict(r)
            by_root[gkey(d["kcd_code"])]["onco"].append(d)
            by_root[gkey(d["kcd_code"])]["codes"].add(d["kcd_code"])
        c.close()
    if staging_path("drug").exists():
        c = _conn("drug")
        try:
            for r in c.execute("SELECT m.kcd_code, m.confidence, m.review_status, m.disease_label, i.* FROM criteria_disease_kcd m "
                               "JOIN drug_criteria_item i ON i.item_key=m.subject_key WHERE m.subject_type='drug_item' "
                               "AND coalesce(m.note,'') != 'rel:mention'"):
                d = dict(r)
                by_root[gkey(d["kcd_code"])]["drug"].append(d)
                by_root[gkey(d["kcd_code"])]["codes"].add(d["kcd_code"])
        except sqlite3.OperationalError:
            pass
        c.close()
    index = []
    for root in sorted(by_root):
        b = by_root[root]
        title = GROUP_NAME.get(root) or kname(root) or kname(sorted(b["codes"])[0])
        L = [f"# {root} {title}", "", f"> {SOURCE_NOTE}", "",
             f"포함 코드: {', '.join(f'`{c}` {kname(c)}' for c in sorted(b['codes'])[:30])}", ""]
        if b["onco"]:
            L += ["## 항암제 공고 급여요법 (치료 순서별)", "", "| 암종 | 투여요법 | 투여단계 | 요법 | 투여대상(요약) | 이전 치료 조건 | 매핑 |",
                  "|---|---|---|---|---|---|---|"]
            seen = set()
            for d in sorted(b["onco"], key=lambda x: (_cancer_sort(x["cancer_code"]), _skey(x["setting_code"]), _lkey(x["line_code"]), x["regimen_key"])):
                if d["regimen_key"] in seen:
                    continue
                seen.add(d["regimen_key"])
                rv = "" if d["review_status"] == "auto" else "검토필요"
                L.append(f"| {d['cancer_name_kr']} | {setting_label(d['setting_code'])} | {line_label(d['line_code'])} | "
                         f"{_cell(d['regimen_text'], 120)} | {_cell(d['target_text'], 160)} | {_cell(d['prior_therapy'], 160)} | {rv} |")
        if b["drug"]:
            L += ["", "## 일반약제 급여기준 (세부사항 고시)", "", "| 분류 | 항목 | 최신 고시 | 시행일 | 질환 표현 | 매핑 |", "|---|---|---|---|---|---|"]
            for d in {x["item_key"]: x for x in b["drug"]}.values():
                rv = "" if d["review_status"] in ("auto", "reviewed") else "검토필요"
                L.append(f"| {d['class_no']} | {_cell(d['title'], 120)} | {d['gosi_no'] or ''} | {d['effective_date'] or ''} | "
                         f"{_cell(d['disease_label'], 80)} | {rv} |")
        p = out_dir / f"{root}_{_slug(title)}.md"
        p.write_text("\n".join(L) + "\n", encoding="utf-8")
        index.append((root, title, len({d['regimen_key'] for d in b['onco']}), 0,
                      len({d['item_key'] for d in b['drug']}), p.name))
    L = ["# 질환(KCD-9)별 급여 약제 색인", "", f"> {SOURCE_NOTE}", "",
         "| KCD | 질환 | 항암 공고요법 | 일반약제 항목 | 파일 |", "|---|---|---|---|---|"]
    for root, title, a, b2, c2, f in index:
        L.append(f"| {root} | {title} | {a} | {c2} | [{f}]({f}) |")
    (out_dir / "README.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    common.close()
    return {"diseases": len(index), "with_onco": sum(1 for x in index if x[2]), "with_drug": sum(1 for x in index if x[4])}
