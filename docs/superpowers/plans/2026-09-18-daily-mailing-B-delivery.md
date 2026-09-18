# Daily Mailing — Slice B: Delivery · Scheduling · Approval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the production server generate, gate, approve and send each subscription's daily briefing via Gmail, with per-subscription approval→auto modes, retries, a health check, admin UI actions, scope input validation, and retirement of the Hermes git bus.

**Architecture:** A new `dispatch` module orchestrates state transitions over the existing `daily_mailing_run` table (new columns + partial unique index) and calls the content engine (`engine.generate_run`, Slice A) through injected callables, so every path is testable with fakes. The scheduler runs a 10-minute dispatch tick, a 06:40 top-news job and a 09:30 health job. Flask adds approve/reject/preview/health admin endpoints; `test-request` becomes an immediate background test send. React admin kanban gets approve/reject/preview; the subscription form gets delivery-mode and top-news toggles.

**Tech Stack:** Python 3.12, sqlite3, APScheduler 3, Flask, Gmail API (`google-api-python-client`, `google-auth`), React + TypeScript (Vite), pytest.

**Spec:** `docs/superpowers/specs/2026-09-18-daily-mailing-service-design.md` §4, §6, §7, §8, §9.

---

## Working rules for this slice

- Work in the git worktree you were given. Python: `/Users/kimjeong-ae/MA_AI_Dossier/.venv/bin/python`; run tests from the worktree root: `/Users/kimjeong-ae/MA_AI_Dossier/.venv/bin/python -m pytest <path> -q`.
- Do **not** modify Slice A files: `agents/daily_mailing/discovery.py`, `article_body.py`, `llm.py`, `brief.py`, `archive_candidates.py`, `engine.py`, `config/settings.json`, `scripts/daily_mailing_pilot.py`. `engine.py`/`brief.py` may not exist in your worktree yet — import them **lazily inside functions only** (never at module top level) and fake them in tests.
- Never call real Gmail/OpenAI/Naver in tests. Never run `flyctl`, `git push`, or send email.
- `import api.server` inside the worktree creates a fresh empty sqlite DB under the worktree's `data/db/` (the real DB is gitignored and not present). That is expected — do not copy or symlink the real DB. `config/.env` is also absent; tests must not need it.
- Commit after each task with the message shown; end every message with
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`

## Interface contract with Slice A (what you may rely on)

```python
from agents.daily_mailing.engine import EngineConfig, EngineDeps, generate_run, generate_top_ma_news
from agents.daily_mailing.brief import BriefItem       # BriefItem.to_dict() / BriefItem.from_dict(d)
# generate_run(scope, *, deps, config=None, name=None, now=None, top_ma_news=None, is_test=False) -> RunResult
# RunResult attributes used here: run_id, status ("ready"|"no_news"|"held"), payload (dict), articles (list[dict])
# payload contains: run_id, subscription_id(int|None), run_date("YYYY-MM-DD"), is_test(bool),
#   recipients(list), email_subject, email_html, status, items(list of BriefItem.to_dict() — each has "ma_implication"), ...
```

In tests, fake `generate` with a function returning `types.SimpleNamespace(run_id=..., status=..., payload={...}, articles=[])`.

## File structure

| File | Responsibility |
|---|---|
| `agents/daily_mailing/dashboard_scope.py` (modify) | `scope_from_dict()` — build `DashboardScope` from a dict (subscription rows) |
| `agents/daily_mailing/subscription_bridge.py` (modify) | `delivery_mode`/`include_top_ma_news` columns + mapping into scope |
| `agents/daily_mailing/storage.py` (modify) | run delivery columns, partial unique index, duplicate guard, keep LLM notes, kanban omits HTML |
| `agents/daily_mailing/run_store.py` (new) | run state queries & transitions (claim/update/expire/error run) |
| `agents/daily_mailing/schedule.py` (new) | pure due-time logic (KST) |
| `agents/daily_mailing/top_news.py` (new) | per-day cache of '오늘의 주요 MA 뉴스' |
| `agents/daily_mailing/dispatch.py` (new) | orchestration: tick, route, send, approve/reject, retry, test send, health |
| `agents/daily_mailing/scope_validation.py` (new) | subscription scope validation + name/brand mismatch warnings |
| `agents/notify/gmail_delivery.py` (modify) | token path resolution + materialize token from `GOOGLE_TOKEN_JSON` |
| `scripts/gmail_oauth_setup.py` (new) | one-time local OAuth to produce the Gmail token for the fly secret |
| `requirements.txt` (modify) | Gmail API libs |
| `api/server.py` (modify) | subscription fields/validation, admin run endpoints, test-request, test-send |
| `scheduler.py` (modify) | new mailing jobs, retire Hermes bus jobs, CLI flags |
| `frontend/src/api/dailyMailingKanban.ts`, `frontend/src/api/mailSubscriptions.ts` (modify) | API types/functions |
| `frontend/src/pages/admin/daily-mailing-kanban/page.tsx` (modify) | health banner, preview modal, approve/reject |
| `frontend/src/pages/daily-mailing/page.tsx` (modify) | delivery-mode + top-news toggles, warnings, copy |
| `.github/workflows/hermes-daily-mailing.yml` → `docs/daily_mailing/retired/` | retire Hermes test-send workflow |

---

### Task B1: `scope_from_dict`

**Files:** Modify `agents/daily_mailing/dashboard_scope.py` · Test `tests/test_dm_scope_from_dict.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_dm_scope_from_dict.py
from __future__ import annotations

import json

from agents.daily_mailing.dashboard_scope import load_dashboard_scope, scope_from_dict


def test_scope_from_dict_matches_file_loader(tmp_path):
    data = {"subscription_id": 19, "name": "파드셉", "keywords": ["약가 인하"], "brands": ["파드셉"],
            "aliases": {"파드셉": "Padcev"}, "include_top_ma_signals": False, "lookback_hours": 36}
    scope = scope_from_dict(data)
    assert scope.subscription_id == "19"
    assert scope.brands == ("파드셉",)
    assert scope.aliases == {"파드셉": ("Padcev",)}
    assert scope.include_top_ma_signals is False
    assert scope.lookback_hours == 36
    path = tmp_path / "s.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert load_dashboard_scope(path) == scope
```

- [ ] **Step 2:** Run `…/python -m pytest tests/test_dm_scope_from_dict.py -q` → FAIL (`ImportError: scope_from_dict`).

- [ ] **Step 3: Implement** — replace `load_dashboard_scope` with:

```python
def scope_from_dict(data: dict[str, Any]) -> DashboardScope:
    aliases_raw = data.get("aliases") or {}
    aliases: dict[str, tuple[str, ...]] = {}
    for key, val in aliases_raw.items():
        aliases[str(key)] = tuple(str(x).strip() for x in (val if isinstance(val, list) else [val]) if str(x).strip())
    return DashboardScope(
        subscription_id=str(data.get("subscription_id") or "default"),
        owner_email=data.get("owner_email"),
        recipients=_tuple(data, "recipients"),
        keywords=_tuple(data, "keywords"),
        companies=_tuple(data, "companies"),
        brands=_tuple(data, "brands"),
        aliases=aliases,
        disease_areas=_tuple(data, "disease_areas"),
        policy_topics=_tuple(data, "policy_topics"),
        media=_tuple(data, "media"),
        personas=_tuple(data, "personas") or ("ma_lead", "brand_strategy", "policy_watch"),
        lookback_hours=int(data.get("lookback_hours") or 24),
        delivery_mode=str(data.get("delivery_mode") or "gmail_draft"),
        include_top_ma_signals=bool(data.get("include_top_ma_signals", True)),
        include_user_keyword_watchlist=bool(data.get("include_user_keyword_watchlist", True)),
    )


def load_dashboard_scope(path: str | Path) -> DashboardScope:
    return scope_from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
```

- [ ] **Step 4:** Run `tests/test_dm_scope_from_dict.py tests/test_dashboard_scope.py` → PASS.
- [ ] **Step 5: Commit** `git commit -am "refactor(daily-mailing): scope_from_dict — dict 기반 DashboardScope 생성"` (add the new test file first: `git add tests/test_dm_scope_from_dict.py`).

---

### Task B2: Subscription delivery columns and scope mapping

**Files:** Modify `agents/daily_mailing/subscription_bridge.py` · Test `tests/test_dm_subscription_delivery.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_dm_subscription_delivery.py
from __future__ import annotations

import sqlite3

from agents.daily_mailing.subscription_bridge import ensure_delivery_columns, subscription_to_scope


def test_ensure_delivery_columns_idempotent(tmp_path):
    conn = sqlite3.connect(tmp_path / "s.db")
    conn.execute("CREATE TABLE mail_subscription (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO mail_subscription (name) VALUES ('x')")
    ensure_delivery_columns(conn)
    ensure_delivery_columns(conn)
    row = conn.execute("SELECT delivery_mode, include_top_ma_news FROM mail_subscription").fetchone()
    assert row == ("approval", 1)


def test_ensure_delivery_columns_without_table_is_noop(tmp_path):
    conn = sqlite3.connect(tmp_path / "e.db")
    ensure_delivery_columns(conn)  # no error


def test_scope_mapping():
    scope = subscription_to_scope({"id": 19, "name": "파드셉", "emails": ["r@msd.com"], "brands": ["파드셉"],
                                   "delivery_mode": "auto", "include_top_ma_news": False})
    assert scope["delivery_mode"] == "auto"
    assert scope["include_top_ma_signals"] is False
    default = subscription_to_scope({"id": 1, "name": "n"})
    assert default["delivery_mode"] == "approval"
    assert default["include_top_ma_signals"] is True
```

- [ ] **Step 2:** Run → FAIL (`ImportError: ensure_delivery_columns`).
- [ ] **Step 3: Implement** — add after `ensure_test_request_column`:

```python
DELIVERY_MODES = ("approval", "auto")


def ensure_delivery_columns(conn) -> None:
    """mail_subscription.delivery_mode('approval'|'auto') / include_top_ma_news 멱등 ALTER.

    신규·기존 구독 모두 승인 모드로 시작한다 (설계 §3 — 품질 확인 후 구독별 자동 전환).
    """
    try:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(mail_subscription)").fetchall()}
    except Exception:
        return
    if not existing:
        return
    if "delivery_mode" not in existing:
        conn.execute("ALTER TABLE mail_subscription ADD COLUMN delivery_mode TEXT NOT NULL DEFAULT 'approval'")
    if "include_top_ma_news" not in existing:
        conn.execute("ALTER TABLE mail_subscription ADD COLUMN include_top_ma_news INTEGER NOT NULL DEFAULT 1")
    conn.commit()
```

In `subscription_to_scope`, change the two entries:

```python
        "delivery_mode": sub.get("delivery_mode") or "approval",
```
```python
        "include_top_ma_signals": bool(sub.get("include_top_ma_news", True)),
```

Also update the module docstring's first paragraph to say the scope is consumed by the production dispatcher (`agents/daily_mailing/dispatch.py`), not Hermes.

- [ ] **Step 4:** Run `tests/test_dm_subscription_delivery.py tests/test_daily_mailing_bridge.py` → PASS.
- [ ] **Step 5: Commit** `feat(daily-mailing): 구독 발송모드(approval|auto)·주요 MA 뉴스 포함 컬럼 + 스콥 매핑`

---

### Task B3: Storage — delivery columns, unique regular run, keep LLM notes, kanban without HTML

**Files:** Modify `agents/daily_mailing/storage.py` · Test `tests/test_dm_storage_delivery.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_dm_storage_delivery.py
from __future__ import annotations

import sqlite3

import pytest

from agents.daily_mailing.storage import (
    DuplicateRegularRunError,
    load_admin_kanban,
    persist_daily_mailing_run,
)


def _payload(**kw):
    base = {"run_id": "dm_19_a", "generated_at": "2026-09-18T08:00:00+09:00", "window_label": "이전 24시간",
            "lookback_hours": 24, "keywords": ["파드셉"], "media": [], "subscription_id": 19,
            "owner_email": "o@msd.com", "recipients": ["r@msd.com"], "status": "ready",
            "quality_report": {"status": "ready"}, "personas": ["ma_lead"],
            "items": [{"title": "t", "ma_implication": "LLM 시사점", "work_note": "LLM 시사점"}],
            "dashboard_scope": {}, "run_date": "2026-09-18", "is_test": False,
            "email_subject": "[MA Daily] 파드셉 · 9/18(금)", "email_html": "<p>x</p>",
            "delivery_status": "pending_approval", "approval_status": "pending"}
    base.update(kw)
    return base


def _row(db, run_id):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return dict(conn.execute("SELECT * FROM daily_mailing_run WHERE run_id=?", (run_id,)).fetchone())


def test_persist_stores_delivery_fields(tmp_path):
    db = tmp_path / "d.db"
    persist_daily_mailing_run(_payload(), articles=[], db_path=db)
    row = _row(db, "dm_19_a")
    assert row["run_date"] == "2026-09-18"
    assert row["is_test"] == 0
    assert row["email_subject"] == "[MA Daily] 파드셉 · 9/18(금)"
    assert row["email_html"] == "<p>x</p>"
    assert row["send_attempts"] == 0
    assert row["delivery_status"] == "pending_approval"


