"""「요양급여의 적용기준 및 방법에 관한 세부사항(약제)」 책자 파서.

입력: kordoc 청크 Markdown(50쪽 단위, 순서대로 이어붙임). 본문은 2열 표 "구 분 | 세부인정기준 및 방법".
- 최상위 표의 행만 본다(셀 안 중첩 표는 텍스트로 평탄화).
- 왼쪽 셀이 비어 있으면 앞 항목의 이어짐(페이지 경계로 쪼개진 항목 이어붙이기).
- 왼쪽 셀에 '[분류]' 제목이 2개 이상 들어 있으면(짧은 항목이 한 행으로 합쳐짐) 오른쪽 본문을 고시 표식
  '(고시 제…호, ’yy.m.d.)' 기준으로 나눠 배정한다. 나눌 수 없으면 parse_flags 에 merged_split_uncertain.
- 항목의 최신 고시 = 본문 안 고시 표식 중 시행일이 가장 늦은 것(동일하면 마지막).

색인: pdftotext 로 '분류번호순 색인' 페이지를 읽어 (분류, 제목, 고시번호, 시행일, 쪽) 목록 → 파싱 결과와 대조.
"""
from __future__ import annotations

import html as _html
import re
import subprocess
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

GOSI_RE = re.compile(
    r"\(\s*고시\s*제?\s*(\d{4})\s*-\s*(\d+)\s*호\s*[,，]\s*[’'‘ʼ`´]?\s*(\d{2})\s*\.\s*(\d{1,2})\s*\.\s*(\d{1,2})\s*\.?\s*\)")
TITLE_MARK_RE = re.compile(r"\[\s*(일반원칙|\d{3}(?:\s*[,·]\s*\d{3})*)\s*\]")
FORM_WORDS = ("경구제", "주사제", "외용제", "흡입제", "흡입액", "점안제", "점안액", "패취제", "좌제", "시럽제", "내복액",
              "서방경구제", "복합경구제", "복합제", "제제", "액제", "연고", "크림", "겔", "흡입분말", "비강분무제", "경피제")


@dataclass
class Item:
    class_no: str
    title: str
    ingredient: str
    dosage_form: str
    brand_text: str
    body_parts: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    chunk: str = ""

    @property
    def criteria_text(self) -> str:
        return "\n".join(p.strip("\n") for p in self.body_parts if p.strip()).strip()


# ── 고시 표식 ────────────────────────────────────────────────────────────────

def parse_gosi(text: str) -> list[tuple[str, str]]:
    """본문의 고시 표식 → [(‘고시 제2026-69호’, ‘2026-04-01’)]. 2자리 연도는 고시번호 연도와 같은 세기."""
    out = []
    for m in GOSI_RE.finditer(text or ""):
        y4, no, yy, mm, dd = m.groups()
        century = int(y4) // 100 * 100
        year = century + int(yy)
        if year > int(y4) + 2:          # 세기 경계 보정(거의 없음)
            year -= 100
        out.append((f"고시 제{y4}-{int(no)}호", f"{year:04d}-{int(mm):02d}-{int(dd):02d}"))
    return out


def latest_gosi(text: str) -> tuple[Optional[str], Optional[str]]:
    g = parse_gosi(text)
    if not g:
        return None, None
    best = max(enumerate(g), key=lambda t: (t[1][1], t[0]))[1]
    return best


# ── HTML(셀) → 텍스트 ──────────────────────────────────────────────────────────

def cell_to_text(h: str) -> str:
    """셀 HTML → 텍스트. 중첩 표는 '| a | b |' 줄로."""
    h = re.sub(r"</t[hd]>\s*<t[hd][^>]*>", " | ", h)
    h = re.sub(r"<tr[^>]*>\s*<t[hd][^>]*>", "\n| ", h)
    h = re.sub(r"</t[hd]>\s*</tr>", " |", h)
    h = re.sub(r"<br\s*/?>", "\n", h)
    h = re.sub(r"</?table[^>]*>", "\n", h)
    h = re.sub(r"<[^>]+>", "", h)
    h = _html.unescape(h).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in h.split("\n")]
    return "\n".join(ln for ln in lines if ln)


_TOK = re.compile(r"<(/?)(table|tr|td|th)\b[^>]*>", re.I)
_PIPE_SEP = re.compile(r"^\|\s*:?-{3,}")


