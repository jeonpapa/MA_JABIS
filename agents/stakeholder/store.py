"""Stakeholder & Verbatim Intelligence 저장소 — sv_* 테이블 (독립 store, agents/db/base.py 무접촉).

소유권: sv_person / sv_person_role_history / sv_source_domain / sv_audit_proposal(+event) 는 **대쉬보드가 기록 원본**.
sync(agents/stakeholder/sync.py) 는 이 네 테이블에 insert-if-absent 만 한다. 나머지(sv_source·sv_artifact·sv_event·
sv_statement·sv_quote·sv_monitoring_run·sv_audit_run)는 외부 에이전트 산출물의 미러(origin 으로 구분).
쓰기는 transaction() 안에서만 — BEGIN IMMEDIATE 가 소스의 파일 잠금+저널을 대체한다.

호출 규약
- 모든 공개 함수는 `db_path=` 를 받는다. 이미 연 트랜잭션 안에서 부르려면 `conn=` 을 넘긴다(그 연결의 트랜잭션에 합류,
  커밋은 호출자 몫). 외부 트랜잭션을 쥔 채 `db_path=` 로 쓰기 함수를 부르면 같은 파일에 두 번째 쓰기 잠금을 기다리다
  busy_timeout(5s) 뒤 실패한다 — 반드시 `conn=` 을 넘길 것.
- `*_row(conn, ...)` 는 호출자 트랜잭션 전용 엄격 삽입(중복 → ConflictError). `*_if_absent` 는 sync/import 전용(기존 행 보존).
- 모듈 import 는 DB 를 열지 않는다. 테이블은 첫 호출에서 ensure_tables() 로 지연 생성된다.
- 오류: ValidationError(422) · ConflictError(409) · NotFoundError(404). 메시지는 소스 stakeholder_service.py 문구를 유지한다.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "db" / "drug_prices.db"
BUSY_TIMEOUT_SECONDS = 5
KST = timezone(timedelta(hours=9))

PERSON_STATUSES = ("active", "paused")
VERIFICATION_STATUSES = ("verified", "candidate")
PERSON_ORIGINS = ("import", "dashboard", "audit")
SOURCE_GROUPS = ("official", "top_tier_general", "healthcare_trade", "discovered_additional")
SOURCE_DOMAIN_STATUSES = ("approved", "retired")
PROPOSAL_TYPES = ("source_expansion", "role_change", "new_person")
PRIORITIES = ("urgent", "high", "normal", "low")
PROPOSAL_STATUSES = ("needs_review", "needs_evidence", "approved", "scheduled", "applied", "rejected", "superseded",
                     "application_failed")
OPEN_PROPOSAL_STATUSES = ("needs_review", "needs_evidence")
DEDUPE_BLOCKING_STATUSES = ("needs_review", "needs_evidence", "scheduled")   # 소스 create_audit_proposal 의 409 기준
PROPOSAL_ORIGINS = ("import", "agent_api", "dashboard")
ROLE_HISTORY_STATUSES = ("scheduled", "applied", "superseded", "failed")   # failed: 반영 실패로 대기 해제(T2b)
ORIGINS = ("import", "dashboard", "audit", "agent_api", "canonical_md")
EVIDENCE_GRADES = ("A", "B-quote", "B-summary", "C-quote", "C-summary", "Role-only", "Candidate-only")
INTEGRITY_STATUSES = ("verified", "mismatch", "missing", "no_local", "unchecked")
LOCATOR_MATCHES = ("range", "file", "none", "unchecked")
RUN_KINDS = ("monitoring", "audit")
DEFAULT_TOPIC_TAGS = ["약가", "건강보험 재정"]   # 소스 PersonInput 기본값

TABLES = ("sv_meta", "sv_person", "sv_person_role_history", "sv_source_domain", "sv_source", "sv_artifact", "sv_event",
          "sv_statement", "sv_quote", "sv_monitoring_run", "sv_audit_run", "sv_audit_proposal",
          "sv_audit_proposal_event", "sv_audit_conflict_ack", "sv_sync_file")


class StoreError(Exception):
    status = 500

    def __init__(self, message: str, code: str = "STORE"):
        super().__init__(message)
        self.message, self.code = message, code


class ValidationError(StoreError):
    """422 — 입력 형식·어휘 위반."""
    status = 422


class ConflictError(StoreError):
    """409 — 중복·상태 불일치."""
    status = 409


class NotFoundError(StoreError):
    """404."""
    status = 404


# 직책 이력 DDL — SCHEMA_SQL 과 CHECK 어휘 마이그레이션(_migrate_role_history)이 같은 문자열을 쓴다.
_ROLE_HISTORY_DDL = """CREATE TABLE IF NOT EXISTS {name} (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id      TEXT NOT NULL,
    organization   TEXT, department TEXT, role TEXT,
    effective_from TEXT, effective_to TEXT,
    status         TEXT NOT NULL CHECK (status IN ('scheduled','applied','superseded','failed')),
    proposal_id    TEXT, evidence_json TEXT, approved_at TEXT, applied_at TEXT
);"""
_ROLE_HISTORY_INDEX = ("CREATE INDEX IF NOT EXISTS idx_sv_role_hist_person "
                       "ON sv_person_role_history(person_id, status);")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sv_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS sv_person (
    person_id            TEXT PRIMARY KEY,
    name                 TEXT NOT NULL,
    organization         TEXT NOT NULL,
    organization_code    TEXT,
    department           TEXT NOT NULL,
    role                 TEXT NOT NULL,
    status               TEXT NOT NULL CHECK (status IN ('active','paused')),
    verification_status  TEXT NOT NULL CHECK (verification_status IN ('verified','candidate')),
    effective_from       TEXT NOT NULL,
    aliases_json         TEXT NOT NULL DEFAULT '[]',
    topic_tags_json      TEXT NOT NULL DEFAULT '[]',
    photo_url            TEXT,            -- 소스 메타(언론 CDN). UI 는 렌더링하지 않는다
    photo_path           TEXT,            -- 볼륨 photos/<file>.webp (admin 업로드)
    importance_score     INTEGER,         -- 부서-인물지도 §2.2 중요도(1~5) — sync 소유 컬럼
    importance_json      TEXT,            -- 구성점수 원본 — sync 소유 컬럼
    discovered_from_proposal TEXT,
    discovery_evidence_json  TEXT,
    origin               TEXT NOT NULL CHECK (origin IN ('import','dashboard','audit')),
    updated_by           TEXT,            -- 마지막 대쉬보드 수정자(admin 이메일). import 행은 NULL
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    UNIQUE (name, organization)
);
{role_history_ddl}

CREATE TABLE IF NOT EXISTS sv_source_domain (
    source_id      TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    domain         TEXT NOT NULL UNIQUE,
    source_group   TEXT NOT NULL CHECK (source_group IN ('official','top_tier_general','healthcare_trade','discovered_additional')),
    status         TEXT NOT NULL CHECK (status IN ('approved','retired')),
    effective_from TEXT NOT NULL,
    approved_from_proposal TEXT, evidence_json TEXT,
    origin         TEXT NOT NULL CHECK (origin IN ('import','dashboard','audit')),
    created_at     TEXT NOT NULL, updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sv_source (                 -- source_manifest.json .sources[] 미러
    source_id        TEXT PRIMARY KEY,
    title TEXT, url TEXT, source_type TEXT, evidence_grade TEXT, access_status TEXT,
    local_path TEXT, sha256 TEXT, text_path TEXT, text_sha256 TEXT, bytes INTEGER,
    event_date TEXT, publication_date TEXT, publication_date_basis TEXT,
    locator TEXT, limitation TEXT, scope_status TEXT, scope_note TEXT, video_limitation TEXT,
    evidence_facets_json TEXT, official_video_json TEXT, last_live_check_json TEXT, known_timestamp TEXT,
    raw_json TEXT,                                     -- manifest 항목 원문 그대로 (미지 키 포함 — 손실 없음)
    integrity_status TEXT NOT NULL DEFAULT 'unchecked'
        CHECK (integrity_status IN ('verified','mismatch','missing','no_local','unchecked')),
    integrity_checked_at TEXT, manifest_sha256 TEXT, imported_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sv_artifact (               -- source_manifest.json .artifacts[] 미러
    path TEXT PRIMARY KEY, name TEXT, description TEXT, sha256 TEXT, bytes INTEGER, raw_json TEXT,
    integrity_status TEXT NOT NULL DEFAULT 'unchecked'
        CHECK (integrity_status IN ('verified','mismatch','missing','no_local','unchecked')),
    integrity_checked_at TEXT, imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sv_event (
    event_id TEXT PRIMARY KEY, event_date TEXT NOT NULL, title TEXT NOT NULL,
    context TEXT, publication_dates_json TEXT, topics_json TEXT, speakers_json TEXT, source_ids_json TEXT,
    grades_json TEXT, origin TEXT NOT NULL DEFAULT 'canonical_md', canonical_sha256 TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sv_statement (              -- (행사 × 발언자 × 주제) 블록: 요약·해석·한계·등급·근거
    statement_id TEXT PRIMARY KEY, event_id TEXT NOT NULL, topic TEXT NOT NULL,
    speaker_name TEXT NOT NULL, speaker_role_at_event TEXT, person_id TEXT, multi_speaker INTEGER NOT NULL DEFAULT 0,
    summary_text TEXT, analysis_text TEXT, limitation_text TEXT, policy_status TEXT,
    evidence_grade TEXT NOT NULL, locators_json TEXT, source_ids_json TEXT, source_url TEXT, role_check_url TEXT,
    publication_date TEXT, headline_quote_id TEXT,
    origin TEXT NOT NULL DEFAULT 'canonical_md', canonical_sha256 TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sv_statement_event ON sv_statement(event_id, topic);
CREATE INDEX IF NOT EXISTS idx_sv_statement_person ON sv_statement(person_id);
CREATE TABLE IF NOT EXISTS sv_quote (                  -- 인용 블록 1개 = 1행(블록 전체 = quote_text, `[…]` 조각 = segments_json). 원문 그대로. (행사, 발언자, 정규화 문구) 유일
    quote_id TEXT PRIMARY KEY, event_id TEXT NOT NULL, speaker_name TEXT NOT NULL, person_id TEXT,
    quote_text TEXT NOT NULL, quote_norm TEXT NOT NULL, segments_json TEXT NOT NULL DEFAULT '[]',
    statement_ids_json TEXT NOT NULL, topics_json TEXT NOT NULL,
    source_id TEXT, locator TEXT,                      -- 첫 블록의 locator (`sources/<f>.txt:a-b`)
    locators_json TEXT NOT NULL DEFAULT '[]',          -- 인용이 나온 모든 블록의 locator [{path,start,end}]
    locator_match TEXT NOT NULL DEFAULT 'unchecked'
        CHECK (locator_match IN ('range','file','none','unchecked')),
    evidence_grade TEXT, origin TEXT NOT NULL DEFAULT 'canonical_md', canonical_sha256 TEXT, created_at TEXT NOT NULL,
    UNIQUE (event_id, speaker_name, quote_norm)
);

CREATE TABLE IF NOT EXISTS sv_monitoring_run (
    run_id TEXT PRIMARY KEY, run_date TEXT NOT NULL, executed_at TEXT, window_start TEXT, window_end TEXT,
    run_status TEXT, scope_snapshot_id TEXT, people_registry_sha256 TEXT, source_registry_sha256 TEXT,
    active_people INTEGER, approved_domains INTEGER, new_quotes INTEGER, saturation_status TEXT,
    payload_json TEXT NOT NULL, md_path TEXT, raw_dir TEXT, payload_sha256 TEXT NOT NULL, imported_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sv_audit_run (
    audit_run_id TEXT PRIMARY KEY, run_date TEXT NOT NULL, executed_at TEXT, completed_at TEXT, run_status TEXT,
    claim TEXT, why_this_matters TEXT, proposals_created INTEGER,
    payload_json TEXT NOT NULL, md_path TEXT, raw_dir TEXT, payload_sha256 TEXT NOT NULL, imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sv_audit_proposal (
    proposal_id TEXT PRIMARY KEY,
    proposal_type TEXT NOT NULL CHECK (proposal_type IN ('source_expansion','role_change','new_person')),
    dedupe_key TEXT NOT NULL,
    priority TEXT NOT NULL CHECK (priority IN ('urgent','high','normal','low')),
    summary TEXT NOT NULL, rationale TEXT NOT NULL,
    proposed_change_json TEXT NOT NULL, evidence_json TEXT NOT NULL DEFAULT '[]', conflicts_json TEXT NOT NULL DEFAULT '[]',
    audit_run_id TEXT, created_by TEXT,
    status TEXT NOT NULL CHECK (status IN ('needs_review','needs_evidence','approved','scheduled','applied','rejected',
                                           'superseded','application_failed')),
    effective_from TEXT, reviewer TEXT, reviewed_at TEXT, decision_reason TEXT, applied_at TEXT,
    origin TEXT NOT NULL CHECK (origin IN ('import','agent_api','dashboard')),
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
-- 열린 제안은 dedupe_key 당 1건 (소스 create_audit_proposal 의 409 규칙을 DB 가 강제)
CREATE UNIQUE INDEX IF NOT EXISTS idx_sv_proposal_open_dedupe ON sv_audit_proposal(dedupe_key)
    WHERE status IN ('needs_review','needs_evidence','scheduled');
CREATE INDEX IF NOT EXISTS idx_sv_proposal_run ON sv_audit_proposal(audit_run_id);
CREATE TABLE IF NOT EXISTS sv_audit_proposal_event (   -- append-only 감사 기록 (service_request_event 선례)
    id INTEGER PRIMARY KEY AUTOINCREMENT, proposal_id TEXT NOT NULL, actor TEXT NOT NULL, event TEXT NOT NULL,
    detail TEXT, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sv_proposal_event ON sv_audit_proposal_event(proposal_id, id);
CREATE TABLE IF NOT EXISTS sv_audit_conflict_ack (     -- 충돌별 확인(T2b, 사용자 결정 2026-10-12). 승인 전 모든 index 필요
    proposal_id     TEXT NOT NULL,
    conflict_index  INTEGER NOT NULL CHECK (conflict_index >= 0),
    conflict_text   TEXT NOT NULL,                     -- 확인 당시 문구 스냅숏 — 지금 문구와 다르면 그 확인은 무효
    reason          TEXT NOT NULL,
    acknowledged_by TEXT NOT NULL,
    acknowledged_at TEXT NOT NULL,
    PRIMARY KEY (proposal_id, conflict_index)
);
CREATE TABLE IF NOT EXISTS sv_sync_file (              -- 볼륨 미러 색인: 경로 → git blob sha / sha256
    path TEXT PRIMARY KEY, blob_sha TEXT NOT NULL, sha256 TEXT, bytes INTEGER, synced_at TEXT NOT NULL
);
""".replace("{role_history_ddl}", _ROLE_HISTORY_DDL.format(name="sv_person_role_history") + "\n" + _ROLE_HISTORY_INDEX)


