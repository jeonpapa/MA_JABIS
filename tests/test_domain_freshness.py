"""도메인 신선도 판정기 (P1.5 Task 3) — 도메인 규칙(freshness.DOMAIN_CHECKS)·중복 억제·판정 불가 강등·잡/CLI/API 연결.

테스트는 실제 DB 를 읽지 않는다: 도메인마다 필요한 테이블/컬럼만 가진 tmp_path 픽스처 DB 를 만든다.
"""
from __future__ import annotations

import os
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path

from agents.agent_tasks import freshness as fr
from agents.agent_tasks.store import list_tasks, update_task

TODAY = date(2026, 9, 30)


def _seed(db: Path, *statements: str) -> None:
    conn = sqlite3.connect(db)
    for s in statements:
        conn.executescript(s)
    conn.commit()
    conn.close()


def _ctx(tmp_path, today=TODAY, root=None) -> fr.Ctx:
    db = tmp_path / "d.db"
    if not db.exists():
        sqlite3.connect(db).close()
    return fr.Ctx(db_path=db, base_dir=tmp_path, today=today, policy_root=root or tmp_path / "pi")


def test_domestic_price_stale_after_10th(tmp_path):
    _seed(tmp_path / "d.db", "CREATE TABLE drug_prices (apply_date TEXT); INSERT INTO drug_prices VALUES ('2026.07.01');")
    r = fr.check_domestic_price(_ctx(tmp_path))
    assert r.stale is True and r.observed == "2026.07.01" and r.deadline == "2026.09.01" and r.runbook == "domestic_price_catchup"
    r2 = fr.check_domestic_price(_ctx(tmp_path, today=date(2026, 8, 5)))      # 9일까지는 전월(7월) 이면 신선
    assert r2.stale is False and r2.deadline == "2026.07.01"


def test_exchange_rate_cache_uses_latest_file_window(tmp_path):
    d = tmp_path / "data" / "foreign" / "exchange_rate"
    d.mkdir(parents=True)
    (d / "keb_avg_rate_20230403_20260331.xlsx").write_bytes(b"x")
    (d / "keb_avg_rate_20230603_20260531.xlsx").write_bytes(b"x")
    r = fr.check_exchange_rate_cache(_ctx(tmp_path))
    assert r.stale is True and r.observed == "20260531" and r.deadline == "20260831"
    r2 = fr.check_exchange_rate_cache(_ctx(tmp_path, today=date(2026, 6, 3)))   # 5일 전에는 전전월 말 기준
    assert r2.stale is False and r2.deadline == "20260430"


def test_fx_alignment_counts_only_latest_priced_rows(tmp_path):
    d = tmp_path / "data" / "foreign" / "exchange_rate"
    d.mkdir(parents=True)
    (d / "keb_avg_rate_20230603_20260531.xlsx").write_bytes(b"x")
    _seed(tmp_path / "d.db", """
        CREATE TABLE foreign_drug_prices (id INTEGER PRIMARY KEY, query_name TEXT, country TEXT, local_price REAL, exchange_rate_to TEXT);
        INSERT INTO foreign_drug_prices VALUES (1,'keytruda','US',100,'20260331');
        INSERT INTO foreign_drug_prices VALUES (2,'keytruda','US',100,'20260531');   -- 최신 행은 정합
        INSERT INTO foreign_drug_prices VALUES (3,'welireg','UK',50,'20260331');     -- 최신 행이 구 환율
        INSERT INTO foreign_drug_prices VALUES (4,'welireg','FR',NULL,'20260331');   -- 가격 없음 → 제외
    """)
    # 2026-06-15: 기대 창 종료일 = 전월 말일 20260531 = 캐시 창 → 캐시는 최신, 최신 행이 구 환율인 welireg/UK 1건만 — 재계산 런북
    r = fr.check_foreign_price_fx_alignment(_ctx(tmp_path, today=date(2026, 6, 15)))
    assert r.stale is True and r.observed == "1" and r.runbook == "exchange_rate_recompute"


