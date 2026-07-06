"""뉴스 텍스트 → signal_type 휴리스틱 분류 + 가중치 — Access Insight S1 / B7.

분류 소스는 두 계층:
  1) `amjilsim_signature_lexicon` DB (큐레이션·priority 정렬·first-match) — 1차.
  2) 모듈 상수 `_KEYWORDS` (seed + fallback) — DB 미시딩/미접근 시.

B7 개선점
---------
- lexicon DB 로더(캐시, priority 오름차순, first-match). token 별 match_mode
  ('substring'|'word') 로 한국어 합성어 오탐(예: '의원'⊂'병의원') 방지.
- 오분류 콜리전 교정: '의원'→'국회의원'/'의원 발의', '통과'→'약평위 통과'/'급여
  통과', '실적/매출' 범위 축소, '교수/전문가'는 인용 맥락 한정, '예정' 약화.
- fallback 완화: 미매칭이 무조건 IR_RELEASE 로 몰리지 않도록 kind 기반 라우팅.
"""
from __future__ import annotations

import re
import sqlite3
import threading
from pathlib import Path
from typing import Optional, Union

PathLike = Union[str, Path]

BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = BASE_DIR / "data" / "db" / "drug_prices.db"

# signal_type enum (scripts/migrate_amjilsim_v1.py 기준)
PRE_AGENDA_LEAK = "PRE_AGENDA_LEAK"
QUEUE_INVENTORY = "QUEUE_INVENTORY"  # 백필 휴리스틱에서는 미사용 (S5 신선 크롤러 전용)
IR_RELEASE = "IR_RELEASE"
GOV_STATEMENT = "GOV_STATEMENT"
PATIENT_PETITION = "PATIENT_PETITION"
KOL_OPINION = "KOL_OPINION"
RESULT_REPORT = "RESULT_REPORT"
# 저신뢰 미분류 버킷 — signal_type CHECK enum 확장이 적용된 DB 에서만 쓴다.
# (확장 migration: scripts/migrate_amjilsim_v1.py ensure_signal_type_unclassified)
UNCLASSIFIED = "UNCLASSIFIED"

# 우선순위 순서 (특이도 높은 카테고리 먼저 매치). DB lexicon 미접근 시 fallback.
# S4 소스 확장 (국회 NATIONAL_ASSEMBLY · 환자단체 PATIENT_GROUP · 의료진/학회
# MEDICAL_SOCIETY seed) 기사가 fallback 이 아닌 의도한 유형으로 분류되도록 보강.
_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (PATIENT_PETITION, ("환자단체", "환우회", "청원", "탄원", "환자 접근성")),
    (KOL_OPINION, ("학회", "의료진", "전문가", "교수", "의사회", "의학회",
                   "진료지침", "전문의")),
    (GOV_STATEMENT, ("국회", "복지위", "보건복지위", "의원", "국정감사",
                     "법안", "발의")),
    (IR_RELEASE, ("ir", "실적", "컨퍼런스콜", "보도자료", "press release", "매출")),
    (RESULT_REPORT, ("암질심 결과", "약평위 결과", "급여 결정", "통과", "부결")),
    (PRE_AGENDA_LEAK, ("상정", "안건", "예정", "심의 예정")),
)

# gov_policy 아카이브 기사에 한해 GOV_STATEMENT 로 인정하는 기관 키워드 (fallback 경로).
_GOV_POLICY_AGENCY_KEYWORDS = ("복지부", "심평원", "공단", "건정심")


