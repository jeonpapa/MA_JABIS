"""서비스 보완/개선 요청 (Service Request) 저장소.

사용자가 대쉬보드 어느 페이지에서든 개선/보완 요청을 남기고, 관리자가 트리아지해
Claude 핸드오프 마크다운 패키지로 정리·확인·전달(기록)하는 MVP 의 단일 store.

패턴 미러링:
- SCHEMA_SQL + ensure_*_tables dict-루프 idempotent ALTER + _json_or
  → agents/daily_mailing/storage.py
- 사용자 피드백 테이블 선례 → agents/analog/store.py (analog_search_feedback)
- append-only 감사 테이블 → agents/analog/yakpyungwi_match.py (_AUDIT_SCHEMA)

원칙:
- 모든 상태전이는 service_request_event 에 append-only 감사 기록.
- context_json 은 저장 전 반드시 _redact_context 로 민감 키 마스킹.
- send_to_claude 는 외부 호출 없음 — 최종 마크다운 저장/반환 + sent 마킹만.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "db" / "drug_prices.db"

# 요청 상태 흐름: open → in_review → packaged → confirmed → sent (+ rejected/done)
STATUSES = ("open", "in_review", "packaged", "confirmed", "sent", "rejected", "done")
REQUEST_TYPES = ("bug", "improvement", "feature", "data", "other")
PRIORITIES = ("low", "medium", "high", "urgent")
PACKAGE_STATUSES = ("none", "draft", "final")

# 최종 확인 체크리스트 — 5개 전부 true 여야 confirm 가능
CHECKLIST_KEYS = (
    "scope_clear",              # 요청 범위가 명확한가
    "context_redacted",         # 컨텍스트 민감정보 레닥션 확인
    "no_secrets",               # 자격증명/토큰/비밀번호 미포함
    "expected_outcome_defined", # 기대 결과가 정의되어 있는가
    "no_deploy_ack",            # 승인 없이 배포/푸시 금지 인지
)

# context_json 저장 금지 민감 키 (키에 아래 토큰 포함 시 값 마스킹, 대소문자 무시)
SENSITIVE_KEYS = (
    "token", "authorization", "password", "secret",
    "api_key", "jwt", "session", "cookie", "auth",
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS service_request (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_email      TEXT NOT NULL,
    page_path        TEXT,
    page_label       TEXT,
    source_url       TEXT,
    request_type     TEXT DEFAULT 'improvement',
    priority         TEXT DEFAULT 'medium',
    title            TEXT NOT NULL,
    body             TEXT,
    expected_outcome TEXT,
    context_json     TEXT NOT NULL DEFAULT 'null',
    status           TEXT NOT NULL DEFAULT 'open',
    admin_note       TEXT,
    package_markdown TEXT,
    package_status   TEXT NOT NULL DEFAULT 'none',
    checklist_json   TEXT NOT NULL DEFAULT 'null',
    confirmed_at     TEXT,
    sent_at          TEXT,
    sent_markdown    TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_service_request_owner ON service_request(owner_email);
CREATE INDEX IF NOT EXISTS idx_service_request_status ON service_request(status);

-- 감사 이벤트 (append-only) — create/update/package/confirm/send/reject
CREATE TABLE IF NOT EXISTS service_request_event (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id   INTEGER NOT NULL,
    actor_email  TEXT,
    event_type   TEXT NOT NULL,
    from_status  TEXT,
    to_status    TEXT,
    note         TEXT,
    payload_json TEXT NOT NULL DEFAULT 'null',
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_service_request_event_req ON service_request_event(request_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_or(raw, default):
    """Parse a stored *_json column, returning ``default`` on null/empty/invalid."""
    if raw is None or raw == "":
        return default
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return default
    return default if parsed is None else parsed


def _dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def ensure_service_request_tables(db_path: str | Path | None = None) -> None:
    with sqlite3.connect(str(db_path or DEFAULT_DB_PATH)) as conn:
        conn.executescript(SCHEMA_SQL)
        # Lightweight migrations — 초기 배포 이전 생성된 테이블 자기치유용 (idempotent).
        existing = {row[1] for row in conn.execute("PRAGMA table_info(service_request)").fetchall()}
        for col, ddl in {
            "admin_note": "ALTER TABLE service_request ADD COLUMN admin_note TEXT",
            "package_markdown": "ALTER TABLE service_request ADD COLUMN package_markdown TEXT",
            "package_status": "ALTER TABLE service_request ADD COLUMN package_status TEXT NOT NULL DEFAULT 'none'",
            "checklist_json": "ALTER TABLE service_request ADD COLUMN checklist_json TEXT NOT NULL DEFAULT 'null'",
            "confirmed_at": "ALTER TABLE service_request ADD COLUMN confirmed_at TEXT",
            "sent_at": "ALTER TABLE service_request ADD COLUMN sent_at TEXT",
            "sent_markdown": "ALTER TABLE service_request ADD COLUMN sent_markdown TEXT",
        }.items():
            if col not in existing:
                conn.execute(ddl)
        existing_event = {row[1] for row in conn.execute("PRAGMA table_info(service_request_event)").fetchall()}
        for col, ddl in {
            "note": "ALTER TABLE service_request_event ADD COLUMN note TEXT",
            "payload_json": "ALTER TABLE service_request_event ADD COLUMN payload_json TEXT NOT NULL DEFAULT 'null'",
        }.items():
            if col not in existing_event:
                conn.execute(ddl)
        conn.commit()


# ── 레닥션 ────────────────────────────────────────────────────────────────────

def _redact_context(obj):
    """민감 키(값이 아닌 KEY 기준, 대소문자 무시) 마스킹 — 중첩 dict/list 재귀.

    auth 토큰/쿠키/비밀번호는 context_json 에 저장 금지. 키에 SENSITIVE_KEYS
    토큰이 하나라도 포함되면 값 전체를 "[REDACTED]" 로 치환한다.
    """
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            key_l = str(k).lower()
            if any(s in key_l for s in SENSITIVE_KEYS):
                out[k] = "[REDACTED]"
            else:
                out[k] = _redact_context(v)
        return out
    if isinstance(obj, list):
        return [_redact_context(v) for v in obj]
    return obj


# ── row 변환 ──────────────────────────────────────────────────────────────────

def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["context"] = _json_or(d.pop("context_json", None), None)
    d["checklist"] = _json_or(d.pop("checklist_json", None), None)
    return d


def _event_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["payload"] = _json_or(d.pop("payload_json", None), None)
    return d


def _connect(db_path: str | Path | None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _fetch_row(conn: sqlite3.Connection, request_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM service_request WHERE id=?", (request_id,)).fetchone()


def _add_event(
    conn: sqlite3.Connection,
    request_id: int,
    actor_email: str | None,
    event_type: str,
    *,
    from_status: str | None = None,
    to_status: str | None = None,
    note: str | None = None,
    payload=None,
) -> None:
    """감사 이벤트 append (모든 상태전이에서 호출, append-only)."""
    conn.execute(
        """
        INSERT INTO service_request_event
            (request_id, actor_email, event_type, from_status, to_status, note, payload_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (request_id, actor_email, event_type, from_status, to_status, note, _dumps(payload), _now()),
    )