def test_duplicate_regular_run_rejected_but_tests_and_reimport_allowed(tmp_path):
    db = tmp_path / "d.db"
    persist_daily_mailing_run(_payload(), articles=[], db_path=db)
    persist_daily_mailing_run(_payload(), articles=[], db_path=db)  # 같은 run_id 재적재 OK
    with pytest.raises(DuplicateRegularRunError):
        persist_daily_mailing_run(_payload(run_id="dm_19_b"), articles=[], db_path=db)
    persist_daily_mailing_run(_payload(run_id="dm_19_c_test", is_test=True), articles=[], db_path=db)
    assert _row(db, "dm_19_c_test")["is_test"] == 1


def test_llm_items_not_overwritten_by_rule_based_notes(tmp_path):
    import json
    db = tmp_path / "d.db"
    persist_daily_mailing_run(_payload(), articles=[], db_path=db)
    items = json.loads(_row(db, "dm_19_a")["draft_items_json"])
    assert items[0]["work_note"] == "LLM 시사점"


def test_kanban_omits_email_html(tmp_path):
    db = tmp_path / "d.db"
    persist_daily_mailing_run(_payload(), articles=[], db_path=db)
    run = load_admin_kanban(db)["runs"][0]
    assert "email_html" not in run
    assert run["has_email_html"] is True
    assert run["email_subject"].startswith("[MA Daily]")
```

- [ ] **Step 2:** Run → FAIL (`ImportError: DuplicateRegularRunError`).
- [ ] **Step 3: Implement** in `agents/daily_mailing/storage.py`:

1. Below `RETENTION_DAYS = 183`, add:

```python
class DuplicateRegularRunError(RuntimeError):
    """같은 구독·같은 날(KST)의 정규 run 이 이미 있다 — INSERT OR REPLACE 로 발송 이력을 덮지 않는다."""
```

2. `_enrich_draft_items`: right after `out = dict(item)` add:

```python
        if "ma_implication" in out:
            # 엔진(LLM) 작성 항목 — 규칙 기반 문구로 덮어쓰지 않는다
            enriched.append(out)
            continue
```

3. `ensure_daily_mailing_tables`: add to the `daily_mailing_run` migration dict (after the `dashboard_scope_json` entry):

```python
            # 2026-09-18 프로덕션 발송 (설계 §6)
            "run_date": "ALTER TABLE daily_mailing_run ADD COLUMN run_date TEXT",
            "is_test": "ALTER TABLE daily_mailing_run ADD COLUMN is_test INTEGER NOT NULL DEFAULT 0",
            "email_subject": "ALTER TABLE daily_mailing_run ADD COLUMN email_subject TEXT",
            "email_html": "ALTER TABLE daily_mailing_run ADD COLUMN email_html TEXT",
            "send_attempts": "ALTER TABLE daily_mailing_run ADD COLUMN send_attempts INTEGER NOT NULL DEFAULT 0",
            "delivery_error": "ALTER TABLE daily_mailing_run ADD COLUMN delivery_error TEXT",
            "approved_by": "ALTER TABLE daily_mailing_run ADD COLUMN approved_by TEXT",
            "approved_at": "ALTER TABLE daily_mailing_run ADD COLUMN approved_at TEXT",
```

and immediately after that `for col, ddl in {...}.items(): ...` loop (before `existing_article = ...`):

```python
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_daily_mailing_run_regular "
            "ON daily_mailing_run(subscription_id, run_date) "
            "WHERE is_test = 0 AND subscription_id IS NOT NULL AND run_date IS NOT NULL"
        )
```

4. `persist_daily_mailing_run`: before `with sqlite3.connect(str(db_path)) as conn:` add

```python
    sub_id = payload.get("subscription_id")
    run_date = payload.get("run_date")
    is_test = 1 if payload.get("is_test") else 0
```

and make the first statements inside that `with` block:

```python
        if sub_id is not None and run_date and not is_test:
            clash = conn.execute(
                "SELECT run_id FROM daily_mailing_run WHERE subscription_id=? AND run_date=? "
                "AND is_test=0 AND run_id<>?",
                (sub_id, run_date, run_id),
            ).fetchone()
            if clash:
                raise DuplicateRegularRunError(f"구독 {sub_id} {run_date} 정규 run 이미 존재: {clash[0]}")
```

In the `INSERT OR REPLACE INTO daily_mailing_run (` column list change `counts_json, draft_items_json, dashboard_scope_json,` to
`counts_json, draft_items_json, dashboard_scope_json, run_date, is_test, email_subject, email_html,`,
change its `VALUES (...)` line to 32 placeholders:

```python
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
```

and in the values tuple, right after `json.dumps(payload.get("dashboard_scope"), ensure_ascii=False),` insert:

```python
                run_date,
                is_test,
                payload.get("email_subject"),
                payload.get("email_html"),
```

5. `load_admin_kanban`: in the `for r in runs:` loop after `run = dict(r)` add:

```python
        run["has_email_html"] = bool(run.pop("email_html", None))
```

- [ ] **Step 4:** Run `tests/test_dm_storage_delivery.py tests/test_daily_mailing_kanban_migration.py tests/test_daily_mailing_sync.py` → PASS.
- [ ] **Step 5: Commit** `feat(daily-mailing): run 발송 컬럼·정규 run 유일성·LLM 노트 보존·칸반 HTML 제외`

---

### Task B4: `run_store` — state queries and transitions

**Files:** Create `agents/daily_mailing/run_store.py` · Test `tests/test_dm_run_store.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_dm_run_store.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agents.daily_mailing.run_store import (
    claim_for_sending,
    expire_pending_before,
    find_regular_run,
    get_run,
    list_failed_sends,
    list_runs_for_date,
    record_error_run,
    update_run,
)
from agents.daily_mailing.storage import persist_daily_mailing_run

KST = timezone(timedelta(hours=9))


def _persist(db, run_id, *, sub=19, run_date="2026-09-18", is_test=False, delivery="pending_approval"):
    persist_daily_mailing_run({
        "run_id": run_id, "generated_at": "2026-09-18T08:00:00+09:00", "keywords": [], "subscription_id": sub,
        "status": "ready", "run_date": run_date, "is_test": is_test, "email_subject": "s", "email_html": "<p/>",
        "delivery_status": delivery, "approval_status": "pending", "recipients": ["r@msd.com"],
    }, articles=[], db_path=db)


def test_find_and_list(tmp_path):
    db = tmp_path / "d.db"
    _persist(db, "a")
    _persist(db, "t", is_test=True)
    assert find_regular_run(19, "2026-09-18", db_path=db)["run_id"] == "a"
    assert find_regular_run(19, "2026-09-17", db_path=db) is None
    assert [r["run_id"] for r in list_runs_for_date("2026-09-18", db_path=db)] == ["a"]
    assert {r["run_id"] for r in list_runs_for_date("2026-09-18", db_path=db, include_tests=True)} == {"a", "t"}


def test_update_allows_only_known_fields(tmp_path):
    db = tmp_path / "d.db"
    _persist(db, "a")
    update_run("a", db_path=db, delivery_status="sent", send_attempts=1)
    assert get_run("a", db_path=db)["delivery_status"] == "sent"
    with pytest.raises(ValueError):
        update_run("a", db_path=db, email_html="x")


def test_claim_is_exclusive(tmp_path):
    db = tmp_path / "d.db"
    _persist(db, "a")
    assert claim_for_sending("a", from_statuses=("pending_approval",), db_path=db) is True
    assert claim_for_sending("a", from_statuses=("pending_approval",), db_path=db) is False
    assert get_run("a", db_path=db)["delivery_status"] == "sending"


def test_expire_and_failed_list(tmp_path):
    db = tmp_path / "d.db"
    _persist(db, "old", run_date="2026-09-17")
    _persist(db, "new", sub=20)
    assert expire_pending_before("2026-09-18", db_path=db) == 1
    assert get_run("old", db_path=db)["delivery_status"] == "expired"
    update_run("new", db_path=db, delivery_status="send_failed", send_attempts=1)
    assert [r["run_id"] for r in list_failed_sends(db_path=db)] == ["new"]
    update_run("new", db_path=db, send_attempts=3)
    assert list_failed_sends(db_path=db) == []


def test_error_run_occupies_day(tmp_path):
    db = tmp_path / "d.db"
    run_id = record_error_run(subscription_id=21, run_date="2026-09-18", error="boom",
                              now=datetime(2026, 9, 18, 8, 0, tzinfo=KST), db_path=db)
    run = find_regular_run(21, "2026-09-18", db_path=db)
    assert run["run_id"] == run_id
    assert (run["status"], run["delivery_status"], run["delivery_error"]) == ("error", "error", "boom")
```

- [ ] **Step 2:** Run → FAIL (`ModuleNotFoundError`).
- [ ] **Step 3: Implement**

```python
# agents/daily_mailing/run_store.py
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
    return _rows(db_path, "SELECT * FROM daily_mailing_run WHERE delivery_status='send_failed' "
                          "AND send_attempts < ? ORDER BY generated_at", (max_attempts,))


def update_run(run_id: str, *, db_path: str | Path = DEFAULT_DB_PATH, **fields) -> None:
    unknown = set(fields) - UPDATABLE_FIELDS
    if unknown:
        raise ValueError(f"update_run: 허용되지 않은 필드 {sorted(unknown)}")
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    _write(db_path, f"UPDATE daily_mailing_run SET {cols} WHERE run_id=?", (*fields.values(), run_id))


def claim_for_sending(run_id: str, *, from_statuses: tuple[str, ...],
                      db_path: str | Path = DEFAULT_DB_PATH) -> bool:
    """조건부 UPDATE 로 발송 선점 — web(승인)·scheduler(재시도)의 이중 발송 방지."""
    placeholders = ",".join("?" for _ in from_statuses)
    return _write(db_path, "UPDATE daily_mailing_run SET delivery_status='sending' "
                           f"WHERE run_id=? AND delivery_status IN ({placeholders})",
                  (run_id, *from_statuses)) == 1


def expire_pending_before(run_date: str, *, db_path: str | Path = DEFAULT_DB_PATH) -> int:
    return _write(db_path, "UPDATE daily_mailing_run SET delivery_status='expired' "
                           "WHERE delivery_status='pending_approval' AND run_date < ?", (run_date,))


def record_error_run(*, subscription_id: int, run_date: str, error: str, now: datetime,
                     db_path: str | Path = DEFAULT_DB_PATH) -> str:
    """생성 예외를 그날의 정규 run 자리에 기록 (같은 날 재생성·LLM 재호출 폭주 방지, 설계 §6)."""
    run_id = f"dm_{subscription_id}_{now:%Y%m%d_%H%M%S}_error"
    created = datetime.now(timezone.utc)
    expires = (created + timedelta(days=RETENTION_DAYS)).isoformat()
    _write(db_path,
           "INSERT OR IGNORE INTO daily_mailing_run (run_id, generated_at, keywords_json, subscription_id, "
           "run_date, is_test, status, delivery_status, approval_status, delivery_error, expires_at, created_at) "
           "VALUES (?, ?, '[]', ?, ?, 0, 'error', 'error', 'not_required', ?, ?, ?)",
           (run_id, now.isoformat(), subscription_id, run_date, error[:500], expires, created.isoformat()))
    return run_id
```

- [ ] **Step 4:** Run → PASS (5 tests).
- [ ] **Step 5: Commit** `feat(daily-mailing): run_store — 발송 선점·만료·재시도 목록·오류 run`

---

### Task B5: `schedule` — due-time logic

**Files:** Create `agents/daily_mailing/schedule.py` · Test `tests/test_dm_schedule.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_dm_schedule.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agents.daily_mailing.schedule import is_due, parse_hhmm, scheduled_today

KST = timezone(timedelta(hours=9))
FRI_0759 = datetime(2026, 9, 18, 7, 59, tzinfo=KST)
FRI_0800 = datetime(2026, 9, 18, 8, 0, tzinfo=KST)


def test_daily():
    sub = {"active": True, "schedule": "Daily", "time": "08:00"}
    assert is_due(sub, FRI_0759) is False
    assert is_due(sub, FRI_0800) is True


def test_weekly_day_match():
    sub = {"active": True, "schedule": "Weekly", "time": "07:00", "weekDay": "Friday"}
    assert is_due(sub, FRI_0800) is True
    assert is_due({**sub, "weekDay": "Monday"}, FRI_0800) is False
    assert scheduled_today({**sub, "weekDay": None, "week_day": "Friday"}, FRI_0800) is True


def test_inactive_or_bad_time():
    assert is_due({"active": False, "schedule": "Daily", "time": "08:00"}, FRI_0800) is False
    assert is_due({"active": True, "schedule": "Daily", "time": "25:99"}, FRI_0800) is False
    assert parse_hhmm("7:5").hour == 7
    assert parse_hhmm("") is None
```

- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implement**

```python
# agents/daily_mailing/schedule.py
"""Daily Mailing 발송 시각 판정 (KST) — 순수 함수.

구독 schedule: 'Daily' 매일 / 'Weekly' + week_day('Monday'..). time 'HH:MM'.
10분 주기 dispatch 가 '오늘 예정 & 시각 도래 & 오늘 run 없음' 인 구독을 생성한다(재시작 따라잡기 포함).
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

KST = timezone(timedelta(hours=9))
WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def parse_hhmm(value: str | None) -> time | None:
    try:
        hh, mm = str(value or "").strip().split(":")
        return time(int(hh), int(mm))
    except (ValueError, TypeError):
        return None


def scheduled_today(sub: dict, now_kst: datetime) -> bool:
    if not sub.get("active"):
        return False
    schedule = sub.get("schedule")
    if schedule == "Daily":
        return True
    if schedule == "Weekly":
        return (sub.get("weekDay") or sub.get("week_day")) == WEEKDAY_NAMES[now_kst.weekday()]
    return False


def is_due(sub: dict, now_kst: datetime) -> bool:
    at = parse_hhmm(sub.get("time"))
    return at is not None and scheduled_today(sub, now_kst) and now_kst.time() >= at
```

- [ ] **Step 4:** Run → PASS. **Step 5: Commit** `feat(daily-mailing): 발송 시각 판정(schedule)`

---

### Task B6: `top_news` cache

**Files:** Create `agents/daily_mailing/top_news.py` · Test `tests/test_dm_top_news.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_dm_top_news.py
from agents.daily_mailing.top_news import load_top_ma_news, store_top_ma_news


def test_round_trip(tmp_path):
    db = tmp_path / "d.db"
    assert load_top_ma_news("2026-09-18", db_path=db) is None
    store_top_ma_news("2026-09-18", [{"title": "약평위"}], db_path=db)
    assert load_top_ma_news("2026-09-18", db_path=db) == [{"title": "약평위"}]
    store_top_ma_news("2026-09-18", [], db_path=db)
    assert load_top_ma_news("2026-09-18", db_path=db) == []
```

- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implement**

```python
# agents/daily_mailing/top_news.py
"""'오늘의 주요 MA 뉴스' 일일 캐시 (설계 §5.1) — 06:40 1회 산출, 구독 공통 재사용."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .storage import DEFAULT_DB_PATH

SCHEMA = ("CREATE TABLE IF NOT EXISTS daily_mailing_top_news ("
          "run_date TEXT PRIMARY KEY, items_json TEXT NOT NULL, created_at TEXT NOT NULL)")


def load_top_ma_news(run_date: str, *, db_path: str | Path = DEFAULT_DB_PATH) -> list[dict] | None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(SCHEMA)
        row = conn.execute("SELECT items_json FROM daily_mailing_top_news WHERE run_date=?", (run_date,)).fetchone()
    if row is None:
        return None
    items = json.loads(row[0] or "[]")
    return items if isinstance(items, list) else []


def store_top_ma_news(run_date: str, items: list[dict], *, db_path: str | Path = DEFAULT_DB_PATH) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(SCHEMA)
        conn.execute("INSERT OR REPLACE INTO daily_mailing_top_news (run_date, items_json, created_at) VALUES (?, ?, ?)",
                     (run_date, json.dumps(items, ensure_ascii=False), datetime.now(timezone.utc).isoformat()))
        conn.commit()
```

- [ ] **Step 4:** Run → PASS. **Step 5: Commit** `feat(daily-mailing): 주요 MA 뉴스 일일 캐시`

---

### Task B7: Gmail token from secret + dependencies + OAuth setup script

**Files:** Modify `agents/notify/gmail_delivery.py`, `requirements.txt` · Create `scripts/gmail_oauth_setup.py` · Test `tests/test_dm_gmail_token.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_dm_gmail_token.py
from __future__ import annotations

import base64
import json
import stat

import pytest

from agents.notify import gmail_delivery as g

TOKEN = {"token": "t", "refresh_token": "r", "scopes": ["https://www.googleapis.com/auth/gmail.send"]}


def test_materialize_from_base64(monkeypatch, tmp_path):
    raw = base64.b64encode(json.dumps(TOKEN).encode()).decode()
    monkeypatch.setenv("GOOGLE_TOKEN_JSON", raw)
    path = g.materialize_token_from_env(tmp_path / "secrets" / "t.json")
    assert json.loads(path.read_text()) == TOKEN
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert g.gmail_configured(path) is True


def test_materialize_from_raw_json(monkeypatch, tmp_path):
    monkeypatch.setenv("GOOGLE_TOKEN_JSON", json.dumps(TOKEN))
    assert g.materialize_token_from_env(tmp_path / "t.json").exists()


def test_no_env_uses_existing_file_or_none(monkeypatch, tmp_path):
    monkeypatch.delenv("GOOGLE_TOKEN_JSON", raising=False)
    assert g.materialize_token_from_env(tmp_path / "missing.json") is None
    existing = tmp_path / "e.json"
    existing.write_text(json.dumps(TOKEN))
    assert g.materialize_token_from_env(existing) == existing


def test_invalid_json_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("GOOGLE_TOKEN_JSON", "not-json")
    with pytest.raises(ValueError):
        g.materialize_token_from_env(tmp_path / "t.json")


def test_resolve_token_path_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("GOOGLE_TOKEN_PATH", str(tmp_path / "x.json"))
    assert g.resolve_token_path() == tmp_path / "x.json"
```

- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implement** — append to `agents/notify/gmail_delivery.py`:

```python
def resolve_token_path() -> Path:
    """Gmail 토큰 파일 위치: GOOGLE_TOKEN_PATH > 프로덕션 볼륨(/app/data/secrets) > repo data/secrets."""
    configured = os.environ.get("GOOGLE_TOKEN_PATH")
    if configured:
        return Path(configured)
    if Path("/app/data").exists():
        return Path("/app/data/secrets/google_token.json")
    return Path(__file__).resolve().parents[2] / "data" / "secrets" / "google_token.json"


def materialize_token_from_env(token_path: str | Path | None = None, *,
                               env_var: str = "GOOGLE_TOKEN_JSON") -> Path | None:
    """fly secret GOOGLE_TOKEN_JSON(base64 또는 raw JSON) → 토큰 파일(0600). 없으면 기존 파일 또는 None.

    잘못된 JSON 은 ValueError — 호출부(헬스체크/발송)가 실패로 표시한다.
    """
    path = Path(token_path) if token_path else resolve_token_path()
    raw = os.environ.get(env_var, "").strip()
    if not raw:
        return path if path.exists() else None
    text = raw
    if not raw.startswith("{"):
        try:
            text = base64.b64decode(raw, validate=True).decode("utf-8")
        except Exception as exc:
            raise ValueError(f"{env_var} base64 디코드 실패") from exc
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{env_var} 가 유효한 JSON 이 아님") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")
    os.chmod(path, 0o600)
    return path
