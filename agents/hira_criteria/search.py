"""급여기준 DB 조회 로직 (대쉬보드 '급여 기준' 화면). 읽기 전용.

1) 약제 검색  : resolve_drug(한글 제품명/성분/INN) → search_drug → 항암 공고요법·일반약제 기준 (허가초과 요법은 급여가 아니므로 제외)
2) 질환 검색  : 질환 '그룹'(항암=공고 암종 단위, 그 외=KCD 3단) → clarify(LLM 이 DB 실재 그룹·값 안에서만 해석)
               → results(필터·약제 포커스·판정)
3) 약제 비교  : compare(최대 3개, 기준 약제 대비) — 칸(암종×투여요법×투여단계) 정렬 + 문단(질환) 단위 diff

원칙
- 결과·판정은 DB 원문에서 결정론적으로 만든다. LLM 은 질환 해석·조건 추출·질문 만들기에만 쓰고, 값은 실재 목록으로 검증.
- 바이오마커는 양성/음성(변이 있음/없음)을 구분한다. 필터는 '환자 상태'다: 반대 조건을 요구하는 요법만 빼고,
  조건이 없는 요법은 남긴다(HER2 음성 환자에게 HER2 조건 없는 세포독성 요법은 해당).
- 항암 질환은 KCD 해부학 부위가 아니라 공고 암종(두경부암·비소세포폐암 …) 단위로 다룬다.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Callable, Optional

from .md_export import LINE_KR, SETTING_KR, line_label, setting_label
from .onco_kcd import CANCER_KCD, ROW_TO_KCD, cancer_of_text

from .drug_sections import GROUP_NAME, gkey, section_text, split_sections
from .drug_tables import normalize_tables

BASE_DIR = Path(__file__).resolve().parents[2]
MAIN_DB = BASE_DIR / "data" / "db" / "drug_prices.db"
AI_MODEL = os.environ.get("HIRA_CRITERIA_AI_MODEL", "gpt-4.1-mini")
EARLY_SETTINGS = {"neoadjuvant", "adjuvant", "perioperative", "ccrt"}
EXCLUDED_CANCERS = ("38", "39", "40")   # 전처치·기타암·기타병용 — 그룹이 아니라 행 단위로 붙는다

# ── 바이오마커 (양성/음성) ───────────────────────────────────────────────────────
# kind: mut=변이 있음/없음, exp=양성/음성, msi=MSI-H·dMMR / MSS·pMMR
MARKERS = [
    ("PD-L1", r"PD-?L1", "exp"), ("EGFR", r"EGFR", "mut"), ("ALK", r"\bALK\b", "mut"), ("ROS1", r"ROS-?1", "mut"),
    ("BRAF", r"BRAF", "mut"), ("KRAS", r"KRAS", "mut"), ("HER2", r"HER2|사람\s*상피세포\s*성장인자\s*수용체\s*2", "exp"),
    ("BRCA", r"BRCA", "mut"), ("HRD", r"상동재조합결핍|HRD", "exp"), ("MSI-H/dMMR", r"MSI-H|dMMR|현미부체\s*불안정|불일치\s*복구\s*결함", "msi"),
    ("RET", r"\bRET\b", "mut"), ("MET", r"\bMET\b", "mut"), ("NTRK", r"NTRK", "mut"), ("IDH1", r"IDH1", "mut"),
    ("IDH2", r"IDH2", "mut"), ("FLT3", r"FLT3", "mut"), ("FGFR", r"FGFR\d?", "mut"),
    ("호르몬수용체", r"호르몬\s*수용체|\bHR\b", "exp"), ("T790M", r"T790M", "mut"), ("CD20", r"CD20", "exp"),
    ("CD30", r"CD30", "exp"), ("BCMA", r"BCMA", "exp"), ("필라델피아 염색체", r"필라델피아|Ph\+|Ph\-", "exp"),
    ("CLDN18.2", r"CLDN18\.?2|Claudin", "exp"), ("PIK3CA", r"PIK3CA", "mut"),
]
_MKIND = {n: k for n, _, k in MARKERS}
NEG_AFTER = re.compile(r"^[^.,;\n※]{0,22}?(음성|없는|없고|없으며|없음|없어야|미확인|negative)")
POS_EXCLUDED = re.compile(r"^[^.,;\n※]{0,14}?양성[^.,;\n]{0,14}?(제외|않)")
INHIBITOR_AFTER = re.compile(r"^\s*(inhibitor|억제제|저해제|항체|치료)", re.I)


def marker_label(name: str, pos: bool) -> str:
    if name == "조혈모세포이식":
        return "이식 가능" if pos else "이식 불가"
    k = _MKIND.get(name, "exp")
    if k == "mut":
        return f"{name} 변이" if pos else f"{name} 변이 없음"
    if k == "msi":
        return "MSI-H/dMMR" if pos else "MSS/pMMR"
    return f"{name} 양성" if pos else f"{name} 음성"


_ANY_MARKER = re.compile("|".join(f"(?:{pat})" for _, pat, _ in MARKERS), re.I)
COND_AFTER = re.compile(r"^[^.;\n]{0,20}?(확인된\s*환자는|인\s*경우에는|있는\s*경우에는|환자는\s*이러한)")
TRANSPLANT = re.compile(r"(?:자가\s*)?조혈모\s*세포\s*이식")


def biomarker_status(text: str) -> list[dict]:
    """투여대상 문장 → [{name, pos, label, value}] (value='HER2|+').
    - 'PD-L1 inhibitor' 같은 약제 언급 제외
    - 판단 창은 다음 바이오마커 이름 앞에서 끊는다('gBRCA 변이 HER2-음성' 의 '음성' 은 HER2 것)
    - '변이가 확인된 환자는 …' 같은 조건부 문장은 요구 조건이 아님 → 제외
    - 조혈모세포이식 가능/불가능도 환자 상태로 다룬다"""
    text = text or ""
    found: dict[str, bool] = {}
    for name, pat, _k in MARKERS:
        for m in re.finditer(pat, text, re.I):
            # 괄호 속 영문 풀이('dMMR(mismatch repair deficient) 또는 MSI-H(microsatellite instability- high)가 없는')는 판단에서 뺀다
            after = re.sub(r"\([^)]*\)", "", text[m.end(): m.end() + 90])[:40]
            nm = _ANY_MARKER.search(after)
            if nm and nm.start() > 0 and not re.fullmatch(r"\s*(또는|및|혹은|이나|와|과|,|/|·)?\s*", after[: nm.start()]):
                after = after[: nm.start()]   # 다음 마커 앞에서 끊기('EGFR 또는 ALK 변이가 없는' 처럼 연결어만 있으면 함께 판단)
            if INHIBITOR_AFTER.match(after) or COND_AFTER.match(text[m.end(): m.end() + 40]):
                continue
            neg = bool(NEG_AFTER.match(after) or POS_EXCLUDED.match(after)) or "Ph-" in m.group(0)
            if re.match(r"^\s*[-–]?\s*양성", after) and not POS_EXCLUDED.match(after):
                neg = False
            found[name] = (found[name] or not neg) if name in found else (not neg)
    for m in TRANSPLANT.finditer(text):
        after = text[m.end(): m.end() + 14]
        if re.match(r"^[^.,;\n]{0,6}?(불가능|비대상|적합하지\s*않|이\s*불가)", after):
            found["조혈모세포이식"] = False if "조혈모세포이식" not in found else found["조혈모세포이식"]
        elif re.match(r"^[^.,;\n]{0,6}?(가능|적합한|대상)", after) or re.match(r"^\s*(종료\s*)?후", after):
            found["조혈모세포이식"] = True   # '이식 후 …' 은 이식을 받은 환자
    return [{"name": n, "pos": p, "label": marker_label(n, p), "value": f"{n}|{'+' if p else '-'}"} for n, p in found.items()]


# ── 공통 ─────────────────────────────────────────────────────────────────────

def connect(db: Path = MAIN_DB) -> sqlite3.Connection:
    c = sqlite3.connect(db, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.create_function("gkey", 1, gkey, deterministic=True)     # 질환 그룹 키(3단 + 합병증·특수형 분리)
    try:
        for kind in ("onco_consolidated", "drug_booklet"):
            r = c.execute("SELECT url FROM hira_criteria_source WHERE kind=? ORDER BY source_id DESC LIMIT 1", (kind,)).fetchone()
            _SRC_URL[kind] = r[0] if r else None
    except sqlite3.Error:
        pass
    _load_cancer_names(c)
    return c


_CANCER_NAMES: dict[str, str] = {}
_SRC_URL: dict[str, Optional[str]] = {}


def _load_cancer_names(conn: sqlite3.Connection) -> None:
    if not _CANCER_NAMES:
        try:
            for r in conn.execute("SELECT DISTINCT cancer_code, cancer_name_kr FROM onco_criteria_regimen"):
                _CANCER_NAMES[r[0]] = r[1]
        except sqlite3.OperationalError:
            pass


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").lower())


FOOT_RE = re.compile(r"(?<=[A-Za-z0-9%)\]])주\s?\d{1,2}(?![\d회기간차일])")


def clean(text: str) -> str:
    """표시용 정리: 각주 기호(주3) 제거, 깨진 글머리표(？) → •, PDF 줄바꿈 이어붙이기."""
    from .onco_parser import unwrap
    t = normalize_tables(text or "") if "|" in (text or "") else (text or "")   # 책자 표 정리(머리행·끊긴 칸)
    t = FOOT_RE.sub("", t)
    t = re.sub(r"(^|\n)\s*？\s*", r"\1• ", t)
    return unwrap(t)


def line_set(code: str) -> Optional[set[int]]:
    """'1L+'→{1..9}, '2L_or_3L'→{2,3}, 'NA'→None(구분 없음)."""
    if not code or code == "NA":
        return None
    out: set[int] = set()
    for alt in re.split(r"\||_or_", code):
        m = re.fullmatch(r"(\d)L(\+?)", alt)
        if m:
            n = int(m.group(1))
            out |= set(range(n, 10)) if m.group(2) else {n}
    return out or None


def line_matches(code: str, patient_line: Optional[int]) -> bool:
    """환자 치료 차수 n 에 쓸 수 있는 기준인가. '1차 이상' 은 2차 환자에도 해당. 구분 없음(NA)은 포함."""
    if not patient_line:
        return True
    s = line_set(code)
    return s is None or patient_line in s


def _cancer_key(code: str) -> list[int]:
    try:
        return [int(p) for p in code.split("-")]
    except ValueError:
        return [999]


_SORDER = list(SETTING_KR)
_LORDER = list(LINE_KR)


def _skey(code: str) -> int:
    c = (code or "NA").split("|")[0]
    return _SORDER.index(c) if c in _SORDER else 99


def _lkey(code: str) -> int:
    c = (code or "NA").split("|")[0]
    return _LORDER.index(c) if c in _LORDER else 99


def _row_cancer_code(code: str, extra: dict) -> str:
    """40번 표 행의 실제 암종 코드(행 '주요 암종' → 없으면 '허가사항' 문구)."""
    if code != "40":
        return code
    rc = re.sub(r"\s+", "", extra.get("row_cancer", ""))
    if rc and not cancer_of_text(rc) and any(re.sub(r"\s+", "", kw) in rc for kw, _, _ in ROW_TO_KCD):
        return "40"                   # 공고 암종 밖 질환(아밀로이드증 등)은 암종 그룹에 넣지 않는다
    return cancer_of_text(extra.get("row_cancer", "")) or cancer_of_text(extra.get("label", "")) or "40"


_CLAUSE_HEAD = re.compile(r"(?m)^\s*(?=[가-하]\.\s)")
_CLAUSE_LINE = re.compile(r"^\s*(?:[가-하]\.\s*)?(\d)\s*차(\s*이상)?")


def clause_lines(target: str) -> list[tuple[str, Optional[str]]]:
    """투여대상을 '가. / 나.' 항목으로 나누고, 항목이 '○차(이상) …' 로 시작하면 그 차수 코드(예: '2L+')를 붙인다.
    (유지요법의 '2차 이상의 백금기반요법에 반응한…' 처럼 항목마다 해당 차수가 다른 경우)"""
    parts = [p for p in _CLAUSE_HEAD.split(target or "") if p.strip()]
    out = []
    for p in parts or [target or ""]:
        m = _CLAUSE_LINE.match(p)
        out.append((p.strip(), f"{m.group(1)}L{'+' if m.group(2) else ''}" if m else None))
    return out


def _onco_row(r: sqlite3.Row) -> dict:
    d = dict(r)
    extra = json.loads(d.get("extra_json") or "{}")
    combo = d["cancer_code"] == "40"
    real = _row_cancer_code(d["cancer_code"], extra)
    cancer = _CANCER_NAMES.get(real, d["cancer_name_kr"]) if combo else d["cancer_name_kr"]
    target = clean(d["target_text"])
    added = clean(extra.get("label", "")) if combo else ""
    setting, line = d["setting_code"], d["line_code"]
    remark = clean(extra.get("remark", "")) if combo else ""
    if combo and line == "NA":
        mr = re.search(r"투여\s*단계\s*[:：]?\s*(\d)\s*차(\s*이상)?", remark)
        if mr:
            line = f"{mr.group(1)}L{'+' if mr.group(2) else ''}"
    if combo and line in ("NA", "1L+"):   # 허가 문구가 'N차 치료로서' 라고 하면 그 차수(1차 치료용 병용이 2차 비교에 끼지 않게)
        ml = re.search(r"(\d)\s*차\s*치료(?:로|제로)?", added)
        if ml and not re.match(r"\s*이상", added[ml.end():ml.end() + 4]):
            line = f"{ml.group(1)}L"
    if combo and added:   # 병용 신약 추가 요법: 단계는 추가 약제 허가 문구가 정한다(수술 전·후 요법이 '1차 고식적'으로 묶이는 것 방지)
        lab = re.sub(r"이전에[^.]*?(경험이\s*있는|받은|투여한)", " ", added)   # '이전에 수술 전 보조요법 … 경험이 있는' 은 과거 치료
        pre = re.search(r"수술\s*전\s*보조|neoadjuvant|선행", lab)
        post = re.search(r"수술\s*후|adjuvant", lab.replace("neoadjuvant", ""))
        if pre or post:
            setting, line = ("perioperative" if pre and post else "neoadjuvant" if pre else "adjuvant"), "NA"
        elif setting == "NA":              # 혈액암 병용: 허가·대상 문구의 '새로 진단 / 재발·불응' 로 단계 구분
            tt = added + " " + target
            if re.search(r"새롭게\s*진단|새로\s*진단|이전에\s*항암요법을\s*받지\s*않", tt):
                setting = "newly_diagnosed"
            elif re.search(r"재발|불응|치료에\s*실패|이전에\s*한\s*가지\s*이상의\s*치료", tt):
                setting = "relapsed_refractory"
    mtxt = extra.get("marker_text") if combo else None      # 한 문장이 두 연번에 걸친 허가 문구는 연번별 부분만
    markers = biomarker_status(((mtxt or added) + "\n" + target) if combo else target)
    return {
        "kind": "onco_regimen", "key": d["regimen_key"], "cancer_code": real, "cancer": cancer,
        "setting_code": setting, "setting": setting_label(setting),
        "line_code": line, "line": line_label(line),
        "regimen_no": d["regimen_no"], "regimen": FOOT_RE.sub("", d["regimen_text"]), "target": target,
        "markers": markers, "biomarkers": [m["label"] for m in markers],
        "self_pay": d["self_pay"], "subgroup": d["subgroup_raw"],
        "notice": d["last_notice_no"], "effective": d["last_effective"],
        "is_combo_table": combo, "added_condition": added,
        "base_regimen": extra.get("base_regimen") if combo else None,
        "footnotes": [{"mark": k, "text": clean(v)} for k, v in (extra.get("footnote_text") or {}).items()],
        "source_url": _SRC_URL.get("onco_consolidated"),
        "remark": remark,
        "source_loc": f"항암 공고 전문 · {cancer} · {'기타 항암제 병용요법 ' if combo else ''}연번 {d['regimen_no']}",
        "drugs": json.loads(d.get("drugs_json") or "[]"),
    }


def _source_meta(conn: sqlite3.Connection) -> dict:
    out = {}
    for kind in ("onco_consolidated", "onco_notice", "kcd_master", "drug_booklet"):
        r = conn.execute("SELECT title, notice_no, effective_date, posted_date, url, fetched_at FROM hira_criteria_source "
                         "WHERE kind=? ORDER BY source_id DESC LIMIT 1", (kind,)).fetchone()
        out[kind] = dict(r) if r else None
    r = conn.execute("SELECT MAX(fetched_at) FROM hira_criteria_source").fetchone()
    out["checked_at"] = (r[0] or "")[:10] if r else None
    r = conn.execute("SELECT MAX(effective_date) FROM drug_criteria_item").fetchone()
    out["drug_latest_effective"] = r[0] if r else None
    return out


# ── 1) 약제 ──────────────────────────────────────────────────────────────────

_DOSE_RE = re.compile(r"^([a-z][a-z0-9 \-\.,'()]+?)\s{2,}|^([a-z][a-z \-]+?)\s+\d")
_SALT = re.compile(r"\b(hydrochloride|hcl|phosphate|sodium|potassium|mesylate|maleate|tartrate|hydrate|monohydrate|"
                   r"sulfate|citrate|besylate|acetate|fumarate|succinate|bromide|calcium|magnesium|dihydrate)\b")


def _ing_name(ing: str) -> tuple[str, bool]:
    """drug_latest.ingredient → (주성분명, 복합제 여부)."""
    ing = (ing or "").strip().lower()
    combo = bool(re.search(r"\+|/|,\s*[a-z]", ing.split("  ")[0]))
    m = _DOSE_RE.match(ing + "  ")
    name = (m.group(1) or m.group(2)).strip() if m else ing.split("  ")[0]
    name = re.sub(r"\(.*$", "", name).strip(" ,")
    return name, combo


GENERIC_WORDS = {"병용", "단독", "비교", "차이", "치료", "요법", "수술", "수술전", "수술후", "보조", "선행", "환자", "급여", "기준", "가능",
                 "여부", "조건", "성인", "소아", "아이", "고령", "재발", "전이", "진행", "유지", "항암", "항암제", "주사", "경구", "알려줘", "되나",
                 "되나요", "뭐가", "달라", "어떻게", "무엇", "적용", "투여", "처방", "보험", "약제", "약물"}


def resolve_drug(conn: sqlite3.Connection, q: str) -> dict:
    """검색어 → 성분(INN, 소문자) 후보. 한글 제품명은 alias·국내 약가(drug_latest)로, 영문은 그대로.
    복합제(가브스메트 등)는 같은 이름의 단일제가 있으면 쓰지 않는다."""
    q = (q or "").strip()
    if not q or q in GENERIC_WORDS:          # '병용'·'단독' 같은 일반어는 약이 아니다
        return {"query": q, "inns": [], "via": []}
    inns: list[str] = []
    via: list[str] = []
    if re.search(r"[a-zA-Z]", q) and not re.search(r"[가-힣]", q) and _known_inn(conn, q.lower()):
        inns.append(q.lower())
        via.append("영문 성분명")
    for r in conn.execute("SELECT inn, brand_aliases_json FROM product_alias_map"):
        aliases = [a.lower() for a in json.loads(r["brand_aliases_json"] or "[]")]
        if q.lower() in aliases or any(a.startswith(q.lower()) and len(q) >= 2 for a in aliases):
            inns.append(r["inn"].lower())
            via.append("제품 별칭")
    if re.search(r"[가-힣]", q):
        rows = conn.execute("SELECT DISTINCT product_name_kr, ingredient FROM drug_latest WHERE product_name_kr LIKE ? LIMIT 60",
                            (q + "%",)).fetchall()
        if not rows and len(q) >= 3:
            rows = conn.execute("SELECT DISTINCT product_name_kr, ingredient FROM drug_latest WHERE product_name_kr LIKE ? LIMIT 60",
                                (f"%({q}%",)).fetchall() or conn.execute(
                "SELECT DISTINCT product_name_kr, ingredient FROM drug_latest WHERE product_name_kr LIKE ? LIMIT 60",
                (f"%{q}%",)).fetchall()
        singles, combos = [], []
        for r in rows:
            name, combo = _ing_name(r["ingredient"])
            # '가브스' 검색에 '가브스메트정'(복합제) 은 제품명이 검색어 바로 뒤에 한글 음절이 더 붙는 경우
            longer_brand = bool(re.match(re.escape(q) + r"[가-힣]", r["product_name_kr"] or "")) and \
                not re.match(re.escape(q) + r"(정|주|캡|시럽|산|액|연질|서방|필름|프리|펜|오토|흡입|크림|겔|패치|점안|정제)", r["product_name_kr"] or "")
            (combos if (combo or longer_brand) else singles).append(name)
        for name in (singles or combos):
            if name:
                inns.append(name)
                via.append("국내 약가 제품명")
        for r in conn.execute("SELECT DISTINCT ingredient FROM drug_criteria_item WHERE title LIKE ? OR brand_text LIKE ? LIMIT 10",
                              (f"%{q}%", f"%{q}%")):
            if r["ingredient"]:
                inns.append(r["ingredient"].lower())
                via.append("급여기준 품명")
    seen, out = set(), []
    for i in inns:
        i = re.sub(r"\s+", " ", i).strip(" ,")
        if i and i not in seen:
            seen.add(i)
            out.append(i)
    return {"query": q, "inns": out[:4], "via": list(dict.fromkeys(via))}


def _known_inn(conn: sqlite3.Connection, term: str) -> bool:
    """영문 검색어가 실제 약 성분인지(바이오마커 'HER2'·'BRCA' 를 약으로 잡지 않게)."""
    if len(term) < 4 or _ANY_MARKER.fullmatch(term.strip()):
        return False
    like = f"%{term}%"
    return bool(conn.execute("SELECT 1 FROM onco_criteria_regimen_drug WHERE ingredient LIKE ? LIMIT 1", (like,)).fetchone()
                or conn.execute("SELECT 1 FROM drug_criteria_item WHERE lower(ingredient) LIKE ? OR lower(title) LIKE ? LIMIT 1", (like, like)).fetchone()
                or conn.execute("SELECT 1 FROM drug_latest WHERE lower(ingredient) LIKE ? LIMIT 1", (term + "%",)).fetchone())


def _inn_terms(inn: str) -> list[str]:
    base = re.sub(r"\s+", " ", _SALT.sub("", inn)).strip()
    return [base] if base else [inn]


def _section_excerpt(conn: sqlite3.Connection, key: str, raw: str, roots: list[str]) -> Optional[str]:
    """문단↔질환 표(criteria_section_kcd)로 이 질환 문단만. 하위 문단이면 상위 제목 한 줄을 붙인다."""
    q = ",".join("?" * len(roots))
    heads = {r[0] for r in conn.execute(f"SELECT heading FROM criteria_section_kcd WHERE subject_key=? AND gkey(kcd_code) IN ({q})",
                                        (key, *roots))}
    if not heads:
        return None
    secs = split_sections(raw)
    picked = [sc for sc in secs if sc.heading in heads]
    # 상위 문단이 뽑혔으면 그 안의 하위 문단은 중복이므로 뺀다
    picked = [sc for sc in picked if not any(p.level < sc.level and p.start < sc.start and p.end >= sc.end for p in picked)]
    if not picked or len(picked) == len(secs):
        return None
    return clean("\n".join(section_text(raw, sc, secs) for sc in picked))


def _drug_item(conn: sqlite3.Connection, r: sqlite3.Row, terms: list[str] | None = None, roots: list[str] | None = None) -> dict:
    d = dict(r)
    kcds = [dict(x) for x in conn.execute(
        "SELECT m.kcd_code, k.name_kr, m.review_status, substr(coalesce(m.note,''),5) AS rel FROM criteria_disease_kcd m "
        "LEFT JOIN kcd_code k ON k.code=m.kcd_code "
        "WHERE m.subject_type='drug_item' AND m.subject_key=? ORDER BY m.kcd_code", (d["item_key"],))]
    text = clean(d["criteria_text"])
    item = {"kind": "drug_item", "key": d["item_key"], "class_no": d["class_no"], "title": d["title"],
            "ingredient": d["ingredient"], "criteria": text, "notice": d["gosi_no"],
            "effective": d["effective_date"], "latest_basis": d["latest_basis"],
            "url": d["latest_url"] or _SRC_URL.get("drug_booklet"), "url_kind": "고시" if d["latest_url"] else "책자", "kcd": kcds,
            "toc": [sc.heading[:120] for sc in split_sections(text) if sc.level <= 2][:30],
            "excerpt": None, "relevance": 0}
    if roots:
        item["excerpt"] = _section_excerpt(conn, d["item_key"], d["criteria_text"] or "", roots)
    if terms and not item["excerpt"]:
        secs = sections(text)
        for t in sorted({x for x in terms if x}, key=len, reverse=True):   # 가장 구체적인 표현부터
            hit = [s for s in secs if t in _norm(s["heading"] + s["text"])]
            if hit:
                if len(hit) < len(secs):
                    head = [s for s in hit if t in _norm(s["heading"])]   # 제목에 질환명이 있는 문단 우선
                    item["excerpt"] = "\n".join((s["heading"] + "\n" + s["text"]).strip() for s in (head or hit))
                break
    if terms:
        nt = _norm(text)
        item["relevance"] = sum(nt.count(t) for t in terms if t) + 5 * sum(1 for t in terms if t and t in _norm(d["title"]))
    return item


def search_drug(conn: sqlite3.Connection, q: str) -> dict:
    res = resolve_drug(conn, q)
    onco, gen = [], []
    seen_o, seen_g = set(), set()
    for inn in res["inns"]:
        for term in _inn_terms(inn):
            like = f"%{term}%"
            for r in conn.execute(
                    "SELECT DISTINCT g.* FROM onco_criteria_regimen g LEFT JOIN onco_criteria_regimen_drug d "
                    "ON d.regimen_key=g.regimen_key WHERE d.ingredient LIKE ? OR lower(g.regimen_text) LIKE ?", (like, like)):
                if r["regimen_key"] not in seen_o:
                    seen_o.add(r["regimen_key"])
                    onco.append(_onco_row(r))
            for r in conn.execute("SELECT * FROM drug_criteria_item WHERE lower(ingredient) LIKE ? OR lower(title) LIKE ?",
                                  (like, like)):
                if r["item_key"] not in seen_g:
                    seen_g.add(r["item_key"])
                    gen.append({**_drug_item(conn, r), "match": "title"})
    for inn in res["inns"]:   # 묶음 기준(당뇨병용제 등) 본문에만 나오는 경우
        for term in _inn_terms(inn):
            if len(term) < 5:
                continue
            for r in conn.execute("SELECT * FROM drug_criteria_item WHERE lower(criteria_text) LIKE ?", (f"%{term}%",)):
                if r["item_key"] not in seen_g:
                    seen_g.add(r["item_key"])
                    it = _drug_item(conn, r)
                    it["match"] = "mentioned"
                    it["excerpt"] = _mention_excerpt(it["criteria"], term)
                    gen.append(it)
    onco.sort(key=lambda x: (x["is_combo_table"], _cancer_key(x["cancer_code"]), _skey(x["setting_code"]),
                             _lkey(x["line_code"])))
    qn = _norm(q)
    branded = [g for g in gen if g.get("match") == "title" and len(qn) >= 2 and qn in _norm(g["title"])]
    if branded:   # 품명으로 찾으면 그 품명 항목만 '이 약의 기준'(같은 성분 다른 품명은 아래로 — 텍피데라 vs 스킬라렌스)
        for g in gen:
            if g.get("match") == "title" and g not in branded:
                g["match"] = "same_ingredient"
    gen.sort(key=lambda g: {"title": 0, "same_ingredient": 1}.get(g.get("match"), 2))
    return {"resolved": res, "onco": onco, "general": gen, "source": _source_meta(conn)}


def _mention_excerpt(text: str, term: str) -> Optional[str]:
    """묶음 기준에서 성분이 나오는 줄과 앞뒤 2줄(그 줄이 속한 문단 제목 포함)."""
    lines = (text or "").split("\n")
    idx = [i for i, ln in enumerate(lines) if term in ln.lower()]
    if not idx:
        return None
    keep: list[int] = []
    for i in idx[:4]:
        head = next((k for k in range(i, -1, -1) if SECTION_HEAD.match(lines[k])), None)
        if head is not None and head not in keep:
            keep.append(head)
        keep += [k for k in range(max(0, i - 2), min(len(lines), i + 3)) if k not in keep]
    keep.sort()
    out, prev = [], None
    for k in keep:
        if prev is not None and k > prev + 1:
            out.append("…")
        out.append(lines[k])
        prev = k
    return "\n".join(out)


# ── 2) 질환 그룹 ─────────────────────────────────────────────────────────────

def _fns(conn: sqlite3.Connection) -> None:
    conn.create_function("gkey", 1, gkey, deterministic=True)


def disease_groups(conn: sqlite3.Connection) -> list[dict]:
    """항암 = 공고 암종 단위(id 'onco:2'), 그 밖 = KCD 3단(id 'L20'). 급여기준이 연결된 것만."""
    _fns(conn)
    groups: list[dict] = []
    covered: set[str] = set()
    for r in conn.execute("SELECT cancer_code, MIN(cancer_name_kr) n, MIN(cancer_name_en) e, COUNT(*) c FROM onco_criteria_regimen "
                          "WHERE cancer_code NOT IN ('38','39','40') GROUP BY cancer_code"):
        codes = CANCER_KCD.get(r["cancer_code"], ([], 0, ""))[0]
        roots = sorted({c[:3] for c in codes})
        covered |= set(roots)
        groups.append({"id": f"onco:{r['cancer_code']}", "kind": "onco", "cancer_code": r["cancer_code"], "name": r["n"],
                       "name_en": r["e"], "roots": roots, "onco": r["c"]})
    counts = defaultdict(lambda: {"drug": 0, "onco": 0})
    for r in conn.execute("SELECT gkey(kcd_code) root, subject_type, COUNT(DISTINCT subject_key) n FROM criteria_disease_kcd "
                          "WHERE coalesce(note,'') != 'rel:mention' GROUP BY 1,2"):
        if r["subject_type"] in ("drug_item", "onco_regimen"):          # 허가초과(onco_offlabel)는 급여가 아니므로 세지 않는다
            counts[r["root"]]["drug" if r["subject_type"] == "drug_item" else "onco"] = r["n"]
    for g in groups:
        g["drug"] = sum(counts[x]["drug"] for x in g["roots"])
    for root, c in counts.items():
        if root in covered:
            continue
        k = conn.execute("SELECT name_kr FROM kcd_code WHERE code=?", (root,)).fetchone()
        groups.append({"id": root, "kind": "kcd", "name": GROUP_NAME.get(root) or (k[0] if k else root), "roots": [root],
                       "onco": c["onco"], "drug": c["drug"]})
    groups.sort(key=lambda g: (g["kind"] != "onco", _cancer_key(g.get("cancer_code", "999")) if g["kind"] == "onco" else [0], g["id"]))
    return groups


STOPWORDS = {"치료", "환자", "변이", "양성", "음성", "이상", "수술", "수술전", "수술후", "1차", "2차", "3차", "급여",
             "기준", "약", "약제", "진행성", "전이성", "재발", "재발성", "초기", "말기", "보조", "요법", "투여", "가능", "불가",
             "아이", "소아", "성인", "가능?", "되나", "되나요"}


def disease_candidates(conn: sqlite3.Connection, q: str, limit: int = 8) -> list[dict]:
    """이름 매칭 → 질환 그룹 후보(LLM 힌트용)."""
    q = (q or "").strip()
    if not q:
        return []
    groups = disease_groups(conn)
    by_root: dict[str, list[dict]] = defaultdict(list)
    for g in groups:
        for r in g["roots"]:
            by_root[r].append(g)
    score: dict[str, float] = defaultdict(float)
    terms = [t for t in re.split(r"[\s,/?]+", q) if len(t) >= 2 and t not in STOPWORDS
             and not re.fullmatch(r"\d+차(이상)?|\d+(L|차)\+?|만?\d+세", t)] or [q]
    for t in terms:
        nt = _norm(t)
        ts: dict[str, float] = defaultdict(float)
        for g in groups:
            if g["kind"] == "onco" and (nt in _norm(g["name"]) or _norm(g["name"]).replace("암", "") in nt):
                ts[g["id"]] = max(ts[g["id"]], 4.0)
        for r in conn.execute(
                "SELECT gkey(code) AS root, MAX(CASE WHEN replace(name_kr,' ','')=? THEN 3 "
                "WHEN replace(name_kr,' ','') LIKE ? THEN 2.5 WHEN replace(name_kr,' ','') LIKE ? THEN 2 ELSE 1 END) AS v "
                "FROM kcd_alias WHERE replace(name_kr,' ','') LIKE ? OR lower(name_en) LIKE ? GROUP BY root",
                (nt, "%" + nt, nt + "%", f"%{nt}%", f"%{t.lower()}%")):
            for g in by_root.get(r["root"], []):
                ts[g["id"]] = max(ts[g["id"]], float(r["v"]) * (0.8 if g["kind"] == "onco" else 1.0))
        for gid, v in ts.items():
            score[gid] += v
    top = max(score.values(), default=0)
    gmap = {g["id"]: g for g in groups}
    ranked = [g for g in sorted(score, key=lambda x: (-score[x], x)) if score[g] >= max(1.0, top * 0.5)][:limit]
    return [{**gmap[g], "score": round(score[g], 1)} for g in ranked]


def _group_onco_rows(conn: sqlite3.Connection, g: dict) -> list[sqlite3.Row]:
    if g["kind"] == "onco":
        rows = conn.execute("SELECT * FROM onco_criteria_regimen WHERE cancer_code=?", (g["cancer_code"],)).fetchall()
        for r in conn.execute("SELECT * FROM onco_criteria_regimen WHERE cancer_code='40'"):
            if _row_cancer_code("40", json.loads(r["extra_json"] or "{}")) == g["cancer_code"]:
                rows.append(r)
        if g["roots"]:
            q = ",".join("?" * len(g["roots"]))
            rows += conn.execute(f"SELECT DISTINCT o.* FROM criteria_disease_kcd m JOIN onco_criteria_regimen o ON o.regimen_key=m.subject_key "
                                 f"WHERE m.subject_type='onco_regimen' AND o.cancer_code='39' AND gkey(m.kcd_code) IN ({q})",
                                 g["roots"]).fetchall()
        return rows
    q = ",".join("?" * len(g["roots"]))
    return conn.execute(f"SELECT DISTINCT o.* FROM criteria_disease_kcd m JOIN onco_criteria_regimen o ON o.regimen_key=m.subject_key "
                        f"WHERE m.subject_type='onco_regimen' AND gkey(m.kcd_code) IN ({q})", g["roots"]).fetchall()


def group_facets(conn: sqlite3.Connection, gids: list[str]) -> dict:
    gmap = {g["id"]: g for g in disease_groups(conn)}
    rows = [_onco_row(r) for gid in gids if gid in gmap for r in _group_onco_rows(conn, gmap[gid])]
    st, ln, mk = defaultdict(int), defaultdict(int), defaultdict(int)
    for r in rows:
        for s in r["setting_code"].split("|"):
            st[s] += 1
        ln[r["line_code"]] += 1
        for m in r["markers"]:
            mk[m["value"]] += 1
    lines_avail = sorted({n for c in ln for n in (line_set(c) or set()) if n <= 4})
    mopts = []
    for v, n in sorted(mk.items(), key=lambda x: -x[1]):
        name, pol = v.split("|")
        mopts.append({"value": v, "label": marker_label(name, pol == "+"), "count": n})
    return {"settings": [{"code": s, "label": SETTING_KR.get(s, s), "count": n} for s, n in sorted(st.items(), key=lambda x: _skey(x[0])) if s != "NA"],
            "lines": [{"value": n, "label": f"{n}차" + (" 이상" if n == 4 else "")} for n in lines_avail],
            "markers": mopts[:16], "onco_rows": len(rows)}


def _disease_terms(names: list[str], extra: list[str]) -> list[str]:
    out = []
    for n in names:   # 전체 핵심 이름(공백 제거)을 먼저 — '아토피성피부염', '아토피피부염'
        core = _norm(re.sub(r"\[.*?\]|\(.*?\)|의 악성 신생물|악성 신생물|상세불명의?|기타|혈청검사양성", " ", n or ""))
        if len(core) >= 3:
            out += [core, core.replace("성피부염", "피부염")]
    for n in names + extra:
        n = re.sub(r"\[.*?\]|\(.*?\)|의 악성 신생물|악성 신생물|상세불명의?|기타|달리 분류된", " ", n or "")
        for t in re.split(r"[\s,·/]+", n):
            t = _norm(t)
            if len(t) >= 2 and t not in {_norm(x) for x in STOPWORDS}:
                out.append(t)
    return list(dict.fromkeys(out))


def results(conn: sqlite3.Connection, gids: list[str], *, settings: list[str] | None = None, line: Optional[int] = None,
            markers: list[str] | None = None, drugs: list[str] | None = None, keywords: list[str] | None = None,
            age: Optional[dict] = None) -> dict:
    """질환 그룹 + 필터 → 항암 공고요법(치료 순서) · 병용 신약 추가 요법 · 일반약제 + 판정(약제 포커스). 허가초과 요법은 급여가 아니므로 제외."""
    gmap = {g["id"]: g for g in disease_groups(conn)}     # gkey 함수 등록 포함
    gs = [gmap[g] for g in gids if g in gmap]
    if not gs:
        return {"targets": [], "onco": [], "combo": [], "general": [], "answers": []}
    want = {}
    for v in markers or []:
        if "|" in v:
            n, p = v.split("|", 1)
            want[n] = p == "+"
    inn_terms = [t for d in (drugs or []) for t in _inn_terms(d.lower())]
    kws = [_norm(k) for k in (keywords or []) if len(_norm(k)) >= 2][:6]
    onco, seen, all_rows = [], set(), []
    for g in gs:
        for r in _group_onco_rows(conn, g):
            if r["regimen_key"] in seen:
                continue
            seen.add(r["regimen_key"])
            row = _onco_row(r)
            all_rows.append(row)
            if settings and row["setting_code"] != "NA" and not (set(row["setting_code"].split("|")) & set(settings)):
                continue
            if not line_matches(row["line_code"], line):
                continue
            if line and row["line_code"] == "NA" and set(row["setting_code"].split("|")) <= EARLY_SETTINGS:
                continue
            row["line_clause"] = None
            if line and row["line_code"] == "NA":      # 항목마다 차수가 다른 투여대상: 환자 차수에 맞는 항목만
                cl = clause_lines(row["target"])
                coded = [(t, c) for t, c in cl if c]
                if coded:
                    ok = [t for t, c in coded if line_matches(c, line)]
                    if not ok and all(c for _, c in cl):
                        continue
                    if ok and len(cl) > 1:
                        row["line_clause"] = "\n".join(ok)
            req = {m["name"]: m["pos"] for m in row["markers"]}
            if any(n in req and req[n] != p for n, p in want.items()):
                continue                       # 환자 상태와 반대 조건을 요구하는 요법만 제외
            row["marker_match"] = [marker_label(n, p) for n, p in want.items() if req.get(n) == p]
            row["focus"] = bool(inn_terms) and any(t in row["regimen"].lower() for t in inn_terms)
            nt = _norm(row["target"] + row["added_condition"])
            row["keyword_hits"] = [k for k in kws if k in nt]
            onco.append(row)
    onco.sort(key=lambda x: (_cancer_key(x["cancer_code"]), _skey(x["setting_code"]), _lkey(x["line_code"]),
                             int(re.sub(r"\D.*", "", x["regimen_no"]) or 0)))
    main = [o for o in onco if not o["is_combo_table"]]
    main_regs = {(o["cancer_code"], _norm(re.sub(r"\(.*?\)", "", o["regimen"]))) for o in main}
    combo = [o for o in onco if o["is_combo_table"]
             and (o["cancer_code"], _norm(re.sub(r"\(.*?\)", "", o["regimen"]))) not in main_regs]   # 본표와 같은 요법은 본표만
    # 일반약제
    roots = sorted({r for g in gs for r in g["roots"]})
    terms = _disease_terms([g["name"] for g in gs], keywords or [])
    gen, mentioned = [], []
    if roots:
        q = ",".join("?" * len(roots))
        for r in conn.execute(f"SELECT i.*, MIN(CASE WHEN coalesce(m.note,'')='rel:mention' THEN 1 ELSE 0 END) AS mention_only "
                              f"FROM criteria_disease_kcd m JOIN drug_criteria_item i ON i.item_key=m.subject_key "
                              f"WHERE m.subject_type='drug_item' AND gkey(m.kcd_code) IN ({q}) GROUP BY i.item_key", roots):
            if r["mention_only"]:          # 위험요인·부작용 등으로 언급만 된 기준 — 접어서 따로
                mentioned.append({"key": r["item_key"], "title": r["title"], "ingredient": r["ingredient"]})
                continue
            it = _drug_item(conn, r, terms, roots)
            it["focus"] = bool(inn_terms) and any(t in (it["ingredient"] or "").lower() or t in it["title"].lower() for t in inn_terms)
            gen.append(it)
    if age:                                  # 환자 나이 → 해당 연령 조항 강조(빼지는 않는다: 원문 해석 오류로 잘못 빠지는 것 방지)
        for g in gen:
            g["age"] = age_clause(g["excerpt"] or g["criteria"], age) or age_clause(g["criteria"], age)
        gen.sort(key=lambda x: (not x["focus"], (x.get("age") or {}).get("status") == "outside", -x["relevance"], x["title"]))
    if want.get("조혈모세포이식") is False:   # 이식 불가 환자: 조혈모세포 채집·동원 약제는 해당 없음
        gen = [g for g in gen if not re.search(r"조혈모\s*세포\s*(채집|동원)|mobiliz", g["title"] + g["criteria"][:400], re.I)]
    gen.sort(key=lambda x: (not x["focus"], -x["relevance"], x["title"]))
    return {"targets": [{"id": g["id"], "name": g["name"], "kind": g["kind"]} for g in gs],
            "onco": main, "combo": combo, "general": gen, "mentioned": mentioned,
            "answers": _answer(drugs or [], main, combo, gen, age, all_rows) if drugs else [], "age": age,
            "matches": [] if drugs else [{"key": r["key"], "regimen": r["regimen"], "cancer": r["cancer"], "setting_code": r["setting_code"],
                                          "line_code": r["line_code"], "notice": r["notice"], "effective": r["effective"],
                                          "matched": r["marker_match"], "combo": bool(r["is_combo_table"]),
                                          "condition": _short((r["added_condition"] + " / " if r["is_combo_table"] else "") + (r.get("line_clause") or r["target"]))}
                                         for r in main + combo if r["marker_match"]][:8],
            "source": _source_meta(conn)}


# ── 나이 ──────────────────────────────────────────────────────────────────────
_AGE_Q = re.compile(r"(?:만\s*)?(\d{1,2})\s*(세|살|개월)")
_AGE_WORDS = [("신생아", (0, 0)), ("영아", (0, 11)), ("유아", (12, 71)), ("아이", (0, 143)), ("어린이", (0, 143)),
              ("소아", (0, 215)), ("청소년", (144, 215)), ("성인", (216, 1500)), ("노인", (780, 1500))]


def parse_age(query: str) -> Optional[dict]:
    """'만 8세' → {months: 96, label: '만 8세'} · '아이' → {lo, hi, label}. 나이가 없으면 None."""
    q = query or ""
    m = _AGE_Q.search(q)
    if m and not re.search(r"\d+\s*(?:세|살|개월)\s*(?:이상|이하|미만)", q[m.start():m.end() + 4]):
        n = int(m.group(1))
        months = n if m.group(2) == "개월" else n * 12
        return {"months": months, "lo": months, "hi": months, "label": f"만 {n}{'개월' if m.group(2) == '개월' else '세'}"}
    for w, (lo, hi) in _AGE_WORDS:
        if w in q:
            return {"months": None, "lo": lo, "hi": hi, "label": w}
    return None


_RANGE_PATS = [
    (re.compile(r"만\s*(\d+)\s*(세|개월)\s*(?:이상)?\s*(?:-|~|∼|–|부터)\s*만?\s*(\d+)\s*(세|개월)\s*(미만|이하)?"), "range"),
    (re.compile(r"만\s*(\d+)\s*(세|개월)\s*이상\s*,?\s*만\s*(\d+)\s*(세|개월)\s*(미만|이하)"), "range"),
    (re.compile(r"만\s*(\d+)\s*(세|개월)\s*(이상|초과)"), "ge"),
    (re.compile(r"만\s*(\d+)\s*(세|개월)\s*(미만|이하)"), "lt"),
]


def _mo(n: str, unit: str) -> int:
    return int(n) * (1 if unit == "개월" else 12)


def age_ranges(text: str) -> list[dict]:
    """기준 문구 속 연령 구간 → [{lo, hi, line, label}] (개월 단위, hi 포함)."""
    out, seen = [], set()
    for li, ln in enumerate((text or "").split("\n")):
        taken: list[tuple[int, int]] = []
        for pat, kind in _RANGE_PATS:
            for m in pat.finditer(ln):
                if any(a <= m.start() < b for a, b in taken):
                    continue
                taken.append((m.start(), m.end()))
                g = m.groups()
                if kind == "range":
                    lo = _mo(g[0], g[1])
                    hi = _mo(g[2], g[3]) - 1 if g[4] == "미만" else _mo(g[2], g[3]) + (11 if g[3] == "세" else 0)
                elif kind == "ge":
                    lo, hi = _mo(g[0], g[1]) + (1 if g[2] == "초과" else 0), 1500
                else:
                    lo, hi = 0, _mo(g[0], g[1]) - (1 if g[2] == "미만" else 0)
                key = (lo, hi, li)
                if key not in seen:
                    seen.add(key)
                    out.append({"lo": lo, "hi": hi, "line": li, "label": re.sub(r"\s+", " ", m.group(0)).strip()})
    return out


_SUB_MARK = re.compile(r"^\s*(?:\d{1,2}\)|[가-하]\)|\(\d{1,2}\)|\([가-하]\)|[가-하]\.|\d{1,2}\.)")


def age_clause(text: str, age: dict) -> Optional[dict]:
    """환자 나이에 맞는 조항(그 연령 구간이 나온 하위 항목 전체). 구간이 있는데 맞는 게 없으면 status='outside'."""
    rs = age_ranges(text)
    if not rs or not age:
        return None
    lines = (text or "").split("\n")
    hit = [r for r in rs if r["lo"] <= age["hi"] and age["lo"] <= r["hi"]]
    if not hit:
        lo = min(r["lo"] for r in rs)
        return {"status": "outside", "ranges": list(dict.fromkeys(r["label"] for r in rs))[:4],
                "message": f"{age['label']} 환자는 이 기준의 연령 구간 밖입니다(기준: {', '.join(list(dict.fromkeys(r['label'] for r in rs))[:3])})"
                if lo > age["hi"] or age["lo"] > max(r["hi"] for r in rs) else f"{age['label']}에 해당하는 연령 조항이 없습니다"}
    if age.get("months") is None and len(hit) > 1:      # '아이' 처럼 범위로 물으면 해당 구간 조항 모두
        parts = [age_clause(text, {"lo": r["lo"], "hi": r["lo"], "months": r["lo"], "label": r["label"]}) for r in
                 sorted(hit, key=lambda r: r["line"])]            # 원문 순서(3)이 2)를 가리키는 경우가 있어)
        parts = [p for p in parts if p and p["status"] == "match"]
        texts = list(dict.fromkeys(p["text"] for p in parts))
        return {"status": "match", "label": ", ".join(dict.fromkeys(p["label"] for p in parts)), "text": "\n".join(texts)}
    # 가장 좁은 구간 우선
    best = min(hit, key=lambda r: r["hi"] - r["lo"])
    i = best["line"]
    start = i
    while start > 0 and not _SUB_MARK.match(lines[start]) and i - start < 3:
        start -= 1
    mk = _SUB_MARK.match(lines[start])
    kd = lambda x: re.sub(r"\d+", "0", re.sub(r"[가-하]", "가", x.strip()))  # noqa: E731  '2)'≠'가)'
    kind = kd(mk.group(0)) if mk else None
    end = i + 1
    while end < len(lines):
        m2 = _SUB_MARK.match(lines[end])
        if m2 and (kind is None or kd(m2.group(0)) == kind or (kd(m2.group(0)) in ("0.", "가.") and kind not in ("0.", "가."))):
            break
        end += 1
    return {"status": "match", "label": best["label"], "text": "\n".join(lines[start:min(end, start + 30)]).strip()}


def key_condition(text: str, n: int = 260) -> str:
    """판정 카드용 요약: 서두 상투문·제목 줄을 건너뛰고 '투여대상' 부분부터."""
    t = text or ""
    m = re.search(r"투여\s*대상", t)
    if m:
        t = t[m.end():]
    lines = [ln for ln in t.split("\n") if ln.strip() and not re.search(r"허가사항\s*범위\s*내에서|약값\s*전액|^\s*-\s*(?:다\s*음|아\s*래)\s*-\s*$", ln)]
    return _short(" ".join(lines), n)


def _short(text: str, n: int = 220) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    return t if len(t) <= n else t[:n].rsplit(" ", 1)[0] + " …"


def _answer(drug_inns: list[str], main: list, combo: list, gen: list, age: Optional[dict] = None, all_rows: list | None = None) -> list[dict]:
    """약제별 판정(결정론). status: covered | combo_only | age_outside | none. 조건은 원문 앞부분 요약."""
    out = []
    for inn in drug_inns:
        terms = _inn_terms(inn.lower())
        hit = lambda txt: any(t in (txt or "").lower() for t in terms)  # noqa: E731
        m = [r for r in main if hit(r["regimen"])]
        c = [r for r in combo if hit(r["regimen"])]
        g = [x for x in gen if hit(x["ingredient"]) or hit(x["title"])]
        if g and not m and age and all((x.get("age") or {}).get("status") == "outside" for x in g):
            status, msg = "age_outside", f"급여 기준은 있으나 {age['label']} 환자는 연령 구간 밖"
        elif m or g:
            status, msg = "covered", "급여 기준 있음 — 아래 조건 충족 시"
        elif c:
            status, msg = "combo_only", "이 약 자체는 급여 아님 — 함께 쓰는 기존 항암요법만 급여"
        else:
            status, msg = "none", "선택한 조건에 해당하는 급여 기준 없음"
        elsewhere = []
        if status == "none" and all_rows:     # 조건(단계·차수·바이오마커)을 풀면 있는지 — 다른 조건에서 급여되는 단계를 알려준다
            for r in all_rows:
                if hit(r["regimen"]):
                    lab = (plain_step(r["setting_code"], r["line_code"]) or "단계 구분 없음") + (" (병용 신약 추가 요법)" if r["is_combo_table"] else "")
                    if lab not in elsewhere:
                        elsewhere.append(lab)
        rows = (m or c)[:6]
        out.append({"drug": inn, "status": status, "message": msg, "elsewhere": elsewhere[:6],
                    "rows": [{"key": r["key"], "regimen": r["regimen"], "cancer": r["cancer"], "setting_code": r["setting_code"],
                              "line_code": r["line_code"], "notice": r["notice"], "effective": r["effective"],
                              "condition": _short((r["added_condition"] + " / " if r["is_combo_table"] else "") + (r.get("line_clause") or r["target"]))}
                             for r in rows],
                    "items": [{"key": x["key"], "title": x["title"], "notice": x["notice"], "effective": x["effective"],
                               "condition": key_condition((x.get("age") or {}).get("text") or x["excerpt"] or x["criteria"], 320),
                               "age": x.get("age")} for x in g[:3]]})
    return out


# ── LLM 해석 ──────────────────────────────────────────────────────────────────

LLMFn = Callable[[str, str, int], tuple[dict, dict]]

SYS_PICK = """너는 한국 건강보험 급여기준 검색 도우미다. 사용자 질의의 질환을 아래 '질환 목록'(이 DB 에 급여기준이 있는 것만) 중에서 고른다.
- 항암은 'onco:번호' 암종 단위로 고른다(두경부암이면 onco:16 하나. 해부학 세부 부위는 묻지 않는다).
- 목록 밖 id 는 절대 만들지 마라.
JSON: {"interpretation": "질의를 한 문장으로 다시 쓴 것(한국어)", "targets": ["onco:2", ...] (가능성 높은 순, 최대 8),
"ambiguous": true/false, "question": "targets 가 여러 개라 사용자가 하나를 골라야 할 때만 한 문장(아니면 빈 문자열)",
"drugs": ["질의에 나온 약 이름(제품명·성분명 그대로)"]}
- 폐암처럼 목록에 소세포/비소세포가 따로 있고 질의로 구분되지 않으면 ambiguous=true.
- '혈액암'처럼 넓은 표현은 해당 암종을 모두 targets 에 넣고 ambiguous=true.
- '류마티스관절염'처럼 같은 질환의 세부 코드가 여럿이면 모두 넣고 ambiguous=false(함께 보여 준다).
- 일반 질환은 사람들이 흔히 말하는 대표 질환 코드만 고른다(예: 당뇨 → 2형·1형 당뇨병). 신생아·임신·합병증 코드는 질의에 명시될 때만.
- 질의에 없는 관련 질환·합병증은 넣지 마라(예: '건선' → 건선만, 건선성 관절병증은 질의에 '관절'이 있을 때만).
- HER2·BRCA·EGFR·PD-L1 같은 바이오마커와 질환명은 drugs 에 넣지 마라(약 이름만)."""

SYS_REFINE = """너는 한국 건강보험 급여기준 검색 도우미다. 질의에서 환자 조건을 뽑아 주어진 '선택지 값'으로만 필터를 채운다.
JSON: {"filters": {"settings": [...], "line": 정수|null, "markers": ["HER2|-", ...]},
"keywords": ["급여기준 문장에 실제로 나올 법한 핵심 표현(예: 조혈모세포이식이 불가능, 백금민감성, 만 6세)", ...] (최대 5),
"questions": [{"field": "settings|line|markers", "text": "질문", "options": [{"label": "보기", "value": 값}]}],
"summary": "적용한 조건을 한 문장으로"}
- 질의에 드러난 조건은 묻지 말고 filters 에 넣는다. '음성/없음'은 '|-', '양성/변이'는 '|+'.
- 질의에 차수(1차·2차…)가 있으면 line 에 넣고 settings 는 비운다. '수술 전'=neoadjuvant, '수술 후'=adjuvant.
- '이식 불가'·'이식 불가능'은 markers 의 '조혈모세포이식|-', '이식 가능'은 '조혈모세포이식|+'.
- '삼중음성'은 호르몬수용체|- 와 HER2|- 둘 다.
- 결과를 크게 좁히는 데 꼭 필요한 질문만(최대 2개). 이미 answers 에 있는 필드는 묻지 마라. 각 질문에 '상관없음'(value null) 보기를 넣는다.
- 반드시 주어진 값만 쓴다."""


def _as_list(v) -> list:
    """LLM 출력 정규화: None→[], 단일값→[값]."""
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _as_int(v) -> Optional[int]:
    """LLM 출력 정규화: 2 / '2' / '2차' / [2] → 2. 그 밖 → None."""
    if isinstance(v, list):
        v = v[0] if v else None
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    m = re.search(r"\d+", str(v))
    return int(m.group(0)) if m else None


def _default_llm() -> Optional[LLMFn]:
    try:
        from agents.analog.ai_analyze import _api_key, call_llm
    except Exception:  # noqa: BLE001
        return None
    if not _api_key():
        return None
    return lambda s, u, mt: call_llm(s, u, mt, model=AI_MODEL)


SETTING_KW = {
    "neoadjuvant": ["수술 전", "수술전", "선행", "neoadjuvant"], "adjuvant": ["수술 후", "수술후", "보조", "adjuvant"],
    "perioperative": ["수술 전후", "수술전후", "periop"], "maintenance": ["유지"], "ccrt": ["방사선", "ccrt", "동시"],
    "palliative": ["진행", "전이", "고식", "말기", "palliative"], "newly_diagnosed": ["새로 진단", "새롭게 진단", "신규", "초치료", "처음"],
    "relapsed_refractory": ["재발", "불응", "relapse"], "induction": ["관해유도", "유도"], "consolidation": ["공고"],
    "primary": ["일차", "primary"], "salvage": ["구제", "salvage"], "intravesical": ["방광 내", "방광내"], "conditioning": ["전처치"],
}
# 차수 표현 + 차수를 함의하는 표현('백금 치료 후 재발한' → 2차 이상): 이런 말이 질문에 있을 때만 LLM 이 정한 차수를 인정
_LINE_KW = re.compile(r"\d\s*차|\b[1-9]\s*L\b|첫|처음|두\s*번째|세\s*번째|1선|2선|3선|재발|불응|실패|내성|이전\s*치료|치료\s*후")


def _guard_filters(f: dict, query: str) -> None:
    """LLM 이 질의에 없는 조건을 마음대로 붙이지 못하게 — 질문 속 표현이 실제로 있는 단계·차수·바이오마커만 남긴다.
    ('자궁내막암' 만 입력했는데 수술 후 보조요법이 자동 적용되던 문제)"""
    q = (query or "").lower()
    f["settings"] = [c for c in f.get("settings", []) if any(k.lower() in q for k in SETTING_KW.get(c, [c]))]
    if f.get("line") and not _LINE_KW.search(query or ""):
        f["line"] = None
    keep = []
    for v in f.get("markers", []):
        name = v.split("|")[0]
        if name == "조혈모세포이식":
            ok = bool(TRANSPLANT.search(query or "") or "이식" in (query or ""))
        else:
            pat = next((p for n, p, _k in MARKERS if n == name), re.escape(name))
            ok = bool(re.search(pat, query or "", re.I)) or (name in ("호르몬수용체", "HER2") and "삼중음성" in (query or "")) \
                or (name == "MSI-H/dMMR" and re.search(r"MSI|MMR|현미부체", query or "", re.I) is not None)
        if ok:
            keep.append(v)
    f["markers"] = keep


def _scan_drugs(conn: sqlite3.Connection, query: str, drug_names: list[str]) -> list[dict]:
    """LLM 이 준 약 이름 + 질의 단어에서 결정론으로 찾은 약 → 질문에 나온 순서(첫 약 = 비교 기준), 같은 성분 중복 제거, 최대 3개.
    LLM 이 한 약만 돌려주거나 빠뜨려도 '키트루다랑 옵디보' 에서 둘 다 유지한다."""
    drug_names = list(drug_names)
    _PART = re.compile(r"(이랑|랑|하고|와|과|은|는|이|가|을|를|의|도|만|에서|에|으로|로|비교|차이)$")
    for t in re.split(r"[\s,/·]+", query):
        for cand in dict.fromkeys([t, _PART.sub("", t)]):
            if len(cand) >= 2 and cand not in drug_names and not _ANY_MARKER.fullmatch(cand) \
                    and resolve_drug(conn, cand)["inns"] and not disease_candidates(conn, cand):
                drug_names.append(cand)
                break
    resolved = _resolve_names(conn, drug_names)
    seen_inn, uniq = set(), []
    for d in sorted(resolved, key=lambda d: (query.find(d["name"]) if d["name"] in query else 999)):
        key = tuple(d["inns"][:1])
        if key in seen_inn:
            continue
        seen_inn.add(key)
        uniq.append(d)
    return uniq[:3]


def clarify(conn: sqlite3.Connection, query: str, answers: dict | None = None, targets: list[str] | None = None,
            llm: Optional[LLMFn] = None) -> dict:
    """질의 → 질환 그룹 해석 → 필터 자동 적용 + (필요할 때만) 확인 질문. 모든 값은 DB 실재 값으로 검증."""
    query = (query or "").strip()[:200]
    answers = answers or {}
    llm = llm if llm is not None else _default_llm()
    groups = disease_groups(conn)
    gmap = {g["id"]: g for g in groups}
    cands = disease_candidates(conn, query)
    out: dict = {"query": query, "ai": bool(llm), "interpretation": "", "targets": [], "alternatives": [],
                 "filters": {"settings": [], "line": None, "markers": []}, "drugs": [], "keywords": [],
                 "questions": [], "summary": "", "age": parse_age(query)}
    drug_names: list[str] = []
    if targets:
        picked = [t for t in targets if t in gmap]
    elif llm:
        cat = "\n".join(f"{g['id']} | {g['name']}" + (f" ({g['name_en']})" if g.get("name_en") else "") for g in groups)
        hint = ", ".join(f"{c['id']} {c['name']}" for c in cands)
        try:
            j, _ = llm(SYS_PICK, f"질의: {query}\n이름 매칭 힌트: {hint or '(없음)'}\n\n질환 목록:\n{cat}", 600)
        except Exception as e:  # noqa: BLE001
            j, out["ai_error"] = {}, str(e)[:200]
        picked = [t for t in _as_list(j.get("targets")) if isinstance(t, str) and t in gmap][:8]
        kcd_picked = [t for t in picked if gmap[t]["kind"] == "kcd"]
        if len(kcd_picked) > 3:   # 일반 질환은 대표 코드 3개까지
            picked = [t for t in picked if gmap[t]["kind"] == "onco"] + kcd_picked[:3]
        out["interpretation"] = j.get("interpretation", "")
        drug_names = [str(d)[:40] for d in _as_list(j.get("drugs")) if str(d).strip()][:3]
        if j.get("ambiguous") and len(picked) > 1:
            out["targets"] = []
            out["alternatives"] = [{"id": t, "name": gmap[t]["name"]} for t in picked]
            out["questions"] = [{"field": "targets", "text": j.get("question") or "어떤 질환인가요?",
                                 "options": [{"label": gmap[t]["name"], "value": t} for t in picked]
                                 + [{"label": "모두 보기", "value": "__all__:" + ",".join(picked)}]}]
            out["drugs"] = _scan_drugs(conn, query, drug_names)
            return out
    else:
        picked = [cands[0]["id"]] if cands else []
        out["interpretation"] = "AI 미사용 — 이름이 가장 잘 맞는 질환"
    if not picked and cands:
        picked = [cands[0]["id"]]
    if not picked:
        out["drugs"] = _scan_drugs(conn, query, drug_names)       # 질환 없이 약만 입력('휴미라 레미케이드 차이') → 비교로 안내
        out["questions"] = [{"field": "none", "text": ("질환 없이 약만 입력하셨네요. 약끼리 비교하거나 약제로 찾을 수 있어요." if out["drugs"]
                                                       else "맞는 질환을 찾지 못했습니다. 다른 표현으로 입력해 주세요."), "options": []}]
        return out
    out["targets"] = [{"id": t, "name": gmap[t]["name"], "kind": gmap[t]["kind"]} for t in picked]
    out["alternatives"] = []   # 이름 매칭 후보는 화면에 내지 않는다(엉뚱한 질환 제안 방지) — LLM 이 고른 것만
    facets = group_facets(conn, picked)
    out["facets"] = facets
    valid = {"settings": {s["code"] for s in facets["settings"]}, "line": {l["value"] for l in facets["lines"]},
             "markers": {m["value"] for m in facets["markers"]}}
    if llm:
        ctx = {"query": query, "answers": answers, "diseases": [gmap[t]["name"] for t in picked], "values": {
            "settings": [{"value": s["code"], "label": s["label"]} for s in facets["settings"]],
            "line": [{"value": l["value"], "label": l["label"]} for l in facets["lines"]],
            "markers": [{"value": m["value"], "label": m["label"]} for m in facets["markers"]]}}
        try:
            j, _ = llm(SYS_REFINE, json.dumps(ctx, ensure_ascii=False), 900)
        except Exception as e:  # noqa: BLE001
            j, out["ai_error"] = {}, str(e)[:200]
        f = j.get("filters") if isinstance(j.get("filters"), dict) else {}
        ln = _as_int(f.get("line"))
        out["filters"] = {"settings": [x for x in _as_list(f.get("settings")) if x in valid["settings"]],
                          "line": ln if ln in valid["line"] else None,
                          "markers": [x for x in _as_list(f.get("markers")) if x in valid["markers"]]}
        if out["filters"]["line"]:
            out["filters"]["settings"] = []
        out["keywords"] = [str(k)[:30] for k in _as_list(j.get("keywords")) if str(k).strip()][:5]
        for qd in [q for q in _as_list(j.get("questions")) if isinstance(q, dict)][:2]:
            field = qd.get("field")
            if field not in valid or field in answers or out["filters"].get(field):
                continue
            if field == "settings" and out["filters"]["line"]:
                continue
            if field == "line" and out["filters"]["settings"] and set(out["filters"]["settings"]) <= EARLY_SETTINGS:
                continue   # 수술 전·후 요법에는 차수 개념이 없다
            opts = []
            for o in [o for o in _as_list(qd.get("options")) if isinstance(o, dict)]:
                v = _as_int(o.get("value")) if field == "line" else o.get("value")
                if v is None or (isinstance(v, (str, int)) and v in valid[field]):
                    opts.append({"label": str(o.get("label", v))[:40], "value": v})
            if any(o.get("value") is not None for o in opts):
                out["questions"].append({"field": field, "text": qd.get("text", ""), "options": opts})
        out["summary"] = j.get("summary", "")
        if not drug_names:
            drug_names = [str(d)[:40] for d in _as_list(j.get("drugs")) if str(d).strip()][:3]
    _guard_filters(out["filters"], query)
    for k, v in answers.items():                      # 사용자가 답한 값이 최우선
        if k == "line":
            v = _as_int(v)
            out["filters"]["line"] = v if v in valid["line"] else None
        elif k in valid:
            vals = v if isinstance(v, list) else ([v] if v not in (None, "") else [])
            out["filters"][k] = [x for x in vals if x in valid[k]]
    out["drugs"] = _scan_drugs(conn, query, drug_names)
    if out["drugs"]:      # 약이 정해졌으면 바이오마커·변이 되묻기는 생략(질환·차수만 묻는다)
        out["questions"] = [q for q in out["questions"] if q.get("field") != "markers"]
    return out


def _resolve_names(conn: sqlite3.Connection, names: list[str]) -> list[dict]:
    """약 이름만 남긴다 — 바이오마커(HER2·BRCA·PD-L1)·질환명은 약이 아니다."""
    out = []
    for n in names:
        if _ANY_MARKER.fullmatch(n.strip()) or _ANY_MARKER.match(n.strip()) and len(n.strip()) <= 6:
            continue
        r = resolve_drug(conn, n)
        if r["inns"]:
            out.append({"name": n, "inns": r["inns"][:2]})
    return out


# ── 3) 비교 ──────────────────────────────────────────────────────────────────

LIST_MARK = re.compile(r"(?m)^\s*(?:[가-하]\)|[가-하]\.|\d{1,2}\)|\d{1,2}\.|\(\d{1,2}\)|\([가-하]\)|[①-⑳]|[-•·*])\s*")
NOTE_MARK = re.compile(r"-\s*(?:아\s*래|다\s*음)\s*-")


def _diff_norm(text: str) -> str:
    """비교용 정규화: 줄 이어붙이기, 각주·글머리 번호(가)·1)·①)·'- 아래 -' 제거(번호만 다른 것은 차이가 아님)."""
    t = clean(text or "")
    t = re.sub(r"\(\s*(?:개정\s*)?고시\s*제[^)]*\)", "", t)   # 끝에 붙은 '(고시 제…호, 날짜)' 는 차이가 아님
    t = NOTE_MARK.sub("", t)
    t = LIST_MARK.sub("", t)
    t = re.sub(r"[ \t]+", " ", t)
    return "\n".join(x.strip() for x in t.split("\n") if x.strip())


SECTION_HEAD = re.compile(r"^\s*(?:[가-하]|\d{1,2})\.\s*(\S.{0,160})$")


def sections(text: str, flat: bool = False) -> list[dict]:
    """'1. ○○'(level 1) / '가. ○○'(level 2) 문단 단위로 나눈다. level 1 문단에는 그 아래 '가.' 문단을 포함한다."""
    raw: list[dict] = [{"heading": "", "text": "", "level": 0, "parent": ""}]
    cur_parent = ""
    for ln in (text or "").split("\n"):
        m = SECTION_HEAD.match(ln)
        long_l1 = bool(m and re.match(r"^\s*\d", ln) and len(ln) < 170 and re.search(r"허가사항\s*범위를\s*초과|교체\s*투여|금기", ln))   # 줄이 합쳐진 긴 1단계 제목
        if m and (len(ln) < 70 or long_l1) and not re.search(r"(함|음|됨|임)\.?$", ln.strip()):
            lvl = 1 if re.match(r"^\s*\d", ln) else 2
            if lvl == 1:
                cur_parent = ln.strip()
            raw.append({"heading": ln.strip(), "text": "", "level": lvl, "parent": cur_parent if lvl == 2 else ""})
        else:
            raw[-1]["text"] += ln + "\n"
    raw = [x for x in raw if (x["heading"] or x["text"].strip())]
    has_l1 = any(x["level"] == 1 for x in raw)
    if not has_l1 or flat:
        return raw
    out: list[dict] = []
    for x in raw:      # level 2 는 앞선 level 1 문단 본문에 합친다
        if x["level"] == 2 and out and out[-1]["level"] == 1:
            out[-1]["text"] += x["heading"] + "\n" + x["text"]
        else:
            out.append(dict(x))
    return out


def _sig(ch: str) -> bool:
    """비교에 의미 있는 글자(공백·구두점·따옴표·괄호는 무시)."""
    return ch.isalnum() or ch in "%≥≤≧≦<>+~∼=" or "\uac00" <= ch <= "\ud7a3"


def _snap(text: str, i: int, j: int) -> tuple[int, int]:
    while i > 0 and not text[i - 1].isspace():
        i -= 1
    while j < len(text) and not text[j].isspace():
        j += 1
    return i, j


def text_diff(base: str, other: str) -> list[dict]:
    """base 대비 other 의 차이 → [{op: eq|add|del, text}]. 띄어쓰기·줄바꿈·글머리 번호 차이는 무시,
    표시는 단어 단위로 넓혀 조각난 하이라이트를 막는다."""
    A, B = _diff_norm(base), _diff_norm(other)
    a_idx = [k for k, ch in enumerate(A) if _sig(ch)]
    b_idx = [k for k, ch in enumerate(B) if _sig(ch)]
    sm = difflib.SequenceMatcher(a=[A[k] for k in a_idx], b=[B[k] for k in b_idx],
                                 autojunk=len(a_idx) + len(b_idx) > 24000)
    changed = [False] * len(B)
    dels: dict[int, list[str]] = defaultdict(list)
    spans: list[list[int]] = []
    anchors: list[int] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if j2 > j1:
            x, y = _snap(B, b_idx[j1], b_idx[j2 - 1] + 1)
            for k in range(x, y):
                changed[k] = True
        if i2 > i1:
            x, y = _snap(A, a_idx[i1], a_idx[i2 - 1] + 1)
            anchor = b_idx[j1] if j1 < len(b_idx) else len(B)
            anchor = _snap(B, anchor, anchor)[0] if anchor < len(B) else anchor
            if spans and x <= spans[-1][1]:
                spans[-1][1] = max(spans[-1][1], y)
            else:
                spans.append([x, y])
                anchors.append(anchor)
    for (x, y), anchor in zip(spans, anchors):
        dels[anchor].append(A[x:y].strip())
    out: list[dict] = []

    def push(op, t):
        if not t:
            return
        if out and out[-1]["op"] == op:
            out[-1]["text"] += t
        else:
            out.append({"op": op, "text": t})
    for k, ch in enumerate(B):
        if k in dels:
            for d in dels.pop(k):
                push("del", d + " ")
        push("add" if changed[k] and (not ch.isspace() or (k + 1 < len(B) and changed[k + 1])) else "eq", ch)
    for k in sorted(dels):
        for d in dels[k]:
            push("del", " " + d)
    return out


def _has_diff(d: list[dict]) -> bool:
    return any(x["op"] != "eq" and x["text"].strip() for x in d)


def _similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(a=_norm(a), b=_norm(b), autojunk=False).ratio()


TOPICS = ["금기", "교체", "평가", "투여대상", "결핵", "자가주사", "처방기간", "투여용량", "투여기간"]


def _topic(h: str) -> str:
    n = _norm(h or "")
    return next((t for t in TOPICS if t in n), "")


def _head_key(h: str) -> str:
    return _norm(re.sub(r"^\s*(?:[가-하]|\d{1,2})\.\s*", "", h or ""))


def section_diff(base_text: str, other_text: str, terms: list[str] | None = None) -> list[dict]:
    """문단(질환) 단위 정렬 diff → [{heading, status: same|changed|only_this|only_base, diff, text}]."""
    bs, os_ = sections(base_text, flat=True), sections(other_text, flat=True)   # 질환 문단(가.)끼리 맞춘다
    if terms:
        f = lambda ss: [s for s in ss if any(t in _norm(s["heading"] + s["text"]) for t in terms)] or ss  # noqa: E731
        bs, os_ = f(bs), f(os_)
    if len(bs) < 2 and len(os_) < 2:
        tb = base_text if not terms else "\n".join(s["heading"] + "\n" + s["text"] for s in bs)
        to = other_text if not terms else "\n".join(s["heading"] + "\n" + s["text"] for s in os_)
        d = text_diff(tb, to)
        return [{"heading": "", "status": "changed" if _has_diff(d) else "same", "diff": d, "orig": (tb, to)}]
    used, blocks = set(), []
    pairs = sum(1 for s in os_ if any(_similar(_head_key(s["heading"]), _head_key(b["heading"])) >= 0.55 for b in bs if s["heading"] and b["heading"]))
    pb = next((x for x in bs if not x["heading"]), None)
    po = next((x for x in os_ if not x["heading"]), None)
    preface_skew = bool(pb and po and max(len(pb["text"]), len(po["text"])) > 200
                        and min(len(pb["text"]), len(po["text"])) < 0.35 * max(len(pb["text"]), len(po["text"])))   # 한쪽만 '가. 투여대상' 제목이 풀려 서두가 비정상적으로 긴 경우
    if pairs < 0.5 * min(len(os_), len(bs)) or preface_skew:   # 문단 구조가 크게 다르면 통째 비교(가짜 '이 약은 없음' 방지)
        jb = "\n".join(x["heading"] + "\n" + x["text"] for x in bs)
        jo = "\n".join(x["heading"] + "\n" + x["text"] for x in os_)
        d = text_diff(jb, jo)
        return [{"heading": "", "status": "changed" if _has_diff(d) else "same", "diff": d, "orig": (jb, jo)}]
    other_all = _norm(" ".join(x["heading"] + x["text"] for x in os_))
    for s in os_:
        best, score = None, 0.0
        for k, b in enumerate(bs):
            if k in used:
                continue
            sc = _similar(_head_key(s["heading"]), _head_key(b["heading"])) if (s["heading"] or b["heading"]) else 0.9
            if _topic(s["heading"]) and _topic(s["heading"]) == _topic(b["heading"]):
                sc = max(sc, 0.8)          # 제목 표현만 다른 같은 주제(금기·교체투여·평가 등)
            if sc > score:
                best, score = k, sc
        if best is not None and score >= 0.55:
            used.add(best)
            d = text_diff(bs[best]["text"], s["text"])
            blocks.append({"heading": s["heading"], "status": "changed" if _has_diff(d) else "same", "diff": d, "parent": s.get("parent", ""),
                           "orig": (bs[best]["text"], s["text"])})
        else:
            blocks.append({"heading": s["heading"], "status": "only_this", "diff": None, "text": s["text"].strip()})
    for k, b in enumerate(bs):
        if k not in used and b["heading"] and _head_key(b["heading"])[:8] not in other_all:
            blocks.append({"heading": b["heading"], "status": "only_base", "diff": None, "text": ""})
    return blocks


def _cell_text(r: dict) -> str:
    """비교용 칸 문장: 병용 신약 추가 요법은 '추가 약제 조건 + 기존 요법 조건'."""
    if r["is_combo_table"]:
        return f"추가 약제 조건: {r['added_condition']}\n급여되는 기존 요법: {r['base_regimen'] or ''}\n{r['target']}"
    return r["target"]


def _core_name(name: str) -> str:
    """같은 질환의 세부 코드 이름을 하나로: '혈청검사양성 류마티스관절염'·'기타 류마티스관절염' → '류마티스관절염'."""
    n = re.sub(r"\[.*?\]|\(.*?\)", "", name or "")
    n = re.sub(r"^(혈청검사양성|혈청검사음성|기타|상세불명의|달리 분류되지 않은)\s*", "", n.strip())
    return n.strip()


def drug_profile(conn: sqlite3.Connection, q: str) -> dict:
    s = search_drug(conn, q)
    own = [g for g in s["general"] if g.get("match") == "title"]
    s["general"] = own or s["general"]
    label = f"{q} ({s['resolved']['inns'][0]})" if s["resolved"]["inns"] and s["resolved"]["inns"][0] != q.lower() else q
    diseases: dict[str, str] = {}
    for o in s["onco"]:
        diseases[f"onco:{o['cancer_code']}"] = o["cancer"]
    covered = {r for g in disease_groups(conn) if g["kind"] == "onco" for r in g["roots"]}
    for g in s["general"]:
        for k in g["kcd"]:
            if k.get("rel") == "mention":
                continue
            root = gkey(k["kcd_code"])
            if root not in covered:
                # 그룹 이름 우선, 없으면 세부 코드 이름(M35 '결합조직의 기타 전신침범' 대신 M352 '베체트병')
                nm = GROUP_NAME.get(root) or (k.get("name_kr") if len(k["kcd_code"]) > 3 and k.get("name_kr") else None) \
                    or (conn.execute("SELECT name_kr FROM kcd_code WHERE code=?", (root,)).fetchone() or [root])[0]
                diseases.setdefault(f"kcd:{root}", _core_name(nm))
    text = _norm(" ".join(g["criteria"] for g in s["general"]))
    return {"query": q, "label": label, "inns": s["resolved"]["inns"], "onco": s["onco"], "general": s["general"],
            "diseases": diseases, "text": text,
            "bundle_only": bool(s["general"]) and not own}


def _focus_terms(focus: Optional[str], gmap: dict) -> Optional[list[str]]:
    if not focus:
        return None
    if focus.startswith("name:"):
        return _disease_terms([focus[5:]], [])
    if focus.startswith("kcd:"):
        root = focus[4:]
        nm = gmap[root]["name"] if root in gmap else GROUP_NAME.get(root, root)
        return _disease_terms([nm], [])
    if focus in gmap:
        return _disease_terms([gmap[focus]["name"]], [])
    return None


def compare(conn: sqlite3.Connection, queries: list[str], base: int = 0, focus: Optional[str] = None,
            line: Optional[int] = None) -> dict:
    """최대 3개 약제를 기준 약제(base) 대비 비교.
    status: base | same | changed | only_this(이 약만 급여) | only_base(이 약은 해당 없음) | none"""
    queries = [q for q in queries if (q or "").strip()][:3]
    if not queries:
        return {"drugs": []}
    base = base if 0 <= base < len(queries) else 0
    profs = [drug_profile(conn, q) for q in queries]
    gmap = {g["id"]: g for g in disease_groups(conn)}
    if focus in gmap and gmap[focus]["kind"] == "kcd":
        focus = f"kcd:{gmap[focus]['roots'][0]}"
    focus_terms = _focus_terms(focus, gmap)
    focus_roots = [focus[4:]] if focus and focus.startswith("kcd:") else (gmap[focus]["roots"] if focus in gmap and gmap[focus]["kind"] == "kcd" else None)
    # 급여 질환: 질병코드 + 실제 기준 문구에 질환명이 있는지(자동 매핑 누락 보정)
    all_d: dict[str, str] = {}
    for p in profs:
        all_d.update(p["diseases"])
    coverage = []
    for did, name in sorted(all_d.items(), key=lambda x: (not x[0].startswith("onco:"), x[1])):
        has = [did in p["diseases"] for p in profs]
        coverage.append({"id": did, "name": name, "has": has,
                         "status": ["base" if i == base else ("same" if h == has[base] else ("only_this" if h else "only_base"))
                                    for i, h in enumerate(has)]})
    focus_options = _focus_options(coverage)          # 필터 목록은 항상 전체(골라도 드롭다운 유지)
    if focus:                       # 질환을 고르면 급여 질환 표·차이 수도 그 질환만
        coverage = [c for c in coverage if c["id"] == focus] or coverage
    # 항암 칸
    cells: dict[tuple, list[list[dict]]] = {}
    for i, p in enumerate(profs):
        for o in p["onco"]:
            if focus and focus.startswith("onco:") and f"onco:{o['cancer_code']}" != focus:
                continue
            if line and (not line_matches(o["line_code"], line) or
                         (o["line_code"] == "NA" and set(o["setting_code"].split("|")) <= EARLY_SETTINGS)):
                continue
            k = (o["cancer_code"], o["cancer"], o["is_combo_table"], o["setting_code"], o["line_code"])
            cells.setdefault(k, [[] for _ in profs])[i].append(o)
    onco = []
    for k in sorted(cells, key=lambda k: (_cancer_key(k[0]), k[2], _skey(k[3]), _lkey(k[4]))):
        per = cells[k]
        brows = per[base]
        entries = []
        for i, rows in enumerate(per):
            if i == base:
                entries.append({"status": "base" if rows else "none", "rows": [{**r, "diff": None} for r in rows]})
            elif not rows:
                entries.append({"status": "only_base" if brows else "none", "rows": []})
            elif not brows:
                entries.append({"status": "only_this", "rows": [{**r, "diff": None} for r in rows]})
            else:
                out_rows, changed = [], False
                for r in rows:
                    best = max(brows, key=lambda b: _similar(_cell_text(b), _cell_text(r)))
                    d = text_diff(_cell_text(best), _cell_text(r))
                    changed |= _has_diff(d)
                    out_rows.append({**r, "diff": d, "compared_with": best["regimen"]})
                entries.append({"status": "changed" if changed else "same", "rows": out_rows})
        onco.append({"cancer": k[1], "combo": k[2], "setting": setting_label(k[3]), "line": line_label(k[4]),
                     "setting_code": k[3], "line_code": k[4], "entries": entries,
                     "differs": any(e["status"] in ("changed", "only_this", "only_base") for e in entries)})
    # 일반약제 (문단 단위)
    bp = profs[base]
    general = []
    for i, p in enumerate(profs):
        items = []
        for g in p["general"]:
            if focus_roots and not any(gkey(k["kcd_code"]) == gkey(focus_roots[0]) and k.get("rel") != "mention" for k in g["kcd"]):
                continue                  # 이 약에 선택한 질환의 급여기준이 없으면 다른 질환 문단과 비교하지 않는다
            if focus_roots:
                raw = conn.execute("SELECT criteria_text FROM drug_criteria_item WHERE item_key=?", (g["key"],)).fetchone()
                ex = _section_excerpt(conn, g["key"], raw[0] if raw else "", focus_roots)
                if ex:
                    g = {**g, "excerpt": ex, "criteria": ex}
            if focus_terms and not (focus_roots and g.get("excerpt")):
                hit = [sct for sct in sections(g["criteria"]) if any(t in _norm(sct["heading"] + sct["text"]) for t in focus_terms)]
                if hit:
                    g = {**g, "excerpt": "\n".join((sct["heading"] + "\n" + sct["text"]).strip() for sct in hit)}
            if i == base or not bp["general"]:
                items.append({**g, "blocks": None, "compared_with": None, "status": "base" if i == base else "only_this"})
                continue
            g_roots = {gkey(k["kcd_code"]) for k in g["kcd"]}
            best = max(bp["general"], key=lambda b: (len(g_roots & {gkey(k["kcd_code"]) for k in b["kcd"]}),
                                                     _similar(b["criteria"][:800], g["criteria"][:800])))
            if best["key"] == g["key"]:
                items.append({**g, "blocks": None, "compared_with": best["title"], "status": "shared"})
                continue
            blocks = section_diff(best["criteria"], g["criteria"], focus_terms)
            st = "same" if all(b["status"] == "same" for b in blocks) else "changed"
            shared = _shared_disease_name(conn, g, best, focus_roots)
            items.append({**g, "blocks": blocks, "compared_with": best["title"], "status": st, "shared_disease": shared})
        general.append(items)
    names = [p["label"].split(" (")[0] for p in profs]
    return {"base": base, "focus": focus, "line": line,
            "drugs": [{"query": p["query"], "label": p["label"], "inns": p["inns"], "onco_count": len(p["onco"]),
                       "general_count": len(p["general"]),
                       "bundle_only": p["bundle_only"]} for p in profs],
            "coverage": coverage, "onco": onco, "general": general,
            "focus_options": focus_options,
            "highlights": _coverage_lines(coverage, names, base) + _highlights(onco, general, names, base),
            "sources": _compare_sources(profs, names),
            "summary": {"cells": len(onco), "differ": sum(1 for o in onco if o["differs"]),
                        "diseases_differ": sum(1 for c in coverage if any(s in ("only_this", "only_base") for s in c["status"]))},
            "source": _source_meta(conn)}


def _focus_options(coverage: list[dict]) -> list[dict]:
    return [{"id": c["id"], "name": c["name"]} for c in coverage]


SETTING_PLAIN = {"neoadjuvant": "수술 전", "perioperative": "수술 전후", "adjuvant": "수술 후", "ccrt": "동시항암방사선",
                 "primary": "일차요법", "induction": "관해유도", "consolidation": "공고요법", "maintenance": "유지요법",
                 "palliative": "진행·전이", "salvage": "구제요법", "relapsed_refractory": "재발·불응", "newly_diagnosed": "새로 진단",
                 "intravesical": "방광 내 주입", "conditioning": "이식 전처치"}
LINE_PLAIN = {"1L": "1차", "1L+": "1차 이상", "2L": "2차", "2L+": "2차 이상", "3L": "3차", "3L+": "3차 이상",
              "2L_or_3L": "2차 또는 3차", "1L_or_1L+": "1차·1차 이상"}


def plain_step(setting_code: str, line_code: str) -> str:
    st = " · ".join(SETTING_PLAIN.get(x, "") for x in setting_code.split("|") if SETTING_PLAIN.get(x))
    ln = " / ".join(LINE_PLAIN.get(x, x) for x in line_code.split("|") if x != "NA")
    return " · ".join(x for x in (st, ln) if x)


def _sentences(text: str) -> list[str]:
    return [x.strip() for x in re.split(r"\n|※|(?<=[.。])\s+", text or "") if len(x.strip()) >= 4]


def _changed_sentences(d: list[dict], op: str, n: int = 2, maxlen: int = 90) -> list[str]:
    """diff 에서 op(add=이 약, del=기준 약) 쪽 문장 중 바뀐 부분이 든 문장만 — 조각 대신 읽을 수 있는 문장."""
    side = "".join(x["text"] for x in d if x["op"] in ("eq", op))
    segs = [re.sub(r"\s+", " ", x["text"]).strip(" ,.") for x in d if x["op"] == op]
    segs = [x for x in segs if len(re.sub(r"\W", "", x)) >= 2]
    out = []
    for sent in _sentences(side):
        ns = re.sub(r"\s+", " ", sent)
        hit = next((g for g in segs if g and g in ns), None)
        if hit:
            if len(ns) > maxlen:   # 바뀐 부분 중심으로 자르기
                i = max(0, ns.find(hit) - 30)
                ns = ("…" if i else "") + ns[i:i + maxlen].rsplit(" ", 1)[0] + "…"
            if ns not in out:
                out.append(ns)
        if len(out) >= n:
            break
    return out


ATTR_PATTERNS = [
    ("연령", re.compile(r"만\s*\d+\s*(?:세|개월)\s*(?:이상|이하|미만|초과)?(?:\s*[-~∼–]\s*만?\s*\d+\s*(?:세|개월)\s*(?:이상|이하|미만|초과)?)?")),
    ("체중", re.compile(r"체중\s*\d+(?:\.\d+)?\s*kg\s*(?:이상|이하|미만|초과)?")),
    ("PD-L1", re.compile(r"(?:PD-?L1[^.\n]{0,25}?|CPS\s*|TPS\s*)(?:≥|≧|>=|>|이상)\s*\d+\s*%?|발현\s*비율\s*(?:≥|≧|>=)\s*\d+\s*%", re.I)),
    ("점수", re.compile(r"\b(?:EASI|PASI|DAS28|BSA|IGA|vIGA|CDAI|PCDAI|Mayo|BASDAI|SCORAD|DLQI|NRS|UAS7|ASDAS|HbA1[cC])"
                        r"[^\d\n.]{0,12}?(?:≥|≧|≤|≦|>|<)?\s*\d+(?:\.\d+)?\s*(?:%|점)?\s*(?:이상|이하|미만|초과)?")),
    ("용법·용량", re.compile(r"\d+(?:\.\d+)?\s*mg/(?:kg|㎡|m2)[^.,\n]{0,8}?(?:\d+\s*주\s*간격)?")),
    ("병기", re.compile(r"stage\s*[ⅠⅡⅢⅣIV]+[ABC]?\s*(?:이상|이하)?", re.I)),
]
_DUR = re.compile(r"(?<![\d,.])(\d+)\s*(?:[~∼]\s*(\d+)\s*)?(주|개월|년)(?!울)")
BOILER = re.compile(r"허가사항\s*범위\s*내에서|동\s*인정기준\s*이외에는|약값\s*전액을|요양\s*급여를\s*인정하며")
_SUBKIND = re.compile(r"^\s*(?:\(?\d{1,2}\)|\([가-하]\)|[가-하]\)|\d{1,2}\.|[가-하]\.)\s*(투여\s*대상|평가\s*방법|평가|투여\s*방법|투여\s*기간|투여\s*중지|투여\s*용량|교체\s*투여|금기)")


def _line_kinds(t: str) -> list[tuple[int, str]]:
    """본문에서 '1) 투여대상 / 2) 평가방법' 같은 소제목이 시작되는 위치와 종류 — 수치가 어느 소제목 밑인지로 기간 종류를 정한다."""
    out, pos, kind = [], 0, ""
    for ln in t.split("\n"):
        m = _SUBKIND.match(ln)
        if m:
            w = m.group(1).replace(" ", "")
            kind = "eval" if w.startswith("평가") else "target" if w == "투여대상" else "skip" if w in ("교체투여", "금기") else "admin"
        out.append((pos, kind))
        pos += len(ln) + 1
    return out


def extract_attrs(text: str) -> dict[str, list[str]]:
    """비교용 핵심 수치: 연령·체중·PD-L1·점수·용량·병기·기간(증상 지속/이전 치료/평가 시점)."""
    t = re.sub(r"[ \t]+", " ", text or "")
    t = re.sub(r"\bBASDA(?!I)", "BASDAI", t)          # 원문 오타 통일
    out: dict[str, list[str]] = {}
    for key, pat in ATTR_PATTERNS:
        vals = [re.sub(r"\s+", " ", re.sub(r"[()]", " ", m.group(0))).strip(" ,") for m in pat.finditer(t)]
        if key == "PD-L1":       # '≥50%' 만
            vals = list(dict.fromkeys("≥" + m.group(1) + "%" for v in vals
                                      for m in [re.search(r"(?:≥|≧|>=|>)\s*(\d+)", v)] if m))
        if vals:
            out[key] = list(dict.fromkeys(vals))
    kinds = _line_kinds(t)
    import bisect
    starts = [p for p, _ in kinds]
    for m in _DUR.finditer(t):
        if int(m.group(1)) >= 100:            # '2010년 제정' 같은 연도
            continue
        after = t[m.end():m.end() + 14]
        if re.match(r"^\s*\)?\s*(?:간격|마다)?\s*(?:으로\s*)?투여", after) or re.match(r"^\s*\)", after):
            continue                          # '(0, 2주) 투여' = 투여 일정, 기준 아님
        kind = kinds[max(0, bisect.bisect_right(starts, m.start()) - 1)][1] if kinds else ""
        if kind in ("admin", "skip"):
            continue                          # 투여방법·기간·교체투여·금기 아래 수치는 비교 대상이 아니다
        w = t[max(0, m.start() - 22):m.end() + 10]
        near = re.split(r"[.;※]", t[m.end():m.end() + 34])[0]        # 같은 문장 안 34자 이내에 '평가' → 평가 시점
        if kind == "eval":
            key = "평가 시점"
        elif kind == "target":
            key = "증상 지속" if re.search(r"증상|지속|병력", w) else "이전 치료 기간" if re.search(r"투여|치료|사용", w) else None
        else:
            key = "평가 시점" if "평가" in near or "평가" in w else "증상 지속" if re.search(r"증상|지속|병력", w) else \
                  "이전 치료 기간" if re.search(r"투여|치료|사용", w) else None
        if key:
            out.setdefault(key, [])
            rng = f"{m.group(1)}~{m.group(2)}" if m.group(2) else m.group(1)
            v = rng + m.group(3) + (" 이상" if "이상" in t[m.end():m.end() + 4] else "")
            if v not in out[key]:
                out[key].append(v)
    return out


_NUM_TOK = re.compile(r"\d+(?:\.\d+)?(?:\s*[~∼]\s*\d+(?:\.\d+)?)?\s*(?:주|개월|일|년|%|mg/kg|mg|kg|세|점|회|주기|㎍|ng/ml)")


def numeric_diff(d: list[dict]) -> Optional[tuple[list[str], list[str]]]:
    """같은 단위(주·개월·%·mg/kg…)로 양쪽에 서로 다른 값이 있을 때만 (이 약 값, 기준 약 값).
    한쪽에만 있는 단위('3회'·'20점' 등 그 약 고유 기준)는 비교 대상이 아니라 제외."""
    def by_unit(text: str) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for m in _NUM_TOK.finditer(text):
            tok = re.sub(r"\s+", "", m.group(0))
            unit = re.sub(r"[\d.~∼]+", "", tok)
            out.setdefault(unit, [])
            if tok not in out[unit]:
                out[unit].append(tok)
        return out
    mine, base = by_unit(_side(d, "add")), by_unit(_side(d, "del"))
    om, ob = [], []
    for u in mine.keys() & base.keys():
        a = [x for x in mine[u] if x not in base[u]]
        b = [x for x in base[u] if x not in mine[u]]
        if a and b:
            om += a
            ob += b
    return (om, ob) if (om and ob) else None


def attr_diff(base_text: str, other_text: str) -> list[tuple[str, list[str], list[str]]]:
    a, b = extract_attrs(base_text), extract_attrs(other_text)
    out = []
    for k in [x for x, _ in ATTR_PATTERNS] + ["증상 지속", "이전 치료 기간", "평가 시점"]:
        va, vb = a.get(k, []), b.get(k, [])
        if k in ("증상 지속", "이전 치료 기간", "평가 시점") and not (va and vb):
            continue                          # 기간은 문맥 추출이 불안정 → 양쪽 다 있을 때만 비교('~만' 오판 방지)
        if set(map(_norm, va)) != set(map(_norm, vb)) and (va or vb):
            out.append((k, va, vb))
    return out


def _side(d: list[dict], op: str) -> str:
    return "".join(x["text"] for x in d if x["op"] in ("eq", op))


def _fmt_attrs(diffs, name_o: str, name_b: str, n: int = 3) -> list[str]:
    """'연령 — 듀피젠트만 만 6세-만 11세' / '평가 시점 — 레미케이드 14주 ↔ 휴미라 16주' (다른 값만)."""
    out = []
    for k, vb, vo in diffs[:n]:
        if k in ("증상 지속", "이전 치료 기간", "평가 시점"):       # 기간은 양쪽 값을 다 보여준다
            out.append(f"{k} — {name_o} {', '.join(vo[:3])} ↔ {name_b} {', '.join(vb[:3])}")
            continue
        nb, no = {_norm(x) for x in vb}, {_norm(x) for x in vo}
        only_o = [x for x in vo if _norm(x) not in nb]
        only_b = [x for x in vb if _norm(x) not in no]
        if only_o and only_b:
            out.append(f"{k} — {name_o} {', '.join(only_o[:3])} ↔ {name_b} {', '.join(only_b[:3])}")
        elif only_o:
            out.append(f"{k} — {name_o}만 {', '.join(only_o[:3])}")
        elif only_b:
            out.append(f"{k} — {name_b}만 {', '.join(only_b[:3])}")
    return out


def _compare_sources(profs: list[dict], names: list[str]) -> list[str]:
    """요약 복사에 붙일 출처: 약마다 일반약제 고시번호·시행일, 항암 공고번호(최신)."""
    out = []
    for p, n in zip(profs, names):
        bits = [f"{g['notice']}({g['effective']} 시행)" for g in p["general"][:2] if g.get("notice")]
        onco = sorted({(o["notice"], o["effective"]) for o in p["onco"] if o.get("notice")}, key=lambda x: x[1] or "", reverse=True)[:2]
        bits += [f"{a}({b} 시행)" for a, b in onco]
        if bits:
            out.append(f"{n}: " + ", ".join(bits))
    return out


def _shared_disease_name(conn: sqlite3.Connection, a: dict, b: dict, focus_roots: Optional[list[str]]) -> str:
    """두 기준이 함께 급여하는 질환 이름(비교 줄 머리: '해당 질환 기준' 대신 '아토피성 피부염')."""
    ga = {gkey(k["kcd_code"]): k for k in a["kcd"] if k.get("rel") != "mention"}
    gb = {gkey(k["kcd_code"]) for k in b["kcd"] if k.get("rel") != "mention"}
    roots = [r for r in ga if r in gb]
    if focus_roots:
        roots = [r for r in roots if r == gkey(focus_roots[0])] or roots
    names = []
    for r in roots[:2]:
        k = ga[r]
        nm = GROUP_NAME.get(r) or (conn.execute("SELECT name_kr FROM kcd_code WHERE code=?", (r,)).fetchone() or [k.get("name_kr") or r])[0]
        names.append(_core_name(nm))
    return " · ".join(names)


def _coverage_lines(coverage: list[dict], names: list[str], base: int) -> list[str]:
    out = []
    for c in coverage:
        if c["id"].startswith("onco:"):
            continue
        only = [names[i] for i, h in enumerate(c["has"]) if h]
        miss = [names[i] for i, h in enumerate(c["has"]) if not h]
        if only and miss and len(only) < len(names):
            out.append(f"급여 질환 — {c['name']}: {', '.join(only)}만 급여 ({', '.join(miss)} 해당 없음)")
    return out[:8]


def _highlights(onco: list[dict], general: list[list[dict]], names: list[str], base: int) -> list[str]:
    """비교 결론 요약 — 먼저 수치(연령·체중·PD-L1·점수·용량·기간) 차이, 없으면 바뀐 문장. 약마다 최대 4줄, 전체 12줄."""
    per: dict[int, list[str]] = defaultdict(list)
    combo_cancers = {(o["cancer"], i) for o in onco if o["combo"] for i, e in enumerate(o["entries"]) if e["rows"]}
    for o in onco:
        step = plain_step(o["setting_code"], o["line_code"])
        where = f"{o['cancer']}{' · 병용 신약 추가 요법' if o['combo'] else ''}{' · ' + step if step else ''}"
        for i, e in enumerate(o["entries"]):
            if i == base:
                continue
            if e["status"] == "only_this":
                what = "만 병용 등재(추가 약은 비급여, 기존 요법만 급여)" if o["combo"] else "만 급여"
                per[i].append(f"{where}: {names[i]}{what} — {', '.join(r['regimen'] for r in e['rows'][:2])}")
            elif e["status"] == "only_base":
                alt = " — 단 병용 신약 추가 요법으로는 등재" if (not o["combo"] and (o["cancer"], i) in combo_cancers) else ""
                per[i].append(f"{where}: {names[base]}{'만 병용 등재' if o['combo'] else '만 급여'} ({names[i]} 해당 없음{alt})")
            elif e["status"] == "changed":
                for r in e["rows"]:
                    ad = attr_diff(_side(r["diff"], "del"), _side(r["diff"], "add"))
                    if ad:
                        per[i].append(f"{where} — " + " / ".join(_fmt_attrs(ad, names[i], names[base])))
                        break
                    if o["combo"]:         # 병용 표는 문장 조각이 깨지기 쉬워 '문구가 다름'만 알린다
                        per[i].append(f"{where} — {names[i]}: 추가 약제 허가 조건 문구가 {names[base]}와 다름(아래 비교 표에서 확인)")
                        break
                    add = [x for x in _changed_sentences(r["diff"], "add") if not BOILER.search(x)]
                    rem = [x for x in _changed_sentences(r["diff"], "del") if not BOILER.search(x)]
                    if add or rem:
                        per[i].append(f"{where} — {names[i]}: " + " / ".join(add or ["(해당 문구 없음)"])
                                      + f"  ↔  {names[base]}: " + " / ".join(rem or ["(해당 문구 없음)"]))
                        break
    for i, items in enumerate(general):
        if i == base:
            continue
        for g in items:
            for b in g.get("blocks") or []:
                head = re.sub(r"^\s*(?:[가-하]|\d{1,2})\.\s*", "", b["heading"] or "") or g.get("shared_disease") or "해당 질환 기준"
                if len(head) > 46:
                    head = head[:44].rstrip(" ,(") + "…"
                if _topic(b["heading"]) in ("금기", "결핵") or _topic(b.get("parent", "")) in ("금기", "결핵"):
                    continue
                if re.match(r"^\s*\d{1,2}\.\s*허가사항\s*범위를\s*초과", b["heading"] or "") and b["status"] in ("only_this", "only_base"):
                    continue          # 하위 항목(괴저성 농피증 등)이 따로 줄로 나오므로 머리말 문단만의 줄은 생략
                if b["status"] == "changed":
                    ad = attr_diff(*(b.get("orig") or (_side(b["diff"], "del"), _side(b["diff"], "add"))))   # 원문 소제목(1) 투여대상 / 2) 평가방법)이 살아 있는 본문 기준
                    if ad:
                        per[i].append(f"[{names[i]} vs {names[base]}] {head} — " + " / ".join(_fmt_attrs(ad, names[i], names[base])))
                    else:
                        nd = numeric_diff(b["diff"])
                        if nd:       # 분류 못 한 수치 차이도 숨기지 않는다(예: 크론병 첫 평가 2주 ↔ 4주)
                            per[i].append(f"[{names[i]} vs {names[base]}] {head} — 수치 — {names[i]} {', '.join(nd[0]) or '—'} ↔ {names[base]} {', '.join(nd[1]) or '—'}")
                elif b["status"] == "only_this" and b["heading"]:
                    per[i].append(f"[{names[i]} vs {names[base]}] {head} — {names[i]}에만 있음")
                elif b["status"] == "only_base" and b["heading"]:
                    per[i].append(f"[{names[i]} vs {names[base]}] {head} — {names[base]}에만 있음")
            blocks = g.get("blocks") or []
            if blocks and not per[i] and not any(b["status"] in ("only_this", "only_base") for b in blocks) and all(
                    b["status"] == "same" or (b["diff"] and not numeric_diff(b["diff"]) and not attr_diff(
                        *(b.get("orig") or (_side(b["diff"], "del"), _side(b["diff"], "add"))))) for b in blocks):
                # 바뀐 부분에 수치가 하나도 없을 때만 '같음' — 수치가 있으면 위에서 차이로 나온다
                per[i].append(f"{names[i]}: {names[base]}와 연령·점수·기간 등 핵심 수치가 같음(문구 표현만 다름)")
    out: list[str] = []
    for i in sorted(per):
        out += per[i][:6]
    return out[:14]
