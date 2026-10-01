"""반복 조건 신호는 '같은 행을 다시 연다' (P1.5 검증 결함 B).

재현됐던 결함: 디스패처는 런북이 ok 면 과제를 closed 로 닫는다. 분리 런북 4개는 '접수' 만으로 ok=True 고 동기 런북도 데이터를
못 바꿔도 성공할 수 있다. 신선도 키 "{domain}:{YYYY-MM}" 의 중복 억제는 DEDUP_STATUSES(열림+blocked)라 closed 를 포함하지
않으므로 다음 날 07:45 이 같은 키로 **새 행**(attempts=0)을 만들고, 설계 §5 의 재시도 한도(3회 → blocked)는 영원히 걸리지
않는다. rc:* 도 같은 루프다. 그래서 "정상 ≤3건/일" 이 2일차부터 깨진다.

해법: store.reopen_task(조건부 UPDATE 1회) + signals.record_recurring — (source, source_ref) 로 과제를 찾아
없으면 생성 / 열려 있으면(OPEN+blocked) recurred 이벤트 ≤1/일 / closed·rejected 면 **같은 행**을 triaged 로 다시 열고(attempts
보존) attempts 가 한도면 blocked 로 두어 사람 확인을 남긴다. ALLOWED_TRANSITIONS 는 그대로(closed/rejected 종단) — update_task
(API·사람·디스패처 경로)는 여전히 되돌리지 못하고, 신호 재발만 reopen_task 를 쓴다.
"""
from __future__ import annotations

import io
import sys
import urllib.error
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.agent_tasks import freshness as fr
from agents.agent_tasks import signals, store
from agents.agent_tasks.store import (
    ALLOWED_TRANSITIONS, MAX_ATTEMPTS, AgentTaskError, claim_task, create_task, get_task, list_events,
    reopen_task, triage_task, update_task,
)

TODAY = date(2026, 9, 30)
STALE = fr.Freshness("news_archive", True, "2026-09-20", "2026-09-27", "competitor_news MAX(fetched_at)=2026-09-20",
                     "news_daily_fetch")
FRESH = fr.Freshness("news_archive", False, "2026-09-30", "2026-09-27", "신선", "news_daily_fetch")


def _events(tid, db):
    return [e["event"] for e in list_events(tid, db_path=db)]


def _next_day(db):
    """테스트는 한 날에 돈다 — 기존 이벤트를 하루 전으로 돌려 '다음 날 재발' 을 흉내낸다(recurred 는 하루 1건 상한)."""
    from datetime import datetime, timedelta
    conn = store.connect(db)
    for eid, ts in conn.execute("SELECT id, created_at FROM agent_task_event").fetchall():
        conn.execute("UPDATE agent_task_event SET created_at=? WHERE id=?",
                     ((datetime.fromisoformat(ts) - timedelta(days=1)).isoformat(), eid))
    conn.commit()
    conn.close()


def _rows(db, source="domain_freshness"):
    return store.connect(db).execute("SELECT COUNT(*) FROM agent_task WHERE source=?", (source,)).fetchone()[0]


def _dispatch_ok(tid, db, actor="vps-1"):
    """디스패처가 하는 일을 원장 수준에서: 자동 분류 → 선점 → 런북 ok → closed. claim 결과를 돌려준다."""
    triage_task(tid, actor=actor, db_path=db)
    outcome = claim_task(tid, actor=actor, db_path=db)
    if outcome == "claimed":
        update_task(tid, actor=actor, db_path=db, status="closed", resolution="런북 ok (접수)")
    return outcome


# ── store.reopen_task — 상태 기계 밖의 유일한 역방향 전이 ───────────────────────────

def test_state_machine_keeps_closed_and_rejected_terminal_for_update_task(tmp_path):
    """ALLOWED_TRANSITIONS 는 손대지 않는다: API·사람·디스패처 경로(update_task)는 closed/rejected 를 되돌릴 수 없다."""
    assert ALLOWED_TRANSITIONS["closed"] == () and ALLOWED_TRANSITIONS["rejected"] == ()
    db = tmp_path / "a.db"
    tid = create_task(kind="ops_incident", source="x", source_ref="y", title="t", db_path=db)
    assert _dispatch_ok(tid, db) == "claimed"
    with pytest.raises(AgentTaskError):
        update_task(tid, actor="admin", db_path=db, status="triaged")