# ── 시간 ─────────────────────────────────────────────────────────────────────────

def now_kst() -> datetime:
    return datetime.now(KST)


def today_kst() -> date:
    return now_kst().date()


def tomorrow_kst() -> str:
    """효력일(익일). today_kst 를 호출 시점에 찾으므로 테스트의 monkeypatch 가 먹는다."""
    return (today_kst() + timedelta(days=1)).isoformat()


def _ts() -> str:
    return now_kst().isoformat(timespec="seconds")


# ── 연결·트랜잭션 ─────────────────────────────────────────────────────────────────

_ENSURED: set[str] = set()
_ENSURE_LOCK = threading.Lock()


def connect(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """autocommit 연결(isolation_level=None) — 트랜잭션은 transaction() 이 명시적으로 연다."""
    conn = sqlite3.connect(str(db_path), timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_SECONDS * 1000}")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def ensure_tables(db_path: str | Path = DEFAULT_DB_PATH) -> None:
    """sv_* 스키마 멱등 생성. 프로세스 안에서 경로당 1회만 DDL 을 돈다(파일이 사라지면 다시)."""
    p = Path(db_path)
    key = str(p.resolve())
    if key in _ENSURED and p.exists():
        return
    with _ENSURE_LOCK:
        conn = connect(p)
        try:
            conn.executescript(SCHEMA_SQL)
            _migrate_role_history(conn)
        finally:
            conn.close()
        _ENSURED.add(key)


def _migrate_role_history(conn: sqlite3.Connection) -> None:
    """T1 스키마(CHECK 에 'failed' 없음)로 만들어진 sv_person_role_history 를 행·id 보존한 채 재구성. 멱등.

    SQLite 는 CHECK 를 ALTER 할 수 없으므로 새 테이블 생성 → 복사 → 교체 → 인덱스 재생성을 한 BEGIN IMMEDIATE 로."""
    def stale() -> bool:
        r = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='sv_person_role_history'").fetchone()
        return r is not None and "'failed'" not in (r[0] or "")

    if not stale():
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        if stale():                                    # 다른 프로세스가 먼저 끝냈을 수 있다
            cols = ",".join(("id",) + _ROLE_HIST_COLS)
            conn.execute("DROP TABLE IF EXISTS sv_person_role_history__new")
            conn.execute(_ROLE_HISTORY_DDL.format(name="sv_person_role_history__new"))
            conn.execute(f"INSERT INTO sv_person_role_history__new ({cols}) SELECT {cols} FROM sv_person_role_history")
            conn.execute("DROP TABLE sv_person_role_history")
            conn.execute("ALTER TABLE sv_person_role_history__new RENAME TO sv_person_role_history")
            conn.execute(_ROLE_HISTORY_INDEX)
            logger.info("sv_person_role_history: status CHECK 에 'failed' 추가(테이블 재구성)")
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


@contextmanager
def transaction(db_path: str | Path = DEFAULT_DB_PATH) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE … COMMIT. 예외면 ROLLBACK. 소스의 flock+저널+복구를 대체한다."""
    ensure_tables(db_path)
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


@contextmanager
def _reader(conn: sqlite3.Connection | None, db_path) -> Iterator[sqlite3.Connection]:
    if conn is not None:
        yield conn
        return
    ensure_tables(db_path)
    c = connect(db_path)
    try:
        yield c
    finally:
        c.close()


@contextmanager
def _writer(conn: sqlite3.Connection | None, db_path) -> Iterator[sqlite3.Connection]:
    if conn is not None:
        yield conn
        return
    with transaction(db_path) as c:
        yield c


# ── 직렬화 유틸 ───────────────────────────────────────────────────────────────────

def _j(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False)


def _jl(s: str | None) -> list:
    try:
        v = json.loads(s or "[]")
        return v if isinstance(v, list) else []
    except (TypeError, ValueError):
        return []


def _jo(s: str | None) -> Any:
    if s is None:
        return None
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return None


def _jopt(v: Any) -> str | None:
    return None if v is None else _j(v)


def _sha256_text(s: str | bytes) -> str:
    return hashlib.sha256(s if isinstance(s, bytes) else s.encode("utf-8")).hexdigest()


def _as_dict(obj: Any) -> dict:
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    return dict(obj)


def _check(value: Any, allowed: Iterable[str], field: str) -> None:
    if value not in tuple(allowed):
        raise ValidationError(f"{field} 값이 올바르지 않습니다: {value!r}", f"BAD_{field.upper()}")


def _str_list(v: Any, field: str) -> list[str]:
    if v is None:
        return []
    if not isinstance(v, (list, tuple)):
        raise ValidationError(f"{field} 는 문자열 목록이어야 합니다.", f"BAD_{field.upper()}")
    return [" ".join(str(x).split()) for x in v if str(x).strip()]


def _statuses(status: str | Iterable[str] | None) -> tuple[str, ...] | None:
    if status is None:
        return None
    return (status,) if isinstance(status, str) else tuple(status)


def norm_quote(s: str) -> str:
    """정본 파서와 같은 정규화(공백 전부 + “”‘’\"' 제거). 파서가 quote_norm 을 주지 않을 때만 쓴다."""
    return re.sub(r"[\s“”‘’\"']", "", s or "")


# ── 인물 ─────────────────────────────────────────────────────────────────────────

_PERSON_COLS = ("person_id", "name", "organization", "organization_code", "department", "role", "status",
                "verification_status", "effective_from", "aliases_json", "topic_tags_json", "photo_url", "photo_path",
                "discovered_from_proposal", "discovery_evidence_json", "origin", "updated_by", "created_at", "updated_at")
# PersonInput(소스 pydantic) 길이 규칙
_PERSON_TEXT_LIMITS = {"name": (2, 30), "organization": (2, 80), "department": (1, 120), "role": (1, 80)}
_PERSON_TEXT_LABELS = {"name": "이름", "organization": "기관", "department": "부서", "role": "직책"}


def person_row_to_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["aliases"] = _jl(d.pop("aliases_json", None))
    d["topic_tags"] = _jl(d.pop("topic_tags_json", None))
    de = d.pop("discovery_evidence_json", None)
    d["discovery_evidence"] = _jo(de) if de else None
    imp = d.pop("importance_json", None)
    d["importance"] = _jo(imp) if imp else None
    return d


def validate_person_input(data: dict, *, current: dict | None = None) -> dict:
    """소스 PersonInput 과 같은 규칙: 공백 정규화, 길이, status 어휘. 반환은 정리된 입력(verification_status 제외).

    current(수정 대상 기존 행)가 있으면 **값이 그대로인 텍스트 필드는 길이 규칙을 면제**한다 — import 된 기존 값이
    상태·별칭 같은 다른 편집을 막지 않게(빈 값은 여전히 거부)."""
    out: dict[str, Any] = {}
    for field, (lo, hi) in _PERSON_TEXT_LIMITS.items():
        value = " ".join(str(data.get(field) or "").split())
        if current is not None and value and value == current.get(field):
            out[field] = value
            continue
        if not lo <= len(value) <= hi:
            raise ValidationError(f"{_PERSON_TEXT_LABELS[field]} 길이는 {lo}~{hi}자여야 합니다.", f"BAD_{field.upper()}")
        out[field] = value
    status = data.get("status") or "active"
    if status not in PERSON_STATUSES:
        raise ValidationError("status는 active 또는 paused여야 합니다.", "BAD_STATUS")
    out["status"] = status
    out["aliases"] = _str_list(data.get("aliases"), "aliases")
    out["topic_tags"] = (_str_list(data.get("topic_tags"), "topic_tags") if "topic_tags" in data
                         else list(DEFAULT_TOPIC_TAGS))
    return out


def _person_values(p: dict, origin: str) -> tuple:
    ts = _ts()
    return (p["person_id"], p["name"], p["organization"], p.get("organization_code"), p["department"], p["role"],
            p["status"], p["verification_status"], p["effective_from"], _j(p.get("aliases") or []),
            _j(p.get("topic_tags") or []), p.get("photo_url"), p.get("photo_path"), p.get("discovered_from_proposal"),
            _j(p["discovery_evidence"]) if p.get("discovery_evidence") else None, origin, p.get("updated_by"),
            p.get("created_at") or ts, p.get("updated_at") or ts)


def _check_person_enums(p: dict, origin: str) -> None:
    for k in ("person_id", "name", "organization", "department", "role", "effective_from"):
        if not p.get(k):
            raise ValidationError(f"인물 필수 필드 누락: {k}", "BAD_PERSON")
    _check(p.get("status"), PERSON_STATUSES, "status")
    _check(p.get("verification_status"), VERIFICATION_STATUSES, "verification_status")
    _check(origin, PERSON_ORIGINS, "origin")


_PERSON_INSERT = f"INSERT INTO sv_person ({','.join(_PERSON_COLS)}) VALUES ({','.join('?' * len(_PERSON_COLS))})"


def insert_person_if_absent(p: dict, *, origin: str, db_path: str | Path = DEFAULT_DB_PATH,
                            conn: sqlite3.Connection | None = None) -> bool:
    """sync/import 전용 — 기존 person_id 는 절대 갱신하지 않는다. 반환: 삽입 여부.

    같은 (이름, 기관) 이 다른 person_id 로 이미 있으면 역시 삽입하지 않고 False(경고 로그) — 운영자 결정 보존이 우선.
    어휘(status 등) 위반은 조용히 무시하지 않고 ValidationError — sync 가 오류로 집계한다."""
    _check_person_enums(p, origin)
    with _writer(conn, db_path) as c:
        if c.execute("SELECT 1 FROM sv_person WHERE person_id=?", (p["person_id"],)).fetchone():
            return False
        dup = c.execute("SELECT person_id FROM sv_person WHERE name=? AND organization=?",
                        (p["name"], p["organization"])).fetchone()
        if dup:
            logger.warning("sv_person insert skipped: %s(%s) already registered as %s", p["person_id"], p["name"],
                           dup["person_id"])
            return False
        c.execute(_PERSON_INSERT, _person_values(p, origin))
        return True


def insert_person_row(conn: sqlite3.Connection, p: dict, *, origin: str) -> None:
    """호출자 트랜잭션 안의 엄격 삽입(승인 new_person 등). 같은 id 또는 같은 기관 동명 → ConflictError."""
    _check_person_enums(p, origin)
    try:
        conn.execute(_PERSON_INSERT, _person_values(p, origin))
    except sqlite3.IntegrityError as exc:
        raise ConflictError("같은 기관에 동일한 이름의 인물이 이미 있습니다.", "DUPLICATE_PERSON") from exc


def get_person(person_id: str, *, db_path: str | Path = DEFAULT_DB_PATH,
               conn: sqlite3.Connection | None = None) -> dict | None:
    with _reader(conn, db_path) as c:
        r = c.execute("SELECT * FROM sv_person WHERE person_id=?", (person_id,)).fetchone()
    return person_row_to_dict(r) if r else None


def _require_person(c: sqlite3.Connection, person_id: str) -> dict:
    r = c.execute("SELECT * FROM sv_person WHERE person_id=?", (person_id,)).fetchone()
    if r is None:
        raise NotFoundError("인물을 찾을 수 없습니다.", "NOT_FOUND")
    return person_row_to_dict(r)


def list_people(*, status: str | None = None, q: str | None = None, db_path: str | Path = DEFAULT_DB_PATH,
                conn: sqlite3.Connection | None = None) -> list[dict]:
    """정렬: active 우선 → organization → name (소스 /api/people 와 같다). q 는 이름·기관·부서·직책·별칭 부분일치."""
    sql, args = "SELECT * FROM sv_person WHERE 1=1", []
    if status:
        sql += " AND status=?"
        args.append(status)
    if q and q.strip():
        like = f"%{q.strip()}%"
        sql += (" AND (name LIKE ? OR organization LIKE ? OR department LIKE ? OR role LIKE ? OR aliases_json LIKE ?"
                " OR topic_tags_json LIKE ?)")
        args += [like] * 6
    sql += " ORDER BY status != 'active', organization, name"
    with _reader(conn, db_path) as c:
        return [person_row_to_dict(r) for r in c.execute(sql, args)]


def create_person(data: dict, *, actor: str, db_path: str | Path = DEFAULT_DB_PATH,
                  conn: sqlite3.Connection | None = None) -> dict:
    """대쉬보드 등록 — person_id `P-USER-<12hex>`, verification_status 는 입력과 무관하게 candidate,
    effective_from 은 익일. 같은 기관 동명 → ConflictError."""
    clean = validate_person_input(data)
    ts = _ts()
    row = {**clean, "person_id": f"P-USER-{uuid.uuid4().hex[:12].upper()}", "verification_status": "candidate",
           "organization_code": data.get("organization_code") or "USER", "effective_from": tomorrow_kst(),
           "photo_url": None, "photo_path": None, "updated_by": actor, "created_at": ts, "updated_at": ts}
    with _writer(conn, db_path) as c:
        if c.execute("SELECT 1 FROM sv_person WHERE name=? AND organization=?",
                     (row["name"], row["organization"])).fetchone():
            raise ConflictError("같은 기관에 동일한 이름의 인물이 이미 등록되어 있습니다.", "DUPLICATE_PERSON")
        c.execute(_PERSON_INSERT, _person_values(row, "dashboard"))
        return _require_person(c, row["person_id"])


def update_person(person_id: str, data: dict, *, actor: str, db_path: str | Path = DEFAULT_DB_PATH,
                  conn: sqlite3.Connection | None = None) -> dict:
    """소스 PUT /api/people/{id}: 검증 인물의 기관·부서·직책 변경은 409(감사 제안 승인으로만),
    verification_status 는 입력 무시·보존, effective_from 은 익일."""
    with _writer(conn, db_path) as c:
        person = _require_person(c, person_id)
        clean = validate_person_input(data, current=person)
        if c.execute("SELECT 1 FROM sv_person WHERE person_id!=? AND name=? AND organization=?",
                     (person_id, clean["name"], clean["organization"])).fetchone():
            raise ConflictError("같은 기관에 동일한 이름의 인물이 이미 등록되어 있습니다.", "DUPLICATE_PERSON")
        if person["verification_status"] == "verified" and any(
                clean[k] != person[k] for k in ("organization", "department", "role")):
            raise ConflictError("검증 인물의 기관·부서·직책 변경은 공식 근거가 있는 감사 제안 승인으로만 가능합니다.",
                                "VERIFIED_PROTECTED")
        c.execute("UPDATE sv_person SET name=?, organization=?, department=?, role=?, status=?, aliases_json=?, "
                  "topic_tags_json=?, effective_from=?, updated_by=?, updated_at=? WHERE person_id=?",
                  (clean["name"], clean["organization"], clean["department"], clean["role"], clean["status"],
                   _j(clean["aliases"]), _j(clean["topic_tags"]), tomorrow_kst(), actor, _ts(), person_id))
        return _require_person(c, person_id)


def set_person_status(person_id: str, status: str, *, actor: str, db_path: str | Path = DEFAULT_DB_PATH,
                      conn: sqlite3.Connection | None = None) -> dict:
    """소스 PATCH status: active|paused, effective_from 익일. 삭제는 없다(paused 로만)."""
    if status not in PERSON_STATUSES:
        raise ValidationError("status는 active 또는 paused여야 합니다.", "BAD_STATUS")
    with _writer(conn, db_path) as c:
        _require_person(c, person_id)
        c.execute("UPDATE sv_person SET status=?, effective_from=?, updated_by=?, updated_at=? WHERE person_id=?",
                  (status, tomorrow_kst(), actor, _ts(), person_id))
        return _require_person(c, person_id)


def set_person_photo(person_id: str, photo_path: str | None, *, actor: str, db_path: str | Path = DEFAULT_DB_PATH,
                     conn: sqlite3.Connection | None = None) -> dict:
    """내부 업로드 사진 경로(볼륨 root 기준 상대경로) 설정/해제. 구 파일 삭제는 호출자(photos.py) 몫 —
    호출 전에 get_person()['photo_path'] 를 읽어 둘 것. photo_url(언론 CDN 메타)은 건드리지 않는다."""
    with _writer(conn, db_path) as c:
        _require_person(c, person_id)
        c.execute("UPDATE sv_person SET photo_path=?, updated_by=?, updated_at=? WHERE person_id=?",
                  (photo_path, actor, _ts(), person_id))
        return _require_person(c, person_id)


def set_person_importance(name: str, importance_score: int | None, importance: dict | None, *,
                          db_path: str | Path = DEFAULT_DB_PATH, conn: sqlite3.Connection | None = None) -> int:
    """부서-인물지도 §2.2 중요도표 → 이름 일치 행의 importance_* (sync 소유 두 컬럼만). 반환: 갱신 행 수."""
    with _writer(conn, db_path) as c:
        cur = c.execute("UPDATE sv_person SET importance_score=?, importance_json=? WHERE name=?",
                        (importance_score, _jopt(importance), name))
        return cur.rowcount


# ── 직책 이력 ─────────────────────────────────────────────────────────────────────

_ROLE_HIST_COLS = ("person_id", "organization", "department", "role", "effective_from", "effective_to", "status",
                   "proposal_id", "evidence_json", "approved_at", "applied_at")


def insert_role_history_row(conn: sqlite3.Connection, row: dict) -> int:
    """호출자 트랜잭션 안에서 직책 이력 1행. evidence(list) 또는 evidence_json(str). 반환: id."""
    _check(row.get("status"), ROLE_HISTORY_STATUSES, "status")
    if not row.get("person_id"):
        raise ValidationError("person_id 가 필요합니다.", "BAD_PERSON")
    ev = row.get("evidence_json") if "evidence_json" in row else _jopt(row.get("evidence"))
    vals = [row.get(k) for k in _ROLE_HIST_COLS]
    vals[_ROLE_HIST_COLS.index("evidence_json")] = ev
    cur = conn.execute(f"INSERT INTO sv_person_role_history ({','.join(_ROLE_HIST_COLS)}) "
                       f"VALUES ({','.join('?' * len(_ROLE_HIST_COLS))})", vals)
    return int(cur.lastrowid)


def list_role_history(person_id: str, *, status: str | Iterable[str] | None = None,
                      db_path: str | Path = DEFAULT_DB_PATH, conn: sqlite3.Connection | None = None) -> list[dict]:
    sql, args = "SELECT * FROM sv_person_role_history WHERE person_id=?", [person_id]
    sts = _statuses(status)
    if sts:
        sql += f" AND status IN ({','.join('?' * len(sts))})"
        args += list(sts)
    with _reader(conn, db_path) as c:
        rows = [dict(r) for r in c.execute(sql + " ORDER BY id", args)]
    for r in rows:
        r["evidence"] = _jo(r.pop("evidence_json")) or []
    return rows


# ── 출처 도메인 레지스트리 ─────────────────────────────────────────────────────────

_DOMAIN_COLS = ("source_id", "name", "domain", "source_group", "status", "effective_from", "approved_from_proposal",
                "evidence_json", "origin", "created_at", "updated_at")
_DOMAIN_INSERT = f"INSERT INTO sv_source_domain ({','.join(_DOMAIN_COLS)}) VALUES ({','.join('?' * len(_DOMAIN_COLS))})"


def _domain_values(s: dict, origin: str) -> tuple:
    for k in ("source_id", "name", "domain", "effective_from"):
        if not s.get(k):
            raise ValidationError(f"출처 필수 필드 누락: {k}", "BAD_SOURCE")
    _check(s.get("source_group"), SOURCE_GROUPS, "source_group")
    _check(s.get("status", "approved"), SOURCE_DOMAIN_STATUSES, "status")
    _check(origin, PERSON_ORIGINS, "origin")
    ts = _ts()
    ev = s.get("evidence_json") if "evidence_json" in s else _jopt(s.get("evidence"))
    return (s["source_id"], s["name"], s["domain"], s["source_group"], s.get("status", "approved"), s["effective_from"],
            s.get("approved_from_proposal"), ev, origin, s.get("created_at") or ts, s.get("updated_at") or ts)


def _domain_row_to_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    ev = d.pop("evidence_json", None)
    d["evidence"] = _jo(ev) if ev else None
    return d


def insert_source_domain_if_absent(s: dict, *, origin: str, db_path: str | Path = DEFAULT_DB_PATH,
                                   conn: sqlite3.Connection | None = None) -> bool:
    """sync/import 전용. 같은 source_id 또는 같은 domain 이 있으면 False(기존 행 보존)."""
    vals = _domain_values(s, origin)
    with _writer(conn, db_path) as c:
        if c.execute("SELECT 1 FROM sv_source_domain WHERE source_id=? OR domain=?",
                     (s["source_id"], s["domain"])).fetchone():
            return False
        c.execute(_DOMAIN_INSERT, vals)
        return True


def insert_source_domain_row(conn: sqlite3.Connection, s: dict, *, origin: str) -> None:
    """호출자 트랜잭션 안의 엄격 삽입(승인 source_expansion). 중복 → ConflictError."""
    vals = _domain_values(s, origin)
    try:
        conn.execute(_DOMAIN_INSERT, vals)
    except sqlite3.IntegrityError as exc:
        raise ConflictError("이미 출처 레지스트리에 있는 도메인입니다.", "DUPLICATE_DOMAIN") from exc


def get_source_domain(source_id: str, *, db_path: str | Path = DEFAULT_DB_PATH,
                      conn: sqlite3.Connection | None = None) -> dict | None:
    with _reader(conn, db_path) as c:
        r = c.execute("SELECT * FROM sv_source_domain WHERE source_id=?", (source_id,)).fetchone()
    return _domain_row_to_dict(r) if r else None


def list_source_domains(*, status: str | None = None, db_path: str | Path = DEFAULT_DB_PATH,
                        conn: sqlite3.Connection | None = None) -> list[dict]:
    """정렬: source_group → name (소스 /api/audit/sources 와 같다)."""
    sql, args = "SELECT * FROM sv_source_domain", []
    if status:
        sql += " WHERE status=?"
        args.append(status)
    with _reader(conn, db_path) as c:
        return [_domain_row_to_dict(r) for r in c.execute(sql + " ORDER BY source_group, name", args)]


# ── 감사 제안 ─────────────────────────────────────────────────────────────────────

_PROPOSAL_COLS = ("proposal_id", "proposal_type", "dedupe_key", "priority", "summary", "rationale",
                  "proposed_change_json", "evidence_json", "conflicts_json", "audit_run_id", "created_by", "status",
                  "effective_from", "reviewer", "reviewed_at", "decision_reason", "applied_at", "origin",
                  "created_at", "updated_at")
_PROPOSAL_INSERT = (f"INSERT INTO sv_audit_proposal ({','.join(_PROPOSAL_COLS)}) "
                    f"VALUES ({','.join('?' * len(_PROPOSAL_COLS))})")
_PRIORITY_ORDER_SQL = "CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 WHEN 'low' THEN 3 ELSE 9 END"


def proposal_row_to_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["proposed_change"] = _jo(d.pop("proposed_change_json", None)) or {}
    d["evidence"] = _jl(d.pop("evidence_json", None))
    d["conflicts"] = _jl(d.pop("conflicts_json", None))
    return d


def _proposal_values(p: dict, origin: str) -> tuple:
    for k in ("proposal_id", "dedupe_key", "summary", "rationale"):
        if not p.get(k):
            raise ValidationError(f"제안 필수 필드 누락: {k}", "BAD_PROPOSAL")
    _check(p.get("proposal_type"), PROPOSAL_TYPES, "proposal_type")
    _check(p.get("priority", "normal"), PRIORITIES, "priority")
    _check(p.get("status", "needs_review"), PROPOSAL_STATUSES, "status")
    _check(origin, PROPOSAL_ORIGINS, "origin")
    if not isinstance(p.get("proposed_change", {}), dict):
        raise ValidationError("proposed_change 는 객체여야 합니다.", "BAD_CHANGE")
    ts = _ts()
    return (p["proposal_id"], p["proposal_type"], p["dedupe_key"], p.get("priority", "normal"), p["summary"],
            p["rationale"], _j(p.get("proposed_change") or {}), _j(p.get("evidence") or []),
            _j(p.get("conflicts") or []), p.get("audit_run_id"), p.get("created_by"), p.get("status", "needs_review"),
            p.get("effective_from"), p.get("reviewer"), p.get("reviewed_at"), p.get("decision_reason"),
            p.get("applied_at"), origin, p.get("created_at") or ts, p.get("updated_at") or ts)


def insert_proposal_row(conn: sqlite3.Connection, p: dict, *, origin: str) -> None:
    """호출자 트랜잭션 안의 엄격 삽입. 같은 id 또는 열린 같은 dedupe_key → ConflictError."""
    vals = _proposal_values(p, origin)
    try:
        conn.execute(_PROPOSAL_INSERT, vals)
    except sqlite3.IntegrityError as exc:
        raise ConflictError(f"같은 감사 제안이 이미 열려 있습니다: {p['dedupe_key']}", "DUPLICATE") from exc


def insert_proposal_if_absent(p: dict, *, origin: str, db_path: str | Path = DEFAULT_DB_PATH,
                              conn: sqlite3.Connection | None = None) -> bool:
    """같은 proposal_id 가 있으면 False(기존 행·운영자 결정 보존). 다른 id 로 열린 같은 dedupe_key 가 있으면
    ConflictError — 부분 유니크 인덱스(needs_review|needs_evidence|scheduled)가 강제한다."""
    vals = _proposal_values(p, origin)
    with _writer(conn, db_path) as c:
        if c.execute("SELECT 1 FROM sv_audit_proposal WHERE proposal_id=?", (p["proposal_id"],)).fetchone():
            return False
        try:
            c.execute(_PROPOSAL_INSERT, vals)
        except sqlite3.IntegrityError as exc:
            raise ConflictError(f"같은 감사 제안이 이미 열려 있습니다: {p['dedupe_key']}", "DUPLICATE") from exc
        return True


def get_proposal(proposal_id: str, *, db_path: str | Path = DEFAULT_DB_PATH,
                 conn: sqlite3.Connection | None = None) -> dict | None:
    with _reader(conn, db_path) as c:
        r = c.execute("SELECT * FROM sv_audit_proposal WHERE proposal_id=?", (proposal_id,)).fetchone()
    return proposal_row_to_dict(r) if r else None


def find_open_proposal(dedupe_key: str, *, db_path: str | Path = DEFAULT_DB_PATH,
                       conn: sqlite3.Connection | None = None) -> dict | None:
    """dedupe 를 막고 있는 제안(needs_review|needs_evidence|scheduled) — 409 메시지에 id 를 싣는 용도."""
    q = ",".join("?" * len(DEDUPE_BLOCKING_STATUSES))
    with _reader(conn, db_path) as c:
        r = c.execute(f"SELECT * FROM sv_audit_proposal WHERE dedupe_key=? AND status IN ({q})",
                      (dedupe_key, *DEDUPE_BLOCKING_STATUSES)).fetchone()
    return proposal_row_to_dict(r) if r else None


def list_proposals(*, status: str | Iterable[str] | None = None, type: str | None = None,  # noqa: A002 (계획 계약명)
                   audit_run_id: str | None = None, db_path: str | Path = DEFAULT_DB_PATH,
                   conn: sqlite3.Connection | None = None) -> list[dict]:
    """정렬: 열린 것(needs_review|needs_evidence) 우선 → priority(urgent→low) → created_at (소스와 같다)."""
    sql, args = "SELECT * FROM sv_audit_proposal WHERE 1=1", []
    sts = _statuses(status)
    if sts:
        sql += f" AND status IN ({','.join('?' * len(sts))})"
        args += list(sts)
    if type:
        sql += " AND proposal_type=?"
        args.append(type)
    if audit_run_id:
        sql += " AND audit_run_id=?"
        args.append(audit_run_id)
    sql += (f" ORDER BY status NOT IN ('needs_review','needs_evidence'), {_PRIORITY_ORDER_SQL}, created_at, "
            "proposal_id")
    with _reader(conn, db_path) as c:
        return [proposal_row_to_dict(r) for r in c.execute(sql, args)]


def add_proposal_event_row(conn: sqlite3.Connection, proposal_id: str, *, actor: str, event: str,
                           detail: str | None = None) -> None:
    """append-only 감사 기록 — 호출자 트랜잭션 안에서(상태 전이와 원자적으로)."""
    conn.execute("INSERT INTO sv_audit_proposal_event (proposal_id, actor, event, detail, created_at) "
                 "VALUES (?,?,?,?,?)", (proposal_id, actor, event, detail, _ts()))


def add_proposal_event(proposal_id: str, *, actor: str, event: str, detail: str | None = None,
                       db_path: str | Path = DEFAULT_DB_PATH, conn: sqlite3.Connection | None = None) -> None:
    with _writer(conn, db_path) as c:
        add_proposal_event_row(c, proposal_id, actor=actor, event=event, detail=detail)


def list_proposal_events(proposal_id: str, *, db_path: str | Path = DEFAULT_DB_PATH,
                         conn: sqlite3.Connection | None = None) -> list[dict]:
    with _reader(conn, db_path) as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM sv_audit_proposal_event WHERE proposal_id=? ORDER BY id", (proposal_id,))]


# ── 충돌 확인 (sv_audit_conflict_ack) ────────────────────────────────────────────────

_ACK_COLS = ("proposal_id", "conflict_index", "conflict_text", "reason", "acknowledged_by", "acknowledged_at")


def put_conflict_ack_row(conn: sqlite3.Connection, row: dict) -> None:
    """호출자 트랜잭션 안에서 (proposal_id, conflict_index) 확인 1행을 쓴다(있으면 교체). 교체 허용 여부(유효한 확인은
    덮어쓰지 않음)는 호출자(approval.acknowledge_conflict)가 판정한다 — 이력은 sv_audit_proposal_event 에 남는다."""
    for k in _ACK_COLS:
        if row.get(k) is None or row.get(k) == "":
            raise ValidationError(f"충돌 확인 필수 필드 누락: {k}", "BAD_ACK")
    conn.execute(f"INSERT INTO sv_audit_conflict_ack ({','.join(_ACK_COLS)}) VALUES ({','.join('?' * len(_ACK_COLS))}) "
                 "ON CONFLICT(proposal_id, conflict_index) DO UPDATE SET conflict_text=excluded.conflict_text, "
                 "reason=excluded.reason, acknowledged_by=excluded.acknowledged_by, "
                 "acknowledged_at=excluded.acknowledged_at", [row[k] for k in _ACK_COLS])


def list_conflict_acks(proposal_id: str | None = None, *, proposal_ids: Iterable[str] | None = None,
                       db_path: str | Path = DEFAULT_DB_PATH, conn: sqlite3.Connection | None = None) -> list[dict]:
    """확인 행 목록(proposal_id → conflict_index 순). proposal_id 하나, proposal_ids 여러 개, 둘 다 없으면 전부."""
    sql, args = "SELECT * FROM sv_audit_conflict_ack WHERE 1=1", []
    if proposal_id is not None:
        sql += " AND proposal_id=?"
        args.append(proposal_id)
    if proposal_ids is not None:
        ids = list(proposal_ids)
        if not ids:
            return []
        sql += f" AND proposal_id IN ({','.join('?' * len(ids))})"
        args += ids
    with _reader(conn, db_path) as c:
        return [dict(r) for r in c.execute(sql + " ORDER BY proposal_id, conflict_index", args)]


# ── 런 (모니터링·감사) ──────────────────────────────────────────────────────────────

def _payload(payload: dict | bytes | str) -> tuple[dict, str, str]:
    """(dict, payload_json, sha256). bytes/str 는 파일 원문 그대로 저장·해시(바이트 동일성), dict 는 정렬 JSON."""
    if isinstance(payload, (bytes, str)):
        raw = payload.decode("utf-8") if isinstance(payload, bytes) else payload
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise ValidationError(f"런 JSON 을 파싱할 수 없습니다: {exc}", "BAD_JSON") from exc
        if not isinstance(data, dict):
            raise ValidationError("런 JSON 최상위는 객체여야 합니다.", "BAD_JSON")
        return data, raw, _sha256_text(raw)
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return dict(payload), raw, _sha256_text(raw)


def _int_or_none(v: Any) -> int | None:
    try:
        return None if v is None or isinstance(v, bool) else int(v)
    except (TypeError, ValueError):
        return None


def _count(v: Any) -> int | None:
    """런 JSON 의 스콥 크기 — schema 1.1 은 목록(active_people[], approved_sources[]), 구형은 정수."""
    return len(v) if isinstance(v, (list, tuple, dict)) else _int_or_none(v)


def _upsert_run(c: sqlite3.Connection, table: str, key_col: str, row: dict) -> str:
    prev = c.execute(f"SELECT payload_sha256, md_path, raw_dir FROM {table} WHERE {key_col}=?",
                     (row[key_col],)).fetchone()
    if prev and prev["payload_sha256"] == row["payload_sha256"] \
            and (row.get("md_path") is None or prev["md_path"] == row["md_path"]) \
            and (row.get("raw_dir") is None or prev["raw_dir"] == row["raw_dir"]):
        return "unchanged"
    if prev:
        row = {**row, "md_path": row.get("md_path") or prev["md_path"], "raw_dir": row.get("raw_dir") or prev["raw_dir"]}
    cols = list(row)
    c.execute(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))}) "
              f"ON CONFLICT({key_col}) DO UPDATE SET " + ",".join(f"{k}=excluded.{k}" for k in cols if k != key_col),
              [row[k] for k in cols])
    return "updated" if prev else "inserted"