```

`requirements.txt` — append:

```
google-api-python-client>=2.130.0
google-auth>=2.29.0
google-auth-oauthlib>=1.2.0
```

Install into the shared venv so imports work: `/Users/kimjeong-ae/MA_AI_Dossier/.venv/bin/python -m pip install "google-api-python-client>=2.130.0" "google-auth>=2.29.0" "google-auth-oauthlib>=1.2.0"`.

Create `scripts/gmail_oauth_setup.py`:

```python
"""개인 Gmail 발송 토큰 생성 (사용자가 로컬에서 1회 실행).

준비 (Google Cloud Console):
  1) OAuth 동의 화면 — 게시 상태를 '프로덕션'으로 (테스트 상태는 토큰이 7일 만에 만료)
  2) 사용자 인증 정보 → OAuth 클라이언트 ID(데스크톱 앱) → client_secret JSON 다운로드
실행:
  .venv/bin/python scripts/gmail_oauth_setup.py --client-secret ~/Downloads/client_secret.json
결과: data/secrets/google_token.json (0600) + fly secret 등록 명령 출력.
"""
from __future__ import annotations

import argparse
import base64
import os
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description="Gmail gmail.send OAuth 토큰 생성")
    parser.add_argument("--client-secret", required=True)
    parser.add_argument("--out", default=str(REPO_ROOT / "data" / "secrets" / "google_token.json"))
    args = parser.parse_args()

    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = InstalledAppFlow.from_client_secrets_file(args.client_secret, SCOPES).run_local_server(port=0)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(creds.to_json(), encoding="utf-8")
    os.chmod(out, 0o600)
    encoded = base64.b64encode(out.read_bytes()).decode("ascii")
    print(f"토큰 저장: {out}")
    print("프로덕션 등록(사용자 직접 실행):")
    print(f"  flyctl secrets set GOOGLE_TOKEN_JSON={encoded} -a ma-ai-dossier")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4:** Run `tests/test_dm_gmail_token.py tests/test_daily_mailing_final_gates.py` → PASS; `…/python -m py_compile scripts/gmail_oauth_setup.py` → no output.
- [ ] **Step 5: Commit** `feat(daily-mailing): Gmail 토큰 secret 복원·경로 해석 + OAuth 설정 스크립트 + google 라이브러리`

---

### Task B8: `dispatch` — orchestration

