"""일반약제 기준의 깨진 표 복원 — 고시 원본(HWP)에서 다시 읽는다.

왜: 책자 PDF 의 표 중 세로 글자 머리행(예: 당뇨병용제 '인정 가능 2제 요법')은 변환 중 열 제목이 사라지고,
상세 팝업(HTML)은 '관련표는 첨부파일 참고' 로 표를 빼고 준다. 고시 원본 HWP 는 표 구조가 그대로 남아 있다.

절차 (항목마다)
1) 깨진 표 감지: 파이프 표의 첫 행(머리행)이 비었거나, 열 수가 들쭉날쭉하거나, 본문에 '첨부파일 참고'.
2) 보험인정기준 '고시' 목록에서 같은 제목(match_key)의 가장 최근 고시 → 상세 팝업의 HWP 첨부 → kordoc 변환.
3) HWP 의 '세부인정기준 및 방법' 칸 → 줄바꿈 텍스트 + 파이프 표(병합 셀 펼침, 2단 머리행 합침).
4) 표 밖 본문이 기존 본문과 충분히 같을 때(유사도 ≥ 0.75)만 교체. 아니면 보고서에 '불일치' 로 남긴다.
"""
from __future__ import annotations

import difflib
import html as htmllib
import re
import time
from typing import Callable, Optional

from . import sources
from .drug_latest import SLEEP, collect_drug_gosi, gosi_tuple, match_key
from .http import download
from .kordoc_runner import convert
from .onco_parser import table_grid
from .store import RAW_DIR

ATTACH_HINT = re.compile(r"(관련\s*표|별표|표)\S{0,6}\s*(는|은)?\s*(아래\s*)?첨부\s*파일")


def _pipe_blocks(text: str) -> list[list[str]]:
    blocks, cur = [], []
    for ln in (text or "").split("\n"):
        if ln.strip().startswith("|"):
            cur.append(ln.strip())
        elif cur:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    return blocks


def _cells(row: str) -> list[str]:
    return [c.strip() for c in row.strip().strip("|").split("|")]


def needs_recovery(text: str) -> bool:
    if ATTACH_HINT.search(text or ""):
        return True
    for b in _pipe_blocks(text):
        widths = [len(_cells(r)) for r in b if not re.match(r"^\|\s*:?-{3}", r)]
        if not widths or max(widths) < 3:
            continue
        head = _cells(b[0])
        if all(not c for c in head):                       # 머리행이 통째로 빈 표
            return True
        if len(set(widths)) > 2 and max(widths) >= 4:       # 열 수가 들쭉날쭉
            return True
    return False


# ── HWP(kordoc md) → 텍스트 ───────────────────────────────────────────────────

def _strip(h: str) -> str:
    h = re.sub(r"<br\s*/?>", "\n", h)
    h = re.sub(r"<[^>]+>", "", h)
    return htmllib.unescape(h)


def _table_to_pipe(table_html: str) -> list[str]:
    grid, head = table_grid(table_html)
    if not grid:
        return []
    grid = [[re.sub(r"\s+", " ", c or "").strip() for c in row] for row in grid]
    caption = []
    while len(grid) > 2 and len(set(grid[0])) == 1 and len(grid[0]) > 1:   # 전체 폭 제목 행('출혈의 예방') → 표 제목
        caption.append(grid[0][0])
        grid = grid[1:]
    # 2단 머리행: 둘째 행 칸 절반 이상이 첫 행과 같으면(rowspan 복제) 합친다
    nhead = 1
    if len(grid) > 2:
        same = sum(1 for a, b in zip(grid[0], grid[1]) if a == b)
        if same >= len(grid[0]) / 2:
            grid[0] = [a if a == b else (f"{a} · {b}" if a and b else (a or b)) for a, b in zip(grid[0], grid[1])]
            nhead = 2
    hdr, body = grid[0], grid[nhead:]
    # colspan 으로 같은 머리 글이 이웃 칸에 복제된 열 → 하나로(값이 다르면 '·' 로 잇는다)
    keep: list[list[int]] = []
    for c, h in enumerate(hdr):
        if keep and hdr[keep[-1][0]] == h:
            keep[-1].append(c)
        else:
            keep.append([c])

    def merge(row: list[str]) -> list[str]:
        out = []
        for cols in keep:
            vals = list(dict.fromkeys(row[c] for c in cols if c < len(row) and row[c]))
            out.append(" · ".join(vals))
        return out
    rows = [merge(hdr)] + [merge(r) for r in body]
    # 칸 안 줄바꿈으로 갈라진 낱말 잇기('Thiazoli- dinedione', 'dapagli flozin' — 표 안 다른 칸에 붙은 꼴이 있을 때)
    vocab = {w.lower() for r in rows for c in r for w in re.findall(r"[A-Za-z]{4,}", c)}

    def join(c: str) -> str:
        c = re.sub(r"([A-Za-z])-\s+([a-z])", r"\1\2", c)
        toks, out, k = c.split(" "), [], 0
        while k < len(toks):
            if k + 1 < len(toks) and re.fullmatch(r"[A-Za-z]{3,}", toks[k]) and re.fullmatch(r"[a-z]{3,}", toks[k + 1]) \
                    and (toks[k] + toks[k + 1]).lower() in vocab:
                out.append(toks[k] + toks[k + 1])
                k += 2
            else:
                out.append(toks[k])
                k += 1
        return " ".join(out)
    rows = [[join(c) for c in r] for r in rows]
    out = caption + ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * len(rows[0])]
    out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return out