def upsert_monitoring_run(payload: dict | bytes | str, *, md_path: str | None = None, raw_dir: str | None = None,
                          db_path: str | Path = DEFAULT_DB_PATH, conn: sqlite3.Connection | None = None) -> str:
    """daily_monitoring.json → sv_monitoring_run. payload sha 가 같으면 skip. 반환 inserted|updated|unchanged.
    payload 의 절대경로 문자열은 표시용일 뿐 — 파일 접근 키로 쓰지 않는다."""
    data, raw, sha = _payload(payload)
    run_id, run_date = data.get("run_id"), data.get("run_date_kst") or data.get("run_date")
    if not run_id or not run_date:
        raise ValidationError("모니터링 런에 run_id·run_date_kst 가 필요합니다.", "BAD_RUN")
    scope = data.get("scope") if isinstance(data.get("scope"), dict) else {}
    summary = data.get("search_summary") if isinstance(data.get("search_summary"), dict) else {}
    sat = data.get("source_saturation") if isinstance(data.get("source_saturation"), dict) else {}
    row = {"run_id": run_id, "run_date": run_date, "executed_at": data.get("executed_at_kst"),
           "window_start": data.get("window_start_kst"), "window_end": data.get("window_end_kst"),
           "run_status": data.get("run_status"), "scope_snapshot_id": scope.get("scope_snapshot_id"),
           "people_registry_sha256": scope.get("people_registry_sha256"),
           "source_registry_sha256": scope.get("source_registry_sha256"),
           "active_people": _count(scope.get("active_people")),
           "approved_domains": _count(scope.get("approved_sources")),
           "new_quotes": _int_or_none(summary.get("new_independent_quotes")), "saturation_status": sat.get("status"),
           "payload_json": raw, "md_path": md_path, "raw_dir": raw_dir, "payload_sha256": sha, "imported_at": _ts()}
    with _writer(conn, db_path) as c:
        return _upsert_run(c, "sv_monitoring_run", "run_id", row)