def test_reopen_task_closed_to_triaged_preserves_attempts_and_clears_claim(tmp_path):
    db = tmp_path / "a.db"
    tid = create_task(kind="ops_incident", source="x", source_ref="y", title="t", detail="old", db_path=db)
    _dispatch_ok(tid, db)
    assert reopen_task(tid, actor="freshness", detail="new evidence", db_path=db) == "reopened"
    t = get_task(tid, db_path=db)
    assert t["status"] == "triaged" and t["attempts"] == 1 and t["claimed_by"] is None and t["claimed_at"] is None
    assert t["resolution"] is None and t["detail"] == "new evidence"
    assert _events(tid, db)[-2:] == ["reopened", "status:triaged"]
    assert claim_task(tid, actor="vps-2", db_path=db) == "claimed" and get_task(tid, db_path=db)["attempts"] == 2


def test_reopen_task_rejected_to_triaged(tmp_path):
    db = tmp_path / "a.db"
    tid = create_task(kind="ops_incident", source="x", source_ref="y", title="t", db_path=db)
    update_task(tid, actor="admin", db_path=db, status="rejected", resolution="오탐이라 봤음")
    assert reopen_task(tid, actor="freshness", db_path=db) == "reopened"
    assert get_task(tid, db_path=db)["status"] == "triaged"


def test_reopen_task_exhausted_goes_blocked_not_triaged(tmp_path):
    """attempts 가 한도(3)에 닿은 closed 과제는 다시 열지 않고 blocked — 사람 확인."""
    db = tmp_path / "a.db"
    tid = create_task(kind="ops_incident", source="x", source_ref="y", title="t", db_path=db)
    for _ in range(MAX_ATTEMPTS):
        assert _dispatch_ok(tid, db) == "claimed"
        if get_task(tid, db_path=db)["attempts"] < MAX_ATTEMPTS:
            assert reopen_task(tid, actor="s", db_path=db) == "reopened"
    assert get_task(tid, db_path=db)["attempts"] == MAX_ATTEMPTS
    assert reopen_task(tid, actor="s", db_path=db) == "exhausted"
    t = get_task(tid, db_path=db)
    assert t["status"] == "blocked" and t["attempts"] == MAX_ATTEMPTS and "재시도 한도" in t["resolution"]
    assert _events(tid, db)[-2:] == ["exhausted", "status:blocked"]
    assert reopen_task(tid, actor="s", db_path=db) == "not_reopenable"     # blocked 는 열린 쪽 — 호출자가 recurred 로


def test_reopen_task_refuses_open_states(tmp_path):
    db = tmp_path / "a.db"
    tid = create_task(kind="ops_incident", source="x", source_ref="y", title="t", db_path=db)
    assert reopen_task(tid, actor="s", db_path=db) == "not_reopenable"
    triage_task(tid, actor="s", db_path=db)
    claim_task(tid, actor="s", db_path=db)
    assert reopen_task(tid, actor="s", db_path=db) == "not_reopenable"
    assert get_task(tid, db_path=db)["status"] == "in_progress" and reopen_task(999, actor="s", db_path=db) == "not_reopenable"


# ── signals.record_recurring — 공용 진입점 ────────────────────────────────────────

def _recur(db, detail="d", **kw):
    kw.setdefault("kind", "ops_incident")
    kw.setdefault("source", "sig")
    kw.setdefault("source_ref", "k")
    kw.setdefault("title", "t")
    kw.setdefault("priority", 2)
    kw.setdefault("actor", "s")
    return signals.record_recurring(detail=detail, db_path=db, **kw)


def test_record_recurring_outcomes(tmp_path):
    db = tmp_path / "a.db"
    tid, outcome = _recur(db)
    assert outcome == "created" and _events(tid, db) == ["created"]
    assert _recur(db) == (tid, "unchanged")                       # 같은 날 두 번째: 이벤트조차 없다
    assert _events(tid, db) == ["created"]
    _dispatch_ok(tid, db)
    assert _recur(db, detail="again") == (tid, "reopened")
    assert get_task(tid, db_path=db)["detail"] == "again" and _rows(db, "sig") == 1
    update_task(tid, actor="admin", db_path=db, status="blocked", resolution="x")
    assert _recur(db)[1] in ("recurred", "unchanged") and get_task(tid, db_path=db)["status"] == "blocked"
    assert _rows(db, "sig") == 1


def test_record_recurring_rejected_policy(tmp_path):
    """ops_incident: rejected 도 재발하면 다시 연다(조건이 되돌아왔다는 뜻). reopen_rejected=False 면 rejected 는 그대로 두고 recurred 만."""
    db = tmp_path / "a.db"
    tid, _ = _recur(db)
    update_task(tid, actor="admin", db_path=db, status="rejected", resolution="x")
    assert _recur(db)[1] == "reopened" and get_task(tid, db_path=db)["status"] == "triaged"
    tid2, _ = _recur(db, source_ref="k2", kind="improvement")
    update_task(tid2, actor="admin", db_path=db, status="rejected", resolution="안 한다")
    _next_day(db)
    assert _recur(db, source_ref="k2", kind="improvement", reopen_rejected=False) == (tid2, "recurred")
    assert _recur(db, source_ref="k2", kind="improvement", reopen_rejected=False) == (tid2, "unchanged")   # 하루 1건
    assert get_task(tid2, db_path=db)["status"] == "rejected" and _rows(db, "sig") == 2