**Files:** Create `agents/daily_mailing/dispatch.py` · Test `tests/test_dm_dispatch.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_dm_dispatch.py
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from agents.daily_mailing.dispatch import (
    ApprovalError,
    DispatchDeps,
    approve_run,
    dispatch_due_subscriptions,
    health_check,
    reject_run,
    send_test,
)
from agents.daily_mailing.run_store import find_regular_run, get_run, list_runs_for_date

KST = timezone(timedelta(hours=9))
FRI_0810 = datetime(2026, 9, 18, 8, 10, tzinfo=KST)

DDL = """CREATE TABLE mail_subscription (
    id INTEGER PRIMARY KEY AUTOINCREMENT, owner_email TEXT NOT NULL, name TEXT NOT NULL,
    keywords_json TEXT NOT NULL, media_json TEXT NOT NULL, schedule TEXT NOT NULL, time TEXT NOT NULL,
    week_day TEXT, emails_json TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
    companies_json TEXT NOT NULL DEFAULT '[]', brands_json TEXT NOT NULL DEFAULT '[]',
    policy_topics_json TEXT NOT NULL DEFAULT '[]', disease_areas_json TEXT NOT NULL DEFAULT '[]',
    custom_sources_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    last_sent_at TEXT)"""


def _db(tmp_path, *subs):
    db = tmp_path / "d.db"
    with sqlite3.connect(db) as c:
        c.execute(DDL)
        c.execute("ALTER TABLE mail_subscription ADD COLUMN delivery_mode TEXT NOT NULL DEFAULT 'approval'")
        c.execute("ALTER TABLE mail_subscription ADD COLUMN include_top_ma_news INTEGER NOT NULL DEFAULT 1")
        for s in subs:
            c.execute("INSERT INTO mail_subscription (id, owner_email, name, keywords_json, media_json, schedule, time, "
                      "emails_json, brands_json, active, delivery_mode, created_at, updated_at) "
                      "VALUES (?, 'o@msd.com', ?, '[]', '[]', 'Daily', ?, ?, ?, ?, ?, 'x', 'x')",
                      (s["id"], s.get("name", "파드셉"), s.get("time", "08:00"), json.dumps(["r@msd.com"]),
                       json.dumps(["파드셉"]), 1 if s.get("active", True) else 0, s.get("mode", "approval")))
    return db


class Recorder:
    def __init__(self, results=None, raise_on_generate=False, send_ok=True):
        self.generated = []
        self.sent = []
        self.results = results or {}
        self.raise_on_generate = raise_on_generate
        self.send_ok = send_ok

    def generate(self, scope, *, name, now, top_ma_news, is_test):
        if self.raise_on_generate:
            raise RuntimeError("engine down")
        self.generated.append((scope.subscription_id, is_test))
        status = self.results.get(scope.subscription_id, "ready")
        run_id = f"dm_{scope.subscription_id}_{now:%H%M%S}" + ("_test" if is_test else "")
        payload = {"run_id": run_id, "generated_at": now.isoformat(), "keywords": list(scope.brands),
                   "subscription_id": int(scope.subscription_id), "recipients": list(scope.recipients),
                   "status": status, "run_date": now.date().isoformat(), "is_test": is_test,
                   "email_subject": ("[TEST] " if is_test else "") + f"[MA Daily] {name}",
                   "email_html": "<p>brief</p>", "items": []}
        return SimpleNamespace(run_id=run_id, status=status, payload=payload, articles=[])

    def send(self, *, recipients, subject, body_html):
        self.sent.append((list(recipients), subject))
        if self.send_ok:
            return {"ok": True, "sent": True, "gmail_message_id": f"m{len(self.sent)}"}
        return {"ok": False, "sent": False, "message": "token expired"}


def _deps(db, rec, now=FRI_0810, gmail_ready=True):
    return DispatchDeps(db_path=db, generate=rec.generate, load_top_ma_news=lambda d: [],
                        send=rec.send, now=lambda: now, gmail_ready=lambda: gmail_ready)


def test_not_due_does_nothing(tmp_path):
    db = _db(tmp_path, {"id": 19, "time": "09:00"})
    rec = Recorder()
    dispatch_due_subscriptions(_deps(db, rec))
    assert rec.generated == []


def test_approval_mode_waits_and_is_idempotent(tmp_path):
    db = _db(tmp_path, {"id": 19})
    rec = Recorder()
    dispatch_due_subscriptions(_deps(db, rec))
    run = find_regular_run(19, "2026-09-18", db_path=db)
    assert (run["delivery_status"], run["approval_status"]) == ("pending_approval", "pending")
    assert rec.sent == []
    summary = dispatch_due_subscriptions(_deps(db, rec, now=FRI_0810 + timedelta(minutes=10)))
    assert len(rec.generated) == 1 and summary["skipped"] == 1


def test_auto_mode_sends_and_marks_last_sent(tmp_path):
    db = _db(tmp_path, {"id": 19, "mode": "auto"})
    rec = Recorder()
    dispatch_due_subscriptions(_deps(db, rec))
    run = find_regular_run(19, "2026-09-18", db_path=db)
    assert run["delivery_status"] == "sent" and run["gmail_message_id"] == "m1"
    assert rec.sent == [(["r@msd.com"], "[MA Daily] 파드셉")]
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT last_sent_at FROM mail_subscription WHERE id=19").fetchone()[0]


def test_auto_no_news_is_sent_as_no_news(tmp_path):
    db = _db(tmp_path, {"id": 19, "mode": "auto"})
    dispatch_due_subscriptions(_deps(db, Recorder(results={"19": "no_news"})))
    assert find_regular_run(19, "2026-09-18", db_path=db)["delivery_status"] == "no_news_sent"


def test_held_is_not_sent_even_in_auto(tmp_path):
    db = _db(tmp_path, {"id": 19, "mode": "auto"})
    rec = Recorder(results={"19": "held"})
    dispatch_due_subscriptions(_deps(db, rec))
    assert find_regular_run(19, "2026-09-18", db_path=db)["delivery_status"] == "held"
    assert rec.sent == []


def test_generate_error_records_error_run_once(tmp_path):
    db = _db(tmp_path, {"id": 19})
    dispatch_due_subscriptions(_deps(db, Recorder(raise_on_generate=True)))
    run = find_regular_run(19, "2026-09-18", db_path=db)
    assert run["delivery_status"] == "error" and "engine down" in run["delivery_error"]
    rec = Recorder()
    dispatch_due_subscriptions(_deps(db, rec, now=FRI_0810 + timedelta(minutes=10)))
    assert rec.generated == []


def test_send_failure_retries_then_holds(tmp_path):
    db = _db(tmp_path, {"id": 19, "mode": "auto"})
    rec = Recorder(send_ok=False)
    dispatch_due_subscriptions(_deps(db, rec))   # 생성 즉시 발송 attempt 1 + 같은 주기 끝 재시도 attempt 2
    assert find_regular_run(19, "2026-09-18", db_path=db)["delivery_status"] == "send_failed"
    dispatch_due_subscriptions(_deps(db, rec))   # 다음 주기 재시도 attempt 3 → held
    dispatch_due_subscriptions(_deps(db, rec))   # held 는 더 이상 재시도하지 않음
    run = find_regular_run(19, "2026-09-18", db_path=db)
    assert (run["delivery_status"], run["send_attempts"]) == ("held", 3)
    assert run["delivery_error"] == "token expired"


def test_approve_and_reject(tmp_path):
    db = _db(tmp_path, {"id": 19}, {"id": 20})
    rec = Recorder()
    deps = _deps(db, rec)
    dispatch_due_subscriptions(deps)
    r19 = find_regular_run(19, "2026-09-18", db_path=db)["run_id"]
    r20 = find_regular_run(20, "2026-09-18", db_path=db)["run_id"]
    sent = approve_run(r19, approver="admin@msd.com", deps=deps)
    assert sent["delivery_status"] == "sent" and sent["approved_by"] == "admin@msd.com"
    with pytest.raises(ApprovalError):
        approve_run(r19, approver="admin@msd.com", deps=deps)
    rejected = reject_run(r20, approver="admin@msd.com", deps=deps)
    assert (rejected["delivery_status"], rejected["approval_status"]) == ("held", "rejected")
    with pytest.raises(LookupError):
        approve_run("nope", approver="a", deps=deps)


def test_stale_pending_expires_next_day(tmp_path):
    db = _db(tmp_path, {"id": 19})
    rec = Recorder()
    dispatch_due_subscriptions(_deps(db, rec))
    run_id = find_regular_run(19, "2026-09-18", db_path=db)["run_id"]
    next_day = _deps(db, rec, now=FRI_0810 + timedelta(days=1))
    with pytest.raises(ApprovalError):
        approve_run(run_id, approver="a", deps=next_day)
    assert get_run(run_id, db_path=db)["delivery_status"] == "expired"


def test_send_test_goes_to_requester_only_and_does_not_block_regular(tmp_path):
    db = _db(tmp_path, {"id": 19, "time": "09:00"})
    rec = Recorder()
    deps = _deps(db, rec)
    out = send_test(19, requester_email="me@msd.com", deps=deps)
    assert out["delivery_status"] == "sent"
    assert rec.sent == [(["me@msd.com"], "[TEST] [MA Daily] 파드셉")]
    assert find_regular_run(19, "2026-09-18", db_path=db) is None
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT last_sent_at FROM mail_subscription WHERE id=19").fetchone()[0] is None
    assert [r["is_test"] for r in list_runs_for_date("2026-09-18", db_path=db, include_tests=True)] == [1]


def test_health_check(tmp_path):
    db = _db(tmp_path, {"id": 19}, {"id": 20, "time": "23:00"})
    rec = Recorder(results={"19": "held"})
    report = health_check(_deps(db, rec, gmail_ready=False))
    assert report["ok"] is False
    problems = {(p["subscription_id"], p["problem"]) for p in report["problems"]}
    assert (19, "not_generated") in problems and (None, "gmail_token_invalid") in problems
    assert all(p["subscription_id"] != 20 for p in report["problems"])   # 23:00 아직 예정 전
    dispatch_due_subscriptions(_deps(db, rec))
    problems = {(p["subscription_id"], p["problem"]) for p in health_check(_deps(db, rec))["problems"]}
    assert problems == {(19, "held")}
```

- [ ] **Step 2:** Run → FAIL (`ModuleNotFoundError: agents.daily_mailing.dispatch`).
- [ ] **Step 3: Implement**

