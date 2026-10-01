"""QG·RC 산출물 → 원장 (P1.5 Task 6).

오탐 억제가 핵심: QG 는 매일 같은 13건(코드패턴 2 + 제안 11)을 낸다. 날짜 키면 하루 13건씩 쌓인다.
- 코드패턴/제안은 그룹 키(qg:issue:<유형>, qg:suggestion:<이모지>)로 improvement 1건씩 — 내용이 바뀌면 refreshed 이벤트.
- 숫자는 내용 키에서 지운다 — "793줄" 이 "801줄" 이 돼도 같은 내용(refreshed 조차 없다).
- 회귀/drift 는 낱개 ops_incident, RC FAIL 은 rc:<memory> 로 메모리당 1건, blocked 도 억제.
"""
from __future__ import annotations

from agents.agent_tasks.signals import (
    _content_key, count_source_tasks, record_compliance_results, record_quality_review,
)
from agents.agent_tasks.store import list_events, list_tasks, update_task
from agents.rule_compliance.checks import CheckResult

REVIEW = {
    "date": "2026-07-28",
    "code_issues": ["BaseScraper 미상속: tier1_news_sites.py", "BaseScraper 미상속: kr_health_kr.py"],
    "rule_drifts": [],
    "mfds_regressions": [],
    "suggestions": [
        "📦 `agents/reimb_pipeline.py` (793줄) — 모듈 분리 검토 (scraper/parser/business logic 레이어 구분)",
        "📦 `agents/foreign_price_agent.py` (764줄) — 모듈 분리 검토 (scraper/parser/business logic 레이어 구분)",
        "🔑 `DISEASE_KR` 미커버 disease: CMV, CMV_P, KIDTX — 해당 indication 은 MFDS 매칭 0 이므로 키워드 추가 필요",
        "💊 drug_enrichment 커버리지 28/67846 (0.041%) — 비교약제 추가 시 대부분 일일투약비/허가일 공백.",
        "🔐 DocCheck 로그인 실패 — 자격증명 만료/변경 가능성. config/.env 갱신 필요",
    ],
    "report_path": "x",
}


def test_content_key_ignores_numbers():
    assert _content_key("agents/reimb_pipeline.py (793줄)") == _content_key("agents/reimb_pipeline.py (801줄)")
    assert _content_key("a") != _content_key("b")


def test_quality_review_groups_items(tmp_path):
    db = tmp_path / "a.db"
    ids = record_quality_review(REVIEW, db_path=db)
    assert len(ids["incident"]) == 0 and len(ids["improvement"]) == 5
    tasks = {t["source_ref"]: t for t in list_tasks(db_path=db)}
    assert "qg:issue:BaseScraper 미상속" in tasks and tasks["qg:issue:BaseScraper 미상속"]["kind"] == "improvement"
    assert tasks["qg:suggestion:📦"]["detail"].count("📦") == 2
    assert {"qg:suggestion:🔑", "qg:suggestion:💊", "qg:suggestion:🔐"} <= set(tasks)
    assert all(t["kind"] == "improvement" and t["priority"] == 3 for t in tasks.values())
    assert count_source_tasks("quality_guard", db_path=db) == 5


def test_quality_review_same_content_next_day_adds_nothing(tmp_path):
    db = tmp_path / "a.db"
    record_quality_review(REVIEW, db_path=db)
    record_quality_review(REVIEW, db_path=db)
    assert len(list_tasks(db_path=db)) == 5
    assert count_source_tasks("quality_guard", db_path=db) == 5
    tid = list_tasks(db_path=db)[0]["id"]
    assert [e["event"] for e in list_events(tid, db_path=db)] == ["created"]


def test_quality_review_number_only_drift_is_not_a_change(tmp_path):
    """'793줄'→'801줄', '28/67846'→'28/67900' 은 같은 내용 — detail 도 이벤트도 그대로(매일 refreshed 가 뜨면 그것도 오탐)."""
    db = tmp_path / "a.db"
    record_quality_review(REVIEW, db_path=db)
    drifted = dict(REVIEW, suggestions=[
        s.replace("793줄", "801줄").replace("28/67846 (0.041%)", "28/67900 (0.040%)") for s in REVIEW["suggestions"]])
    record_quality_review(drifted, db_path=db)
    tasks = {t["source_ref"]: t for t in list_tasks(db_path=db)}
    assert len(tasks) == 5
    assert "793줄" in tasks["qg:suggestion:📦"]["detail"] and "28/67846" in tasks["qg:suggestion:💊"]["detail"]
    for ref in ("qg:suggestion:📦", "qg:suggestion:💊"):
        assert [e["event"] for e in list_events(tasks[ref]["id"], db_path=db)] == ["created"]