# ── 3일 시뮬레이션: 원장 수준(신선도 판정 + store) ────────────────────────────────────

def test_three_day_simulation_same_row_until_exhausted(tmp_path):
    """1일차 생성→claim→closed, 2일차 재판정→같은 행 reopen(attempts 1)→claim(attempts 2)→closed, 3일차 reopen→claim(attempts 3)
    →closed, 4일차 재판정→한도라 다시 열지 않고 blocked(같은 행), 5일차 이후 새 행 0건·blocked 유지. 설계 §5: 최초 1 + 재시도 2 = 3회.
    """
    db = tmp_path / "ledger.db"
    (tid,) = fr.record_freshness_problems([STALE], today=TODAY, db_path=db)                 # 1일차
    assert _dispatch_ok(tid, db) == "claimed" and get_task(tid, db_path=db)["attempts"] == 1
    for day, expected_attempts in ((2, 2), (3, 3)):
        assert fr.record_freshness_problems([STALE], today=TODAY, db_path=db) == [tid], day     # 같은 행
        t = get_task(tid, db_path=db)
        assert t["status"] == "triaged" and t["attempts"] == expected_attempts - 1 and "reopened" in _events(tid, db)
        assert _dispatch_ok(tid, db) == "claimed" and get_task(tid, db_path=db)["attempts"] == expected_attempts
        assert get_task(tid, db_path=db)["status"] == "closed" and _rows(db) == 1
    assert fr.record_freshness_problems([STALE], today=TODAY, db_path=db) == [tid]                 # 4일차
    t = get_task(tid, db_path=db)
    assert t["status"] == "blocked" and t["attempts"] == MAX_ATTEMPTS and _rows(db) == 1
    assert _dispatch_ok(tid, db) == "not_claimable"                                                # 디스패처는 더 못 집는다
    for _ in range(3):                                                                             # 5일차~: 0건 추가
        assert fr.record_freshness_problems([STALE], today=TODAY, db_path=db) == [tid]
    t = get_task(tid, db_path=db)
    assert t["status"] == "blocked" and _rows(db) == 1
    assert _events(tid, db).count("reopened") == 2 and _events(tid, db).count("exhausted") == 1
    # 같은 달 안의 반복이라 recurred 는 하루 1건 상한 — 테스트는 한 날이라 0~1건
    assert _events(tid, db).count("recurred") <= 1


def test_recovered_domain_leaves_ledger_untouched(tmp_path):
    """도메인이 실제로 복구되면 다음 판정에서 아무 일도 일어나지 않는다 — closed 그대로, 이벤트 0, 행 0."""
    db = tmp_path / "ledger.db"
    (tid,) = fr.record_freshness_problems([STALE], today=TODAY, db_path=db)
    _dispatch_ok(tid, db)
    before = (get_task(tid, db_path=db), _events(tid, db))
    assert fr.record_freshness_problems([FRESH], today=TODAY, db_path=db) == []
    assert (get_task(tid, db_path=db), _events(tid, db)) == before and _rows(db) == 1


def test_new_month_still_starts_a_fresh_row(tmp_path):
    """월 키는 유지: 새 달엔 새 행(attempts 0) — 월 단위 재시도 예산이자 도메인당 월 1행 상한(계획 T3)."""
    db = tmp_path / "ledger.db"
    (tid,) = fr.record_freshness_problems([STALE], today=TODAY, db_path=db)
    (tid2,) = fr.record_freshness_problems([STALE], today=date(2026, 10, 1), db_path=db)
    assert tid2 != tid and _rows(db) == 2


# ── RC FAIL · QG 과제도 같은 행을 다시 연다 ─────────────────────────────────────────

def test_compliance_fail_reopens_same_row_after_close(tmp_path):
    from agents.rule_compliance.checks import CheckResult
    db = tmp_path / "a.db"
    results = [CheckResult("project_xnational_reimbursement", "fail", "last_fetched 오래됨", {"total": 44})]
    (tid,) = signals.record_compliance_results(results, db_path=db)
    _dispatch_ok(tid, db)
    assert signals.record_compliance_results(results, db_path=db) == [tid]
    assert get_task(tid, db_path=db)["status"] == "triaged" and _rows(db, "rule_compliance") == 1