def _cell_html_to_text(cell_html: str) -> str:
    out: list[str] = []
    pos = 0
    for m in re.finditer(r"<table>[\s\S]*?</table>", cell_html):
        out.append(_strip(cell_html[pos:m.start()]))
        out.append("\n" + "\n".join(_table_to_pipe(m.group(0))) + "\n")
        pos = m.end()
    out.append(_strip(cell_html[pos:]))
    t = "".join(out)
    t = re.sub(r"[ \t]+\n", "\n", t)
    return re.sub(r"\n{2,}", "\n", t).strip()


def hwp_criteria_text(md: str) -> Optional[str]:
    """고시 HWP(kordoc md) 의 바깥 표 '구 분 | 세부인정기준 및 방법' 에서 기준 칸(둘째 칸) 본문."""
    m = re.search(r"<th>\s*구\s*분\s*</th>\s*<th>[^<]*세부인정기준[^<]*</th>\s*</tr>\s*<tr>\s*<td>[\s\S]*?</td>\s*<td>", md)
    if not m:
        return None
    # 둘째 칸: 중첩 표를 세며 짝 맞는 </td> 까지
    i, depth = m.end(), 0
    for t in re.finditer(r"<(/?)(table|td)\b[^>]*>", md[i:]):
        closing, tag = t.group(1) == "/", t.group(2)
        if tag == "table":
            depth += -1 if closing else 1
        elif tag == "td" and closing and depth == 0:
            return _cell_html_to_text(md[i:i + t.start()])
    return None


def _plain(text: str) -> str:
    """표·공백·구두점 제외 본문(유사도 비교용)."""
    t = "\n".join(ln for ln in (text or "").split("\n") if not ln.strip().startswith("|"))
    return re.sub(r"[\s\W_]+", "", t)


def similarity(a: str, b: str) -> float:
    pa, pb = _plain(a), _plain(b)
    if not pa or not pb:
        return 0.0
    return difflib.SequenceMatcher(None, pa[:6000], pb[:6000], autojunk=False).ratio()


# ── 실행 ──────────────────────────────────────────────────────────────────────

