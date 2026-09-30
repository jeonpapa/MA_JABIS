"""dispatch ↔ 실제 engine 통합 — 가짜는 바깥 경계에만 (Naver 클라이언트·JSON LLM·본문 fetch·Gmail send).

DispatchDeps.production(db_path=tmp) 을 그대로 쓰고 engine.EngineDeps.default 만 가짜로 바꾼다.
결정적·오프라인: 시각은 deps.now 주입, 네트워크 호출 없음. 실제 메일·LLM·Naver 는 절대 부르지 않는다.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from agents.daily_mailing import engine as engine_mod
from agents.daily_mailing.article_body import ArticleBody
from agents.daily_mailing.dispatch import DispatchDeps, approve_run, dispatch_due_subscriptions, health_check, send_test
from agents.daily_mailing.engine import EngineDeps
from agents.daily_mailing.llm import RELEVANCE_SYSTEM, LLMError
from agents.daily_mailing.run_store import find_regular_run, get_run, list_runs_for_date
from agents.daily_mailing.source_registry import SourceInfo, SourceRegistry
from agents.daily_mailing.storage import load_admin_kanban
from agents.daily_mailing.top_news import TOP_NEWS_OK, load_top_ma_news_entry

KST = timezone(timedelta(hours=9))
FRI_0810 = datetime(2026, 9, 18, 8, 10, tzinfo=KST)
SAT_0810 = FRI_0810 + timedelta(days=1)
MON_0810 = FRI_0810 + timedelta(days=3)
BODY = ("파드셉과 키트루다 병용요법이 요로상피암 1차 치료로 급여 확대 논의에 들어갔다. "
        "심평원 암질심은 다음 달 안건으로 상정할 예정이다. ") * 4

DDL = """CREATE TABLE mail_subscription (
    id INTEGER PRIMARY KEY AUTOINCREMENT, owner_email TEXT NOT NULL, name TEXT NOT NULL,
    keywords_json TEXT NOT NULL, media_json TEXT NOT NULL, schedule TEXT NOT NULL, time TEXT NOT NULL,
    week_day TEXT, emails_json TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
    companies_json TEXT NOT NULL DEFAULT '[]', brands_json TEXT NOT NULL DEFAULT '[]',
    policy_topics_json TEXT NOT NULL DEFAULT '[]', disease_areas_json TEXT NOT NULL DEFAULT '[]',
    custom_sources_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    last_sent_at TEXT, delivery_mode TEXT NOT NULL DEFAULT 'approval', include_top_ma_news INTEGER NOT NULL DEFAULT 1)"""


def _raw(title, url, pub="Fri, 18 Sep 2026 06:00:00 +0900"):
    return {"title": title, "originallink": url, "link": url, "description": f"{title} 설명", "pubDate": pub}


PADCEV = _raw("파드셉 요로상피암 급여 확대", "https://www.yakup.com/p1")
ENFLON = _raw("한국MSD 엔플론시아 RSV 예방", "https://www.yakup.com/m1")
PADCEV_FRI_PM = _raw("파드셉 암질심 통과", "https://www.yakup.com/p2", pub="Fri, 18 Sep 2026 15:00:00 +0900")


class FakeNaver:
    """바깥 경계 1: Naver 뉴스 검색 API."""
    def __init__(self, by_query):
        self.by_query, self.queries = by_query, []

    def search(self, query, display=20, sort="date"):
        self.queries.append(query)
        return self.by_query.get(query, [])


class FakeLLM:
    """바깥 경계 2: JSON LLM — 제목에 스콥 용어가 있으면 관련, 인용은 본문 첫 40자(검증 통과)."""
    def __init__(self, fail_relevance=False):
        self.fail_relevance, self.calls = fail_relevance, 0

    def complete_json(self, *, system, user, model):
        self.calls += 1
        data = json.loads(user)
        if system == RELEVANCE_SYSTEM:
            if self.fail_relevance:
                raise LLMError("down")
            hits = [t for t in data["scope_terms"] if t in data["title"]]
            return {"relevant": bool(hits), "matched_scope_terms": hits, "reason": "제목 주제" if hits else "무관"}
        return {"summary_bullets": [f"{data['title']} 요약"], "ma_implication": f"{data['title']} 시사점",
                "evidence_quote": data["article_body"][:40]}


class FakeGmail:
    """바깥 경계 4: Gmail send."""
    def __init__(self, ok=True):
        self.ok, self.sent = ok, []

    def send(self, *, recipients, subject, body_html):
        self.sent.append({"recipients": list(recipients), "subject": subject, "html": body_html})
        if not self.ok:
            return {"ok": False, "sent": False, "message": "invalid_grant"}
        return {"ok": True, "sent": True, "gmail_message_id": f"gm{len(self.sent)}"}


def _db(tmp_path, *, mode="approval", time="08:00"):
    db = tmp_path / "prod.db"
    with sqlite3.connect(db) as c:
        c.execute(DDL)
        c.execute("INSERT INTO mail_subscription (id, owner_email, name, keywords_json, media_json, schedule, time, "
                  "emails_json, brands_json, delivery_mode, created_at, updated_at) "
                  "VALUES (19, 'o@msd.com', '파드셉', '[]', '[]', 'Daily', ?, ?, ?, ?, 'x', 'x')",
                  (time, json.dumps(["r@msd.com"]), json.dumps(["파드셉"]), mode))
    return db


def _production(monkeypatch, db, *, naver, llm=None, now=FRI_0810, gmail=None, body_status="ok"):
    """실제 DispatchDeps.production — 엔진 의존성(EngineDeps.default)만 가짜 경계로 교체, send/now/gmail_ready 덮어씀."""
    registry = SourceRegistry([SourceInfo(name="약업신문", domains=("yakup.com",), source_tier="media_tier_A")])
    fake_engine = EngineDeps(naver=naver, llm=llm or FakeLLM(), registry=registry,
                             fetch_body=lambda p, n: ArticleBody(body_status, BODY if body_status == "ok" else "", p),
                             archive_db_path=str(db))   # competitor_news 없음 → 아카이브 후보 [] 로 정상 강등
    monkeypatch.setattr(engine_mod.EngineDeps, "default", classmethod(lambda cls, **kw: fake_engine))
    deps = DispatchDeps.production(db_path=db)
    gmail = gmail or FakeGmail()
    deps.send = gmail.send
    deps.now = lambda: now
    deps.gmail_ready = lambda: True
    return deps, gmail


def test_approval_flow_generates_with_real_engine_then_sends_on_approve(tmp_path, monkeypatch):
    db = _db(tmp_path)
    naver = FakeNaver({"파드셉": [PADCEV, ENFLON]})
    deps, gmail = _production(monkeypatch, db, naver=naver)

    summary = dispatch_due_subscriptions(deps)
    assert summary["errors"] == [] and summary["deferred"] == [] and len(summary["generated"]) == 1
    assert naver.queries[-1] == "파드셉" and gmail.sent == []            # 스콥 용어만 검색, 승인 전 미발송
    run = find_regular_run(19, "2026-09-18", db_path=db)
    assert (run["delivery_status"], run["approval_status"], run["status"]) == ("pending_approval", "pending", "ready")
    assert run["email_subject"] == "[MA Daily] 파드셉 · 9/18(금)"
    assert "파드셉 요로상피암 급여 확대" in run["email_html"] and "엔플론시아" not in run["email_html"]
    assert run["lookback_hours"] == 24 and run["window_label"] == "이전 24시간"
    assert json.loads(run["recipients_json"]) == ["r@msd.com"]
    top = load_top_ma_news_entry("2026-09-18", db_path=db)
    assert top["status"] == TOP_NEWS_OK and top["items"] == []           # 주요 MA 뉴스는 하루 1회 캐시
    assert health_check(deps)["ok"] is True                              # 승인 대기는 문제 아님

    # 10분 뒤 tick — 슬롯 점유로 재생성 없음 (Naver·LLM 재호출 없음)
    queries_before = len(naver.queries)
    again = dispatch_due_subscriptions(deps)
    assert again["skipped"] == 1 and again["generated"] == [] and len(naver.queries) == queries_before

    sent = approve_run(run["run_id"], approver="admin@msd.com", deps=deps)
    assert (sent["delivery_status"], sent["approved_by"], sent["gmail_message_id"]) == ("sent", "admin@msd.com", "gm1")
    assert sent["sent_at"] and sent["send_attempts"] == 1
    assert [m["recipients"] for m in gmail.sent] == [["r@msd.com"]]
    assert gmail.sent[0]["subject"] == "[MA Daily] 파드셉 · 9/18(금)" and "파드셉 요로상피암" in gmail.sent[0]["html"]
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT last_sent_at FROM mail_subscription WHERE id=19").fetchone()[0] == sent["sent_at"]
    assert health_check(deps)["ok"] is True
    board = load_admin_kanban(db)
    assert board["runs"][0]["delivery_status"] == "sent" and board["runs"][0]["has_email_html"] is True


def test_auto_mode_weekday_rule_and_monday_lookback_reach_the_engine(tmp_path, monkeypatch):
    """토요일: 생성 없음. 월요일: 스콥 lookback 72h 가 엔진까지 전달돼 금요일 오후 기사가 포함된다."""
    db = _db(tmp_path, mode="auto")
    naver = FakeNaver({"파드셉": [PADCEV_FRI_PM]})   # 금 15:00 발행 — 월 08:10 기준 65시간 전
    deps, gmail = _production(monkeypatch, db, naver=naver, now=SAT_0810)
    assert dispatch_due_subscriptions(deps)["generated"] == [] and naver.queries == []
    assert list_runs_for_date("2026-09-19", db_path=db) == []

    deps.now = lambda: MON_0810
    summary = dispatch_due_subscriptions(deps)
    assert summary["generated"][0]["delivery_status"] == "sent"
    run = find_regular_run(19, "2026-09-21", db_path=db)
    assert (run["lookback_hours"], run["window_label"], run["status"]) == (72, "이전 72시간", "ready")
    assert "파드셉 암질심 통과" in run["email_html"]
    assert [m["recipients"] for m in gmail.sent] == [["r@msd.com"]] and run["email_subject"] == "[MA Daily] 파드셉 · 9/21(월)"


def test_send_test_uses_real_engine_and_goes_to_requester_only(tmp_path, monkeypatch):
    db = _db(tmp_path, time="09:00")
    deps, gmail = _production(monkeypatch, db, naver=FakeNaver({"파드셉": [PADCEV]}))
    out = send_test(19, requester_email="me@msd.com", deps=deps)
    assert (out["status"], out["delivery_status"]) == ("ready", "sent")
    assert gmail.sent[0]["recipients"] == ["me@msd.com"]
    assert gmail.sent[0]["subject"] == "[TEST] [MA Daily] 파드셉 · 9/18(금)"
    assert find_regular_run(19, "2026-09-18", db_path=db) is None       # 정규 슬롯은 비어 있음
    assert get_run(out["run_id"], db_path=db)["is_test"] == 1
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT last_sent_at FROM mail_subscription WHERE id=19").fetchone()[0] is None


def test_llm_outage_holds_run_and_gmail_failure_is_retried_then_recovers(tmp_path, monkeypatch):
    """설계 §8: LLM 전면 장애 → held(자동 모드도 미발송, 헬스체크 문제). Gmail 실패 → send_failed → 다음 주기 재시도 성공."""
    db = _db(tmp_path, mode="auto")
    deps, gmail = _production(monkeypatch, db, naver=FakeNaver({"파드셉": [PADCEV]}), llm=FakeLLM(fail_relevance=True))
    dispatch_due_subscriptions(deps)
    run = find_regular_run(19, "2026-09-18", db_path=db)
    assert (run["status"], run["delivery_status"]) == ("held", "held") and gmail.sent == []
    assert "llm_unavailable" in json.loads(run["quality_report_json"])["blocking_reasons"]
    assert {p["problem"] for p in health_check(deps)["problems"]} == {"held"}

    (tmp_path / "second").mkdir()
    db2 = _db(tmp_path / "second", mode="auto")
    flaky = FakeGmail(ok=False)
    deps2, _ = _production(monkeypatch, db2, naver=FakeNaver({"파드셉": [PADCEV]}), gmail=flaky)
    dispatch_due_subscriptions(deps2)
    run = find_regular_run(19, "2026-09-18", db_path=db2)
    assert (run["delivery_status"], run["send_attempts"], run["delivery_error"]) == ("send_failed", 1, "invalid_grant")
    assert {p["problem"] for p in health_check(deps2)["problems"]} == {"send_failed"}
    flaky.ok = True
    deps2.now = lambda: FRI_0810 + timedelta(minutes=10)
    assert dispatch_due_subscriptions(deps2)["retried"] == 1
    run = find_regular_run(19, "2026-09-18", db_path=db2)
    assert (run["delivery_status"], run["send_attempts"], run["gmail_message_id"]) == ("sent", 2, "gm2")
    assert health_check(deps2)["ok"] is True