def upsert_audit_run(payload: dict | bytes | str, *, md_path: str | None = None, raw_dir: str | None = None,
                     db_path: str | Path = DEFAULT_DB_PATH, conn: sqlite3.Connection | None = None) -> str:
    """coverage_audit.json → sv_audit_run. claim·why_this_matters·run_status 승격(홈 카드).
    proposals_created: schema 1.0 신형 proposal_summary.created, 구형 proposal_counts.created."""
    data, raw, sha = _payload(payload)
    run_id, run_date = data.get("audit_run_id"), data.get("run_date_kst") or data.get("run_date")
    if not run_id or not run_date:
        raise ValidationError("감사 런에 audit_run_id·run_date_kst 가 필요합니다.", "BAD_RUN")
    created = None
    for key in ("proposal_summary", "proposal_counts"):
        block = data.get(key)
        if isinstance(block, dict) and block.get("created") is not None:
            created = _int_or_none(block.get("created"))
            break
    row = {"audit_run_id": run_id, "run_date": run_date, "executed_at": data.get("executed_at_kst"),
           "completed_at": data.get("completed_at_kst"), "run_status": data.get("run_status"),
           "claim": data.get("claim"), "why_this_matters": data.get("why_this_matters"), "proposals_created": created,
           "payload_json": raw, "md_path": md_path, "raw_dir": raw_dir, "payload_sha256": sha, "imported_at": _ts()}
    with _writer(conn, db_path) as c:
        return _upsert_run(c, "sv_audit_run", "audit_run_id", row)


