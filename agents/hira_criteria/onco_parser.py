"""항암 통합본(공고책자) kordoc Markdown → 요법 단위 구조화.

구조: 암종(목차 기준) → 투여요법(setting, '1. 선행화학요법(neoadjuvant)') → [소분류('가. HER2 양성…')]
      → 투여단계('가. 투여단계: 1차') → 표(연번·항암요법·투여대상[·투여단계·투여요법]).

투여단계 판정 우선순위: 표 '투여단계' 칼럼 > 소제목 '투여단계:' > 투여대상 문장 속 'N차' > NA.
표는 rowspan/colspan 을 격자로 펼친다. 한 연번 안에서
  - 항암요법 칸이 '(제2022-38호: 2022.3.1., 개정 …)' 인 행 → 공고 연혁
  - 나머지 → 투여대상/단계 변형(variant) 1행씩.
파싱이 확실하지 않은 부분은 parse_flags 로 남긴다(조용히 버리지 않음).
"""
from __future__ import annotations

import html as _html
import json
import re
from dataclasses import dataclass, field, asdict
from html.parser import HTMLParser
from typing import Optional

# ── 정규화 사전 ─────────────────────────────────────────────────────────────────────

SETTING_RULES = [  # (패턴, 코드) — 위에서부터 먼저 맞는 것
    (r"전체\s*선행|Total Neoadjuvant", "neoadjuvant"),
    (r"선행\s*방사선화학|선행화학|neoadjuvant|수술\s*전", "neoadjuvant"),
    (r"수술\s*전후|perioperative", "perioperative"),
    (r"수술\s*후\s*보조|adjuvant", "adjuvant"),
    (r"동시\s*항암\s*화학\s*방사선|concurrent", "ccrt"),
    (r"관해\s*공고|consolidation", "consolidation"),
    (r"관해\s*유도|induction", "induction"),
    (r"유지\s*요법|maintenance", "maintenance"),
    (r"구제\s*요법|salvage", "salvage"),
    (r"방광\s*내", "intravesical"),
    (r"전처치", "conditioning"),
    (r"일차\s*요법|primary therapy", "primary"),
    (r"고식적|palliative", "palliative"),
    (r"새로이\s*진단|새롭게\s*진단|newly", "newly_diagnosed"),
    (r"이전\s*치료에\s*실패|재발|불응", "relapsed_refractory"),
]
SETTING_HEAD_HINT = re.compile(r"요법|therapy|진단된|실패한 경우|주입", re.I)

BIOMARKER_RE = re.compile(
    r"(PD-?L1[^,)\n]{0,30}|EGFR[^,)\n]{0,20}|ALK[^,)\n]{0,12}|ROS-?1|BRAF\s*V600[EK]?|BRAF|KRAS\s*G12C|KRAS|"
    r"HER2[^,)\n]{0,12}|BRCA[^,)\n]{0,10}|상동재조합결핍[^,)\n]{0,20}|HRD|MSI-H|dMMR|pMMR|TMB-H|RET[\s-]*융합|RET|"
    r"MET[^,)\n]{0,15}|NTRK|IDH1|IDH2|FLT3|FGFR\d?|호르몬\s*수용체\s*양성|HR\+|CD20|CD30|CD19|BCMA|CD38|"
    r"필라델피아[^,)\n]{0,10}|Ph\+|T790M|CPS\s*[≥≧>]+\s*\d+|TPS\s*[≥≧>]+\s*\d+%?|del\(17p\)|TP53|PIK3CA|ESR1|"
    r"Claudin[\s-]*18\.2|CLDN18\.2|NRG1|HLA-A\*02:01|Exon\s*20|exon\s*20)", re.I)
STAGE_RE = re.compile(
    r"(stage\s*[ⅠⅡⅢⅣIV0-9][^,\n]{0,14}|병기\s*[ⅠⅡⅢⅣ0-9][^,\n]{0,10}|FIGO[^,\n]{0,20}|전이성|국소\s*진행성|"
    r"진행성|재발성|불응성|절제\s*불가능|수술이\s*불가능|절제\s*가능|국한성|확장성|제한성|고위험|저위험)", re.I)
PRIOR_KEYS = ("이전", "실패", "투여 후", "치료 후", "경험", "불응", "진행이 확인", "받지 않은", "투여한 적",
              "받은 적", "내성", "종료 후", "반응한", "반응(CR", "재발한 경우", "후 재발", "치료를 받은",
              "치료에 실패", "투여 받", "투여받")
HISTORY_RE = re.compile(r"^\(?\s*(?:개정\s*)?제\s*\d{4}\s*-\s*\d+\s*호")
NOTICE_RE = re.compile(r"(제\s*\d{4}\s*-\s*\d+\s*호)\s*[:：]\s*(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})")
CONT: dict[str, str] = {}   # 쪼개진 셀 뒷부분 → 합친 전체 문장

INLINE_NOTICE_RE = re.compile(r"\((?:개정\s*)?제\s*\d{4}\s*-\s*\d+\s*호\s*[:：][^()]*\)")
FOOTNOTE_RE = re.compile(r"주\s?(\d{1,2})(?![\d차])")


