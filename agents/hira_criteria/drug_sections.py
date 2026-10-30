"""일반약제 기준의 '문단 ↔ 질환' 연결 (결정론 후처리).

왜: 항목 단위 KCD 매핑은 '건선' 이 '건선성 관절염' 안에 들어 있어도, '당뇨병' 이 '당뇨병성 황반부종'·'당뇨병이 병발한
경우(부작용)' 에만 나와도 같은 질환으로 연결했다. 그 결과 질환 검색에 엉뚱한 문단·약이 섞였다.

하는 일 (LLM 매핑 직후, 같은 스테이징 DB 에서)
1) 원문을 문단(1. / 가.)으로 나누고, 질환 표현을 '긴 표현 우선'으로 찾는다(긴 표현 안의 짧은 표현은 가린다).
2) 문단 제목(1. / 가. 줄)에 나온 질환 → criteria_section_kcd 에 저장(검색 발췌·비교에 사용).
3) 항목 단위 매핑 정리 (criteria_disease_kcd.note 에 관계 기록)
   - rel:indication  제목 줄·서두에 나온 질환(급여 대상)
   - rel:mention     하위 목록(1), 가) …)에서만 언급(예: '당뇨병이 병발한 경우', '당뇨병 환자' 위험군)
   - 다른 질환의 긴 표현 안에서만 나오는 코드 → 삭제(예: 골리무맙 '건선' ← '건선성 관절염')
   - 제목 줄에 나왔는데 매핑이 없던 질환 → 추가(method='section')
- method='manual' 행은 건드리지 않는다.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

from .drug_disease import CURATED

# 합병증·특수 형태: 상위 질환 코드로 뭉개지지 않게 별도 코드로 (긴 표현이 먼저 잡힌다)
EXTRA_PHRASES: dict[str, list[str]] = {
    "건선성 관절염": ["M07", "L405"], "건선성관절염": ["M07", "L405"], "관절병성 건선": ["M07", "L405"],
    "당뇨병성 황반부종": ["H360"], "당뇨황반부종": ["H360"], "당뇨병성 망막병증": ["H360"], "당뇨망막병증": ["H360"],
    "당뇨병성 말초 신경병증": ["G632"], "당뇨병성 말초신경병증": ["G632"], "당뇨병성 신경병증": ["G632"],
    "당뇨병성 신경증": ["G632"], "당뇨병성 다발신경병증": ["G632"], "당뇨병성 족부궤양": ["L97"], "당뇨발": ["L97"],
    "당뇨병성 신증": ["N083"], "당뇨병성 신장병": ["N083"],
    "건선성 포도막염": ["H20"], "소아 건선": ["L40"], "보통건선": ["L400"], "판상 건선": ["L400"],
    "류마티스성 관절염": ["M05", "M06"], "류마티스성관절염": ["M05", "M06"], "소아 특발성 관절염": ["M08"],
    "비방사선 축성 척추관절염": ["M468"], "축성 척추관절염": ["M468"],
    "폐동맥성 고혈압": ["I270"], "폐고혈압": ["I27"], "폐동맥 고혈압": ["I270"], "혈전색전성 폐고혈압": ["I272"],
    "재생불량성 빈혈": ["D61"], "재생불량성빈혈": ["D61"], "만성신부전 환자의 빈혈": ["D631"],
    "A형 혈우병": ["D66"], "B형 혈우병": ["D67"], "후천성 혈우병": ["D684"], "폐렴주위 흉수": ["J90"],
    "B형간염": ["B181"], "만성 활동성 B형간염": ["B181"], "당뇨병용제": ["E11"],
    "신성 빈혈": ["D631"], "신성빈혈": ["D631"], "만성 신부전에 의한 빈혈": ["D631"], "만성신부전에 의한 빈혈": ["D631"],
}

# 질환이 아닌데 질환명을 품은 표현(가리기만 함): 결과(예방 대상)·진단 기준 문구 등
MASK_PHRASES = ["뇌졸중 및 전신 색전증의 예방", "뇌졸중 및 전신색전증의 예방", "증상성 기면증", "철결핍성 빈혈로 진단된",
                "심부전의 증상 또는 징후 유무에 관계없이", "당뇨병치료제", "당뇨병 치료제"]

# 질환 그룹: 이 코드는 3단 상위가 아니라 다른 그룹으로 묶는다(검색·비교 공통)
GROUP_REMAP = {"M06": "M05", "H30": "H20", "L405": "M07", "E103": "H36", "E113": "H36", "E123": "H36", "E133": "H36", "E143": "H36",
               "H360": "H36", "E104": "G63", "E114": "G63", "E124": "G63", "E134": "G63", "E144": "G63", "G632": "G63"}
GROUP_NAME = {"M05": "류마티스관절염", "H20": "포도막염", "M07": "건선성 관절염", "H36": "당뇨병성 망막병증·황반부종", "G63": "당뇨병성 신경병증 등 다발신경병증",
              "L97": "다리 궤양(당뇨병성 족부궤양 등)"}


def gkey(code: str) -> str:
    code = (code or "").replace(".", "")
    for n in (5, 4):
        if code[:n] in GROUP_REMAP:
            return GROUP_REMAP[code[:n]]
    return code[:3]


HEAD_RE = re.compile(r"^\s*(\d{1,2}|[가-하])\.\s*\S")
SUB_RE = re.compile(r"^\s*(?:\d{1,2}\)|[가-하]\)|\(\d{1,2}\)|\([가-하]\)|[①-⑳]|[○◦•·\-])\s*")
MARK_RE = re.compile(r"-\s*(?:아\s*래|다\s*음)\s*-")
# 급여 대상이 아닌 문단(금기·제외·부작용·교체·결핵 지침 등) — 여기 나온 질환은 '언급'
# 질환이 급여 대상이 아니라 위험요인·부작용·감별 대상으로 언급되는 문맥
MENTION_CTX = re.compile(r"부작용|병발|위험\s*요인|위험\s*인자|위험군|면역저하|금기|제외|감별|이상반응")
NEG_HEAD = re.compile(r"금기|제외|투여\s*금지|투여하지|중단|부작용|주의\s*사항|잠복결핵|교체\s*투여|이상반응|위험군|고위험")


def _n(s: str) -> str:
    return re.sub(r"\s+", "", s or "").lower()


@dataclass
class Section:
    level: int                # 1 = '1.' , 2 = '가.'
    heading: str              # 제목 줄(원문)
    start: int                # 줄 번호
    end: int = 0              # 다음 같은/상위 수준 제목 직전 줄
    codes: list[str] = field(default_factory=list)


def split_sections(text: str) -> list[Section]:
    lines = (text or "").split("\n")
    secs: list[Section] = []
    for i, ln in enumerate(lines):
        m = HEAD_RE.match(ln)
        if m and not re.match(r"^\s*\d{1,2}\.\d", ln):        # '1.5mg' 같은 숫자 제외
            secs.append(Section(level=1 if m.group(1).isdigit() else 2, heading=ln.strip(), start=i))
    for k, s in enumerate(secs):
        nxt = next((t.start for t in secs[k + 1:] if t.level <= s.level), len(lines))
        s.end = nxt
    return secs


def negative_lines(text: str) -> set[int]:
    """금기·제외 등 급여 대상이 아닌 문단에 속한 줄 번호."""
    out: set[int] = set()
    for s in split_sections(text):
        if NEG_HEAD.search(re.sub(r"\([^)]*\)|단\s*,.*", " ", s.heading)):
            out |= set(range(s.start, s.end))
    return out


def section_text(text: str, sec: Section, with_parent: list[Section] | None = None) -> str:
    lines = (text or "").split("\n")
    body = "\n".join(lines[sec.start:sec.end]).strip()
    if with_parent and sec.level == 2:
        parent = next((p for p in reversed(with_parent) if p.level == 1 and p.start < sec.start), None)
        if parent and parent.end >= sec.end and not re.search(r"허가사항|요양\s*급여|기준으로", parent.heading):
            body = parent.heading + "\n" + body
    return body


@dataclass
class Hit:
    phrase: str
    codes: list[str]
    line: int
    primary: bool             # 제목 줄·서두·목록 없는 본문


class PhraseIndex:
    def __init__(self, conn: sqlite3.Connection, schema: str = "main"):
        self.exists = {r[0] for r in conn.execute(f"SELECT code FROM {schema}.kcd_code")}
        self.name = {r[0]: r[1] for r in conn.execute(f"SELECT code, name_kr FROM {schema}.kcd_code")}
        phr: dict[str, list[str]] = {}
        for src in (CURATED, EXTRA_PHRASES):
            for p, codes in src.items():
                ok = [c for c in codes if c in self.exists]
                if ok and len(_n(p)) >= 2:
                    phr[_n(p)] = ok
        for p in MASK_PHRASES:
            phr[_n(p)] = []                       # 가리기 전용
        self.phrases = sorted(phr.items(), key=lambda x: -len(x[0]))

    def hits(self, text: str, extra: dict[str, list[str]] | None = None) -> tuple[list[Hit], list[str]]:
        """긴 표현 우선. 반환: (hits, 가린 뒤 남은 줄들 — 짧은 표현 재검사용)."""
        table = self.phrases
        if extra:
            table = sorted(dict(self.phrases, **{_n(k): v for k, v in extra.items() if len(_n(k)) >= 2}).items(),
                           key=lambda x: -len(x[0]))
        lines = (text or "").split("\n")
        neg = negative_lines(text)
        out: list[Hit] = []
        masked: list[str] = []
        for i, ln in enumerate(lines):
            nl = _n(ln)
            # 언급: 금기·제외 문단, 또는 이 줄/바로 앞 도입 문장(제목 줄까지 최대 2줄)이 위험요인·부작용 문맥
            ctx = [ln]
            k = i - 1
            while not HEAD_RE.match(ctx[0]) and k >= 0 and len(ctx) < 3:
                ctx.insert(0, lines[k])
                k -= 1
            ctx = re.sub(r"\([^)]*\)|단\s*,.*", " ", " ".join(ctx))     # 괄호·단서(단, …) 는 문맥 판단에서 뺀다
            primary = i not in neg and not MENTION_CTX.search(ctx)
            for p, codes in table:
                while p in nl:
                    pos = nl.index(p)
                    after = nl[pos + len(p):pos + len(p) + 10]
                    # '… 판상 건선에 투여 시' 처럼 바로 뒤가 투여 대상 표현이면 문맥(부작용 등)과 무관하게 급여 대상
                    strong = bool(re.match(r"^(\)|\(.*?\))?(에|에게|환자에게|환자에)(투여|사용)", after))
                    out.append(Hit(p, codes, i, primary or (strong and i not in neg)))
                    nl = nl[:pos] + "\u0000" * len(p) + nl[pos + len(p):]
            masked.append(nl)
        return out, masked


_GEN = {"증후군", "질환", "장애", "병", "기타", "상세불명", "상세불명의", "명시된", "달리", "분류된", "환자", "치료", "동반한",
        "의한", "인한", "관련", "급성", "만성", "중증", "증", "염"}


def _toks(s: str) -> set[str]:
    out = set()
    for w in re.split(r"[\s,·/\-]+", re.sub(r"\(.*?\)|\[.*?\]", "", s or "")):
        w = re.sub(r"(의|에|으로|로|성|한|된|인)$", "", w)
        if len(w) >= 2 and w not in _GEN:
            out.add(w)
    return out


def label_matches_code(label: str, name: str) -> bool:
    """원문 표현과 KCD 이름이 최소한 닮았는지(공통 단어 또는 의미 있는 2글자 2개 이상)."""
    if _toks(label) & _toks(name):
        return True
    bg = lambda s: {s[i:i + 2] for i in range(len(s) - 1)} - {"증후", "질환", "장애"}  # noqa: E731
    return len(bg(_n(label)) & bg(_n(name))) >= 2


def classify_item(text: str, rows: list[dict], pidx: PhraseIndex) -> tuple[list[dict], list[Section]]:
    """rows: 기존 항목 매핑(kcd_code, disease_label, method). 반환 (조정된 rows, 질환이 붙은 문단)."""
    # 기존 매핑의 원문 표현도 표현 사전에 (긴 표현 우선 규칙 안에서)
    extra = {}
    for r in rows:
        lab = re.sub(r"\(.*?\)|\[.*?\]", "", r.get("disease_label") or "").strip()
        if 2 <= len(_n(lab)) <= 20 and _n(lab) not in dict(pidx.phrases):
            extra[lab] = [r["kcd_code"]]
    hits, masked = pidx.hits(text, extra)
    secs = split_sections(text)
    neg = negative_lines(text)
    for s in secs:
        if s.start in neg:
            continue
        s.codes = sorted({c for h in hits if h.line == s.start for c in h.codes})
    by_group: dict[str, list[Hit]] = {}
    for h in hits:
        for c in h.codes:
            by_group.setdefault(gkey(c), []).append(h)
    out = []
    for r in rows:
        if r.get("method") == "manual":
            out.append(r)
            continue
        if (r.get("confidence") or 1) < 0.8 and r.get("method") == "llm" \
                and not label_matches_code(r.get("disease_label") or "", pidx.name.get(r["kcd_code"], "")):
            r["note"] = "rel:mismatch"            # 이름 검색 2차 후보에서 온, 표현과 닮지 않은 코드 → 삭제
            continue
        g = gkey(r["kcd_code"])
        hs = by_group.get(g)
        if hs:
            r["note"] = "rel:indication" if any(h.primary for h in hs) else "rel:mention"
            out.append(r)
            continue
        # 사전에 없는 질환: 원문 표현·KCD 이름의 핵심어가 가려지지 않은 채 남아 있는지
        raw_n, masked_n = _n(text), "".join(masked)
        found_free = found_masked = False
        for src in (r.get("disease_label") or "", pidx.name.get(r["kcd_code"], "")):
            base = re.sub(r"\(.*?\)|\[.*?\]|상세불명의?|기타|달리 분류된", "", src)
            for v in {_n(base), _n(re.sub(r"^\s*(제?\s*\d\s*형|만성|급성|중증의?|활동성)\s*", "", base))}:
                if len(v) < 2 or v not in raw_n:
                    continue
                if v in masked_n:
                    found_free = True
                else:
                    found_masked = True
        if found_masked and not found_free:
            r["note"] = "rel:contained"         # 다른 질환의 긴 표현 안에서만 나옴 → 삭제
            continue
        r["note"] = r.get("note") or "rel:indication"
        out.append(r)
    have = {gkey(r["kcd_code"]) for r in out}
    for s in secs:                              # 제목 줄에 나온 질환인데 매핑이 없던 것 추가
        for c in s.codes:
            if gkey(c) not in have and c in pidx.exists:
                out.append({"kcd_code": c, "disease_label": pidx.name.get(c, c), "method": "section",
                            "confidence": 0.9, "review_status": "auto", "note": "rel:indication"})
                have.add(gkey(c))
    return out, [s for s in secs if s.codes]


def refine(conn: sqlite3.Connection, kcd_schema: str = "main") -> dict:
    """스테이징(또는 본 DB) 의 drug_item 매핑을 문단 기준으로 정리하고 criteria_section_kcd 를 다시 만든다."""
    pidx = PhraseIndex(conn, kcd_schema)
    stats = {"items": 0, "added": 0, "removed": 0, "mention": 0, "sections": 0, "changes": []}
    conn.execute("DELETE FROM criteria_section_kcd")
    for it in conn.execute("SELECT item_key, criteria_text FROM drug_criteria_item").fetchall():
        key, text = it[0], it[1] or ""
        rows = [dict(r) for r in conn.execute(
            "SELECT kcd_code, disease_label, method, confidence, review_status, note FROM criteria_disease_kcd "
            "WHERE subject_type='drug_item' AND subject_key=?", (key,))]
        new, secs = classify_item(text, [dict(r) for r in rows], pidx)
        before = {r["kcd_code"] for r in rows}
        after = {r["kcd_code"] for r in new}
        removed, added = before - after, after - before
        stats["items"] += 1
        stats["removed"] += len(removed)
        stats["added"] += len(added)
        if removed or added:
            stats["changes"].append({"item": key, "removed": sorted(removed), "added": sorted(added)})
        conn.execute("DELETE FROM criteria_disease_kcd WHERE subject_type='drug_item' AND subject_key=? AND method!='manual'", (key,))
        for r in new:
            if r.get("method") == "manual":
                continue
            stats["mention"] += r.get("note") == "rel:mention"
            conn.execute("INSERT OR REPLACE INTO criteria_disease_kcd(subject_type,subject_key,kcd_code,disease_label,method,"
                         "confidence,review_status,note) VALUES ('drug_item',?,?,?,?,?,?,?)",
                         (key, r["kcd_code"], r.get("disease_label"), r["method"], r.get("confidence"),
                          r.get("review_status") or "auto", r.get("note")))
        for s in secs:
            for c in s.codes:
                conn.execute("INSERT OR IGNORE INTO criteria_section_kcd(subject_key, heading, level, kcd_code) VALUES (?,?,?,?)",
                             (key, s.heading, s.level, c))
                stats["sections"] += 1
    return stats
