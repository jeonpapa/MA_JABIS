# 에이전트 팀 P1.5 — 플랫폼 전면 커버리지 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** P1 이 만든 원장·디스패처·런북 위에 **관측**을 얹는다 — 22개 스케줄러 잡 전부, 14개 데이터 도메인의 신선도, 프로세스 생존, 화면별 스모크, QualityGuard·RuleCompliance 산출물이 모두 `agent_task` 원장에 도착하게 하고, 항상 성공하는 placeholder 를 치운다. P2(요청 처리자)·P3(개선자)가 행동할 신호가 이 단계에서 생긴다.

**Architecture:** 신호는 세 층에서 나온다. ① **잡 층** — 모든 잡을 `guarded_job` 데코레이터로 감싸 `{ok, counts, errors}` 규약으로 통일하고, 예외·`errors`·`error`·`failed` 키·misfire 를 원장 과제로 바꾼다. ② **데이터 층** — `freshness.py` 가 잡 성공 여부와 무관하게 "데이터가 언제 것인가" 를 도메인별 규칙으로 판정한다(잡이 성공해도 폴백·빈 결과·수동 업로드 누락은 여기서만 잡힌다). ③ **프로세스 층** — 스케줄러 heartbeat 파일 + `/api/health` 503 + Fly HTTP check + 화면별 스모크. 세 층 모두 P1 의 `create_task(source, source_ref)` 중복 억제를 쓰되, **blocked 상태도 중복 억제에 포함**시켜 "런북 없음 → blocked" 로 끝난 과제가 매일 다시 생기지 않게 한다. 오탐 억제 원칙: **날짜 source_ref 는 하루 1건, 설정 문제(config)·신선도(월)·QG/RC(내용 키)는 상태가 닫힐 때까지 1건.**

**Tech Stack:** Python 3.12, sqlite3(WAL, 공용 `drug_prices.db`), Flask(`api/server.py`), APScheduler 3.11(`scheduler.py`, `apscheduler.events`), Fly.io(`fly.toml` http checks), pytest.

**Spec:** `docs/superpowers/specs/2026-09-30-agent-team-full-service-design.md` §1(목표 3가지 중 "운영: 잡 실패·데이터 공백을 스스로 감지"), §4(운영 감시자 = 헬스·잡 결과·데이터 신선도 점검), §5(원장), §7(관측과 보고). 선행: `docs/superpowers/plans/2026-09-30-agent-team-P1-ops-sentinel.md` (원장·신호·런북·디스패처 — 모두 배포됨, HEAD `d17fa552`).

---

## 작업 규칙

- Python: `/Users/kimjeong-ae/MA_AI_Dossier/.venv/bin/python`. 테스트는 저장소 루트에서 `.venv/bin/python -m pytest <경로> -q`.
- TDD: 실패 테스트 → 최소 구현 → 통과 → 커밋. 커밋 메시지 끝에 `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- 실제 외부 호출 금지(네트워크·Gmail·LLM·Playwright). `flyctl`·`git push`·배포 금지 — 배포는 문서 말미 "배포(사용자 확인 후)" 절차대로 **사용자만** 한다.
- 자격증명은 `config/.env` / fly secrets 만. 코드·테스트·문서에 값 금지.
- 신호 경계 관례(P1): 원장 기록 실패는 **호출부가 아니라 경계 함수 안에서** 삼키고 warning 만 남긴다(`scheduler._agent_record_*`, `agents/agent_tasks/jobs.py:_record`). 본 작업(잡·API)이 신호 때문에 죽으면 회귀다.
- 테스트는 실제 원장(`data/db/drug_prices.db`)에 쓰지 않는다 — Task 1 의 `tests/conftest.py` 가 `AGENT_TASK_DB` 를 임시 파일로 돌린다. 신호 함수는 항상 `db_path=` 를 명시해 부른다.

## 감사 확인 결과 (코드로 대조, `파일:줄`)

계획을 쓰기 전에 감사 8개 항목을 코드·로컬 DB로 다시 확인했다. **틀렸던 주장**은 굵게 표시했다.

| # | 감사 주장 | 확인 결과 |
|---|---|---|
| 1 | 21/22 잡이 실패를 삼킨다 | 맞다. `scheduler.py:1000-1261` 에 `add_job` 22회. 예외가 APScheduler 까지 가는 경로는 `scheduler.py:110-111` (`DashboardAgent(config, BASE_DIR).run()`, try 밖) 하나. `add_listener`/`EVENT_JOB_*` 0회(grep). `misfire_grace_time=86400` 은 `scheduler.py:1014-1015` 의 `domestic_price_pipeline` 만(나머지 기본 1초). 로컬 `logs/ma_ai_dossier.log` 에 `was missed by` **611건**. |
| 2 | 원장 신호 호출 두 곳뿐 | 맞다. `scheduler.py:99,106`(국내약가) · `:559`(메일링 헬스). `agents/agent_tasks/signals.py:16-20` 의 `JOB_RUNBOOK` 에 `daily_mailing_dispatch`/`daily_mailing_top_news` 가 있으나 그 잡(`scheduler.py:525-547`)은 신호를 부르지 않는다(죽은 매핑). `HEALTH_RUNBOOK`(`signals.py:21-25`)은 3코드만. 실제 문제 코드: `agents/daily_mailing/dispatch.py:50` `PROBLEM_STATUSES=("held","send_failed","error","expired")` + `:490 not_generated` + `:498 ready_to_send` + `:504 gmail_token_invalid`. |
| 3 | 반환값·폴백이 실패를 가린다 | 맞다. `reimb_data_sync`: `scheduler.py:436-441`(데이터 폴백 → `source=…(폴백:local)` 로 INFO), `:473-475`(보고서 폴백). **`policy_analysis_sync.py:14-15,21` 은 import 줄이다** — 실제 `error` 키 반환은 `agents/ingest/policy_analysis_sync.py:97-101`(토큰 없음 fail-closed) · `:104-105`(fetch 실패); 잡은 `scheduler.py:1167-1172` 에서 INFO/warning 만. `foreign_approval_sync`: `scheduler.py:146-147` `failed` 를 warning. `daily_mailing_dispatch`: `:530-531` `errors` 를 INFO. |
| 4 | 도메인 신선도 판정 없음 + stale 6개 | `data_freshness` 는 `hta` 35행, 최신 `2026-04-18`(`agents/db/schema.py:123-130`, `/api/data/freshness` `api/server.py:2106-2117`). 로컬 실측(2026-09-30): `drug_prices MAX(apply_date)=2026.07.01` ✓ · 환율 캐시 `keb_avg_rate_20230603_20260531.xlsx` ✓ · `reimbursement_xnational` 전부 `fetched_at=2026-04-29`, AU/PBAC 0행(JP 7·UK 30·US 7) ✓ · `analog_reports MAX(session_date)=2026-01-15` ✓ · **`market_share_upload_log` 에는 `quarter` 컬럼이 없다**(`quarters_json`) — 사실은 `market_share_quarterly MAX(quarter)=2025Q4` ✓ · 정책인텔 `manifests/latest_ingest_status.json ran_at=2026-06-30T03:56:14+00:00`, `analysis/` 비어 있음 ✓ — **매니페스트 부재 시 FileNotFoundError 는 `agents/policy_intelligence.py:145-146`**(`agents/ingest/` 아님) · `download_log` 최신 3행(id 395-397) `process_status='pending', record_count=0` ✓ — 원인은 `agents/domestic_catchup.py:232-234` 의 `log_download(..., status="success")` 가 `download_status` 만 쓰고 `process_status` 를 갱신하지 않기 때문. |
| 5 | 생존 확인 없음 | 맞다. `api/server.py:7309-7311`. `fly.toml` 에 `[[http_service.checks]]` 없음. `scripts/start_production.sh` 는 `wait -n` 으로 프로세스 종료만 감지. |
| 6 | placeholder 잡 4개 | 맞다. `scheduler.py:625`, `:652`, `:675`, `:691-694`. `amjilsim_sessions` SCHEDULED: AMJILSIM 5건(~2026-12-23), YAKPYUNGWI 6건(~**2026-12-03**). **추가 발견**: 지난 세션이 SCHEDULED 로 남아 있다(YAKPYUNGWI 2026-07-02 이후, AMJILSIM 2026-07-08 이후 — 결과 미반영). |
| 7 | QG/RC 산출물이 버려진다 | 맞다. `agents/quality_guard/agent.py:452-459` 반환, `deviations.py:12-14` 경로(`<repo>/quality_guard`, 컨테이너 경로), `:17-24` dedup 키. 최신 `quality_guard/review_2026-07-28.md` = 코드패턴 2 + 제안 11 = 13건. RC: `agents/rule_compliance/checks.py:21-26` `CheckResult`, `:571-591` 체크 14개, `compliance_2026-07-28.md` PASS 10/FAIL 2/SKIP 42. 프로덕션 skip `scheduler.py:159-161`, 메모리 경로 `agents/rule_compliance/agent.py:40-57`. **추가**: `quality_guard/` 는 git 추적(74 파일)이라 로컬 `git_backup_job` 이 매일 커밋한다. |
| 8 | 런북 후보는 코드에 있다 | 맞다. `ExchangeRateFetcher.refresh` `agents/exchange_rate.py:226` + `recompute_foreign_fx` `agents/db/foreign.py:278` · `sync_from_prices` `agents/foreign_approval/agent.py:154` + `list_coverage_gaps` `:122` · `reimbursement_sync.sync_all` `:100`(save 는 UPSERT — `agents/db/foreign.py:348-368`, 멱등) · `competitor_news_agent.crawl` `:231` · `gov_policy_news.crawl` `:77` · `promote_from_archive` `:355` · `nhis_negotiation_import.run` `:253` · `run_fresh_crawl` `:195` · `reimb_committee_import.run/load_payload` `:897/:873` · `sync_analysis` `:85`. CLI: `--fx-refresh-now`·`--fx-recompute-now`·`--approval-sync-now`·`--reimb-sync-now`·`--promote-trends-now`·`--nhis-sync-now`·`--fresh-signals-now`·`--amjilsim-daily-crawl-now`(위원회 데이터+정책 분석 sync) 있음. **뉴스 일일/주간 fetch 플래그는 없다** → Task 5 가 `--news-daily-now`/`--news-weekly-now` 추가. |

그 밖의 확인: `ForeignApprovalAgent.__init__`(`agent.py:40-47`)은 스크레이퍼 6개를 생성하므로 신선도 판정에서 직접 만들지 않는다(Task 3 이 `coverage_gaps(db_path)` 모듈 함수로 뺀다). `require_auth`(`api/auth.py:75-103`)는 토큰의 `role` 만 보고 users DB 를 조회하지 않으며 `JWT_SECRET` 은 `config/.env` → 환경변수 순(`api/auth.py:28-42`)이라 **같은 컨테이너의 스케줄러 프로세스가 스모크용 토큰을 직접 발급할 수 있다**(계정 불필요). `agent_task` 테이블은 로컬 DB 에 아직 없다(P1 은 프로덕션에서만 돌았다). `mail_subscription` 은 로컬에 0행.

## 파일 구조

| 파일 | 책임 | Task |
|---|---|---|
| `agents/agent_tasks/jobs.py` (신규) | 잡 결과 규약(`normalize_result`), `guarded_job` 데코레이터, `scheduler_job_run` 실행 기록, `resolve_db_path()` | T1 |
| `agents/agent_tasks/store.py` (수정) | `create_task(dedup_statuses=)`, `find_task()` | T1 |
| `agents/agent_tasks/signals.py` (수정) | `JOB_RUNBOOK` 22개 전부, `DEDUP_STATUSES`, `record_job_failure(error_class=)`, `record_job_missed`, `record_quality_review`, `record_compliance_results`, `record_known_debts` | T1·T5·T6·T7 |
| `agents/agent_tasks/freshness.py` (신규) | 14 도메인 신선도 판정 + `domain_freshness` 테이블 + 과제 생성 | T3 |
| `agents/agent_tasks/heartbeat.py` (신규) | 스케줄러 heartbeat 파일 쓰기/판정 | T4 |
| `agents/agent_tasks/runbooks.py` (수정) | 런북 11개 추가(동기/분리 실행) | T5 |
| `agents/foreign_approval/agent.py` (수정) | `coverage_gaps(db_path)` 모듈 함수로 분리 | T3 |
| `agents/quality_guard/deviations.py` (수정) | 출력 경로를 볼륨(`data/quality_guard`)으로, `QUALITY_GUARD_DIR` 오버라이드 | T6 |
| `agents/rule_compliance/agent.py` (수정) | 메모리 없는 모드(`require_memory=False`, DB 체크만) | T6 |
| `scheduler.py` (수정) | 잡 데코레이션·본문 전환, `JobSpec`/`build_job_specs`, 리스너, misfire, 신규 잡 3개, placeholder 4개 해제, CLI 플래그 | T1·T2·T3·T4·T5·T6·T7 |
| `api/server.py` (수정) | `/api/health` 확장(+503), `/api/data/freshness?type=domain` | T3·T4 |
| `fly.toml` (수정) | `[[http_service.checks]]` | T4 |
| `scripts/smoke.py` (신규) | 화면별 엔드포인트 스모크(21 화면 + 권한 게이트), CLI | T4 |
| `tests/conftest.py` (신규) | `AGENT_TASK_DB` 임시 DB autouse | T1 |
| `.gitignore` (수정) | `data/quality_guard/` | T6 |

### Task 경계와 `scheduler.py` 접촉 영역 (병렬 구현용)

| Task | `scheduler.py` 접촉 영역 | 다른 공유 파일 | 선행 |
|---|---|---|---|
| T1 | 전체 골격: import·경계 함수(`:45-66`)·잡 데코레이션·본문 전환(T2/T6/T7 소유 잡 제외)·`main()` 등록부(`:995-1270`) → `build_job_specs` | `signals.py`(전면), `store.py`, `tests/conftest.py` | 없음 |
| T2 | 잡 본문 4개만: `foreign_approval_sync_job`, `_reimb_committee_data_sync`/`_reimb_reports_sync`/`reimb_data_sync_job`, `policy_analysis_sync_job`, `daily_mailing_dispatch_job` | 없음 | T1 |
| T3 | `domain_freshness_job` 함수 1개 + `build_job_specs` 1행 + CLI 1개 | `signals.py` `JOB_RUNBOOK` 1행, `api/server.py:2106-2117`, `agents/foreign_approval/agent.py:122-153` | T1 |
| T4 | `scheduler_heartbeat_job`·`platform_smoke_job` 2개 + `build_job_specs` 2행 + CLI 1개 | `signals.py` `JOB_RUNBOOK` 2행, `api/server.py:7309-7311`, `fly.toml` | T1 |
| T5 | CLI 2개(`--news-daily-now`, `--news-weekly-now`) | `runbooks.py`(전면), `signals.py` `JOB_RUNBOOK` 값 10개 | T1 |
| T6 | 잡 본문 2개: `rule_compliance_audit_job`, `quality_guard_review_job` + 경계 함수 2개 | `signals.py` 함수 2개 추가, QG/RC 모듈, `.gitignore` | T1 |
| T7 | `build_job_specs` 4행 삭제 + `main()` 에 `_agent_record_known_debts()` 1행 | `signals.py` `JOB_RUNBOOK` 4행 삭제 + `KNOWN_DEBTS` | T1 (T3·T4 뒤에 머지하면 충돌 0) |
| T8 | 없음 | 없음 | 전부 |

**병렬 묶음 제안:** T1 단독 → {T2, T3, T4, T5, T6} 동시(각각 worktree; `signals.py` 의 `JOB_RUNBOOK` 은 서로 다른 행을 만지므로 머지 시 줄 단위 충돌만 남고 의미 충돌 없음) → T7 → T8. 프로덕션에는 **한 번에** 배포한다(T3 의 런북 힌트가 T5 의 등록 이름을 가리킨다).

**첫 실행 과제 수 예측(프로덕션, 배포 다음날 09:00 기준):** T1/T2 1~3건(정책 분석 sync `auth required` 1 + 알려진 국내약가 9/1 실패 1 + 미확인 1) · T3 6건(±1) · T4 1건(정책인텔 overview 500) · T6 7건(RC FAIL 2 + QG 그룹 5) · T7 2건 → **약 17~19건, 이후 정상 상태 ≤3건/일**. 근거는 각 Task 의 완료 기준에 있다. 로컬에서 같은 검사를 돌리면 스케줄러가 7/28 이후 멈춰 있어 T3 가 11건이 된다 — 이것은 로컬 사실이지 오탐이 아니다.

---

### Task 1: 잡 결과 표준화 + 신호 래퍼 + 리스너 + misfire + `JOB_RUNBOOK` 22개

**Files:**
- Create: `agents/agent_tasks/jobs.py`, `tests/conftest.py`, `tests/test_agent_jobs.py`, `tests/test_scheduler_job_specs.py`
- Modify: `agents/agent_tasks/store.py:112-137` (`create_task`), `agents/agent_tasks/signals.py` (전면), `scheduler.py` (경계 함수 `:45-66`, 잡 정의부, `main()` `:995-1270`)
- Modify tests: `tests/test_agent_signals.py` (gmail_token_invalid ref 변경 1곳)

규약: **잡 함수는 dict 를 돌려주거나 예외를 던진다.** dict 의 `ok`(기본 True)·`counts`·`errors`·`error`·`failed`·`error_class`(`transient`|`config`) 를 래퍼가 해석한다. 기존 잡의 바깥 `try/except Exception: logger.exception` 은 **지운다**(래퍼가 같은 로그를 남기고 신호까지 만든다). 한 잡 안의 독립 블록(뉴스 잡의 경쟁사/정부 두 크롤)은 `errors` 리스트에 모아 반환한다.

- [ ] **Step 1: 테스트 격리 — `tests/conftest.py`**

```python
# tests/conftest.py
"""잡 래퍼·신호가 실제 원장(data/db/drug_prices.db)에 쓰지 않게 매 테스트마다 임시 DB 를 지정한다.

agents/agent_tasks/jobs.resolve_db_path() 와 scheduler 의 _agent_* 경계 함수가 이 환경변수를 읽는다.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_agent_task_db(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_TASK_DB", str(tmp_path / "agent_tasks.db"))
```

- [ ] **Step 2: 실패 테스트 — `tests/test_agent_jobs.py`**

```python
# tests/test_agent_jobs.py
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from agents.agent_tasks import jobs
from agents.agent_tasks.store import add_event, connect, list_events, list_tasks, update_task


def _db(monkeypatch, tmp_path):
    db = tmp_path / "a.db"
    monkeypatch.setenv("AGENT_TASK_DB", str(db))
    return db


def test_normalize_result_shapes():
    assert jobs.normalize_result(None) == {"ok": True, "counts": {}, "errors": [], "error_class": "transient"}
    r = jobs.normalize_result({"built": ["a"], "failed": [{"slug": "x", "reason": "ID 미확인"}]})
    assert r["ok"] is False and r["errors"] == ["x: ID 미확인"]
    r2 = jobs.normalize_result({"copied": 0, "skipped": 0, "rejected": 0,
                                "error": "auth required: POLICY_ANALYSIS_TOKEN missing", "error_class": "config"})
    assert r2["ok"] is False and r2["error_class"] == "config"
    assert r2["counts"] == {"copied": 0, "skipped": 0, "rejected": 0}
    r3 = jobs.normalize_result({"generated": [], "errors": [14], "skipped": 2})
    assert r3["errors"] == ["14"] and r3["counts"] == {"skipped": 2}
    r4 = jobs.normalize_result({"ok": False, "counts": {"n": 1}})
    assert r4["ok"] is False and r4["errors"] == []


def test_guarded_job_records_exception_as_failure(monkeypatch, tmp_path):
    db = _db(monkeypatch, tmp_path)

    @jobs.guarded_job("x_job")
    def boom():
        raise RuntimeError("HIRA timeout")

    res = boom()
    assert res["ok"] is False and res["errors"] == ["RuntimeError: HIRA timeout"]
    tasks = list_tasks(db_path=db)
    assert len(tasks) == 1 and tasks[0]["source_ref"].startswith("x_job:") and tasks[0]["source"] == "scheduler_job"
    runs = sqlite3.connect(db).execute("SELECT job_id, ok FROM scheduler_job_run").fetchall()
    assert runs == [("x_job", 0)]


def test_guarded_job_success_records_run_and_no_task(monkeypatch, tmp_path):
    db = _db(monkeypatch, tmp_path)

    @jobs.guarded_job("y_job")
    def fine():
        return {"counts": {"rows": 3}}

    res = fine()
    assert res == {"ok": True, "counts": {"rows": 3}, "errors": [], "error_class": "transient"}
    assert list_tasks(db_path=db) == []
    assert sqlite3.connect(db).execute("SELECT ok, counts FROM scheduler_job_run").fetchall() == [(1, '{"rows": 3}')]


def test_guarded_job_config_error_uses_stable_ref(monkeypatch, tmp_path):
    db = _db(monkeypatch, tmp_path)

    @jobs.guarded_job("z_job")
    def misconfigured():
        return {"error": "auth required", "error_class": "config"}

    misconfigured()
    misconfigured()
    tasks = list_tasks(db_path=db)
    assert [t["source_ref"] for t in tasks] == ["z_job:config"]
    assert "설정 문제" in tasks[0]["title"]


def test_guarded_job_exposes_job_id_and_skips_run_record_when_asked(monkeypatch, tmp_path):
    db = _db(monkeypatch, tmp_path)

    @jobs.guarded_job("hb", record_runs=False)
    def hb():
        return None

    assert hb.__job_id__ == "hb"
    hb()
    conn = connect(db)
    conn.executescript(jobs.JOB_RUN_SCHEMA)
    assert conn.execute("SELECT COUNT(*) FROM scheduler_job_run").fetchone()[0] == 0


def test_signal_failure_does_not_break_job(monkeypatch, tmp_path, caplog):
    _db(monkeypatch, tmp_path)

    def boom(*a, **kw):
        raise RuntimeError("DB 잠김")

    monkeypatch.setattr("agents.agent_tasks.signals.record_job_failure", boom)

    @jobs.guarded_job("w_job")
    def failing():
        return {"errors": ["x"]}

    with caplog.at_level("WARNING", logger="agents.agent_tasks.jobs"):
        res = failing()                       # 예외가 밖으로 나오면 실패
    assert res["ok"] is False
    assert any("DB 잠김" in r.getMessage() for r in caplog.records)


def test_blocked_task_suppresses_new_task_until_closed(monkeypatch, tmp_path):
    db = _db(monkeypatch, tmp_path)

    @jobs.guarded_job("v_job")
    def failing():
        return {"errors": ["같은 오류"]}

    first = failing()
    tid = list_tasks(db_path=db)[0]["id"]
    update_task(tid, db_path=db, status="blocked", actor="dispatcher", resolution="런북 없음")
    failing()
    assert len(list_tasks(db_path=db, statuses=("new", "blocked"))) == 1     # blocked 도 중복 억제
    update_task(tid, db_path=db, status="closed", actor="admin", resolution="처리")
    failing()
    assert len(list_tasks(db_path=db, statuses=("new",))) == 1               # 닫힌 뒤에는 새 과제


def test_recurrence_event_once_per_day(monkeypatch, tmp_path):
    db = _db(monkeypatch, tmp_path)

    @jobs.guarded_job("u_job")
    def failing():
        return {"errors": ["오류"]}

    failing()
    failing()
    tid = list_tasks(db_path=db)[0]["id"]
    assert [e["event"] for e in list_events(tid, db_path=db)] == ["created"]      # 같은 날 반복은 이벤트 없음
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    conn = connect(db)
    conn.execute("UPDATE agent_task_event SET created_at=? WHERE task_id=?", (yesterday, tid))
    conn.commit()
    failing()
    failing()
    assert [e["event"] for e in list_events(tid, db_path=db)] == ["created", "recurred"]


def test_recent_job_runs_returns_last_run_per_job(monkeypatch, tmp_path):
    db = _db(monkeypatch, tmp_path)
    started = datetime.now(timezone.utc)
    jobs.record_job_run("a", started, {"ok": False, "counts": {}, "errors": ["e"], "error_class": "transient"}, db_path=db)
    jobs.record_job_run("a", started, {"ok": True, "counts": {}, "errors": [], "error_class": "transient"}, db_path=db)
    jobs.record_job_run("b", started, {"ok": False, "counts": {}, "errors": ["e"], "error_class": "transient"}, db_path=db)
    rows = jobs.recent_job_runs(hours=24, db_path=db)
    assert {(r["job_id"], r["ok"]) for r in rows} == {("a", 1), ("b", 0)}
```

- [ ] **Step 3: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_agent_jobs.py -q`
Expected: FAIL — `ModuleNotFoundError: agents.agent_tasks.jobs`

- [ ] **Step 4: 구현 — `agents/agent_tasks/jobs.py`**

```python
# agents/agent_tasks/jobs.py
"""스케줄러 잡 결과 표준화 + 신호 경계 (P1.5 Task 1).

규약: 잡 함수는 **dict 를 돌려주거나 예외를 던진다**. dict 에서 해석하는 키:
  ok(bool, 기본 True) · counts(dict) · errors(list) · error(str) · failed(list[dict|str]) · error_class("transient"|"config")