```python
# agents/daily_mailing/dispatch.py
"""Daily Mailing 발송 오케스트레이션 — 판정·생성·승인·발송·재시도·테스트·헬스체크.

설계: docs/superpowers/specs/2026-09-18-daily-mailing-service-design.md §4·§6·§8.
콘텐츠 생성은 engine.generate_run(슬라이스 A)을 DispatchDeps.generate 로 주입받는다.
상태: pending_approval → (승인) → sending → sent / send_failed(→재시도 최대 3회→held)
      ready_to_send(자동) → sending → sent | no_news_sent ; held / error / expired
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .dashboard_scope import scope_from_dict
from .run_store import (
    claim_for_sending,
    expire_pending_before,
    find_regular_run,
    get_run,
    list_failed_sends,
    list_runs_for_date,
    record_error_run,
    update_run,
)
from .schedule import KST, is_due
from .storage import DEFAULT_DB_PATH, ensure_daily_mailing_tables, persist_daily_mailing_run
from .subscription_bridge import ensure_delivery_columns, subscription_to_scope

logger = logging.getLogger(__name__)

MAX_SEND_ATTEMPTS = 3
REPO_ROOT = Path(__file__).resolve().parents[2]
PROBLEM_STATUSES = ("held", "send_failed", "error", "expired")


class ApprovalError(ValueError):
    """승인/반려할 수 없는 상태."""


def _default_now() -> datetime:
    return datetime.now(KST)


@dataclass
class DispatchDeps:
    db_path: str | Path = DEFAULT_DB_PATH
    generate: Callable[..., Any] | None = None          # (scope, *, name, now, top_ma_news, is_test) -> RunResult
    load_top_ma_news: Callable[[str], list] | None = None  # run_date -> list[BriefItem]
    send: Callable[..., dict] | None = None             # (*, recipients, subject, body_html) -> dict
    now: Callable[[], datetime] = _default_now
    gmail_ready: Callable[[], bool] = lambda: False

    @classmethod
    def production(cls, db_path: str | Path = DEFAULT_DB_PATH) -> "DispatchDeps":
        """실제 엔진·Gmail 연결. 엔진/LLM 클라이언트는 첫 생성 시점에 지연 생성."""
        from agents.notify.gmail_delivery import gmail_configured, materialize_token_from_env, send_gmail_message

        from .brief import BriefItem
        from .engine import EngineConfig, EngineDeps, generate_run, generate_top_ma_news
        from .top_news import load_top_ma_news, store_top_ma_news

        config = EngineConfig.from_settings(_load_settings())
        cache: dict[str, Any] = {}

        def engine_deps():
            if "deps" not in cache:
                cache["deps"] = EngineDeps.default(db_path=str(db_path))
            return cache["deps"]

        def generate(scope, **kwargs):
            return generate_run(scope, deps=engine_deps(), config=config, **kwargs)

        def top_ma_news(run_date: str) -> list:
            cached = load_top_ma_news(run_date, db_path=db_path)
            if cached is not None:
                return [BriefItem.from_dict(d) for d in cached]
            items = generate_top_ma_news(deps=engine_deps(), config=config)
            store_top_ma_news(run_date, [i.to_dict() for i in items], db_path=db_path)
            return items

        def token_path():
            try:
                return materialize_token_from_env()
            except ValueError as exc:
                logger.error("[daily_mailing] Gmail 토큰 secret 오류: %s", exc)
                return None

        def send(**kwargs) -> dict:
            path = token_path()
            if path is None:
                return {"ok": False, "sent": False, "message": "Gmail 토큰 없음 (GOOGLE_TOKEN_JSON)"}
            return send_gmail_message(token_path=path, **kwargs)

        def gmail_ready() -> bool:
            path = token_path()
            return bool(path) and gmail_configured(path)

        return cls(db_path=db_path, generate=generate, load_top_ma_news=top_ma_news, send=send,
                   gmail_ready=gmail_ready)


def _load_settings() -> dict:
    try:
        return json.loads((REPO_ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def _json_list(raw) -> list:
    try:
        value = json.loads(raw or "[]")
    except Exception:
        return []
    return value if isinstance(value, list) else []


def _sub_row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    return {
        "id": d["id"], "name": d["name"], "owner_email": d.get("owner_email"),
        "keywords": _json_list(d.get("keywords_json")), "media": _json_list(d.get("media_json")),
        "schedule": d.get("schedule"), "time": d.get("time"), "weekDay": d.get("week_day"),
        "emails": _json_list(d.get("emails_json")), "active": bool(d.get("active")),
        "companies": _json_list(d.get("companies_json")), "brands": _json_list(d.get("brands_json")),
        "policy_topics": _json_list(d.get("policy_topics_json")),
        "disease_areas": _json_list(d.get("disease_areas_json")),
        "custom_sources": _json_list(d.get("custom_sources_json")),
        "delivery_mode": d.get("delivery_mode") or "approval",
        "include_top_ma_news": bool(d.get("include_top_ma_news", 1)),
        "last_sent_at": d.get("last_sent_at"),
    }


def load_subscriptions(db_path: str | Path, *, active_only: bool = True) -> list[dict]:
    with sqlite3.connect(str(db_path)) as conn:
        ensure_delivery_columns(conn)
        conn.row_factory = sqlite3.Row
        sql = "SELECT * FROM mail_subscription" + (" WHERE active = 1" if active_only else "") + " ORDER BY id"
        return [_sub_row_to_dict(r) for r in conn.execute(sql).fetchall()]


def _now(deps: DispatchDeps) -> datetime:
    return deps.now().astimezone(KST)


def _top_news(deps: DispatchDeps, run_date: str) -> list:
    if deps.load_top_ma_news is None:
        return []
    try:
        return list(deps.load_top_ma_news(run_date) or [])
    except Exception:
        logger.exception("[daily_mailing] 주요 MA 뉴스 로드 실패 — 없이 진행")
        return []


def _persist(result, deps: DispatchDeps, *, delivery_status: str, approval_status: str) -> dict | None:
    payload = dict(result.payload)
    payload["delivery_status"] = delivery_status
    payload["approval_status"] = approval_status
    persist_daily_mailing_run(payload, articles=result.articles, db_path=deps.db_path)
    return get_run(result.run_id, db_path=deps.db_path)


def _mark_last_sent(subscription_id: int, sent_at: str, db_path: str | Path) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("UPDATE mail_subscription SET last_sent_at=? WHERE id=?", (sent_at, subscription_id))
        conn.commit()


def send_run(run_id: str, *, deps: DispatchDeps, from_statuses: tuple[str, ...],
             recipients_override: list[str] | None = None) -> dict | None:
    run = get_run(run_id, db_path=deps.db_path)
    if run is None or not claim_for_sending(run_id, from_statuses=from_statuses, db_path=deps.db_path):
        return run
    recipients = recipients_override or _json_list(run.get("recipients_json"))
    attempts = int(run.get("send_attempts") or 0) + 1
    try:
        res = deps.send(recipients=recipients, subject=run.get("email_subject") or "",
                        body_html=run.get("email_html") or "")
    except Exception as exc:
        res = {"ok": False, "sent": False, "message": f"{type(exc).__name__}: {exc}"}
    if res.get("ok") and res.get("sent"):
        sent_at = _now(deps).isoformat()
        update_run(run_id, db_path=deps.db_path,
                   delivery_status="no_news_sent" if run.get("status") == "no_news" else "sent",
                   sent_at=sent_at, gmail_message_id=res.get("gmail_message_id"),
                   send_attempts=attempts, delivery_error=None)
        if not run.get("is_test") and run.get("subscription_id") is not None:
            _mark_last_sent(run["subscription_id"], sent_at, deps.db_path)
    else:
        update_run(run_id, db_path=deps.db_path,
                   delivery_status="send_failed" if attempts < MAX_SEND_ATTEMPTS else "held",
                   send_attempts=attempts, delivery_error=str(res.get("message") or "send failed")[:500])
    return get_run(run_id, db_path=deps.db_path)


def _route_new_run(result, sub: dict, deps: DispatchDeps) -> dict | None:
    if result.status == "held":
        return _persist(result, deps, delivery_status="held", approval_status="not_required")
    if sub.get("delivery_mode") == "auto":
        _persist(result, deps, delivery_status="ready_to_send", approval_status="not_required")
        return send_run(result.run_id, deps=deps, from_statuses=("ready_to_send",))
    return _persist(result, deps, delivery_status="pending_approval", approval_status="pending")


def retry_failed_sends(deps: DispatchDeps) -> int:
    runs = list_failed_sends(db_path=deps.db_path, max_attempts=MAX_SEND_ATTEMPTS)
    for run in runs:
        send_run(run["run_id"], deps=deps, from_statuses=("send_failed",))
    return len(runs)


def dispatch_due_subscriptions(deps: DispatchDeps) -> dict:
    """10분 주기: 만료 처리 → 발송 시각 도래 구독 생성·분기 → 실패 발송 재시도."""
    now = _now(deps)
    run_date = now.date().isoformat()
    ensure_daily_mailing_tables(deps.db_path)
    summary: dict[str, Any] = {"run_date": run_date,
                               "expired": expire_pending_before(run_date, db_path=deps.db_path),
                               "generated": [], "skipped": 0, "errors": []}
    top: list | None = None
    for sub in load_subscriptions(deps.db_path):
        if not is_due(sub, now):
            continue
        if find_regular_run(sub["id"], run_date, db_path=deps.db_path):
            summary["skipped"] += 1
            continue
        try:
            if top is None:
                top = _top_news(deps, run_date)
            scope = scope_from_dict(subscription_to_scope(sub))
            result = deps.generate(scope, name=sub["name"], now=now, top_ma_news=top, is_test=False)
            run = _route_new_run(result, sub, deps)
        except Exception as exc:
            logger.exception("[daily_mailing] 구독 %s 생성 실패", sub["id"])
            record_error_run(subscription_id=sub["id"], run_date=run_date,
                             error=f"{type(exc).__name__}: {exc}", now=now, db_path=deps.db_path)
            summary["errors"].append(sub["id"])
            continue
        summary["generated"].append({"subscription_id": sub["id"], "run_id": result.run_id,
                                     "delivery_status": (run or {}).get("delivery_status")})
    summary["retried"] = retry_failed_sends(deps)
    return summary


def approve_run(run_id: str, *, approver: str, deps: DispatchDeps) -> dict:
    run = get_run(run_id, db_path=deps.db_path)
    if run is None:
        raise LookupError(run_id)
    if run.get("delivery_status") != "pending_approval" or run.get("approval_status") != "pending":
        raise ApprovalError(f"승인 대기 상태가 아님: {run.get('delivery_status')}/{run.get('approval_status')}")
    now = _now(deps)
    if run.get("run_date") and run["run_date"] < now.date().isoformat():
        update_run(run_id, db_path=deps.db_path, delivery_status="expired")
        raise ApprovalError("당일 승인 기한이 지나 만료되었습니다")
    update_run(run_id, db_path=deps.db_path, approval_status="approved",
               approved_by=approver, approved_at=now.isoformat())
    return send_run(run_id, deps=deps, from_statuses=("pending_approval",)) or {}


def reject_run(run_id: str, *, approver: str, deps: DispatchDeps) -> dict:
    run = get_run(run_id, db_path=deps.db_path)
    if run is None:
        raise LookupError(run_id)
    if run.get("delivery_status") != "pending_approval":
        raise ApprovalError(f"승인 대기 상태가 아님: {run.get('delivery_status')}")
    update_run(run_id, db_path=deps.db_path, delivery_status="held", approval_status="rejected",
               approved_by=approver, approved_at=_now(deps).isoformat())
    return get_run(run_id, db_path=deps.db_path) or {}


def send_test(subscription_id: int, *, requester_email: str, deps: DispatchDeps) -> dict:
    """테스트 메일: 게이트·승인 모드와 무관하게 즉시 생성 → 요청자에게만 [TEST] 발송 (정규 run 과 분리)."""
    sub = next((s for s in load_subscriptions(deps.db_path, active_only=False) if s["id"] == subscription_id), None)
    if sub is None:
        raise LookupError(subscription_id)
    now = _now(deps)
    scope = scope_from_dict(subscription_to_scope(sub))
    result = deps.generate(scope, name=sub["name"], now=now,
                           top_ma_news=_top_news(deps, now.date().isoformat()), is_test=True)
    _persist(result, deps, delivery_status="ready_to_send", approval_status="not_required")
    run = send_run(result.run_id, deps=deps, from_statuses=("ready_to_send",),
                   recipients_override=[requester_email])
    return {"run_id": result.run_id, "status": result.status, "delivery_status": (run or {}).get("delivery_status")}


def health_check(deps: DispatchDeps) -> dict:
    """오늘 이미 발송 시각이 지난 구독 중 미생성·보류·발송실패·오류·만료 + Gmail 토큰 상태."""
    now = _now(deps)
    run_date = now.date().isoformat()
    runs = {r["subscription_id"]: r for r in list_runs_for_date(run_date, db_path=deps.db_path)}
    problems: list[dict] = []
    for sub in load_subscriptions(deps.db_path):
        if not is_due(sub, now):
            continue
        run = runs.get(sub["id"])
        if run is None:
            problems.append({"subscription_id": sub["id"], "name": sub["name"], "problem": "not_generated"})
        elif run.get("delivery_status") in PROBLEM_STATUSES:
            problems.append({"subscription_id": sub["id"], "name": sub["name"],
                             "problem": run["delivery_status"], "detail": run.get("delivery_error")})
    try:
        gmail_ok = bool(deps.gmail_ready())
    except Exception:
        gmail_ok = False
    if not gmail_ok:
        problems.append({"subscription_id": None, "name": "-", "problem": "gmail_token_invalid"})
    return {"run_date": run_date, "checked_at": now.isoformat(), "ok": not problems,
            "problems": problems, "gmail_ready": gmail_ok}
```

- [ ] **Step 4:** Run `tests/test_dm_dispatch.py` → PASS (12 tests).
- [ ] **Step 5: Commit** `feat(daily-mailing): dispatch — 판정·승인/자동 분기·발송 선점·재시도·테스트·헬스체크`

---

### Task B9: Scope validation

**Files:** Create `agents/daily_mailing/scope_validation.py` · Test `tests/test_dm_scope_validation.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_dm_scope_validation.py
from agents.daily_mailing.scope_validation import MSD_BRANDS, validate_scope


def test_empty_scope_rejected():
    errors, _ = validate_scope({"name": "x"}, [])
    assert errors


def test_policy_only_scope_allowed():
    errors, warnings = validate_scope({"name": "정책", "policy_topics": ["약가 유연계약"]}, MSD_BRANDS)
    assert errors == [] and warnings == []


def test_name_brand_missing_from_terms_warns():
    errors, warnings = validate_scope({"name": "파드셉", "keywords": ["약가 인하", "급여 등재"]}, ["파드셉", "키트루다"])
    assert errors == []
    assert warnings == ["구독 이름의 '파드셉' 가 키워드·브랜드에 없습니다 — 이 약제 기사는 수집되지 않습니다"]
    _, ok = validate_scope({"name": "파드셉", "brands": ["파드셉"]}, ["파드셉"])
    assert ok == []
```

- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implement**

```python
# agents/daily_mailing/scope_validation.py
"""구독 스콥 입력 검증 (설계 §7).

- 스콥 항목(keywords·brands·companies·policy_topics·disease_areas)이 모두 비면 오류.
- 구독 이름에 알려진 약제명이 있는데 스콥에 없으면 경고 (2026-08 '파드셉' 구독 사례:
  이름만 파드셉이고 키워드는 '약가 인하'·'급여 등재' → 파드셉 기사 0건).
"""
from __future__ import annotations

from typing import Iterable

SCOPE_FIELDS = ("keywords", "brands", "companies", "policy_topics", "disease_areas")
MSD_BRANDS = ("키트루다", "가다실", "린파자", "웰리렉", "브리디온", "에멘드", "윈레브에어", "박스뉴반스",
              "엔플론시아", "Keytruda", "Gardasil", "Lynparza", "Welireg")


def validate_scope(sub: dict, known_brands: Iterable[str]) -> tuple[list[str], list[str]]:
    terms = [str(t) for field in SCOPE_FIELDS for t in (sub.get(field) or []) if str(t).strip()]
    errors: list[str] = []
    if not terms:
        errors.append("모니터링 스콥이 비어 있습니다 — 키워드·브랜드·회사·정책토픽·질환 중 1개 이상 필요합니다")
    name = str(sub.get("name") or "").lower()
    lowered = [t.lower() for t in terms]
    warnings: list[str] = []
    for brand in sorted({b.strip() for b in known_brands if b and len(b.strip()) >= 2}, key=len, reverse=True):
        b = brand.lower()
        if b in name and not any(b in t for t in lowered):
            warnings.append(f"구독 이름의 '{brand}' 가 키워드·브랜드에 없습니다 — 이 약제 기사는 수집되지 않습니다")
    return errors, warnings
```

- [ ] **Step 4:** Run → PASS. **Step 5: Commit** `feat(daily-mailing): 구독 스콥 검증 (빈 스콥 거부 · 이름-약제 불일치 경고)`

---

### Task B10: API — subscription fields/validation, admin run endpoints, test-request, test-send

**Files:** Modify `api/server.py` · Test `tests/test_dm_api.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_dm_api.py
from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import api.server as s
from agents.daily_mailing import dispatch
from agents.daily_mailing.storage import persist_daily_mailing_run
from api.auth import _issue_token


def _admin():
    return {"Authorization": f"Bearer {_issue_token('admin@msd.com', 'admin')}"}


def _user():
    return {"Authorization": f"Bearer {_issue_token('me@msd.com', 'user')}"}


def test_coerce_delivery_fields():
    out = s._coerce_mail_sub_input({"deliveryMode": "auto", "includeTopMaNews": False})
    assert out == {"delivery_mode": "auto", "include_top_ma_news": 0}
    bad = s._coerce_mail_sub_input({"deliveryMode": "sometimes"})
    assert isinstance(bad, tuple)


def test_row_dict_has_delivery_fields():
    row = (1, "n", "[]", "[]", "Daily", "08:00", None, '["r@msd.com"]', 1, "c", "u", None,
           "[]", "[]", "[]", "[]", "[]", None, "auto", 0)
    item = s._mail_sub_row_to_dict(row)
    assert item["deliveryMode"] == "auto" and item["includeTopMaNews"] is False


def test_scope_for_validation_merges_existing():
    existing = {"name": "파드셉", "keywords": ["약가 인하"], "brands": [], "companies": [],
                "policy_topics": [], "disease_areas": []}
    merged = s._mail_scope_for_validation({"brands": ["파드셉"]}, existing)
    assert merged["keywords"] == ["약가 인하"] and merged["brands"] == ["파드셉"] and merged["name"] == "파드셉"


def _pending_run(db):
    persist_daily_mailing_run({"run_id": "dm_19_x", "generated_at": "2026-09-18T08:00:00+09:00", "keywords": [],
                               "subscription_id": 19, "status": "ready", "run_date": "2026-09-18",
                               "is_test": False, "email_subject": "[MA Daily] 파드셉", "email_html": "<p>brief</p>",
                               "recipients": ["r@msd.com"], "delivery_status": "pending_approval",
                               "approval_status": "pending"}, articles=[], db_path=db)


def _patch_deps(monkeypatch, db, sent):
    from datetime import datetime, timedelta, timezone
    now = datetime(2026, 9, 18, 9, 0, tzinfo=timezone(timedelta(hours=9)))
    deps = dispatch.DispatchDeps(db_path=db, send=lambda **kw: (sent.append(kw), {"ok": True, "sent": True, "gmail_message_id": "m"})[1],
                                 now=lambda: now, gmail_ready=lambda: True)
    monkeypatch.setattr(dispatch.DispatchDeps, "production", classmethod(lambda cls, db_path=None: deps))


def test_admin_preview_approve_reject_and_health(monkeypatch, tmp_path):
    db = tmp_path / "d.db"
    _pending_run(db)
    sent = []
    _patch_deps(monkeypatch, db, sent)
    c = s.app.test_client()
    assert c.post("/api/admin/daily-mailing/runs/dm_19_x/approve", headers=_user()).status_code == 403
    preview = c.get("/api/admin/daily-mailing/runs/dm_19_x/preview", headers=_admin()).get_json()
    assert preview["html"] == "<p>brief</p>" and preview["subject"] == "[MA Daily] 파드셉"
    r = c.post("/api/admin/daily-mailing/runs/dm_19_x/approve", headers=_admin())
    assert r.status_code == 200 and r.get_json()["run"]["delivery_status"] == "sent"
    assert sent[0]["recipients"] == ["r@msd.com"]
    again = c.post("/api/admin/daily-mailing/runs/dm_19_x/approve", headers=_admin())
    assert again.status_code == 409
    assert c.post("/api/admin/daily-mailing/runs/nope/reject", headers=_admin()).status_code == 404
    import sqlite3
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE mail_subscription (id INTEGER PRIMARY KEY, owner_email TEXT, name TEXT,
            keywords_json TEXT, media_json TEXT, schedule TEXT, time TEXT, week_day TEXT, emails_json TEXT,
            active INTEGER, companies_json TEXT, brands_json TEXT, policy_topics_json TEXT,
            disease_areas_json TEXT, custom_sources_json TEXT, created_at TEXT, updated_at TEXT, last_sent_at TEXT)""")
    health = c.get("/api/admin/daily-mailing/health", headers=_admin()).get_json()
    assert health["ok"] is True and health["gmail_ready"] is True


def test_test_request_runs_in_background(monkeypatch):
    calls = []
    monkeypatch.setattr(s, "_mail_sub_exists", lambda item_id, owner: True)
    monkeypatch.setattr(dispatch.DispatchDeps, "production", classmethod(lambda cls, db_path=None: SimpleNamespace()))
    monkeypatch.setattr(dispatch, "send_test", lambda sid, requester_email, deps: calls.append((sid, requester_email)) or {})
    r = s.app.test_client().post("/api/mail-subscriptions/19/test-request", headers=_user())
    assert r.status_code == 202
    assert "me@msd.com" in r.get_json()["message"]
    for _ in range(50):
        if calls:
            break
        time.sleep(0.02)
    assert calls == [(19, "me@msd.com")]
```