def test_foreign_approval_coverage_uses_module_function(tmp_path):
    _seed(tmp_path / "d.db", """
        CREATE TABLE foreign_drug_prices (query_name TEXT);
        CREATE TABLE indications_master (product TEXT);
        INSERT INTO foreign_drug_prices VALUES ('pembrolizumab'), ('belzutifan');
        INSERT INTO indications_master VALUES ('keytruda');
    """)
    r = fr.check_foreign_approval_coverage(_ctx(tmp_path))
    assert r.stale is True and "welireg" in r.evidence and r.runbook == "foreign_approval_sync"


def test_coverage_gaps_module_function_needs_no_agent(tmp_path):
    """ForeignApprovalAgent(스크레이퍼 6개 생성) 없이 모듈 함수만으로 판정 — alias 로 brand↔INN 을 맞춘다."""
    from agents.foreign_approval.agent import coverage_gaps
    db = tmp_path / "g.db"
    _seed(db, """
        CREATE TABLE foreign_drug_prices (query_name TEXT);
        CREATE TABLE indications_master (product TEXT);
        INSERT INTO foreign_drug_prices VALUES ('Pembrolizumab '), ('olaparib'), (NULL), ('');
        INSERT INTO indications_master VALUES ('keytruda'), ('lynparza');
    """)
    assert coverage_gaps(db) == []
    _seed(db, "INSERT INTO foreign_drug_prices VALUES ('letermovir');")
    assert coverage_gaps(db) == ["prevymis"]


def test_committee_schedule_flags_committee_without_upcoming_session(tmp_path):
    _seed(tmp_path / "d.db", """
        CREATE TABLE amjilsim_sessions (session_id INTEGER, committee_type TEXT, status TEXT, session_date TEXT);
        INSERT INTO amjilsim_sessions VALUES (1,'AMJILSIM','SCHEDULED','2026-12-23'), (2,'YAKPYUNGWI','SCHEDULED','2026-12-03'),
                                             (3,'YAKPYUNGWI','COMPLETED','2026-06-04');
    """)
    # 10-25 기준 60일 창(~12-24)에 두 위원회 모두 1건씩 — 계획 초안의 10-20 은 12-23 까지 64일이라 창 밖(산술 오류)
    assert fr.check_committee_schedule(_ctx(tmp_path, today=date(2026, 10, 25))).stale is False
    r = fr.check_committee_schedule(_ctx(tmp_path, today=date(2026, 12, 4)))     # YAKPYUNGWI 다음 세션 없음
    assert r.stale is True and "YAKPYUNGWI" in r.evidence and r.runbook is None


def test_committee_results_flags_past_scheduled_sessions(tmp_path):
    _seed(tmp_path / "d.db", """
        CREATE TABLE amjilsim_sessions (session_id INTEGER, committee_type TEXT, status TEXT, session_date TEXT);
        INSERT INTO amjilsim_sessions VALUES (1,'YAKPYUNGWI','SCHEDULED','2026-07-02'), (2,'YAKPYUNGWI','SCHEDULED','2026-10-15');
    """)
    r = fr.check_committee_results(_ctx(tmp_path))
    # P1.6: 잡 실패 신호가 없으면 소스 JSON 이 안 바뀐 것 — 런북(reimb_data_sync)은 '변경 없음 skip' 이라 무효 → None
    assert r.stale is True and r.observed == "2026-07-02" and r.runbook is None and "헤르메스/사람" in r.evidence
    from datetime import datetime, timezone
    from agents.agent_tasks.jobs import record_job_run
    record_job_run("reimb_data_sync", datetime.now(timezone.utc), {"ok": False, "counts": {}, "errors": ["원격 fetch 실패"]},
                   db_path=tmp_path / "d.db")                    # ledger_path 없음 → 데이터 DB 와 같은 파일(프로덕션 배치)
    r2 = fr.check_committee_results(_ctx(tmp_path))
    assert r2.stale is True and r2.runbook == "reimb_data_sync"


