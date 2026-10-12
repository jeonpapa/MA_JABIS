"""정본 발언 타임라인 MD → 행사·발언블록·인용 (결정론 파서, 순수 함수).

외부 에이전트가 발행하는 `YYYY-MM-DD_주제별_인물발언_타임라인_정본.md` 를 읽어
`sv_event` / `sv_statement` / `sv_quote` 에 넣을 **데이터만** 돌려준다. DB·네트워크·LLM 을 모른다.
파일은 읽기만 한다.

증거 모델(소스 `MONITORING_SOURCE_AND_QUOTE_POLICY.md` §1, §7):
- verbatim  — `> ` 인용 블록만. 블록 원문을 **글자 그대로** `quote_text` 에 둔다(정규화·교정 없음).
- summary   — `내용`·`공식 발표요약`·`직접발언`·`맥락`·무라벨 글머리. 인용부호로 승격하지 않는다.
- analysis  — `해석`·`성격`·`대안해석`. 사실·인용과 분리.
- limitation — `한계`·`시점 한계`·`정량 한계`·`제도상태`·`직접인용(미확보)`.
라벨이 무엇이든(예: `직접발언`) 글머리 필드는 절대 인용(sv_quote)이 되지 않는다.

인용 검증: 각 세그먼트(곡괄호 “…” 안)를 생략표시 `[…]` 로 나눈 조각마다, `근거` locator 의 줄 범위
→ 파일 전체 순으로 정규화(공백·인용부호 제거) 부분문자열 검사 → `range|file|none`, locator 없으면 `unchecked`.

공개 API: `parse_timeline`, `parse_importance_table`, `norm`, `slugify`, `make_event_id`, `parse_locators`,
`extract_segments`, 데이터클래스 `ParsedEvent/ParsedStatement/ParsedQuote/ParseResult`, 상수 `TOPIC_SLUGS`,
`EVIDENCE_GRADES`.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ── 어휘 ────────────────────────────────────────────────────────────────────────

TOPIC_SLUGS: dict[str, str] = {
    "①": "price_reform", "②": "access", "③": "rwe", "④": "finance", "⑤": "negotiation",
}
EVIDENCE_GRADES: tuple[str, ...] = (
    "A", "B-quote", "B-summary", "C-quote", "C-summary", "Role-only", "Candidate-only",
)

# 글머리 필드 라벨 → 귀속 (verbatim 은 여기에 없다 — 인용은 `> ` 블록만)
_SUMMARY_LABELS = ("내용", "공식 발표요약", "직접발언", "맥락")
_ANALYSIS_LABELS = ("해석", "성격")
_LIMITATION_LABELS = ("한계", "시점 한계", "정량 한계", "제도상태")
_OTHER_LABELS = ("대안해석", "해석 신뢰도", "직접인용", "등급", "근거", "원문", "행사일/게시일", "행사·직책 확인")
KNOWN_FIELD_LABELS: tuple[str, ...] = _SUMMARY_LABELS + _ANALYSIS_LABELS + _LIMITATION_LABELS + _OTHER_LABELS

# ── 문법 ────────────────────────────────────────────────────────────────────────

_RE_H1 = re.compile(r"^# (.+?)\s*$")
_RE_NUMBERED_H1 = re.compile(r"^# (\d+)\.\s*(.*?)\s*$")
_RE_TOPIC = re.compile(r"^# (\d+)\. 주제 ([①②③④⑤⑥⑦⑧⑨⑩]) (.+?)\s*$")
_RE_EVENT = re.compile(r"^## (\d{4}-\d{2}-\d{2})\s*[｜|]\s*(.+?)\s*$")
_RE_H2 = re.compile(r"^## ")
_RE_SPEAKER = re.compile(r"^### (.+?)(?: [—–] (.+?))?\s*$")
_RE_BLOCKQUOTE = re.compile(r"^>(?: (.*)|)$")
_RE_FIELD = re.compile(r"^- \*\*(.+?)[:：]\*\*\s*(.*?)\s*$")
_RE_BULLET = re.compile(r"^- (.*?)\s*$")
_RE_MASTER_ROW = re.compile(r"^\| \*\*(\d{4}-\d{2}-\d{2})\*\* \| (.+?) \| (.+?) \| (.+?) \| (.+?) \|$")
_RE_SEGMENT = re.compile(r"“([^”]+)”")
_RE_LOCATOR = re.compile(r"`(sources/[^`:]+?)(?::([^`]*))?`")
_RE_LINESPEC = re.compile(r"^(\d+)(?:\s*-\s*(\d+))?$")
_RE_ELLIPSIS = re.compile(r"\[(?:…|\.\.\.)\]")
_RE_HR = re.compile(r"^-{3,}\s*$")
_RE_IMPORTANCE_ROW = re.compile(
    r"^\| \*\*(.+?)\*\* \| (\d+) \| (\d+) \| (\d+) \| (\d+) \| (\d+) \| \*\*(\d+)\*\* \| \*\*(\d)\*\* \| (\d)(\(잠정\))? \| (\d) \|$"
)
_NORM_DROP = re.compile(r"[\s“”‘’\"']+")
_SLUG_DROP = re.compile(r"[^0-9A-Za-z가-힣]+")
_TITLE_SPLIT = re.compile(r"[\s「」『』“”‘’\"'()（）]+")

_MATCH_RANK = {"none": 0, "file": 1, "range": 2}


# ── 데이터 ──────────────────────────────────────────────────────────────────────

@dataclass
class ParsedEvent:
    event_id: str
    event_date: str
    title: str                         # 정본 섹션 헤딩 제목(첫 등장). 섹션이 없으면 마스터 표 제목
    context: str | None = None         # 마스터 표 `행사·맥락`
    speakers: list[str] = field(default_factory=list)     # 마스터 표 `핵심 인물과 당시 직책` (", " 분할)
    topics: list[str] = field(default_factory=list)       # 마스터 표 `핵심 주제` (", " 분할)
    grades: list[str] = field(default_factory=list)       # 마스터 표 `발언근거` ("/"·"+" 분할)
    grades_raw: str | None = None
    topic_slugs: list[str] = field(default_factory=list)  # 이 행사가 실린 주제 섹션(등장 순)
    speaker_names: list[str] = field(default_factory=list)  # 섹션 `###` 발언자(등장 순)
    publication_dates: list[str] = field(default_factory=list)
    source_paths: list[str] = field(default_factory=list)   # 발언블록 locator 의 파일(등장 순)
    in_master: bool = True
    line: int | None = None


@dataclass
class ParsedStatement:
    statement_id: str
    event_id: str
    topic: str
    speaker_name: str
    speaker_role_at_event: str | None
    multi_speaker: bool
    summary_text: str | None = None
    analysis_text: str | None = None
    analysis_confidence: str | None = None
    limitation_text: str | None = None
    policy_status: str | None = None
    evidence_grade: str = ""
    evidence_ref: str | None = None    # `근거` 원문 그대로
    locators: list[dict] = field(default_factory=list)
    source_url: str | None = None
    role_check_url: str | None = None
    event_date_stated: str | None = None
    publication_date: str | None = None
    headline_quote_id: str | None = None
    quote_ids: list[str] = field(default_factory=list)
    fields: list[tuple[str, str]] = field(default_factory=list)   # (라벨, 값) 원본 순서 — 무라벨은 ""
    line: int | None = None


@dataclass
class ParsedQuote:
    quote_id: str
    event_id: str
    speaker_name: str
    quote_text: str                    # `> ` 블록 원문 그대로(여러 줄이면 "\n" 연결)
    quote_norm: str
    segments: list[str]                # “…” 안 문구들(없으면 블록 전체 1개)
    statement_ids: list[str]
    topics: list[str]
    locator: str | None                # 첫 블록에서 가장 잘 맞은 locator ("sources/x.txt:a-b")
    locator_ref: dict | None
    locator_match: str                 # range | file | none | unchecked (모든 등장 블록 중 최선)
    locators: list[dict] = field(default_factory=list)   # 모든 등장 블록 locator 합집합(순서 보존)
    evidence_grade: str | None = None  # 첫 등장 발언블록의 등급
    line: int | None = None


@dataclass
class ParseResult:
    events: list[ParsedEvent]
    statements: list[ParsedStatement]
    quotes: list[ParsedQuote]
    meta: dict[str, Any]
    warnings: list[str]
    raw_quote_blocks: int              # 주제 섹션 안 `> ` 블록 수(교차 표시 중복 포함)
    quote_blocks: list[str] = field(default_factory=list)          # 그 블록들의 원문(손실 없음 검증용)
    excluded_blockquotes: list[dict] = field(default_factory=list)  # 주제 섹션 밖 `> ` (편집 주석 — 인용 아님)
    canonical_sha256: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


# ── 순수 헬퍼 ────────────────────────────────────────────────────────────────────

def norm(s: str) -> str:
    """비교용 정규화: 공백 전부 + 인용부호(“”‘’\"') 제거. 저장용이 아니다."""
    return _NORM_DROP.sub("", s or "")


def slugify(title: str) -> str:
    return _SLUG_DROP.sub("-", title).strip("-")


def make_event_id(event_date: str, title: str) -> str:
    return f"{event_date}-{slugify(title)}"[:90]


def _sha1_16(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]


def _split_list(s: str) -> list[str]:
    return [p.strip() for p in s.split(",") if p.strip()]


def _split_grades(s: str) -> list[str]:
    return [p.strip() for p in re.split(r"[/+]", s or "") if p.strip()]


def _join(a: str | None, b: str) -> str:
    return b if not a else f"{a}\n{b}"


def _fmt_locator(loc: dict) -> str:
    if loc.get("start") is None:
        return loc["path"]
    if loc["start"] == loc["end"]:
        return f"{loc['path']}:{loc['start']}"
    return f"{loc['path']}:{loc['start']}-{loc['end']}"


def parse_locators(text: str, warnings: list[str] | None = None, *, where: str = "") -> list[dict]:
    """`근거` 값에서 `` `sources/<file>[:a-b[, c-d | ,n]]` `` 를 전부 → [{path, start, end}] (범위마다 1개).

    줄 지정 없음 → start=end=None(파일 전체). 단일 줄 `n` → start=end=n.
    """
    out: list[dict] = []
    for m in _RE_LOCATOR.finditer(text or ""):
        path, spec = m.group(1).strip(), m.group(2)
        if spec is None or not spec.strip():
            out.append({"path": path, "start": None, "end": None})
            continue
        for part in spec.split(","):
            part = part.strip()
            lm = _RE_LINESPEC.match(part)
            if not lm:
                if warnings is not None:
                    warnings.append(f"{where}locator 줄 지정 해석 불가: `{m.group(0)}` ({part!r})")
                continue
            start = int(lm.group(1))
            end = int(lm.group(2)) if lm.group(2) else start
            if end < start and warnings is not None:
                warnings.append(f"{where}locator 범위 역순: `{m.group(0)}`")
            out.append({"path": path, "start": start, "end": end})
    return out


def extract_segments(block_text: str) -> list[str]:
    """블록 안 곡괄호 “…” 세그먼트 전부. 없으면 블록 전체가 세그먼트 1개."""
    segs = _RE_SEGMENT.findall(block_text)
    return segs if segs else [block_text]


def _pieces(segment: str) -> list[str]:
    return [n for n in (norm(p) for p in _RE_ELLIPSIS.split(segment)) if n]


class _SourceText:
    """locator 파일 캐시: 줄별 정규화 문자열."""

    def __init__(self, root: Path):
        self.root = root
        self._cache: dict[str, list[str] | None] = {}

    def lines(self, rel: str) -> list[str] | None:
        if rel not in self._cache:
            p = self.root / rel
            try:
                raw = p.read_bytes().decode("utf-8-sig")
            except (OSError, UnicodeDecodeError):
                self._cache[rel] = None
            else:
                self._cache[rel] = [norm(x) for x in raw.splitlines()]
        return self._cache[rel]


def _piece_level(piece: str, loc: dict, src: _SourceText) -> str | None:
    lines = src.lines(loc["path"])
    if lines is None:
        return None
    if loc["start"] is None:
        window = "".join(lines)
    else:
        window = "".join(lines[max(loc["start"] - 1, 0):loc["end"]])
    if piece in window:
        return "range"
    if piece in "".join(lines):
        return "file"
    return "none"


def _verify_block(segments: list[str], locators: list[dict], src: _SourceText) -> tuple[str, dict | None]:
    """블록 1개의 대조 결과와 가장 잘 맞은 locator.

    조각마다 locator 들 중 최선을 취하고(다중 범위·다중 출처 인용), 블록 수준은 조각 중 최악.
    읽을 수 있는 locator 파일이 하나도 없으면 unchecked.
    """
    readable = [loc for loc in locators if src.lines(loc["path"]) is not None]
    if not readable:
        return "unchecked", (locators[0] if locators else None)
    pieces = [p for seg in segments for p in _pieces(seg)]
    if not pieces:
        return "unchecked", readable[0]
    worst = "range"
    best_loc: dict | None = None
    for i, piece in enumerate(pieces):
        best, best_for_piece = "none", None
        for loc in readable:
            lvl = _piece_level(piece, loc, src) or "none"
            if _MATCH_RANK[lvl] > _MATCH_RANK[best]:
                best, best_for_piece = lvl, loc
        if i == 0:
            best_loc = best_for_piece or readable[0]
        if _MATCH_RANK[best] < _MATCH_RANK[worst]:
            worst = best
    return worst, best_loc


def _titles_compatible(a: str, b: str) -> bool:
    """정본 섹션 제목과 마스터 표 제목의 일치 판정(약칭 허용).

    정규화 후 한쪽이 다른 쪽을 포함하거나, 한쪽 토큰 집합이 다른 쪽의 부분집합이면 일치.
    (실측: `약제 가산제도 설명회` ⊂ `약제 결정·조정기준 개정 가산제도 설명회`,
           `기등재 … 설명회` ⊂ `심평원 「기등재 … 설명회」`)
    """
    ca, cb = _TITLE_SPLIT.sub("", a), _TITLE_SPLIT.sub("", b)
    if not ca or not cb:
        return False
    if ca in cb or cb in ca:
        return True
    ta = {t for t in _TITLE_SPLIT.split(a) if t}
    tb = {t for t in _TITLE_SPLIT.split(b) if t}
    return bool(ta and tb) and (ta <= tb or tb <= ta)


def _token_overlap(a: str, b: str) -> int:
    return len({t for t in _TITLE_SPLIT.split(a) if t} & {t for t in _TITLE_SPLIT.split(b) if t})


def _grade_warnings(grade: str, where: str, warnings: list[str]) -> None:
    for tok in _split_grades(grade):
        if tok not in EVIDENCE_GRADES:
            warnings.append(f"{where}어휘 밖 증거등급 '{tok}' (원문 '{grade}')")


def _section_body(lines: list[str], start: int, end: int) -> str:
    body = lines[start:end]
    while body and (not body[-1].strip() or _RE_HR.match(body[-1])):
        body.pop()
    while body and not body[0].strip():
        body.pop(0)
    return "\n".join(body)


# ── 파서 ────────────────────────────────────────────────────────────────────────

@dataclass
class _Block:
    text: str
    line: int


@dataclass
class _RawStatement:
    topic: str
    date: str
    title: str
    speaker: str
    role: str | None
    line: int
    blocks: list[_Block] = field(default_factory=list)
    fields: list[tuple[str, str, int]] = field(default_factory=list)


def parse_timeline(path: str | Path, *, root: str | Path | None = None) -> ParseResult:
    """정본 타임라인 MD 를 파싱한다. `root` 는 locator(`sources/...`) 의 기준 디렉터리(기본: MD 의 폴더).

    어휘 밖 등급·필드, 마스터 표 불일치, 원문 대조 실패, 근거 파일 부재는 `warnings` 로만 남긴다(예외 없음).
    """
    path = Path(path)
    root_dir = Path(root) if root is not None else path.parent
    raw_bytes = path.read_bytes()
    text = raw_bytes.decode("utf-8-sig")
    lines = text.splitlines()
    warnings: list[str] = []
    meta: dict[str, Any] = {"topics": [], "header": {}}

    # 1) 최상위 `# ` 섹션 경계
    h1: list[tuple[int, str]] = [(i, ln) for i, ln in enumerate(lines) if _RE_H1.match(ln)]
    bounds: list[tuple[int, int, str]] = []
    for k, (i, ln) in enumerate(h1):
        end = h1[k + 1][0] if k + 1 < len(h1) else len(lines)
        bounds.append((i, end, ln))

    first_numbered = next((i for i, _, ln in bounds if _RE_NUMBERED_H1.match(ln)), len(lines))
    if bounds and not _RE_NUMBERED_H1.match(bounds[0][2]):
        meta["title"] = _RE_H1.match(bounds[0][2]).group(1)
    preamble_start = bounds[0][0] + 1 if bounds and not _RE_NUMBERED_H1.match(bounds[0][2]) else 0
    meta["preamble_md"] = _section_body(lines, preamble_start, first_numbered)
    for ln in lines[preamble_start:first_numbered]:
        if _RE_H2.match(ln):
            break
        fm = _RE_FIELD.match(ln)
        if fm:
            meta["header"][fm.group(1)] = fm.group(2)

    # 2) `> ` 블록 수집 (전 문서) — 주제 섹션 밖은 편집 주석으로 분리 보존
    topic_ranges: list[tuple[int, int, str, str, str]] = []   # (start, end, slug, marker, title)
    master_range: tuple[int, int] | None = None
    for i, end, ln in bounds:
        tm = _RE_TOPIC.match(ln)
        nm = _RE_NUMBERED_H1.match(ln)
        if tm:
            marker = tm.group(2)
            slug = TOPIC_SLUGS.get(marker)
            if slug is None:
                warnings.append(f"L{i + 1}: 알 수 없는 주제 번호 '{marker}' — 섹션 무시")
                continue
            topic_ranges.append((i, end, slug, marker, tm.group(3)))
            meta["topics"].append({"slug": slug, "marker": marker, "number": int(tm.group(1)),
                                   "title": tm.group(3)})
        elif nm:
            num = nm.group(1)
            body = _section_body(lines, i + 1, end)
            if num == "1":
                master_range = (i, end)
            elif num == "7":
                meta["repeat_messages_md"] = body
            elif num == "8":
                meta["source_ledger_md"] = body
            elif num == "9":
                meta["withheld_md"] = body

    in_topic = [False] * len(lines)
    for s, e, *_ in topic_ranges:
        for j in range(s, e):
            in_topic[j] = True
    excluded: list[dict] = []
    j = 0
    while j < len(lines):
        if _RE_BLOCKQUOTE.match(lines[j]) and not in_topic[j]:
            start = j
            buf = []
            while j < len(lines) and _RE_BLOCKQUOTE.match(lines[j]):
                buf.append(_RE_BLOCKQUOTE.match(lines[j]).group(1) or "")
                j += 1
            excluded.append({"line": start + 1, "text": "\n".join(buf)})
            continue
        j += 1

    # 3) 마스터 표
    master: list[dict] = []
    if master_range is None:
        warnings.append("마스터 표 섹션(# 1.)이 없음")
    else:
        for i in range(master_range[0] + 1, master_range[1]):
            ln = lines[i]
            mm = _RE_MASTER_ROW.match(ln)
            if mm:
                master.append({"date": mm.group(1), "title": mm.group(2).strip(), "speakers": mm.group(3).strip(),
                               "topics": mm.group(4).strip(), "grades": mm.group(5).strip(), "line": i + 1,
                               "event": None})
            elif ln.startswith("| **"):
                warnings.append(f"L{i + 1}: 마스터 표 행 형식 불일치 — 무시")
        for row in master:
            _grade_warnings(row["grades"], f"L{row['line']}: 마스터 표 ", warnings)

    # 4) 주제 섹션 → 원시 발언블록
    raw_statements: list[_RawStatement] = []
    quote_blocks: list[str] = []
    for s, e, slug, _marker, _title in topic_ranges:
        cur_event: tuple[str, str] | None = None
        cur: _RawStatement | None = None
        last_field_idx: int | None = None   # 연속 줄을 이어붙일 필드
        i = s + 1
        while i < e:
            ln = lines[i]
            em = _RE_EVENT.match(ln)
            sm = _RE_SPEAKER.match(ln)
            if em:
                cur_event, cur, last_field_idx = (em.group(1), em.group(2)), None, None
                i += 1
                continue
            if _RE_H2.match(ln):
                warnings.append(f"L{i + 1}: 행사 형식이 아닌 '## ' 헤딩 — 이후 행사 미지정")
                cur_event, cur, last_field_idx = None, None, None
                i += 1
                continue
            if sm:
                last_field_idx = None
                if cur_event is None:
                    warnings.append(f"L{i + 1}: 행사 헤딩 없이 발언자 '{sm.group(1)}' — 무시")
                    cur = None
                else:
                    cur = _RawStatement(topic=slug, date=cur_event[0], title=cur_event[1],
                                        speaker=sm.group(1).strip(),
                                        role=(sm.group(2) or "").strip() or None, line=i + 1)
                    raw_statements.append(cur)
                i += 1
                continue
            bm = _RE_BLOCKQUOTE.match(ln)
            if bm:
                start = i
                buf = []
                while i < e and _RE_BLOCKQUOTE.match(lines[i]):
                    buf.append(_RE_BLOCKQUOTE.match(lines[i]).group(1) or "")
                    i += 1
                block = "\n".join(buf)
                quote_blocks.append(block)
                if cur is None:
                    warnings.append(f"L{start + 1}: 발언자 없는 인용 블록 — 인용으로 적재하지 않음")
                else:
                    cur.blocks.append(_Block(text=block, line=start + 1))
                last_field_idx = None
                continue
            if not ln.strip() or _RE_HR.match(ln):
                last_field_idx = None
                i += 1
                continue
            fm = _RE_FIELD.match(ln)
            bu = None if fm else _RE_BULLET.match(ln)
            if cur is None:
                warnings.append(f"L{i + 1}: 발언자 밖 내용 — 무시: {ln[:40]}")
                i += 1
                continue
            if fm:
                cur.fields.append((fm.group(1).strip(), fm.group(2), i + 1))
                last_field_idx = len(cur.fields) - 1
            elif bu:
                cur.fields.append(("", bu.group(1), i + 1))
                last_field_idx = len(cur.fields) - 1
            elif last_field_idx is not None and not ln.startswith(("#", "|")):
                lab, val, at = cur.fields[last_field_idx]
                cur.fields[last_field_idx] = (lab, f"{val}\n{ln.strip()}", at)
            else:
                warnings.append(f"L{i + 1}: 분류되지 않은 줄 — 무시: {ln[:40]}")
            i += 1

    # 5) 섹션 행사 ↔ 마스터 표 결합
    events: list[ParsedEvent] = []
    by_key: dict[tuple[str, str], ParsedEvent] = {}
    for rs in raw_statements:
        key = (rs.date, rs.title)
        if key in by_key:
            continue
        cands = [r for r in master if r["date"] == rs.date]
        hit = next((r for r in cands if r["title"] == rs.title), None) \
            or next((r for r in cands if _titles_compatible(rs.title, r["title"])), None)
        if hit is None and cands:
            hit = max(cands, key=lambda r: _token_overlap(rs.title, r["title"]))
            warnings.append(f"L{rs.line}: 행사 '{rs.date}｜{rs.title}' 가 마스터 표 제목 '{hit['title']}' 와 불일치"
                            f" — 같은 날짜 행으로 결합")
        if hit is None:
            warnings.append(f"L{rs.line}: 행사 '{rs.date}｜{rs.title}' 가 마스터 표에 없음")
            ev = ParsedEvent(event_id=make_event_id(rs.date, rs.title), event_date=rs.date, title=rs.title,
                             in_master=False, line=rs.line)
            events.append(ev)
        elif hit["event"] is None:
            ev = ParsedEvent(event_id=make_event_id(rs.date, rs.title), event_date=rs.date, title=rs.title,
                             context=hit["title"], speakers=_split_list(hit["speakers"]),
                             topics=_split_list(hit["topics"]), grades=_split_grades(hit["grades"]),
                             grades_raw=hit["grades"], line=hit["line"])
            hit["event"] = ev
        else:
            ev = hit["event"]
        by_key[key] = ev
    for row in master:
        if row["event"] is None:
            warnings.append(f"L{row['line']}: 마스터 표 행사 '{row['date']}｜{row['title']}' 에 발언 섹션 없음")
            row["event"] = ParsedEvent(event_id=make_event_id(row["date"], row["title"]), event_date=row["date"],
                                       title=row["title"], context=row["title"],
                                       speakers=_split_list(row["speakers"]), topics=_split_list(row["topics"]),
                                       grades=_split_grades(row["grades"]), grades_raw=row["grades"],
                                       line=row["line"])
    ordered = [r["event"] for r in master]
    seen_ev: set[int] = set()
    events_out: list[ParsedEvent] = []
    for ev in ordered + events:
        if id(ev) not in seen_ev:
            seen_ev.add(id(ev))
            events_out.append(ev)
    ids = [ev.event_id for ev in events_out]
    for dup in sorted({x for x in ids if ids.count(x) > 1}):
        warnings.append(f"event_id 충돌: {dup}")

    # 6) 발언블록 → ParsedStatement, 인용 → ParsedQuote(교차 주제 병합)
    src = _SourceText(root_dir)
    statements: list[ParsedStatement] = []
    quotes: dict[tuple[str, str, str], ParsedQuote] = {}
    quote_best_rank: dict[str, int] = {}
    seen_sid: set[str] = set()
    for rs in raw_statements:
        ev = by_key[(rs.date, rs.title)]
        where = f"L{rs.line} {rs.speaker}: "
        sid = _sha1_16(f"{ev.event_id}|{rs.speaker}|{rs.topic}")
        if sid in seen_sid:
            warnings.append(f"{where}발언블록 중복(행사×발언자×주제) — statement_id {sid}")
        seen_sid.add(sid)
        multi = "·" in rs.speaker and rs.role is None
        st = ParsedStatement(statement_id=sid, event_id=ev.event_id, topic=rs.topic, speaker_name=rs.speaker,
                             speaker_role_at_event=rs.role, multi_speaker=multi, line=rs.line)
        for label, value, _at in rs.fields:
            st.fields.append((label, value))
            if label == "" or label in _SUMMARY_LABELS:
                st.summary_text = _join(st.summary_text, value)
            elif label in _ANALYSIS_LABELS:
                st.analysis_text = _join(st.analysis_text, value)
            elif label == "대안해석":
                st.analysis_text = _join(st.analysis_text, f"대안해석: {value}")
            elif label == "해석 신뢰도":
                st.analysis_confidence = value
            elif label in _LIMITATION_LABELS:
                st.limitation_text = _join(st.limitation_text, value)
                if label == "제도상태":
                    st.policy_status = _join(st.policy_status, value)
            elif label == "직접인용":
                st.limitation_text = _join(st.limitation_text, f"직접인용: {value}")
            elif label == "등급":
                st.evidence_grade = value
            elif label == "근거":
                st.evidence_ref = value
                st.locators = parse_locators(value, warnings, where=where)
                if not st.locators:
                    warnings.append(f"{where}근거에 `sources/...` locator 없음: {value[:60]}")
            elif label == "원문":
                st.source_url = value
            elif label == "행사일/게시일":
                parts = [p.strip() for p in value.split("/") if p.strip()]
                if parts:
                    st.event_date_stated, st.publication_date = parts[0], parts[-1]
                    if parts[0] != ev.event_date:
                        warnings.append(f"{where}행사일 표기 {parts[0]} ≠ 섹션 {ev.event_date}")
            elif label == "행사·직책 확인":
                st.role_check_url = value
            else:
                warnings.append(f"{where}어휘 밖 필드 '{label}' — 원문만 보존(fields)")
        if not st.evidence_grade:
            warnings.append(f"{where}등급 없음")
        else:
            _grade_warnings(st.evidence_grade, where, warnings)
        if rs.blocks and not st.locators:
            warnings.append(f"{where}인용 블록이 있으나 근거 locator 없음 — 원문 대조 불가(unchecked)")
        for loc in st.locators:
            if src.lines(loc["path"]) is None:
                warnings.append(f"{where}근거 파일 없음/읽기 실패: {loc['path']}")

        for blk in rs.blocks:
            segs = extract_segments(blk.text)
            if blk.text.count("“") != blk.text.count("”"):
                warnings.append(f"L{blk.line}: 곡괄호 인용부호 짝 불일치")
            qn = norm(blk.text)
            level, best_loc = _verify_block(segs, st.locators, src)
            key = (ev.event_id, rs.speaker, qn)
            q = quotes.get(key)
            if q is None:
                q = ParsedQuote(quote_id=_sha1_16(f"{ev.event_id}|{rs.speaker}|{qn}"), event_id=ev.event_id,
                                speaker_name=rs.speaker, quote_text=blk.text, quote_norm=qn, segments=segs,
                                statement_ids=[], topics=[],
                                locator=_fmt_locator(best_loc) if best_loc else None,
                                locator_ref=dict(best_loc) if best_loc else None,
                                locator_match=level, evidence_grade=st.evidence_grade or None, line=blk.line)
                quotes[key] = q
                quote_best_rank[q.quote_id] = _MATCH_RANK.get(level, -1)
            else:
                rank = _MATCH_RANK.get(level, -1)
                if rank > quote_best_rank[q.quote_id]:
                    q.locator_match, quote_best_rank[q.quote_id] = level, rank
            if sid not in q.statement_ids:
                q.statement_ids.append(sid)
            if rs.topic not in q.topics:
                q.topics.append(rs.topic)
            for loc in st.locators:
                if loc not in q.locators:
                    q.locators.append(dict(loc))
            if q.quote_id not in st.quote_ids:
                st.quote_ids.append(q.quote_id)
        st.headline_quote_id = st.quote_ids[0] if st.quote_ids else None
        statements.append(st)

        # 행사 보강(섹션 유래)
        if rs.topic not in ev.topic_slugs:
            ev.topic_slugs.append(rs.topic)
        if rs.speaker not in ev.speaker_names:
            ev.speaker_names.append(rs.speaker)
        if st.publication_date and st.publication_date not in ev.publication_dates:
            ev.publication_dates.append(st.publication_date)
        for loc in st.locators:
            if loc["path"] not in ev.source_paths:
                ev.source_paths.append(loc["path"])

    for q in quotes.values():
        if q.locator_match == "none":
            warnings.append(f"L{q.line} {q.speaker_name}: 인용 원문 대조 실패(none) — {q.quote_text[:60]}")

    return ParseResult(events=events_out, statements=statements, quotes=list(quotes.values()), meta=meta,
                       warnings=warnings, raw_quote_blocks=len(quote_blocks), quote_blocks=quote_blocks,
                       excluded_blockquotes=excluded,
                       canonical_sha256=hashlib.sha256(raw_bytes).hexdigest())


# ── 부서-인물지도 §2.2 중요도표 ───────────────────────────────────────────────────

def parse_importance_table(path: str | Path, *, warnings: list[str] | None = None) -> dict[str, dict]:
    """`부서-인물지도` §2.2 표 → {이름: {score_total, importance, components, work_importance,
    work_provisional, public_confidence}}. 섹션·행 형식 실패는 경고(warnings·log)만, 해당 결과 없음."""
    warns = warnings if warnings is not None else []
    n0 = len(warns)
    lines = Path(path).read_bytes().decode("utf-8-sig").splitlines()
    start = next((i for i, ln in enumerate(lines) if re.match(r"^#{1,6} 2\.2(?:\s|$)", ln)), None)
    if start is None:
        warns.append("중요도표: '2.2' 섹션 없음")
        log.warning("importance table: %s", warns[-1])
        return {}
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"^#{1,2} ", lines[i])), len(lines))
    out: dict[str, dict] = {}
    for i in range(start + 1, end):
        ln = lines[i]
        if not ln.startswith("| **"):
            continue
        m = _RE_IMPORTANCE_ROW.match(ln)
        if not m:
            warns.append(f"중요도표 L{i + 1}: 행 형식 불일치 — 무시")
            continue
        name = m.group(1).strip()
        comps = [int(m.group(k)) for k in range(2, 7)]
        total = int(m.group(7))
        if sum(comps) != total:
            warns.append(f"중요도표 L{i + 1}: {name} 구성점수 합 {sum(comps)} ≠ 총점 {total}")
        if name in out:
            warns.append(f"중요도표 L{i + 1}: {name} 중복 — 첫 행 유지")
            continue
        out[name] = {"score_total": total, "importance": int(m.group(8)), "components": comps,
                     "work_importance": int(m.group(9)), "work_provisional": bool(m.group(10)),
                     "public_confidence": int(m.group(11))}
    if not out:
        warns.append("중요도표: 해석된 행 없음")
    for w in warns[n0:]:
        log.warning("importance table: %s", w)
    return out