# ── CRUD ─────────────────────────────────────────────────────────────────────

def create_request(
    owner_email: str,
    *,
    title: str,
    body: str | None = None,
    expected_outcome: str | None = None,
    request_type: str = "improvement",
    priority: str = "medium",
    page_path: str | None = None,
    page_label: str | None = None,
    source_url: str | None = None,
    context: dict | None = None,
    db_path: str | Path | None = None,
) -> dict:
    """요청 생성 — context 레닥션 + event=create."""
    ensure_service_request_tables(db_path)
    title = (title or "").strip()
    if not title:
        raise ValueError("title required")
    redacted = _redact_context(context) if context is not None else None
    now = _now()
    with _connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO service_request
                (owner_email, page_path, page_label, source_url, request_type, priority,
                 title, body, expected_outcome, context_json, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)
            """,
            (
                owner_email, page_path, page_label, source_url,
                request_type or "improvement", priority or "medium",
                title, body, expected_outcome, _dumps(redacted), now, now,
            ),
        )
        request_id = cur.lastrowid
        _add_event(conn, request_id, owner_email, "create", to_status="open",
                   payload={"title": title, "request_type": request_type, "priority": priority})
        conn.commit()
        row = _fetch_row(conn, request_id)
    return _row_to_dict(row)


def list_mine(owner_email: str, limit: int = 200, *, db_path: str | Path | None = None) -> list[dict]:
    ensure_service_request_tables(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM service_request WHERE owner_email=? ORDER BY created_at DESC, id DESC LIMIT ?",
            (owner_email, int(limit)),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def list_all(
    *,
    status: str | None = None,
    priority: str | None = None,
    request_type: str | None = None,
    limit: int = 500,
    db_path: str | Path | None = None,
) -> list[dict]:
    ensure_service_request_tables(db_path)
    where: list[str] = []
    params: list = []
    for col, val in (("status", status), ("priority", priority), ("request_type", request_type)):
        if val:
            where.append(f"{col}=?")
            params.append(val)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    params.append(int(limit))
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT * FROM service_request {clause} ORDER BY created_at DESC, id DESC LIMIT ?",
            params,
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_request(request_id: int, *, db_path: str | Path | None = None) -> dict | None:
    ensure_service_request_tables(db_path)
    with _connect(db_path) as conn:
        row = _fetch_row(conn, request_id)
    return _row_to_dict(row) if row else None


def list_events(request_id: int, *, db_path: str | Path | None = None) -> list[dict]:
    ensure_service_request_tables(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM service_request_event WHERE request_id=? ORDER BY id ASC",
            (request_id,),
        ).fetchall()
    return [_event_to_dict(r) for r in rows]


def admin_update(
    request_id: int,
    actor_email: str,
    *,
    status: str | None = None,
    priority: str | None = None,
    request_type: str | None = None,
    admin_note: str | None = None,
    db_path: str | Path | None = None,
) -> dict | None:
    """관리자 부분 수정 (status/priority/request_type/admin_note) + event=update."""
    ensure_service_request_tables(db_path)
    with _connect(db_path) as conn:
        row = _fetch_row(conn, request_id)
        if row is None:
            return None
        fields: dict = {}
        if status is not None:
            fields["status"] = status
        if priority is not None:
            fields["priority"] = priority
        if request_type is not None:
            fields["request_type"] = request_type
        if admin_note is not None:
            fields["admin_note"] = admin_note
        if fields:
            fields["updated_at"] = _now()
            set_clause = ", ".join(f"{k}=?" for k in fields)
            conn.execute(
                f"UPDATE service_request SET {set_clause} WHERE id=?",
                list(fields.values()) + [request_id],
            )
            from_status = row["status"]
            to_status = fields.get("status")
            changed = from_status != to_status if to_status is not None else False
            _add_event(
                conn, request_id, actor_email, "update",
                from_status=from_status if changed else None,
                to_status=to_status if changed else None,
                payload={k: v for k, v in fields.items() if k != "updated_at"},
            )
            conn.commit()
        row = _fetch_row(conn, request_id)
    return _row_to_dict(row)


def save_package(
    request_id: int,
    actor_email: str,
    *,
    mode: str,
    markdown: str | None = None,
    db_path: str | Path | None = None,
) -> dict | tuple[None, str]:
    """Claude 핸드오프 패키지 생성/저장 (mode=generate|save_draft|save_final) + event=package.

    - generate:   build_claude_package(row) 로 마크다운 생성 → package_status='draft'
    - save_draft: 편집본 저장 → package_status='draft'
    - save_final: 편집본 저장 → package_status='final'
    status 가 open/in_review 이면 → 'packaged' 전이.
    """
    ensure_service_request_tables(db_path)
    if mode not in ("generate", "save_draft", "save_final"):
        return (None, "invalid mode")
    with _connect(db_path) as conn:
        row = _fetch_row(conn, request_id)
        if row is None:
            return (None, "not found")
        if mode == "generate":
            md = build_claude_package(_row_to_dict(row))
            package_status = "draft"
        else:
            if not (markdown or "").strip():
                return (None, "markdown required")
            md = markdown
            package_status = "final" if mode == "save_final" else "draft"
        from_status = row["status"]
        to_status = "packaged" if from_status in ("open", "in_review") else from_status
        conn.execute(
            "UPDATE service_request SET package_markdown=?, package_status=?, status=?, updated_at=? WHERE id=?",
            (md, package_status, to_status, _now(), request_id),
        )
        _add_event(
            conn, request_id, actor_email, "package",
            from_status=from_status if to_status != from_status else None,
            to_status=to_status if to_status != from_status else None,
            payload={"mode": mode, "package_status": package_status},
        )
        conn.commit()
        row = _fetch_row(conn, request_id)
    return _row_to_dict(row)


def confirm_request(
    request_id: int,
    actor_email: str,
    checklist: dict,
    *,
    db_path: str | Path | None = None,
) -> dict | tuple[None, str]:
    """최종 확인 — CHECKLIST_KEYS 5개 전부 true 여야 confirmed 전이 + event=confirm."""
    ensure_service_request_tables(db_path)
    checklist = checklist if isinstance(checklist, dict) else {}
    if not all(bool(checklist.get(k)) for k in CHECKLIST_KEYS):
        return (None, "checklist incomplete")
    with _connect(db_path) as conn:
        row = _fetch_row(conn, request_id)
        if row is None:
            return (None, "not found")
        stored = {k: bool(checklist.get(k)) for k in CHECKLIST_KEYS}
        now = _now()
        from_status = row["status"]
        conn.execute(
            "UPDATE service_request SET checklist_json=?, status='confirmed', confirmed_at=?, updated_at=? WHERE id=?",
            (_dumps(stored), now, now, request_id),
        )
        _add_event(conn, request_id, actor_email, "confirm",
                   from_status=from_status, to_status="confirmed", payload=stored)
        conn.commit()
        row = _fetch_row(conn, request_id)
    return _row_to_dict(row)


def send_to_claude(
    request_id: int,
    actor_email: str,
    *,
    db_path: str | Path | None = None,
) -> dict | tuple[None, str]:
    """sent 마킹 — 외부 호출 없음. sent_markdown=package_markdown 확정 저장 + event=send.

    status != 'confirmed' 이면 (None, "not confirmed").
    """
    ensure_service_request_tables(db_path)
    with _connect(db_path) as conn:
        row = _fetch_row(conn, request_id)
        if row is None:
            return (None, "not found")
        if row["status"] != "confirmed":
            return (None, "not confirmed")
        now = _now()
        conn.execute(
            "UPDATE service_request SET sent_markdown=package_markdown, status='sent', sent_at=?, updated_at=? WHERE id=?",
            (now, now, request_id),
        )
        _add_event(conn, request_id, actor_email, "send",
                   from_status="confirmed", to_status="sent")
        conn.commit()
        row = _fetch_row(conn, request_id)
    return _row_to_dict(row)


# ── Claude 핸드오프 패키지 빌더 ────────────────────────────────────────────────

_CHECKLIST_LABELS = {
    "scope_clear": "요청 범위(무엇을/어디를)가 명확한가",
    "context_redacted": "컨텍스트 민감정보 레닥션을 확인했는가",
    "no_secrets": "토큰/쿠키/비밀번호 등 자격증명이 포함되지 않았는가",
    "expected_outcome_defined": "기대 결과(완료 기준)가 정의되어 있는가",
    "no_deploy_ack": "승인 전 배포/푸시 금지를 인지했는가",
}

NO_DEPLOY_LINE = "⚠️ Joseph/Hermes 승인 없이 배포(flyctl deploy)·git push 금지."


def build_claude_package(row: dict) -> str:
    """요청 row(dict) → 결정론적 Claude 핸드오프 마크다운.

    외부 호출/현재시각 없이 row 필드만으로 생성 (같은 row → 같은 출력).
    context 는 저장 시 이미 레닥션되지만 방어적으로 한 번 더 레닥션한다.
    """
    context = _redact_context(row.get("context")) if row.get("context") is not None else None
    context_block = json.dumps(context, ensure_ascii=False, indent=2) if context is not None else "(없음)"
    lines = [
        f"# [Service Request #{row.get('id')}] {row.get('title') or ''}".rstrip(),
        "",
        "## 메타",
        f"- 유형: {row.get('request_type') or 'improvement'}",
        f"- 우선순위: {row.get('priority') or 'medium'}",
        f"- 페이지: {row.get('page_label') or '(미지정)'} (`{row.get('page_path') or '-'}`)",
        f"- 소스 URL: {row.get('source_url') or '-'}",
        f"- 요청자: {row.get('owner_email') or '-'}",
        f"- 요청일: {row.get('created_at') or '-'}",
        "",
        "## 배경 / 문제",
        (row.get("body") or "").strip() or "(작성 없음)",
        "",
        "## 기대 결과",
        (row.get("expected_outcome") or "").strip() or "(작성 없음)",
        "",
        "## 컨텍스트 (route/query — 민감 키 레닥션 적용)",
        "```json",
        context_block,
        "```",
        "",
        "## 관리자 노트",
        (row.get("admin_note") or "").strip() or "(없음)",
        "",
        "## SAFETY / REDACTION CHECKLIST",
    ]
    checklist = row.get("checklist") if isinstance(row.get("checklist"), dict) else {}
    for key in CHECKLIST_KEYS:
        mark = "x" if checklist.get(key) else " "
        lines.append(f"- [{mark}] {key} — {_CHECKLIST_LABELS[key]}")
    lines += [
        "",
        NO_DEPLOY_LINE,
        "",
    ]
    return "\n".join(lines)