_RUN_TABLES = {"monitoring": ("sv_monitoring_run", "run_id"), "audit": ("sv_audit_run", "audit_run_id")}


def _run_table(kind: str) -> tuple[str, str]:
    if kind not in _RUN_TABLES:
        raise ValueError(f"kind 는 {RUN_KINDS} 중 하나여야 합니다: {kind!r}")
    return _RUN_TABLES[kind]


def list_runs(kind: str, *, limit: int = 30, db_path: str | Path = DEFAULT_DB_PATH,
              conn: sqlite3.Connection | None = None) -> list[dict]:
    """요약 목록(payload_json 제외), 최신 run_date 먼저."""
    table, key = _run_table(kind)
    with _reader(conn, db_path) as c:
        rows = [dict(r) for r in c.execute(f"SELECT * FROM {table} ORDER BY run_date DESC, {key} DESC LIMIT ?",
                                           (max(1, int(limit)),))]
    for r in rows:
        r.pop("payload_json", None)
    return rows


def get_run(kind: str, run_id: str, *, db_path: str | Path = DEFAULT_DB_PATH,
            conn: sqlite3.Connection | None = None) -> dict | None:
    """요약 + payload(파싱된 전체 JSON)."""
    table, key = _run_table(kind)
    with _reader(conn, db_path) as c:
        r = c.execute(f"SELECT * FROM {table} WHERE {key}=?", (run_id,)).fetchone()
    if r is None:
        return None
    d = dict(r)
    d["payload"] = _jo(d.pop("payload_json"))
    return d