ABBR_SETTING = {"N": "neoadjuvant", "A": "adjuvant", "P": "palliative", "S": "salvage",
                "C": "ccrt", "M": "maintenance"}


def setting_code(raw: str) -> str:
    """투여요법 원문 → 코드. 약어 'N, A, P' 는 'neoadjuvant|adjuvant|palliative'. 빈칸/'-' → NA."""
    t = re.sub(r"\s+", " ", (raw or "").replace("<br>", " ")).strip()
    if not t or t in ("-", "－") or "세부기준" in t:
        return "NA"
    if re.fullmatch(r"[NAPSCM](?:\s*[,/·]\s*[NAPSCM])*", t):
        return "|".join(dict.fromkeys(ABBR_SETTING[x] for x in re.findall(r"[NAPSCM]", t)))
    codes = []
    for part in re.split(r"\s*[,/]\s*", t):
        for pat, code in SETTING_RULES:
            if re.search(pat, part, re.I):
                codes.append(code)
                break
    return "|".join(dict.fromkeys(codes)) if codes else "other"


def line_code(raw: str) -> str:
    """'1차(first-line)'→1L, '2차 이상'→2L+, '1차 또는 1차 이상'→1L_or_1L+, '2차 또는 3차'→2L_or_3L,
    '1차 이상 / 2차 이상'→1L+|2L+, '-'/''→NA."""
    s = re.sub(r"\([^)]*\)", "", raw or "").replace("<br>", " ")
    s = re.sub(r"\s+", " ", s).strip()
    if not s or s in ("-", "－"):
        return "NA"
    parts = re.split(r"\s*/\s*", s)
    out = []
    for p in parts:
        alts = re.split(r"\s*또는\s*", p)
        codes = []
        for a in alts:
            m = re.search(r"(\d)\s*차\s*(이상)?", a)
            if m:
                codes.append(f"{m.group(1)}L" + ("+" if m.group(2) else ""))
        if codes:
            out.append("_or_".join(dict.fromkeys(codes)))
    return "|".join(out) if out else "NA"


PAREN_RE = re.compile(r"\(([^()]{1,40})\)")


def line_from_text(text: str) -> Optional[str]:
    """투여대상 문장 속 괄호 표기 '(2차 이상, 고식적요법)' / '(1차)' 만 신뢰한다.
    '1차 백금 기반 항암화학요법에 반응한' 같은 서술은 투여단계가 아니므로 쓰지 않는다."""
    for m in PAREN_RE.finditer(text or ""):
        g = m.group(1)
        mm = re.match(r"^\s*(\d\s*차(?:\s*이상)?(?:\s*또는\s*\d\s*차(?:\s*이상)?)?)\s*(?:,|$)", g)
        if mm:
            return line_code(mm.group(1))
    return None


def setting_from_text(text: str) -> str:
    """괄호 표기 '(1차 이상, 고식적요법)', '(수술후보조요법)', '(관해유도요법)' → setting 코드. 없으면 ''."""
    for m in PAREN_RE.finditer(text or ""):
        g = m.group(1)
        if re.search(r"요법|adjuvant|palliative", g) and len(g) < 30:
            code = setting_code(g.split(",")[-1])
            if code not in ("NA", "other"):
                return code
    return ""


# ── HTML 표 → 격자 ──────────────────────────────────────────────────────────────────