def test_market_share_expected_quarter():
    """규칙표: '90일 전 시점에 끝난 마지막 분기'. 2026-02-15 의 90일 전은 2025-11-17 → 그 전에 끝난 분기는 2025Q3
    (계획 초안의 2025Q4 는 Q4 가 아직 진행 중인 시점이라 규칙과 모순 — 규칙을 따른다)."""
    assert fr.expected_quarter(date(2026, 9, 30)) == "2026Q2"
    assert fr.expected_quarter(date(2026, 2, 15)) == "2025Q3"
    assert fr.expected_quarter(date(2026, 4, 1)) == "2025Q4"       # 경계: 90일 전 = 2026-01-01
    assert fr.expected_quarter(date(2026, 7, 1)) == "2026Q1"


def test_market_share_reads_quarterly_table_not_upload_log(tmp_path):
    """market_share_upload_log 에는 quarter 컬럼이 없다(quarters_json) — 판정은 market_share_quarterly 로."""
    _seed(tmp_path / "d.db", """
        CREATE TABLE market_share_upload_log (id INTEGER, uploaded_at TEXT, quarters_json TEXT);
        CREATE TABLE market_share_quarterly (product_id TEXT, quarter TEXT, values_lc REAL);
        INSERT INTO market_share_quarterly VALUES ('p','2025Q4',1.0), ('p','2025Q3',1.0);
    """)
    r = fr.check_market_share(_ctx(tmp_path))
    assert r.stale is True and r.observed == "2025Q4" and r.deadline == "2026Q2" and r.runbook is None
    assert r.kind == "improvement"            # P1.6: admin 엑셀 업로드는 사람 몫 — 월 키 ops_incident 가 아니라 도메인당 1행


def test_human_upload_domains_are_improvement_not_ops_budget(tmp_path):
    """예산 리허설(P1.6): 런북 없는 수동 업로드 도메인(아날로그 PDF·마켓셰어 엑셀)은 improvement — 매월 1일 ops_incident burst 에서 뺀다."""
    _seed(tmp_path / "d.db", "CREATE TABLE analog_reports (session_date TEXT); INSERT INTO analog_reports VALUES ('2026-01-15');")
    r = fr.check_analog_reports(_ctx(tmp_path))
    assert r.stale is True and r.kind == "improvement" and r.runbook is None and "담당: 사람" in r.evidence
    ids = fr.record_freshness_problems([r], today=TODAY, db_path=tmp_path / "a.db")
    assert list_tasks(db_path=tmp_path / "a.db")[0]["source_ref"] == "analog_reports:backlog"
    assert fr.record_freshness_problems([r], today=date(2026, 10, 1), db_path=tmp_path / "a.db") == ids    # 새 달에도 같은 행


def test_policy_intelligence_ingest_and_analysis(tmp_path):
    root = tmp_path / "pi"
    (root / "manifests").mkdir(parents=True)
    (root / "analysis").mkdir()
    (root / "manifests" / "latest_ingest_status.json").write_text('{"ran_at": "2026-06-30T03:56:14+00:00"}')
    r = fr.check_policy_intelligence(_ctx(tmp_path, root=root))
    assert r.stale is True and r.runbook is None                      # ingest 자체가 오래됨 → 사람
    (root / "manifests" / "latest_ingest_status.json").write_text('{"ran_at": "2026-09-25T03:00:00+00:00"}')
    r2 = fr.check_policy_intelligence(_ctx(tmp_path, root=root))
    assert r2.stale is True and r2.runbook == "policy_intel_analysis_sync"   # ingest 는 신선, analysis 없음
    (root / "analysis" / "analysis_manifest.json").write_text("{}")
    os.utime(root / "analysis" / "analysis_manifest.json", (time.time(), time.time()))
    r3 = fr.check_policy_intelligence(_ctx(tmp_path, root=root, today=date.today()))
    assert r3.stale is False


def test_run_all_survives_missing_tables(tmp_path):
    results = fr.run_all(_ctx(tmp_path))
    assert len(results) == len(fr.DOMAIN_CHECKS) >= 16
    assert [r.domain for r in results] == list(fr.DOMAIN_CHECKS)
    assert all(r.stale is False for r in results if r.evidence.startswith("판정 불가"))
    undeterminable = {r.domain for r in results if r.evidence.startswith("판정 불가")}
    assert "domestic_price" in undeterminable and "market_share" in undeterminable