# ─────────────────────────────────────────────────────────────────────────────
# 큐레이션 seed lexicon — DB(amjilsim_signature_lexicon) 시딩용.
# 형식: (token, category, signal_type, weight, priority, match_mode, notes)
#   - priority 낮을수록 먼저 매치 (특이도 높은 토큰이 우선).
#   - match_mode='word' : token 이 다른 한글/영숫자에 붙어있지 않을 때만 매치
#     (합성어 내부 substring 오탐 방지 — '의원'⊂'병의원', 'ir'⊂'their').
# ─────────────────────────────────────────────────────────────────────────────
_SEED_LEXICON: tuple[tuple, ...] = (
    # RESULT_REPORT — 위원회 결과 (가장 특이도 높음, 최우선)
    ("암질심 결과", "committee_result", RESULT_REPORT, 1.5, 10, "substring", None),
    ("약평위 결과", "committee_result", RESULT_REPORT, 1.5, 10, "substring", None),
    ("급여 결정", "committee_result", RESULT_REPORT, 1.5, 12, "substring", None),
    ("약평위 통과", "committee_result", RESULT_REPORT, 1.5, 12,
     "substring", "구 '통과' 콜리전 교정 — 약평위 맥락 한정"),
    ("급여 통과", "committee_result", RESULT_REPORT, 1.5, 12,
     "substring", "구 '통과' 콜리전 교정 — 급여 맥락 한정"),
    ("암질심 통과", "committee_result", RESULT_REPORT, 1.5, 12, "substring", None),
    ("부결", "committee_result", RESULT_REPORT, 1.5, 14, "word", None),
    # PATIENT_PETITION — 환자단체
    ("환자단체", "patient", PATIENT_PETITION, 1.4, 20, "substring", None),
    ("환우회", "patient", PATIENT_PETITION, 1.4, 20, "substring", None),
    ("환자 접근성", "patient", PATIENT_PETITION, 1.4, 22, "substring", None),
    ("청원", "patient", PATIENT_PETITION, 1.4, 24, "word", None),
    ("탄원", "patient", PATIENT_PETITION, 1.4, 24, "word", None),
    # PRE_AGENDA_LEAK — 상정/안건 (예정 약화: 단독 '예정' 제거)
    ("심의 예정", "agenda", PRE_AGENDA_LEAK, 1.1, 30, "substring", None),
    ("상정 예정", "agenda", PRE_AGENDA_LEAK, 1.1, 30, "substring", None),
    ("안건 상정", "agenda", PRE_AGENDA_LEAK, 1.1, 32, "substring", None),
    ("상정", "agenda", PRE_AGENDA_LEAK, 1.1, 34, "word", "단독 '상정'"),
    ("안건", "agenda", PRE_AGENDA_LEAK, 1.1, 34, "word", "단독 '안건'"),
    # KOL_OPINION — 학회/의료진 (교수·전문가는 인용/발언 맥락 한정)
    ("학회", "kol", KOL_OPINION, 1.2, 40, "substring", None),
    ("의학회", "kol", KOL_OPINION, 1.2, 40, "substring", None),
    ("의사회", "kol", KOL_OPINION, 1.2, 40, "substring", None),
    ("진료지침", "kol", KOL_OPINION, 1.2, 40, "substring", None),
    ("전문의", "kol", KOL_OPINION, 1.2, 42, "word", None),
    ("의료진", "kol", KOL_OPINION, 1.2, 42, "substring", None),
    ("교수는", "kol", KOL_OPINION, 1.2, 44,
     "substring", "구 '교수' 콜리전 교정 — 발언 맥락 한정"),
    ("교수가", "kol", KOL_OPINION, 1.2, 44, "substring", "발언 맥락 한정"),
    ("교수 등", "kol", KOL_OPINION, 1.2, 44, "substring", "발언 맥락 한정"),
    ("전문가는", "kol", KOL_OPINION, 1.2, 44,
     "substring", "구 '전문가' 콜리전 교정 — 발언 맥락 한정"),
    ("전문가들", "kol", KOL_OPINION, 1.2, 44, "substring", "발언 맥락 한정"),
    # GOV_STATEMENT — 국회/복지부/심평원 (의원 콜리전 교정)
    ("국회의원", "gov", GOV_STATEMENT, 1.5, 50,
     "substring", "구 '의원' 콜리전 교정 — 병'의원' 오탐 제거"),
    ("의원 발의", "gov", GOV_STATEMENT, 1.5, 50,
     "substring", "구 '의원' 콜리전 교정 — 발의 맥락 한정"),
    ("국회", "gov", GOV_STATEMENT, 1.5, 52, "substring", None),
    ("복지위", "gov", GOV_STATEMENT, 1.5, 52, "substring", None),
    ("보건복지위", "gov", GOV_STATEMENT, 1.5, 52, "substring", None),
    ("국정감사", "gov", GOV_STATEMENT, 1.5, 52, "substring", None),
    ("법안", "gov", GOV_STATEMENT, 1.5, 54, "word", None),
    ("발의", "gov", GOV_STATEMENT, 1.5, 54, "word", None),
    ("복지부", "gov_agency", GOV_STATEMENT, 1.5, 56, "substring", None),
    ("심평원", "gov_agency", GOV_STATEMENT, 1.4, 56, "substring", None),
    ("건강보험공단", "gov_agency", GOV_STATEMENT, 1.4, 56, "substring", None),
    ("건정심", "gov_agency", GOV_STATEMENT, 1.5, 56, "substring", None),
    # IR_RELEASE — 기업 IR (최하위 우선순위, 실적/매출 범위 축소)
    ("컨퍼런스콜", "ir", IR_RELEASE, 0.8, 60, "substring", None),
    ("보도자료", "ir", IR_RELEASE, 0.8, 62, "substring", None),
    ("press release", "ir", IR_RELEASE, 0.8, 62, "substring", None),
    ("실적 발표", "ir", IR_RELEASE, 0.8, 62,
     "substring", "구 '실적' 범위 축소 — 발표 맥락 한정"),
    ("분기 실적", "ir", IR_RELEASE, 0.8, 62, "substring", "실적 범위 축소"),
    ("영업이익", "ir", IR_RELEASE, 0.8, 64, "substring", None),
    ("매출액", "ir", IR_RELEASE, 0.8, 64,
     "substring", "구 '매출' 범위 축소 — 매출'액' 한정"),
    ("ir", "ir", IR_RELEASE, 0.8, 66, "word", "영문 단어경계 (their 등 오탐 방지)"),
)