def recover_items(items: list[dict], *, gosi_rows: list[dict] | None = None,
                  fetch_detail: Callable = sources.insu_detail, min_sim: float = 0.75) -> dict:
    """items(제자리 수정) 중 깨진 표가 있는 항목을 고시 HWP 로 복원. 반환: 보고서."""
    todo = [it for it in items if it.get("class_no") != "한약제제" and needs_recovery(it.get("criteria_text") or "")]
    report = {"candidates": len(todo), "recovered": [], "skipped": []}
    if not todo:
        return report
    gosi_rows = gosi_rows if gosi_rows is not None else collect_drug_gosi()
    by_key: dict[str, list[dict]] = {}
    for g in gosi_rows:
        by_key.setdefault(match_key(g["title"]), []).append(g)
    for it in todo:
        cands = sorted(by_key.get(match_key(it["title"]), []), key=lambda g: gosi_tuple(g["gosi_no"]), reverse=True)
        if not cands:
            report["skipped"].append({"title": it["title"], "reason": "고시 목록에서 같은 제목 없음"})
            continue
        g = cands[0]
        try:
            time.sleep(SLEEP)
            d = fetch_detail(*g["key"])
            hwp = next((a for a in d.get("attachments", []) if a["fnm"].lower().endswith((".hwp", ".hwpx"))), None)
            if not hwp:
                report["skipped"].append({"title": it["title"], "reason": "HWP 첨부 없음", "gosi": g["gosi_no"]})
                continue
            f = download(hwp["url"], RAW_DIR / "drug_gosi", expect=("ole", "hwp", "zip"), name_hint="gosi.hwp")
            md = convert(f["path"]).read_text(encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            report["skipped"].append({"title": it["title"], "reason": f"오류: {str(e)[:120]}", "gosi": g["gosi_no"]})
            continue
        new = hwp_criteria_text(md)
        if not new:
            report["skipped"].append({"title": it["title"], "reason": "HWP 에서 기준 칸을 찾지 못함", "gosi": g["gosi_no"]})
            continue
        sim = similarity(it["criteria_text"], new)
        older = gosi_tuple(g["gosi_no"]) < gosi_tuple(it.get("gosi_no"))
        need = min_sim - 0.15 if gosi_tuple(g["gosi_no"]) == gosi_tuple(it.get("gosi_no")) else min_sim  # 같은 고시면 느슨하게
        if sim < need or older:
            report["skipped"].append({"title": it["title"], "reason": f"본문 불일치(유사도 {sim:.2f})" if sim < need
                                      else "HWP 고시가 현재 본문보다 오래됨", "gosi": g["gosi_no"]})
            continue
        if needs_recovery(new):
            report["skipped"].append({"title": it["title"], "reason": "HWP 표도 머리행이 비어 있음", "gosi": g["gosi_no"]})
            continue
        it["criteria_text"] = new
        it["parse_flags"] = ",".join([x for x in (it.get("parse_flags") or "").split(",") if x] + ["table_from_hwp"])
        it["latest_url"] = it.get("latest_url") or d["url"]
        report["recovered"].append({"title": it["title"], "gosi": g["gosi_no"], "similarity": round(sim, 3),
                                    "url": d["url"], "file": hwp["fnm"]})
    return report


# ── 표시용 정리(원문 텍스트는 그대로 두고 화면에 낼 때만) ──────────────────────────

_MARK_ONLY = re.compile(r"^-?\s*(아\s*래|다\s*음)\s*-?$")


def normalize_tables(text: str) -> str:
    """책자 PDF 에서 온 파이프 표 정리.
    - 칸 안 줄바꿈으로 끊긴 행을 잇는다('| 운동부하심폐검사' + '(Cardio…) | … |')
    - 빈 행·'-아래-' 만 있는 행은 표 밖 글줄로
    - 한 칸짜리 상자(※ 설명 박스)는 일반 글로
    - 앞 칸이 빠진 행(병합 셀 이어짐)은 앞을 빈 칸으로 채워 열 수를 맞춘다
    """
    lines = (text or "").split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        if not lines[i].strip().startswith("|"):
            out.append(re.sub(r"\s*\|\s*$", "", lines[i]))     # 상자 테두리 잔재 '… |'
            i += 1
            continue
        if False:
            i += 1
            continue
        # 표 구간: '|' 로 시작하는 줄과, 끝나지 않은 칸의 이어지는 줄
        rows: list[str] = []
        while i < len(lines):
            s = lines[i].strip()
            if s.startswith("|"):
                cur, j = s, i
                while not cur.endswith("|") and j + 1 < len(lines) and not lines[j + 1].strip().startswith("|") and j - i < 5:
                    j += 1
                    cur += " " + lines[j].strip()
                if not cur.endswith("|"):           # 닫히지 않는 칸 = 상자 테두리 잔재 → 표 끝, 이 줄부터 일반 글
                    lines[i] = s.lstrip("|").strip()
                    break
                rows.append(cur)
                i = j + 1
            else:
                break
        cells = [_cells(r) for r in rows]
        pre, body = [], []
        for c in cells:
            vals = [x for x in c if x]
            if not vals:
                continue
            if len(vals) == 1 and _MARK_ONLY.match(vals[0]) and not body:
                pre.append(vals[0])
                continue
            body.append(c)
        out += pre
        if not body:
            continue
        width = max(len(c) for c in body)
        if width <= 1 or all(sum(1 for x in c if x) <= 1 for c in body):
            out += [next((x for x in c if x), "") for c in body]      # 상자 → 글
            continue
        sep = [k for k, r in enumerate(rows) if re.match(r"^\|\s*:?-{3}", r)]
        body = [c for c in body if not all(re.fullmatch(r":?-{3,}:?", x or "-") for x in c)]
        norm = [[""] * (width - len(c)) + c if len(c) < width else c for c in body]
        out.append("| " + " | ".join(norm[0]) + " |")
        out.append("|" + "---|" * width)
        out += ["| " + " | ".join(r) + " |" for r in norm[1:]]
        del sep
    return "\n".join(out)
