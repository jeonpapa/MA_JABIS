"""급여 파이프라인 — '최근 상태'·마지막 결과·카드 신선도·NHIS 대체 행·보고서 날짜 (2026-10-17 사용자 테스트).

재현 (프로덕션 2026-10-17):
- 웰리렉 admin '최근 상태' = 암질심 재심의 2025-12-10 인데 이벤트에 더 최근 2026-03-20 암질심 대기(차수 미연결)가 있음.
  원인: observed_at(수집 시각) 기준 max — 과거 차수(2025-12-10)가 나중(2026-06-12)에 백필되어 최신처럼 보였다.
- 자카비: 협상 AGREED 인데 약평위 통과일 미확인 → 단계 '약평위' 로 보임.
- 키트루다·파드셉: 공단 협상 완료(2026-09, 공단 공식) 인데 nhis_negotiations 에 같은 협상의 '협상 중' 아카이브 행이 남아
  in_progress 조회가 이미 끝난 협상까지 셌다 (56건 중 41건).
- 보고서 session_date(2026-09-29) 는 D-1 사전검토 '작성일' — 회의일(8차 암질심 2026-09-30)과 분리 표시해야 함.

네트워크·실제 DB 금지: 임시 sqlite 로 DB_PATH 교체.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agents.reimb_pipeline as rp  # noqa: E402
import agents.reimb_reports as rr  # noqa: E402

_SCHEMA = """
CREATE TABLE amjilsim_sessions (
    session_id INTEGER PRIMARY KEY, year INTEGER NOT NULL, ordinal_assumed INTEGER NOT NULL DEFAULT 0,
    ordinal_official INTEGER, session_date DATE NOT NULL, status TEXT NOT NULL DEFAULT 'SCHEDULED',
    official_minutes_url TEXT, note TEXT, created_at TEXT, committee_type TEXT NOT NULL DEFAULT 'AMJILSIM'
);
CREATE TABLE amjilsim_drugs (
    drug_id INTEGER PRIMARY KEY, product_slug TEXT, brand_kr TEXT NOT NULL, brand_en TEXT, ingredient_inn TEXT,
    atc TEXT, manufacturer TEXT, msd_flag INTEGER NOT NULL DEFAULT 0, competitor_class TEXT, first_seen_at TEXT,
    tracking_priority TEXT NOT NULL DEFAULT 'generic_new_drug', amjilsim_pass_date DATE, yakpyungwi_pass_date DATE,
    negotiation_status TEXT, indication TEXT, listing_type TEXT, submitted_date DATE, notes TEXT,
    expected_session_id INTEGER, key_issues TEXT, negotiation_complete_date TEXT, negotiation_date_source TEXT,
    nhis_registered_ym TEXT, efficacy_group TEXT
);
CREATE TABLE amjilsim_drug_queue_status (
    id INTEGER PRIMARY KEY AUTOINCREMENT, drug_id INTEGER NOT NULL, session_id INTEGER, queue_state TEXT NOT NULL,
    queue_entry_date DATE, n_th_attempt INTEGER NOT NULL DEFAULT 1, evidence_url TEXT, observed_at TEXT,
    committee_type TEXT NOT NULL DEFAULT 'AMJILSIM'
);
CREATE TABLE nhis_negotiations (
    id INTEGER PRIMARY KEY AUTOINCREMENT, list_type TEXT NOT NULL, product_name TEXT NOT NULL, manufacturer TEXT,
    efficacy_group TEXT, registered_ym TEXT, result TEXT, completed_ym TEXT, source_url TEXT NOT NULL,
    content_hash TEXT NOT NULL, drug_id INTEGER, first_seen_at TEXT, fetched_at TEXT
);
"""

SESSIONS = [  # (id, year, ord_assumed, ord_official, date, status, committee)
    (32, 2025, 9, 9, "2025-12-10", "COMPLETED", "AMJILSIM"),
    (23, 2025, 9, 9, "2025-09-04", "COMPLETED", "YAKPYUNGWI"),
    (1, 2026, 1, None, "2026-01-21", "COMPLETED", "AMJILSIM"),
    (13, 2026, 4, None, "2026-04-02", "COMPLETED", "YAKPYUNGWI"),
    (7, 2026, 8, 8, "2026-09-30", "COMPLETED", "AMJILSIM"),
    (19, 2026, 10, 10, "2026-10-01", "COMPLETED", "YAKPYUNGWI"),
    (15, 2026, 6, None, "2026-06-04", "COMPLETED", "YAKPYUNGWI"),
    (8, 2026, 9, 9, "2026-11-11", "SCHEDULED", "AMJILSIM"),
    (20, 2026, 11, None, "2026-11-05", "SCHEDULED", "YAKPYUNGWI"),
]

WELIREG_ISSUE = ('["9차 암질심(2025-12-10 brdBltNo=11684) 미설정 — VHL병 신세포암·CNS 혈관모세포종·췌장 신경내분비종양", '
                 '"MSD 핵심 추적 자산 — 재상정 trajectory 관리 (Welireg/belzutifan)"]')

# (drug_id, brand_kr, manufacturer, amj_pass, yak_pass, neg, indication, notes, key_issues, neg_done, neg_src, nhis_reg, efficacy)
DRUGS = [
    (1, "웰리렉", "한국MSD", None, None, None, None, None, WELIREG_ISSUE, None, None, None, None),
    (2, "키트루다", "한국MSD", None, "2025-09-04", "AGREED", "11개 적응증", "9차 약평위 통과", None,
     "2026-09", "nhis_official", "2026-05", "항악성종양제"),
    (9, "파드셉주 20·30mg", "한국아스텔라스제약", None, "2026-04-02", "AGREED", "요로상피암 1차 (펨브로 병용)",
     "4차 약평위 통과. 펨브로(키트루다) 병용 적응증.", None, "2026-09", "nhis_official", "2026-05", "항악성종양제"),
    (37, "자카비", "한국노바티스", "2026-01-21", None, "AGREED", "진성적혈구증가증 PV 2차", None, None,
     None, None, None, None),
]

QUEUE = [  # (id, drug_id, session_id, state, entry_date, evidence, observed_at, committee)
    (1, 1, None, "QUEUE_PENDING", "2026-03-20", None, "2026-05-28 04:18:43", "AMJILSIM"),
    (75, 1, 32, "REJECTED_REQUEUE", "2025-12-10", "HIRA brdBltNo=11684 (9차 암질심 VHL병 신세포암 등 미설정)",
     "2026-06-12 05:09:31", "AMJILSIM"),
    (38, 2, 23, "APPROVED", "2025-09-04", "매체: 메디포뉴스 206439 — HIRA 본문 미verified", "2026-06-11 23:06:24",
     "YAKPYUNGWI"),
    (9, 9, 13, "APPROVED", "2026-04-02", "HIRA brdBltNo=11763", "2026-06-11 23:06:24", "YAKPYUNGWI"),
    (40, 37, 1, "APPROVED", "2026-01-21", "https://www.kpanews.co.kr/article/534206", "2026-06-11 23:06:24", "AMJILSIM"),
]

U1 = "https://www.nhis.or.kr/nhis/together/retrieveMediList.do"
U2 = "https://www.nhis.or.kr/nhis/together/retrieveMediList2.do"
NHIS = [  # (id, list_type, product, reg, result, done, url, hash, drug_id, first_seen, fetched)
    (42, "확대", "키트루다주(펨브롤리주맙,유전자재조합)_(0.1g/4mL)", "2026-05", "-", None, U2, "h42", 2,
     "2026-06-14 05:38:41", "2026-09-27 17:30:10"),
    (114, "확대", "키트루다주(펨브롤리주맙,유전자재조합)_(0.1g/4mL)", "2026-05", "합의", "2026-09", U2, "h114", 2,
     "2026-10-11 17:30:07", "2026-10-11 17:30:07"),
    (50, "확대", "키트루다주(펨브롤리주맙, 유전자재조합)_(0.1g/4mL)", "2025-10", "합의", "2025-12", U2, "h50", 2,
     "2026-06-14 05:38:41", "2026-10-11 17:30:07"),
    (2, "신규", "파드셉주 20밀리그램,30밀리그램_(엔포투맙베도틴)", "2026-05", "-", None, U1, "h2", 9,
     "2026-06-14 05:38:41", "2026-06-14 17:30:08"),
    (55, "신규", "파드셉주 20밀리그램,30밀리그램(엔포투맙베도틴)", "2026-05", "-", None, U1, "h55", 9,
     "2026-07-12 17:30:07", "2026-09-27 17:30:10"),
    (92, "신규", "파드셉주 20밀리그램,30밀리그램(엔포투맙베도틴)", "2026-05", "합의", "2026-09", U1, "h92", 9,
     "2026-10-11 17:30:07", "2026-10-11 17:30:07"),
    # 최신 수집에서도 '협상 중' 으로 떠 있는 행 — 같은 달 완료 행(동명 제품)이 있어도 숨기지 않는다
    (120, "신규", "가상제품주", "2026-08", "-", None, U1, "h120", None, "2026-08-30 00:00:00", "2026-10-11 17:30:05"),
    (121, "신규", "가상제품주", "2026-08", "합의", "2026-10", U1, "h121", None, "2026-10-11 00:00:00",
     "2026-10-11 17:30:06"),
    # 최신 수집에서 사라졌지만 완료 짝이 없는 행 — 대체 근거 없음 → 유지
    (130, "확대", "고아제품정", "2026-03", "-", None, U2, "h130", None, "2026-04-01 00:00:00", "2026-07-05 17:30:08"),
]


class _FrozenDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 10, 17)


@pytest.fixture
def db(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "reimb.db"
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    conn.executemany("INSERT INTO amjilsim_sessions (session_id, year, ordinal_assumed, ordinal_official, "
                     "session_date, status, committee_type) VALUES (?,?,?,?,?,?,?)", SESSIONS)
    conn.executemany("INSERT INTO amjilsim_drugs (drug_id, brand_kr, manufacturer, amjilsim_pass_date, "
                     "yakpyungwi_pass_date, negotiation_status, indication, notes, key_issues, "
                     "negotiation_complete_date, negotiation_date_source, nhis_registered_ym, efficacy_group) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", DRUGS)
    conn.executemany("INSERT INTO amjilsim_drug_queue_status (id, drug_id, session_id, queue_state, "
                     "queue_entry_date, evidence_url, observed_at, committee_type) VALUES (?,?,?,?,?,?,?,?)", QUEUE)
    conn.executemany("INSERT INTO nhis_negotiations (id, list_type, product_name, registered_ym, result, "
                     "completed_ym, source_url, content_hash, drug_id, first_seen_at, fetched_at) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?)", NHIS)
    conn.commit()
    conn.close()
    monkeypatch.setattr(rp, "DB_PATH", path)
    monkeypatch.setattr(rp, "date", _FrozenDate)
    return path


def _admin() -> dict[str, dict]:
    return {d["brand_kr"]: d for d in rp.list_drugs_admin()}


def _board() -> dict[str, dict]:
    out = {}
    for stage in rp.get_pipeline(include_completed=True)["stages"]:
        for d in stage["drugs"]:
            out[d["name"]] = {**d, "_stage": stage["id"]}
    return out


# ── 1. 최근 상태 = 사건일 기준 ────────────────────────────────────────────────

def test_welireg_latest_state_uses_event_date_not_observed_at(db):
    w = _admin()["웰리렉"]
    assert w["latest_queue"]["id"] == 1, "2026-03-20 대기(미연결)가 2025-12-10 재심의보다 최신"
    assert w["latest_queue"]["state"] == "QUEUE_PENDING"
    assert w["latest_queue"]["event_date"] == "2026-03-20"
    assert w["last_result"]["state"] == "QUEUE_PENDING" and w["last_result"]["date"] == "2026-03-20"
    assert w["last_result"]["sessionLinked"] is False
    assert w["unlinked_events"] == 1
    assert w["stage"] == "cancer" and w["next_step"].startswith("암질심")


def test_same_date_tie_breaks():
    rows = [
        {"id": 1, "queue_state": "APPROVED", "session_date": "2026-01-21", "queue_entry_date": None,
         "observed_at": "2026-06-11 00:00:00", "committee_type": "AMJILSIM"},
        {"id": 2, "queue_state": "QUEUE_PENDING", "session_date": None, "queue_entry_date": "2026-01-21",
         "observed_at": "2026-06-12 00:00:00", "committee_type": "AMJILSIM"},
    ]
    assert rp.latest_queue_row(rows)["id"] == 2, "같은 사건일이면 나중에 관측된 기록"
    rows[1]["observed_at"] = rows[0]["observed_at"]
    assert rp.latest_queue_row(rows)["id"] == 1, "관측 시각도 같으면 결과(통과) > 대기"
    assert rp.latest_queue_row([]) is None


def test_jakavi_agreed_without_yakpyungwi_date_is_completed_not_evaluation(db):
    j = _admin()["자카비"]
    assert j["stage"] == "nhis" and j["status"] == "completed" and j["next_step"] == "협상 완료"
    # 협상 완료연월이 없으니 마지막 결과는 날짜가 있는 암질심 통과
    assert j["last_result"]["committeeLabel"] == "암질심" and j["last_result"]["label"] == "통과"
    assert rp.get_pipeline()["completedExcluded"] >= 1


def test_keytruda_last_result_is_nhis_agreement(db):
    k = _admin()["키트루다"]
    assert k["status"] == "completed" and k["next_step"] == "협상 완료"
    lr = k["last_result"]
    assert lr["kind"] == "negotiation" and lr["label"] == "협상 타결" and lr["month"] == "2026-09"
    assert lr["source"] == "nhis_official"


# ── 2. 쟁점·근거 문구 / 연도 포함 차수 ──────────────────────────────────────────

def test_key_issues_humanized_and_history_has_year(db):
    w = _board()["웰리렉"]
    first = w["keyIssues"][0]
    assert "brdBltNo" not in first
    assert first.startswith("2025년 9차 암질심(2025-12-10)")
    assert "급여기준 미설정" in first
    assert "brdBltNo=11684" in w["keyIssuesRaw"][0]
    ev = next(h for h in w["history"] if h["id"] == 75)
    assert ev["cycleLabel"] == "2025년 9차" and ev["year"] == 2025
    assert ev["evidenceUrl"] is None and "#11684" in ev["evidenceText"] and "brdBltNo" not in ev["evidenceText"]
    j = _board()["자카비"]
    ev = next(h for h in j["history"] if h["id"] == 40)
    assert ev["evidenceUrl"].startswith("https://") and ev["evidenceText"] is None


def test_humanize_issue_idempotent():
    s = rp.humanize_issue("9차 암질심(2025-12-10 brdBltNo=11684) 미설정 — X")
    assert rp.humanize_issue(s) == s
    assert rp.humanize_issue(None) is None


# ── 3. 카드 신선도 ─────────────────────────────────────────────────────────────

def test_stale_flag(db):
    b = _board()
    w = b["웰리렉"]
    assert w["updatedDate"].startswith("2026-06-12") and w["staleDays"] == 127 and w["isStale"] is True
    k = b["키트루다"]
    assert k["updatedDate"].startswith("2026-10-11"), "공단 자료 상태 변화(완료 행 first_seen)도 갱신으로 본다"
    assert k["isStale"] is False and k["nhisCheckedAt"].startswith("2026-10-11")


# ── 4. NHIS 협상 중 아카이브 행 대체 ────────────────────────────────────────────

def test_superseded_in_progress_rows_hidden(db):
    with rp._connect() as conn:
        sup = rp.superseded_nhis_ids(conn)
    assert sup == {42, 2, 55}
    res = rp.list_nhis_negotiations(status="in_progress")
    ids = {i["id"] for i in res["items"]}
    assert ids == {120, 130}, "최신 수집에서도 협상 중인 행·대체 근거 없는 행은 유지"
    assert res["counts"]["in_progress"] == 2 and res["counts"]["superseded"] == 3
    done = rp.list_nhis_negotiations(status="completed")
    assert {i["id"] for i in done["items"]} >= {114, 92}


def test_combo_search_finds_padcev_by_keytruda(db):
    res = rp.list_nhis_negotiations(status="completed", q="키트루다")
    names = {i["productName"] for i in res["items"]}
    assert any(n.startswith("파드셉") for n in names), "병용 약제 메모로 파드셉+키트루다 등재가 검색되어야 함"
    pad = next(i for i in res["items"] if i["id"] == 92)
    assert pad["drugIndication"] == "요로상피암 1차 (펨브로 병용)"


# ── 5. 보고서 작성일 vs 회의일 ─────────────────────────────────────────────────

def test_report_dates_split_meeting_vs_report():
    sessions = [{"session_id": s[0], "committee_type": s[6], "year": s[1],
                 "session_date": s[4], "cycle": s[3] if s[3] is not None else s[2]} for s in SESSIONS]
    pre = rr.report_dates({"file_name": "2026-09-29_amjilsim-8_d_minus_1.pdf", "committee": "cancer",
                           "committee_scope": "cancer", "year": 2026, "cycle": 8,
                           "session_date": "2026-09-29"}, sessions)
    assert pre == {"report_date": "2026-09-29", "meeting_date": "2026-09-30",
                   "meeting_session_id": 7, "report_offset_days": -1}
    post = rr.report_dates({"file_name": "2026-10-02_yakpyungwi-10_d_plus_1.pdf", "committee": "evaluation",
                            "committee_scope": "evaluation", "year": 2026, "cycle": 10,
                            "session_date": "2026-10-02"}, sessions)
    assert post["meeting_date"] == "2026-10-01" and post["report_offset_days"] == 1
    # inbox 보고서: session_date 가 이미 회의일 — 차수로도 같은 회의일
    inbox = rr.report_dates({"file_name": "2026-06-02_yakpyungwi-6_d_minus_2_v6.pdf", "committee": "evaluation",
                             "year": 2026, "cycle": 6, "session_date": "2026-06-04"}, sessions)
    assert inbox["meeting_date"] == "2026-06-04" and inbox["report_offset_days"] == -2
    # 회차를 못 찾으면 회의일을 만들지 않는다
    none = rr.report_dates({"file_name": "monthly.pdf", "committee": None, "year": None, "cycle": None,
                            "session_date": None}, sessions)
    assert none["meeting_date"] is None and none["report_date"] is None