# ─────────────────────────────────────────────────────────────────────────────
# lexicon 스키마 보강 (멱등) + seed
# ─────────────────────────────────────────────────────────────────────────────
_LEXICON_ADD_COLUMNS = (
    ("priority", "ALTER TABLE amjilsim_signature_lexicon ADD COLUMN priority INTEGER DEFAULT 100"),
    ("is_active", "ALTER TABLE amjilsim_signature_lexicon ADD COLUMN is_active INTEGER DEFAULT 1"),
    ("match_mode", "ALTER TABLE amjilsim_signature_lexicon ADD COLUMN match_mode TEXT DEFAULT 'substring'"),
)


def ensure_lexicon_schema(conn: sqlite3.Connection) -> None:
    """amjilsim_signature_lexicon 존재 보장 + priority/is_active/match_mode 컬럼 멱등 추가."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS amjilsim_signature_lexicon (
            token               TEXT PRIMARY KEY,
            category            TEXT NOT NULL,
            signal_type         TEXT,
            weight              REAL NOT NULL DEFAULT 1.0,
            preferred_outlets   TEXT,
            last_calibrated_at  TEXT,
            notes               TEXT
        )
        """
    )
    existing = {r[1] for r in conn.execute("PRAGMA table_info(amjilsim_signature_lexicon)")}
    for col, ddl in _LEXICON_ADD_COLUMNS:
        if col not in existing:
            conn.execute(ddl)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_lex_active_priority "
        "ON amjilsim_signature_lexicon(is_active, priority)"
    )


def seed_lexicon(conn: sqlite3.Connection, *, overwrite: bool = False) -> int:
    """큐레이션 seed 를 lexicon 에 적재. overwrite=False 면 INSERT OR IGNORE (기존 편집 보존)."""
    ensure_lexicon_schema(conn)
    verb = "INSERT OR REPLACE" if overwrite else "INSERT OR IGNORE"
    n = 0
    for token, category, signal_type, weight, priority, match_mode, notes in _SEED_LEXICON:
        cur = conn.execute(
            f"{verb} INTO amjilsim_signature_lexicon "
            "(token, category, signal_type, weight, priority, is_active, match_mode, "
            " last_calibrated_at, notes) "
            "VALUES (?,?,?,?,?,1,?, datetime('now'), ?)",
            (token, category, signal_type, weight, priority, match_mode, notes),
        )
        n += cur.rowcount
    conn.commit()
    return n


# ─────────────────────────────────────────────────────────────────────────────
# lexicon 로더 (캐시) — priority 오름차순 정렬 first-match
# ─────────────────────────────────────────────────────────────────────────────
_CACHE_LOCK = threading.Lock()
_LEXICON_CACHE: dict[str, list[dict]] = {}


def invalidate_lexicon_cache(db_path: Optional[PathLike] = None) -> None:
    """lexicon 캐시 무효화 (admin CRUD write 후 호출)."""
    with _CACHE_LOCK:
        if db_path is None:
            _LEXICON_CACHE.clear()
        else:
            _LEXICON_CACHE.pop(str(db_path), None)


