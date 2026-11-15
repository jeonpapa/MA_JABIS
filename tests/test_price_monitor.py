"""해외약가 모니터링(2026-11-10): 주기·선행 시작·수신자 도메인·비교·미확인 판정·재요청·워커 메일·텔레그램. 임시 DB, 실제 발송 없음."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from agents.price_monitor import schedule as sch
from agents.price_monitor.diff import compare_country, compare_runs
from agents.price_monitor.store import MonitorStore, validate_recipients
from agents.price_monitor import tick as tk
from agents.price_monitor import report
from agents.price_request_store import PriceRequestStore, validate_verdict

KST = sch.KST


# ── 주기 ───────────────────────────────────────────────────────────────────
def test_next_due_weekly_monthly_dates():
    s = sch.validate_schedule({"type": "weekly", "weekdays": [1, 4], "hour": 9})
    at = datetime(2026, 11, 10, 10, 0, tzinfo=KST)          # 화요일 10시
    assert sch.next_due(s, at) == datetime(2026, 11, 12, 9, 0, tzinfo=KST)   # 목요일 09시
    m = sch.validate_schedule({"type": "monthly", "days": [31], "hour": 8})
    assert sch.next_due(m, datetime(2026, 11, 1, tzinfo=KST)) == datetime(2026, 11, 30, 8, 0, tzinfo=KST)   # 11월 31일 없음 → 말일
    d = sch.validate_schedule({"type": "dates", "dates": ["2026-12-01", "2026-11-20"], "hour": 9})
    assert sch.next_due(d, datetime(2026, 11, 21, tzinfo=KST)) == datetime(2026, 12, 1, 9, tzinfo=KST)
    assert sch.next_due(d, datetime(2026, 12, 2, tzinfo=KST)) is None
    with pytest.raises(ValueError):
        sch.validate_schedule({"type": "weekly", "weekdays": [], "hour": 9})


def test_lead_hours_scales_with_items_and_has_floor():
    assert sch.lead_hours(1, list(sch.A8)) == sch.MIN_LEAD_HOURS            # 3 + 12 < 30
    assert sch.lead_hours(20, list(sch.A8)) == 20 * sch.HOURS_PER_ITEM_A8 + 12
    assert sch.lead_hours(20, ["JP", "US"]) < sch.lead_hours(20, list(sch.A8))


def test_recipients_company_domain_only(monkeypatch):
    monkeypatch.setenv("MONITOR_MAIL_DOMAINS", "msd.com")
    assert validate_recipients("a@msd.com, B@MSD.com; a@msd.com") == ["a@msd.com", "b@msd.com"]
    assert validate_recipients(["x@kr.msd.com"]) == ["x@kr.msd.com"]
    with pytest.raises(ValueError):
        validate_recipients(["me@gmail.com"])
    with pytest.raises(ValueError):
        validate_recipients(["a@notmsd.com"])


# ── 비교 ───────────────────────────────────────────────────────────────────
def row(price, label="RED BOOK (WAC)", fk="40mg|oral", pc=90, krw=None):
    return {"formulation_key": fk, "pack_count": pc, "local_price": price, "currency": "USD", "source_label": label,
            "product_name": "WELIREG", "package_unit": f"{pc} tablets", "adjusted_price_krw": krw}


def test_compare_country_kinds():
    p = {"status": "priced", "rows": [row(100.0, krw=1000)]}
    assert compare_country(p, {"status": "priced", "rows": [row(100.0, krw=1000)]})["kind"] == "same"
    d = compare_country(p, {"status": "priced", "rows": [row(95.0)]})
    assert d["kind"] == "price" and d["changes"][0]["pct"] == -0.05
    assert compare_country(p, {"status": "priced", "rows": [row(80.0, label="Other list")]})["kind"] == "basis"
    assert compare_country(p, {"status": "priced", "rows": [row(100.0), row(50.0, pc=30)]})["kind"] == "packs"
    assert compare_country(p, {"status": "unconfirmed", "rows": []})["kind"] == "lost"
    assert compare_country({"status": "failed", "rows": []}, {"status": "priced", "rows": [row(1.0)]})["kind"] == "now_priced"
    fx = compare_country(p, {"status": "priced", "rows": [row(100.0, krw=1100)]})
    assert fx["kind"] == "same" and fx["fx"]["pct"] == 0.1          # 원화만 바뀜 → 가격 변동 아님


def test_compare_runs_totals_and_first():
    cur = {"items": [{"query": "a", "label": "A", "countries": {"US": {"status": "priced", "rows": [row(90.0)]}}}]}
    first = compare_runs(None, cur)
    assert not first["has_previous"] and first["totals"]["first"] == 1 and first["highlight_count"] == 0
    prev = {"items": [{"query": "a", "label": "A", "countries": {"US": {"status": "priced", "rows": [row(100.0)]}}}]}
    d = compare_runs(prev, cur)
    assert d["totals"]["price"] == 1 and d["highlight_count"] == 1 and d["items"][0]["highlight"]


def test_verdict_requires_independent_sources():
    with pytest.raises(ValueError):
        validate_verdict("not_marketed", [{"url": "https://a.org/x", "finding": "검색 0건"}])
    with pytest.raises(ValueError):   # 같은 사이트 2개는 독립 출처가 아니다
        validate_verdict("not_marketed", [{"url": "https://a.org/x", "finding": "0건"}, {"url": "https://a.org/y", "finding": "0건"}])
    ok = validate_verdict("not_marketed", [{"url": "https://a.org/x", "finding": "가격 0건"}, {"url": "https://fda.gov/ndc", "finding": "NDC 없음"}])
    assert len(ok) == 2
    assert validate_verdict("access_blocked", [{"url": "https://a.org/login", "finding": "로그인 차단"}])
    with pytest.raises(ValueError):
        validate_verdict("guess", [{"url": "https://a.org", "finding": "x"}])


# ── 전체 흐름 ──────────────────────────────────────────────────────────────
@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("MONITOR_MAIL_DOMAINS", "msd.com")
    monkeypatch.delenv("TELEGRAM_MONITOR_BOT_TOKEN", raising=False)
    path = tmp_path / "t.db"
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE foreign_drug_prices (id INTEGER PRIMARY KEY, query_name TEXT, country TEXT, product_name TEXT, "
                  "package_unit TEXT, pack_count INTEGER, local_price REAL, currency TEXT, source_label TEXT, formulation_key TEXT, "
                  "searched_at TEXT, adjusted_price_krw REAL)")
    return path


def _worker_complete(ps, rid, outcomes):
    """outcomes: {cc: price | ('unconfirmed', verdict, sources)}"""
    tok = ps.claim(rid, "worker")["lease_token"]
    for cc, o in outcomes.items():
        if isinstance(o, tuple):
            ps.accept(rid, cc, "worker", tok, o[0], "검색 0건", [], cc + "x", o[1], o[2])
        else:
            ps.accept(rid, cc, "worker", tok, "priced", None,
                      [{"query_name": "x", "country": cc, "product_name": "DRUGX", "package_unit": "1 vial", "pack_count": 1,
                        "local_price": o, "currency": "USD", "source_label": "List", "formulation_key": "1mg|injection",
                        "searched_at": "2026-11-10T00:00:00"}], cc + str(o))
    d = ps.detail(rid)
    ids = sorted(i for x in d["country_results"] for i in x["row_ids"])
    return ps.verify(rid, "worker", tok, ids, "proof.png", d["dashboard_url"])


def test_full_cycle_retry_worker_mail_and_telegram(db):
    ms, ps = MonitorStore(db), PriceRequestStore(db)
    with pytest.raises(ValueError):
        ms.create_project("u@msd.com", "P", ["drugx"], ["US"], {"type": "weekly", "weekdays": [4], "hour": 9}, ["x@gmail.com"])
    p = ms.create_project("u@msd.com", "주간 감시", ["drugx"], ["US", "FR"], {"type": "weekly", "weekdays": [4], "hour": 9}, ["a@msd.com"])
    t0 = datetime(2026, 11, 10, 10, 0, tzinfo=KST)               # 화 → 목 09:00 발송, 30시간 전 = 수 03:00 시작
    sent_tg = []
    fake_tg = lambda text: (sent_tg.append(text), {"ok": True})[1]
    r = tk.tick(db, t0, gmail_factory=lambda: None, tg_send=fake_tg)
    assert len(r["scheduled"]) == 1 and r["started"] == []
    run_id = r["scheduled"][0]
    run = ms.get_run(run_id)
    assert run["status"] == "scheduled" and run["due_at"].startswith("2026-11-12T00:00")

    t1 = datetime(2026, 11, 11, 4, 0, tzinfo=KST)                # 시작 시각 지남 + 발송 29시간 전
    r = tk.tick(db, t1, gmail_factory=lambda: None, tg_send=fake_tg)
    assert r["started"] == [run_id]
    run = ms.get_run(run_id)
    rid = run["requests"]["drugx"][0]
    req = ps.detail(rid)
    assert req["origin"] == "monitor:" + run_id and req["countries"] == ["FR", "US"]

    # 모니터링 요청은 판정 없는 미확인을 받지 않는다
    tok = ps.claim(rid, "worker")["lease_token"]
    with pytest.raises(ValueError):
        ps.accept(rid, "FR", "worker", tok, "unconfirmed", "0건", [], "h1")
    with sqlite3.connect(db) as c:   # lease 반납(테스트용)
        c.execute("UPDATE price_requests SET lease_until=NULL WHERE id=?", (rid,))
    done = _worker_complete(ps, rid, {"US": 100.0, "FR": ("failed", "access_blocked", [{"url": "https://legifrance.gouv.fr/x", "finding": "Cloudflare 403"}])})
    assert done["status"] == "partial_completed" and done["notification_status"] == "not_required"   # 요청별 메일 없음

    r = tk.tick(db, t1 + timedelta(minutes=10), gmail_factory=lambda: None, tg_send=fake_tg)
    run = ms.get_run(run_id)
    assert run["status"] == "collecting" and len(run["requests"]["drugx"]) == 2       # 접속 차단 국가만 재요청
    retry = ps.detail(run["requests"]["drugx"][1])
    assert retry["countries"] == ["FR"] and retry["origin"] == "monitor:" + run_id
    _worker_complete(ps, retry["id"], {"FR": ("unconfirmed", "not_listed",
                                              [{"url": "https://legifrance.gouv.fr/a", "finding": "가격 공시 없음"},
                                               {"url": "https://base-donnees-publique.medicaments.gouv.fr/b", "finding": "비급여(remboursement 없음)"}])})
    r = tk.tick(db, t1 + timedelta(minutes=20), gmail_factory=lambda: None, tg_send=fake_tg)
    assert r["ready"] == [run_id] and r["sent"] == []
    run = ms.get_run(run_id)
    assert run["mail_via"] == "worker" and run["snapshot"]["items"][0]["countries"]["FR"]["verdict"] == "not_listed"
    assert run["snapshot"]["items"][0]["countries"]["US"]["rows"][0]["local_price"] == 100.0
    assert not any("내일 해외약가 모니터링" in m for m in sent_tg)                      # 아직 24시간 전이 아님
    tk.tick(db, datetime(2026, 11, 11, 9, 30, tzinfo=KST), gmail_factory=lambda: None, tg_send=fake_tg)
    eve = [m for m in sent_tg if "내일 해외약가 모니터링" in m]
    assert len(eve) == 1 and "발송 대기" in eve[0]                                     # 하루 전 안내(1회)
    tk.tick(db, datetime(2026, 11, 11, 9, 40, tzinfo=KST), gmail_factory=lambda: None, tg_send=fake_tg)
    assert len([m for m in sent_tg if "내일 해외약가 모니터링" in m]) == 1

    # 워커 메일
    assert [x["id"] for x in ms.mail_queue()] == [run_id]
    lease = ms.mail_claim(run_id, "worker")["lease_token"]
    content = tk.mail_content(ms, run_id)
    assert content["recipients"] == ["a@msd.com"] and "첫 회차" in content["subject"] and content["attachment"][:2] == b"PK"
    with pytest.raises(Exception):
        ms.mail_notify(run_id, "worker", lease, "sent", "gmail:example")
    ms.mail_notify(run_id, "worker", lease, "begin")
    ms.mail_notify(run_id, "worker", lease, "sent", "gmail:1a1aec9f738f2a20")
    tk.tick(db, t1 + timedelta(minutes=30), gmail_factory=lambda: None, tg_send=fake_tg)
    assert ms.get_run(run_id)["status"] == "sent" and any("모니터링 완료" in m for m in sent_tg)

    # 다음 회차: 가격 인하 → 하이라이트
    tk.tick(db, t1 + timedelta(minutes=40), gmail_factory=lambda: None, tg_send=fake_tg)
    run2 = [x["id"] for x in ms.list_runs(p["id"]) if x["id"] != run_id][0]   # 발송이 끝나면 바로 다음 회차 예약
    assert ms.get_run(run2)["due_at"].startswith("2026-11-19")
    t2 = datetime(2026, 11, 18, 4, 0, tzinfo=KST)
    tk.tick(db, t2, gmail_factory=lambda: None, tg_send=fake_tg)
    rid2 = ms.get_run(run2)["requests"]["drugx"][0]
    _worker_complete(ps, rid2, {"US": 90.0, "FR": ("unconfirmed", "not_listed",
                                                   [{"url": "https://legifrance.gouv.fr/a", "finding": "가격 공시 없음"},
                                                    {"url": "https://base-donnees-publique.medicaments.gouv.fr/b", "finding": "비급여"}])})
    tk.tick(db, t2 + timedelta(minutes=10), gmail_factory=lambda: None, tg_send=fake_tg)
    run = ms.get_run(run2)
    assert run["diff"]["totals"]["price"] == 1 and run["diff"]["totals"]["unpriced"] == 1
    html, _ = report.build_html(ms.get_project(p["id"]), run, run["snapshot"], run["diff"], "https://x")
    assert "가격 변동" in html and "-10.0%" in html and "가격·급여 목록 미등재" in html


def test_server_gmail_path_sends_with_attachment(db):
    ms, ps = MonitorStore(db), PriceRequestStore(db)
    ms.create_project("u@msd.com", "P", ["drugx"], ["US"], {"type": "dates", "dates": ["2026-11-12"], "hour": 9}, ["a@msd.com"])
    t = datetime(2026, 11, 11, 4, 0, tzinfo=KST)
    tk.tick(db, t, gmail_factory=lambda: None, tg_send=lambda m: {"ok": True})
    run = ms.list_runs(ms.list_projects()[0]["id"])[0]
    _worker_complete(ps, run["requests"]["drugx"][0], {"US": 10.0})
    sent = {}

    class Svc:
        def users(self): return self
        def messages(self): return self
        def send(self, userId, body):
            sent["raw"] = body["raw"]
            return self
        def execute(self): return {"id": "abc123def4567890"}
    r = tk.tick(db, t + timedelta(minutes=10), gmail_factory=lambda: Svc(), tg_send=lambda m: {"ok": True})
    assert r["sent"] == [run["id"]]
    got = ms.get_run(run["id"])
    assert got["status"] == "sent" and got["mail_via"] == "server" and got["mail_receipt"] == "gmail:abc123def4567890"
    import base64
    raw = base64.urlsafe_b64decode(sent["raw"]).decode("utf-8", "ignore")
    assert "a@msd.com" in raw and ".xlsx" in raw


def test_settings_change_reschedules_unstarted_run(db):
    ms = MonitorStore(db)
    p = ms.create_project("u@msd.com", "P", ["a", "b"], ["US"], {"type": "weekly", "weekdays": [4], "hour": 9}, ["a@msd.com"])
    tk.schedule_runs(ms, datetime(2026, 11, 10, 10, tzinfo=KST))
    ms.update_project(p["id"], "u@msd.com", False, {"schedule": {"type": "weekly", "weekdays": [5], "hour": 9}, "items": ["a", "c"]})
    assert ms.list_runs(p["id"]) == []                          # 예약 회차 취소
    tk.schedule_runs(ms, datetime(2026, 11, 10, 10, tzinfo=KST))
    assert ms.list_runs(p["id"])[0]["due_at"].startswith("2026-11-13")
    assert [i["query"] for i in ms.get_project(p["id"])["items"]] == ["a", "c"]
    with pytest.raises(KeyError):
        ms.update_project(p["id"], "other@msd.com", False, {"name": "x"})


# ── API (Flask test client — api.server 를 불러오지 않는다) ───────────────
def test_monitor_routes_crud_permissions_and_worker_mail(tmp_path, monkeypatch):
    pytest.importorskip("flask")
    monkeypatch.setenv("MONITOR_MAIL_DOMAINS", "msd.com")
    from flask import Flask
    from api.auth import _issue_token
    from api.price_monitor_routes import build_price_monitor_blueprint
    path = tmp_path / "r.db"
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE foreign_drug_prices (id INTEGER PRIMARY KEY, query_name TEXT, country TEXT, product_name TEXT, "
                  "package_unit TEXT, pack_count INTEGER, local_price REAL, currency TEXT, source_label TEXT, formulation_key TEXT, searched_at TEXT)")
    app = Flask(__name__)
    app.testing = True
    app.register_blueprint(build_price_monitor_blueprint(path))
    cl = app.test_client()
    h = lambda sub, role="user": {"Authorization": "Bearer " + _issue_token(sub, role)}
    u, other, worker = h("u@msd.com"), h("o@msd.com"), h("w@x.org", "price_agent")
    body = {"name": "감시", "items": ["drugx", {"query": "drugy"}], "countries": ["US", "JP"],
            "schedule": {"type": "weekly", "weekdays": [1], "hour": 9}, "recipients": "a@msd.com"}
    assert cl.post("/api/price-monitor/projects", json={**body, "recipients": "a@gmail.com"}, headers=u).status_code == 400
    r = cl.post("/api/price-monitor/projects", json=body, headers=u)
    assert r.status_code == 201
    pid = r.get_json()["project"]["id"]
    assert r.get_json()["project"]["countries"] == ["JP", "US"]
    assert cl.get(f"/api/price-monitor/projects/{pid}", headers=other).status_code == 404       # 남의 프로젝트
    assert [p["id"] for p in cl.get("/api/price-monitor/projects", headers=other).get_json()["projects"]] == []
    cfg = cl.get("/api/price-monitor/config", headers=u).get_json()
    assert cfg["domains"] == ["msd.com"] and "not_marketed" in cfg["verdicts"]
    run = cl.post(f"/api/price-monitor/projects/{pid}/run-now", headers=u)
    assert run.status_code == 201 and run.get_json()["run"]["status"] == "collecting"
    assert cl.post(f"/api/price-monitor/projects/{pid}/run-now", headers=u).status_code == 409
    rid = run.get_json()["run"]["id"]
    detail = cl.get(f"/api/price-monitor/runs/{rid}", headers=u).get_json()["run"]
    assert {p["query"] for p in detail["progress"]} == {"drugx", "drugy"}
    assert cl.get("/api/price-agent/monitor-mails", headers=u).status_code == 403                 # 워커 전용
    assert cl.get("/api/price-agent/monitor-mails", headers=worker).get_json()["runs"] == []
    assert cl.patch(f"/api/price-monitor/projects/{pid}", json={"status": "paused"}, headers=u).get_json()["project"]["status"] == "paused"
    assert cl.delete(f"/api/price-monitor/projects/{pid}", headers=other).status_code == 404
    assert cl.delete(f"/api/price-monitor/projects/{pid}", headers=u).status_code == 200