class _TableParser(HTMLParser):
    """최상위 표의 행/셀만 수집(중첩 표는 셀 안 HTML 문자열로 보존)."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.depth = 0
        self.rows: list[list[dict]] = []
        self.cell: Optional[dict] = None
        self.buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.depth += 1
            if self.depth == 1:
                return
        if self.depth == 1 and tag == "tr":
            self.rows.append([])
            return
        if self.depth == 1 and tag in ("td", "th"):
            a = dict(attrs)
            self.cell = {"th": tag == "th", "rowspan": int(a.get("rowspan", 1) or 1),
                         "colspan": int(a.get("colspan", 1) or 1)}
            self.buf = []
            return
        if self.cell is not None:
            self.buf.append(self.get_starttag_text() or "")

    def handle_endtag(self, tag):
        if tag == "table":
            if self.depth > 1 and self.cell is not None:
                self.buf.append("</table>")
            self.depth -= 1
            return
        if self.depth == 1 and tag in ("td", "th"):
            if self.cell is not None:
                self.cell["html"] = "".join(self.buf)
                if self.rows:
                    self.rows[-1].append(self.cell)
                self.cell = None
            return
        if self.depth == 1 and tag == "tr":
            return
        if self.cell is not None:
            self.buf.append(f"</{tag}>")

    def handle_data(self, data):
        if self.cell is not None:
            self.buf.append(data)

    def handle_entityref(self, name):
        if self.cell is not None:
            self.buf.append(f"&{name};")

    def handle_charref(self, name):
        if self.cell is not None:
            self.buf.append(f"&#{name};")


def cell_text(h: str) -> str:
    t = re.sub(r"<br\s*/?>", "\n", h or "")
    t = re.sub(r"</t[dh]>", " | ", t)
    t = re.sub(r"</tr>", "\n", t)
    t = re.sub(r"<[^>]+>", "", t)
    t = _html.unescape(t).replace("\xa0", " ")
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\n\s*", "\n", t).strip()


ORIGIN: dict[int, list[list[bool]]] = {}   # id(grid) → 원래 셀 여부(rowspan 복제 아님)


def table_grid(table_html: str) -> tuple[list[list[str]], list[bool]]:
    """→ (grid[row][col] = 셀 텍스트, is_header_row[row]). rowspan/colspan 은 복제해 채운다.
    원래 셀인지(복제 아님)는 ORIGIN[id(grid)] 에 둔다."""
    p = _TableParser()
    p.feed(table_html)
    grid: list[list[Optional[str]]] = []
    origin: list[list[bool]] = []
    header: list[bool] = []
    pending: dict[tuple[int, int], str] = {}
    for r, row in enumerate(p.rows):
        out: list[Optional[str]] = []
        org: list[bool] = []
        c = 0
        cells = iter(row)
        is_head = bool(row) and all(x["th"] for x in row)
        while True:
            while (r, c) in pending:
                out.append(pending.pop((r, c)))
                org.append(False)
                c += 1
            cell = next(cells, None)
            if cell is None:
                break
            txt = cell_text(cell.get("html", ""))
            for dc in range(cell["colspan"]):
                out.append(txt)
                org.append(dc == 0)
                for dr in range(1, cell["rowspan"]):
                    pending[(r + dr, c + dc)] = txt
            c += cell["colspan"]
        while (r, c) in pending:
            out.append(pending.pop((r, c)))
            org.append(False)
            c += 1
        grid.append(out)
        origin.append(org)
        header.append(is_head)
    width = max((len(x) for x in grid), default=0)
    grid = [x + [""] * (width - len(x)) for x in grid]
    origin = [x + [False] * (width - len(x)) for x in origin]
    ORIGIN[id(grid)] = origin
    return grid, header


def pipe_grid(lines: list[str], i: int) -> tuple[list[list[str]], list[bool], int]:
    """Markdown 파이프 표 → (grid, is_header_row, 다음 줄)."""
    rows, j = [], i
    while j < len(lines) and lines[j].strip().startswith("|"):
        rows.append(lines[j].strip())
        j += 1
    grid, head = [], []
    for k, r in enumerate(rows):
        cells = [c.strip().replace("<br>", "\n") for c in r.strip("|").split("|")]
        if k == 1 and all(re.fullmatch(r":?-{3,}:?", c) for c in cells if c):
            continue
        grid.append([_html.unescape(c) for c in cells])
        head.append(k == 0)
    return grid, head, j


# ── 레코드 ────────────────────────────────────────────────────────────────────────

@dataclass
class Regimen:
    cancer_code: str
    cancer_name_kr: str
    cancer_name_en: str
    setting_code: str
    setting_raw: str
    subgroup_raw: str
    line_code: str
    line_raw: str
    line_source: str
    table_idx: int
    regimen_no: str
    variant: int
    regimen_text: str
    drugs: list
    target_text: str
    prior_therapy: str
    biomarker_text: str
    stage_text: str
    self_pay: str
    footnotes: str
    notice_history: str
    last_notice_no: str
    last_effective: str
    extra: dict = field(default_factory=dict)
    parse_flags: list = field(default_factory=list)

    @property
    def key(self) -> str:
        return (f"{self.cancer_code}|{self.setting_code}|{self.line_code}|t{self.table_idx}|"
                f"{self.regimen_no}|v{self.variant}")


def _clean_regimen(text: str) -> tuple[str, str, str]:
    """→ (요법 텍스트, 각주 '주1,주3', 본인부담 표시)."""
    t = text.split("\n※")[0]
    t = INLINE_NOTICE_RE.sub("", t)
    t = re.sub(r"\n", " ", t).strip()
    notes = sorted(set(FOOTNOTE_RE.findall(t)), key=int)
    t = FOOTNOTE_RE.sub("", t)
    self_pay = ""
    if "(100/100)" in t or "약값 전액을 본인이 부담" in text:
        self_pay = "100/100"
    m = re.search(r"\((\d+/100)\)", t)
    if m and not self_pay:
        self_pay = m.group(1)
    t = re.sub(r"\s+", " ", t).strip(" ,")
    return t, ",".join(f"주{n}" for n in notes), self_pay


DRUG_SPLIT = re.compile(r"\s*(?:\+|→|±|,|/|\band\b)\s*")


def split_drugs(regimen_text: str) -> list[str]:
    t = re.sub(r"\[[^\]]*\]", " ", regimen_text)               # [Neoadjuvant]
    t = re.sub(r"\((?:100/100|\d+/100|weekly|tablet|SC|IV|PO|oral|capsule|비급여)[^)]*\)", " ", t, flags=re.I)
    out = []
    for tok in DRUG_SPLIT.split(t):
        tok = re.sub(r"\([^)]*\)", "", tok).strip(" .·-").lower()
        tok = re.sub(r"\s+", " ", tok)
        if not tok or len(tok) < 3 or re.fullmatch(r"[\d\s.]+", tok):
            continue
        out.append(tok)
    return list(dict.fromkeys(out))


BULLET_RE = re.compile(r"^(\||※|-|·|ㆍ|○|\*|[①-⑳]|[가-하]\.|[가-하]\)|\(?\d{1,2}\)|\d{1,2}\.)")


def unwrap(text: str) -> str:
    """PDF 줄바꿈(<br>) 이어붙이기. 글머리표(※, -, ①, 가., 1) …) 앞에서만 줄을 유지한다."""
    out: list[str] = []
    for ln in (text or "").split("\n"):
        ln = ln.strip()
        if not ln:
            continue
        if out and not BULLET_RE.match(ln) and not out[-1].startswith("|"):
            joiner = "" if re.search(r"[(\-/]$", out[-1]) else " "
            out[-1] = out[-1] + joiner + ln
        else:
            out.append(ln)
    return "\n".join(out)


def _extract(target: str) -> tuple[str, str, str]:
    lines = [x.strip(" -※") for x in re.split(r"\n|(?=※)", target) if x.strip()]
    target = FOOTNOTE_RE.sub("", target)
    prior = [x for x in lines if any(k in x for k in PRIOR_KEYS)]
    bio = list(dict.fromkeys(m.group(0).strip() for m in BIOMARKER_RE.finditer(target)))
    stage = list(dict.fromkeys(m.group(0).strip() for m in STAGE_RE.finditer(target)))
    return " / ".join(prior), "; ".join(bio), "; ".join(stage)


def _history(rows_text: list[str]) -> tuple[str, str, str]:
    hist = " ".join(x.replace("\n", " ") for x in rows_text).strip()
    best = ("", "")
    for m in NOTICE_RE.finditer(hist):
        d = f"{int(m.group(2)):04d}-{int(m.group(3)):02d}-{int(m.group(4)):02d}"
        if d >= best[1]:
            best = (re.sub(r"\s+", "", m.group(1)), d)
    return hist, best[0], best[1]


# ── 본문 탐색 ─────────────────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    return re.sub(r"[\s#/·\t'’`]+", "", s or "")


def toc_cancers(md: str) -> list[tuple[str, str]]:
    """목차 줄 '1. 소세포폐암 13 2. 비소세포폐암 15 …' → [('1','소세포폐암'), …]."""
    for line in md.split("\n")[:400]:
        if line.startswith("<") or not re.search(r"\d+\.\s*소세포폐암", line):
            continue
        items = re.findall(r"(\d{1,2}(?:-\d)?)\.\s*(.+?)\s+\d{1,3}(?=\s+\d{1,2}(?:-\d)?\.|\s*$)", line)
        if len(items) >= 20:
            return [(c, n.strip()) for c, n in items]
    raise ValueError("목차(주요 암종별 항암요법)를 찾지 못함")


def locate_cancers(lines: list[str], toc: list[tuple[str, str]], start: int) -> list[tuple[int, str, str, str]]:
    """각 암종 본문 시작 줄. → [(line_idx, code, name_kr, name_en)]. 목차 순서대로 앞에서부터 찾는다."""
    found = []
    pos = start
    for code, name in toc:
        key_main = _norm(name.split("(")[0])
        hit = None
        for i in range(pos, len(lines)):
            s = lines[i].strip()
            if not s or s.startswith("<") or len(s) > 140:
                continue
            ns = _norm(s)
            m = re.match(r"^#*\s*" + re.escape(code) + r"\s*\t\s*(.+)$", s)
            if m and _norm(m.group(1)).startswith(key_main[:4]):
                hit = (i, m.group(1))
                break
            if ns == key_main or (ns.startswith(key_main) and "(" in s and len(ns) <= len(key_main) + 60):
                hit = (i, s)
                break
        if hit is None:
            found.append((-1, code, name, ""))
            continue
        i, text = hit
        en = ""
        m = re.search(r"\(([A-Za-z][^)]*)\)", text)
        if m:
            en = m.group(1)
        else:  # 다음 몇 줄에 '(Ovarian Cancer / …)'
            for j in range(i + 1, min(i + 4, len(lines))):
                m = re.match(r"^\s*\(([A-Za-z][^)]*)\)\s*$", lines[j])
                if m:
                    en = m.group(1)
                    break
        found.append((i, code, name, en.strip()))
        pos = i + 1
    return found


def _collect_table(lines: list[str], i: int) -> tuple[str, int]:
    """lines[i] 가 '<table' 로 시작 → (표 HTML, 다음 줄 인덱스)."""
    buf, depth, j = [], 0, i
    while j < len(lines):
        s = lines[j]
        buf.append(s)
        depth += len(re.findall(r"<table", s)) - len(re.findall(r"</table>", s))
        j += 1
        if depth <= 0:
            break
    return "\n".join(buf), j


HEAD_ALIASES = {
    "연번": "no", "항암요법": "regimen", "투여대상": "target", "대상": "group", "투여단계": "line",
    "투여요법": "setting", "대상질환": "target", "주요암종": "cancer", "항암제병용요법": "regimen",
    "허가사항": "label", "급여요법": "base_regimen", "급여기준": "target", "비고": "remark",
}


def _header_roles(cells: list[str]) -> list[str]:
    roles = []
    for c in cells:
        k = re.sub(r"\s+", "", c)
        roles.append(HEAD_ALIASES.get(k, ""))
    return roles


def parse(md: str) -> tuple[list[Regimen], dict]:
    """Markdown 전문 → (요법 레코드, 통계/경고)."""
    CONT.clear()
    lines = md.split("\n")
    toc = toc_cancers(md)
    # 본문 시작: 목차 다음 첫 '1\t소세포폐암'
    start = next(i for i, s in enumerate(lines) if re.match(r"^#*\s*1\s*\t\s*소세포폐암", s.strip()))
    end = next((i for i, s in enumerate(lines) if i > start and re.match(r"^#\s*항구토제", s.strip())), len(lines))
    heads = locate_cancers(lines, toc, start)
    missing = [c for i, c, *_ in heads if i < 0]
    heads = [h for h in heads if h[0] >= 0]
    out: list[Regimen] = []
    stats = {"toc_cancers": len(toc), "located_cancers": len(heads), "missing_cancers": missing,
             "tables": 0, "regimen_tables": 0, "flags": {}}
    last_header: Optional[list[str]] = None
    for hi, (li, code, name, en) in enumerate(heads):
        stop = heads[hi + 1][0] if hi + 1 < len(heads) else end
        first_rec = len(out)
        setting_raw, sub_raw, line_raw, line_src = "", "", "", "none"
        group_head = ""
        table_idx = 0
        i = li + 1
        while i < stop:
            s = lines[i].strip()
            if s.startswith("<table") or (s.startswith("|") and i + 1 < stop and re.match(r"^\|\s*-{3}", lines[i + 1].strip())):
                if s.startswith("<table"):
                    html_t, nxt = _collect_table(lines, i)
                    grid, is_head = table_grid(html_t)
                else:
                    grid, is_head, nxt = pipe_grid(lines, i)
                i = nxt
                stats["tables"] += 1
                if not grid:
                    continue
                hdr = grid[0] if is_head[0] else None
                if hdr is not None and re.fullmatch(r"\d+", (hdr[0] or "").strip()):
                    hdr = None          # 페이지 넘김 표: 첫 데이터 행이 <th> 로 변환됨(호지킨 4번 등)
                origin = ORIGIN.pop(id(grid), None)
                body = grid[1:] if hdr else grid
                body_origin = (origin[1:] if hdr else origin) if origin else None
                roles = _header_roles(hdr) if hdr else (last_header if last_header and len(last_header) == len(grid[0]) else None)
                if not roles or "no" not in roles or "regimen" not in roles:
                    continue
                last_header = roles
                table_idx += 1
                stats["regimen_tables"] += 1
                out.extend(_rows_from_table(body, roles, code, name, en, setting_raw, sub_raw or group_head,
                                            line_raw, line_src, table_idx, stats, body_origin))
                continue
            i += 1
            if not s or s.startswith("<") or s.startswith("!["):
                continue
            t = re.sub(r"^#+\s*", "", s)
            t = re.sub(r"^-\s*", "", t)
            # 투여단계 소제목
            m = re.match(r"^[가-하]\.\s*투여\s*단계\s*[:：]\s*(.+)$", t)
            if m and len(t) < 60:
                line_raw, line_src = m.group(1).strip(), "heading"
                continue
            # [2군 항암제를 포함한 요법] 등 묶음 제목
            if re.match(r"^\[[^\]]{2,40}\]$", t):
                group_head = t.strip("[]")
                continue
            # 투여요법(setting) 제목: '1. 선행화학요법(neoadjuvant)' / '1. 4. 고식적요법(palliative)'
            m = re.match(r"^(?:\d\.\s*)*(\d)\.\s*(.+)$", t)
            if m and len(t) < 70 and SETTING_HEAD_HINT.search(m.group(2)) and not re.search(r"(함|음|됨|임)\s*[.)]?$", m.group(2)) \
                    and "위에서 언급한" not in t and "사용 시" not in t \
                    and "‘" not in t and "'" not in t:
                setting_raw, sub_raw, line_raw, line_src, group_head = m.group(2).strip(), "", "", "none", ""
                continue
            # 소분류: '가. HER2 양성 유방암의 선행화학요법'
            m = re.match(r"^[가-하]\.\s*(.+)$", t)
            if m and len(t) < 60 and "투여단계" not in t and not re.search(r"(함|음|됨|임|우)\s*[.)]?$", t):
                sub_raw = m.group(1).strip()
                continue
        _attach_footnotes(out[first_rec:], lines[li:stop])
    stats["numbering_gaps"] = numbering_gaps(out)
    return out, stats


def numbering_gaps(recs: list) -> dict:
    """암종별 연번이 1..N 으로 이어지는지 — 빠진 번호는 병합 셀·페이지 넘김으로 행을 놓쳤을 가능성(사람 확인)."""
    nums: dict[str, set[int]] = {}
    for r in recs:
        for n in re.findall(r"\d+", r.regimen_no or ""):
            nums.setdefault(r.cancer_code, set()).add(int(n))
    out = {}
    for code, ns in nums.items():
        miss = [n for n in range(1, max(ns) + 1) if n not in ns]
        if miss:
            out[code] = miss
    return out


FOOT_DEF_RE = re.compile(r"^\s*주\s*(\d{1,2})\s*[.)]\s*(\S.*)$")


def _attach_footnotes(recs: list, sec_lines: list[str]) -> None:
    """암종 절의 '주N. …' 정의를 그 절 요법의 각주 표시(주N)에 붙인다(extra.footnote_text)."""
    defs: dict[str, str] = {}
    cur = None
    for ln in sec_lines:
        s = ln.strip()
        m = FOOT_DEF_RE.match(s)
        if m:
            cur = f"주{int(m.group(1))}"
            defs[cur] = m.group(2).strip()
        elif cur and s and not s.startswith(("<", "|", "#", "!")) and not re.match(r"^[가-하\d]\.", s):
            defs[cur] += " " + s
        else:
            cur = None
    if not defs:
        return
    for r in recs:
        marks = [x for x in (r.footnotes or "").split(",") if x]
        got = {k: defs[k] for k in marks if k in defs}
        if got:
            r.extra["footnote_text"] = got


def _rows_from_table(body, roles, code, name, en, setting_raw, sub_raw, line_raw, line_src, table_idx, stats,
                     body_origin=None):
    idx = {r: roles.index(r) for r in set(roles) if r}
    groups: list[tuple[str, list[list[str]]]] = []
    orig_of = {id(row): (body_origin[k] if body_origin else None) for k, row in enumerate(body)}
    for row in body:
        nums = re.findall(r"\d+", row[idx["no"]]) if "no" in idx else []
        no = "-".join(nums)
        if not no:
            continue
        if groups and groups[-1][0] == no:
            groups[-1][1].append(row)
        else:
            groups.append((no, [row]))
    known_drugs = {w for row in body for w in re.findall(r"[a-z][a-z\-]{3,}", (row[idx["regimen"]] if "regimen" in idx else "").lower())}
    recs = []
    for gi, (no, rows) in enumerate(groups):
        next_no = groups[gi + 1][0] if gi + 1 < len(groups) else None
        hist_rows, var_rows = [], []
        for r in rows:
            reg = r[idx["regimen"]].strip()
            tgt = r[idx["target"]].strip() if "target" in idx else ""
            org = orig_of.get(id(r))
            if (var_rows and reg and org and org[idx["regimen"]] and not HISTORY_RE.match(reg)
                    and not re.search(r"[A-Za-z]", reg) and len(reg) < 80
                    and not re.search(r"[.)]\s*$", var_rows[-1][idx["regimen"]].strip())):
                # 페이지·줄 넘김으로 쪼개진 요법 칸 꼬리('※ … 동 요법' + '투여시 … 인정함') — 앞 요법에 잇는다
                nr = list(var_rows[-1])
                nr[idx["regimen"]] = nr[idx["regimen"]].rstrip() + " " + reg
                var_rows[-1] = nr
                continue
            if HISTORY_RE.match(reg) and org and "base_regimen" in idx and org[idx["base_regimen"]] \
                    and r[idx["base_regimen"]].strip() and var_rows:
                # 40번 표: 연혁 칸과 같은 줄에 새 '기존 요법'(예: tamoxifen) — 앞 요법을 이어받는 별도 행
                hist_rows.append(reg)
                nr = list(var_rows[-1])
                for role in ("base_regimen", "remark", "target", "line", "setting"):
                    if role in idx and org[idx[role]]:
                        nr[idx[role]] = r[idx[role]]
                var_rows.append(nr)
                orig_of[id(nr)] = None
                continue
            if HISTORY_RE.match(reg) and org and "target" in idx and org[idx["target"]] and tgt and var_rows \
                    and not HISTORY_RE.match(tgt):
                # 연혁 행에 끼어 든 투여대상 칸 — 페이지 넘김으로 쪼개진 셀
                hist_rows.append(reg)
                prev = var_rows[-1]
                if re.match(r"^\s*(?:[가-하]\.|[①-⑳]|\d\))", tgt):      # 별도 투여대상(VRd '나.')
                    nr = list(prev)
                    for role in ("target", "line", "setting"):
                        if role in idx and org[idx[role]]:
                            nr[idx[role]] = r[idx[role]]
                    nr[idx["target"]] = tgt
                    var_rows.append(nr)
                    orig_of[id(nr)] = None
                else:                                                    # 이어지는 문장(VTD '4주기)…')
                    nr = list(prev)
                    nr[idx["target"]] = (prev[idx["target"]].rstrip() + " " + tgt).strip()
                    var_rows[-1] = nr
                    CONT[tgt] = nr[idx["target"]]
                continue
            if HISTORY_RE.match(reg):
                hist_rows.append(reg)
            elif HISTORY_RE.match(tgt) and var_rows and reg == var_rows[-1][idx["regimen"]].strip():
                hist_rows.append(tgt)          # 39번 표: 연혁이 '대상질환' 칸에 있는 경우
            else:
                var_rows.append(r)
        # 요법 칸 안에 섞인 공고번호 '(제2011-106호: 2011.10.1.)' 도 연혁으로
        for r in var_rows:
            hist_rows += INLINE_NOTICE_RE.findall(r[idx["regimen"]])
        hist, last_no, last_dt = _history(hist_rows)
        # 40번 표: 급여기준 칸이 앞 연번(예: 1번)의 칸을 위아래로 병합해 이어진 경우 — 이 연번의 것은 공통 비고(주석)뿐
        tgt_origin = [orig_of.get(id(x)) for x in rows]
        tgt_copied = bool(CELL_FIXES and "cancer" in idx and "target" in idx and tgt_origin
                          and all(o is not None for o in tgt_origin) and not any(o[idx["target"]] for o in tgt_origin))
        # 병합된 연번 칸('24 25'): 서로 다른 요법을 순서대로 번호에 배정
        nums = no.split("-")
        distinct_regs = list(dict.fromkeys(r[idx["regimen"]] for r in var_rows))
        no_of = {}
        merged_flag = False
        if len(nums) > 1:
            if len(distinct_regs) == len(nums):
                no_of = dict(zip(distinct_regs, nums))
            else:
                merged_flag = True
        multi_flag = len(nums) == 1 and len(distinct_regs) > 1
        # 변환 중 위아래 병합된 연번 칸('26' 하나가 26~29 네 요법을 덮음): 다음 연번이 정확히 요법 수만큼 뒤에 있으면 순서대로 번호 복원
        if CELL_FIXES and multi_flag and next_no and re.fullmatch(r"\d+", nums[0]) and re.fullmatch(r"\d+", next_no) \
                and int(next_no) - int(nums[0]) == len(distinct_regs):
            no_of = {reg: str(int(nums[0]) + k) for k, reg in enumerate(distinct_regs)}
            multi_flag = False
            renumbered = True
        else:
            renumbered = False
        seen = set()
        variant = 0
        for r in var_rows:
            reg_raw = r[idx["regimen"]]
            raw_t = r[idx["target"]] if "target" in idx else ""
            if raw_t.strip() in CONT:                       # 위암 6번: 쪼개진 셀의 뒷부분만 복제된 경우
                raw_t = CONT[raw_t.strip()]
            if tgt_copied and raw_t:
                raw_t = _shared_notes(raw_t)
            target = unwrap(raw_t) if "target" in idx else ""
            # 39번 표: 요법 칸에 '∙ 투여대상: … ∙ 투여단계: …' 가 함께 들어 있는 경우 분리
            m_in = re.search(r"[∙·]?\s*투여\s*대상\s*[:：]", reg_raw)
            if m_in:
                inline = reg_raw[m_in.start():]
                reg_raw = reg_raw[:m_in.start()].strip()
                mt = re.search(r"투여\s*대상\s*[:：]\s*(.+?)(?=[∙·]\s*투여\s*단계|$)", inline, re.S)
                ml = re.search(r"투여\s*단계\s*[:：]\s*([^\n∙·]+)", inline)
                target = (target + "\n" if target else "") + unwrap(mt.group(1)) if mt else target
                inline_line = ml.group(1).strip() if (ml and "line" not in idx) else ""
            else:
                inline_line = ""
            col_line = r[idx["line"]].strip() if "line" in idx else ""
            col_set = r[idx["setting"]].strip() if "setting" in idx else ""
            grp = r[idx["group"]].strip() if "group" in idx else ""
            sig = (reg_raw, target, col_line, col_set, grp,
                   *(r[idx[x]].strip() for x in ("base_regimen", "remark", "label") if x in idx))
            if sig in seen:
                continue
            seen.add(sig)
            variant += 1
            reg, notes, self_pay = _clean_regimen(reg_raw)
            flags = (["merged_no"] if merged_flag else []) + (["multi_regimen_one_no"] if multi_flag else []) \
                + (["renumbered_by_sequence"] if renumbered else [])
            row_no = no_of.get(reg_raw, no)
            if col_line:
                lraw, lsrc = col_line, "column"
            elif inline_line:
                lraw, lsrc = inline_line, "column"
            elif line_raw:
                lraw, lsrc = line_raw, line_src
            else:
                ft = line_from_text(target)
                lraw, lsrc = (target if ft else ""), ("target_text" if ft else "none")
            lcode = line_code(lraw) if lsrc != "target_text" else line_from_text(target)
            sraw = col_set if col_set and col_set not in ("-", "－") else (setting_raw or col_set)
            scode = setting_code(sraw)
            if scode == "NA":
                st = setting_from_text(target)
                if st:
                    scode, sraw = st, "(투여대상 괄호 표기)"
            if scode in ("NA", "newly_diagnosed") and re.search(r"이식\s*(?:종료\s*)?후", target) and "반응" in target:
                scode, sraw = "maintenance", "(이식 후 반응 환자 — 유지)"
            if scode == "NA":
                flags.append("setting_na")
            prior, bio, stage = _extract(target)
            extra = {}
            for role in ("label", "base_regimen", "remark", "group", "cancer"):
                if role in idx and r[idx[role]].strip():
                    extra[role] = r[idx[role]].strip()
            if "base_regimen" in extra and "항암요법" in extra["base_regimen"] and "연번" in extra["base_regimen"]:
                extra["base_regimen"] = _flat_base_table(extra["base_regimen"])     # 중첩 표 '연번|항암요법' → 한 줄
            if "label" in extra:              # 병합 셀에 두 약의 허가 문구가 함께 → 이 행 약의 문단만
                extra["label"] = _label_for(extra["label"], reg, known_drugs)
            if not hist:
                flags.append("no_history")
            cc, cn = code, name
            if "cancer" in idx:  # 40. 기타 항암제 병용요법 — 행마다 주요 암종
                extra["row_cancer"] = re.sub(r"\s+", "", r[idx["cancer"]])
            rec = Regimen(cc, cn, en, scode, sraw, sub_raw, lcode or "NA", lraw.replace("\n", " "),
                          lsrc, table_idx, row_no, variant, reg, split_drugs(reg), target, prior, bio, stage, self_pay,
                          notes, hist, last_no, last_dt, extra, flags)
            for f in flags:
                stats["flags"][f] = stats["flags"].get(f, 0) + 1
            recs.append(rec)
    if CELL_FIXES:
        _join_split_labels(recs)
    return recs


CELL_FIXES = True      # 40번 표 병합 칸 보정(테스트에서 끄고 비교할 수 있게)
_NOTE_START = re.compile(r"^\s*(?:[·•∙※]|platinum)", re.I)


def _flat_base_table(text: str) -> str:
    """'연번 | 항암요법 | 1 | paclitaxel +platinum | 3 | gemcitabine +platinum' → 'paclitaxel + platinum (연번 1), gemcitabine + platinum (연번 3)'"""
    toks = [t.strip() for t in re.split(r"\||\n", text) if t.strip()]
    toks = [t for t in toks if t not in ("연번", "항암요법")]
    out = []
    for i in range(0, len(toks) - 1, 2):
        if re.fullmatch(r"\d+", toks[i]):
            out.append(f"{re.sub(r'\s*\+\s*', ' + ', toks[i + 1])} (연번 {toks[i]})")
    return ", ".join(out) if out else text


def _shared_notes(text: str) -> str:
    """위 연번에서 병합돼 내려온 급여기준 칸 → 이 연번에 해당하는 공통 비고(주석)만.
    앞부분(그 칸의 원래 연번의 투여대상·'(1차, 고식적요법)')은 버린다. 비고 줄을 못 찾으면 그대로 둔다."""
    lines = text.split("\n")
    k = next((i for i, ln in enumerate(lines) if _NOTE_START.match(ln)), None)
    if k is None:
        k = next((i + 1 for i, ln in enumerate(lines[:-1]) if re.search(r"(?:요법|치료)\)\s*$", ln)), None)
    return "\n".join(lines[k:]).strip() if k else text


def _join_split_labels(recs: list) -> None:
    """페이지·칸 경계에서 한 문장이 두 연번의 허가사항 칸으로 갈라진 경우(자궁내막암 #38/#39) 이어 붙인다.
    앞 연번 칸이 열린 괄호로 끝나고 다음 연번 칸이 닫는 괄호 조각으로 시작할 때만."""
    for prev, cur in zip(recs, recs[1:]):
        a, b = prev.extra.get("label", ""), cur.extra.get("label", "")
        if not a or not b or prev.regimen_no == cur.regimen_no:
            continue
        head = b.split(")")[0] if ")" in b else ""
        if a.count("(") > a.count(")") and ")" in b and "(" not in head:
            joined = (a.rstrip() + " " + b.lstrip()).strip()
            prev.extra["label"] = joined
            cur.extra["label"] = joined
            cut = re.search(r"또는\s*불일치\s*복구\s*결함이\s*없는", joined)
            if cut:     # 바이오마커 판정은 연번별 부분만(앞=dMMR 단독 유지, 뒤=pMMR 올라파립 병용)
                prev.extra["marker_text"] = joined[:cut.start()]
                cur.extra["marker_text"] = joined[cut.start():]
            for r in (prev, cur):
                if "label_joined" not in r.parse_flags:
                    r.parse_flags.append("label_joined")


def _label_for(label: str, regimen: str, known: set[str]) -> str:
    """'belantamab …\n…\ndaratumumab(SC)\n…' 처럼 여러 약의 허가 문구가 한 칸에 있으면 regimen 의 약 문단만."""
    lines = label.split("\n")
    segs: list[tuple[str, list[str]]] = []
    for ln in lines:
        m = re.match(r"^\s*([a-z][a-z\-]{3,})\b", ln.strip().lower())
        if m and m.group(1) in known and len(ln.strip()) <= 40:
            segs.append((m.group(1), [ln]))
        elif segs:
            segs[-1][1].append(ln)
        else:
            segs.append(("", [ln]))
    named = [x for x in segs if x[0]]
    if len(named) < 2:
        return label
    reg = regimen.lower()
    pick = [x for x in named if x[0] in reg]
    if len(pick) != 1:
        return label
    return "\n".join(pick[0][1]).strip()


def to_rows(recs: list[Regimen]) -> list[dict]:
    rows = []
    for r in recs:
        d = asdict(r)
        d["regimen_key"] = r.key
        d["drugs_json"] = json.dumps(r.drugs, ensure_ascii=False)
        d["extra_json"] = json.dumps(r.extra, ensure_ascii=False)
        d["parse_flags"] = ",".join(r.parse_flags)
        rows.append(d)
    return rows
