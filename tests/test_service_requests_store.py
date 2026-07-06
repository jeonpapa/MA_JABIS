"""서비스 보완/개선 요청 store 단위 테스트 (tmp sqlite, 실 DB 미접촉)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.service_requests import store as srs

FULL_CHECKLIST = {k: True for k in srs.CHECKLIST_KEYS}


@pytest.fixture()
def db(tmp_path):
    return tmp_path / "service_requests_test.db"


def _create(db, owner="joseph@test.com", **kw):
    kw.setdefault("title", "국내약가 표 정렬 개선")
    kw.setdefault("body", "정렬이 등재일 기준이 아님")
    kw.setdefault("expected_outcome", "등재일 내림차순 기본 정렬")
    kw.setdefault("page_path", "/domestic")
    kw.setdefault("page_label", "국내 약가")
    return srs.create_request(owner, db_path=db, **kw)


def test_create_returns_row_and_create_event(db):
    item = _create(db, context={"route": "/domestic", "query": {"tab": "price"}})
    assert item["id"] == 1
    assert item["owner_email"] == "joseph@test.com"
    assert item["status"] == "open"
    assert item["request_type"] == "improvement"
    assert item["priority"] == "medium"
    assert item["package_status"] == "none"
    assert item["context"] == {"route": "/domestic", "query": {"tab": "price"}}
    assert item["checklist"] is None
    events = srs.list_events(item["id"], db_path=db)
    assert len(events) == 1
    assert events[0]["event_type"] == "create"
    assert events[0]["to_status"] == "open"
    assert events[0]["actor_email"] == "joseph@test.com"


def test_create_requires_title(db):
    with pytest.raises(ValueError):
        _create(db, title="   ")


def test_owner_scope_isolation(db):
    _create(db, owner="a@test.com", title="A의 요청")
    _create(db, owner="b@test.com", title="B의 요청")
    mine_a = srs.list_mine("a@test.com", db_path=db)
    assert [r["title"] for r in mine_a] == ["A의 요청"]
    assert all(r["owner_email"] == "a@test.com" for r in mine_a)
    assert len(srs.list_all(db_path=db)) == 2


def test_list_all_filters(db):
    _create(db, title="버그", request_type="bug", priority="high")
    _create(db, title="개선", request_type="improvement")
    assert [r["title"] for r in srs.list_all(request_type="bug", db_path=db)] == ["버그"]
    assert [r["title"] for r in srs.list_all(priority="high", db_path=db)] == ["버그"]
    assert srs.list_all(status="sent", db_path=db) == []


def test_admin_update_writes_update_event_with_status_change(db):
    item = _create(db)
    updated = srs.admin_update(item["id"], "admin@test.com",
                               status="in_review", admin_note="확인 중", db_path=db)
    assert updated["status"] == "in_review"
    assert updated["admin_note"] == "확인 중"
    events = srs.list_events(item["id"], db_path=db)
    assert [e["event_type"] for e in events] == ["create", "update"]
    ev = events[-1]
    assert ev["from_status"] == "open" and ev["to_status"] == "in_review"
    assert ev["actor_email"] == "admin@test.com"
    assert ev["payload"]["admin_note"] == "확인 중"
    assert srs.admin_update(9999, "admin@test.com", status="done", db_path=db) is None


def test_redaction_masks_sensitive_keys(db):
    item = _create(db, context={
        "authToken": "x", "note": "ok",
        "nested": {"API_KEY": "abc", "Cookie": "sid=1", "page": "/home"},
        "list": [{"password": "p"}],
    })
    ctx = item["context"]
    assert ctx["authToken"] == "[REDACTED]"
    assert ctx["note"] == "ok"
    assert ctx["nested"]["API_KEY"] == "[REDACTED]"
    assert ctx["nested"]["Cookie"] == "[REDACTED]"
    assert ctx["nested"]["page"] == "/home"
    assert ctx["list"][0]["password"] == "[REDACTED]"


def test_save_package_generate_contains_safety_and_no_deploy(db):
    item = _create(db)
    result = srs.save_package(item["id"], "admin@test.com", mode="generate", db_path=db)
    assert isinstance(result, dict)
    md = result["package_markdown"]
    assert "SAFETY / REDACTION CHECKLIST" in md
    assert srs.NO_DEPLOY_LINE in md
    assert "배포(flyctl deploy)·git push 금지" in md
    assert item["title"] in md
    assert result["package_status"] == "draft"
    assert result["status"] == "packaged"
    # save_final 은 편집본 저장
    result2 = srs.save_package(item["id"], "admin@test.com",
                               mode="save_final", markdown="# 편집본", db_path=db)
    assert result2["package_markdown"] == "# 편집본"
    assert result2["package_status"] == "final"
    # 오류 케이스
    assert srs.save_package(item["id"], "a", mode="bogus", db_path=db) == (None, "invalid mode")
    assert srs.save_package(item["id"], "a", mode="save_draft", db_path=db) == (None, "markdown required")
    assert srs.save_package(9999, "a", mode="generate", db_path=db) == (None, "not found")


def test_confirm_requires_full_checklist(db):
    item = _create(db)
    partial = dict(FULL_CHECKLIST, no_deploy_ack=False)
    assert srs.confirm_request(item["id"], "admin@test.com", partial, db_path=db) == (None, "checklist incomplete")
    assert srs.confirm_request(item["id"], "admin@test.com", {}, db_path=db) == (None, "checklist incomplete")
    result = srs.confirm_request(item["id"], "admin@test.com", FULL_CHECKLIST, db_path=db)
    assert isinstance(result, dict)
    assert result["status"] == "confirmed"
    assert result["confirmed_at"]
    assert result["checklist"] == FULL_CHECKLIST


def test_send_fails_before_confirm_succeeds_after(db):
    item = _create(db)
    srs.save_package(item["id"], "admin@test.com", mode="generate", db_path=db)
    assert srs.send_to_claude(item["id"], "admin@test.com", db_path=db) == (None, "not confirmed")
    srs.confirm_request(item["id"], "admin@test.com", FULL_CHECKLIST, db_path=db)
    result = srs.send_to_claude(item["id"], "admin@test.com", db_path=db)
    assert isinstance(result, dict)
    assert result["status"] == "sent"
    assert result["sent_at"]
    assert result["sent_markdown"] == result["package_markdown"]
    assert srs.NO_DEPLOY_LINE in result["sent_markdown"]
    assert srs.send_to_claude(9999, "admin@test.com", db_path=db) == (None, "not found")


def test_full_flow_events_recorded(db):
    item = _create(db)
    srs.admin_update(item["id"], "admin@test.com", status="in_review", db_path=db)
    srs.save_package(item["id"], "admin@test.com", mode="generate", db_path=db)
    srs.confirm_request(item["id"], "admin@test.com", FULL_CHECKLIST, db_path=db)
    srs.send_to_claude(item["id"], "admin@test.com", db_path=db)
    events = srs.list_events(item["id"], db_path=db)
    assert [e["event_type"] for e in events] == ["create", "update", "package", "confirm", "send"]
    assert events[-1]["from_status"] == "confirmed" and events[-1]["to_status"] == "sent"


def test_ensure_tables_idempotent(db):
    srs.ensure_service_request_tables(db)
    srs.ensure_service_request_tables(db)
    _create(db)
    assert len(srs.list_all(db_path=db)) == 1


# ── 위임 루프 (outbox → claim → resolve) ──────────────────────────────────────

def _send(db, **kw) -> dict:
    """생성→패키지→확인→sent 까지 진행한 요청 반환."""
    item = _create(db, **kw)
    srs.save_package(item["id"], "admin@test.com", mode="generate", db_path=db)
    srs.confirm_request(item["id"], "admin@test.com", FULL_CHECKLIST, db_path=db)
    return srs.send_to_claude(item["id"], "admin@test.com", db_path=db)


def test_outbox_lists_only_sent(db):
    _create(db, title="아직 open")
    sent = _send(db, title="위임된 요청")
    items = srs.list_outbox(db_path=db)
    assert [i["id"] for i in items] == [sent["id"]]
    assert items[0]["status"] == "sent"
    # full row — 에이전트가 소비할 패키지 마크다운 포함
    assert srs.NO_DEPLOY_LINE in items[0]["package_markdown"]
    assert items[0]["sent_markdown"] == items[0]["package_markdown"]
    # claim 하면 outbox 에서 빠진다
    srs.claim_request(sent["id"], "claude-code", db_path=db)
    assert srs.list_outbox(db_path=db) == []


def test_claim_only_from_sent(db):
    open_item = _create(db, title="open 상태")
    assert srs.claim_request(open_item["id"], "claude-code", db_path=db) is None
    assert srs.claim_request(9999, "claude-code", db_path=db) is None
    sent = _send(db)
    claimed = srs.claim_request(sent["id"], "claude-code", db_path=db)
    assert claimed["status"] == "in_progress"
    assert claimed["claimed_by"] == "claude-code"
    assert claimed["claimed_at"]
    ev = srs.list_events(sent["id"], db_path=db)[-1]
    assert ev["event_type"] == "claim"
    assert ev["from_status"] == "sent" and ev["to_status"] == "in_progress"
    # 이중 claim 방지 (이미 in_progress)
    assert srs.claim_request(sent["id"], "other-agent", db_path=db) is None


def test_resolve_sets_fields_and_event(db):
    sent = _send(db)
    srs.claim_request(sent["id"], "claude-code", db_path=db)
    result = srs.resolve_request(
        sent["id"], "claude-code",
        status="done", resolution_note="정렬 로직 수정 완료", commit_ref="abc1234",
        db_path=db,
    )
    assert isinstance(result, dict)
    assert result["status"] == "done"
    assert result["resolution_note"] == "정렬 로직 수정 완료"
    assert result["commit_ref"] == "abc1234"
    assert result["resolved_by"] == "claude-code"
    assert result["resolved_at"]
    ev = srs.list_events(sent["id"], db_path=db)[-1]
    assert ev["event_type"] == "resolve"
    assert ev["from_status"] == "in_progress" and ev["to_status"] == "done"
    assert ev["payload"] == {"resolution_note": "정렬 로직 수정 완료", "commit_ref": "abc1234"}
    # round-trip — get_request 로 다시 읽어도 동일
    again = srs.get_request(sent["id"], db_path=db)
    for k in ("status", "resolution_note", "commit_ref", "resolved_by", "resolved_at",
              "claimed_by", "claimed_at"):
        assert again[k] == result[k]


def test_resolve_directly_from_sent_and_wont_fix(db):
    sent = _send(db)
    result = srs.resolve_request(
        sent["id"], "claude-code",
        status="wont_fix", resolution_note="CLAUDE.md 절대 금지 항목과 충돌", db_path=db)
    assert isinstance(result, dict)
    assert result["status"] == "wont_fix"
    assert result["commit_ref"] is None
    ev = srs.list_events(sent["id"], db_path=db)[-1]
    assert ev["from_status"] == "sent" and ev["to_status"] == "wont_fix"


def test_resolve_rejects_bad_status_and_states(db):
    sent = _send(db)
    assert srs.resolve_request(sent["id"], "a", status="rejected",
                               resolution_note="x", db_path=db) == (None, "bad status")
    assert srs.resolve_request(sent["id"], "a", status="sent",
                               resolution_note="x", db_path=db) == (None, "bad status")
    assert srs.resolve_request(9999, "a", status="done",
                               resolution_note="x", db_path=db) == (None, "not found")
    # note 필수 (CLI --note / UI 제출 게이트와 동일 계약) — 빈/공백 거부
    assert srs.resolve_request(sent["id"], "a", status="done",
                               resolution_note="", db_path=db) == (None, "note required")
    assert srs.resolve_request(sent["id"], "a", status="done",
                               resolution_note="   ", db_path=db) == (None, "note required")
    # open 상태에서는 resolve 불가
    open_item = _create(db)
    assert srs.resolve_request(open_item["id"], "a", status="done",
                               resolution_note="x", db_path=db) == (None, "not resolvable")
    # done 이후 재-resolve 불가
    srs.resolve_request(sent["id"], "a", status="done", resolution_note="ok", db_path=db)
    assert srs.resolve_request(sent["id"], "a", status="done",
                               resolution_note="again", db_path=db) == (None, "not resolvable")


def test_delegation_full_flow_events(db):
    sent = _send(db)
    srs.claim_request(sent["id"], "claude-code", db_path=db)
    srs.resolve_request(sent["id"], "claude-code", status="done",
                        resolution_note="완료", commit_ref="deadbee", db_path=db)
    events = srs.list_events(sent["id"], db_path=db)
    assert [e["event_type"] for e in events] == [
        "create", "package", "confirm", "send", "claim", "resolve"]