# ── manifest 미러 (sv_source · sv_artifact) ─────────────────────────────────────────

_SOURCE_SCALARS = ("title", "url", "source_type", "evidence_grade", "access_status", "local_path", "sha256",
                   "text_path", "text_sha256", "bytes", "event_date", "publication_date", "publication_date_basis",
                   "locator", "limitation", "scope_status", "scope_note", "video_limitation", "known_timestamp")
_SOURCE_JSONS = ("evidence_facets", "official_video", "last_live_check")


def _scalar(v: Any) -> Any:
    return _j(v) if isinstance(v, (dict, list)) else v


def _check_integrity(status: str | None) -> None:
    if status is not None:
        _check(status, INTEGRITY_STATUSES, "integrity_status")


def upsert_source(src: dict, *, integrity_status: str | None = None, integrity_checked_at: str | None = None,
                  manifest_sha256: str | None = None, db_path: str | Path = DEFAULT_DB_PATH,
                  conn: sqlite3.Connection | None = None) -> str:
    """manifest .sources[] 1건 미러. 원문 객체는 raw_json 으로 통째 보존. integrity_status=None 이면 기존 판정 유지.
    반환 inserted|updated|unchanged."""
    if not src.get("source_id"):
        raise ValidationError("source_id 가 필요합니다.", "BAD_SOURCE")
    _check_integrity(integrity_status)
    raw = json.dumps(src, ensure_ascii=False, sort_keys=True)
    row: dict[str, Any] = {"source_id": src["source_id"]}
    row.update({k: _scalar(src.get(k)) for k in _SOURCE_SCALARS})
    row.update({f"{k}_json": _jopt(src.get(k)) for k in _SOURCE_JSONS})
    row["raw_json"] = raw
    with _writer(conn, db_path) as c:
        prev = c.execute("SELECT raw_json, integrity_status, manifest_sha256 FROM sv_source WHERE source_id=?",
                         (src["source_id"],)).fetchone()
        if prev and prev["raw_json"] == raw and integrity_status in (None, prev["integrity_status"]) \
                and manifest_sha256 in (None, prev["manifest_sha256"]):
            return "unchanged"
        row["integrity_status"] = integrity_status or (prev["integrity_status"] if prev else "unchecked")
        row["manifest_sha256"] = manifest_sha256 or (prev["manifest_sha256"] if prev else None)
        row["imported_at"] = _ts()
        cols = list(row)
        sets = [f"{k}=excluded.{k}" for k in cols if k != "source_id"]
        if integrity_checked_at is not None:
            row["integrity_checked_at"] = integrity_checked_at
            cols.append("integrity_checked_at")
            sets.append("integrity_checked_at=excluded.integrity_checked_at")
        c.execute(f"INSERT INTO sv_source ({','.join(cols)}) VALUES ({','.join('?' * len(cols))}) "
                  f"ON CONFLICT(source_id) DO UPDATE SET {','.join(sets)}", [row[k] for k in cols])
        return "updated" if prev else "inserted"