def load_lexicon(db_path: Optional[PathLike] = None) -> list[dict]:
    """활성 lexicon 을 priority 오름차순으로 로드 (캐시). 비었으면 빈 리스트.

    반환 각 항목: {token, signal_type, weight, priority, match_mode}
    """
    path = str(db_path or DEFAULT_DB_PATH)
    with _CACHE_LOCK:
        cached = _LEXICON_CACHE.get(path)
    if cached is not None:
        return cached

    entries: list[dict] = []
    try:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT token, signal_type, weight, "
                "COALESCE(priority, 100) AS priority, "
                "COALESCE(match_mode, 'substring') AS match_mode "
                "FROM amjilsim_signature_lexicon "
                "WHERE COALESCE(is_active, 1) = 1 AND signal_type IS NOT NULL "
                "ORDER BY COALESCE(priority, 100) ASC, token ASC"
            ).fetchall()
            entries = [
                {
                    "token": r["token"],
                    "signal_type": r["signal_type"],
                    "weight": r["weight"],
                    "priority": r["priority"],
                    "match_mode": r["match_mode"],
                }
                for r in rows
            ]
        finally:
            conn.close()
    except sqlite3.Error:
        entries = []

    with _CACHE_LOCK:
        _LEXICON_CACHE[path] = entries
    return entries


_WORD_BOUNDARY = r"(?<![0-9A-Za-z가-힣]){token}(?![0-9A-Za-z가-힣])"


def _token_matches(token: str, mode: str, text_lower: str) -> bool:
    tok = token.lower()
    if mode == "word":
        pattern = _WORD_BOUNDARY.format(token=re.escape(tok))
        return re.search(pattern, text_lower) is not None
    return tok in text_lower


def classify_signal_type(
    title: str,
    snippet: str,
    kind: str,
    lexicon: Optional[list[dict]] = None,
    unclassified_ok: bool = False,
) -> tuple[str, list[str]]:
    """(signal_type, matched_phrases) 반환.

    lexicon 주어지면 (또는 DB 시딩되어 load_lexicon() 이 비어있지 않으면) DB 기반
    priority first-match. 없으면 모듈 `_KEYWORDS` fallback. 미매칭은 blanket
    IR_RELEASE 가 아니라 kind 기반으로 라우팅한다.

    unclassified_ok=True 면 (signal_type CHECK 에 UNCLASSIFIED 가 허용된 DB) 미매칭
    비-gov 신호를 IR_RELEASE 가 아닌 저신뢰 UNCLASSIFIED 로 라우팅한다.
    """
    text = f"{title or ''} {snippet or ''}"
    lowered = text.lower()

    lex = lexicon if lexicon is not None else load_lexicon()
    if lex:
        for entry in lex:  # 이미 priority 오름차순
            if _token_matches(entry["token"], entry["match_mode"], lowered):
                return entry["signal_type"], [entry["token"]]
        return _fallback(kind, lowered, unclassified_ok)

    # ── 모듈 상수 fallback (DB 미시딩/테스트) ──
    for signal_type, keywords in _KEYWORDS:
        matched = [kw for kw in keywords if kw.lower() in lowered]
        if signal_type == GOV_STATEMENT and kind == "gov_policy":
            matched = matched + [
                kw for kw in _GOV_POLICY_AGENCY_KEYWORDS if kw.lower() in lowered
            ]
        if matched:
            return signal_type, matched
    return _fallback(kind, lowered, unclassified_ok)


def _fallback(kind: str, lowered: str, unclassified_ok: bool = False) -> tuple[str, list[str]]:
    """미매칭 라우팅 — blanket IR_RELEASE 완화.

    - gov_policy 아카이브: 기관 키워드 있으면 GOV_STATEMENT, 없어도 GOV_STATEMENT
      (정부 위젯 소스이므로).
    - 그 외(competitor/msd_asset): DB 가 UNCLASSIFIED enum 을 허용하면 저신뢰
      UNCLASSIFIED, 아니면 IR_RELEASE(하위호환). — signal_type CHECK 확장은
      controller 가 ensure_signal_type_unclassified() 로 opt-in.
    """
    if kind == "gov_policy":
        agency = [kw for kw in _GOV_POLICY_AGENCY_KEYWORDS if kw in lowered]
        return GOV_STATEMENT, agency
    return (UNCLASSIFIED, []) if unclassified_ok else (IR_RELEASE, [])