def pipe_tables_to_html(md: str) -> str:
    """kordoc 은 병합·중첩이 없는 표를 Markdown 파이프 표로 낸다 → 같은 파서를 쓰도록 HTML 표로 바꾼다.
    HTML 표 내부(depth>0)의 줄은 건드리지 않는다."""
    out, block, depth = [], [], 0

    def flush():
        if not block:
            return
        rows = [ln for ln in block if not _PIPE_SEP.match(ln.strip())]
        has_hdr = len(block) > 1 and _PIPE_SEP.match(block[1].strip())
        html_rows = []
        for i, ln in enumerate(rows):
            s = ln.strip()
            s = s[1:] if s.startswith("|") else s
            s = s[:-1] if s.endswith("|") and not s.endswith("\\|") else s
            cells = [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", s)]
            tag = "th" if (i == 0 and has_hdr) else "td"
            html_rows.append("<tr>" + "".join(f"<{tag}>{c}</{tag}>" for c in cells) + "</tr>")
        out.append("<table>\n" + "\n".join(html_rows) + "\n</table>")
        block.clear()

    for ln in md.split("\n"):
        if depth == 0 and ln.lstrip().startswith("|"):
            block.append(ln)
            continue
        flush()
        out.append(ln)
        depth += len(re.findall(r"<table\b", ln, re.I)) - len(re.findall(r"</table>", ln, re.I))
        depth = max(depth, 0)
    flush()
    return "\n".join(out)


def top_level_rows(md: str) -> Iterable[tuple[list[str], int]]:
    """Markdown 안 최상위 표의 행 → ([셀 HTML…], 문서 내 위치). 헤더(th) 행도 포함(셀 앞에 '<th>' 표시)."""
    depth = 0
    row_cells: list[str] | None = None
    cell_start = None
    cell_is_th = False
    row_pos = 0
    for m in _TOK.finditer(md):
        closing, tag = m.group(1) == "/", m.group(2).lower()
        if tag == "table":
            if not closing:
                depth += 1
            else:
                depth = max(0, depth - 1)
            continue
        if depth != 1:
            continue
        if tag == "tr" and not closing:
            row_cells, row_pos = [], m.start()
        elif tag == "tr" and closing:
            if row_cells is not None:
                yield row_cells, row_pos
            row_cells = None
        elif tag in ("td", "th") and not closing:
            cell_start, cell_is_th = m.end(), tag == "th"
        elif tag in ("td", "th") and closing and row_cells is not None and cell_start is not None:
            raw = md[cell_start:m.start()]
            row_cells.append(("<th>" if cell_is_th else "") + raw)
            cell_start = None


# ── 제목 ──────────────────────────────────────────────────────────────────────

def _join_title(s: str) -> str:
    s = re.sub(r"\s*\n\s*", " ", s.strip())
    s = re.sub(r"\(\s+", "(", s)
    s = re.sub(r"\s+\)", ")", s)
    return re.sub(r"\s{2,}", " ", s).strip()


def split_titles(left: str) -> list[tuple[str, str]]:
    """왼쪽 셀 텍스트 → [(class_no, title)]."""
    marks = list(TITLE_MARK_RE.finditer(left))
    if not marks:
        return []
    out = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(left)
        title = _join_title(left[m.end():end])
        cls = re.sub(r"\s+", "", m.group(1))
        if title:
            out.append((cls, title))
    return out


def parse_title(title: str) -> tuple[str, str, str]:
    """'Ruxolitinib phosphate 경구제(품명: 자카비정5밀리그램 등)' → (ingredient, form, brand)."""
    brand = ""
    m = re.search(r"\(\s*품\s*명\s*[:：]?\s*(.+)\)\s*$", title)
    head = title
    if m:
        brand = m.group(1).strip()
        head = title[:m.start()].strip()
    form = ""
    for w in sorted(FORM_WORDS, key=len, reverse=True):
        mm = re.search(rf"\s*{re.escape(w)}\b", head)
        if mm:
            form = w
            ing = head[:mm.start()].strip()
            break
    else:
        ing = head
    return ing.strip(" ,"), form, brand


def norm_key(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = re.sub(r"\(\s*품\s*명.*$", "", s)
    s = re.sub(r"[^0-9a-z가-힣α-ω]+", "", s)
    return s


# ── 본문 → 항목 ────────────────────────────────────────────────────────────────

def _split_by_gosi(text: str, k: int) -> list[str] | None:
    """text 를 고시 표식 기준으로 k 조각으로 (앞 k-1 조각은 각자 고시 표식으로 끝남)."""
    ends = [m.end() for m in GOSI_RE.finditer(text)]
    if len(ends) < k - 1:
        return None
    # 고시 표식이 더 많으면(한 항목 안에 여러 개) — 줄 끝 표식 중 균등하게: 앞에서부터 각 1개씩
    cuts = ends[:k - 1]
    parts, prev = [], 0
    for c in cuts:
        parts.append(text[prev:c])
        prev = c
    parts.append(text[prev:])
    return parts


def parse_items(md: str, chunk_label: str = "") -> tuple[list[Item], dict]:
    """최상위 표 행 → 항목.
    - 왼쪽 셀이 '[분류]' 로 시작하면 새 항목(표 헤더와 무관).
    - 본문 표(헤더 '구 분 | …인정기준…')의 왼쪽 빈 행 = 앞 항목 이어짐.
    - 본문 표가 아닌 최상위 표(kordoc 이 셀 안 표를 바깥으로 꺼낸 경우)는 앞 항목 본문에 표 줄로 붙인다.
    """
    items: list[Item] = []
    stats = {"rows": 0, "orphan_rows": 0, "merged_rows": 0, "appended_side_rows": 0, "colspan_rows": 0}
    cur: Item | None = None
    in_body_table = False
    md = pipe_tables_to_html(md)
    for cells, _pos in top_level_rows(md):
        if cells and cells[0].startswith("<th>"):
            first = cell_to_text(cells[0][4:]).replace(" ", "")
            hdr = cell_to_text(" ".join(c[4:] for c in cells)).replace(" ", "")
            # kordoc 이 헤더 글자를 빠뜨리는 경우가 있다('부인정기준 및 방법') → '구분' 또는 '인정기준' 이면 본문 표
            in_body_table = first == "구분" or "인정기준" in hdr
            if not in_body_table and cur is not None:
                cur.body_parts.append("| " + " | ".join(cell_to_text(c[4:]) for c in cells) + " |")
                stats["appended_side_rows"] += 1
            continue
        if not cells:
            continue
        stats["rows"] += 1
        left = cell_to_text(cells[0])
        right = cell_to_text(" ".join(cells[1:])) if len(cells) > 1 else ""
        starts_item = bool(TITLE_MARK_RE.match(left.strip()))
        if starts_item and len(cells) == 1:
            # 왼쪽·오른쪽이 한 셀로 합쳐진 행(colspan) — 제목/본문 경계가 섞임
            stats["colspan_rows"] += 1
            m = TITLE_MARK_RE.match(left.strip())
            rest = left.strip()[m.end():]
            mm = re.search(r"(\d\.\s*)?허가사항|식품의약품안전처장이", rest)
            title = _join_title(rest[:mm.start()] if mm else rest.split("\n")[0])
            if not title:  # 제목이 본문 줄 사이에 끼어 있는 경우 — 첫 영문 성분명(+함량) 추출
                tm = re.search(r"([A-Z][a-z]+(?: [a-zα-ωβ]+)*(?: \d+(?:\.\d+)?\s?mg)?)", rest)
                title = tm.group(1) if tm else ""
            ing, form, brand = parse_title(title)
            cur = Item(class_no=re.sub(r"\s+", "", m.group(1)), title=title, ingredient=ing, dosage_form=form,
                       brand_text=brand, body_parts=[rest], flags=["colspan_garbled"], chunk=chunk_label)
            items.append(cur)
            in_body_table = True
            continue
        if not starts_item:
            if cur is None:
                stats["orphan_rows"] += 1
                continue
            if in_body_table:
                if left.strip():
                    cur.flags.append("left_text_no_mark")
                    right = left + "\n" + right
                cur.body_parts.append(right)
            else:
                cur.body_parts.append("| " + " | ".join(cell_to_text(c) for c in cells) + " |")
                stats["appended_side_rows"] += 1
            continue
        in_body_table = True
        titles = split_titles(left)
        if len(titles) > 1:
            stats["merged_rows"] += 1
            parts = _split_by_gosi(right, len(titles))
        else:
            parts = [right]
        flags_extra = []
        if parts is None:
            parts = [right] + [""] * (len(titles) - 1)
            flags_extra = ["merged_split_uncertain"]
        for (cls, title), body in zip(titles, parts):
            ing, form, brand = parse_title(title)
            cur = Item(class_no=cls, title=title, ingredient=ing, dosage_form=form, brand_text=brand,
                       body_parts=[body], flags=list(flags_extra), chunk=chunk_label)
            items.append(cur)
    return items, stats


_HERBAL_H = re.compile(r"^#\s*한\s*약\s*제\s*제\s*$", re.M)
_GUIDE_H = re.compile(r"^#\s*심\s*사\s*지\s*침\s*$", re.M)


def body_slice(md_all: str) -> str:
    """약제 본문: 첫 '세부인정기준 및 방법' 표부터 '한약제제'(없으면 '심사지침') 장 제목 전까지."""
    start = md_all.find("세부인정기준 및 방법")
    start = md_all.rfind("<table", 0, start) if start > 0 else 0
    rest = md_all[start:]
    ends = [m.start() for m in (_HERBAL_H.search(rest), _GUIDE_H.search(rest)) if m]
    return rest[:min(ends)] if ends else rest


def herbal_slice(md_all: str) -> str:
    start = md_all.find("세부인정기준 및 방법")
    rest = md_all[max(start, 0):]
    h, g = _HERBAL_H.search(rest), _GUIDE_H.search(rest)
    if not h:
        return ""
    return rest[h.start():(g.start() if g and g.start() > h.start() else len(rest))]


def guidance_slice(md_all: str) -> str:
    start = md_all.find("세부인정기준 및 방법")
    m = _GUIDE_H.search(md_all, max(start, 0))
    return md_all[m.start():] if m else ""


def parse_herbal(md: str) -> list[Item]:
    """한약제제: '항목 | 제목 | 세부인정사항' 행 = 항목, '처방명 | 적응증' 표 = 1개 항목(기준처방별 적응증)."""
    items: list[Item] = []
    rx = []
    md = pipe_tables_to_html(md)
    for cells, _ in top_level_rows(md):
        if not cells or cells[0].startswith("<th>"):
            continue
        texts = [cell_to_text(c) for c in cells]
        if len(texts) >= 3 or (len(texts) == 2 and items and not rx and not re.match(r"\d+\.", texts[0])):
            title, body = (texts[-2], texts[-1]) if len(texts) >= 2 else (texts[0], "")
            items.append(Item(class_no="한약제제", title=_join_title(title), ingredient=_join_title(title),
                              dosage_form="한약제제", brand_text="", body_parts=[body]))
        elif len(texts) == 2 and re.match(r"\d+\.", texts[0]):
            rx.append(f"| {texts[0]} | {texts[1]} |")
    if rx:
        items.append(Item(class_no="한약제제", title=f"처방별 적응증(기준처방 {len(rx)}종)", ingredient="한약 기준처방",
                          dosage_form="한약제제", brand_text="", body_parts=["| 처방명 | 적응증 |", *rx]))
    return items


# ── 색인(분류번호순) ───────────────────────────────────────────────────────────

INDEX_ROW_RE = re.compile(r"고시\s*제\s*(\d{4})-(\d+)\s*호\s+(\d{2})\.(\d{1,2})\.(\d{1,2})\.?\s+(\d{1,4})\s*$")


def _pdftotext(pdf: Path, first: int, last: int) -> str:
    r = subprocess.run(["pdftotext", "-layout", "-f", str(first), "-l", str(last), str(pdf), "-"],
                       capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-300:])
    return r.stdout


def find_index_pages(pdf: Path, scan_to: int = 120) -> tuple[int, int]:
    first = last = None
    for p in range(1, scan_to + 1):
        head = "\n".join(_pdftotext(pdf, p, p).splitlines()[:6])
        if "분류번호순 색인" in head and "성 분 명" in _pdftotext(pdf, p, p):
            first = first or p
        if first and ("성분명순 색인" in head.replace(" ", "") or "성분명순색인" in head.replace(" ", "")):
            last = p - 1
            break
    if not first or not last:
        raise RuntimeError("분류번호순 색인 페이지를 찾지 못함")
    return first, last


def parse_index(pdf: Path) -> list[dict]:
    """분류번호순 색인 → [{section, class_no, title, gosi_no, effective_date, page}] (약제만, 한약제제 제외)."""
    first, last = find_index_pages(pdf)
    text = _pdftotext(pdf, first, last)
    out, cls, pending = [], None, []
    in_drug = True
    for raw in text.splitlines():
        line = raw.rstrip()
        s = line.strip()
        if not s or s.startswith("성 분 명") or "분류번호순 색인" in s or re.fullmatch(r"\d{1,3}", s):
            continue
        if s.startswith("▪"):
            in_drug = "약제" in s and "한약" not in s
            continue
        if not in_drug:
            continue
        m = re.match(r"✤\s*(일반원칙|\d{3})", s)
        if m:
            cls, pending = m.group(1), []
            continue
        rm = INDEX_ROW_RE.search(s)
        if rm:
            y4, no, yy, mm, dd, pg = rm.groups()
            lead = s[:rm.start()].strip()
            title = " ".join(pending + ([lead] if lead else []))
            out.append({"class_no": cls, "title": re.sub(r"\s{2,}", " ", title),
                        "gosi_no": f"고시 제{y4}-{int(no)}호",
                        "effective_date": f"{int(y4)//100*100+int(yy):04d}-{int(mm):02d}-{int(dd):02d}",
                        "page": int(pg), "_tail_ok": True})
            pending = []
        else:
            pending.append(re.sub(r"\s{2,}", " ", s))
            if len(pending) > 4:
                pending = pending[-4:]
    return out
