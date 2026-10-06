"""플랫폼 수정(P1.6) 뒤의 신선도·적체 판정 — '조용히 틀린 상태' 가 되돌아오면 에이전트 팀이 알아챈다.

두 종류의 문제를 가른다(예산 ≤3건/일 — 설계 제약):
  - 장애/회귀  → kind=ops_incident: source_ref "{domain}:{YYYY-MM}", 런북이 있으면 디스패처가 선점.
  - 사람 몫 적체 → kind=improvement: source_ref "{domain}:backlog" (월 키 없음) — 도메인당 1행, 끝날 때까지 열려 있고
                    디스패처(CLAIMABLE_KINDS=ops_incident)는 절대 집지 않는다. rejected 는 '안 한다' 는 결정으로 존중.
도메인: pipeline_expected_sessions / committee_results(두 원인 구분) / committee_reports / reimbursement_checklist_coverage /
human_backlog. 실제 DB·네트워크 금지 — 필요한 테이블만 가진 tmp_path DB.
"""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from agents.agent_tasks import freshness as fr
from agents.agent_tasks.store import get_task, list_events, list_tasks, update_task

TODAY = date(2026, 10, 6)


def _seed(db: Path, *statements: str) -> None:
    conn = sqlite3.connect(db)
    for s in statements:
        conn.executescript(s)
    conn.commit()
    conn.close()


def _ctx(tmp_path, today=TODAY, **kw) -> fr.Ctx:
    db = tmp_path / "d.db"
    if not db.exists():
        sqlite3.connect(db).close()
    return fr.Ctx(db_path=db, base_dir=tmp_path, today=today, policy_root=tmp_path / "pi", **kw)


# ── kind: ops_incident(월 키) vs improvement(도메인당 1행) ─────────────────────

def test_freshness_kind_defaults_to_ops_incident():
    r = fr.Freshness("x", True, None, "d", "e")
    assert r.kind == "ops_incident"


def test_improvement_task_is_one_row_per_domain_without_month_key(tmp_path):
    db = tmp_path / "a.db"
    backlog = fr.Freshness("human_backlog", True, "43", "0", "적체 43건", None, 3, kind="improvement")
    ids = fr.record_freshness_problems([backlog], today=date(2026, 9, 30), db_path=db)
    t = get_task(ids[0], db_path=db)
    assert t["kind"] == "improvement" and t["source"] == "domain_freshness" and t["source_ref"] == "human_backlog:backlog"
    assert t["title"].startswith("사람 몫 적체") and "런북 없음" in t["detail"]
    # 다음 달에도 같은 행 — 월 키가 없다 (ops_incident 는 새 달에 새 행)
    assert fr.record_freshness_problems([backlog], today=date(2026, 10, 1), db_path=db) == ids
    assert len(list_tasks(db_path=db)) == 1
    # 디스패처가 보는 목록(kind=ops_incident)에는 없다
    assert list_tasks(db_path=db, kinds=("ops_incident",)) == []
    from scripts.agent_dispatcher import CLAIMABLE_KINDS
    assert CLAIMABLE_KINDS == "ops_incident"


def test_improvement_task_reopens_after_close_but_respects_rejected(tmp_path):
    """사람이 닫았는데 다음 날에도 적체면 같은 행을 다시 연다(끝난 게 아니었다). rejected 는 '안 한다' — recurred 만."""
    db = tmp_path / "a.db"
    backlog = fr.Freshness("human_backlog", True, "43", "0", "적체 43건", None, 3, kind="improvement")
    (tid,) = fr.record_freshness_problems([backlog], today=TODAY, db_path=db)
    update_task(tid, db_path=db, status="triaged", actor="admin")
    update_task(tid, db_path=db, status="in_progress", actor="admin")
    update_task(tid, db_path=db, status="closed", actor="admin", resolution="입력 완료")
    assert fr.record_freshness_problems([backlog], today=TODAY, db_path=db) == [tid]
    assert get_task(tid, db_path=db)["status"] == "triaged" and "reopened" in [e["event"] for e in list_events(tid, db_path=db)]
    update_task(tid, db_path=db, status="rejected", actor="admin", resolution="이 항목은 안 한다")
    assert fr.record_freshness_problems([backlog], today=TODAY, db_path=db) == [tid]
    assert get_task(tid, db_path=db)["status"] == "rejected"           # 존중 — 다시 열지 않는다


def test_ops_incident_keeps_month_key_and_title(tmp_path):
    db = tmp_path / "a.db"
    stale = fr.Freshness("domestic_price", True, "2026.07.01", "2026.09.01", "오래됨", "domestic_price_catchup")
    (tid,) = fr.record_freshness_problems([stale], today=TODAY, db_path=db)
    t = get_task(tid, db_path=db)
    assert t["kind"] == "ops_incident" and t["source_ref"] == "domestic_price:2026-10" and t["title"].startswith("데이터 신선도")


def test_domain_freshness_table_records_kind(tmp_path):
    db = tmp_path / "a.db"
    fr.write_domain_freshness([fr.Freshness("human_backlog", True, "43", "0", "적체", None, 3, kind="improvement"),
                               fr.Freshness("news_archive", False, "x", "y", "신선", "news_daily_fetch")], db_path=db)
    rows = dict(sqlite3.connect(db).execute("SELECT domain, kind FROM domain_freshness").fetchall())
    assert rows == {"human_backlog": "improvement", "news_archive": "ops_incident"}