def test_qg_incident_and_group_reopen_same_row(tmp_path):
    db = tmp_path / "a.db"
    review = {"code_issues": ["BaseScraper 미상속: a.py"], "rule_drifts": [], "suggestions": [],
              "mfds_regressions": [{"indication_id": "keytruda-nsclc-adj", "expected": "x", "actual": "y"}]}
    ids = signals.record_quality_review(review, db_path=db)
    (inc,), (grp,) = ids["incident"], ids["improvement"]
    _dispatch_ok(inc, db)
    update_task(grp, actor="admin", db_path=db, status="triaged")
    update_task(grp, actor="admin", db_path=db, status="in_progress")
    update_task(grp, actor="admin", db_path=db, status="closed", resolution="고침")
    again = signals.record_quality_review(review, db_path=db)
    assert again == ids and _rows(db, "quality_guard") == 2
    assert get_task(inc, db_path=db)["status"] == "triaged" and get_task(grp, db_path=db)["status"] == "triaged"


def test_qg_rejected_group_stays_rejected_until_content_changes(tmp_path):
    """improvement 그룹의 rejected 는 '이 내용은 안 한다' 는 결정 — 같은 내용이면 매일 되살리지 않고(recurred ≤1/일), 내용이 바뀌면 다시 연다."""
    db = tmp_path / "a.db"
    review = {"code_issues": [], "rule_drifts": [], "mfds_regressions": [],
              "suggestions": ["📦 `agents/a.py` (793줄) — 모듈 분리 검토"]}
    (grp,) = signals.record_quality_review(review, db_path=db)["improvement"]
    update_task(grp, actor="admin", db_path=db, status="rejected", resolution="안 한다")
    _next_day(db)
    signals.record_quality_review(review, db_path=db)
    signals.record_quality_review(review, db_path=db)                       # 같은 날 두 번 → recurred 1건
    assert get_task(grp, db_path=db)["status"] == "rejected" and _rows(db, "quality_guard") == 1
    assert _events(grp, db).count("recurred") == 1
    changed = dict(review, suggestions=review["suggestions"] + ["📦 `agents/b.py` (900줄) — 모듈 분리 검토"])
    signals.record_quality_review(changed, db_path=db)
    t = get_task(grp, db_path=db)
    assert t["status"] == "triaged" and "agents/b.py" in t["detail"] and _rows(db, "quality_guard") == 1


# ── 전 계층 시뮬레이션: 신선도 → API → 디스패처(런북 ok) → 다음 날 신선도 ─────────────────────

def _flask_opener(app):
    client = app.test_client()

    def opener(req, timeout=10):
        path = req.full_url.split("http://test", 1)[1]
        resp = client.open(path, method=req.get_method(), headers=dict(req.header_items()),
                           data=req.data, content_type="application/json" if req.data else None)
        if resp.status_code >= 400:
            raise urllib.error.HTTPError(req.full_url, resp.status_code, "err", {}, io.BytesIO(resp.get_data()))
        out = io.BytesIO(resp.get_data())
        out.status = resp.status_code
        return out
    return opener


def test_full_stack_three_days_via_api_and_dispatcher(monkeypatch, tmp_path):
    import api.server as s
    from api.auth import _issue_token
    from scripts.agent_dispatcher import ApiClient, Dispatcher
    db = tmp_path / "ledger.db"
    monkeypatch.setattr(s, "_AGENT_TASK_DB_PATH", db)
    monkeypatch.setattr(s._agent_runbooks, "run", lambda name: {"ok": True, "summary": f"{name} 백그라운드 시작 (접수)", "detail": {}})
    monkeypatch.setattr(ApiClient, "_login", lambda self, email, pw: _issue_token("vps@msd.com", "agent"))
    client = ApiClient("http://test", "vps@msd.com", "pw", opener=_flask_opener(s.app))
    dispatcher = Dispatcher(client, max_tasks=5)
    (tid,) = fr.record_freshness_problems([STALE], today=TODAY, db_path=db)
    for day in (1, 2, 3):
        if day > 1:
            assert fr.record_freshness_problems([STALE], today=TODAY, db_path=db) == [tid]
        out = dispatcher.run_once()
        assert out["handled"] == 1 and out["errors"] == 0, day
        t = get_task(tid, db_path=db)
        assert t["status"] == "closed" and t["attempts"] == day and _rows(db) == 1
    assert fr.record_freshness_problems([STALE], today=TODAY, db_path=db) == [tid]
    assert get_task(tid, db_path=db)["status"] == "blocked"
    out = dispatcher.run_once()
    assert out["candidates"] == 0 and out["handled"] == 0 and _rows(db) == 1
    assert sum(1 for e in _events(tid, db) if e == "claimed") == 3