def test_quality_review_changed_group_refreshes_detail(tmp_path):
    db = tmp_path / "a.db"
    record_quality_review(REVIEW, db_path=db)
    changed = dict(REVIEW, suggestions=REVIEW["suggestions"] + ["📦 `agents/analog/store.py` (957줄) — 모듈 분리 검토"])
    record_quality_review(changed, db_path=db)
    assert count_source_tasks("quality_guard", db_path=db) == 5          # 새 과제 X
    t = {t["source_ref"]: t for t in list_tasks(db_path=db)}["qg:suggestion:📦"]
    assert "analog/store.py" in t["detail"]
    assert [e["event"] for e in list_events(t["id"], db_path=db)] == ["created", "refreshed"]


def test_quality_review_closed_group_is_recreated_but_blocked_is_not(tmp_path):
    db = tmp_path / "a.db"
    record_quality_review(REVIEW, db_path=db)
    first = {t["source_ref"]: t for t in list_tasks(db_path=db)}
    update_task(first["qg:suggestion:🔐"]["id"], db_path=db, status="blocked", actor="t", resolution="x")
    update_task(first["qg:suggestion:💊"]["id"], db_path=db, status="rejected", actor="t", resolution="x")
    record_quality_review(REVIEW, db_path=db)
    assert count_source_tasks("quality_guard", db_path=db) == 6           # 💊 만 다시, 🔐(blocked) 는 억제


def test_regression_and_drift_are_incidents(tmp_path):
    db = tmp_path / "a.db"
    review = dict(REVIEW, code_issues=[], suggestions=[],
                  mfds_regressions=[{"indication_id": "keytruda-nsclc-adj", "expected": "2023-12-19", "actual": "2024-05-14", "reason": "peri 문단"}],
                  rule_drifts=[{"rule": "foreign_agent_rules §daily_cost", "file": "agents/foreign_price_agent.py", "severity": "ERROR"}])
    ids = record_quality_review(review, db_path=db)
    assert len(ids["incident"]) == 2
    tasks = {t["source_ref"]: t for t in list_tasks(db_path=db)}
    reg = tasks["qg:regression:keytruda-nsclc-adj"]
    assert reg["kind"] == "ops_incident" and reg["priority"] == 1 and "2024-05-14" in reg["detail"]
    drift = [t for t in tasks.values() if t["source_ref"].startswith("qg:drift:")][0]
    assert drift["kind"] == "ops_incident" and drift["priority"] == 2
    assert record_quality_review(review, db_path=db) == ids                # 둘째 날 같은 id
    assert count_source_tasks("quality_guard", db_path=db) == 2


def test_compliance_fail_one_task_per_memory(tmp_path):
    db = tmp_path / "a.db"
    results = [
        CheckResult("project_patent_classification_policy", "fail", "옵디보주: 기대=유효, 실제=만료", {"mismatches": 1}),
        CheckResult("project_xnational_reimbursement", "fail", "last_fetched=2026-04-29 > 90일", {"total": 44}),
        CheckResult("project_indication_level_approval", "pass", "104건", {}),
        CheckResult("user_joseph", "skip", "정적", {}),
    ]
    ids = record_compliance_results(results, db_path=db)
    assert len(ids) == 2
    tasks = {t["source_ref"]: t for t in list_tasks(db_path=db)}
    assert set(tasks) == {"rc:project_patent_classification_policy", "rc:project_xnational_reimbursement"}
    assert all(t["kind"] == "ops_incident" and t["priority"] == 2 for t in tasks.values())
    assert tasks["rc:project_xnational_reimbursement"]["detail"].count("런북: reimbursement_xnational_sync") == 1
    assert "런북 없음" in tasks["rc:project_patent_classification_policy"]["detail"]
    assert record_compliance_results(results, db_path=db) == ids
    update_task(ids[0], db_path=db, status="blocked", actor="dispatcher", resolution="x")
    assert record_compliance_results(results, db_path=db) == ids           # blocked 도 억제
    assert count_source_tasks("rule_compliance", db_path=db) == 2