def set_source_integrity(source_id: str, status: str, *, checked_at: str | None = None,
                         db_path: str | Path = DEFAULT_DB_PATH, conn: sqlite3.Connection | None = None) -> bool:
    _check(status, INTEGRITY_STATUSES, "integrity_status")
    with _writer(conn, db_path) as c:
        return c.execute("UPDATE sv_source SET integrity_status=?, integrity_checked_at=? WHERE source_id=?",
                         (status, checked_at or _ts(), source_id)).rowcount == 1


def _source_row_to_dict(r: sqlite3.Row, *, with_raw: bool = False) -> dict:
    d = dict(r)
    for k in _SOURCE_JSONS:
        d[k] = _jo(d.pop(f"{k}_json", None))
    raw = d.pop("raw_json", None)
    if with_raw:
        d["raw"] = _jo(raw)
    return d


def get_source(source_id: str, *, db_path: str | Path = DEFAULT_DB_PATH,
               conn: sqlite3.Connection | None = None) -> dict | None:
    """단건 — manifest 원문 객체를 `raw` 로 함께 준다(손실 없음 검증용)."""
    with _reader(conn, db_path) as c:
        r = c.execute("SELECT * FROM sv_source WHERE source_id=?", (source_id,)).fetchone()
    return _source_row_to_dict(r, with_raw=True) if r else None


def list_sources(*, db_path: str | Path = DEFAULT_DB_PATH, conn: sqlite3.Connection | None = None) -> list[dict]:
    """manifest 순서 대신 게시일 최신 → source_id. local_path·text_path 노출 여부는 API 계층이 정한다."""
    with _reader(conn, db_path) as c:
        return [_source_row_to_dict(r) for r in c.execute(
            "SELECT * FROM sv_source ORDER BY COALESCE(publication_date, event_date, '') DESC, source_id")]


def upsert_artifact(a: dict, *, integrity_status: str | None = None, integrity_checked_at: str | None = None,
                    db_path: str | Path = DEFAULT_DB_PATH, conn: sqlite3.Connection | None = None) -> str:
    if not a.get("path"):
        raise ValidationError("artifact path 가 필요합니다.", "BAD_ARTIFACT")
    _check_integrity(integrity_status)
    raw = json.dumps(a, ensure_ascii=False, sort_keys=True)
    with _writer(conn, db_path) as c:
        prev = c.execute("SELECT raw_json, integrity_status FROM sv_artifact WHERE path=?", (a["path"],)).fetchone()
        if prev and prev["raw_json"] == raw and integrity_status in (None, prev["integrity_status"]):
            return "unchanged"
        status = integrity_status or (prev["integrity_status"] if prev else "unchecked")
        c.execute("INSERT INTO sv_artifact (path, name, description, sha256, bytes, raw_json, integrity_status, "
                  "integrity_checked_at, imported_at) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET "
                  "name=excluded.name, description=excluded.description, sha256=excluded.sha256, bytes=excluded.bytes, "
                  "raw_json=excluded.raw_json, integrity_status=excluded.integrity_status, "
                  "integrity_checked_at=COALESCE(excluded.integrity_checked_at, sv_artifact.integrity_checked_at), "
                  "imported_at=excluded.imported_at",
                  (a["path"], a.get("name"), a.get("description"), a.get("sha256"), _int_or_none(a.get("bytes")), raw,
                   status, integrity_checked_at, _ts()))
        return "updated" if prev else "inserted"


def set_artifact_integrity(path: str, status: str, *, checked_at: str | None = None,
                           db_path: str | Path = DEFAULT_DB_PATH, conn: sqlite3.Connection | None = None) -> bool:
    _check(status, INTEGRITY_STATUSES, "integrity_status")
    with _writer(conn, db_path) as c:
        return c.execute("UPDATE sv_artifact SET integrity_status=?, integrity_checked_at=? WHERE path=?",
                         (status, checked_at or _ts(), path)).rowcount == 1


def list_artifacts(*, db_path: str | Path = DEFAULT_DB_PATH, conn: sqlite3.Connection | None = None) -> list[dict]:
    with _reader(conn, db_path) as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM sv_artifact ORDER BY path")]
    for r in rows:
        r.pop("raw_json", None)
    return rows


# ── 메타 · sync 색인 ───────────────────────────────────────────────────────────────

def set_meta(key: str, value: Any, *, db_path: str | Path = DEFAULT_DB_PATH,
             conn: sqlite3.Connection | None = None) -> None:
    """value 가 str 이 아니면 JSON 으로 직렬화해 저장한다(get_meta 는 항상 문자열을 돌려준다)."""
    text = value if isinstance(value, str) else _j(value)
    with _writer(conn, db_path) as c:
        c.execute("INSERT INTO sv_meta (key, value, updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET "
                  "value=excluded.value, updated_at=excluded.updated_at", (key, text, _ts()))


def get_meta(key: str, default: str | None = None, *, db_path: str | Path = DEFAULT_DB_PATH,
             conn: sqlite3.Connection | None = None) -> str | None:
    with _reader(conn, db_path) as c:
        r = c.execute("SELECT value FROM sv_meta WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def get_sync_index(*, db_path: str | Path = DEFAULT_DB_PATH, conn: sqlite3.Connection | None = None) -> dict[str, dict]:
    """{path: {blob_sha, sha256, bytes, synced_at}} — sync 가 원격 tree 와 대조한다."""
    with _reader(conn, db_path) as c:
        return {r["path"]: {k: r[k] for k in ("blob_sha", "sha256", "bytes", "synced_at")}
                for r in c.execute("SELECT * FROM sv_sync_file")}


def upsert_sync_file(path: str, blob_sha: str, *, sha256: str | None = None, bytes: int | None = None,  # noqa: A002
                     db_path: str | Path = DEFAULT_DB_PATH, conn: sqlite3.Connection | None = None) -> None:
    with _writer(conn, db_path) as c:
        c.execute("INSERT INTO sv_sync_file (path, blob_sha, sha256, bytes, synced_at) VALUES (?,?,?,?,?) "
                  "ON CONFLICT(path) DO UPDATE SET blob_sha=excluded.blob_sha, sha256=excluded.sha256, "
                  "bytes=excluded.bytes, synced_at=excluded.synced_at", (path, blob_sha, sha256, bytes, _ts()))


def counts(*, db_path: str | Path = DEFAULT_DB_PATH, conn: sqlite3.Connection | None = None) -> dict[str, int]:
    with _reader(conn, db_path) as c:
        return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}


# ── 정본 (sv_event · sv_statement · sv_quote) ──────────────────────────────────────

_EVENT_COLS = ("event_id", "event_date", "title", "context", "publication_dates_json", "topics_json", "speakers_json",
               "source_ids_json", "grades_json")
_STATEMENT_COLS = ("statement_id", "event_id", "topic", "speaker_name", "speaker_role_at_event", "person_id",
                   "multi_speaker", "summary_text", "analysis_text", "limitation_text", "policy_status",
                   "evidence_grade", "locators_json", "source_ids_json", "source_url", "role_check_url",
                   "publication_date", "headline_quote_id")
_QUOTE_COLS = ("quote_id", "event_id", "speaker_name", "person_id", "quote_text", "quote_norm", "segments_json",
               "statement_ids_json", "topics_json", "source_id", "locator", "locators_json", "locator_match",
               "evidence_grade")
_JSON_DEFAULTS = {"segments_json": "[]", "statement_ids_json": "[]", "topics_json": "[]", "locators_json": "[]"}
_ALIASES = {"event_date": ("event_date", "date")}


def _col_value(d: dict, col: str) -> Any:
    """파서 객체(dataclass/dict) → 컬럼 값. `x_json` 컬럼은 `x`(list/dict) 를 직렬화하거나 이미 문자열인 `x_json` 을 쓴다."""
    if col.endswith("_json"):
        if col in d and isinstance(d[col], str):
            return d[col]
        v = d.get(col[:-5])
        return _j(v) if v is not None else _JSON_DEFAULTS.get(col)
    for k in _ALIASES.get(col, (col,)):
        if d.get(k) is not None:
            return d[k]
    return None


def _locator_text(v: Any) -> str | None:
    """locator 는 `sources/<file>.txt:<a>-<b>` 문자열로 저장한다(발췌 API 의 locator 파라미터와 같은 형식)."""
    if v is None or isinstance(v, str):
        return v
    if isinstance(v, dict) and v.get("path"):
        s, e = v.get("start"), v.get("end")
        if s is None:
            return v["path"]
        return f"{v['path']}:{s}-{e if e is not None else s}"
    return _j(v)