- [ ] **Step 2:** Run `tests/test_dm_api.py` → FAIL (missing endpoints/helpers).
- [ ] **Step 3: Implement** in `api/server.py`:

1. **Migration** — right after the existing `mail_subscription.test_request_json` migration `try/except` block, add:

```python
# mail_subscription.delivery_mode / include_top_ma_news 멱등 마이그레이션 (Daily Mailing 프로덕션 발송)
try:
    from agents.daily_mailing.subscription_bridge import ensure_delivery_columns as _ensure_mail_sub_delivery_cols  # noqa: E402
    with db._connect() as _conn:
        _ensure_mail_sub_delivery_cols(_conn)
except Exception as _e:  # pragma: no cover
    logger.warning("mail_subscription 발송모드 컬럼 마이그레이션 실패: %s", _e)
```

2. **Row mapping** — in `_mail_sub_row_to_dict` add after the `"test_request"` entry:

```python
        "deliveryMode": r[18] if len(r) > 18 and r[18] else "approval",
        "includeTopMaNews": bool(r[19]) if len(r) > 19 and r[19] is not None else True,
```

and change `_MAIL_SUB_COLS` to end with `..., custom_sources_json, test_request_json, delivery_mode, include_top_ma_news"`.

3. **Input coercion** — in `_coerce_mail_sub_input`, before `return out`, add:

```python
    if "deliveryMode" in body:
        mode = body.get("deliveryMode")
        if mode not in ("approval", "auto"):
            return ({}, "deliveryMode must be approval|auto")
        out["delivery_mode"] = mode
    if "includeTopMaNews" in body:
        out["include_top_ma_news"] = 1 if body.get("includeTopMaNews") else 0
```

4. **Validation helpers** — add right after `_coerce_mail_sub_input`:

```python
_MAIL_SCOPE_BODY_KEYS = {"name": "name", "keywords": "keywords", "brands": "brands", "companies": "companies",
                         "policyTopics": "policy_topics", "diseaseAreas": "disease_areas"}


def _known_mail_brands() -> list[str]:
    """이름-약제 불일치 경고용 약제 사전: MSD 브랜드 + 경쟁사 뉴스 아카이브 브랜드."""
    from agents.daily_mailing.scope_validation import MSD_BRANDS
    brands = set(MSD_BRANDS)
    try:
        with db._connect() as conn:
            brands.update(r[0] for r in conn.execute(
                "SELECT DISTINCT brand FROM competitor_news WHERE brand IS NOT NULL AND brand <> ''"))
    except Exception:
        pass
    return sorted(brands)


def _mail_scope_for_validation(body: dict, existing: dict | None = None) -> dict:
    """검증 대상 스콥 = 기존 구독 값(수정 시) 위에 요청 본문 값을 덮어쓴 것."""
    merged: dict = {"name": "", "keywords": [], "brands": [], "companies": [], "policy_topics": [], "disease_areas": []}
    if existing:
        for key in merged:
            merged[key] = existing.get(key, merged[key])
    for body_key, key in _MAIL_SCOPE_BODY_KEYS.items():
        if body_key in body:
            merged[key] = body.get(body_key) or ("" if key == "name" else [])
    return merged


def _mail_sub_exists(item_id: int, owner: str) -> bool:
    with db._connect() as conn:
        return conn.execute("SELECT 1 FROM mail_subscription WHERE id=? AND owner_email=?",
                            (item_id, owner)).fetchone() is not None
```

5. **Create** — in `mail_sub_create`, right after `fields = result` add:

```python
    from agents.daily_mailing.scope_validation import validate_scope
    errors, warnings = validate_scope(_mail_scope_for_validation(body), _known_mail_brands())
    if errors:
        return jsonify({"error": errors[0], "code": "INVALID"}), 400
```

and change its success return to `return jsonify({"item": _mail_sub_row_to_dict(row), "warnings": warnings}), 201`.

6. **Update** — in `mail_sub_update`, right after `fields = result` / the empty-fields check, add:

```python
    warnings: list[str] = []
    if set(_MAIL_SCOPE_BODY_KEYS) & set(body):
        from agents.daily_mailing.scope_validation import validate_scope
        with db._connect() as conn:
            current = conn.execute(f"SELECT {_MAIL_SUB_COLS} FROM mail_subscription WHERE id=? AND owner_email=?",
                                   (item_id, owner)).fetchone()
        if current is None:
            return jsonify({"error": "not found", "code": "NOT_FOUND"}), 404
        errors, warnings = validate_scope(
            _mail_scope_for_validation(body, _mail_sub_row_to_dict(current)), _known_mail_brands())
        if errors:
            return jsonify({"error": errors[0], "code": "INVALID"}), 400
```

and change its success return to `return jsonify({"item": _mail_sub_row_to_dict(row), "warnings": warnings})`.

(`_mail_sub_row_to_dict` returns `policy_topics`/`disease_areas` keys, matching `_mail_scope_for_validation`.)

7. **test-send ('최근 발송 보기')** — in `mail_sub_test_send`:
   - change the `no_history["message"]` to `"아직 생성된 브리핑이 없습니다 — 구독 발송 시각이 되면 서버가 생성합니다."`;
   - `load_latest_run_for_subscription` falls back to other subscriptions' runs, so right after `run = load_latest_run_for_subscription(item_id, owner)` add:

```python
        if run is not None and run.get("subscription_id") != item_id:
            run = None  # 다른 구독의 run 으로 폴백하지 않는다
```
   - replace `body_html = None` with `body_html = run.get("email_html") or None` and change `if html_path:` to `if body_html is None and html_path:`;
   - replace the `subject = ...` line with
     `subject = run.get("email_subject") or (f"[Daily Mailing] {item['name']}" + (f" · {generated}" if generated else ""))`;
   - change the returned `"message"` to `"이 구독으로 생성된 최신 브리핑입니다."`.

8. **test-request** — replace the whole body of `mail_sub_test_request` (keep decorators and signature) with:

```python
    """테스트 메일 — 서버가 즉시(백그라운드) 브리핑을 생성해 요청자에게만 [TEST] 발송 (설계 §6)."""
    owner = request.user["sub"]  # type: ignore[attr-defined]
    try:
        if not _mail_sub_exists(item_id, owner):
            return jsonify({"error": "not found", "code": "NOT_FOUND"}), 404
        import threading
        from agents.daily_mailing import dispatch as _dm_dispatch

        def _run() -> None:
            try:
                res = _dm_dispatch.send_test(item_id, requester_email=owner,
                                             deps=_dm_dispatch.DispatchDeps.production())
                logger.info("daily mailing 테스트 발송 (구독 %s): %s", item_id, res)
            except Exception:
                logger.exception("daily mailing 테스트 발송 실패 (구독 %s)", item_id)

        threading.Thread(target=_run, name=f"dm-test-{item_id}", daemon=True).start()
        return jsonify({"ok": True, "message": f"테스트 메일을 생성하고 있습니다. 1~3분 뒤 {owner} 메일함을 확인하세요 (제목 [TEST])."}), 202
    except Exception as e:
        logger.error("mail_sub_test_request 실패: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500
```

9. **Admin run endpoints** — add right after `daily_mailing_admin_kanban`:

```python
def _dm_run_brief(run: dict | None) -> dict:
    run = run or {}
    return {k: run.get(k) for k in ("run_id", "delivery_status", "approval_status", "sent_at",
                                     "delivery_error", "approved_by", "approved_at")}


@app.post("/api/admin/daily-mailing/runs/<run_id>/approve")
@require_auth(role="admin")
def daily_mailing_run_approve(run_id: str):
    from agents.daily_mailing import dispatch as _dm_dispatch
    try:
        run = _dm_dispatch.approve_run(run_id, approver=request.user["sub"],  # type: ignore[attr-defined]
                                       deps=_dm_dispatch.DispatchDeps.production())
        return jsonify({"ok": True, "run": _dm_run_brief(run)})
    except LookupError:
        return jsonify({"error": "not found", "code": "NOT_FOUND"}), 404
    except _dm_dispatch.ApprovalError as e:
        return jsonify({"error": str(e), "code": "INVALID_STATE"}), 409
    except Exception as e:
        logger.error("daily_mailing approve 실패: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.post("/api/admin/daily-mailing/runs/<run_id>/reject")
@require_auth(role="admin")
def daily_mailing_run_reject(run_id: str):
    from agents.daily_mailing import dispatch as _dm_dispatch
    try:
        run = _dm_dispatch.reject_run(run_id, approver=request.user["sub"],  # type: ignore[attr-defined]
                                      deps=_dm_dispatch.DispatchDeps.production())
        return jsonify({"ok": True, "run": _dm_run_brief(run)})
    except LookupError:
        return jsonify({"error": "not found", "code": "NOT_FOUND"}), 404
    except _dm_dispatch.ApprovalError as e:
        return jsonify({"error": str(e), "code": "INVALID_STATE"}), 409
    except Exception as e:
        logger.error("daily_mailing reject 실패: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.get("/api/admin/daily-mailing/runs/<run_id>/preview")
@require_auth(role="admin")
def daily_mailing_run_preview(run_id: str):
    from agents.daily_mailing import dispatch as _dm_dispatch
    from agents.daily_mailing.run_store import get_run
    run = get_run(run_id, db_path=_dm_dispatch.DispatchDeps.production().db_path)
    if run is None or not run.get("email_html"):
        return jsonify({"error": "not found", "code": "NOT_FOUND"}), 404
    return jsonify({"subject": run.get("email_subject") or "", "html": run["email_html"],
                    "delivery_status": run.get("delivery_status")})


@app.get("/api/admin/daily-mailing/health")
@require_auth(role="admin")
def daily_mailing_health():
    from agents.daily_mailing import dispatch as _dm_dispatch
    try:
        return jsonify(_dm_dispatch.health_check(_dm_dispatch.DispatchDeps.production()))
    except Exception as e:
        logger.error("daily_mailing health 실패: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500
```

- [ ] **Step 4:** Run `tests/test_dm_api.py tests/test_mail_subscription_sources.py` → PASS.
- [ ] **Step 5: Commit** `feat(daily-mailing): API — 발송모드·스콥 검증·run 승인/반려/미리보기/헬스·테스트 즉시 발송`

---

### Task B11: Scheduler — mailing jobs, retire the Hermes bus

