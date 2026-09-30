"""Daily Mailing run 상태 조회·전이 (발송·승인·재시도·헬스체크).

storage.py 는 적재·칸반 조회를 맡고, 이 모듈은 발송 파이프라인의 상태 전이만 담당한다.
설계 §6: delivery_status ∈ {pending_approval, ready_to_send, sending, sent, held, send_failed,
no_news_sent, error, expired}.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .storage import DEFAULT_DB_PATH, RETENTION_DAYS, ensure_daily_mailing_tables

# 재시도 대상 상태: send_failed(발송 실패) + ready_to_send(persist 직후·선점 전 프로세스 중단 — 발송 시도 0회라 안전)
RETRYABLE_SEND_STATUSES = ("send_failed", "ready_to_send")

UPDATABLE_FIELDS = frozenset({
    "delivery_status", "approval_status", "sent_at", "gmail_message_id",
    "send_attempts", "delivery_error", "approved_by", "approved_at",
})


def _rows(db_path: str | Path, sql: str, params: tuple = ()) -> list[dict]:
    ensure_daily_mailing_tables(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _write(db_path: str | Path, sql: str, params: tuple) -> int:
    ensure_daily_mailing_tables(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.rowcount


def get_run(run_id: str, *, db_path: str | Path = DEFAULT_DB_PATH) -> dict | None:
    rows = _rows(db_path, "SELECT * FROM daily_mailing_run WHERE run_id=?", (run_id,))
    return rows[0] if rows else None


def find_regular_run(subscription_id: int, run_date: str, *, db_path: str | Path = DEFAULT_DB_PATH) -> dict | None:
    rows = _rows(db_path, "SELECT * FROM daily_mailing_run WHERE subscription_id=? AND run_date=? "
                          "AND is_test=0 LIMIT 1", (subscription_id, run_date))
    return rows[0] if rows else None


def list_runs_for_date(run_date: str, *, db_path: str | Path = DEFAULT_DB_PATH,
                       include_tests: bool = False) -> list[dict]:
    sql = "SELECT * FROM daily_mailing_run WHERE run_date=?"
    if not include_tests:
        sql += " AND is_test=0"
    return _rows(db_path, sql + " ORDER BY generated_at", (run_date,))


def list_failed_sends(*, db_path: str | Path = DEFAULT_DB_PATH, max_attempts: int = 3) -> list[dict]:
    """재시도 대상 정규 run — RETRYABLE_SEND_STATUSES 이고 시도 횟수가 남은 것.

    [TEST] run 은 제외한다: recipients_json 이 구독 수신자라 재시도 경로가 집으면 요청자가 아닌
    실수신자에게 테스트 메일이 나간다 (테스트는 요청자가 다시 누르면 된다).
    """
    placeholders = ",".join("?" for _ in RETRYABLE_SEND_STATUSES)
    return _rows(db_path, f"SELECT * FROM daily_mailing_run WHERE delivery_status IN ({placeholders}) "
                          "AND is_test=0 AND send_attempts < ? ORDER BY generated_at",
                 (*RETRYABLE_SEND_STATUSES, max_attempts))


def list_sending_runs(*, db_path: str | Path = DEFAULT_DB_PATH) -> list[dict]:
    """발송 선점('sending') 상태인 run — 프로세스 중단 복구 판정용."""
    return _rows(db_path, "SELECT * FROM daily_mailing_run WHERE delivery_status='sending' "
                          "ORDER BY claimed_at, generated_at")


def update_run(run_id: str, *, db_path: str | Path = DEFAULT_DB_PATH, **fields) -> None:
    unknown = set(fields) - UPDATABLE_FIELDS
    if unknown:
        raise ValueError(f"update_run: 허용되지 않은 필드 {sorted(unknown)}")
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    _write(db_path, f"UPDATE daily_mailing_run SET {cols} WHERE run_id=?", (*fields.values(), run_id))


def claim_for_sending(run_id: str, *, from_statuses: tuple[str, ...], claimed_at: str,
                      db_path: str | Path = DEFAULT_DB_PATH) -> bool:
    """조건부 UPDATE 로 발송 선점 — web(승인)·scheduler(재시도)의 이중 발송 방지.

    claimed_at 은 호출부가 넘기는 tz-aware ISO 문자열(KST) — 선점에 성공한 경우에만 기록된다.
    """
    placeholders = ",".join("?" for _ in from_statuses)
    return _write(db_path, "UPDATE daily_mailing_run SET delivery_status='sending', claimed_at=? "
                           f"WHERE run_id=? AND delivery_status IN ({placeholders})",
                  (claimed_at, run_id, *from_statuses)) == 1


def expire_pending_before(run_date: str, *, db_path: str | Path = DEFAULT_DB_PATH) -> int:
    return _write(db_path, "UPDATE daily_mailing_run SET delivery_status='expired' "
                           "WHERE delivery_status='pending_approval' AND run_date < ?", (run_date,))


def record_error_run(*, subscription_id: int, run_date: str, error: str, now: datetime,
                     is_test: bool = False, db_path: str | Path = DEFAULT_DB_PATH) -> str:
    """생성 예외를 run 으로 기록 (설계 §6).

    정규(is_test=False): 그날의 정규 run 자리를 점유한다 (같은 날 재생성·LLM 재호출 폭주 방지).
    테스트(is_test=True): 접미사 `_test_error` 로 남겨 실패를 칸반에 보이게 하되 정규 슬롯은 점유하지 않는다.
    """
    suffix = "_test_error" if is_test else "_error"
    run_id = f"dm_{subscription_id}_{now:%Y%m%d_%H%M%S}{suffix}"
    created = datetime.now(timezone.utc)
    expires = (created + timedelta(days=RETENTION_DAYS)).isoformat()
    _write(db_path,
           "INSERT OR IGNORE INTO daily_mailing_run (run_id, generated_at, keywords_json, subscription_id, "
           "run_date, is_test, status, delivery_status, approval_status, delivery_error, expires_at, created_at) "
           "VALUES (?, ?, '[]', ?, ?, ?, 'error', 'error', 'not_required', ?, ?, ?)",
           (run_id, now.isoformat(), subscription_id, run_date, 1 if is_test else 0, error[:500],
            expires, created.isoformat()))
    return run_id