def replace_canonical(events: Iterable[Any], statements: Iterable[Any], quotes: Iterable[Any], sha: str,
                      conn: sqlite3.Connection) -> dict[str, int]:
    """origin='canonical_md' 행을 지우고 새 파싱 결과를 넣는다 — **호출자 트랜잭션 안에서만**(실패 시 기존 행 유지).
    다른 origin 행은 건드리지 않는다. 같은 id 의 created_at 은 보존한다. 반환: 삽입 수."""
    if not conn.in_transaction:
        raise RuntimeError("replace_canonical 은 store.transaction() 안에서 호출해야 합니다.")
    ts = _ts()
    created: dict[tuple[str, str], str] = {}
    for table, key in (("sv_event", "event_id"), ("sv_statement", "statement_id"), ("sv_quote", "quote_id")):
        for r in conn.execute(f"SELECT {key}, created_at FROM {table} WHERE origin='canonical_md'"):
            created[(table, r[0])] = r[1]
        conn.execute(f"DELETE FROM {table} WHERE origin='canonical_md'")

    def _insert(table: str, key: str, cols: tuple[str, ...], d: dict, extra: dict) -> None:
        vals = {c: _col_value(d, c) for c in cols}
        vals.update(extra)
        vals["origin"], vals["canonical_sha256"] = "canonical_md", sha
        vals["created_at"] = created.get((table, vals[key])) or ts
        if table != "sv_quote":
            vals["updated_at"] = ts
        names = list(vals)
        conn.execute(f"INSERT INTO {table} ({','.join(names)}) VALUES ({','.join('?' * len(names))})",
                     [vals[n] for n in names])

    n = {"events": 0, "statements": 0, "quotes": 0}
    for e in events:
        _insert("sv_event", "event_id", _EVENT_COLS, _as_dict(e), {})
        n["events"] += 1
    for s in statements:
        d = _as_dict(s)
        _insert("sv_statement", "statement_id", _STATEMENT_COLS, d,
                {"multi_speaker": 1 if d.get("multi_speaker") else 0, "evidence_grade": d.get("evidence_grade") or ""})
        n["statements"] += 1
    for q in quotes:
        d = _as_dict(q)
        lm = d.get("locator_match") or "unchecked"
        _check(lm, LOCATOR_MATCHES, "locator_match")
        _insert("sv_quote", "quote_id", _QUOTE_COLS, d,
                {"quote_norm": d.get("quote_norm") or norm_quote(d.get("quote_text") or ""),
                 "locator": _locator_text(d.get("locator") or (d.get("locators") or [None])[0]),
                 "locator_match": lm})
        n["quotes"] += 1
    return n


def _event_row_to_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    for k in ("publication_dates", "topics", "speakers", "source_ids", "grades"):
        d[k] = _jl(d.pop(f"{k}_json", None))
    return d


def _statement_row_to_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["locators"] = _jl(d.pop("locators_json", None))
    d["source_ids"] = _jl(d.pop("source_ids_json", None))
    d["multi_speaker"] = bool(d.get("multi_speaker"))
    return d


def _quote_row_to_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    for k in ("segments", "statement_ids", "topics", "locators"):
        d[k] = _jl(d.pop(f"{k}_json", None))
    return d


def list_events(*, topic: str | None = None, date_from: str | None = None, date_to: str | None = None,
                db_path: str | Path = DEFAULT_DB_PATH, conn: sqlite3.Connection | None = None) -> list[dict]:
    """최신 행사일 먼저. topic 은 topics_json 원소 일치."""
    sql, args = "SELECT * FROM sv_event e WHERE 1=1", []
    if topic:
        sql += " AND EXISTS (SELECT 1 FROM json_each(e.topics_json) WHERE value=?)"
        args.append(topic)
    if date_from:
        sql += " AND event_date>=?"
        args.append(date_from)
    if date_to:
        sql += " AND event_date<=?"
        args.append(date_to)
    with _reader(conn, db_path) as c:
        return [_event_row_to_dict(r) for r in c.execute(sql + " ORDER BY event_date DESC, event_id", args)]


def get_event(event_id: str, *, db_path: str | Path = DEFAULT_DB_PATH,
              conn: sqlite3.Connection | None = None) -> dict | None:
    with _reader(conn, db_path) as c:
        r = c.execute("SELECT * FROM sv_event WHERE event_id=?", (event_id,)).fetchone()
    return _event_row_to_dict(r) if r else None


def list_statements(*, topic: str | None = None, event_id: str | None = None, person_id: str | None = None,
                    grade: str | None = None, q: str | None = None, date_from: str | None = None,
                    date_to: str | None = None, limit: int | None = None, db_path: str | Path = DEFAULT_DB_PATH,
                    conn: sqlite3.Connection | None = None) -> list[dict]:
    """발언 블록 + 행사(event_date, event_title). 정렬: 행사일 최신 → 파서 순서.
    grade 는 부분일치(`C-summary` ⊂ `C-quote/C-summary`). q 는 발언자·직책·요약·해석·한계·행사명 부분일치.
    기관(org) 필터는 API 계층의 ORG_PATTERNS 몫."""
    sql = ("SELECT s.*, e.event_date AS event_date, e.title AS event_title FROM sv_statement s "
           "LEFT JOIN sv_event e ON e.event_id = s.event_id WHERE 1=1")
    args: list[Any] = []
    for col, val in (("s.topic", topic), ("s.event_id", event_id), ("s.person_id", person_id)):
        if val:
            sql += f" AND {col}=?"
            args.append(val)
    if grade:
        sql += " AND s.evidence_grade LIKE ?"
        args.append(f"%{grade}%")
    if q and q.strip():
        like = f"%{q.strip()}%"
        sql += (" AND (s.speaker_name LIKE ? OR s.speaker_role_at_event LIKE ? OR s.summary_text LIKE ? "
                "OR s.analysis_text LIKE ? OR s.limitation_text LIKE ? OR e.title LIKE ?)")
        args += [like] * 6
    if date_from:
        sql += " AND e.event_date>=?"
        args.append(date_from)
    if date_to:
        sql += " AND e.event_date<=?"
        args.append(date_to)
    sql += " ORDER BY e.event_date DESC, s.event_id, s.rowid"
    if limit:
        sql += " LIMIT ?"
        args.append(int(limit))
    with _reader(conn, db_path) as c:
        return [_statement_row_to_dict(r) for r in c.execute(sql, args)]


def list_quotes(*, event_id: str | None = None, statement_id: str | None = None, person_id: str | None = None,
                db_path: str | Path = DEFAULT_DB_PATH, conn: sqlite3.Connection | None = None) -> list[dict]:
    sql, args = "SELECT * FROM sv_quote q WHERE 1=1", []
    if event_id:
        sql += " AND q.event_id=?"
        args.append(event_id)
    if person_id:
        sql += " AND q.person_id=?"
        args.append(person_id)
    if statement_id:
        sql += " AND EXISTS (SELECT 1 FROM json_each(q.statement_ids_json) WHERE value=?)"
        args.append(statement_id)
    with _reader(conn, db_path) as c:
        return [_quote_row_to_dict(r) for r in c.execute(sql + " ORDER BY q.rowid", args)]


def get_statement(statement_id: str, *, db_path: str | Path = DEFAULT_DB_PATH,
                  conn: sqlite3.Connection | None = None) -> dict | None:
    """발언 블록 + event_date/event_title + quotes(이 블록에 속한 인용 전부, 원문 그대로)."""
    with _reader(conn, db_path) as c:
        r = c.execute("SELECT s.*, e.event_date AS event_date, e.title AS event_title FROM sv_statement s "
                      "LEFT JOIN sv_event e ON e.event_id = s.event_id WHERE s.statement_id=?",
                      (statement_id,)).fetchone()
        if r is None:
            return None
        d = _statement_row_to_dict(r)
        d["quotes"] = list_quotes(statement_id=statement_id, conn=c)
    return d


# ── 에이전트 스콥 스냅샷 ────────────────────────────────────────────────────────────

_SNAPSHOT_PERSON_FIELDS = ("person_id", "name", "organization", "organization_code", "department", "role", "status",
                           "verification_status", "effective_from", "aliases", "topic_tags")


def registry_snapshot(*, today: date, db_path: str | Path = DEFAULT_DB_PATH,
                      conn: sqlite3.Connection | None = None) -> dict:
    """에이전트가 pull 하는 모니터링 스콥 — 소스 run JSON 의 scope.people_registry_sha256 / source_registry_sha256 와 같은 자리.
    sha 는 정렬된 공개 필드만의 JSON 으로 계산한다(사진 경로·사용자 메타 제외) — 같은 스콥이면 같은 해시."""
    t = today.isoformat()
    with _reader(conn, db_path) as c:
        people = [person_row_to_dict(r) for r in c.execute(
            "SELECT * FROM sv_person WHERE status='active' AND effective_from<=? ORDER BY organization, name, person_id",
            (t,))]
        sources = [dict(r) for r in c.execute(
            "SELECT source_id,name,domain,source_group,status,effective_from FROM sv_source_domain "
            "WHERE status='approved' AND effective_from<=? ORDER BY source_group, name, source_id", (t,))]
    pub = [{k: p[k] for k in _SNAPSHOT_PERSON_FIELDS} for p in people]

    def sha(o: Any) -> str:
        return _sha256_text(json.dumps(o, ensure_ascii=False, sort_keys=True))

    return {"as_of": t, "people": pub, "sources": sources, "people_sha256": sha(pub), "sources_sha256": sha(sources)}