def test_missing_column_is_undeterminable_not_a_task(tmp_path):
    """테이블은 있는데 컬럼이 없는 스키마 함정(감사가 틀린 사례) — 판정 불가로 강등, 과제 0건."""
    _seed(tmp_path / "d.db", "CREATE TABLE competitor_news (id INTEGER, kind TEXT, pub_date TEXT);")
    results = fr.run_all(_ctx(tmp_path))
    news = next(r for r in results if r.domain == "news_archive")
    assert news.stale is False and news.evidence.startswith("판정 불가") and "fetched_at" in news.evidence
    assert fr.record_freshness_problems([news], today=TODAY, db_path=tmp_path / "a.db") == []


def test_record_freshness_problems_dedups_per_month_and_blocked(tmp_path):
    db = tmp_path / "a.db"
    stale = fr.Freshness("domestic_price", True, "2026.07.01", "2026.09.01", "오래됨", "domestic_price_catchup")
    fresh = fr.Freshness("news_archive", False, "2026-09-30", "2026-09-27", "신선", "news_daily_fetch")
    ids = fr.record_freshness_problems([stale, fresh], today=TODAY, db_path=db)
    assert len(ids) == 1
    t = list_tasks(db_path=db)[0]
    assert t["source"] == "domain_freshness" and t["source_ref"] == "domestic_price:2026-09" and "런북: domestic_price_catchup" in t["detail"]
    update_task(ids[0], db_path=db, status="blocked", actor="dispatcher", resolution="x")
    assert fr.record_freshness_problems([stale], today=TODAY, db_path=db) == ids           # blocked 도 억제
    assert fr.record_freshness_problems([stale], today=date(2026, 10, 1), db_path=db) != ids  # 다음 달은 새 과제


def test_write_domain_freshness_upserts(tmp_path):
    db = tmp_path / "a.db"
    r = fr.Freshness("news_archive", False, "2026-09-30", "2026-09-27", "신선", "news_daily_fetch")
    fr.write_domain_freshness([r], db_path=db)
    fr.write_domain_freshness([r], db_path=db)
    rows = sqlite3.connect(db).execute("SELECT domain, stale, observed FROM domain_freshness").fetchall()
    assert rows == [("news_archive", 0, "2026-09-30")]


def _stale_fixture(tmp_path) -> Path:
    """stale 2 도메인(국내약가·위원회 결과) + fresh 1(NHIS) + 나머지 판정 불가."""
    db = tmp_path / "data.db"
    _seed(db, """
        CREATE TABLE drug_prices (apply_date TEXT); INSERT INTO drug_prices VALUES ('2026.07.01');
        CREATE TABLE amjilsim_sessions (session_id INTEGER, committee_type TEXT, status TEXT, session_date TEXT);
        INSERT INTO amjilsim_sessions VALUES (1,'YAKPYUNGWI','SCHEDULED','2026-07-02'), (2,'AMJILSIM','SCHEDULED','2026-10-15'),
                                             (3,'YAKPYUNGWI','SCHEDULED','2026-10-20');
        CREATE TABLE nhis_negotiations (fetched_at TEXT); INSERT INTO nhis_negotiations VALUES ('2026-09-29T01:00:00');
    """)
    return db


def test_run_and_record_second_run_adds_zero_tasks(tmp_path):
    """오탐 억제의 핵심 증거: 같은 달 두 번째 실행은 과제를 하나도 더 만들지 않는다(tasks_new=0)."""
    data = _stale_fixture(tmp_path)
    ledger = tmp_path / "ledger.db"
    res1 = fr.run_and_record(db_path=ledger, data_db_path=data, base_dir=tmp_path, today=TODAY)
    assert res1["ok"] is True
    assert res1["counts"]["checked"] == len(fr.DOMAIN_CHECKS)
    assert {"domestic_price", "committee_results"} <= set(res1["stale_domains"])
    assert "nhis_negotiations" not in res1["stale_domains"]
    assert res1["counts"]["tasks_new"] == res1["counts"]["tasks"] == res1["counts"]["stale"] > 0
    res2 = fr.run_and_record(db_path=ledger, data_db_path=data, base_dir=tmp_path, today=TODAY)
    assert res2["counts"]["tasks_new"] == 0 and res2["counts"]["tasks"] == res1["counts"]["tasks"]
    tasks = list_tasks(db_path=ledger)
    assert len(tasks) == res1["counts"]["tasks"] and all(t["source"] == "domain_freshness" for t in tasks)
    # 판정 결과는 원장 DB 의 domain_freshness 에 도메인당 1행, 데이터 DB 에는 아무것도 쓰지 않는다
    assert sqlite3.connect(ledger).execute("SELECT COUNT(*) FROM domain_freshness").fetchone()[0] == len(fr.DOMAIN_CHECKS)
    assert sqlite3.connect(data).execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name IN ('domain_freshness','agent_task')").fetchone()[0] == 0