`guarded_job(job_id)` 는 (1) 예외 → 실패 신호 + exception 로그, (2) errors/error/failed 가 비어 있지 않으면 실패 신호,
(3) scheduler_job_run 에 실행 1행. 신호·기록 실패는 여기서 삼킨다 — 잡을 죽이지 않는다 (P1 `_agent_record_*` 관례).
error_class="config" 는 자격증명·토큰 부재처럼 재실행으로 안 풀리는 실패 — source_ref 에 날짜 대신 'config' 를 써서
사람이 닫을 때까지 과제 1건만 유지한다.
"""
from __future__ import annotations

import functools
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from .store import DEFAULT_DB_PATH, connect

logger = logging.getLogger(__name__)

RETENTION_DAYS = 14
JOB_RUN_SCHEMA = """
CREATE TABLE IF NOT EXISTS scheduler_job_run (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    ok          INTEGER NOT NULL,
    counts      TEXT NOT NULL DEFAULT '{}',
    errors      TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_job_run_job ON scheduler_job_run(job_id, id);
"""


def resolve_db_path() -> Path:
    """원장 DB 경로. 테스트는 AGENT_TASK_DB 로 임시 파일을 지정한다(tests/conftest.py)."""
    return Path(os.environ.get("AGENT_TASK_DB") or DEFAULT_DB_PATH)


def _fmt_failed(item) -> str:
    if isinstance(item, dict):
        who = item.get("slug") or item.get("id") or item.get("subscription_id") or "?"
        why = item.get("reason") or item.get("error") or item.get("detail") or ""
        return f"{who}: {why}".rstrip(": ")
    return str(item)


def normalize_result(raw) -> dict:
    """잡 반환값 → {ok, counts, errors, error_class}. None 은 성공. dict 가 아니면 성공(문자열만 남김)."""
    if raw is None:
        return {"ok": True, "counts": {}, "errors": [], "error_class": "transient"}
    if not isinstance(raw, dict):
        return {"ok": True, "counts": {"result": str(raw)[:200]}, "errors": [], "error_class": "transient"}
    errors = [str(e)[:300] for e in (raw.get("errors") or [])]
    if raw.get("error"):
        errors.append(str(raw["error"])[:300])
    errors += [_fmt_failed(f)[:300] for f in (raw.get("failed") or [])]
    ok = bool(raw.get("ok", True)) and not errors
    counts = raw.get("counts")
    if not isinstance(counts, dict):
        counts = {k: v for k, v in raw.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    return {"ok": ok, "counts": counts, "errors": errors,
            "error_class": raw.get("error_class") or "transient"}


def record_job_run(job_id: str, started: datetime, res: dict, *, db_path: str | Path | None = None) -> None:
    path = Path(db_path) if db_path else resolve_db_path()
    conn = connect(path)
    try:
        conn.executescript(JOB_RUN_SCHEMA)
        now = datetime.now(timezone.utc)
        conn.execute(
            "INSERT INTO scheduler_job_run (job_id, started_at, finished_at, ok, counts, errors) VALUES (?,?,?,?,?,?)",
            (job_id, started.isoformat(), now.isoformat(), 1 if res["ok"] else 0,
             json.dumps(res["counts"], ensure_ascii=False, default=str),
             json.dumps(res["errors"], ensure_ascii=False)))
        conn.execute("DELETE FROM scheduler_job_run WHERE started_at < ?",
                     ((now - timedelta(days=RETENTION_DAYS)).isoformat(),))
        conn.commit()
    finally:
        conn.close()


def recent_job_runs(*, hours: int = 24, db_path: str | Path | None = None) -> list[dict]:
    """job_id 별 마지막 실행(창 안). /api/health 의 jobs_failed_24h 와 신선도 판정기가 쓴다."""
    path = Path(db_path) if db_path else resolve_db_path()
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    conn = connect(path)
    try:
        conn.executescript(JOB_RUN_SCHEMA)
        rows = conn.execute(
            "SELECT r.job_id, r.started_at, r.finished_at, r.ok, r.counts, r.errors FROM scheduler_job_run r "
            "JOIN (SELECT job_id, MAX(id) AS mid FROM scheduler_job_run WHERE started_at >= ? GROUP BY job_id) m "
            "ON r.id = m.mid ORDER BY r.job_id", (since,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _record(job_id: str, started: datetime, res: dict, *, record_runs: bool) -> None:
    path = resolve_db_path()
    if record_runs:
        try:
            record_job_run(job_id, started, res, db_path=path)
        except Exception as e:
            logger.warning("[job:%s] 실행 기록 실패: %s", job_id, e)
    if res["ok"]:
        return
    logger.error("[job:%s] 실패 — %s", job_id, "; ".join(res["errors"])[:500])
    try:
        from .signals import record_job_failure
        record_job_failure(job_id, "; ".join(res["errors"])[:300], error_class=res["error_class"], db_path=path)
    except Exception as e:
        logger.warning("[job:%s] 실패 신호 기록 실패: %s", job_id, e)


def guarded_job(job_id: str, *, record_runs: bool = True):
    """스케줄러 잡 데코레이터. 반환값을 표준화하고 실패를 원장에 남긴다. 예외는 밖으로 나가지 않는다.

    record_runs=False 는 heartbeat 처럼 분 단위로 도는 잡용 — 실행 기록 테이블을 채우지 않는다.
    """
    def deco(fn: Callable):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            started = datetime.now(timezone.utc)
            try:
                res = normalize_result(fn(*args, **kwargs))
            except Exception as exc:
                logger.exception("[job:%s] 예외로 실패", job_id)
                res = {"ok": False, "counts": {}, "errors": [f"{type(exc).__name__}: {exc}"[:300]],
                       "error_class": "transient"}
            _record(job_id, started, res, record_runs=record_runs)
            return res
        wrapper.__job_id__ = job_id  # type: ignore[attr-defined]
        return wrapper
    return deco
```

- [ ] **Step 5: `store.py` — 중복 억제 상태 지정 + `find_task`**

`agents/agent_tasks/store.py:112-137` 의 `create_task` 시그니처와 조회를 바꾸고 `find_task` 를 추가한다.

```python
def create_task(*, kind: str, source: str, title: str, detail: str = "", priority: int = 3,
                source_ref: str | None = None, actor: str = "system",
                dedup_statuses: tuple[str, ...] = OPEN_STATUSES,
                db_path: str | Path = DEFAULT_DB_PATH) -> int:
    """과제 생성. 같은 (source, source_ref) 가 dedup_statuses 중 하나로 남아 있으면 그 과제 id 를 그대로 돌려준다.

    기본은 열린 상태만. 신호(signals.py)는 blocked 도 포함한다 — '런북 없음 → blocked' 로 끝난 과제가 매일 다시
    생기지 않게. 닫히거나(closed) 반려(rejected)되면 다시 만든다.
    """
    if kind not in KINDS:
        raise AgentTaskError(f"알 수 없는 kind: {kind}")
    conn = connect(db_path)
    try:
        if source_ref:
            placeholders = ",".join("?" for _ in dedup_statuses)
            row = conn.execute(
                f"SELECT id FROM agent_task WHERE source=? AND source_ref=? AND status IN ({placeholders})"
                " ORDER BY id LIMIT 1", (source, source_ref, *dedup_statuses)).fetchone()
            if row:
                return int(row["id"])
        # (이하 기존 INSERT + add_event 그대로)
```

`get_task` 아래에 추가:

```python
def find_task(source: str, source_ref: str, *, db_path: str | Path = DEFAULT_DB_PATH) -> dict | None:
    """상태와 무관하게 같은 (source, source_ref) 의 최신 과제. 한 번만 만들 과제(알려진 부채)의 존재 확인용."""
    conn = connect(db_path)
    try:
        row = conn.execute("SELECT * FROM agent_task WHERE source=? AND source_ref=? ORDER BY id DESC LIMIT 1",
                           (source, source_ref)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
```

- [ ] **Step 6: `signals.py` 전면 교체**

```python
# agents/agent_tasks/signals.py
"""운영 신호 → 작업 원장 과제 (설계 §5, P1.5 Task 1).

중복 억제 규칙(오탐 억제의 핵심):
  - 잡 실패(transient): source_ref = "{job_id}:{YYYY-MM-DD}" — 하루 1건. 10분 잡이 144번 실패해도 1건 + recurred 이벤트 1건/일.
  - 잡 실패(config):    source_ref = "{job_id}:config" — 사람이 닫을 때까지 1건 (토큰·키 부재는 재실행으로 안 풀린다).
  - 놓친 실행(misfire):  source_ref = "scheduler_missed:{날짜}" — 하루 1건, 놓친 잡은 이벤트로 누적.
  - blocked 도 중복 억제에 포함(DEDUP_STATUSES) — '런북 없음 → blocked' 가 매일 새 과제를 만들지 않게.
어떤 런북으로 고칠 수 있는지 detail 에 '런북: <이름>' 으로 적는다 — 디스패처(scripts/agent_dispatcher.py:pick_runbook)가 이 힌트를 읽는다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from .store import DEFAULT_DB_PATH, OPEN_STATUSES, add_event, create_task, list_events

KST = timezone(timedelta(hours=9))
DEDUP_STATUSES = OPEN_STATUSES + ("blocked",)

# 잡 id → 런북 (None = 런북 없음, 사람 확인). 키 집합은 scheduler.build_job_specs() 의 id 전체와 같아야 한다
# (tests/test_scheduler_job_specs.py 가 대조). Task 5 가 None 을 등록된 런북 이름으로 바꾼다.
JOB_RUNBOOK: dict[str, str | None] = {
    "domestic_price_pipeline": "domestic_price_catchup",
    "git_auto_backup": None,
    "foreign_approval_sync": None,
    "rule_compliance_audit": None,
    "quality_guard_review": None,
    "competitor_news_weekly": None,
    "competitor_news_daily": None,
    "competitor_trend_promote": None,
    "access_insight_fresh_signals": None,
    "reimbursement_xnational_sync": None,
    "exchange_rate_refresh": None,
    "reimb_data_sync": None,
    "policy_intel_analysis_sync": None,
    "daily_mailing_dispatch": "daily_mailing_dispatch",
    "daily_mailing_top_news": "daily_mailing_top_news",
    "daily_mailing_health": None,
    "nhis_negotiation_sync": None,
    "home_brand_related_refresh": None,
    "amjilsim_d_minus_2_reporter": None,
    "amjilsim_d_plus_1_reporter": None,
    "amjilsim_monthly_trend": None,
    "hira_schedule_fetcher_annual": None,
}
# Daily Mailing 헬스 문제 코드 전부(agents/daily_mailing/dispatch.py:50,490,498,504). None 은 런북으로 안 풀린다:
# held = 재시도 3회 소진(사람 승인/원인 확인), error = 생성 예외(원인 확인), expired = 승인 기한 경과(사람), gmail_token_invalid = 토큰 재발급(사람).
HEALTH_RUNBOOK: dict[str, str | None] = {
    "not_generated": "daily_mailing_dispatch",
    "send_failed": "daily_mailing_dispatch",
    "ready_to_send": "daily_mailing_dispatch",
    "held": None,
    "error": None,
    "expired": None,
    "gmail_token_invalid": None,
}
HIGH_PRIORITY_PROBLEMS = {"gmail_token_invalid", "error"}
# 날짜 없이 1건만 유지하는 헬스 문제(설정 문제) — 매일 새 과제가 아니라 사람이 닫을 때까지 하나
CONFIG_PROBLEMS = {"gmail_token_invalid"}


def _today() -> str:
    return datetime.now(KST).date().isoformat()


def _kst_date(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone(KST).date().isoformat()


def runbook_line(name: str | None) -> str:
    return f"런북: {name}" if name else "런북 없음 — 사람 확인 필요"


_runbook_line = runbook_line   # P1 호환


def _note_recurrence(task_id: int, error: str, today: str, *, db_path: str | Path) -> None:
    """이미 있던 과제에 오늘 처음 다시 났을 때만 recurred 이벤트 1건 (하루 1건 상한)."""
    for e in reversed(list_events(task_id, db_path=db_path)):
        if e["event"] in ("created", "recurred") and _kst_date(e["created_at"]) == today:
            return
    add_event(task_id, actor="scheduler", event="recurred", detail=str(error)[:500], db_path=db_path)


def record_job_failure(job_id: str, error: str, *, error_class: str = "transient",
                       db_path: str | Path = DEFAULT_DB_PATH) -> int:
    runbook = JOB_RUNBOOK.get(job_id)
    today = _today()
    ref = f"{job_id}:config" if error_class == "config" else f"{job_id}:{today}"
    title = f"스케줄러 잡 실패 — {job_id}" + (" (설정 문제)" if error_class == "config" else "")
    tid = create_task(
        kind="ops_incident", source="scheduler_job", source_ref=ref, title=title,
        detail=f"{error}\n{runbook_line(runbook)}", priority=2, actor="scheduler",
        dedup_statuses=DEDUP_STATUSES, db_path=db_path)
    _note_recurrence(tid, error, today, db_path=db_path)
    return tid


def record_job_missed(job_id: str, scheduled_run_time, *, db_path: str | Path = DEFAULT_DB_PATH) -> int:
    """APScheduler EVENT_JOB_MISSED — misfire grace 를 넘겨 폐기된 실행. 하루 1건, 놓친 잡은 이벤트로 누적."""
    today = _today()
    tid = create_task(
        kind="ops_incident", source="scheduler_missed", source_ref=f"scheduler_missed:{today}",
        title="스케줄러가 놓친 실행 (misfire grace 초과)",
        detail="예정 시각에 스케줄러가 살아 있지 않았다(재기동·정지). 놓친 잡은 이벤트 목록 참조.\n"
               + runbook_line(None) + " — 재기동 원인 확인 후 필요한 잡만 `python scheduler.py --<job>-now`",
        priority=3, actor="scheduler", dedup_statuses=DEDUP_STATUSES, db_path=db_path)
    add_event(tid, actor="scheduler", event="missed", detail=f"{job_id} @ {scheduled_run_time}", db_path=db_path)
    return tid


def record_health_problems(report: dict, *, db_path: str | Path = DEFAULT_DB_PATH) -> list[int]:
    run_date = report.get("run_date") or _today()
    ids: list[int] = []
    for problem in report.get("problems") or []:
        code = str(problem.get("problem") or "unknown")
        sub_id = problem.get("subscription_id")
        ref_tail = f"{sub_id}:{code}" if sub_id is not None else code
        ref = f"daily_mailing:{ref_tail}" if code in CONFIG_PROBLEMS else f"daily_mailing:{run_date}:{ref_tail}"
        runbook = HEALTH_RUNBOOK.get(code)
        detail = [f"구독: {problem.get('name') or '-'} ({sub_id})", f"문제: {code}"]
        if problem.get("detail"):
            detail.append(f"상세: {problem['detail']}")
        detail.append(runbook_line(runbook))
        ids.append(create_task(
            kind="ops_incident", source="daily_mailing_health", source_ref=ref,
            title=f"Daily Mailing {code} — 구독 {sub_id if sub_id is not None else '-'}",
            detail="\n".join(detail),
            priority=1 if code in HIGH_PRIORITY_PROBLEMS else 2,
            actor="daily_mailing_health", dedup_statuses=DEDUP_STATUSES, db_path=db_path))
    return ids
```

`tests/test_agent_signals.py::test_health_problems_create_one_task_per_subscription` 의 마지막 두 줄을 바꾼다:

```python
    gmail = tasks["daily_mailing:gmail_token_invalid"]          # 설정 문제 — 날짜 없는 ref
    assert gmail["priority"] == 1 and "런북 없음" in gmail["detail"]
```

`tests/test_agent_signals.py` 끝에 추가:

```python
def test_missed_runs_collapse_into_one_task_per_day(tmp_path):
    from agents.agent_tasks.signals import record_job_missed
    from agents.agent_tasks.store import list_events
    db = tmp_path / "a.db"
    a = record_job_missed("competitor_news_daily", "2026-09-30 06:30:00+09:00", db_path=db)
    b = record_job_missed("competitor_trend_promote", "2026-09-30 07:00:00+09:00", db_path=db)
    assert a == b
    assert [e["event"] for e in list_events(a, db_path=db)] == ["created", "missed", "missed"]


def test_job_runbook_covers_none_for_unmapped_jobs():
    from agents.agent_tasks.signals import JOB_RUNBOOK
    assert JOB_RUNBOOK["reimb_data_sync"] is None and JOB_RUNBOOK["domestic_price_pipeline"] == "domestic_price_catchup"
```

- [ ] **Step 7: 통과 확인 (jobs·signals)**

Run: `.venv/bin/python -m pytest tests/test_agent_jobs.py tests/test_agent_signals.py tests/test_agent_task_store.py -q`
Expected: 모두 PASS (신규 9 + 기존).

- [ ] **Step 8: 실패 테스트 — `tests/test_scheduler_job_specs.py`**

```python
# tests/test_scheduler_job_specs.py
"""스케줄러 등록부가 데이터(JobSpec)로 바뀌었고, 모든 잡이 guarded 이며 misfire 정책과 리스너가 걸려 있는지."""
from __future__ import annotations

import sys
from datetime import datetime, timezone

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED

import scheduler as sch
from agents.agent_tasks.signals import JOB_RUNBOOK
from agents.agent_tasks.store import list_events, list_tasks

SCHED_CFG = {"day": "1-7", "hour": 9, "minute": 0}


def test_every_job_is_guarded_with_matching_id():
    for spec in sch.build_job_specs(SCHED_CFG):
        assert getattr(spec.fn, "__job_id__", None) == spec.id, spec.id


def test_job_ids_match_runbook_map_exactly():
    ids = {s.id for s in sch.build_job_specs(SCHED_CFG)}
    assert ids == set(JOB_RUNBOOK)


def test_misfire_policy_is_explicit_for_every_job():
    specs = {s.id: s for s in sch.build_job_specs(SCHED_CFG)}
    assert specs["domestic_price_pipeline"].misfire_grace_time == 86400          # 기존 정책 유지
    assert specs["daily_mailing_dispatch"].misfire_grace_time == sch.INTERVAL_GRACE
    assert specs["reimbursement_xnational_sync"].misfire_grace_time == sch.PERIODIC_GRACE
    for s in specs.values():
        assert s.coalesce is True and s.misfire_grace_time >= 30 and s.max_instances == 1


def test_main_registers_listener_and_all_specs(monkeypatch):
    captured = {}

    class CapturingScheduler(sch.BlockingScheduler):
        def add_listener(self, callback, mask=None):
            captured["mask"] = mask
            captured["callback"] = callback
            return super().add_listener(callback, mask)

        def start(self, *args, **kwargs):
            captured["ids"] = {job.id for job in self.get_jobs()}
            raise KeyboardInterrupt

    monkeypatch.setattr(sch, "BlockingScheduler", CapturingScheduler)
    monkeypatch.setattr(sys, "argv", ["scheduler.py"])
    sch.main()
    assert captured["callback"] is sch._on_job_event
    assert captured["mask"] & EVENT_JOB_ERROR and captured["mask"] & EVENT_JOB_MISSED
    assert captured["ids"] == {s.id for s in sch.build_job_specs(SCHED_CFG)}


class _Event:
    def __init__(self, code, job_id, exception=None, scheduled_run_time=None):
        self.code, self.job_id, self.exception = code, job_id, exception
        self.scheduled_run_time = scheduled_run_time


def test_missed_event_records_one_task_with_events(tmp_path, monkeypatch):
    db = tmp_path / "a.db"
    monkeypatch.setenv("AGENT_TASK_DB", str(db))
    now = datetime.now(timezone.utc)
    sch._on_job_event(_Event(EVENT_JOB_MISSED, "competitor_news_daily", scheduled_run_time=now))
    sch._on_job_event(_Event(EVENT_JOB_MISSED, "competitor_trend_promote", scheduled_run_time=now))
    tasks = list_tasks(db_path=db)
    assert len(tasks) == 1 and tasks[0]["source"] == "scheduler_missed"
    assert [e["event"] for e in list_events(tasks[0]["id"], db_path=db)] == ["created", "missed", "missed"]


def test_error_event_records_failure_without_double_task(tmp_path, monkeypatch):
    db = tmp_path / "a.db"
    monkeypatch.setenv("AGENT_TASK_DB", str(db))
    sch._on_job_event(_Event(EVENT_JOB_ERROR, "nhis_negotiation_sync", exception=RuntimeError("boom")))
    sch._on_job_event(_Event(EVENT_JOB_ERROR, "nhis_negotiation_sync", exception=RuntimeError("boom again")))
    tasks = list_tasks(db_path=db)
    assert len(tasks) == 1 and tasks[0]["source_ref"].startswith("nhis_negotiation_sync:")
    assert "RuntimeError: boom" in tasks[0]["detail"]


def test_listener_swallows_signal_errors(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("DB 잠김")
    monkeypatch.setattr("agents.agent_tasks.signals.record_job_missed", boom)
    sch._on_job_event(_Event(EVENT_JOB_MISSED, "x", scheduled_run_time="t"))   # 예외가 밖으로 나오면 실패


def test_converted_jobs_return_standard_dict_and_propagate_errors(monkeypatch, tmp_path):
    """뉴스 매일 잡: 두 크롤 중 하나가 죽어도 다른 하나는 돌고, 실패는 errors 로 반환된다."""
    db = tmp_path / "a.db"
    monkeypatch.setenv("AGENT_TASK_DB", str(db))
    import types
    cn = types.ModuleType("agents.competitor_news_agent")
    cn.DEFAULT_LOOKBACK_DAYS = 183
    cn.crawl = lambda **kw: {"total_stored": 4}
    gpn = types.ModuleType("agents.gov_policy_news")
    gpn.DEFAULT_LOOKBACK_DAYS = 31

    def gov_boom(**kw):
        raise RuntimeError("Naver 5xx")
    gpn.crawl = gov_boom
    monkeypatch.setitem(sys.modules, "agents.competitor_news_agent", cn)
    monkeypatch.setitem(sys.modules, "agents.gov_policy_news", gpn)
    res = sch.competitor_news_daily_job()
    assert res["ok"] is False and res["counts"] == {"competitor": 4} and res["errors"] == ["gov_policy: RuntimeError: Naver 5xx"]
    assert list_tasks(db_path=db)[0]["source_ref"].startswith("competitor_news_daily:")


def test_naver_key_missing_is_config_class(monkeypatch, tmp_path):
    db = tmp_path / "a.db"
    monkeypatch.setenv("AGENT_TASK_DB", str(db))
    import types
    cn = types.ModuleType("agents.competitor_news_agent")
    cn.DEFAULT_LOOKBACK_DAYS = 183
    cn.crawl = lambda **kw: {"error": "Naver API 키 미설정", "results": []}
    gpn = types.ModuleType("agents.gov_policy_news")
    gpn.DEFAULT_LOOKBACK_DAYS = 31
    gpn.crawl = lambda **kw: {"total_stored": 0}
    monkeypatch.setitem(sys.modules, "agents.competitor_news_agent", cn)
    monkeypatch.setitem(sys.modules, "agents.gov_policy_news", gpn)
    res = sch.competitor_news_daily_job()
    assert res["ok"] is False and res["error_class"] == "config"
    assert list_tasks(db_path=db)[0]["source_ref"] == "competitor_news_daily:config"
```

- [ ] **Step 9: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_scheduler_job_specs.py -q`
Expected: FAIL — `AttributeError: module 'scheduler' has no attribute 'build_job_specs'`

- [ ] **Step 10: `scheduler.py` — 골격 교체**

(a) import 와 경계 함수(`scheduler.py:20-66`) — 다음을 추가/교체한다.

```python
from dataclasses import dataclass
from typing import Callable

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from agents.agent_tasks.jobs import guarded_job, resolve_db_path
# ... 기존 import 유지 ...

# misfire 정책(초). 재기동이 잦아 기본 1초로는 놓친 실행이 조용히 폐기됐다(로컬 로그 'was missed by' 611건).
DAILY_GRACE = 6 * 3600        # 일간 cron: 6시간 안에 재기동하면 1회 실행(coalesce)
PERIODIC_GRACE = 24 * 3600    # 주간·월간·분기: 하루 안에 재기동하면 1회 실행
INTERVAL_GRACE = 300          # 10분 interval: 다음 tick 이 곧 오므로 5분


# 신호 경계: 원장 기록 실패(DB 잠김 등)는 여기서 삼키고 warning 만 남긴다 — 호출부는 보호막 없이 부른다.
def _agent_record_health_problems(report: dict) -> None:
    try:
        from agents.agent_tasks.signals import record_health_problems
        record_health_problems(report, db_path=resolve_db_path())
    except Exception as signal_err:
        logger.warning("daily mailing health 신호 기록 실패: %s", signal_err)


def _agent_record_job_failure(job_id: str, error: str) -> None:
    try:
        from agents.agent_tasks.signals import record_job_failure
        record_job_failure(job_id, error, db_path=resolve_db_path())
    except Exception as signal_err:
        logger.warning("%s 실패 신호 기록 실패: %s", job_id, signal_err)


def _agent_record_job_missed(job_id: str, scheduled_run_time) -> None:
    try:
        from agents.agent_tasks.signals import record_job_missed
        record_job_missed(job_id, scheduled_run_time, db_path=resolve_db_path())
    except Exception as signal_err:
        logger.warning("%s 놓친 실행 신호 기록 실패: %s", job_id, signal_err)


def _on_job_event(event) -> None:
    """APScheduler 리스너. guarded_job 이 예외를 삼키므로 EVENT_JOB_ERROR 는 데코레이터 없는 잡의 안전망이고,
    EVENT_JOB_MISSED 는 misfire grace 초과(스케줄러 정지·재기동 지연)의 유일한 신호다."""
    if event.code == EVENT_JOB_MISSED:
        logger.warning("놓친 실행: %s @ %s", event.job_id, event.scheduled_run_time)
        _agent_record_job_missed(event.job_id, event.scheduled_run_time)
    elif event.code == EVENT_JOB_ERROR:
        exc = event.exception
        _agent_record_job_failure(event.job_id, f"{type(exc).__name__}: {exc}"[:300])
```

(b) 잡 정의 — **데코레이터만** 붙이는 잡(본문은 다른 Task 소유이거나 삭제 예정):

```python
@guarded_job("foreign_approval_sync")       # 본문 전환은 Task 2
def foreign_approval_sync_job(): ...
@guarded_job("rule_compliance_audit")       # 본문 전환은 Task 6
def rule_compliance_audit_job(): ...
@guarded_job("quality_guard_review")        # 본문 전환은 Task 6
def quality_guard_review_job(): ...
@guarded_job("reimb_data_sync")             # 본문 전환은 Task 2
def reimb_data_sync_job(): ...
@guarded_job("daily_mailing_dispatch")      # 본문 전환은 Task 2
def daily_mailing_dispatch_job(): ...
@guarded_job("amjilsim_d_minus_2_reporter")  # Task 7 이 스케줄에서 내림(함수·CLI 는 보존)
def amjilsim_d_minus_2_reporter_job(): ...
@guarded_job("amjilsim_d_plus_1_reporter")
def amjilsim_d_plus_1_reporter_job(): ...
@guarded_job("amjilsim_monthly_trend")
def amjilsim_monthly_trend_job(): ...
@guarded_job("hira_schedule_fetcher_annual")
def hira_schedule_fetcher_job(): ...
@guarded_job("exchange_rate_recompute")     # 수동 전용(스케줄 미등록) — 런북(Task 5)이 부르므로 기록은 남긴다
def exchange_rate_recompute_job(): ...
```

(c) 본문을 전환하는 잡 — 바깥 `try/except` 제거, 결과 dict 반환. 아래 코드로 각 함수를 통째로 교체한다.

```python
async def run_pipeline():
    """(docstring 기존 유지)"""
    logger.info("━━━ 파이프라인 시작 ━━━")
    config = load_config()
    from agents.domestic_catchup import catch_up
    counts = {"ingested": 0, "catchup_errors": 0}
    try:
        result = await catch_up(config, BASE_DIR)
        counts["ingested"] = len(result.get("ingested") or [])
        counts["catchup_errors"] = len(result.get("errors") or [])
        logger.info("국내 약가 catch-up: 신규 적재 %s · 오류 %s", result.get("ingested"), result.get("errors"))
        if result.get("skipped"):
            logger.info("국내 약가 catch-up skip (%s) — 당월 이미 적재", result["skipped"])
        elif result.get("errors") and not result.get("ingested"):
            logger.error("국내 약가 catch-up 실패 — 적재 0건, 오류 %s (1~7일 일일 재시도)", result["errors"])
            _agent_record_job_failure("domestic_price_pipeline",
                                      "; ".join(str(e) for e in result["errors"])[:300])
        elif result.get("errors"):
            logger.warning("catch-up 일부 오류 — 다음 실행 시 재시도(멱등): %s", result["errors"])
    except Exception as e:
        logger.error("국내 약가 catch-up 실패: %s", e, exc_info=True)
        _agent_record_job_failure("domestic_price_pipeline", f"{type(e).__name__}: {e}"[:300])
    dashboard_agent = DashboardAgent(config, BASE_DIR)      # 예외는 guarded_job(job) 이 받는다
    html_path = dashboard_agent.run()
    logger.info("━━━ 파이프라인 완료 ━━━")
    logger.info("대쉬보드: file://%s", html_path.resolve())
    counts["dashboard"] = str(html_path)
    return {"ok": True, "counts": counts}


@guarded_job("domestic_price_pipeline")
def job():
    """APScheduler 콜백 — 비동기 파이프라인 실행"""
    return asyncio.run(run_pipeline())


@guarded_job("git_auto_backup")
def git_backup_job():
    """Git 자정 자동 백업 — 로컬 전용 (프로덕션 컨테이너는 git 없음 → 매일 ERROR 방지)"""
    if is_production() or shutil.which("git") is None:
        logger.info("Git 자동 백업 skip — 로컬 전용 잡 (프로덕션 컨테이너 또는 git 미설치)")
        return None
    logger.info("━━━ 자정 Git 자동 백업 시작 ━━━")
    try:
        subprocess.run(["git", "add", "."], cwd=BASE_DIR, check=True)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        res_commit = subprocess.run(["git", "commit", "-m", f"Auto backup: {now_str}"],
                                    cwd=BASE_DIR, capture_output=True, text=True)
        if "nothing to commit" in res_commit.stdout or "nothing to commit" in res_commit.stderr:
            logger.info("변경된 파일이 없어 백업(Commit)을 건너뜁니다.")
            return {"counts": {"committed": 0}}
        subprocess.run(["git", "push"], cwd=BASE_DIR, capture_output=True, text=True, check=True)
        logger.info("Git 백업 성공")
        return {"counts": {"committed": 1}}
    except subprocess.CalledProcessError as e:
        return {"errors": [f"git exit {e.returncode}: {(e.stderr or e.output or '')[:200]}"]}


@guarded_job("exchange_rate_refresh")
def exchange_rate_refresh_job():
    """(docstring 기존 유지)"""
    logger.info("━━━ KEB 36개월 평균환율 갱신 시작 ━━━")
    from agents.exchange_rate import ExchangeRateFetcher
    fetcher = ExchangeRateFetcher()
    rates = asyncio.run(fetcher.refresh(headless=True))
    meta = fetcher._rate_meta
    logger.info("환율 갱신 완료: %d개 통화 (기간 %s~%s) — %s", len(rates), meta.get("from", "?"), meta.get("to", "?"),
                {k: rates.get(k) for k in ("USD", "EUR", "JPY", "GBP", "CHF") if k in rates})
    counts = {"currencies": len(rates), "to": meta.get("to")}
    errors = []
    try:
        from agents.db import DrugPriceDB
        db = DrugPriceDB(BASE_DIR / "data" / "db" / "drug_prices.db")
        rc = db.recompute_foreign_fx(rates, meta)
        logger.info("환율 갱신 후 기존행 재계산: 대상 %d · 갱신 %d · skip %d", rc["candidates"], rc["updated"], rc["skipped"])
        counts.update({"recompute_candidates": rc["candidates"], "recompute_updated": rc["updated"]})
    except Exception as e:
        logger.exception("환율 갱신 후 재계산 실패(캐시는 갱신됨): %s", e)
        errors.append(f"재계산 실패(캐시는 갱신됨): {type(e).__name__}: {e}")
    return {"ok": not errors, "counts": counts, "errors": errors}


@guarded_job("reimbursement_xnational_sync")
def reimbursement_xnational_sync_job():
    """(docstring 기존 유지)"""
    logger.info("━━━ Reimbursement xnational sync 시작 ━━━")
    from agents.foreign_approval.reimbursement_sync import sync_all
    summary = sync_all()
    total = 0
    for slug, counts in summary.items():
        logger.info("  %s: %s", slug, counts)
        total += sum(int(v or 0) for v in counts.values())
    errors = [] if total > 0 else ["4개 사이트 모두 0행 — 스크레이퍼/셀렉터 확인"]
    return {"ok": not errors, "counts": {"products": len(summary), "rows": total}, "errors": errors}


def _crawl_news(lookback_competitor: int, lookback_gov: int) -> dict:
    """경쟁사·정부 두 크롤을 독립 실행 — 한쪽 실패가 다른 쪽을 막지 않고, 둘 다 errors 로 반환."""
    counts, errors, error_class = {}, [], "transient"
    try:
        from agents import competitor_news_agent as cn
        r = cn.crawl(lookback_days=lookback_competitor, t1_only=True)
        if r.get("error"):
            errors.append(f"competitor: {r['error']}")
            if "미설정" in str(r["error"]):
                error_class = "config"
        else:
            counts["competitor"] = r.get("total_stored", 0)
            if r.get("expired_removed") is not None:
                counts["expired_removed"] = r.get("expired_removed", 0)
    except Exception as e:
        logger.exception("Competitor News 크롤 실패: %s", e)
        errors.append(f"competitor: {type(e).__name__}: {e}")
    try:
        from agents import gov_policy_news as gpn
        g = gpn.crawl(lookback_days=lookback_gov)
        if g.get("error"):
            errors.append(f"gov_policy: {g['error']}")
        else:
            counts["gov_policy"] = g.get("total_stored", 0)
    except Exception as e:
        logger.exception("Gov Policy News 크롤 실패: %s", e)
        errors.append(f"gov_policy: {type(e).__name__}: {e}")
    return {"ok": not errors, "counts": counts, "errors": errors, "error_class": error_class}


@guarded_job("competitor_news_weekly")
def competitor_news_weekly_job():
    """(docstring 기존 유지)"""
    logger.info("━━━ Competitor/Gov News 주간 크롤 시작 ━━━")
    from agents import competitor_news_agent as cn
    from agents import gov_policy_news as gpn
    res = _crawl_news(cn.DEFAULT_LOOKBACK_DAYS, gpn.DEFAULT_LOOKBACK_DAYS)
    logger.info("주간 크롤 완료: %s", res["counts"])
    return res


@guarded_job("competitor_news_daily")
def competitor_news_daily_job():
    """(docstring 기존 유지)"""
    logger.info("━━━ Competitor/Gov News 매일 fetch 시작 ━━━")
    res = _crawl_news(3, 3)
    logger.info("매일 fetch 완료: %s", res["counts"])
    return res


@guarded_job("access_insight_fresh_signals")
def access_insight_fresh_signal_job():
    """(docstring 기존 유지)"""
    logger.info("━━━ Access Insight 신선 신호 주간 크롤 시작 ━━━")
    from agents.amjilsim_tracker.signal_extractor import run_fresh_crawl
    res = run_fresh_crawl()
    logger.info("신선 신호 크롤 완료: articles=%d matched=%d inserted=%d dup=%d (tier별 %s)",
                res.get("articles", 0), res.get("matched", 0), res.get("inserted", 0),
                res.get("duplicate_skipped", 0), res.get("by_tier", {}))
    return {"counts": {k: res.get(k, 0) for k in ("articles", "matched", "inserted", "duplicate_skipped")}}


@guarded_job("competitor_trend_promote")
def competitor_trend_promote_job():
    """(docstring 기존 유지)"""
    logger.info("━━━ Competitor Trend 승격 (아카이브→카드) 시작 ━━━")
    from agents import competitor_trends_agent as ct
    res = ct.promote_from_archive(days=2)
    t = res.get("totals", {})
    logger.info("Trend 승격 완료: archive=%s accepted=%s upserted=%s", t.get("archive"), t.get("accepted"), t.get("upserted"))
    return {"counts": {k: t.get(k, 0) for k in ("archive", "accepted", "upserted")}}


@guarded_job("daily_mailing_top_news")
def daily_mailing_top_news_job():
    """매일 06:40 — '오늘의 주요 MA 뉴스' 산출·캐시 (06:30 뉴스 수집 직후, 구독 공통)."""
    from agents.daily_mailing.dispatch import DispatchDeps
    from agents.daily_mailing.schedule import KST
    items = DispatchDeps.production().load_top_ma_news(datetime.now(KST).date().isoformat(), retry_failed=True)
    logger.info("daily mailing 주요 MA 뉴스: %d건", len(items))
    return {"counts": {"items": len(items)}}


@guarded_job("daily_mailing_health")
def daily_mailing_health_job():
    """매일 09:30 — 오늘 예정 구독의 미생성·보류·발송실패·Gmail 토큰 상태 점검. 문제는 헬스 신호로(잡 실패 아님)."""
    from agents.daily_mailing.dispatch import DispatchDeps, health_check
    res = health_check(DispatchDeps.production())
    if res["ok"]:
        logger.info("daily mailing health OK (%s)", res["run_date"])
    else:
        logger.error("daily mailing health 문제 %d건: %s", len(res["problems"]), res["problems"])
        _agent_record_health_problems(res)
    return {"counts": {"problems": len(res.get("problems") or [])}}


@guarded_job("nhis_negotiation_sync")
def nhis_negotiation_sync_job():
    """(docstring 기존 유지)"""
    logger.info("━━━ NHIS 약가협상 공개자료 주간 sync 시작 ━━━")
    from agents.ingest import nhis_negotiation_import as imp
    res = imp.run()
    logger.info("NHIS sync 완료: 수집 %d · 신규아카이브 %d · 매칭 drug %d/row %d · 미매칭 %d",
                res["fetched"], res["inserted"], res["matched_drugs"], res["matched_rows"], res["unmatched_count"])
    errors = [] if res["fetched"] > 0 else ["NHIS 수집 0건 — 사이트/파서 확인"]
    return {"ok": not errors, "counts": {k: res[k] for k in ("fetched", "inserted", "matched_drugs", "unmatched_count")},
            "errors": errors}


@guarded_job("home_brand_related_refresh")
def home_brand_related_refresh_job():
    """(docstring 기존 유지)"""
    logger.info("━━━ Home 브랜드 연관검색어 확장 주간 sync 시작 ━━━")
    from agents import naver_related
    res = naver_related.expand_home_brands()
    logger.info("Home 브랜드 연관검색어 확장 완료: seeds_processed=%d candidates_added=%d",
                res.get("seeds_processed", 0), res.get("candidates_added", 0))
    return {"counts": {k: res.get(k, 0) for k in ("seeds_processed", "candidates_added")}}


@guarded_job("policy_intel_analysis_sync")          # main() 안의 중첩 함수 _policy_analysis_sync_job 을 모듈 수준으로 끌어올림(본문 의미는 Task 2)
def policy_analysis_sync_job():
    """매일 02:10 — Policy Intelligence 큐레이션 사이드카 git sync."""
    res = _sync_policy_analysis()
    logger.info("policy analysis sync: %s", res)
    return res
```

(d) `main()` 의 등록부(`scheduler.py:995-1270`)를 `JobSpec` 목록 + 루프로 교체한다. 기존 `scheduler.add_job(...)` 22개와 중첩 함수 `_policy_analysis_sync_job` 을 지우고 다음을 넣는다(잡 시각·이름은 기존과 동일).

```python
@dataclass(frozen=True)
class JobSpec:
    id: str
    name: str
    fn: Callable
    trigger: object
    misfire_grace_time: int = DAILY_GRACE
    coalesce: bool = True
    max_instances: int = 1


def _cron(**kw) -> CronTrigger:
    return CronTrigger(timezone="Asia/Seoul", **kw)


def build_job_specs(sched_cfg: dict) -> list[JobSpec]:
    """등록할 잡 전부. 테스트가 이 목록으로 guarded·misfire·JOB_RUNBOOK 일치를 대조한다."""
    return [
        JobSpec("domestic_price_pipeline", "국내 약가 모니터링 파이프라인", job,
                _cron(day=sched_cfg["day"], hour=sched_cfg["hour"], minute=sched_cfg["minute"]),
                misfire_grace_time=86400),                                   # 기존 정책(7/1·6/1 미실행 원인) 유지
        JobSpec("git_auto_backup", "Git 자동 백업", git_backup_job, _cron(hour=0, minute=0)),
        JobSpec("foreign_approval_sync", "ForeignApproval 가격↔허가 auto-sync", foreign_approval_sync_job,
                _cron(hour=4, minute=30)),
        JobSpec("rule_compliance_audit", "Rule Compliance 일일 감사", rule_compliance_audit_job, _cron(hour=5, minute=30)),
        JobSpec("quality_guard_review", "QualityGuard 코드베이스 일일 리뷰", quality_guard_review_job, _cron(hour=6, minute=0)),
        JobSpec("competitor_news_weekly", "경쟁사 뉴스 주간 크롤 (Tier 1, 13 브랜드)", competitor_news_weekly_job,
                _cron(day_of_week="mon", hour=3, minute=30), misfire_grace_time=PERIODIC_GRACE),
        JobSpec("competitor_news_daily", "Competitor/Gov News 매일 06:30 fetch (신선분)", competitor_news_daily_job,
                _cron(hour=6, minute=30)),
        JobSpec("competitor_trend_promote", "경쟁사 동향 카드 일일 승격 (아카이브→카드)", competitor_trend_promote_job,
                _cron(hour=7, minute=0)),
        JobSpec("access_insight_fresh_signals", "Access Insight 신선 신호 주간 크롤 (tier A/B/D → media signals)",
                access_insight_fresh_signal_job, _cron(day_of_week="mon", hour=4, minute=0),
                misfire_grace_time=PERIODIC_GRACE),
        JobSpec("reimbursement_xnational_sync", "Reimbursement cross-national 분기 sync (NICE/PBAC/CMS/CHUIKYO)",
                reimbursement_xnational_sync_job, _cron(month="1,4,7,10", day=1, hour=2, minute=0),
                misfire_grace_time=PERIODIC_GRACE),
        JobSpec("exchange_rate_refresh", "KEB 36개월 평균환율 월간 갱신 (매월 2일 04:00)", exchange_rate_refresh_job,
                _cron(day=2, hour=4, minute=0), misfire_grace_time=PERIODIC_GRACE),
        JobSpec("reimb_data_sync", "Reimbursement 위원회 데이터 매일 02:00 git sync", reimb_data_sync_job,
                _cron(hour=2, minute=0)),
        JobSpec("policy_intel_analysis_sync", "Policy Intelligence 큐레이션 사이드카 매일 02:10 git sync",
                policy_analysis_sync_job, _cron(hour=2, minute=10)),
        JobSpec("daily_mailing_dispatch", "Daily Mailing 10분 주기 발송 판정 (생성→승인대기/자동발송·재시도)",
                daily_mailing_dispatch_job, IntervalTrigger(minutes=10, timezone="Asia/Seoul"),
                misfire_grace_time=INTERVAL_GRACE),
        JobSpec("daily_mailing_top_news", "Daily Mailing 주요 MA 뉴스 매일 06:40", daily_mailing_top_news_job,
                _cron(hour=6, minute=40)),
        JobSpec("daily_mailing_health", "Daily Mailing 헬스체크 매일 09:30", daily_mailing_health_job, _cron(hour=9, minute=30)),
        JobSpec("nhis_negotiation_sync", "NHIS 약가협상 공개자료 주간 sync (신규/확대 → 매칭 교체)", nhis_negotiation_sync_job,
                _cron(day_of_week="mon", hour=2, minute=30), misfire_grace_time=PERIODIC_GRACE),
        JobSpec("home_brand_related_refresh", "Home 브랜드 Naver 연관검색어 확장 (주간, 후보 제안 → admin 승인 대기)",
                home_brand_related_refresh_job, _cron(day_of_week="mon", hour=5, minute=0),
                misfire_grace_time=PERIODIC_GRACE),
        JobSpec("amjilsim_d_minus_2_reporter", "amjilsim·약평위 D-2 사전 예측 보고서 발사 (16:00, 17:00 마감)",
                amjilsim_d_minus_2_reporter_job, _cron(hour=16, minute=0)),
        JobSpec("amjilsim_d_plus_1_reporter", "amjilsim·약평위 D+1 결과 리뷰 + 예측 룰 자가 학습 (08:00, 09:00 마감)",
                amjilsim_d_plus_1_reporter_job, _cron(hour=8, minute=0)),
        JobSpec("amjilsim_monthly_trend", "amjilsim·약평위 월간 트렌드 진단 (매월 마지막 약평위 D+7)",
                amjilsim_monthly_trend_job, _cron(hour=9, minute=0)),
        JobSpec("hira_schedule_fetcher_annual", "HIRA 공식 일정 매년 1/1 자동 갱신", hira_schedule_fetcher_job,
                _cron(month=1, day=1, hour=9, minute=0), misfire_grace_time=PERIODIC_GRACE),
    ]
```

`main()` 끝부분(기존 `sched_cfg = ...` 부터 `scheduler.start()` 까지):

```python
    sched_cfg = config["domestic_agent"]["schedule"]
    scheduler = BlockingScheduler(timezone="Asia/Seoul")
    for spec in build_job_specs(sched_cfg):
        scheduler.add_job(spec.fn, trigger=spec.trigger, id=spec.id, name=spec.name, replace_existing=True,
                          misfire_grace_time=spec.misfire_grace_time, coalesce=spec.coalesce,
                          max_instances=spec.max_instances)
    scheduler.add_listener(_on_job_event, EVENT_JOB_ERROR | EVENT_JOB_MISSED)

    logger.info("%s", _schedule_banner(sched_cfg))
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("스케줄러 종료")
```

`--amjilsim-daily-crawl-now` 분기의 `_sync_policy_analysis()` 직접 호출은 `policy_analysis_sync_job()` 호출로 바꾼다(래퍼 경유).

- [ ] **Step 11: 통과 확인 + 기존 회귀**

Run: `.venv/bin/python -m pytest tests/test_scheduler_job_specs.py tests/test_dm_scheduler_jobs.py tests/test_scheduler_local_only_jobs.py tests/test_agent_signal_wiring.py tests/test_agent_jobs.py tests/test_agent_signals.py -q`
Expected: 모두 PASS. (`test_scheduler_local_only_jobs` 는 잡이 dict 를 돌려줘도 ERROR 로그가 없으므로 그대로 통과한다.)

- [ ] **Step 12: 커밋**

```bash
git add agents/agent_tasks/jobs.py agents/agent_tasks/store.py agents/agent_tasks/signals.py scheduler.py \
        tests/conftest.py tests/test_agent_jobs.py tests/test_scheduler_job_specs.py tests/test_agent_signals.py
git commit -m "feat(agent-team): 잡 결과 규약 + guarded_job 래퍼 + misfire/리스너 — 22개 잡 전부 원장에 남게"
```

**완료 기준 (T1)**
- `build_job_specs` 의 22개 id == `JOB_RUNBOOK` 키 집합, 전부 `__job_id__` 일치, misfire 명시, 리스너 등록(테스트 4개).
- 예외·`errors`·`error`·`failed` 가 원장 과제가 되고, 신호 실패는 잡을 죽이지 않는다(테스트).
- **오탐 억제**: 날짜 ref 는 잡당 하루 1건(recurred 이벤트로 재발 표시), config ref 와 blocked 는 닫힐 때까지 1건, misfire 는 하루 1건. 프로덕션 첫날 예측: 이미 알려진 국내약가 9/1 실패 1건(P1 과 동일 ref) + 정책 분석 sync 1건(Task 2 전환 후) — **1~3건**, 이후 0~1건/일. 근거: 프로덕션 스케줄러 점검 메모리의 상시 실패 4종 중 git·RC 는 INFO skip, runs sync 는 retire.

---

### Task 2: 가려진 실패 4종 — 폴백·`error` 키·`failed`·`errors` 를 신호로

**Files:**
- Modify: `scheduler.py` — `foreign_approval_sync_job`, `_reimb_committee_data_sync`, `_reimb_reports_sync`, `reimb_data_sync_job`, `policy_analysis_sync_job`, `daily_mailing_dispatch_job` (6개 함수 본문만)
- Test: `tests/test_scheduler_masked_failures.py`

원칙: **데이터는 그대로 쓴다**(폴백 적용, 부분 성공 반영) — 단, 폴백했다는 사실·실패한 항목은 `errors` 로 반환해 래퍼가 과제를 만들게 한다. `auth required`(토큰 부재)는 `error_class="config"` 로 ref 를 고정한다.

- [ ] **Step 1: 실패 테스트**

```python
# tests/test_scheduler_masked_failures.py
"""잡이 '성공' 로그를 남기며 삼키던 4종 실패가 표준 결과(errors)와 원장 과제로 드러나는지."""
from __future__ import annotations

import sys
import types
from datetime import datetime, timedelta, timezone

import scheduler as sch
from agents.agent_tasks.store import list_tasks

KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).date().isoformat()


def _db(monkeypatch, tmp_path):
    db = tmp_path / "a.db"
    monkeypatch.setenv("AGENT_TASK_DB", str(db))
    return db


# ── reimb_data_sync: 원격 fetch 실패 → 로컬 폴백은 '적용은 하되 실패 신호' ─────────────────

def _fake_reimb_modules(monkeypatch, *, url_fails: bool):
    imp = types.ModuleType("agents.ingest.reimb_committee_import")

    def load_payload(source=None):
        if source is not None and url_fails:
            raise RuntimeError("HTTP 404")
        return {"drugs": [], "events": []}
    imp.load_payload = load_payload
    imp.payload_hash = lambda payload: "h" + str(len(str(payload)))
    imp.run = lambda payload: {"drugs_after": 65, "events_total": 120, "events_added": 0}
    rr = types.ModuleType("agents.reimb_reports")
    rr.backfill_blobs = lambda: {}
    rr.load_reports_manifest = lambda source=None: {"reports": []}
    rr.sync_reports = lambda manifest: {"reports_in_manifest": 0, "ingested": [], "skipped": 0, "errors": []}
    monkeypatch.setitem(sys.modules, "agents.ingest.reimb_committee_import", imp)
    monkeypatch.setitem(sys.modules, "agents.reimb_reports", rr)


def test_reimb_sync_fallback_is_reported_but_data_still_applied(monkeypatch, tmp_path):
    db = _db(monkeypatch, tmp_path)
    monkeypatch.setattr(sch, "BASE_DIR", tmp_path)            # .last_applied_hash 를 임시 경로에
    monkeypatch.setenv("REIMB_DATA_URL", "https://raw.example/committee_results.json")
    monkeypatch.delenv("REPORTS_DATA_URL", raising=False)
    _fake_reimb_modules(monkeypatch, url_fails=True)
    res = sch.reimb_data_sync_job()
    assert res["ok"] is False
    assert res["counts"]["data"] == {"drugs_after": 65, "events_total": 120, "events_added": 0}   # 폴백 데이터는 적용됨
    assert "로컬 이미지 폴백" in res["errors"][0] and "HTTP 404" in res["errors"][0]
    assert (tmp_path / "data" / "reimb" / ".last_applied_hash").exists()
    tasks = list_tasks(db_path=db)
    assert len(tasks) == 1 and tasks[0]["source_ref"] == f"reimb_data_sync:{TODAY}"


def test_reimb_sync_without_fallback_is_clean(monkeypatch, tmp_path):
    db = _db(monkeypatch, tmp_path)
    monkeypatch.setattr(sch, "BASE_DIR", tmp_path)
    monkeypatch.setenv("REIMB_DATA_URL", "https://raw.example/committee_results.json")
    monkeypatch.delenv("REPORTS_DATA_URL", raising=False)
    _fake_reimb_modules(monkeypatch, url_fails=False)
    res = sch.reimb_data_sync_job()
    assert res["ok"] is True and res["errors"] == []
    assert list_tasks(db_path=db) == []
    res2 = sch.reimb_data_sync_job()                             # 같은 해시 → 변경 없음 skip, 여전히 ok
    assert res2["ok"] is True and res2["counts"]["data"] == {"changed": 0}


# ── policy_intel_analysis_sync: 반환 dict 의 error 키 ────────────────────────────────

def test_policy_sync_auth_error_is_config_class(monkeypatch, tmp_path):
    db = _db(monkeypatch, tmp_path)
    monkeypatch.setattr(sch, "_sync_policy_analysis", lambda: {
        "copied": 0, "skipped": 0, "rejected": 0,
        "error": "auth required: POLICY_ANALYSIS_TOKEN/GITHUB_TOKEN missing (fail-closed in production)", "dest": "x"})
    res = sch.policy_analysis_sync_job()
    assert res["ok"] is False and res["error_class"] == "config"
    assert list_tasks(db_path=db)[0]["source_ref"] == "policy_intel_analysis_sync:config"


def test_policy_sync_fetch_error_is_transient(monkeypatch, tmp_path):
    db = _db(monkeypatch, tmp_path)
    monkeypatch.setattr(sch, "_sync_policy_analysis", lambda: {
        "copied": 0, "skipped": 0, "rejected": 0, "error": "source fetch failed: timeout", "dest": "x"})
    res = sch.policy_analysis_sync_job()
    assert res["ok"] is False and res["error_class"] == "transient"
    assert list_tasks(db_path=db)[0]["source_ref"] == f"policy_intel_analysis_sync:{TODAY}"


def test_policy_sync_success(monkeypatch, tmp_path):
    db = _db(monkeypatch, tmp_path)
    monkeypatch.setattr(sch, "_sync_policy_analysis", lambda: {"copied": 2, "skipped": 5, "rejected": 0, "dest": "x"})
    res = sch.policy_analysis_sync_job()
    assert res == {"ok": True, "counts": {"copied": 2, "skipped": 5, "rejected": 0}, "errors": [], "error_class": "transient"}
    assert list_tasks(db_path=db) == []


# ── foreign_approval_sync: result["failed"] ─────────────────────────────────────────

def test_foreign_approval_failed_slugs_become_errors(monkeypatch, tmp_path):
    db = _db(monkeypatch, tmp_path)
    mod = types.ModuleType("agents.foreign_approval.agent")

    class FakeAgent:
        def list_coverage_gaps(self):
            return ["welireg", "prevymis"]

        def sync_from_prices(self, **kw):
            return {"built": ["welireg"], "failed": [{"slug": "prevymis", "reason": "FDA ID 미확인"}], "skipped": []}
    mod.ForeignApprovalAgent = FakeAgent
    monkeypatch.setitem(sys.modules, "agents.foreign_approval.agent", mod)
    res = sch.foreign_approval_sync_job()
    assert res["ok"] is False and res["errors"] == ["prevymis: FDA ID 미확인"]
    assert res["counts"] == {"gaps": 2, "built": 1, "failed": 1}
    assert list_tasks(db_path=db)[0]["source_ref"] == f"foreign_approval_sync:{TODAY}"


def test_foreign_approval_no_gaps_is_ok(monkeypatch, tmp_path):
    db = _db(monkeypatch, tmp_path)
    mod = types.ModuleType("agents.foreign_approval.agent")

    class FakeAgent:
        def list_coverage_gaps(self):
            return []
    mod.ForeignApprovalAgent = FakeAgent
    monkeypatch.setitem(sys.modules, "agents.foreign_approval.agent", mod)
    assert sch.foreign_approval_sync_job()["counts"] == {"gaps": 0, "built": 0, "failed": 0}
    assert list_tasks(db_path=db) == []


# ── daily_mailing_dispatch: res["errors"] ───────────────────────────────────────────

def test_dispatch_errors_become_task(monkeypatch, tmp_path):
    db = _db(monkeypatch, tmp_path)

    class FakeDeps:
        @classmethod
        def production(cls):
            return cls()
    monkeypatch.setattr("agents.daily_mailing.dispatch.DispatchDeps", FakeDeps)
    monkeypatch.setattr("agents.daily_mailing.dispatch.dispatch_due_subscriptions",
                        lambda deps: {"run_date": TODAY, "expired": [], "generated": [{"subscription_id": 3}],
                                      "skipped": 1, "errors": [14], "deferred": [], "retried": 0})
    res = sch.daily_mailing_dispatch_job()
    assert res["ok"] is False and res["errors"] == ["구독 14 생성 실패"]
    assert res["counts"] == {"generated": 1, "expired": 0, "retried": 0, "skipped": 1}
    assert list_tasks(db_path=db)[0]["source_ref"] == f"daily_mailing_dispatch:{TODAY}"
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_scheduler_masked_failures.py -q`
Expected: 8 중 대부분 FAIL (`reimb_data_sync_job` 이 `None` 을 돌려주고, `policy_analysis_sync_job` 이 error 를 ok 로 정규화하는 등).

- [ ] **Step 3: 구현 — 6개 함수 본문 교체**

```python
@guarded_job("foreign_approval_sync")
def foreign_approval_sync_job():
    """ForeignApprovalAgent auto-sync — 매일 04:30 Seoul (compliance 감사 전).

    foreign_drug_prices 에 있으나 indications_master 에 없는 제품을 자동 build. 실패 slug 는 failed 로 반환 → 과제.
    """
    from agents.foreign_approval.agent import ForeignApprovalAgent
    logger.info("━━━ ForeignApproval auto-sync 시작 ━━━")
    agent = ForeignApprovalAgent()
    gaps = agent.list_coverage_gaps()
    if not gaps:
        logger.info("ForeignApproval: gap 0건 — 이미 동기화됨")
        return {"counts": {"gaps": 0, "built": 0, "failed": 0}}
    logger.info("ForeignApproval: gap %d건 — %s", len(gaps), gaps)
    result = agent.sync_from_prices()
    logger.info("ForeignApproval: built=%d failed=%d", len(result["built"]), len(result["failed"]))
    for f in result["failed"]:
        logger.warning("  ✗ %s: %s", f["slug"], f["reason"])
    return {"counts": {"gaps": len(gaps), "built": len(result["built"]), "failed": len(result["failed"])},
            "failed": result["failed"]}


def _reimb_committee_data_sync() -> dict:
    """위원회 데이터(JSON) git-sync — 멱등. 반환: {counts, fallback}. fallback 은 원격 실패 사유(없으면 None)."""
    from agents.ingest import reimb_committee_import as imp
    from agents import reimb_reports as rr

    source = os.environ.get("REIMB_DATA_URL") or None
    fallback = None
    if source:
        try:
            payload = imp.load_payload(source)
        except Exception as e:
            logger.warning("데이터 sync: REIMB_DATA_URL fetch 실패(%s) → 로컬 폴백", e)
            payload = imp.load_payload(None)
            fallback = f"{type(e).__name__}: {e}"
    else:
        payload = imp.load_payload(None)
    new_hash = imp.payload_hash(payload)

    hash_file = BASE_DIR / "data" / "reimb" / ".last_applied_hash"
    prev_hash = hash_file.read_text().strip() if hash_file.exists() else ""
    if new_hash == prev_hash:
        logger.info("데이터 sync: 변경 없음 (hash %s) — skip", new_hash[:12])
        return {"counts": {"changed": 0}, "fallback": fallback}

    result = imp.run(payload)
    try:
        rr.backfill_blobs()
    except Exception as e:
        logger.warning("데이터 sync: blob backfill 스킵: %s", e)
    hash_file.parent.mkdir(parents=True, exist_ok=True)
    hash_file.write_text(new_hash)
    logger.info("데이터 sync 완료: 약제 %d · 이벤트 %d (신규 %d) · hash %s · source=%s",
                result["drugs_after"], result["events_total"], result["events_added"], new_hash[:12],
                f"{source} (폴백:local)" if fallback else (source or "local"))
    return {"counts": {k: result[k] for k in ("drugs_after", "events_total", "events_added")}, "fallback": fallback}


def _reimb_reports_sync() -> dict:
    """위원회 전후 보고서 git-sync — 멱등. 반환: {counts, fallback, errors}."""
    import hashlib as _hl
    import json as _j
    from agents import reimb_reports as rr

    rsrc = os.environ.get("REPORTS_DATA_URL") or None
    rhash_file = BASE_DIR / "data" / "reimb" / ".last_reports_hash"
    prev_rhash = rhash_file.read_text().strip() if rhash_file.exists() else ""
    fallback = None
    try:
        manifest = rr.load_reports_manifest(rsrc)
    except Exception as e:
        logger.warning("보고서 sync: REPORTS_DATA_URL fetch 실패(%s) → 로컬 매니페스트 폴백", e)
        manifest = rr.load_reports_manifest(None)
        fallback = f"{type(e).__name__}: {e}"
    m_hash = _hl.sha256(_j.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    if m_hash == prev_rhash:
        logger.info("보고서 sync: 매니페스트 변경 없음 (hash %s) — skip", m_hash[:12])
        return {"counts": {"changed": 0}, "fallback": fallback, "errors": []}
    rres = rr.sync_reports(manifest)
    rhash_file.parent.mkdir(parents=True, exist_ok=True)
    rhash_file.write_text(m_hash)
    logger.info("보고서 sync 완료: 매니페스트 %d · 신규적재 %d · skip %d · 오류 %d · hash %s",
                rres["reports_in_manifest"], len(rres["ingested"]), rres["skipped"], len(rres["errors"]), m_hash[:12])
    return {"counts": {"manifest": rres["reports_in_manifest"], "ingested": len(rres["ingested"]),
                       "skipped": rres["skipped"]},
            "fallback": fallback, "errors": [str(e)[:200] for e in rres["errors"]]}


@guarded_job("reimb_data_sync")
def reimb_data_sync_job():
    """매일 02:00 Seoul + 부팅 1회 — 위원회 데이터 + 보고서 git → DB 멱등 적재. (docstring 나머지 기존 유지)

    데이터/보고서는 독립 실행. 원격 fetch 실패 시 로컬 이미지 폴백을 **적용하되** 그 사실을 errors 로 반환한다 —
    '변경 없음 skip' 처럼 보이던 문제(감사 #3)를 과제로 드러낸다.
    """
    logger.info("━━━ Reimbursement 데이터·보고서 sync 시작 ━━━")
    counts, errors = {}, []
    try:
        d = _reimb_committee_data_sync()
        counts["data"] = d["counts"]
        if d["fallback"]:
            errors.append(f"위원회 데이터 원격 fetch 실패 → 로컬 이미지 폴백 적용 (데이터는 적용됨): {d['fallback']}")
    except Exception as e:
        logger.exception("Reimbursement 데이터 sync 실패: %s", e)
        errors.append(f"데이터 sync: {type(e).__name__}: {e}")
    try:
        r = _reimb_reports_sync()
        counts["reports"] = r["counts"]
        if r["fallback"]:
            errors.append(f"보고서 매니페스트 원격 fetch 실패 → 로컬 폴백 적용: {r['fallback']}")
        errors += [f"보고서 적재 오류: {e}" for e in r["errors"]]
    except Exception as e:
        logger.exception("Reimbursement 보고서 sync 실패: %s", e)
        errors.append(f"보고서 sync: {type(e).__name__}: {e}")
    return {"ok": not errors, "counts": counts, "errors": errors}


@guarded_job("policy_intel_analysis_sync")
def policy_analysis_sync_job():
    """매일 02:10 — Policy Intelligence 큐레이션 사이드카 git sync. 반환 dict 의 error 키를 실패로 해석한다."""
    res = _sync_policy_analysis()
    counts = {k: res.get(k, 0) for k in ("copied", "skipped", "rejected")}
    if res.get("error"):
        logger.warning("policy analysis sync 실패: %s", res["error"])
        cls = "config" if str(res["error"]).startswith("auth required") else "transient"
        return {"ok": False, "counts": counts, "errors": [str(res["error"])], "error_class": cls}
    logger.info("policy analysis sync: %s", res)
    return {"ok": True, "counts": counts, "errors": []}


def _n(x) -> int:
    return len(x) if isinstance(x, (list, tuple, set, dict)) else int(x or 0)


@guarded_job("daily_mailing_dispatch")
def daily_mailing_dispatch_job():
    """10분 주기 — 발송 시각이 된 구독 브리핑 생성 → 승인 대기/자동 발송 + 실패 재시도 (설계 §6).
    생성 실패 구독(errors)은 과제로 — 하루 1건(ref 에 날짜), 다음 tick 에 성공하면 런북이 닫는다."""
    from agents.daily_mailing.dispatch import DispatchDeps, dispatch_due_subscriptions
    res = dispatch_due_subscriptions(DispatchDeps.production())
    if res.get("generated") or res.get("errors") or res.get("expired") or res.get("retried"):
        logger.info("daily mailing dispatch: %s", res)
    return {"ok": not res.get("errors"),
            "counts": {"generated": _n(res.get("generated")), "expired": _n(res.get("expired")),
                       "retried": _n(res.get("retried")), "skipped": _n(res.get("skipped"))},
            "errors": [f"구독 {sid} 생성 실패" for sid in (res.get("errors") or [])]}
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_scheduler_masked_failures.py tests/test_scheduler_job_specs.py tests/test_agent_signal_wiring.py -q`
Expected: 모두 PASS.

- [ ] **Step 5: 커밋**

```bash
git add scheduler.py tests/test_scheduler_masked_failures.py
git commit -m "fix(agent-team): 가려진 실패 4종 — reimb 폴백·policy error 키·approval failed·dispatch errors 를 과제로"
```

**완료 기준 (T2)**
- 4종 모두 테스트로 `ok=False` + 과제 생성이 증명되고, 폴백 데이터는 여전히 적용된다(hash 파일 생성 확인).
- **오탐 억제**: `reimb_data_sync` 폴백은 원격이 실제로 죽었을 때만(하루 1건). 정책 분석 `auth required` 는 `:config` ref 로 사람이 닫을 때까지 1건 — 프로덕션에 `POLICY_ANALYSIS_TOKEN` 이 없으면 배포 첫날 이 1건이 생기는 것이 정상이며 "사람만 할 수 있는 것" 목록에 있다. `daily_mailing_dispatch` 는 헬스 잡(09:30)과 같은 구독을 두 번 가리킬 수 있다(source 가 다름) — 하루 최대 2건, 허용.

---

### Task 3: 도메인 신선도 판정기 (`freshness.py`) — 14 도메인, 하루 1회

**Files:**
- Create: `agents/agent_tasks/freshness.py`, `tests/test_domain_freshness.py`
- Modify: `agents/foreign_approval/agent.py:122-153` (`list_coverage_gaps` 본문을 모듈 함수 `coverage_gaps(db_path)` 로), `scheduler.py` (`domain_freshness_job` + `JobSpec` 1행 + `--freshness-now`), `agents/agent_tasks/signals.py` (`JOB_RUNBOOK["domain_freshness"] = None` 1행), `api/server.py:2106-2117` (`?type=domain`)

`data_freshness` 테이블(HTA 35행, `hta_agent` 가 약제별 수집 시각을 적는 장부)과의 관계: **그대로 둔다**. 도메인 판정 결과는 새 테이블 `domain_freshness`(도메인당 1행, 최신 판정)에 기록하고 같은 API `/api/data/freshness?type=domain` 으로 노출한다. 두 테이블은 단위가 다르다(항목별 수집 시각 vs 도메인별 판정) — 합치면 `hta` 행의 의미가 깨진다.

**도메인 규칙표** (오늘=2026-09-30 로컬 실측; "프로덕션 예상" 은 프로덕션 스케줄러가 일·주간 잡을 정상 실행 중이라는 가정)

| # | domain | 관측 쿼리/파일 | 기한 규칙 | 로컬 관측 | 로컬 | 프로덕션 예상 | 런북 |
|---|---|---|---|---|---|---|---|
| 1 | `domestic_price` | `MAX(apply_date) FROM drug_prices` | 매월 10일부터 당월 `YYYY.MM.01` (9일까지는 전월) | `2026.07.01` | stale | stale(9/1 실패 알려짐) | `domestic_price_catchup` |
| 2 | `exchange_rate_cache` | `data/foreign/exchange_rate/keb_avg_rate_*_<to>.xlsx` 최신 `to` | 매월 5일부터 전월 말일 이상 | `20260531` | stale | 불확실(2일 잡 성공 여부) | `exchange_rate_refresh` |
| 3 | `foreign_price_fx_alignment` | 최신 가격행 중 `exchange_rate_to != 캐시 to` 건수 | 0건 | 0(197행 모두 20260531) | fresh | fresh | `exchange_rate_recompute` |
| 4 | `foreign_approval_coverage` | `coverage_gaps(db)` | 빈 목록 | [] (RC 커버리지 100%) | fresh | fresh | `foreign_approval_sync` |
| 5 | `reimbursement_xnational` | `MAX(fetched_at)` | 100일 이내 | `2026-04-29`(154일), AU 0행 | stale | stale(분기 잡 미확인) | `reimbursement_xnational_sync` |
| 6 | `committee_schedule` | `amjilsim_sessions` SCHEDULED, 오늘~+60일, 위원회별 | 두 위원회 모두 ≥1 | AMJILSIM 2·YAKPYUNGWI 2 | fresh | fresh(12월 초 stale 전환 — Task 7) | 없음(사람: 차수 입력) |
| 7 | `committee_results` | 14일 넘게 지난 세션이 SCHEDULED 로 남은 수 | 0건 | YAKPYUNGWI 07-02~, AMJILSIM 07-08~ | stale | stale(같은 git 데이터) | `reimb_data_sync` |
| 8 | `analog_reports` | `MAX(session_date)` | 75일 이내 | `2026-01-15`(258일) | stale | stale(Mac 수동 sync) | 없음(사람: `apply_analog_sync`) |
| 9 | `market_share` | `MAX(quarter) FROM market_share_quarterly` | 90일 전 시점에 끝난 마지막 분기 이상 | `2025Q4` < `2026Q2` | stale | stale(엑셀 업로드) | 없음(사람: admin 업로드) |
| 10 | `policy_intelligence` | `manifests/latest_ingest_status.json` `ran_at` + `analysis/analysis_manifest.json` mtime | ingest 21일·analysis 3일 | ingest `2026-06-30`(92일), analysis 없음 | stale | stale | ingest stale→없음 / analysis 만 stale→`policy_intel_analysis_sync` |
| 11 | `news_archive` | `MAX(fetched_at) FROM competitor_news WHERE kind IN ('competitor','gov_policy')` | 3일 | `2026-07-28` | stale(로컬 정지) | fresh | `news_daily_fetch` |
| 12 | `competitor_trends` | `MAX(updated_at) WHERE source_type='promoted'` | 7일 | `2026-07-27` | stale(로컬 정지) | fresh | `competitor_trend_promote` |
| 13 | `access_insight_signals` | `MAX(crawled_at) FROM amjilsim_media_signals` | 10일 | `2026-07-26` | stale(로컬 정지) | fresh | `access_insight_fresh_signals` |
| 14 | `nhis_negotiations` | `MAX(fetched_at) FROM nhis_negotiations` | 10일 | `2026-07-26` | stale(로컬 정지) | fresh | `nhis_negotiation_sync` |

로컬 첫 실행: **11건**(1·2·5·7·8·9·10·11·12·13·14). 프로덕션 첫 실행: **6건 ±1**(1·5·7·8·9·10 확실, 2 는 잡 성공 여부에 따라). 감사의 "6개" 와 일치한다. 런북 이름은 Task 5 가 등록한다 — Task 5 전에 이 잡을 프로덕션에서 켜면 디스패처가 `400 알 수 없는 런북` 으로 과제를 blocked 처리하므로 **두 Task 를 한 배포에 묶는다.**

- [ ] **Step 1: 실패 테스트**

```python
# tests/test_domain_freshness.py
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
    r = fr.check_foreign_price_fx_alignment(_ctx(tmp_path))
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


def test_committee_schedule_flags_committee_without_upcoming_session(tmp_path):
    _seed(tmp_path / "d.db", """
        CREATE TABLE amjilsim_sessions (session_id INTEGER, committee_type TEXT, status TEXT, session_date TEXT);
        INSERT INTO amjilsim_sessions VALUES (1,'AMJILSIM','SCHEDULED','2026-12-23'), (2,'YAKPYUNGWI','SCHEDULED','2026-12-03'),
                                             (3,'YAKPYUNGWI','COMPLETED','2026-06-04');
    """)
    assert fr.check_committee_schedule(_ctx(tmp_path, today=date(2026, 10, 20))).stale is False
    r = fr.check_committee_schedule(_ctx(tmp_path, today=date(2026, 12, 4)))     # YAKPYUNGWI 다음 세션 없음
    assert r.stale is True and "YAKPYUNGWI" in r.evidence and r.runbook is None


def test_committee_results_flags_past_scheduled_sessions(tmp_path):
    _seed(tmp_path / "d.db", """
        CREATE TABLE amjilsim_sessions (session_id INTEGER, committee_type TEXT, status TEXT, session_date TEXT);
        INSERT INTO amjilsim_sessions VALUES (1,'YAKPYUNGWI','SCHEDULED','2026-07-02'), (2,'YAKPYUNGWI','SCHEDULED','2026-10-15');
    """)
    r = fr.check_committee_results(_ctx(tmp_path))
    assert r.stale is True and r.observed == "2026-07-02" and r.runbook == "reimb_data_sync"


def test_market_share_expected_quarter():
    assert fr.expected_quarter(date(2026, 9, 30)) == "2026Q2"
    assert fr.expected_quarter(date(2026, 2, 15)) == "2025Q4"
    assert fr.expected_quarter(date(2026, 7, 1)) == "2026Q1"


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
    assert len(results) == len(fr.DOMAIN_CHECKS) == 14
    assert all(r.stale is False for r in results if r.evidence.startswith("판정 불가"))


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
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_domain_freshness.py -q`
Expected: FAIL — `ModuleNotFoundError: agents.agent_tasks.freshness`

- [ ] **Step 3: `coverage_gaps` 모듈 함수 분리 (`agents/foreign_approval/agent.py`)**

`list_coverage_gaps` 본문(`:122-153`)을 클래스 밖으로 옮기고 메서드는 위임한다. alias dict 는 그대로 옮긴다.

```python
# 클래스 정의 위에 추가
def coverage_gaps(db_path: Path) -> list[str]:
    """foreign_drug_prices 에 있지만 indications_master 에 없는 product slug (LOWER(query_name) 기준).

    ForeignApprovalAgent 를 만들지 않고도 부를 수 있게 모듈 함수 — 신선도 판정기(agents/agent_tasks/freshness.py)가 쓴다.
    __init__ 은 스크레이퍼 6개를 생성하므로 판정 목적에는 과하다.
    """
    with sqlite3.connect(str(db_path)) as c:
        price_slugs = {(r[0] or "").strip().lower() for r in c.execute(
            "SELECT DISTINCT query_name FROM foreign_drug_prices WHERE query_name IS NOT NULL").fetchall() if r[0]}
        approval_slugs = {(r[0] or "").strip().lower() for r in c.execute(
            "SELECT DISTINCT product FROM indications_master").fetchall() if r[0]}
    aliases: dict[str, str] = {
        "pembrolizumab": "keytruda", "belzutifan": "welireg", "olaparib": "lynparza",
        "lenvatinib": "lenvima", "sitagliptin": "januvia", "letermovir": "prevymis",
    }
    return sorted({aliases.get(s, s) for s in price_slugs} - approval_slugs)
```

```python
    # 클래스 안 — 기존 메서드를 위임으로 교체
    def list_coverage_gaps(self) -> list[str]:
        return coverage_gaps(self.db_path)
```

- [ ] **Step 4: 구현 — `agents/agent_tasks/freshness.py`**

```python
# agents/agent_tasks/freshness.py
"""도메인 신선도 판정기 (P1.5 Task 3).

잡이 '성공' 해도 데이터가 안 늘어나는 경우(폴백·빈 결과·수동 업로드 누락·잡 미등록)를 잡는다. 도메인마다
관측값(마지막 데이터 시점)과 기한(오늘 기준 언제까지는 있어야 하는가)을 계산해 stale 를 판정한다.
하루 1회 잡(scheduler.domain_freshness_job) + CLI(--freshness-now). 결과는 domain_freshness 테이블(도메인당 1행)에
기록하고 stale 만 원장 과제로 만든다.

중복 억제: source_ref = "{domain}:{YYYY-MM}" — 같은 달 안에서는 과제 하나(blocked 포함). 사람이 닫았는데 다음 날에도
stale 이면 다시 연다(고친 게 아니었다는 뜻). 새 달이 되면 다시 연다(월 상한 14건).
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from .jobs import resolve_db_path
from .signals import DEDUP_STATUSES, runbook_line
from .store import DEFAULT_DB_PATH, connect, create_task

logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))
REPO_ROOT = Path(__file__).resolve().parents[2]

XNATIONAL_MAX_DAYS = 100
SCHEDULE_HORIZON_DAYS = 60
RESULTS_LAG_DAYS = 14
ANALOG_MAX_DAYS = 75
MARKET_SHARE_LAG_DAYS = 90
POLICY_INGEST_MAX_DAYS = 21
POLICY_ANALYSIS_MAX_DAYS = 3
NEWS_MAX_DAYS = 3
TRENDS_MAX_DAYS = 7
SIGNALS_MAX_DAYS = 10
NHIS_MAX_DAYS = 10

DOMAIN_FRESHNESS_SCHEMA = """
CREATE TABLE IF NOT EXISTS domain_freshness (
    domain     TEXT PRIMARY KEY,
    checked_at TEXT NOT NULL,
    stale      INTEGER NOT NULL,
    observed   TEXT,
    deadline   TEXT,
    evidence   TEXT,
    runbook    TEXT
);
"""


@dataclass(frozen=True)
class Freshness:
    domain: str
    stale: bool
    observed: str | None      # 마지막 데이터 시점(원본 표기 그대로)
    deadline: str             # 이 값 이상이어야 신선
    evidence: str             # 사람용 한 줄
    runbook: str | None = None
    priority: int = 2


@dataclass(frozen=True)
class Ctx:
    db_path: Path
    base_dir: Path
    today: date
    policy_root: Path | None = None   # None 이면 agents.policy_intelligence._default_root()


def _scalar(ctx: Ctx, sql: str, params: tuple = ()):
    conn = sqlite3.connect(str(ctx.db_path))
    try:
        row = conn.execute(sql, params).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _rows(ctx: Ctx, sql: str, params: tuple = ()) -> list[tuple]:
    conn = sqlite3.connect(str(ctx.db_path))
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _days_ago(ctx: Ctx, days: int) -> str:
    return (ctx.today - timedelta(days=days)).isoformat()


def _max_ts(ctx: Ctx, domain: str, sql: str, max_days: int, runbook: str | None, what: str, priority: int = 2) -> Freshness:
    observed = _scalar(ctx, sql)
    deadline = _days_ago(ctx, max_days)
    stale = (str(observed or "")[:10]) < deadline
    return Freshness(domain, stale, observed, deadline, f"{what}={observed}, 기대≥{deadline} ({max_days}일)", runbook, priority)


# ── 1 국내약가 ─────────────────────────────────────────────────────────────

def _first_of(y: int, m: int) -> str:
    return f"{y:04d}.{m:02d}.01"


def check_domestic_price(ctx: Ctx) -> Freshness:
    observed = _scalar(ctx, "SELECT MAX(apply_date) FROM drug_prices")
    t = ctx.today
    if t.day >= 10:
        deadline = _first_of(t.year, t.month)
    else:
        deadline = _first_of(t.year if t.month > 1 else t.year - 1, t.month - 1 if t.month > 1 else 12)
    return Freshness("domestic_price", (observed or "") < deadline, observed, deadline,
                     f"drug_prices MAX(apply_date)={observed}, 기대≥{deadline} (HIRA 월초 고시, 1~7일 캐치업)",
                     "domestic_price_catchup")


# ── 2 환율 캐시 · 3 FX 정합 ────────────────────────────────────────────────

def _latest_fx_window_to(ctx: Ctx) -> str | None:
    files = sorted((ctx.base_dir / "data" / "foreign" / "exchange_rate").glob("keb_avg_rate_*.xlsx"))
    if not files:
        return None
    m = re.search(r"keb_avg_rate_(\d{8})_(\d{8})", files[-1].stem)
    return m.group(2) if m else None


def _last_day_of_prev_month(d: date) -> date:
    return d.replace(day=1) - timedelta(days=1)


def check_exchange_rate_cache(ctx: Ctx) -> Freshness:
    to = _latest_fx_window_to(ctx)
    ref = ctx.today if ctx.today.day >= 5 else _last_day_of_prev_month(ctx.today)
    deadline = _last_day_of_prev_month(ref).strftime("%Y%m%d")
    return Freshness("exchange_rate_cache", (to or "") < deadline, to, deadline,
                     f"KEB 캐시 창 종료일={to}, 기대≥{deadline} (매월 2일 갱신 잡)", "exchange_rate_refresh")


def check_foreign_price_fx_alignment(ctx: Ctx) -> Freshness:
    to = _latest_fx_window_to(ctx)
    if not to:
        return Freshness("foreign_price_fx_alignment", False, None, "0", "환율 캐시 없음 — exchange_rate_cache 가 먼저", None, 3)
    n = _scalar(ctx, """
        SELECT COUNT(*) FROM foreign_drug_prices f
        WHERE f.local_price IS NOT NULL AND f.exchange_rate_to IS NOT NULL AND f.exchange_rate_to != ?
          AND f.id = (SELECT MAX(g.id) FROM foreign_drug_prices g
                      WHERE g.query_name = f.query_name AND g.country = f.country AND g.local_price IS NOT NULL)""", (to,))
    return Freshness("foreign_price_fx_alignment", int(n or 0) > 0, str(n), "0",
                     f"최신 해외약가 행 중 캐시 환율창({to})과 다른 행 {n}건 — 환율 갱신 후 재계산 누락", "exchange_rate_recompute", 3)


# ── 4 해외 허가 커버리지 ───────────────────────────────────────────────────

def check_foreign_approval_coverage(ctx: Ctx) -> Freshness:
    from agents.foreign_approval.agent import coverage_gaps
    gaps = coverage_gaps(ctx.db_path)
    return Freshness("foreign_approval_coverage", bool(gaps), str(len(gaps)), "0",
                     f"가격은 있으나 허가 master 없는 제품 {len(gaps)}건: {gaps[:10]}", "foreign_approval_sync")


# ── 5 해외 급여 ────────────────────────────────────────────────────────────

def check_reimbursement_xnational(ctx: Ctx) -> Freshness:
    r = _max_ts(ctx, "reimbursement_xnational", "SELECT MAX(fetched_at) FROM reimbursement_xnational",
                XNATIONAL_MAX_DAYS, "reimbursement_xnational_sync", "MAX(fetched_at)")
    au = _scalar(ctx, "SELECT COUNT(*) FROM reimbursement_xnational WHERE country='AU'")
    return Freshness(r.domain, r.stale, r.observed, r.deadline, f"{r.evidence}; AU/PBAC 행 {au}건", r.runbook)


# ── 6 위원회 일정 · 7 위원회 결과 ──────────────────────────────────────────

def check_committee_schedule(ctx: Ctx) -> Freshness:
    horizon = (ctx.today + timedelta(days=SCHEDULE_HORIZON_DAYS)).isoformat()
    counts = {"AMJILSIM": 0, "YAKPYUNGWI": 0}
    for committee, n in _rows(ctx, "SELECT committee_type, COUNT(*) FROM amjilsim_sessions WHERE status='SCHEDULED' "
                                   "AND session_date BETWEEN ? AND ? GROUP BY committee_type",
                              (ctx.today.isoformat(), horizon)):
        counts[committee] = n
    missing = [k for k, v in counts.items() if v == 0]
    last = _scalar(ctx, "SELECT MAX(session_date) FROM amjilsim_sessions WHERE status='SCHEDULED'")
    return Freshness("committee_schedule", bool(missing), last, horizon,
                     f"향후 {SCHEDULE_HORIZON_DAYS}일 SCHEDULED 세션 {counts} — 0건 위원회 {missing}. "
                     "HIRA 일정 fetcher 는 placeholder(미구현) → 사람이 amjilsim_sessions 에 차수 입력", None)


def check_committee_results(ctx: Ctx) -> Freshness:
    cutoff = _days_ago(ctx, RESULTS_LAG_DAYS)
    n = _scalar(ctx, "SELECT COUNT(*) FROM amjilsim_sessions WHERE status='SCHEDULED' AND session_date < ?", (cutoff,))
    oldest = _scalar(ctx, "SELECT MIN(session_date) FROM amjilsim_sessions WHERE status='SCHEDULED' AND session_date < ?", (cutoff,))
    return Freshness("committee_results", int(n or 0) > 0, oldest, cutoff,
                     f"{RESULTS_LAG_DAYS}일 넘게 지난 세션이 SCHEDULED 로 남음 {n}건(최초 {oldest}) — 위원회 결과(committee_results.json) 미반영",
                     "reimb_data_sync")


# ── 8 아날로그 · 9 마켓셰어 ────────────────────────────────────────────────

def check_analog_reports(ctx: Ctx) -> Freshness:
    r = _max_ts(ctx, "analog_reports", "SELECT MAX(session_date) FROM analog_reports", ANALOG_MAX_DAYS, None,
                "analog_reports MAX(session_date)", 3)
    return Freshness(r.domain, r.stale, r.observed, r.deadline,
                     r.evidence + " — 약평위 결과 PDF 는 Mac 에서 apply_analog_sync 로 수동 반영", None, 3)


def expected_quarter(today: date) -> str:
    ref = today - timedelta(days=MARKET_SHARE_LAG_DAYS)
    q = (ref.month - 1) // 3          # ref 가 속한 분기 전에 끝난 분기 수
    return f"{ref.year - 1}Q4" if q == 0 else f"{ref.year}Q{q}"


def check_market_share(ctx: Ctx) -> Freshness:
    last = _scalar(ctx, "SELECT MAX(quarter) FROM market_share_quarterly")
    exp = expected_quarter(ctx.today)
    return Freshness("market_share", (last or "") < exp, last, exp,
                     f"market_share_quarterly MAX(quarter)={last}, 기대≥{exp} (분기 종료 +{MARKET_SHARE_LAG_DAYS}일) — admin 엑셀 업로드", None, 3)


# ── 10 정책 인텔리전스 ─────────────────────────────────────────────────────

def check_policy_intelligence(ctx: Ctx) -> Freshness:
    if ctx.policy_root is not None:
        root = ctx.policy_root
    else:
        from agents.policy_intelligence import _default_root
        root = _default_root()
    status = root / "manifests" / "latest_ingest_status.json"
    ran_at = None
    if status.exists():
        try:
            ran_at = json.loads(status.read_text(encoding="utf-8")).get("ran_at")
        except Exception:
            ran_at = None
    ingest_deadline = _days_ago(ctx, POLICY_INGEST_MAX_DAYS)
    ingest_stale = (ran_at or "")[:10] < ingest_deadline
    manifest = root / "analysis" / "analysis_manifest.json"
    a_date = datetime.fromtimestamp(manifest.stat().st_mtime).date().isoformat() if manifest.exists() else None
    analysis_deadline = _days_ago(ctx, POLICY_ANALYSIS_MAX_DAYS)
    analysis_stale = (a_date or "") < analysis_deadline
    evidence = (f"ingest ran_at={ran_at} (기대≥{ingest_deadline}); analysis manifest={a_date} (기대≥{analysis_deadline})"
                + ("" if status.exists() else " — latest_ingest_status.json 없음: /api/policy-intelligence/overview 500 (agents/policy_intelligence.py:145-146)"))
    return Freshness("policy_intelligence", ingest_stale or analysis_stale, ran_at,
                     ingest_deadline if ingest_stale else analysis_deadline, evidence,
                     None if ingest_stale else "policy_intel_analysis_sync")


# ── 11~14 뉴스·동향·신호·NHIS ──────────────────────────────────────────────

def check_news_archive(ctx: Ctx) -> Freshness:
    return _max_ts(ctx, "news_archive",
                   "SELECT MAX(fetched_at) FROM competitor_news WHERE kind IN ('competitor','gov_policy')",
                   NEWS_MAX_DAYS, "news_daily_fetch", "competitor_news MAX(fetched_at)")


def check_competitor_trends(ctx: Ctx) -> Freshness:
    return _max_ts(ctx, "competitor_trends",
                   "SELECT MAX(updated_at) FROM competitor_trend WHERE source_type='promoted'",
                   TRENDS_MAX_DAYS, "competitor_trend_promote", "competitor_trend(promoted) MAX(updated_at)", 3)


def check_access_insight_signals(ctx: Ctx) -> Freshness:
    return _max_ts(ctx, "access_insight_signals", "SELECT MAX(crawled_at) FROM amjilsim_media_signals",
                   SIGNALS_MAX_DAYS, "access_insight_fresh_signals", "amjilsim_media_signals MAX(crawled_at)", 3)


def check_nhis_negotiations(ctx: Ctx) -> Freshness:
    return _max_ts(ctx, "nhis_negotiations", "SELECT MAX(fetched_at) FROM nhis_negotiations",
                   NHIS_MAX_DAYS, "nhis_negotiation_sync", "nhis_negotiations MAX(fetched_at)", 3)


DOMAIN_CHECKS: dict[str, Callable[[Ctx], Freshness]] = {
    "domestic_price": check_domestic_price,
    "exchange_rate_cache": check_exchange_rate_cache,
    "foreign_price_fx_alignment": check_foreign_price_fx_alignment,
    "foreign_approval_coverage": check_foreign_approval_coverage,
    "reimbursement_xnational": check_reimbursement_xnational,
    "committee_schedule": check_committee_schedule,
    "committee_results": check_committee_results,
    "analog_reports": check_analog_reports,
    "market_share": check_market_share,
    "policy_intelligence": check_policy_intelligence,
    "news_archive": check_news_archive,
    "competitor_trends": check_competitor_trends,
    "access_insight_signals": check_access_insight_signals,
    "nhis_negotiations": check_nhis_negotiations,
}


def run_all(ctx: Ctx) -> list[Freshness]:
    out: list[Freshness] = []
    for domain, fn in DOMAIN_CHECKS.items():
        try:
            out.append(fn(ctx))
        except Exception as exc:   # 테이블 부재·파일 권한 등 — 판정 불가는 과제가 아니다(로그로만)
            logger.warning("[freshness] %s 판정 불가: %s", domain, exc)
            out.append(Freshness(domain, False, None, "", f"판정 불가: {type(exc).__name__}: {exc}"[:300]))
    return out


def write_domain_freshness(results: list[Freshness], *, db_path: str | Path = DEFAULT_DB_PATH) -> None:
    conn = connect(db_path)
    try:
        conn.executescript(DOMAIN_FRESHNESS_SCHEMA)
        now = datetime.now(timezone.utc).isoformat()
        for r in results:
            conn.execute("INSERT INTO domain_freshness (domain, checked_at, stale, observed, deadline, evidence, runbook) "
                         "VALUES (?,?,?,?,?,?,?) ON CONFLICT(domain) DO UPDATE SET checked_at=excluded.checked_at, "
                         "stale=excluded.stale, observed=excluded.observed, deadline=excluded.deadline, "
                         "evidence=excluded.evidence, runbook=excluded.runbook",
                         (r.domain, now, 1 if r.stale else 0, r.observed, r.deadline, r.evidence, r.runbook))
        conn.commit()
    finally:
        conn.close()


def record_freshness_problems(results: list[Freshness], *, today: date,
                              db_path: str | Path = DEFAULT_DB_PATH) -> list[int]:
    ids: list[int] = []
    for r in results:
        if not r.stale:
            continue
        ids.append(create_task(
            kind="ops_incident", source="domain_freshness", source_ref=f"{r.domain}:{today:%Y-%m}",
            title=f"데이터 신선도 — {r.domain} stale",
            detail=f"{r.evidence}\n관측={r.observed} 기한={r.deadline}\n{runbook_line(r.runbook)}",
            priority=r.priority, actor="freshness", dedup_statuses=DEDUP_STATUSES, db_path=db_path))
    return ids


def run_and_record(*, db_path: str | Path | None = None, base_dir: Path = REPO_ROOT, today: date | None = None) -> dict:
    """잡·CLI 진입점. stale 도메인은 과제이지 잡 실패가 아니다(ok=True) — 아니면 domain_freshness:{날짜} 과제가 하나 더 생긴다."""
    ctx = Ctx(db_path=Path(db_path) if db_path else resolve_db_path(), base_dir=Path(base_dir),
              today=today or datetime.now(KST).date())
    results = run_all(ctx)
    write_domain_freshness(results, db_path=ctx.db_path)
    ids = record_freshness_problems(results, today=ctx.today, db_path=ctx.db_path)
    return {"ok": True,
            "counts": {"checked": len(results), "stale": sum(1 for r in results if r.stale), "tasks": len(ids)},
            "stale_domains": [r.domain for r in results if r.stale]}
```

- [ ] **Step 5: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_domain_freshness.py -q`
Expected: 12 passed

- [ ] **Step 6: 잡 등록 + CLI + API**

`scheduler.py` — 잡 함수(`home_brand_related_refresh_job` 아래):

```python
@guarded_job("domain_freshness")
def domain_freshness_job():
    """매일 07:45 Seoul — 14개 데이터 도메인 신선도 판정 → stale 은 원장 과제(월 1건). 02:00~07:00 수집 잡 뒤, 업무 전."""
    from agents.agent_tasks.freshness import run_and_record
    res = run_and_record(base_dir=BASE_DIR)
    logger.info("도메인 신선도: %s (stale=%s)", res["counts"], res["stale_domains"])
    return res
```

`build_job_specs` 에 1행(`home_brand_related_refresh` 다음):

```python
        JobSpec("domain_freshness", "데이터 도메인 신선도 판정 (매일 07:45, stale → 원장)", domain_freshness_job,
                _cron(hour=7, minute=45)),
```

CLI(`main()` 의 argparse 와 분기):

```python
    parser.add_argument("--freshness-now", action="store_true",
                        help="14개 도메인 신선도 즉시 판정 + stale 과제 생성 (표 출력)")
    ...
    if args.freshness_now:
        from agents.agent_tasks.freshness import Ctx, run_all
        from agents.agent_tasks.jobs import resolve_db_path
        from datetime import date as _date
        for r in run_all(Ctx(db_path=resolve_db_path(), base_dir=BASE_DIR, today=_date.today())):
            print(f"{'STALE' if r.stale else 'ok   '} {r.domain:28s} 관측={r.observed} 기한={r.deadline} — {r.evidence}")
        res = domain_freshness_job()
        logger.info("신선도 판정 결과: %s", res)
        return
```

`agents/agent_tasks/signals.py` `JOB_RUNBOOK` 에 `"domain_freshness": None,` 추가.

`api/server.py:2106-2117` 교체:

```python
@app.get("/api/data/freshness")
def data_freshness():
    """데이터 신선도 조회.
    - ?type=hta&key=belzutifan_FDA : 항목별 수집 시각(data_freshness)
    - ?type=domain                 : 도메인별 최신 판정(domain_freshness, agents/agent_tasks/freshness.py)
    - 파라미터 없음                 : data_freshness 전체
    """
    data_type = request.args.get("type", "").strip()
    scope_key = request.args.get("key", "").strip()
    if data_type == "domain":
        from agents.agent_tasks.freshness import DOMAIN_FRESHNESS_SCHEMA
        with db._connect() as conn:
            conn.executescript(DOMAIN_FRESHNESS_SCHEMA)
            rows = conn.execute("SELECT * FROM domain_freshness ORDER BY stale DESC, domain").fetchall()
        return jsonify([dict(r) for r in rows])
    if data_type and scope_key:
        info = db.get_freshness(data_type, scope_key)
        return jsonify(info or {"status": "not_found"})
    with db._connect() as conn:
        rows = conn.execute("SELECT * FROM data_freshness ORDER BY last_fetched DESC").fetchall()
    return jsonify([dict(r) for r in rows])
```

- [ ] **Step 7: 로컬 실측 (네트워크 없음, 읽기 위주 — `AGENT_TASK_DB` 를 임시 파일로 두고 실제 DB 는 읽기만)**

Run: `AGENT_TASK_DB=/tmp/p15_freshness.db .venv/bin/python scheduler.py --freshness-now`
Expected: 14행 출력, `STALE` 11행(1·2·5·7·8·9·10·11·12·13·14), `counts={'checked': 14, 'stale': 11, 'tasks': 11}`. (`policy_intelligence` 는 로컬 root 가 `/opt/data/policy_intelligence` 라 `POLICY_INTELLIGENCE_ROOT=data/policy_intelligence` 를 함께 주면 표의 값과 같다.)

- [ ] **Step 8: 통과 확인 + 커밋**

Run: `.venv/bin/python -m pytest tests/test_domain_freshness.py tests/test_scheduler_job_specs.py -q`
Expected: PASS (`test_job_ids_match_runbook_map_exactly` 는 `JOB_RUNBOOK` 1행 추가로 통과).

```bash
git add agents/agent_tasks/freshness.py agents/foreign_approval/agent.py scheduler.py agents/agent_tasks/signals.py \
        api/server.py tests/test_domain_freshness.py
git commit -m "feat(agent-team): 도메인 신선도 판정기 — 14 도메인, 월 1건 과제, domain_freshness 테이블 + API"
```

**완료 기준 (T3)**
- 14개 도메인 판정 함수 + 테스트 12개 통과, 로컬 실측이 규칙표와 일치(11 stale).
- **오탐 억제**: source_ref 월 단위 + blocked 포함 → 도메인당 월 최대 1건(사람이 닫고 여전히 stale 이면 다음 날 재개). 프로덕션 첫 실행 **6건 ±1**, 이후 새 달 1일에 stale 인 도메인만 재개. "판정 불가"(테이블·파일 없음)는 과제를 만들지 않는다.
- 런북 힌트 10종은 Task 5 의 등록 이름과 문자열이 같다(`DOMAIN_CHECKS` 의 runbook 값 ⊆ `runbooks.list_runbooks()` — Task 5 테스트가 대조).

---

### Task 4: 생존 헬스 (`/api/health` + heartbeat + Fly check) + 화면별 스모크

**Files:**
- Create: `agents/agent_tasks/heartbeat.py`, `scripts/smoke.py`, `tests/test_health_endpoint.py`, `tests/test_smoke_screens.py`
- Modify: `api/server.py:7309-7311` (`/api/health`), `fly.toml` (`[[http_service.checks]]`), `scheduler.py` (`scheduler_heartbeat_job`·`platform_smoke_job` + `JobSpec` 2행 + `--smoke-now`), `agents/agent_tasks/signals.py` (`JOB_RUNBOOK` 2행: `scheduler_heartbeat`, `platform_smoke`)

설계 결정:
- **heartbeat 는 파일**(`data/scheduler_heartbeat.json`, 볼륨). DB 가 아니라 파일인 이유: 60초마다 sqlite 쓰기 잠금을 만들지 않고, DB 가 잠겨도 생존 신호는 남는다. 스케줄러의 60초 interval 잡이 atomic rename 으로 쓴다(APScheduler 기본 ThreadPool 10 워커라 긴 Playwright 잡과 병렬).
- **`/api/health` 는 503 을 낸다** — `db_ok=False` 이거나 heartbeat 가 15분 넘게 오래됐을 때(부팅 5분 유예). Fly HTTP check 가 503 을 보면 머신을 재시작한다 → `start_production.sh` 가 스케줄러를 다시 띄운다. 이것이 "스케줄러 스레드 hang 을 아무도 모른다" 의 답이다. 웹 프로세스는 stale 을 볼 때 원장에 하루 1건 기록한다(재시작이 원장에 흔적을 남기게).
- **스모크는 스케줄러 프로세스 안에서** `http://127.0.0.1:5001` 을 친다(같은 컨테이너, 실제 웹 프로세스 검증). 토큰은 `api.auth._issue_token` 으로 직접 발급 — `JWT_SECRET` 이 fly secret(환경변수)이라 두 프로세스가 같은 비밀을 갖는다(`api/auth.py:28-42`). **스모크 계정은 필요 없다.** VPS·로컬에서 외부 실행할 때만 `--login-email/--login-password`(관리자가 `POST /api/admin/users` 로 만든 admin 계정 — admin 은 user 라우트도 통과) 를 쓴다. 로컬에서 `JWT_SECRET` 이 `config/.env` 에도 환경에도 없으면 프로세스마다 임시 비밀이라 발급 토큰이 401 — 스모크는 이를 `skipped` 로 표시한다(오탐 아님).

- [ ] **Step 1: 실패 테스트 — heartbeat 와 `/api/health`**

```python
# tests/test_health_endpoint.py
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import api.server as s
from agents.agent_tasks import heartbeat as hb
from agents.agent_tasks.store import list_tasks


def test_write_and_status_fresh(tmp_path):
    p = tmp_path / "hb.json"
    hb.write_heartbeat(pid=123, path=p)
    st = hb.heartbeat_status(now=time.time(), process_started_at=time.time() - 1000, path=p)
    assert st["stale"] is False and 0 <= st["age_s"] < 5


def test_status_stale_after_threshold(tmp_path):
    p = tmp_path / "hb.json"
    hb.write_heartbeat(pid=1, path=p)
    old = time.time() - hb.STALE_AFTER_S - 10
    os.utime(p, (old, old))
    assert hb.heartbeat_status(now=time.time(), process_started_at=time.time() - 1000, path=p)["stale"] is True


def test_missing_file_has_boot_grace(tmp_path):
    p = tmp_path / "hb.json"
    assert hb.heartbeat_status(now=time.time(), process_started_at=time.time() - 10, path=p)["stale"] is False
    assert hb.heartbeat_status(now=time.time(), process_started_at=time.time() - hb.BOOT_GRACE_S - 1, path=p)["stale"] is True


def test_health_ok_shape(monkeypatch, tmp_path):
    p = tmp_path / "hb.json"
    hb.write_heartbeat(pid=1, path=p)
    monkeypatch.setattr(hb, "HEARTBEAT_PATH", p)
    c = s.app.test_client()
    r = c.get("/api/health")
    body = r.get_json()
    assert r.status_code == 200 and body["status"] == "ok" and body["db_ok"] is True
    assert body["scheduler_heartbeat_age_s"] < 5 and isinstance(body["frontend_build"], bool)
    assert isinstance(body["jobs_failed_24h"], list) and "available_countries" in body


def test_health_503_when_scheduler_stale_and_records_once(monkeypatch, tmp_path):
    p = tmp_path / "hb.json"
    hb.write_heartbeat(pid=1, path=p)
    old = time.time() - hb.STALE_AFTER_S - 10
    os.utime(p, (old, old))
    monkeypatch.setattr(hb, "HEARTBEAT_PATH", p)
    db = tmp_path / "a.db"
    monkeypatch.setenv("AGENT_TASK_DB", str(db))
    c = s.app.test_client()
    assert c.get("/api/health").status_code == 503
    assert c.get("/api/health").status_code == 503
    tasks = list_tasks(db_path=db)
    assert len(tasks) == 1 and tasks[0]["source_ref"].startswith("scheduler_heartbeat:")


def test_health_503_when_db_down(monkeypatch, tmp_path):
    p = tmp_path / "hb.json"
    hb.write_heartbeat(pid=1, path=p)
    monkeypatch.setattr(hb, "HEARTBEAT_PATH", p)
    monkeypatch.setattr(s, "_health_db_ok", lambda: False)
    r = s.app.test_client().get("/api/health")
    assert r.status_code == 503 and r.get_json()["db_ok"] is False
```

- [ ] **Step 2: 실패 확인** — `.venv/bin/python -m pytest tests/test_health_endpoint.py -q` → FAIL (`ModuleNotFoundError: agents.agent_tasks.heartbeat`)

- [ ] **Step 3: 구현 — `agents/agent_tasks/heartbeat.py`**

```python
# agents/agent_tasks/heartbeat.py
"""스케줄러 생존 신호 (P1.5 Task 4).

스케줄러 프로세스가 60초마다 파일을 atomic 으로 갱신하고, 웹 프로세스의 /api/health 가 파일 mtime 으로 나이를 잰다.
파일인 이유: sqlite 잠금과 무관하게 남고(DB 가 잠겨도 '살아 있다' 는 보인다), 볼륨(data/)에 있어 재배포 뒤에도
직전 값이 남아 부팅 직후 오탐이 없다.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HEARTBEAT_PATH = REPO_ROOT / "data" / "scheduler_heartbeat.json"
STALE_AFTER_S = 900     # 15분: 60초 잡이 15번 연속 못 돌면 스케줄러가 멈춘 것
BOOT_GRACE_S = 300      # 부팅 5분 안에는 파일이 없어도 정상(첫 볼륨)


def write_heartbeat(*, pid: int, path: Path | None = None) -> None:
    path = path or HEARTBEAT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"ts": time.time(), "pid": pid}), encoding="utf-8")
    os.replace(tmp, path)


def heartbeat_status(*, now: float, process_started_at: float, path: Path | None = None) -> dict:
    """{'age_s': float|None, 'stale': bool}. 파일 없음은 부팅 유예 뒤에만 stale."""
    path = path or HEARTBEAT_PATH
    try:
        age = now - path.stat().st_mtime
    except FileNotFoundError:
        age = None
    uptime = now - process_started_at
    stale = (age is None and uptime > BOOT_GRACE_S) or (age is not None and age > STALE_AFTER_S)
    return {"age_s": None if age is None else round(age, 1), "stale": stale}
```

`api/server.py:7309-7311` 교체(파일 상단 import 에 `import time` 이 없으면 추가):

```python
_PROCESS_STARTED_AT = time.time()


def _health_db_ok() -> bool:
    try:
        with db._connect() as conn:
            conn.execute("SELECT 1").fetchone()
        return True
    except Exception as e:
        logger.error("health: DB 접근 실패: %s", e)
        return False


def _health_record_scheduler_stale(status: dict) -> None:
    """heartbeat stale 을 원장에 하루 1건 남긴다 — Fly 가 재시작해도 '그런 일이 있었다' 가 원장에 보이게. 실패는 삼킨다."""
    try:
        from agents.agent_tasks.jobs import resolve_db_path
        from agents.agent_tasks.signals import record_job_failure
        record_job_failure("scheduler_heartbeat", f"heartbeat age={status['age_s']}s (>{900}s) — 스케줄러 정지/hang 의심",
                           db_path=resolve_db_path())
    except Exception as e:
        logger.warning("health: heartbeat stale 신호 기록 실패: %s", e)


@app.get("/api/health")
def health():
    """생존 헬스. 503 = DB 불가 또는 스케줄러 heartbeat 15분 초과(부팅 5분 유예) → Fly 가 머신을 재시작한다(fly.toml checks)."""
    from agents.agent_tasks import heartbeat as _hb
    from agents.agent_tasks.jobs import recent_job_runs
    db_ok = _health_db_ok()
    st = _hb.heartbeat_status(now=time.time(), process_started_at=_PROCESS_STARTED_AT, path=_hb.HEARTBEAT_PATH)
    failed: list[str] = []
    try:
        failed = [r["job_id"] for r in recent_job_runs(hours=24) if not r["ok"]]
    except Exception as e:
        logger.warning("health: job run 조회 실패: %s", e)
    body = {
        "status": "ok" if (db_ok and not st["stale"]) else "degraded",
        "db_ok": db_ok,
        "scheduler_heartbeat_age_s": st["age_s"],
        "scheduler_stale": st["stale"],
        "frontend_build": (_SPA_DIR / "index.html").exists(),
        "jobs_failed_24h": failed,
        "available_countries": AVAILABLE_COUNTRIES,
    }
    if st["stale"]:
        _health_record_scheduler_stale(st)
    return jsonify(body), (200 if body["status"] == "ok" else 503)
```

(`recent_job_runs` 는 `AGENT_TASK_DB` 가 없으면 기본 원장 DB 를 읽는다 — 웹 프로세스에서 `db` 와 같은 파일이다.)

`scheduler.py` 잡 + spec:

```python
@guarded_job("scheduler_heartbeat", record_runs=False)
def scheduler_heartbeat_job():
    """60초 — 생존 파일 갱신. /api/health 가 mtime 으로 판정(15분 초과 → 503 → Fly 재시작)."""
    from agents.agent_tasks.heartbeat import write_heartbeat
    write_heartbeat(pid=os.getpid())
    return None
```

```python
        JobSpec("scheduler_heartbeat", "스케줄러 heartbeat (60초, /api/health 생존 판정)", scheduler_heartbeat_job,
                IntervalTrigger(seconds=60, timezone="Asia/Seoul"), misfire_grace_time=30),
```

`fly.toml` `[http_service]` 블록 아래에 추가:

```toml
  # /api/health 는 DB 불가·스케줄러 heartbeat 15분 초과 시 503 → 머신 재시작(start_production.sh 가 둘 다 다시 띄움).
  # grace 120s: 부팅 sync(reimb --amjilsim-daily-crawl-now) + 스케줄러 기동 시간.
  [[http_service.checks]]
    grace_period = "120s"
    interval = "30s"
    timeout = "5s"
    method = "GET"
    path = "/api/health"
```

`agents/agent_tasks/signals.py` `JOB_RUNBOOK` 에 `"scheduler_heartbeat": None,` 과 `"platform_smoke": None,` 추가.

- [ ] **Step 4: 통과 확인** — `.venv/bin/python -m pytest tests/test_health_endpoint.py -q` → 6 passed

- [ ] **Step 5: 실패 테스트 — 스모크**

```python
# tests/test_smoke_screens.py
from __future__ import annotations

import io
import json
import urllib.error
import urllib.request

from scripts import smoke


def _fake_opener(routes: dict):
    """path → (status, body). 등록 안 된 경로는 404."""
    def opener(req, timeout=10):
        path = req.full_url.split("http://test", 1)[1]
        status, body = routes.get(path, (404, {"error": "nf"}))
        raw = json.dumps(body).encode()
        if status >= 400:
            raise urllib.error.HTTPError(req.full_url, status, "err", {}, io.BytesIO(raw))
        resp = io.BytesIO(raw)
        resp.status = status
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda s, *a: None
        return resp
    return opener


def test_screen_table_covers_all_routes():
    names = {s.name for s in smoke.SCREENS}
    user = {"login", "home", "domestic-pricing", "regimen-cost", "international-pricing", "market-share",
            "competitor-trends", "daily-mailing", "reimbursement-status", "policy-intelligence", "analog-search", "my-requests"}
    admin = {"admin/market-share", "admin/msd-pipeline", "admin/competitor-trends", "admin/reimbursement",
             "admin/reimbursement-pipeline", "admin/daily-mailing-kanban", "admin/home-brands",
             "admin/service-requests", "admin/agent-tasks"}
    assert user <= names and admin <= names and "auth-gates" in names
    assert sum(len(s.checks) for s in smoke.SCREENS) >= 40


def test_run_smoke_reports_failures_per_check():
    ok_routes = {c.path: (200, {"tasks": []} if "agent-tasks" in c.path else []) for s in smoke.SCREENS for c in s.checks}
    ok_routes["/api/auth/me"] = (401, {"error": "missing token"})
    ok_routes["/api/admin/agent-tasks"] = (200, {"tasks": []})
    # auth-gates: user 토큰으로 admin 라우트는 403 이어야 한다 → 가짜 서버는 헤더를 보고 403
    def opener(req, timeout=10):
        path = req.full_url.split("http://test", 1)[1]
        if path == "/api/admin/agent-tasks" and req.get_header("Authorization") == "Bearer user-token":
            raise urllib.error.HTTPError(req.full_url, 403, "forbidden", {}, io.BytesIO(b'{"error":"admin required"}'))
        return _fake_opener(ok_routes)(req, timeout)
    res = smoke.run_smoke("http://test", lambda role: f"{role}-token" if role else None, opener=opener)
    assert res["ok"] is True and res["counts"]["failed"] == 0 and res["errors"] == []

    broken = dict(ok_routes)
    broken["/api/policy-intelligence/overview"] = (500, {"error": "manifest not found"})
    res2 = smoke.run_smoke("http://test", lambda role: f"{role}-token" if role else None,
                           opener=lambda req, timeout=10: opener(req, timeout) if "overview" not in req.full_url
                           else _fake_opener(broken)(req, timeout))
    assert res2["ok"] is False
    assert res2["errors"] == ["policy-intelligence /api/policy-intelligence/overview → HTTP 500"]


def test_run_smoke_skips_when_token_unavailable():
    res = smoke.run_smoke("http://test", lambda role: None, opener=_fake_opener({"/api/auth/me": (401, {})}))
    assert res["counts"]["skipped"] > 0 and all("skipped" not in e for e in res["errors"])
```

- [ ] **Step 6: 실패 확인** — `.venv/bin/python -m pytest tests/test_smoke_screens.py -q` → FAIL (`ModuleNotFoundError: scripts.smoke`)

- [ ] **Step 7: 구현 — `scripts/smoke.py`**

```python
# scripts/smoke.py
"""플랫폼 스모크 — 화면별 API 세트를 실제 서버에 쳐서 기대 조건을 확인한다 (P1.5 Task 4).

in-process(스케줄러 잡 platform_smoke, 매일 07:50): base=http://127.0.0.1:5001, 토큰은 공유 JWT_SECRET 으로 직접 발급.
외부(VPS·로컬 수동): python scripts/smoke.py --base https://ma-ai-dossier.fly.dev --login-email ... --login-password ...
  (관리자가 만든 admin 계정 — admin 은 user 라우트도 통과한다. 새 계정 생성은 사용자 몫: VPS_SETUP.md §1 과 같은 절차)
기대 조건은 '200 + JSON' 이 기본이고 화면별로 키 존재·상태코드를 덧붙인다. 데이터 내용은 검사하지 않는다(신선도는 Task 3).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger(__name__)
DEFAULT_BASE = "http://127.0.0.1:5001"
Expect = Callable[[int, object], "str | None"]     # (status, body) → 실패 사유 | None


def _json_ok(status: int, body) -> str | None:
    return None if status == 200 and isinstance(body, (dict, list)) else f"HTTP {status}"


def _has_key(key: str) -> Expect:
    return lambda status, body: None if status == 200 and isinstance(body, dict) and key in body else f"HTTP {status} / '{key}' 없음"


def _status(code: int) -> Expect:
    return lambda status, body: None if status == code else f"HTTP {status} (기대 {code})"


@dataclass(frozen=True)
class Check:
    path: str
    expect: Expect = _json_ok


@dataclass(frozen=True)
class Screen:
    name: str
    role: str | None          # None = 토큰 없이, "user"/"admin" = 그 역할 토큰
    checks: tuple[Check, ...]


SCREENS: tuple[Screen, ...] = (
    # ── 사용자 12 화면 (frontend/src/router/config.tsx:27-38) ──
    Screen("login", None, (Check("/api/auth/me", _status(401)),)),                       # 인증 게이트 생존
    Screen("home", None, (Check("/api/home/top-price-changes?limit=5"), Check("/api/msd/summary"),
                          Check("/api/home/media-intelligence"), Check("/api/home/government-keyword-summary"))),
    Screen("domestic-pricing", None, (Check("/api/domestic/price-changes?q=키트루다"),)),
    Screen("regimen-cost", "user", (Check("/api/regimen/onco/search?q=pembrolizumab"), Check("/api/regimen-comparisons"))),
    Screen("international-pricing", None, (Check("/api/foreign/drugs"), Check("/api/foreign/cached?q=keytruda"),
                                           Check("/api/hta/approvals?drug=pembrolizumab"))),
    Screen("market-share", None, (Check("/api/market-share/search?q=키트루다"),)),
    Screen("competitor-trends", "user", (Check("/api/competitor-trends"), Check("/api/competitor-news?limit=5"),
                                         Check("/api/access-insight/drugs"))),
    Screen("daily-mailing", "user", (Check("/api/mail-subscriptions"),)),
    Screen("reimbursement-status", "user", (Check("/api/reimbursement/meetings"), Check("/api/reimbursement/pipeline"),
                                            Check("/api/reimbursement/reports"), Check("/api/reimbursement/nhis-negotiations"))),
    Screen("policy-intelligence", "user", (Check("/api/policy-intelligence/overview"), Check("/api/policy-intelligence/events"),
                                           Check("/api/policy-intelligence/ingest-status"))),
    Screen("analog-search", "user", (Check("/api/analog/facets"), Check("/api/analog/search?q=키트루다"))),
    Screen("my-requests", "user", (Check("/api/service-requests/mine"),)),
    # ── 관리자 9 화면 (config.tsx:39-47) ──
    Screen("admin/market-share", "admin", (Check("/api/admin/market-share/uploads"),)),
    Screen("admin/msd-pipeline", "admin", (Check("/api/msd/pipeline"),)),
    Screen("admin/competitor-trends", "admin", (Check("/api/competitor-trends"),)),
    Screen("admin/reimbursement", "admin", (Check("/api/admin/reimbursement?product=keytruda"),)),
    Screen("admin/reimbursement-pipeline", "admin", (Check("/api/admin/reimb-pipeline/drugs"),
                                                     Check("/api/admin/reimb-pipeline/nhis-unmatched"))),
    Screen("admin/daily-mailing-kanban", "admin", (Check("/api/admin/daily-mailing/kanban"), Check("/api/admin/daily-mailing/health"))),
    Screen("admin/home-brands", "admin", (Check("/api/admin/home-brands"),)),
    Screen("admin/service-requests", "admin", (Check("/api/admin/service-requests"), Check("/api/admin/service-requests/outbox/count"))),
    Screen("admin/agent-tasks", "admin", (Check("/api/admin/agent-tasks", _has_key("tasks")),)),
    # ── 권한 경계 ──
    Screen("auth-gates", "user", (Check("/api/admin/agent-tasks", _status(403)),)),
)


def _get(base: str, path: str, token: str | None, opener, timeout: float) -> tuple[int, object]:
    req = urllib.request.Request(f"{base}{path}")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with opener(req, timeout=timeout) as resp:
            raw = resp.read()
            status = getattr(resp, "status", 200)
    except urllib.error.HTTPError as exc:
        raw = exc.read() if hasattr(exc, "read") else b""
        status = exc.code
    try:
        body = json.loads(raw.decode("utf-8")) if raw else None
    except Exception:
        body = None
    return status, body


def run_smoke(base: str, token_for: Callable[[str | None], str | None], *,
              opener=urllib.request.urlopen, timeout: float = 10.0) -> dict:
    """모든 화면을 돌고 {ok, counts, errors} 를 돌려준다(잡 규약). 토큰을 못 만든 역할의 화면은 skipped."""
    errors: list[str] = []
    passed = skipped = 0
    for screen in SCREENS:
        token = token_for(screen.role) if screen.role else None
        if screen.role and not token:
            skipped += len(screen.checks)
            continue
        for check in screen.checks:
            try:
                status, body = _get(base, check.path, token, opener, timeout)
                why = check.expect(status, body)
            except Exception as exc:
                why = f"{type(exc).__name__}: {exc}"[:200]
            if why:
                errors.append(f"{screen.name} {check.path} → {why}")
            else:
                passed += 1
    return {"ok": not errors,
            "counts": {"screens": len(SCREENS), "passed": passed, "failed": len(errors), "skipped": skipped},
            "errors": errors}


def mint_token(role: str | None) -> str | None:
    """같은 컨테이너의 JWT_SECRET 으로 역할 토큰 발급 (api/auth.py:45). 비밀이 없으면 None → 그 화면은 skipped."""
    if not role:
        return None
    try:
        import os
        from api import auth
        if not os.environ.get("JWT_SECRET") and "JWT_SECRET=" not in (Path("config/.env").read_text(encoding="utf-8")
                                                                       if Path("config/.env").exists() else ""):
            return None
        return auth._issue_token("smoke@internal", role)
    except Exception as exc:
        logger.warning("smoke: 토큰 발급 실패: %s", exc)
        return None


def login_token_factory(base: str, email: str, password: str) -> Callable[[str | None], str | None]:
    """외부 실행용: 한 계정(admin 권장)으로 로그인해 모든 역할에 같은 토큰을 준다."""
    data = json.dumps({"email": email, "password": password}).encode()
    req = urllib.request.Request(f"{base}/api/auth/login", data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        token = json.loads(resp.read().decode())["token"]
    return lambda role: token if role else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MA AI Dossier 화면별 스모크")
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--login-email")
    parser.add_argument("--login-password")
    args = parser.parse_args(argv)
    token_for = (login_token_factory(args.base, args.login_email, args.login_password)
                 if args.login_email else mint_token)
    res = run_smoke(args.base, token_for)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

`scheduler.py` 잡 + spec + CLI:

```python
@guarded_job("platform_smoke")
def platform_smoke_job():
    """매일 07:50 — 21개 화면 + 권한 게이트 스모크 (같은 컨테이너의 웹 프로세스). 실패 = 화면 깨짐 → 과제(하루 1건)."""
    from scripts.smoke import DEFAULT_BASE, mint_token, run_smoke
    res = run_smoke(os.environ.get("MA_SMOKE_BASE", DEFAULT_BASE), mint_token)
    logger.info("플랫폼 스모크: %s", res["counts"])
    return res
```

```python
        JobSpec("platform_smoke", "화면별 API 스모크 (매일 07:50, 21 화면 + 권한 게이트)", platform_smoke_job,
                _cron(hour=7, minute=50)),
```

```python
    parser.add_argument("--smoke-now", action="store_true", help="화면별 스모크 즉시 실행 (로컬 웹 서버 필요)")
    ...
    if args.smoke_now:
        logger.info("플랫폼 스모크 즉시 실행: %s", platform_smoke_job())
        return
```

- [ ] **Step 8: 통과 확인 + 커밋**

Run: `.venv/bin/python -m pytest tests/test_smoke_screens.py tests/test_health_endpoint.py tests/test_scheduler_job_specs.py -q`
Expected: PASS.

로컬 스모크(선택, 웹 서버가 떠 있을 때): `.venv/bin/python scripts/smoke.py --base http://127.0.0.1:5001` → `config/.env` 에 `JWT_SECRET` 이 있으면 21 화면 실행, `policy-intelligence` 만 실패(로컬 root `/opt/data/...` 부재)할 수 있다.

```bash
git add agents/agent_tasks/heartbeat.py scripts/smoke.py api/server.py fly.toml scheduler.py agents/agent_tasks/signals.py \
        tests/test_health_endpoint.py tests/test_smoke_screens.py
git commit -m "feat(agent-team): 생존 헬스 — heartbeat 파일 + /api/health 503 + Fly check + 화면별 스모크"
```

**완료 기준 (T4)**
- `/api/health` 가 `db_ok`·`scheduler_heartbeat_age_s`·`frontend_build`·`jobs_failed_24h` 를 돌려주고, DB 불가·heartbeat 15분 초과에 503(테스트 6개). `fly.toml` 에 check 블록.
- 스모크 21 화면 + 권한 게이트, 기대 조건 44개 이상(테스트 3개). 계정 없이 in-process 발급으로 동작.
- **오탐 억제**: heartbeat 는 부팅 5분 유예 + 15분 임계(60초 잡 15회 연속 실패) → Fly 재시작은 실제 hang 에서만. 스모크 과제는 `platform_smoke:{날짜}` 하루 1건 + blocked 억제. 프로덕션 첫 실행 예측 **1건**(`policy-intelligence overview` — 볼륨에 매니페스트가 없으면 500, 감사 #4) — Task 3 의 `policy_intelligence` 과제와 원인이 같지만 층이 다르므로(화면 깨짐 vs 데이터 오래됨) 둘 다 남긴다.

---

### Task 5: 런북 확장 — 코드에 이미 있는 복구 절차 11개 등록

**Files:**
- Modify: `agents/agent_tasks/runbooks.py` (런북 11개 + 동기/분리 실행 헬퍼), `agents/agent_tasks/signals.py` (`JOB_RUNBOOK` 값 10개), `scheduler.py` (`--news-daily-now`, `--news-weekly-now`), `tests/test_agent_runbooks.py`

**런북 표** (실행 시간은 로컬 실행 경험·코드 경로 기준 추정; "분리" = 별도 프로세스로 띄우고 즉시 반환)

| 런북 | 부르는 잡 함수 / CLI | 모드 | 예상 시간 | 위험 | 멱등성 |
|---|---|---|---|---|---|
| `exchange_rate_refresh` | `exchange_rate_refresh_job` / `--fx-refresh-now` | 동기 | 1~2분 | KEB 사이트 + chromium(Playwright) | 예: 같은 창이면 같은 파일명 덮어씀, 재계산은 tolerance 로 skip |
| `exchange_rate_recompute` | `exchange_rate_recompute_job` / `--fx-recompute-now` | 동기 | <10초 | 없음(네트워크 X) | 예: 비율 변화 없으면 skip |
| `foreign_approval_sync` | `foreign_approval_sync_job` / `--approval-sync-now` | **분리** | gap 당 수 분(FDA/EMA/PMDA/MFDS/MHRA/TGA 크롤 + Gemini grounded) | 외부 크롤 6곳 + `GEMINI_API_KEY` | 예: gap 만 build |
| `reimbursement_xnational_sync` | `reimbursement_xnational_sync_job` / `--reimb-sync-now` | **분리** | 9 제품 × 4 사이트 ≈ 5~15분 | NICE/PBS/CMS/中医協 크롤 | 예: UPSERT(`agents/db/foreign.py:348-368`) |
| `news_daily_fetch` | `competitor_news_daily_job` / `--news-daily-now`(신규) | 동기 | 1~3분 | Naver API 키(config 클래스), T1 사이트 | 예: url_hash dedup |
| `news_weekly_crawl` | `competitor_news_weekly_job` / `--news-weekly-now`(신규) | **분리** | 6개월 창 × 13 브랜드 ≈ 10분+ | Naver 키, 사이트 | 예 |
| `competitor_trend_promote` | `competitor_trend_promote_job` / `--promote-trends-now` | 동기 | 1~2분 | `OPENAI_API_KEY`(gpt-4o-mini) | 예: UPSERT, manual 보존 |
| `nhis_negotiation_sync` | `nhis_negotiation_sync_job` / `--nhis-sync-now` | 동기 | ~1분 | 공개 사이트(자격증명 X) | 예: content_hash |
| `access_insight_fresh_signals` | `access_insight_fresh_signal_job` / `--fresh-signals-now` | **분리** | 수 분(8일 창, tier A/B/D) | Naver 키, 사이트 | 예: (url, drug_id) INSERT-only |
| `reimb_data_sync` | `reimb_data_sync_job` / `--amjilsim-daily-crawl-now` | 동기 | <1분 | `REIMB_DATA_URL`/`REPORTS_DATA_URL` git raw | 예: hash |
| `policy_intel_analysis_sync` | `policy_analysis_sync_job` / (같은 플래그) | 동기 | <1분 | `POLICY_ANALYSIS_TOKEN`(config 클래스) | 예: sha256 |

**오래 걸리는 런북의 처리(600초 한계, 202 폴링은 P2):** 분리 모드는 `subprocess.Popen([python, "scheduler.py", "--<flag>-now"], start_new_session=True)` 로 띄우고 `{"ok": True, "summary": "… 백그라운드 시작(pid)"}` 를 즉시 돌려준다. 디스패처는 과제를 closed 로 닫는다 — **이때 closed 는 "요청 접수" 이지 "복구 확인" 이 아니다.** 실제 결과는 (1) 자식 프로세스가 같은 `guarded_job` 을 돌기 때문에 `scheduler_job_run` 에 성공/실패 행이 남고 실패면 원장 과제가 생기며, (2) 다음 날 신선도 판정(Task 3)이 여전히 stale 이면 과제를 다시 연다. 겹침 방지: 런북별 pid 파일(`logs/runbook_<name>.pid`) + 살아 있는 프로세스면 거부. 1GB 머신에서 Playwright 2개가 동시에 뜨지 않도록 디스패처의 `flock` (`VPS_SETUP.md` §4) 과 이 pid 가드가 함께 막는다.

런북은 **웹 프로세스 안에서 `scheduler` 모듈을 지연 import** 해 같은 잡 함수를 부른다 — 코드 경로가 하나(잡 = 런북)라 결과 기록·실패 신호가 동일하게 남는다. `scheduler.py` 는 import 시 `main()` 을 부르지 않고 `setup_logging` 도 부르지 않으므로 안전하다.

- [ ] **Step 1: 테스트 교체/추가 — `tests/test_agent_runbooks.py`**

`test_registry_lists_known_runbooks` 를 바꾸고 아래를 추가한다.

```python
def test_registry_lists_known_runbooks():
    assert set(runbooks.list_runbooks()) == {
        "domestic_price_catchup", "daily_mailing_dispatch", "daily_mailing_top_news",
        "exchange_rate_refresh", "exchange_rate_recompute", "foreign_approval_sync", "reimbursement_xnational_sync",
        "news_daily_fetch", "news_weekly_crawl", "competitor_trend_promote", "nhis_negotiation_sync",
        "access_insight_fresh_signals", "reimb_data_sync", "policy_intel_analysis_sync"}


def test_every_runbook_hint_in_signals_and_freshness_is_registered():
    from agents.agent_tasks.freshness import DOMAIN_CHECKS, Ctx
    from agents.agent_tasks.signals import HEALTH_RUNBOOK, JOB_RUNBOOK
    registered = set(runbooks.list_runbooks())
    hints = {v for v in JOB_RUNBOOK.values() if v} | {v for v in HEALTH_RUNBOOK.values() if v}
    assert hints <= registered, hints - registered
    # freshness 의 런북 이름은 함수 소스에 문자열로 있다 — 판정을 돌리지 않고 상수만 대조
    import inspect
    from agents.agent_tasks import freshness
    src = inspect.getsource(freshness)
    for name in ("domestic_price_catchup", "exchange_rate_refresh", "exchange_rate_recompute", "foreign_approval_sync",
                 "reimbursement_xnational_sync", "reimb_data_sync", "policy_intel_analysis_sync", "news_daily_fetch",
                 "competitor_trend_promote", "access_insight_fresh_signals", "nhis_negotiation_sync"):
        assert f'"{name}"' in src and name in registered


def test_sync_runbook_wraps_guarded_job_result(monkeypatch):
    monkeypatch.setattr(runbooks, "_job", lambda name: (lambda: {"ok": True, "counts": {"fetched": 12}, "errors": [], "error_class": "transient"}))
    out = runbooks.run("nhis_negotiation_sync")
    assert out["ok"] is True and "완료" in out["summary"] and out["detail"]["counts"] == {"fetched": 12}


def test_sync_runbook_failure_is_not_ok(monkeypatch):
    monkeypatch.setattr(runbooks, "_job", lambda name: (lambda: {"ok": False, "counts": {}, "errors": ["Naver 5xx"], "error_class": "transient"}))
    out = runbooks.run("news_daily_fetch")
    assert out["ok"] is False and "Naver 5xx" in out["summary"]


def test_detached_runbook_spawns_cli_and_guards_overlap(monkeypatch, tmp_path):
    monkeypatch.setattr(runbooks, "DETACHED_LOG_DIR", tmp_path)
    spawned = []

    class FakeProc:
        pid = 4242

        def poll(self):
            return None                                  # 아직 실행 중

    def fake_popen(cmd, **kw):
        spawned.append(cmd)
        return FakeProc()
    monkeypatch.setattr(runbooks.subprocess, "Popen", fake_popen)
    runbooks._DETACHED.clear()
    out = runbooks.run("reimbursement_xnational_sync")
    assert out["ok"] is True and "백그라운드" in out["summary"] and out["detail"]["pid"] == 4242
    assert spawned[0][1:] == [str(runbooks.REPO_ROOT / "scheduler.py"), "--reimb-sync-now"]
    again = runbooks.run("reimbursement_xnational_sync")
    assert again["ok"] is False and "이미 실행 중" in again["summary"] and len(spawned) == 1
```

- [ ] **Step 2: 실패 확인** — `.venv/bin/python -m pytest tests/test_agent_runbooks.py -q` → FAIL (registry 집합 불일치)

- [ ] **Step 3: 구현 — `agents/agent_tasks/runbooks.py` 에 추가**

기존 3개 런북과 `run()`/`list_runbooks()` 는 그대로 두고 다음을 추가한다(`import os, subprocess, sys` 추가).

```python
DETACHED_LOG_DIR = REPO_ROOT / "logs"
_DETACHED: dict[str, "subprocess.Popen"] = {}


def _job(name: str):
    """scheduler.py 의 guarded 잡 함수를 지연 import — 잡과 런북이 같은 코드 경로·같은 기록을 갖는다."""
    import importlib
    return getattr(importlib.import_module("scheduler"), name)


def _from_job(job_fn_name: str, label: str) -> dict:
    res = _job(job_fn_name)()
    errors = res.get("errors") or []
    state = "완료" if res.get("ok") else "실패"
    tail = f"; 오류: {errors[0]}" if errors else ""
    return {"ok": bool(res.get("ok")), "summary": f"{label} {state} — {res.get('counts')}{tail}"[:300], "detail": res}


def _run_detached(name: str, flag: str) -> dict:
    """600초를 넘길 수 있는 런북: 별도 프로세스로 `scheduler.py <flag>` 를 띄우고 즉시 돌아온다.

    결과 확인은 자식이 도는 guarded_job 의 scheduler_job_run 행 + 다음 신선도 판정. pid 파일로 겹침을 막는다.
    """
    DETACHED_LOG_DIR.mkdir(parents=True, exist_ok=True)
    prev = _DETACHED.get(name)
    if prev is not None and prev.poll() is None:
        return {"ok": False, "summary": f"{name} 이미 실행 중 (pid {prev.pid}) — 겹침 방지", "detail": {"pid": prev.pid}}
    pidfile = DETACHED_LOG_DIR / f"runbook_{name}.pid"
    if pidfile.exists():                                   # 서버 재기동 전에 띄운 프로세스가 아직 살아 있나
        try:
            pid = int(pidfile.read_text().strip())
            os.kill(pid, 0)
            return {"ok": False, "summary": f"{name} 이미 실행 중 (pid {pid}) — 겹침 방지", "detail": {"pid": pid}}
        except (ValueError, ProcessLookupError, PermissionError):
            pidfile.unlink(missing_ok=True)
    log = open(DETACHED_LOG_DIR / f"runbook_{name}.out", "ab")
    proc = subprocess.Popen([sys.executable, str(REPO_ROOT / "scheduler.py"), flag], cwd=str(REPO_ROOT),
                            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    _DETACHED[name] = proc
    pidfile.write_text(str(proc.pid))
    return {"ok": True,
            "summary": f"{name} 백그라운드 시작 (pid {proc.pid}, `scheduler.py {flag}`) — 완료 여부는 scheduler_job_run 과 다음 신선도 판정으로 확인",
            "detail": {"pid": proc.pid, "mode": "detached", "flag": flag}}


def _runbook_exchange_rate_refresh() -> dict:
    return _from_job("exchange_rate_refresh_job", "KEB 환율 갱신 + 기존행 재계산")


def _runbook_exchange_rate_recompute() -> dict:
    return _from_job("exchange_rate_recompute_job", "해외약가 FX 재계산")


def _runbook_foreign_approval_sync() -> dict:
    return _run_detached("foreign_approval_sync", "--approval-sync-now")


def _runbook_reimbursement_xnational_sync() -> dict:
    return _run_detached("reimbursement_xnational_sync", "--reimb-sync-now")


def _runbook_news_daily_fetch() -> dict:
    return _from_job("competitor_news_daily_job", "경쟁사·정부 뉴스 3일 fetch")


def _runbook_news_weekly_crawl() -> dict:
    return _run_detached("news_weekly_crawl", "--news-weekly-now")


def _runbook_competitor_trend_promote() -> dict:
    return _from_job("competitor_trend_promote_job", "동향 카드 승격")


def _runbook_nhis_negotiation_sync() -> dict:
    return _from_job("nhis_negotiation_sync_job", "NHIS 협상 공개자료 sync")


def _runbook_access_insight_fresh_signals() -> dict:
    return _run_detached("access_insight_fresh_signals", "--fresh-signals-now")


def _runbook_reimb_data_sync() -> dict:
    return _from_job("reimb_data_sync_job", "위원회 데이터·보고서 sync")


def _runbook_policy_intel_analysis_sync() -> dict:
    return _from_job("policy_analysis_sync_job", "정책 분석 사이드카 sync")


RUNBOOKS.update({
    "exchange_rate_refresh": _runbook_exchange_rate_refresh,
    "exchange_rate_recompute": _runbook_exchange_rate_recompute,
    "foreign_approval_sync": _runbook_foreign_approval_sync,
    "reimbursement_xnational_sync": _runbook_reimbursement_xnational_sync,
    "news_daily_fetch": _runbook_news_daily_fetch,
    "news_weekly_crawl": _runbook_news_weekly_crawl,
    "competitor_trend_promote": _runbook_competitor_trend_promote,
    "nhis_negotiation_sync": _runbook_nhis_negotiation_sync,
    "access_insight_fresh_signals": _runbook_access_insight_fresh_signals,
    "reimb_data_sync": _runbook_reimb_data_sync,
    "policy_intel_analysis_sync": _runbook_policy_intel_analysis_sync,
})
```

- [ ] **Step 4: `JOB_RUNBOOK` 값 채우기 (`signals.py`)**

```python
    "foreign_approval_sync": "foreign_approval_sync",
    "competitor_news_weekly": "news_weekly_crawl",
    "competitor_news_daily": "news_daily_fetch",
    "competitor_trend_promote": "competitor_trend_promote",
    "access_insight_fresh_signals": "access_insight_fresh_signals",
    "reimbursement_xnational_sync": "reimbursement_xnational_sync",
    "exchange_rate_refresh": "exchange_rate_refresh",
    "reimb_data_sync": "reimb_data_sync",
    "policy_intel_analysis_sync": "policy_intel_analysis_sync",
    "nhis_negotiation_sync": "nhis_negotiation_sync",
```

(`git_auto_backup`·`rule_compliance_audit`·`quality_guard_review`·`daily_mailing_health`·`home_brand_related_refresh`·`domain_freshness`·`scheduler_heartbeat`·`platform_smoke`·placeholder 4개는 None 유지 — 재실행이 답이 아니거나 그 자체가 관측 잡이다.)

- [ ] **Step 5: CLI 2개 (`scheduler.py` `main()`)**

```python
    parser.add_argument("--news-daily-now", action="store_true", help="경쟁사·정부 뉴스 3일 fetch 즉시 실행")
    parser.add_argument("--news-weekly-now", action="store_true", help="경쟁사·정부 뉴스 주간(6개월 창) 크롤 즉시 실행")
    ...
    if args.news_daily_now:
        logger.info("뉴스 매일 fetch 즉시 실행: %s", competitor_news_daily_job())
        return
    if args.news_weekly_now:
        logger.info("뉴스 주간 크롤 즉시 실행: %s", competitor_news_weekly_job())
        return
```

- [ ] **Step 6: 통과 확인 + 커밋**

Run: `.venv/bin/python -m pytest tests/test_agent_runbooks.py tests/test_agent_api.py tests/test_agent_dispatcher.py -q`
Expected: PASS (`test_agent_api` 의 가짜 실행기는 레지스트리 이름만 본다).

```bash
git add agents/agent_tasks/runbooks.py agents/agent_tasks/signals.py scheduler.py tests/test_agent_runbooks.py
git commit -m "feat(agent-team): 런북 11개 등록 — 동기/분리 실행, 잡 함수 재사용, 뉴스 CLI 플래그"
```

**완료 기준 (T5)**
- 레지스트리 14개, `JOB_RUNBOOK`·`HEALTH_RUNBOOK`·`freshness` 의 모든 힌트가 등록 이름과 일치(테스트).
- 분리 모드는 겹침을 거부하고 CLI 플래그를 정확히 넘긴다(테스트).
- **오탐 억제**: 분리 런북의 `ok=True` 는 "접수" 이므로 과제가 닫혀도 실제 실패는 자식의 `guarded_job` 이 같은 날 `{job_id}:{날짜}` 과제로 남기고(하루 1건), 신선도가 다음 날 재개한다. 새 과제 유형이 생기지 않는다.

---

### Task 6: QG·RC 산출물 → 원장 (내용 키 dedup, RC 프로덕션 실행, 출력 경로 볼륨)

**Files:**
- Modify: `agents/agent_tasks/signals.py` (`record_quality_review`, `record_compliance_results`, `_content_key`), `scheduler.py` (`rule_compliance_audit_job`, `quality_guard_review_job`, 경계 함수 2개), `agents/quality_guard/deviations.py:11-14`, `agents/rule_compliance/agent.py:40-70,73-90`, `.gitignore`
- Modify tests: `tests/test_scheduler_local_only_jobs.py` (RC 프로덕션 케이스 2개)
- Create: `tests/test_qg_rc_signals.py`

설계:
- **QG 항목은 그룹으로 과제화** — 매일 같은 13건(코드패턴 2 + 제안 11)을 낱개로 만들면 첫날 13건이다. 코드 패턴 위반은 `:` 앞 유형별(예: `BaseScraper 미상속` 1건), 제안은 선두 이모지 카테고리별(📦 모듈 분리 8건→1, 🔑 DISEASE_KR 1, 💊 enrichment 1, 🔐 DocCheck 1)로 묶는다 → **improvement 5건**. source_ref 는 그룹 키(`qg:issue:<유형>`, `qg:suggestion:<카테고리>`)라 내용이 바뀌면 열린 과제의 detail 을 갱신하고 `refreshed` 이벤트를 남긴다(새 과제 X). `mfds_regressions`·`rule_drifts` 는 낱개 `ops_incident`(회귀는 indication_id, drift 는 규칙+파일 키).
- 숫자는 키에서 지운다(`re.sub(r"\d+","#")`) — "793줄" 이 "801줄" 이 돼도 같은 과제.
- **RC FAIL 은 `rc:<memory>`** 로 메모리당 1건.
- **RC 프로덕션**: 메모리 디렉터리 없이 `CHECKS` 레지스트리만 돈다(판정은 전부 DB/파일 증거). `require_memory=False`.
- **출력 경로**: `QUALITY_GUARD_DIR` 환경변수 → 기본 `data/quality_guard`(볼륨). 추적 중인 `quality_guard/`(74 파일)는 역사로 두고 새 산출물은 gitignore. 로컬 1회 이전: `deviation_log.jsonl` 복사(dedup 연속성).

- [ ] **Step 1: 실패 테스트 — `tests/test_qg_rc_signals.py`**

```python
# tests/test_qg_rc_signals.py
from __future__ import annotations

from agents.agent_tasks.signals import _content_key, record_compliance_results, record_quality_review
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


def test_quality_review_same_content_next_day_adds_nothing(tmp_path):
    db = tmp_path / "a.db"
    record_quality_review(REVIEW, db_path=db)
    record_quality_review(REVIEW, db_path=db)
    assert len(list_tasks(db_path=db)) == 5
    tid = list_tasks(db_path=db)[0]["id"]
    assert [e["event"] for e in list_events(tid, db_path=db)] == ["created"]


def test_quality_review_changed_group_refreshes_detail(tmp_path):
    db = tmp_path / "a.db"
    record_quality_review(REVIEW, db_path=db)
    changed = dict(REVIEW, suggestions=REVIEW["suggestions"] + ["📦 `agents/analog/store.py` (957줄) — 모듈 분리 검토"])
    record_quality_review(changed, db_path=db)
    t = {t["source_ref"]: t for t in list_tasks(db_path=db)}["qg:suggestion:📦"]
    assert "analog/store.py" in t["detail"]
    assert [e["event"] for e in list_events(t["id"], db_path=db)] == ["created", "refreshed"]


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
    assert [t for t in tasks.values() if t["source_ref"].startswith("qg:drift:")][0]["priority"] == 2


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
    assert tasks["rc:project_xnational_reimbursement"]["detail"].count("런북: reimbursement_xnational_sync") == 1
    assert "런북 없음" in tasks["rc:project_patent_classification_policy"]["detail"]
    assert record_compliance_results(results, db_path=db) == ids
    update_task(ids[0], db_path=db, status="blocked", actor="dispatcher", resolution="x")
    assert record_compliance_results(results, db_path=db) == ids           # blocked 도 억제
```

- [ ] **Step 2: 실패 확인** — `.venv/bin/python -m pytest tests/test_qg_rc_signals.py -q` → FAIL (`ImportError: record_quality_review`)

- [ ] **Step 3: 구현 — `signals.py` 에 추가**

```python
import hashlib
import json
import re

from .store import find_task, update_task   # 기존 import 줄에 합친다

RC_RUNBOOK: dict[str, str | None] = {
    "project_xnational_reimbursement": "reimbursement_xnational_sync",
    "project_price_approval_coverage": "foreign_approval_sync",
}
_EMOJI_PREFIX = re.compile(r"^\s*([\U0001F300-\U0001FAFF☀-➿])")


def _content_key(text: str) -> str:
    """숫자를 지운 내용 해시 — '793줄' 이 '801줄' 이 돼도 같은 항목."""
    return hashlib.sha1(re.sub(r"\d+", "#", str(text)).strip().encode("utf-8")).hexdigest()[:12]


def _issue_group(text: str) -> str:
    return str(text).split(":", 1)[0].strip() or "기타"


def _suggestion_group(text: str) -> str:
    m = _EMOJI_PREFIX.match(str(text))
    return m.group(1) if m else "기타"


def _upsert_group_task(*, source_ref: str, title: str, items: list[str], db_path: str | Path) -> int:
    """그룹 과제: 없으면 생성, 열려 있는데 내용이 바뀌었으면 detail 갱신 + refreshed 이벤트."""
    detail = "\n".join(f"- {i}" for i in items) + "\n" + runbook_line(None)
    existing = find_task("quality_guard", source_ref, db_path=db_path)
    if existing and existing["status"] in DEDUP_STATUSES:
        if existing["detail"] != detail:
            update_task(existing["id"], actor="quality_guard", db_path=db_path, detail=detail)
            add_event(existing["id"], actor="quality_guard", event="refreshed", detail=f"{len(items)}건", db_path=db_path)
        return int(existing["id"])
    return create_task(kind="improvement", source="quality_guard", source_ref=source_ref, title=title, detail=detail,
                       priority=3, actor="quality_guard", dedup_statuses=DEDUP_STATUSES, db_path=db_path)


def record_quality_review(result: dict, *, db_path: str | Path = DEFAULT_DB_PATH) -> dict[str, list[int]]:
    """QualityGuardAgent.review_codebase() 반환값 → 과제. 회귀·drift 는 ops_incident(낱개), 코드패턴·제안은 improvement(그룹)."""
    ids: dict[str, list[int]] = {"incident": [], "improvement": []}
    for m in result.get("mfds_regressions") or []:
        ind = str(m.get("indication_id"))
        ids["incident"].append(create_task(
            kind="ops_incident", source="quality_guard", source_ref=f"qg:regression:{ind}",
            title=f"MFDS baseline 회귀 — {ind}",
            detail=f"expected {m.get('expected')} / actual {m.get('actual')} ({m.get('reason', '')})\n"
                   "런북 없음 — `python -m scripts.apply_mfds_official_dates --all --apply` 후 "
                   "`agents/rules/kr_mfds_approval_agent_rules.md` §2 LayerSpec 확인",
            priority=1, actor="quality_guard", dedup_statuses=DEDUP_STATUSES, db_path=db_path))
    for d in result.get("rule_drifts") or []:
        text = f"{d.get('rule')} — {d.get('file')}"
        ids["incident"].append(create_task(
            kind="ops_incident", source="quality_guard", source_ref=f"qg:drift:{_content_key(text)}",
            title=f"규칙↔코드 drift — {d.get('rule')}", detail=f"{text}\n{runbook_line(None)}",
            priority=2 if d.get("severity") == "ERROR" else 3, actor="quality_guard",
            dedup_statuses=DEDUP_STATUSES, db_path=db_path))
    groups: dict[str, list[str]] = {}
    for text in result.get("code_issues") or []:
        groups.setdefault(f"qg:issue:{_issue_group(text)}", []).append(str(text))
    for text in result.get("suggestions") or []:
        groups.setdefault(f"qg:suggestion:{_suggestion_group(text)}", []).append(str(text))
    for ref, items in groups.items():
        kind_label = "코드 패턴 위반" if ref.startswith("qg:issue:") else "개선 제안"
        ids["improvement"].append(_upsert_group_task(
            source_ref=ref, title=f"QG {kind_label} — {ref.split(':', 2)[2]} ({len(items)}건)", items=items, db_path=db_path))
    return ids


def record_compliance_results(results, *, db_path: str | Path = DEFAULT_DB_PATH) -> list[int]:
    """RuleComplianceAgent.audit() 의 CheckResult 목록 → FAIL 만 ops_incident, 메모리당 1건."""
    ids: list[int] = []
    for r in results:
        if r.status != "fail":
            continue
        ids.append(create_task(
            kind="ops_incident", source="rule_compliance", source_ref=f"rc:{r.memory}",
            title=f"Rule Compliance FAIL — {r.memory}",
            detail=f"{r.detail}\n수치: {json.dumps(r.metrics, ensure_ascii=False, default=str)[:800]}\n"
                   f"{runbook_line(RC_RUNBOOK.get(r.memory))}",
            priority=2, actor="rule_compliance", dedup_statuses=DEDUP_STATUSES, db_path=db_path))
    return ids
```

- [ ] **Step 4: 잡 본문 전환 (`scheduler.py`) + 경계 함수**

```python
def _agent_record_quality_review(result: dict) -> None:
    try:
        from agents.agent_tasks.signals import record_quality_review
        ids = record_quality_review(result, db_path=resolve_db_path())
        logger.info("QualityGuard → 원장: incident %d · improvement %d", len(ids["incident"]), len(ids["improvement"]))
    except Exception as signal_err:
        logger.warning("QualityGuard 신호 기록 실패: %s", signal_err)


def _agent_record_compliance_results(results) -> None:
    try:
        from agents.agent_tasks.signals import record_compliance_results
        ids = record_compliance_results(results, db_path=resolve_db_path())
        logger.info("Rule Compliance → 원장: FAIL 과제 %d", len(ids))
    except Exception as signal_err:
        logger.warning("Rule Compliance 신호 기록 실패: %s", signal_err)


@guarded_job("rule_compliance_audit")
def rule_compliance_audit_job():
    """RuleComplianceAgent — 매일 05:30 Seoul (QG 06:00 직전).

    로컬: MEMORY.md 인덱스를 소스로 메모리 ↔ 런타임 대조. 프로덕션: 메모리 디렉터리가 없으므로 CHECKS 레지스트리
    (전부 DB/파일 증거)만 돈다(require_memory=False). FAIL 은 과제(메모리당 1건)이지 잡 실패가 아니다.
    """
    from agents.rule_compliance import RuleComplianceAgent
    prod = is_production()
    logger.info("━━━ Rule Compliance 감사 시작 (%s) ━━━", "DB 체크만 — 메모리 없음" if prod else "메모리 대조")
    agent = RuleComplianceAgent(require_memory=not prod)
    results = agent.audit()
    path = agent.write_report(results)
    fails = [r for r in results if r.status == "fail"]
    passes = [r for r in results if r.status == "pass"]
    if fails:
        logger.error("Rule Compliance: ❌ FAIL %d건 / ✅ PASS %d건 — %s", len(fails), len(passes), path)
        for f in fails:
            logger.error("  • %s: %s", f.memory, f.detail)
    else:
        logger.info("Rule Compliance: ✅ PASS %d건, 회귀 0건 — %s", len(passes), path)
    _agent_record_compliance_results(results)
    return {"counts": {"pass": len(passes), "fail": len(fails), "skip": len(results) - len(passes) - len(fails)}}


@guarded_job("quality_guard_review")
def quality_guard_review_job():
    """QualityGuardAgent 지속 리뷰 — 매일 06:00 Seoul. 회귀·drift 는 ops_incident, 코드패턴·제안은 improvement 그룹 과제."""
    from agents.quality_guard import QualityGuardAgent
    logger.info("━━━ QualityGuard 일일 리뷰 시작 ━━━")
    result = QualityGuardAgent().review_codebase()
    regressions = result.get("mfds_regressions", [])
    drifts = result.get("rule_drifts", [])
    if regressions:
        logger.error("QualityGuard: MFDS baseline 회귀 %d건 — %s", len(regressions), result["report_path"])
    elif drifts:
        logger.warning("QualityGuard: 규칙↔코드 drift %d건 — %s", len(drifts), result["report_path"])
    else:
        logger.info("QualityGuard: 회귀 0건 / 제안 %d건 — %s", len(result.get("suggestions", [])), result["report_path"])
    _agent_record_quality_review(result)
    return {"counts": {k: len(result.get(k) or []) for k in ("code_issues", "rule_drifts", "mfds_regressions", "suggestions")}}
```

- [ ] **Step 5: RC 메모리 없는 모드 (`agents/rule_compliance/agent.py`)**

```python
def _resolve_memory_dir(required: bool = True) -> Path | None:
    override = os.environ.get("CLAUDE_MEMORY_DIR")
    if override:
        p = Path(override)
        if p.exists():
            return p
    if _DEFAULT_MEMORY_DIR.exists():
        return _DEFAULT_MEMORY_DIR
    for candidate in (Path.home() / ".claude" / "projects",):
        if candidate.exists():
            for sub in candidate.iterdir():
                mem = sub / "memory"
                if mem.exists():
                    return mem
    if required:
        raise FileNotFoundError("memory 디렉터리를 찾을 수 없음. CLAUDE_MEMORY_DIR 환경변수로 지정하세요.")
    return None


class RuleComplianceAgent:
    def __init__(self, memory_dir: Path | None = None, project_root: Path | None = None, *, require_memory: bool = True):
        self.memory_dir = memory_dir or _resolve_memory_dir(required=require_memory)
        self.project_root = project_root or BASE_DIR
        from agents.quality_guard.deviations import GUARD_DIR
        self.report_dir = GUARD_DIR                      # QG 와 같은 볼륨 경로(data/quality_guard)
        self.report_dir.mkdir(parents=True, exist_ok=True)

    def load_index(self) -> list[dict]:
        if self.memory_dir is None:
            # 메모리 없는 모드(프로덕션): 체크 레지스트리가 곧 목록 — 판정은 전부 DB/파일 증거라 메모리가 필요 없다
            return [{"name": n, "title": n, "hook": "", "file": ""} for n in CHECKS]
        idx_path = self.memory_dir / "MEMORY.md"
        # (이하 기존 그대로)
```

`agents/quality_guard/deviations.py:11-14` 교체:

```python
import os

BASE_DIR = Path(__file__).resolve().parents[2]


def guard_dir() -> Path:
    """산출물 경로. 기본 data/quality_guard (Fly 볼륨 — 재배포에 살아남는다). QUALITY_GUARD_DIR 로 오버라이드.
    구 경로 <repo>/quality_guard 는 git 추적 역사로 남긴다(로컬 git_backup_job 이 매일 커밋하던 원인)."""
    override = os.environ.get("QUALITY_GUARD_DIR")
    return Path(override) if override else BASE_DIR / "data" / "quality_guard"


GUARD_DIR = guard_dir()
DEVIATION_LOG = GUARD_DIR / "deviation_log.jsonl"
```

(`agents/quality_guard/agent.py:21` 은 이미 `from .deviations import BASE_DIR, DEVIATION_LOG, GUARD_DIR, _write_deviation` 이라 변경 없음.)

`.gitignore` 에 `data/quality_guard/` 추가. 로컬 1회 이전:

```bash
mkdir -p data/quality_guard && cp quality_guard/deviation_log.jsonl data/quality_guard/
```

`tests/test_scheduler_local_only_jobs.py` 의 RC 두 테스트 교체:

```python
def test_rule_compliance_runs_in_production_without_memory(monkeypatch, caplog, tmp_path):
    monkeypatch.setenv("FLY_APP_NAME", "ma-ai-dossier")
    seen = {}

    class _Fake:
        def __init__(self, *a, **k):
            seen.update(k)

        def audit(self):
            return []

        def write_report(self, results):
            return tmp_path / "compliance.md"
    monkeypatch.setattr(rc_pkg, "RuleComplianceAgent", _Fake)
    caplog.set_level(logging.INFO, logger="scheduler")
    res = sched.rule_compliance_audit_job()
    assert seen == {"require_memory": False} and res["ok"] is True
    assert not _errors(caplog)


def test_rule_compliance_runs_locally_with_memory(monkeypatch, caplog, tmp_path):
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    seen = {}

    class _Fake:
        def __init__(self, *a, **k):
            seen.update(k)

        def audit(self):
            return []

        def write_report(self, results):
            return tmp_path / "compliance.md"
    monkeypatch.setattr(rc_pkg, "RuleComplianceAgent", _Fake)
    caplog.set_level(logging.INFO, logger="scheduler")
    sched.rule_compliance_audit_job()
    assert seen == {"require_memory": True}
    assert not _errors(caplog)
```

(파일 docstring 의 "rule_compliance_audit_job: … ERROR 매일 05:30" 문장은 "프로덕션에서는 메모리 없는 모드로 실행" 으로 고친다.)

- [ ] **Step 6: 통과 확인 + 로컬 실측**

Run: `.venv/bin/python -m pytest tests/test_qg_rc_signals.py tests/test_scheduler_local_only_jobs.py tests/test_scheduler_job_specs.py -q`
Expected: PASS.

Run: `AGENT_TASK_DB=/tmp/p15_qg.db .venv/bin/python scheduler.py --review-now && AGENT_TASK_DB=/tmp/p15_qg.db .venv/bin/python scheduler.py --compliance-now && .venv/bin/python -c "from agents.agent_tasks.store import list_tasks; ts=list_tasks(db_path='/tmp/p15_qg.db'); print(len(ts)); [print(t['kind'], t['source_ref']) for t in ts]"`
Expected: `data/quality_guard/review_<오늘>.md`·`compliance_<오늘>.md` 생성, 과제 7건 = improvement 5(`qg:issue:BaseScraper 미상속`, `qg:suggestion:📦/🔑/💊/🔐`) + ops_incident 2(`rc:project_patent_classification_policy`, `rc:project_xnational_reimbursement`). 두 번째 실행에서 0건 추가.

- [ ] **Step 7: 커밋**

```bash
git add agents/agent_tasks/signals.py scheduler.py agents/quality_guard/deviations.py agents/rule_compliance/agent.py \
        .gitignore tests/test_qg_rc_signals.py tests/test_scheduler_local_only_jobs.py
git commit -m "feat(agent-team): QG·RC 산출물을 원장으로 — 그룹/내용 키 dedup, RC 프로덕션 DB 모드, 출력 경로 볼륨"
```

**완료 기준 (T6)**
- QG 회귀/drift → `ops_incident`, 코드패턴/제안 → `improvement` 그룹, RC FAIL → `ops_incident` 메모리당 1건(테스트 6개).
- RC 가 프로덕션에서 돈다(메모리 없이 14 체크), QG/RC 보고서가 `data/quality_guard/` 에 남는다.
- **오탐 억제**: 첫 실행 **7건**(로컬 실측과 동일 예상: RC FAIL 2 + QG 그룹 5), 이후 내용 불변이면 0건/일, 내용 변화는 `refreshed` 이벤트. `improvement` 는 디스패처가 집지 않는다(`CLAIMABLE_KINDS="ops_incident"`) — P3 개선자의 입력으로 남는다.

---

### Task 7: placeholder 정리 — 거짓 성공 4개를 원장의 영구 부채 과제로

**Files:**
- Modify: `agents/agent_tasks/signals.py` (`JOB_RUNBOOK` 에서 placeholder 4행 삭제 + `KNOWN_DEBTS` + `record_known_debts()`), `scheduler.py` (`build_job_specs` 4행 삭제, `main()` 기동 시 1회 호출, `--record-debts-now`)
- Create: `tests/test_known_debts.py`

**왜:** `amjilsim_d_minus_2_reporter`(`scheduler.py:605-630`) · `amjilsim_d_plus_1_reporter`(`:632-658`) · `amjilsim_monthly_trend`(`:661-681`) 은 calendar 조회 뒤 `# TODO: backend 호출` 주석만 남기고 끝나고, `hira_schedule_fetcher_annual`(`:684-700`) 은 본문 전체가 placeholder 다. 네 잡은 **매일/매년 "성공"으로 집계**되므로 T1 의 잡 층 신호로는 영원히 잡히지 않는다. 잡을 그대로 두면 관측에 거짓 신호를 섞고, 함수를 지우면 `agents/amjilsim_tracker/` 의 후속 구현 자리가 사라진다. 그래서 **스케줄 등록만 해제하고**(함수·docstring 보존) 미구현 사실을 원장에 **영구 과제**로 남긴다.

> 등록 잡 수 변화: 22 − 4(placeholder) + 3(T3 `domain_freshness` · T4 `scheduler_heartbeat`·`platform_smoke`) = **21**. `tests/test_scheduler_job_specs.py::test_job_ids_match_runbook_map_exactly` 가 `JOB_RUNBOOK` 키 집합과 대조하므로 양쪽을 같이 고친다.

- [ ] **Step 1: 테스트 먼저 — `tests/test_known_debts.py`**

```python
"""구현 대기 기능(placeholder)이 스케줄에서 내려가고 원장의 영구 과제로 남는지."""
from __future__ import annotations

import scheduler as sch
from agents.agent_tasks.signals import KNOWN_DEBTS, JOB_RUNBOOK, record_known_debts
from agents.agent_tasks.store import list_events, list_tasks, update_task

SCHED_CFG = {"day": "1-7", "hour": 9, "minute": 0}
PLACEHOLDERS = {"amjilsim_d_minus_2_reporter", "amjilsim_d_plus_1_reporter",
                "amjilsim_monthly_trend", "hira_schedule_fetcher_annual"}


def test_placeholder_jobs_are_not_registered():
    ids = {s.id for s in sch.build_job_specs(SCHED_CFG)}
    assert ids & PLACEHOLDERS == set()
    assert set(JOB_RUNBOOK) & PLACEHOLDERS == set()      # 매핑도 같이 지운다
    assert ids == set(JOB_RUNBOOK)                        # 등록부 ↔ 매핑 일치 유지


def test_placeholder_functions_are_kept_for_later_implementation():
    # 스케줄에서 내렸을 뿐, 후속 구현 자리는 남긴다
    for name in ("amjilsim_d_minus_2_reporter_job", "amjilsim_d_plus_1_reporter_job",
                 "amjilsim_monthly_trend_job", "hira_schedule_fetcher_job"):
        assert callable(getattr(sch, name))


def test_record_known_debts_creates_one_improvement_task_each(tmp_path):
    db = tmp_path / "a.db"
    ids = record_known_debts(db_path=db)
    assert len(ids) == len(KNOWN_DEBTS) == 2
    tasks = list_tasks(db_path=db, kinds=("improvement",))
    assert {t["source"] for t in tasks} == {"known_debt"}
    assert {t["source_ref"] for t in tasks} == {"debt:amjilsim_reporters", "debt:hira_schedule_fetcher"}
    # 부채는 런북으로 안 풀린다 — 디스패처가 집지 않도록 kind=improvement + '런북 없음'
    assert all("런북 없음" in t["detail"] for t in tasks)


def test_second_run_adds_nothing(tmp_path):
    db = tmp_path / "a.db"
    first = record_known_debts(db_path=db)
    second = record_known_debts(db_path=db)
    assert first == second                                 # 같은 과제 id 를 돌려준다
    assert len(list_tasks(db_path=db, kinds=("improvement",))) == 2


def test_blocked_debt_is_not_recreated(tmp_path):
    """런북 없음 → blocked 로 끝난 부채가 재기동마다 새로 생기면 원장이 쓰레기가 된다."""
    db = tmp_path / "a.db"
    tid = record_known_debts(db_path=db)[0]
    update_task(tid, db_path=db, actor="admin@msd.com", status="triaged")
    update_task(tid, db_path=db, actor="admin@msd.com", status="blocked",
                resolution="구현 대기 — P2")
    record_known_debts(db_path=db)
    assert len(list_tasks(db_path=db, statuses=("new", "triaged", "blocked"),
                          kinds=("improvement",))) == 2


def test_closed_debt_reopens(tmp_path):
    """구현 완료로 닫은 뒤 다시 placeholder 로 되돌아가면(회귀) 과제가 다시 열려야 한다."""
    db = tmp_path / "a.db"
    tid = record_known_debts(db_path=db)[0]
    update_task(tid, db_path=db, actor="admin@msd.com", status="triaged")
    update_task(tid, db_path=db, actor="admin@msd.com", status="in_progress")
    update_task(tid, db_path=db, actor="admin@msd.com", status="closed", resolution="구현 완료")
    again = record_known_debts(db_path=db)
    assert tid not in again


def test_main_records_debts_once_on_startup(monkeypatch, tmp_path):
    db = tmp_path / "a.db"
    monkeypatch.setenv("AGENT_TASK_DB", str(db))
    calls = []
    monkeypatch.setattr(sch, "_agent_record_known_debts", lambda: calls.append(1))

    class StopScheduler(sch.BlockingScheduler):
        def start(self, *a, **k):
            raise KeyboardInterrupt

    monkeypatch.setattr(sch, "BlockingScheduler", StopScheduler)
    monkeypatch.setattr("sys.argv", ["scheduler.py"])
    sch.main()
    assert calls == [1]
```

Run: `.venv/bin/python -m pytest tests/test_known_debts.py -q`
Expected: FAIL — `ImportError: cannot import name 'KNOWN_DEBTS'`.

- [ ] **Step 2: `agents/agent_tasks/signals.py` — 부채 목록과 기록 함수**

`JOB_RUNBOOK` 에서 placeholder 4행을 지우고(T1 이 넣은 22행 → 18행, T3·T4 가 3행을 더해 21행), 파일 끝에 추가한다.

```python
# ── 구현 대기 기능 부채 ────────────────────────────────────────────────────────
# placeholder 잡은 매일 '성공' 으로 집계돼 관측에 거짓 신호를 섞는다. 스케줄에서 내리고(P1.5 Task 7)
# 미구현 사실만 원장에 남긴다. source_ref 에 날짜가 없으므로 사람이 닫을 때까지 1건이고,
# DEDUP_STATUSES(=OPEN_STATUSES + blocked) 때문에 blocked 로 끝난 부채도 재기동마다 새로 생기지 않는다.
KNOWN_DEBTS: tuple[dict[str, str], ...] = (
    {
        "slug": "amjilsim_reporters",
        "title": "암질심·약평위 리포터 3종 백엔드 미구현 (D-2 예측 / D+1 리뷰 / 월간 트렌드)",
        "detail": (
            "scheduler.py 의 amjilsim_d_minus_2_reporter_job(:605) · amjilsim_d_plus_1_reporter_job(:632) · "
            "amjilsim_monthly_trend_job(:661) 은 calendar 조회 뒤 'TODO: backend 호출' 주석만 남기고 끝난다 — "
            "매일 성공으로 집계되지만 보고서는 생성되지 않는다. P1.5 Task 7 에서 스케줄 등록을 해제했다(함수는 보존).\n"
            "필요한 것: agents/amjilsim_tracker/agent.py 의 run_d_minus_2 / run_d_plus_1 / run_monthly_trend 구현. "
            "구현 후 build_job_specs 에 다시 등록하고 JOB_RUNBOOK 에 런북을 매핑한다."
        ),
    },
    {
        "slug": "hira_schedule_fetcher",
        "title": "HIRA 차수 일정 fetcher 미구현 — 2026-12-03 이후 위원회 일정 공백",
        "detail": (
            "scheduler.py 의 hira_schedule_fetcher_job(:684) 은 본문 전체가 placeholder 다. amjilsim_sessions 의 "
            "SCHEDULED 는 약평위 2026-12-03 · 암질심 2026-12-23 까지만 있어 그 뒤로는 D-2/D+1 게이팅과 "
            "위원회 보드가 영구 idle 이 된다.\n"
            "필요한 것: agents/amjilsim_tracker/schedule_fetcher.py (HIRA 공식 일정 페이지 파싱 + 실패 시 검색 fallback) "
            "+ amjilsim_sessions 적재. 공백 자체는 Task 3 의 committee_schedule 신선도 판정이 "
            "'향후 60일 SCHEDULED 0건' 으로 먼저 경고한다."
        ),
    },
)


def record_known_debts(*, db_path: str | Path = DEFAULT_DB_PATH) -> list[int]:
    """구현 대기 기능을 improvement 과제로 등록한다(이미 열려 있으면 그 id 를 돌려준다). 스케줄러 기동 시 1회.

    kind=improvement 라 디스패처(CLAIMABLE_KINDS='ops_incident')는 집지 않는다 — P3 개선자의 입력이다.
    """
    ids: list[int] = []
    for debt in KNOWN_DEBTS:
        ids.append(create_task(
            kind="improvement", source="known_debt", source_ref=f"debt:{debt['slug']}",
            title=debt["title"], detail=f"{debt['detail']}\n\n{runbook_line(None)}",
            priority=3, dedup_statuses=DEDUP_STATUSES, db_path=db_path,
        ))
    return ids
```

- [ ] **Step 3: `scheduler.py` — 등록 해제 + 기동 시 1회 기록**

`build_job_specs` 에서 네 `JobSpec` 을 지우고, 함수들 위에 등록 해제 사유를 남긴다.

```python
# ── 구현 대기(placeholder) — 스케줄 등록 해제됨 (P1.5 Task 7) ──────────────────────
# 아래 네 함수는 backend 미구현 상태로 매일 '성공' 로그만 남겼다. 거짓 성공이 관측을 오염시키므로
# build_job_specs 에서 내렸고, 미구현 사실은 signals.KNOWN_DEBTS → 원장 improvement 과제로 남는다.
# 구현이 끝나면 build_job_specs 와 JOB_RUNBOOK 에 다시 넣고 KNOWN_DEBTS 에서 해당 항목을 지운다.
def amjilsim_d_minus_2_reporter_job():
    ...
```

경계 함수(`_agent_record_*` 관례)와 `main()` 호출부:

```python
def _agent_record_known_debts() -> None:
    """구현 대기 기능 부채를 원장에 남긴다. 신호 경계 — 실패는 여기서 삼킨다."""
    try:
        from agents.agent_tasks.signals import record_known_debts
        ids = record_known_debts(db_path=resolve_db_path())
        logger.info("구현 대기 부채 과제 %d건 확인: %s", len(ids), ids)
    except Exception as e:                                    # noqa: BLE001 — 신호 실패가 기동을 막으면 안 된다
        logger.warning("부채 과제 기록 실패(무시): %s", e)


# main() 안, 잡 등록 직후 · scheduler.start() 전
_agent_record_known_debts()
```

CLI 에 `--record-debts-now` 를 더해 배포 없이도 확인할 수 있게 한다(`main()` 의 one-shot 플래그 블록, 기존 `--review-now` 와 같은 자리).

Run: `.venv/bin/python -m pytest tests/test_known_debts.py tests/test_scheduler_job_specs.py -q`
Expected: PASS.

- [ ] **Step 4: 로컬 실측**

Run: `AGENT_TASK_DB=/tmp/p15_debt.db .venv/bin/python scheduler.py --record-debts-now && AGENT_TASK_DB=/tmp/p15_debt.db .venv/bin/python scheduler.py --record-debts-now && .venv/bin/python -c "from agents.agent_tasks.store import list_tasks; ts=list_tasks(db_path='/tmp/p15_debt.db'); print(len(ts)); [print(t['kind'], t['source_ref'], t['title'][:40]) for t in ts]"`
Expected: 2건(`debt:amjilsim_reporters`, `debt:hira_schedule_fetcher`), 두 번째 실행에서 증가 없음.

- [ ] **Step 5: 커밋**

```bash
git add agents/agent_tasks/signals.py scheduler.py tests/test_known_debts.py
git commit -m "feat(agent-team): placeholder 잡 4개를 스케줄에서 내리고 구현 대기 부채를 원장에 영구 과제로"
```

**완료 기준 (T7)**
- 등록 잡 **21개**이고 `build_job_specs` 의 id 집합 == `JOB_RUNBOOK` 키 집합(테스트 2개).
- placeholder 함수 4개는 보존(후속 구현 자리) — 호출만 끊겼다.
- **오탐 억제**: 첫 실행 **2건**, 재기동·재실행에서 0건 추가, blocked 로 끝나도 재생성 없음, 구현 완료로 닫으면 재발 시 다시 열림(테스트 4개).
- 거짓 성공 제거 효과: 하루 3건(D-2/D+1/월간) × 매일 → 0.

---

### Task 8: 회귀 + 통합 리허설

**Files:** 없음(검증만). 실패하면 해당 Task 로 돌아간다.

- [ ] **Step 1: 전체 스위트**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 신규 테스트 전부 통과. 기존 환경 의존 실패 1건(`tests/test_access_insight_link.py::test_classify_gov_statement_via_kind_and_agency_keyword` — 로컬 DB 렉시콘이 시드된 경우에만 깨진다, P1.5 와 무관)만 FAIL. 그 외 FAIL 0.

- [ ] **Step 2: import·타입·빌드**

Run: `.venv/bin/python -c "import api.server, scheduler, scripts.smoke, scripts.agent_dispatcher; print('ok')"`
Run: `cd frontend && npm run type-check && npm run build`
Expected: 각각 ok / exit 0.

- [ ] **Step 3: 통합 리허설 — 세 신호 층이 한 원장에 모이는지 (오프라인)**

임시 원장 하나에 잡 층·데이터 층·QG/RC·부채를 모두 쏟고 총계와 중복을 본다.

```bash
export AGENT_TASK_DB=/tmp/p15_rehearsal.db
rm -f $AGENT_TASK_DB
.venv/bin/python scheduler.py --record-debts-now
.venv/bin/python scheduler.py --freshness-now
.venv/bin/python scheduler.py --review-now
.venv/bin/python scheduler.py --compliance-now
.venv/bin/python -c "
from agents.agent_tasks.store import list_tasks
ts = list_tasks(db_path='/tmp/p15_rehearsal.db', statuses=('new','triaged','blocked'), limit=200)
print('총', len(ts))
for t in ts: print(f\"  {t['kind']:12} {t['source']:20} {t['source_ref']:34} {t['title'][:46]}\")
"
```
Expected: 로컬 기준 **약 20건**(부채 2 + 신선도 11 + QG 그룹 5 + RC FAIL 2). 로컬 신선도가 6건이 아니라 11건인 것은 로컬 스케줄러가 2026-07-28 이후 멈춰 있기 때문이며 **사실이지 오탐이 아니다**(프로덕션 예측은 6±1건).

- [ ] **Step 4: 멱등 — 같은 검사를 두 번 돌려 새 과제 0건**

Run: 위 네 명령을 그대로 재실행 → `list_tasks` 총계 비교.
Expected: **증가 0**. 증가가 있으면 그 source 의 `source_ref` 설계가 틀린 것이다(날짜가 들어갔거나 내용 해시가 불안정).

- [ ] **Step 5: 디스패처가 집는 것만 집는지 (dry-run)**

Run: `.venv/bin/python scripts/agent_dispatcher.py --dry-run --max-tasks 5` (프로덕션 자격증명 없이 실행 → 로그인 실패로 종료되는 것이 정상. 서버 없이 확인할 때는 `tests/test_agent_dispatcher.py` 의 FakeClient 경로로 대체)
Expected: `kind=ops_incident` 만 요청하고, 런북 힌트가 없는 과제는 planned 에 들어가지 않는다. `improvement`(부채·QG 제안)와 신선도 중 런북 없는 도메인은 사람 몫으로 남는다.

- [ ] **Step 6: 헬스 503 조건 리허설**

Run: `.venv/bin/python -c "
from agents.agent_tasks.heartbeat import write_heartbeat, heartbeat_age_seconds
import api.server as s
c = s.app.test_client()
write_heartbeat()
print('fresh:', c.get('/api/health').status_code)
"`
Expected: 200. heartbeat 파일을 지우거나 과거로 돌리면 503 — **배포 직후 15분은 grace** 라 머신이 죽지 않는다(Task 4 완료 기준).

**완료 기준 (T8)**
- 전체 스위트 FAIL 1건(기존 환경 의존)뿐.
- 리허설 총계가 Task 별 예측 합과 일치하고, 재실행 증가 0.
- 프론트엔드 타입체크·빌드 통과.

---

## 배포 (사용자 확인 후)

> `fly deploy` 와 `git push` 는 **사용자만** 한다. 아래는 사용자가 따라갈 순서다.

1. **머지 전 확인**: `pytest tests/ -q`(FAIL 1건만), `npm run type-check`, Task 8 리허설 결과.
2. **origin 정렬**: 로컬 `main` 이 origin 보다 앞서 있고 origin 에만 있는 커밋(KRPIA 문서)이 있으면 먼저 합친다.
3. **`fly deploy`** — 새 자격증명·secrets 는 필요 없다(신규 외부 API 없음). 다만 `fly.toml` 에 HTTP check 가 처음 들어가므로 **첫 배포에서 머신이 health 로 죽지 않는지** 확인한다(Task 4 의 grace 15분 + `/api/health` 가 DB 실패에만 503).
4. **배포 직후 스모크**: `python scripts/smoke.py --base https://<앱>` (로컬에서 실행, 공개 엔드포인트만으로도 1차 확인 가능). 실패 항목은 원장에 과제로 남는다.
5. **VPS 디스패처 갱신**: 런북 11개가 늘었으므로 `git pull --ff-only`. crontab 은 P1 의 `flock` 줄 그대로.
6. **다음날 09:00 원장 확인**: `/admin/agent-tasks` 에서 **17~19건**(예측 범위) 인지 본다. 이보다 많으면 어느 source 가 중복을 내는지 `source_ref` 로 추적한다 — 그 자체가 P1.5 의 실패 신호다.
7. **1주 후 점검**: 정상 상태에서 하루 ≤3건인지. 초과하면 임계치(Task 3 상수)를 조정한다.

## P2 로 넘김

P1 리뷰에서 넘어온 3건(`docs/superpowers/plans/2026-09-30-agent-team-P1-ops-sentinel.md` "P1 리뷰에서 P2 로 넘김")에 더해:

- **런북 202 + 폴링** — P1.5 Task 5 는 분리 모드(Popen + pid 가드)로 완화만 했다. 분리 런북의 `closed` 는 "요청 접수"이지 "복구 확인"이 아니다.
- **개선자(P3)가 `improvement` 과제를 집는 경로** — P1.5 가 만든 `improvement`(QG 제안·부채·코드패턴)는 지금 아무도 집지 않는다. 디스패처의 `CLAIMABLE_KINDS` 확장은 LLM 실행 경로(P2)가 선행이다.
- **amjilsim 리포터 3종 백엔드 구현**, **HIRA 차수 일정 fetcher 구현** — 원장 부채 과제 2건(Task 7).
- **데이터 정합성 조사**: `reimbursement_xnational` AU/PBAC 0행(스크레이퍼 실패인지 미구현인지 미확인), `foreign_drug_prices.is_us_listed` 0행, `download_log.process_status` 가 영구 `pending`(`agents/domestic_catchup.py:232-234` 이 `download_status` 만 갱신), `data/ma_intelligence.db` 0바이트.
- **자격증명 만료 프로브**(Micromedex/DocCheck/MIMS/NAVER/OPENAI/MFDS 로그인·키 확인 잡) — 해외약가 US/UK/DE 는 로컬에서 이미 반복 실패 중이다.
- **원장 보존 정책**: 과제·이벤트가 수만 행이 되면 목록 정렬이 temp B-tree 를 탄다. 커서 페이지네이션 또는 아카이브.

## 사람만 할 수 있는 것 (에이전트가 과제만 남기고 멈추는 지점)

| 종류 | 예 |
|---|---|
| 자격증명·토큰 | Micromedex / DocCheck / UK MIMS 라이선스, `NAVER_API_*`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `PERPLEXITY_API_KEY`, `MFDS_PATENT_API_KEY`, Gmail OAuth 재발급(`scripts/gmail_oauth_setup.py`) |
| 수동 데이터 입력 | HIRA 급여 체크리스트(`/admin/reimbursement`), 시장점유율 엑셀 업로드, Home 브랜드 후보 승인, analog PDF 코퍼스 반입, 정책 인텔 Gmail ingest 트리거 |
| 외부 구조 변경 | HIRA 게시판 마크업·셀렉터 변경(2026-09-01 `a.btn_file` 케이스처럼 런북 재실행으로 안 풀리고 3회 후 blocked), KEB 폼 변경, 각국 약가 사이트 개편 |
| 판단 | 급여 전략·RSA 해석, 경쟁사 뉴스의 사업적 함의, 배포 승인 |

## 자체 점검

- **스펙 커버리지**: 설계 §1-1(운영: 잡 실패·데이터 공백 자가 감지) → T1·T2·T3·T4; §4 운영 감시자의 "데이터 신선도 점검" → T3; §7 관측과 보고 → T4(`/api/health`·스모크) + T6(QG/RC); §5 원장 중복 억제 → 전 Task 의 `source_ref` 규칙. §6 저위험 자동 배포는 P4 범위로 손대지 않았다.
- **오탐 총량**: 첫 실행 17~19건(프로덕션 예측) → 정상 상태 ≤3건/일. 각 Task 완료 기준에 건수와 근거를 적었고, T8 Step 4 가 멱등을 기계로 확인한다.
- **플레이스홀더 없음**: 모든 Step 에 실제 코드·명령·기대 결과가 있다. placeholder 잡은 코드에서 내리고 원장 부채로 남겼다(T7).
- **이름 일관성**: `guarded_job`/`normalize_result`/`resolve_db_path`(jobs), `record_job_failure`/`record_job_missed`/`record_health_problems`/`record_freshness_problem`/`record_quality_review`/`record_compliance_results`/`record_known_debts`(signals), `DOMAIN_CHECKS`/`Ctx`(freshness), `write_heartbeat`/`heartbeat_age_seconds`(heartbeat), `list_runbooks`/`run`(runbooks) — 계획 전체에서 동일하게 썼다.
- **병렬 안전**: Task 경계 표의 `scheduler.py` 접촉 영역이 서로 겹치지 않는다(T1 이 골격, T2/T6/T7 은 각자 소유한 잡 본문, T3/T4 는 신규 잡 함수). `signals.py` 의 `JOB_RUNBOOK` 은 여러 Task 가 다른 행을 만지므로 머지 시 줄 충돌만 발생한다.