**Files:** Modify `scheduler.py` · Test `tests/test_dm_scheduler_jobs.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_dm_scheduler_jobs.py
from __future__ import annotations

import sys

import scheduler as sch


def test_registers_mailing_jobs_and_retires_bus(monkeypatch):
    captured = {}

    class CapturingScheduler(sch.BlockingScheduler):
        def start(self, *args, **kwargs):
            captured["ids"] = {job.id for job in self.get_jobs()}
            raise KeyboardInterrupt

    monkeypatch.setattr(sch, "BlockingScheduler", CapturingScheduler)
    monkeypatch.setattr(sys, "argv", ["scheduler.py"])
    sch.main()
    ids = captured["ids"]
    assert {"daily_mailing_dispatch", "daily_mailing_top_news", "daily_mailing_health"} <= ids
    assert "daily_mailing_scope_export" not in ids
    assert "daily_mailing_runs_sync" not in ids
```

- [ ] **Step 2:** Run → FAIL (new job ids missing).
- [ ] **Step 3: Implement** in `scheduler.py`:

1. Add module-level job functions next to `daily_mailing_scope_export_job`:

```python
def daily_mailing_dispatch_job():
    """10분 주기 — 발송 시각이 된 구독 브리핑 생성 → 승인 대기/자동 발송 + 실패 재시도 (설계 §6)."""
    try:
        from agents.daily_mailing.dispatch import DispatchDeps, dispatch_due_subscriptions
        res = dispatch_due_subscriptions(DispatchDeps.production())
        if res.get("generated") or res.get("errors") or res.get("expired") or res.get("retried"):
            logger.info("daily mailing dispatch: %s", res)
    except Exception as e:
        logger.error("daily mailing dispatch 실패: %s", e, exc_info=True)


def daily_mailing_top_news_job():
    """매일 06:40 — '오늘의 주요 MA 뉴스' 산출·캐시 (06:30 뉴스 수집 직후, 구독 공통)."""
    try:
        from datetime import datetime
        from agents.daily_mailing.dispatch import DispatchDeps
        from agents.daily_mailing.schedule import KST
        items = DispatchDeps.production().load_top_ma_news(datetime.now(KST).date().isoformat())
        logger.info("daily mailing 주요 MA 뉴스: %d건", len(items))
    except Exception as e:
        logger.error("daily mailing 주요 MA 뉴스 실패: %s", e, exc_info=True)


def daily_mailing_health_job():
    """매일 09:30 — 오늘 예정 구독의 미생성·보류·발송실패·Gmail 토큰 상태 점검."""
    try:
        from agents.daily_mailing.dispatch import DispatchDeps, health_check
        res = health_check(DispatchDeps.production())
        if res["ok"]:
            logger.info("daily mailing health OK (%s)", res["run_date"])
        else:
            logger.error("daily mailing health 문제 %d건: %s", len(res["problems"]), res["problems"])
    except Exception as e:
        logger.error("daily mailing health 실패: %s", e, exc_info=True)
```

2. In `main()`, **remove** the registration of the inner `_daily_mailing_runs_sync_job` (`id="daily_mailing_runs_sync"`) together with its inner function, and the registration with `id="daily_mailing_scope_export"`. In their place add:

```python
    # ── Daily Mailing (2026-09-18 프로덕션 생성·발송) ──
    # 헤르메스 git 버스(runs_sync 07:30 · scope_export 15분)는 retire — 함수/CLI 는 롤백용 보존, 잡 미등록.
    scheduler.add_job(
        daily_mailing_dispatch_job,
        trigger=IntervalTrigger(minutes=10, timezone="Asia/Seoul"),
        id="daily_mailing_dispatch",
        name="Daily Mailing 10분 주기 발송 판정 (생성→승인대기/자동발송·재시도)",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        daily_mailing_top_news_job,
        trigger=CronTrigger(hour=6, minute=40, timezone="Asia/Seoul"),
        id="daily_mailing_top_news",
        name="Daily Mailing 주요 MA 뉴스 매일 06:40",
        replace_existing=True,
    )
    scheduler.add_job(
        daily_mailing_health_job,
        trigger=CronTrigger(hour=9, minute=30, timezone="Asia/Seoul"),
        id="daily_mailing_health",
        name="Daily Mailing 헬스체크 매일 09:30",
        replace_existing=True,
    )
```

3. In the `if args.amjilsim_daily_crawl_now:` boot branch, delete the two `try` blocks that call `_sync_daily_mailing_runs()` and `daily_mailing_scope_export_job()` (leave `reimb_data_sync_job()` and `_sync_policy_analysis()`), adding the comment `# Daily Mailing 헤르메스 버스 retire (2026-09-18) — 부팅 sync/export 제거`.

4. CLI flags — next to the `"--scope-export-now"` argument add:

```python
    parser.add_argument("--mailing-dispatch-now", action="store_true",
                        help="Daily Mailing 발송 판정 1회 즉시 실행")
    parser.add_argument("--mailing-health-now", action="store_true",
                        help="Daily Mailing 헬스체크 1회 즉시 실행")
```

and next to the `if args.scope_export_now:` handler add:

```python
    if args.mailing_dispatch_now:
        logger.info("Daily Mailing 발송 판정 즉시 실행")
        daily_mailing_dispatch_job()
        return

    if args.mailing_health_now:
        logger.info("Daily Mailing 헬스체크 즉시 실행")
        daily_mailing_health_job()
        return
```

5. The `_sync_daily_mailing_runs` import at the top stays (used by nothing now is fine — if the linter/QualityGuard flags an unused import, keep it with `# noqa: F401  (롤백용)`).

- [ ] **Step 4:** Run `tests/test_dm_scheduler_jobs.py tests/test_scheduler_local_only_jobs.py tests/test_domestic_catchup_early_exit.py` → PASS; `…/python -c "import scheduler"` → OK.
- [ ] **Step 5: Commit** `feat(daily-mailing): 스케줄러 — 10분 발송 판정·06:40 주요 뉴스·09:30 헬스, 헤르메스 버스 잡 retire`

---

### Task B12: Frontend API clients

**Files:** Modify `frontend/src/api/dailyMailingKanban.ts`, `frontend/src/api/mailSubscriptions.ts`

- [ ] **Step 1:** In `dailyMailingKanban.ts`, add to `interface KanbanRun` (before its closing brace):

```ts
  run_date?: string | null;
  is_test?: number | boolean | null;
  email_subject?: string | null;
  has_email_html?: boolean;
  send_attempts?: number | null;
  delivery_error?: string | null;
  approved_by?: string | null;
  approved_at?: string | null;
```

and append at the end of the file:

```ts
export interface RunActionResult {
  ok: boolean;
  run: {
    run_id: string;
    delivery_status: string;
    approval_status: string;
    sent_at: string | null;
    delivery_error: string | null;
  };
}

export async function approveDailyMailingRun(runId: string): Promise<RunActionResult> {
  return api.post<RunActionResult>(`/api/admin/daily-mailing/runs/${encodeURIComponent(runId)}/approve`, {});
}

export async function rejectDailyMailingRun(runId: string): Promise<RunActionResult> {
  return api.post<RunActionResult>(`/api/admin/daily-mailing/runs/${encodeURIComponent(runId)}/reject`, {});
}

export interface RunPreview {
  subject: string;
  html: string;
  delivery_status: string | null;
}

export async function fetchDailyMailingRunPreview(runId: string): Promise<RunPreview> {
  return api.get<RunPreview>(`/api/admin/daily-mailing/runs/${encodeURIComponent(runId)}/preview`);
}

export interface HealthProblem {
  subscription_id: number | null;
  name: string;
  problem: string;
  detail?: string | null;
}

export interface DailyMailingHealth {
  run_date: string;
  checked_at: string;
  ok: boolean;
  problems: HealthProblem[];
  gmail_ready: boolean;
}

export async function fetchDailyMailingHealth(): Promise<DailyMailingHealth> {
  return api.get<DailyMailingHealth>('/api/admin/daily-mailing/health');
}
```

- [ ] **Step 2:** In `mailSubscriptions.ts`:
  - add to `MailSubscription`: `deliveryMode?: 'approval' | 'auto';` and `includeTopMaNews?: boolean;`
  - add the same two optional fields to `MailSubscriptionInput`;
  - add `export interface MailSubSaveResult { item: MailSubscription; warnings: string[]; }`;
  - replace `createMailSubscription` / `updateMailSubscription` with:

```ts
export async function createMailSubscription(input: MailSubscriptionInput): Promise<MailSubSaveResult> {
  const r = await api.post<{ item: MailSubscription; warnings?: string[] }>('/api/mail-subscriptions', input);
  return { item: r.item, warnings: r.warnings ?? [] };
}

export async function updateMailSubscription(
  id: number,
  patch: Partial<MailSubscriptionInput>,
): Promise<MailSubSaveResult> {
  const r = await api.patch<{ item: MailSubscription; warnings?: string[] }>(`/api/mail-subscriptions/${id}`, patch);
  return { item: r.item, warnings: r.warnings ?? [] };
}
```

- [ ] **Step 3:** Commit `feat(daily-mailing): 프론트 API — run 승인/반려/미리보기/헬스, 구독 발송모드·경고`

---

### Task B13: Admin kanban UI — health banner, preview, approve/reject

**Files:** Modify `frontend/src/pages/admin/daily-mailing-kanban/page.tsx`

- [ ] **Step 1:** Imports — replace the `@/api/dailyMailingKanban` import with:

```tsx
import {
  fetchDailyMailingKanban, parseJsonArray, asStringArray, normalizeReviewerFindings,
  approveDailyMailingRun, rejectDailyMailingRun, fetchDailyMailingRunPreview, fetchDailyMailingHealth,
  type DailyMailingKanban, type KanbanArticle, type KanbanRun,
  type Persona, type ReviewerRole, type DraftItem, type QualityReport, type RunCounts,
  type DailyMailingHealth, type RunPreview,
} from '@/api/dailyMailingKanban';
```

- [ ] **Step 2:** In `DELIVERY_TONE`, add before the `draft` line:

```tsx
  if (v.includes('pending') || v.includes('held') || v.includes('expired')) return 'bg-[#F59E0B]/10 text-[#F59E0B]';
```

- [ ] **Step 3:** Replace the whole `function RunRow(...) { ... }` with:

```tsx
function RunRow({ run, busy, onPreview, onApprove, onReject }: {
  run: KanbanRun;
  busy: boolean;
  onPreview: (runId: string) => void;
  onApprove: (runId: string) => void;
  onReject: (runId: string) => void;
}) {
  const keywords = parseJsonArray(run.keywords_json);
  const recipients = parseJsonArray(run.recipients_json);
  const qrStatus = run.quality_report?.status ?? null;
  const counts = run.counts ?? {};
  const pending = run.delivery_status === 'pending_approval';
  return (
    <tr className="border-b border-[#1E2530]/50 last:border-b-0 hover:bg-[#1E2530]/30 align-top">
      <td className="py-2.5 pr-3 whitespace-nowrap">
        <span className="text-white text-xs font-mono">{run.run_id}</span>
        {run.is_test ? <span className="ml-1 text-[10px] px-1.5 py-0.5 rounded bg-[#7C3AED]/10 text-[#A78BFA]">TEST</span> : null}
        {run.email_subject && <p className="text-[#8B9BB4] text-[10px] mt-0.5 max-w-[220px] truncate">{run.email_subject}</p>}
      </td>
      <td className="py-2.5 pr-3 text-[#8B9BB4] text-xs whitespace-nowrap">
        {run.generated_at ? new Date(run.generated_at).toLocaleString('ko-KR') : <Dash />}
      </td>
      <td className="py-2.5 pr-3 whitespace-nowrap">
        <span className={`text-[10px] px-2 py-0.5 rounded ${badgeTone(run.status)}`}>{run.status}</span>
      </td>
      <td className="py-2.5 pr-3 whitespace-nowrap">
        {qrStatus ? <span className={`text-[10px] px-2 py-0.5 rounded ${badgeTone(qrStatus)}`}>{qrStatus}</span> : <Dash />}
      </td>
      <td className="py-2.5 pr-3 whitespace-nowrap">
        <span className={`text-[10px] px-2 py-0.5 rounded ${DELIVERY_TONE(run.delivery_status)}`}>{run.delivery_status}</span>
        {run.delivery_error && (
          <p className="text-red-400 text-[10px] mt-0.5 max-w-[180px] truncate" title={run.delivery_error}>{run.delivery_error}</p>
        )}
      </td>
      <td className="py-2.5 pr-3 whitespace-nowrap">
        <span className={`text-[10px] px-2 py-0.5 rounded ${badgeTone(run.approval_status)}`}>{run.approval_status}</span>
        {run.approved_by && <p className="text-[#4A5568] text-[10px] mt-0.5">{run.approved_by}</p>}
      </td>
      <td className="py-2.5 pr-3 text-[#8B9BB4] text-xs whitespace-nowrap">
        발굴 {run.discovered_count} · 최신 {run.recent_count} · 선택 {run.selected_count}
      </td>
      <td className="py-2.5 pr-3 text-[#8B9BB4] text-xs whitespace-nowrap">
        {counts.needs_review ?? <Dash />} / {counts.ready_for_writer ?? <Dash />}
      </td>
      <td className="py-2.5 pr-3 text-[#8B9BB4] text-xs max-w-[220px]">
        <span className="block truncate">{keywords.length > 0 ? keywords.join(', ') : <Dash />}</span>
      </td>
      <td className="py-2.5 pr-3 text-[#8B9BB4] text-xs max-w-[180px]">
        <span className="block truncate">{run.owner_email ?? <Dash />}{recipients.length > 0 ? ` (+${recipients.length})` : ''}</span>
      </td>
      <td className="py-2.5 text-right whitespace-nowrap space-x-1">
        {run.has_email_html && (
          <button onClick={() => onPreview(run.run_id)} disabled={busy}
            className="text-[10px] px-2 py-1 rounded bg-[#1E2530] text-[#8B9BB4] hover:text-white cursor-pointer disabled:opacity-50">
            미리보기
          </button>
        )}
        {pending && (
          <>
            <button onClick={() => onApprove(run.run_id)} disabled={busy}
              className="text-[10px] px-2 py-1 rounded bg-[#00E5CC]/10 text-[#00E5CC] hover:bg-[#00E5CC]/20 cursor-pointer disabled:opacity-50">
              승인·발송
            </button>
            <button onClick={() => onReject(run.run_id)} disabled={busy}
              className="text-[10px] px-2 py-1 rounded bg-red-500/10 text-red-400 hover:bg-red-500/20 cursor-pointer disabled:opacity-50">
              반려
            </button>
          </>
        )}
        {!run.has_email_html && !pending && <Dash />}
      </td>
    </tr>
  );
}

function HealthBanner({ health }: { health: DailyMailingHealth | null }) {
  if (!health || health.ok) return null;
  return (
    <div className="rounded-2xl border border-red-500/30 bg-red-500/10 p-4">
      <p className="text-red-400 text-sm font-bold mb-1">
        <i className="ri-alarm-warning-line mr-1"></i>Daily Mailing 점검 필요 ({health.run_date})
      </p>
      <ul className="text-red-300 text-xs space-y-0.5">
        {health.problems.map((p, i) => (
          <li key={`${p.subscription_id ?? 'x'}_${i}`}>
            {p.subscription_id != null ? `구독 ${p.subscription_id} (${p.name})` : 'Gmail'} — {p.problem}{p.detail ? `: ${p.detail}` : ''}
          </li>
        ))}
      </ul>
    </div>
  );
}

function PreviewModal({ preview, onClose }: { preview: RunPreview | null; onClose: () => void }) {
  if (!preview) return null;
  return (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-6" onClick={onClose}>
      <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] w-full max-w-3xl p-4" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-3">
          <p className="text-white text-sm font-bold truncate">{preview.subject}</p>
          <button onClick={onClose} className="text-[#8B9BB4] hover:text-white cursor-pointer"><i className="ri-close-line text-lg"></i></button>
        </div>
        <iframe title="메일 미리보기" sandbox="" srcDoc={preview.html} className="w-full h-[70vh] bg-white rounded-lg" />
      </div>
    </div>
  );
}
```

