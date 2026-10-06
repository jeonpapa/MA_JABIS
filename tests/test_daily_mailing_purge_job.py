"""daily_mailing 만료 행 purge 는 스케줄 잡(daily_mailing_health_job)의 일 — 칸반 GET 의 부수효과가 아니다.

원칙(CLAUDE.md): 스크레이퍼 → DB → 대쉬보드. 대쉬보드는 DB 만 읽는다. 이전엔 /api/admin/daily-mailing/kanban GET 이
load_admin_kanban(purge=True) 로 만료 행을 DELETE 했다(스모크만 X-MA-Smoke 로 건너뜀). 이제 GET 은 purge=False,
purge 는 매일 09:30 헬스 잡이 수행하고 counts 에 삭제 수를 남긴다.
네트워크·실제 DB 금지: health_check / DispatchDeps.production / purge 를 가짜로.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scheduler as sched  # noqa: E402


def _stub_health(monkeypatch, *, ok: bool = True):
    import agents.daily_mailing.dispatch as dispatch
    monkeypatch.setattr(dispatch.DispatchDeps, "production", classmethod(lambda cls, db_path=None: object()))
    monkeypatch.setattr(dispatch, "health_check",
                        lambda deps: {"ok": ok, "run_date": "2026-10-06",
                                      "problems": [] if ok else [{"problem": "not_generated", "subscription_id": 1}]})


def test_health_job_purges_expired_rows_and_reports_counts(monkeypatch):
    from agents.daily_mailing import storage
    _stub_health(monkeypatch)
    calls: list[tuple] = []
    monkeypatch.setattr(storage, "purge_expired_daily_mailing_rows",
                        lambda *a, **kw: calls.append((a, kw)) or {"runs_deleted": 2, "articles_deleted": 7, "cutoff": "x"})
    res = sched.daily_mailing_health_job()
    assert res["ok"] is True and len(calls) == 1
    assert res["counts"]["purged_runs"] == 2 and res["counts"]["purged_articles"] == 7


def test_purge_failure_does_not_mask_health_check(monkeypatch):
    """purge 가 죽어도 헬스 점검은 돈다 — purge 오류는 errors 로 잡 실패에 남는다."""
    from agents.daily_mailing import storage
    _stub_health(monkeypatch)
    monkeypatch.setattr(storage, "purge_expired_daily_mailing_rows",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("database is locked")))
    res = sched.daily_mailing_health_job()
    assert res["ok"] is False and any("database is locked" in e for e in res["errors"])
    assert res["counts"]["problems"] == 0                                 # 헬스 점검 자체는 수행됨


def test_load_admin_kanban_default_is_read_only(tmp_path, monkeypatch):
    """storage 기본값도 purge=False — 어떤 호출자도 '조회' 로 DELETE 하지 않는다."""
    from agents.daily_mailing import storage
    calls: list = []
    monkeypatch.setattr(storage, "purge_expired_daily_mailing_rows", lambda *a, **kw: calls.append(1) or {})
    storage.load_admin_kanban(tmp_path / "dm.db")
    assert calls == []