def unclassified_allowed(conn: sqlite3.Connection) -> bool:
    """amjilsim_media_signals.signal_type CHECK 에 UNCLASSIFIED 가 허용돼 있는지."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='amjilsim_media_signals'"
    ).fetchone()
    return bool(row and row[0] and "UNCLASSIFIED" in row[0])


# 신호 유형별 기본 가중치 — "공식성이 높을수록 무겁게" (GOV/RESULT/PATIENT 상향, IR 하향).
_TYPE_WEIGHT: dict[str, float] = {
    GOV_STATEMENT: 1.5,
    RESULT_REPORT: 1.5,
    PATIENT_PETITION: 1.4,
    KOL_OPINION: 1.2,
    PRE_AGENDA_LEAK: 1.1,
    QUEUE_INVENTORY: 1.0,
    IR_RELEASE: 0.8,
}

# 매체 tier 배율 — 'D'(미등록/미분류 매체) 가 기본값.
_TIER_MULTIPLIER: dict[str, float] = {
    "A": 1.2,
    "B": 1.0,
    "C": 0.9,
    "D": 0.7,
}


def signal_weight(tier: str = "D", signal_type: str = "") -> float:
    """(tier, signal_type) → weight. 미지정/미매핑 값은 각각 default(1.0/'D') 로 수렴."""
    base = _TYPE_WEIGHT.get(signal_type, 1.0)
    mult = _TIER_MULTIPLIER.get((tier or "D").upper(), _TIER_MULTIPLIER["D"])
    return round(base * mult, 3)


# ─────────────────────────────────────────────────────────────────────────────
# 재분류 잡 (INSERT-only 예외 — 명시적 UPDATE, 삭제 없음)
# ─────────────────────────────────────────────────────────────────────────────
def reclassify_signals(db_path: Optional[PathLike] = None) -> dict:
    """현재 lexicon 으로 기존 amjilsim_media_signals 행의 signal_type/weight/
    signal_phrases 를 UPDATE (삭제 없음). BEFORE/AFTER 분포 통계 반환.

    media_signals 에는 kind 컬럼이 없으므로 원본 competitor_news(url join)에서 kind 를
    복원해 kind 기반 fallback(gov_policy→GOV_STATEMENT)을 보존한다. 매칭 안 되는
    신선크롤 신호는 kind='' → IR_RELEASE fallback.
    """
    import json

    path = str(db_path or DEFAULT_DB_PATH)

    # ⚠️ lexicon 미시딩 DB 에서 재분류하면 구 collision-laden _KEYWORDS 상수로
    # 폴백해 전체 목적이 무너진다 → 재분류 전에 스키마+seed 를 멱등 보장한다.
    # seed_lexicon 은 INSERT OR IGNORE 이므로 기존 편집을 덮지 않고 빈 항목만 채운다.
    _seed_conn = sqlite3.connect(path)
    try:
        ensure_lexicon_schema(_seed_conn)
        seed_lexicon(_seed_conn)
    finally:
        _seed_conn.close()

    invalidate_lexicon_cache(path)
    lex = load_lexicon(path)

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        # url → kind 복원 (competitor_news 존재 시). 없으면 빈 매핑.
        url_kind: dict[str, str] = {}
        try:
            for r in conn.execute("SELECT url, kind FROM competitor_news"):
                if r["url"]:
                    url_kind[r["url"]] = r["kind"] or ""
        except sqlite3.Error:
            url_kind = {}

        unc_ok = unclassified_allowed(conn)

        before: dict[str, int] = {}
        for r in conn.execute(
            "SELECT signal_type, COUNT(*) c FROM amjilsim_media_signals GROUP BY signal_type"
        ):
            before[r["signal_type"] or "NULL"] = r["c"]

        rows = conn.execute(
            "SELECT id, tier, title, snippet, url, signal_type FROM amjilsim_media_signals"
        ).fetchall()
        changed = 0
        for r in rows:
            kind = url_kind.get(r["url"], "")
            new_type, phrases = classify_signal_type(
                r["title"] or "", r["snippet"] or "", kind, lexicon=lex, unclassified_ok=unc_ok
            )
            new_weight = signal_weight((r["tier"] or "D"), new_type)
            conn.execute(
                "UPDATE amjilsim_media_signals SET signal_type=?, weight=?, signal_phrases=? "
                "WHERE id=?",
                (new_type, new_weight, json.dumps(phrases, ensure_ascii=False), r["id"]),
            )
            if new_type != (r["signal_type"] or ""):
                changed += 1
        conn.commit()

        after: dict[str, int] = {}
        for r in conn.execute(
            "SELECT signal_type, COUNT(*) c FROM amjilsim_media_signals GROUP BY signal_type"
        ):
            after[r["signal_type"] or "NULL"] = r["c"]
    finally:
        conn.close()

    return {
        "total": len(rows),
        "changed": changed,
        "unclassified_enabled": unc_ok,
        "before": before,
        "after": after,
    }