- [ ] **Step 4:** In `AdminDailyMailingKanbanPage`:
  - after `const [error, setError] = useState<string | null>(null);` add:

```tsx
  const [health, setHealth] = useState<DailyMailingHealth | null>(null);
  const [preview, setPreview] = useState<RunPreview | null>(null);
  const [busyRunId, setBusyRunId] = useState<string | null>(null);
```

  - inside `reload`, after `setData(r);` add:

```tsx
      fetchDailyMailingHealth().then(setHealth).catch(() => setHealth(null));
```

  - after the `reload` function add:

```tsx
  const runAction = async (runId: string, action: 'approve' | 'reject' | 'preview') => {
    if (action === 'approve' && !window.confirm('이 브리핑을 수신자에게 발송할까요?')) return;
    if (action === 'reject' && !window.confirm('이 브리핑을 반려할까요? (오늘은 발송되지 않습니다)')) return;
    setBusyRunId(runId);
    try {
      if (action === 'preview') {
        setPreview(await fetchDailyMailingRunPreview(runId));
        return;
      }
      const r = action === 'approve' ? await approveDailyMailingRun(runId) : await rejectDailyMailingRun(runId);
      if (r.run.delivery_error) alert(`발송 결과: ${r.run.delivery_status} — ${r.run.delivery_error}`);
      await reload();
    } catch (e) {
      alert(e instanceof Error ? e.message : '처리 실패');
    } finally {
      setBusyRunId(null);
    }
  };
```

  - replace the `boardPurpose` fallback string with `'구독별 브리핑 생성·승인·발송 현황 (Admin 전용). 승인 모드 구독은 여기서 승인해야 발송됩니다.'`;
  - right after `<div className="px-8 py-6 space-y-5">` add `<HealthBanner health={health} />`;
  - in the runs table header, change `<th className="text-right py-2">산출물</th>` to `<th className="text-right py-2">액션</th>`;
  - change `{data.runs.map(r => <RunRow key={r.run_id} run={r} />)}` to:

```tsx
                      {data.runs.map(r => (
                        <RunRow key={r.run_id} run={r} busy={busyRunId === r.run_id}
                          onPreview={id => runAction(id, 'preview')}
                          onApprove={id => runAction(id, 'approve')}
                          onReject={id => runAction(id, 'reject')} />
                      ))}
```

  - change the empty-runs text to `아직 실행 이력이 없습니다. 구독 발송 시각이 되면 서버가 생성한 run 이 표시됩니다.`;
  - just before the final closing `</div>` of the page root (after the `px-8 py-6` container's closing `</div>`), add `<PreviewModal preview={preview} onClose={() => setPreview(null)} />`.

- [ ] **Step 5:** Commit `feat(daily-mailing): 관리자 칸반 — 헬스 배너·메일 미리보기·승인/반려`

---

### Task B14: Subscription page — delivery mode, top news, warnings, copy

**Files:** Modify `frontend/src/pages/daily-mailing/page.tsx`

- [ ] **Step 1:** State — after `const [weekDay, setWeekDay] = useState('Monday');` add:

```tsx
  const [deliveryMode, setDeliveryMode] = useState<'approval' | 'auto'>('approval');
  const [includeTopMaNews, setIncludeTopMaNews] = useState(true);
```

- [ ] **Step 2:** `resetForm` — replace `setSchedule('Daily'); setScheduleTime('08:00'); setWeekDay('Monday');` with
`setSchedule('Daily'); setScheduleTime('08:00'); setWeekDay('Monday'); setDeliveryMode('approval'); setIncludeTopMaNews(true);`
- [ ] **Step 3:** `startEdit` — after `setWeekDay(setting.weekDay ?? 'Monday');` add
`setDeliveryMode(setting.deliveryMode ?? 'approval'); setIncludeTopMaNews(setting.includeTopMaNews ?? true);`
- [ ] **Step 4:** `handleSubmit` — add `deliveryMode, includeTopMaNews,` after `diseaseAreas: selectedDiseaseAreas,` in `payload`, and replace the block from `if (isEdit) {` through the `setTimeout(() => { setSubmitStatus('idle'); ... }, 3000);` line with:

```tsx
      const saved = isEdit
        ? await updateMailSubscription(editingId, payload)
        : await createMailSubscription({ ...payload, active: true });
      const warningText = saved.warnings.length > 0 ? ` ⚠️ ${saved.warnings.join(' / ')}` : '';
      setSubmitStatus('success');
      setSubmitMessage((isEdit
        ? '설정이 수정되었습니다. 다음 발송부터 수정된 스콥이 적용됩니다.'
        : '모니터링 스콥이 저장되었습니다. 발송 시각이 되면 서버가 이 스콥으로 브리핑을 생성합니다.') + warningText);
      setEditingId(null);
      resetForm();
      await reload();
      setActiveTab('saved');
      setTimeout(() => { setSubmitStatus('idle'); setSubmitMessage(null); }, warningText ? 10000 : 3000);
```

- [ ] **Step 5:** Schedule card — replace the info `<p>` text expression with:

```tsx
{(schedule === 'Daily' ? `매일 ${scheduleTime}` : `매주 ${weekDay} ${scheduleTime}`)
  + (deliveryMode === 'approval'
    ? '에 서버가 브리핑을 생성합니다. 관리자가 승인하면 발송됩니다.'
    : '에 서버가 브리핑을 생성해 품질 게이트 통과 시 바로 발송합니다.')}
```

and right after that info `<div>` (still inside the Schedule card) add:

```tsx
              <div className="mt-4 flex flex-wrap items-center gap-6">
                <div className="flex items-center gap-2">
                  <span className={`${textSub} text-xs`}>발송 방식</span>
                  <div className={`flex items-center gap-1 rounded-xl p-1 ${tabBg}`}>
                    {([['approval', '승인 후 발송'], ['auto', '자동 발송']] as const).map(([mode, label]) => (
                      <button type="button" key={mode} onClick={() => setDeliveryMode(mode)}
                        className={`px-4 py-1.5 rounded-lg text-xs font-semibold cursor-pointer whitespace-nowrap transition-all ${deliveryMode === mode ? tabActive : tabInactive}`}>{label}</button>
                    ))}
                  </div>
                </div>
                <label className={`flex items-center gap-2 text-xs cursor-pointer ${textSub}`}>
                  <input type="checkbox" checked={includeTopMaNews} onChange={e => setIncludeTopMaNews(e.target.checked)} />
                  하단에 '오늘의 주요 MA 뉴스' 포함
                </label>
              </div>
```

- [ ] **Step 6:** Copy — in `handleRequestTestMail` change the confirm text to
`'테스트 메일을 지금 생성해 내 로그인 이메일로 보낼까요? (1~3분 소요, 제목 [TEST])'`; in `handleTestSend` change the `'none'` alert fallback to `'아직 생성된 브리핑이 없습니다.'`. Replace any remaining user-facing "헤르메스" wording in this file with server-generation wording (`grep -n "헤르메스" frontend/src/pages/daily-mailing/page.tsx`).
- [ ] **Step 7:** Frontend checks (worktree has no `node_modules` — link the main one):

```bash
ln -s /Users/kimjeong-ae/MA_AI_Dossier/frontend/node_modules frontend/node_modules
cd frontend && npm run type-check && npm run build && cd ..
rm frontend/node_modules
```
Expected: type-check passes with no errors; build succeeds.
- [ ] **Step 8:** Commit `feat(daily-mailing): 구독 화면 — 발송 방식·주요 MA 뉴스 토글·스콥 경고·문구 정리`

---

### Task B15: Retire the Hermes workflow and document

**Files:** Move `.github/workflows/hermes-daily-mailing.yml` · Modify `docs/daily_mailing/HERMES_DAILY_MAILING_HANDOFF.md`

- [ ] **Step 1:**

```bash
mkdir -p docs/daily_mailing/retired
git mv .github/workflows/hermes-daily-mailing.yml docs/daily_mailing/retired/hermes-daily-mailing.yml
```

- [ ] **Step 2:** Insert as the second line of `docs/daily_mailing/HERMES_DAILY_MAILING_HANDOFF.md`:

```markdown
> ⚠️ **RETIRED 2026-09-18** — 생성·발송은 프로덕션 서버(`agents/daily_mailing/dispatch.py` + `engine.py`)로 이전. 설계: `docs/superpowers/specs/2026-09-18-daily-mailing-service-design.md`. 이 문서는 이력용.
```

- [ ] **Step 3:** Commit `chore(daily-mailing): 헤르메스 발송 워크플로·핸드오프 retire`

---

### Task B16: Regression

- [ ] **Step 1:**

```bash
/Users/kimjeong-ae/MA_AI_Dossier/.venv/bin/python -m pytest tests/test_dm_scope_from_dict.py tests/test_dm_subscription_delivery.py tests/test_dm_storage_delivery.py tests/test_dm_run_store.py tests/test_dm_schedule.py tests/test_dm_top_news.py tests/test_dm_gmail_token.py tests/test_dm_dispatch.py tests/test_dm_scope_validation.py tests/test_dm_api.py tests/test_dm_scheduler_jobs.py tests/test_daily_mailing_final_gates.py tests/test_daily_mailing_bridge.py tests/test_daily_mailing_kanban_migration.py tests/test_daily_mailing_sync.py tests/test_daily_mailing_scope_export.py tests/test_dashboard_scope.py tests/test_mail_subscription_sources.py tests/test_scheduler_local_only_jobs.py tests/test_domestic_catchup_early_exit.py -q
```
Expected: all pass. Fix regressions only in B-owned files; report anything else.

- [ ] **Step 2:** `…/python -c "import scheduler, api.server"` → no exception.

---

## Self-review checklist (done by plan author)

- Spec §4 jobs (dispatch 10분, 06:40, 09:30) → B11; bus retirement → B11 + B15. §6 판정/멱등/상태/승인 시점/만료/생성 오류/API/UI/Gmail → B3–B8, B10, B12–B14, B7. §7 → B9 + B10. §8 → B8 (`send_run` retry, `health_check`) + B11. §9 출시 순서 → token setup script B7.
- Names consistent across tasks: `DispatchDeps(db_path, generate, load_top_ma_news, send, now, gmail_ready)`, `send_run(run_id, *, deps, from_statuses, recipients_override)`, `approve_run/reject_run(run_id, *, approver, deps)`, `send_test(subscription_id, *, requester_email, deps)`, `health_check(deps)`, `update_run(... UPDATABLE_FIELDS)`.
