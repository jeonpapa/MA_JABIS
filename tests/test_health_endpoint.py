"""생존 헬스 — heartbeat 파일 판정 + /api/health 200/503 행렬 (P1.5 Task 4).

503 은 DB 접근 불가 때만. 스케줄러 heartbeat stale 은 200 + status='degraded' + 원장 과제 — Fly check 실패는 라우팅을
끊지만 재시작을 보장하지 않으므로, 배경 잡 정지로 사용자 화면을 내리지 않는다.
부팅 유예 안에서는 파일이 없어도·오래됐어도 200 — 배포 직후 볼륨에 남은 옛 heartbeat 로 Fly check 가 새 머신을
죽이지 않게(첫 HTTP check 도입의 가장 큰 위험). 원장 기록·job run 조회 실패는 응답을 바꾸지 않는다(신호 경계).
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import api.server as s
from agents.agent_tasks import heartbeat as hb
from agents.agent_tasks import jobs as jobs_mod
from agents.agent_tasks import signals as signals_mod
from agents.agent_tasks.store import list_tasks


def _age(p: Path, seconds: float) -> None:
    old = time.time() - seconds
    os.utime(p, (old, old))


# ── heartbeat 모듈 ──────────────────────────────────────────────────────────

def test_write_and_status_fresh(tmp_path):
    p = tmp_path / "hb.json"
    hb.write_heartbeat(pid=123, path=p)
    st = hb.heartbeat_status(now=time.time(), process_started_at=time.time() - 1000, path=p)
    assert st["stale"] is False and 0 <= st["age_s"] < 5


def test_write_is_atomic_and_leaves_no_tmp(tmp_path):
    import json
    p = tmp_path / "nested" / "hb.json"            # 디렉터리도 만든다(볼륨 첫 부팅)
    hb.write_heartbeat(pid=42, path=p)
    body = json.loads(p.read_text(encoding="utf-8"))
    assert body["pid"] == 42 and abs(body["ts"] - time.time()) < 5
    assert not p.with_suffix(".tmp").exists()


def test_status_stale_after_threshold(tmp_path):
    p = tmp_path / "hb.json"
    hb.write_heartbeat(pid=1, path=p)
    _age(p, hb.STALE_AFTER_S + 10)
    assert hb.heartbeat_status(now=time.time(), process_started_at=time.time() - 1000, path=p)["stale"] is True


def test_missing_file_has_boot_grace(tmp_path):
    p = tmp_path / "hb.json"
    assert hb.heartbeat_status(now=time.time(), process_started_at=time.time() - 10, path=p)["stale"] is False
    assert hb.heartbeat_status(now=time.time(), process_started_at=time.time() - hb.BOOT_GRACE_S - 1, path=p)["stale"] is True


def test_old_file_inside_boot_grace_is_not_stale(tmp_path):
    """배포 직후: 볼륨에 전 머신의 옛 heartbeat 가 남아 있어도 부팅 유예 안에는 stale 이 아니다 (스케줄러가 60초 안에 덮어쓴다)."""
    p = tmp_path / "hb.json"
    hb.write_heartbeat(pid=1, path=p)
    _age(p, hb.STALE_AFTER_S + 600)
    now = time.time()
    assert hb.heartbeat_status(now=now, process_started_at=now - 10, path=p)["stale"] is False
    assert hb.heartbeat_status(now=now, process_started_at=now - hb.BOOT_GRACE_S - 1, path=p)["stale"] is True


# ── /api/health ─────────────────────────────────────────────────────────────

def _client(monkeypatch, tmp_path, *, hb_age: float | None, uptime: float = 1000.0):
    """hb_age=None 이면 파일 없음. uptime 은 웹 프로세스 가동 시간(초)."""
    p = tmp_path / "hb.json"
    if hb_age is not None:
        hb.write_heartbeat(pid=1, path=p)
        if hb_age > 0:
            _age(p, hb_age)
    monkeypatch.setattr(hb, "HEARTBEAT_PATH", p)
    monkeypatch.setattr(s, "_PROCESS_STARTED_AT", time.time() - uptime)
    return s.app.test_client()


def test_health_ok_shape(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, hb_age=0)
    r = c.get("/api/health")
    body = r.get_json()
    assert r.status_code == 200 and body["status"] == "ok" and body["db_ok"] is True
    assert body["scheduler_heartbeat_age_s"] < 5 and body["scheduler_stale"] is False
    assert isinstance(body["frontend_build"], bool)
    assert isinstance(body["jobs_failed_24h"], list) and "available_countries" in body


def test_health_degraded_not_503_when_scheduler_stale_and_records_once(monkeypatch, tmp_path):
    """스케줄러가 멈춰도 사이트는 서비스한다 — 200 + degraded + 원장 과제 1건."""
    c = _client(monkeypatch, tmp_path, hb_age=hb.STALE_AFTER_S + 10)
    db = Path(os.environ["AGENT_TASK_DB"])                 # conftest 가 임시 파일로 돌린다
    r = c.get("/api/health")
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "degraded" and body["db_ok"] is True and body["scheduler_stale"] is True
    assert c.get("/api/health").status_code == 200
    tasks = list_tasks(db_path=db)
    assert len(tasks) == 1 and tasks[0]["source_ref"].startswith("scheduler_heartbeat:")
    assert tasks[0]["source"] == "scheduler_job"


def test_health_200_missing_heartbeat_inside_boot_grace(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, hb_age=None, uptime=10)
    r = c.get("/api/health")
    body = r.get_json()
    assert r.status_code == 200 and body["status"] == "ok"
    assert body["scheduler_heartbeat_age_s"] is None and body["scheduler_stale"] is False
    assert list_tasks(db_path=Path(os.environ["AGENT_TASK_DB"])) == []


def test_health_degraded_missing_heartbeat_past_boot_grace(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, hb_age=None, uptime=hb.BOOT_GRACE_S + 1)
    r = c.get("/api/health")
    body = r.get_json()
    assert r.status_code == 200 and body["scheduler_stale"] is True and body["status"] == "degraded"


def test_health_200_stale_file_inside_boot_grace(monkeypatch, tmp_path):
    """배포 직후 옛 heartbeat (전 머신이 15분 넘게 꺼져 있던 경우) → 유예 안에는 200. 첫 check 가 머신을 죽이지 않는다."""
    c = _client(monkeypatch, tmp_path, hb_age=hb.STALE_AFTER_S + 600, uptime=10)
    r = c.get("/api/health")
    assert r.status_code == 200 and r.get_json()["scheduler_stale"] is False


def test_health_503_when_db_down(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, hb_age=0)
    monkeypatch.setattr(s, "_health_db_ok", lambda: False)
    r = c.get("/api/health")
    assert r.status_code == 503 and r.get_json()["db_ok"] is False and r.get_json()["status"] == "degraded"


def test_health_reports_failed_jobs_24h(monkeypatch, tmp_path):
    db = Path(os.environ["AGENT_TASK_DB"])
    now = datetime.now(timezone.utc)
    jobs_mod.record_job_run("competitor_news_daily", now, {"ok": False, "counts": {}, "errors": ["x"]}, db_path=db)
    jobs_mod.record_job_run("nhis_negotiation_sync", now, {"ok": True, "counts": {}, "errors": []}, db_path=db)
    c = _client(monkeypatch, tmp_path, hb_age=0)
    r = c.get("/api/health")
    assert r.status_code == 200 and r.get_json()["jobs_failed_24h"] == ["competitor_news_daily"]


def test_health_survives_ledger_errors(monkeypatch, tmp_path):
    """신호 경계: job run 조회·원장 기록이 죽어도 /api/health 는 500 이 아니라 판정대로 응답한다."""
    def boom(*a, **kw):
        raise RuntimeError("DB 잠김")
    monkeypatch.setattr(jobs_mod, "recent_job_runs", boom)
    monkeypatch.setattr(signals_mod, "record_job_failure", boom)
    c = _client(monkeypatch, tmp_path, hb_age=0)
    r = c.get("/api/health")
    assert r.status_code == 200 and r.get_json()["jobs_failed_24h"] == []
    c2 = _client(monkeypatch, tmp_path, hb_age=hb.STALE_AFTER_S + 10)
    assert c2.get("/api/health").status_code == 200          # stale 은 503 이 아니다