def test_run_and_record_accepts_readonly_uri(tmp_path):
    """로컬 실측용: 데이터 DB 를 file:...?mode=ro URI 로 열어도 판정이 된다(실제 DB 에 쓰기 0)."""
    data = _stale_fixture(tmp_path)
    ledger = tmp_path / "ledger.db"
    uri = f"file:{data}?mode=ro"
    res = fr.run_and_record(db_path=ledger, data_db_path=uri, base_dir=tmp_path, today=TODAY)
    assert "domestic_price" in res["stale_domains"] and res["counts"]["tasks_new"] > 0


def test_scheduler_registers_domain_freshness_job(tmp_path, monkeypatch):
    """07:45 일일 잡 — stale 도메인은 과제이지 잡 실패가 아니다(ok=True, scheduler_job 과제 0건, 실행 기록 1행)."""
    import scheduler as sch
    from agents.agent_tasks.signals import JOB_RUNBOOK
    ledger = tmp_path / "ledger.db"
    monkeypatch.setenv("AGENT_TASK_DB", str(ledger))
    specs = {s.id: s for s in sch.build_job_specs({"day": "1-7", "hour": 9, "minute": 0})}
    spec = specs["domain_freshness"]
    assert spec.fn.__job_id__ == "domain_freshness" and "domain_freshness" in JOB_RUNBOOK
    fields = {f.name: str(f) for f in spec.trigger.fields}
    assert fields["hour"] == "7" and fields["minute"] == "45"
    res = sch.domain_freshness_job(data_db_path=_stale_fixture(tmp_path))
    assert res["ok"] is True and res["errors"] == []
    assert res["counts"]["checked"] == len(fr.DOMAIN_CHECKS) and res["counts"]["stale"] >= 2
    sources = {t["source"] for t in list_tasks(db_path=ledger)}
    assert sources == {"domain_freshness"}
    runs = sqlite3.connect(ledger).execute("SELECT job_id, ok FROM scheduler_job_run").fetchall()
    assert runs == [("domain_freshness", 1)]


def test_domain_freshness_api(tmp_path, monkeypatch):
    """/api/data/freshness?type=domain 은 domain_freshness 를 stale 우선으로 돌려주고, 기존 호출 형태는 그대로."""
    import api.server as s
    from agents.db import DrugPriceDB
    monkeypatch.setattr(s, "db", DrugPriceDB(tmp_path / "api.db"))
    c = s.app.test_client()
    assert c.get("/api/data/freshness?type=domain").get_json() == []          # 테이블 없음 → 빈 목록(500 아님)
    fr.write_domain_freshness([
        fr.Freshness("news_archive", False, "2026-09-30", "2026-09-27", "신선", "news_daily_fetch"),
        fr.Freshness("domestic_price", True, "2026.07.01", "2026.09.01", "오래됨", "domestic_price_catchup"),
    ], db_path=tmp_path / "api.db")
    rows = c.get("/api/data/freshness?type=domain").get_json()
    assert [r["domain"] for r in rows] == ["domestic_price", "news_archive"]
    assert rows[0]["stale"] == 1 and rows[0]["runbook"] == "domestic_price_catchup" and rows[0]["checked_at"]
    assert c.get("/api/data/freshness").get_json() == []                       # data_freshness 전체(빈 DB)
    assert c.get("/api/data/freshness?type=hta&key=x").get_json() == {"status": "not_found"}
