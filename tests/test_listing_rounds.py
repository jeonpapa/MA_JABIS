"""급여 파이프라인 — 등재 차수(listing round) 분할 (2026-10-17 사용자 결정).

재현 (프로덕션 2026-10-17, admin API 실측 형태):
- 키트루다(drug_id=2) 1행 = '확대 · 11개 적응증 · 약평위 2025-09-04' 인데 협상 필드는 2026-05 확대(→2026-09 합의)로 덮였다.
  2026 확대(파드셉 병용 요로상피암 1차: 암질심 2025-10-29 → 약평위 2026-04-02 → 공단 2026-05 → 합의 2026-09 →
  급여 2026-10-01)의 위원회 이력은 파드셉(drug_id=9) 행에만 있다.
- 다잘렉스 PV(drug_id=18) 는 공단 묶음 2개(확대 2025-06 '다잘렉스주' / 신규 2026-03 '다잘렉스피하주사') — 제품이 달라 모호.
- nhis_negotiation_import 동일 등록연월 tie-break 가 '협상 중' 을 완료 행보다 우선했다.

네트워크·실제 DB 금지: 임시 sqlite 파일만 쓴다 (conftest 가드).
"""
from __future__ import annotations

import json
import sqlite3
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import agents.reimb_pipeline as rp  # noqa: E402

try:  # 스크레이퍼 의존성(requests 등)이 없는 환경 — 적재 로직만 검증
    import agents.ingest.nhis_negotiation_import as nimp  # noqa: E402
except ImportError:  # pragma: no cover
    sys.modules["agents.scrapers.nhis_negotiation"] = types.ModuleType("agents.scrapers.nhis_negotiation")
    import agents.ingest.nhis_negotiation_import as nimp  # noqa: E402

import split_listing_rounds as slr  # noqa: E402

_SCHEMA = """
CREATE TABLE amjilsim_sessions (
    session_id INTEGER PRIMARY KEY, year INTEGER NOT NULL, ordinal_assumed INTEGER NOT NULL DEFAULT 0,
    ordinal_official INTEGER, session_date DATE NOT NULL UNIQUE, status TEXT NOT NULL DEFAULT 'SCHEDULED',
    official_minutes_url TEXT, note TEXT, created_at TEXT, committee_type TEXT NOT NULL DEFAULT 'AMJILSIM'
);
CREATE TABLE amjilsim_drugs (
    drug_id INTEGER PRIMARY KEY AUTOINCREMENT, product_slug TEXT, brand_kr TEXT NOT NULL, brand_en TEXT,
    ingredient_inn TEXT, atc TEXT, manufacturer TEXT, msd_flag INTEGER NOT NULL DEFAULT 0, competitor_class TEXT,
    first_seen_at TEXT, tracking_priority TEXT NOT NULL DEFAULT 'generic_new_drug', amjilsim_pass_date DATE,
    yakpyungwi_pass_date DATE, negotiation_status TEXT, indication TEXT, listing_type TEXT, submitted_date DATE,
    notes TEXT, expected_session_id INTEGER, key_issues TEXT, negotiation_complete_date TEXT,
    negotiation_date_source TEXT, nhis_registered_ym TEXT, efficacy_group TEXT, is_oncology INTEGER,
    UNIQUE(brand_kr, ingredient_inn)
);
CREATE TABLE amjilsim_drug_queue_status (
    id INTEGER PRIMARY KEY AUTOINCREMENT, drug_id INTEGER NOT NULL, session_id INTEGER,
    queue_state TEXT NOT NULL CHECK (queue_state IN ('QUEUE_PENDING','QUEUE_PROCESSED','APPROVED',
                                                     'REJECTED_REQUEUE','WITHDRAWN')),
    queue_entry_date DATE, n_th_attempt INTEGER NOT NULL DEFAULT 1, evidence_url TEXT,
    observed_at TEXT DEFAULT (datetime('now')),
    committee_type TEXT NOT NULL DEFAULT 'AMJILSIM' CHECK (committee_type IN ('AMJILSIM','YAKPYUNGWI'))
);
CREATE TABLE nhis_negotiations (
    id INTEGER PRIMARY KEY AUTOINCREMENT, list_type TEXT NOT NULL CHECK (list_type IN ('신규','확대')),
    product_name TEXT NOT NULL, manufacturer TEXT, efficacy_group TEXT, registered_ym TEXT, result TEXT,
    completed_ym TEXT, source_url TEXT NOT NULL, content_hash TEXT NOT NULL, drug_id INTEGER,
    first_seen_at TEXT DEFAULT (datetime('now')), fetched_at TEXT DEFAULT (datetime('now')), UNIQUE(content_hash)
);
"""

SESSIONS = [  # (id, year, ord_assumed, ord_official, date, status, committee)
    (23, 2025, 9, 9, "2025-09-04", "COMPLETED", "YAKPYUNGWI"),
    (31, 2025, 8, 8, "2025-10-29", "COMPLETED", "AMJILSIM"),
    (32, 2025, 9, 9, "2025-12-10", "COMPLETED", "AMJILSIM"),
    (40, 2026, 1, 1, "2026-01-15", "COMPLETED", "YAKPYUNGWI"),
    (13, 2026, 4, None, "2026-04-02", "COMPLETED", "YAKPYUNGWI"),
    (8, 2027, 1, None, "2027-01-20", "SCHEDULED", "AMJILSIM"),
    (20, 2027, 2, None, "2027-02-04", "SCHEDULED", "YAKPYUNGWI"),
]

# 프로덕션 admin API 형태 그대로 (2026-10-17)
DRUGS = [  # (drug_id, brand_kr, inn, mfr, msd, listing, indication, amj, yak, neg, neg_done, src, reg, notes)
    (1, "웰리렉", "belzutifan", "한국MSD", 1, None, None, None, None, None, None, None, None, None),
    (2, "키트루다", "pembrolizumab", "한국MSD", 1, "확대", "11개 적응증", None, "2025-09-04", "AGREED",
     "2026-09", "nhis_official", "2026-05",
     "9차 약평위 통과 — 키트루다 11개 적응증 (매체: 메디포뉴스 206439, HIRA 본문 미verified). 2026-01-01 급여 시행. MSD asset."),
    (9, "파드셉주 20·30mg", "enfortumab vedotin", "한국아스텔라스", 0, "신규", "요로상피암 1차 (펨브로 병용)", None,
     "2026-04-02", "AGREED", "2026-09", "nhis_official", "2026-05",
     "4차 약평위 통과 (조건: 즉시 적정). 펨브로(키트루다) 병용 적응증. HIRA 공식 verified."),
    (18, "다잘렉스 PV", "daratumumab", "얀센", 0, "신규", "AL 아밀로이드증", None, "2026-01-15", "AGREED",
     "2026-04", "nhis_official", "2026-03", "1차 약평위 통과 (매체 보도, HIRA 본문 미verified)."),
]

QUEUE = [  # (id, drug_id, session_id, state, entry, evidence, observed, committee)
    (1, 1, None, "QUEUE_PENDING", "2026-03-20", None, "2026-05-28 04:18:43", "AMJILSIM"),
    (75, 1, 32, "REJECTED_REQUEUE", "2025-12-10", "HIRA brdBltNo=11684", "2026-06-12 10:00:00", "AMJILSIM"),
    (38, 2, 23, "APPROVED", "2025-09-04", "매체: 메디포뉴스 206439", "2026-05-28 04:18:43", "YAKPYUNGWI"),
    (9, 9, 13, "APPROVED", "2026-04-02", "HIRA brdBltNo=11763", "2026-05-28 04:18:43", "YAKPYUNGWI"),
    (60, 18, 40, "APPROVED", "2026-01-15", "매체", "2026-05-28 04:18:43", "YAKPYUNGWI"),
]

KT = "키트루다주(펨브롤리주맙,유전자재조합)_(0.1g/4mL)"
PD = "파드셉주 20밀리그램,30밀리그램(엔포투맙베도틴)"
L1 = "https://www.nhis.or.kr/nhis/together/retrieveMediList.do"
L2 = "https://www.nhis.or.kr/nhis/together/retrieveMediList2.do"
NHIS = [  # (id, list_type, product, mfr, reg, result, completed, source, drug_id, fetched)
    (41, "확대", KT, "한국엠에스디(유)", "2025-10", "-", None, L2, 2, "2025-11-01 17:30:00"),
    (50, "확대", KT.replace(",", ", "), "한국엠에스디㈜", "2025-10", "합의", "2025-12", L2, 2, "2026-10-11 17:30:07"),
    (42, "확대", KT, "한국엠에스디(유)", "2026-05", "-", None, L2, 2, "2026-07-26 17:30:04"),
    (114, "확대", KT, "한국엠에스디(유)", "2026-05", "합의", "2026-09", L2, 2, "2026-10-11 17:30:07"),
    (2, "신규", "파드셉주 20밀리그램,30밀리그램_(엔포투맙베도틴)", "한국아스텔라스제약(주)", "2026-05", "-", None, L1, 9,
     "2026-06-14 05:40:48"),
    (63, "신규", PD, "한국아스텔라스제약(주)", "2026-05", "-", None, L1, 9, "2026-07-26 17:30:04"),
    (92, "신규", PD, "한국아스텔라스제약(주)", "2026-05", "합의", "2026-09", L1, 9, "2026-10-11 17:30:07"),
    (53, "확대", "다잘렉스주(다라투무맙)_(0.1g/5mL),(0.4g/20mL)", "(주)한국얀센", "2025-06", "합의", "2025-08", L2, 18,
     "2026-06-28 17:30:07"),
    (106, "신규", "다잘렉스피하주사(1.8g/15mL)(다라투무맙)", "(주)한국얀센", "2026-03", "합의", "2026-04", L1, 18,
     "2026-10-11 17:30:07"),
]


def _build(path: Path, *, drop_nhis: tuple[int, ...] = ()) -> Path:
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    conn.executemany("INSERT INTO amjilsim_sessions (session_id, year, ordinal_assumed, ordinal_official, session_date,"
                     " status, committee_type) VALUES (?,?,?,?,?,?,?)", SESSIONS)
    for d in DRUGS:
        conn.execute("INSERT INTO amjilsim_drugs (drug_id, brand_kr, ingredient_inn, manufacturer, msd_flag, listing_type,"
                     " indication, amjilsim_pass_date, yakpyungwi_pass_date, negotiation_status,"
                     " negotiation_complete_date, negotiation_date_source, nhis_registered_ym, notes, is_oncology)"
                     " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)", d)
    conn.executemany("INSERT INTO amjilsim_drug_queue_status (id, drug_id, session_id, queue_state, queue_entry_date,"
                     " evidence_url, observed_at, committee_type) VALUES (?,?,?,?,?,?,?,?)", QUEUE)
    for n in NHIS:
        if n[0] in drop_nhis:
            continue
        conn.execute("INSERT INTO nhis_negotiations (id, list_type, product_name, manufacturer, registered_ym, result,"
                     " completed_ym, source_url, content_hash, drug_id, first_seen_at, fetched_at)"
                     " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     (n[0], n[1], n[2], n[3], n[4], n[5], n[6], n[7], f"h{n[0]}", n[8], n[9], n[9]))
    conn.commit()
    conn.close()
    return path


@pytest.fixture()
def db(tmp_path, monkeypatch) -> Path:
    path = _build(tmp_path / "rounds.db")
    monkeypatch.setattr(rp, "DB_PATH", path)
    return path


def _conn(path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(path))
    c.row_factory = sqlite3.Row
    return c


def _rounds(path: Path, drug_id: int) -> dict[str, sqlite3.Row]:
    with _conn(path) as c:
        return {r["round_label"]: r for r in rp.list_rounds(c, drug_id)}


def _apply(path: Path, tmp_path: Path) -> dict:
    return slr.run(path, apply=True, backup_dir=tmp_path / "bk")


# ── tie-break ────────────────────────────────────────────────────────────────

def test_same_month_tie_prefers_finished_row():
    in_prog = {"registered_ym": "2026-05", "is_in_progress": True, "list_type": "확대", "completed_ym": None}
    done = {"registered_ym": "2026-05", "is_in_progress": False, "list_type": "확대", "completed_ym": "2026-09"}
    assert nimp._better_current_row(done, in_prog) is True
    assert nimp._better_current_row(in_prog, done) is False
    # 같은 달 다른 목록(동시 진행 별개 협상)은 진행 중 우선 — 보드 노출
    other = dict(in_prog, list_type="신규")
    assert nimp._better_current_row(other, done) is True
    # 더 최근 등록연월은 상태와 무관하게 우선
    newer = {"registered_ym": "2026-07", "is_in_progress": True, "list_type": "확대", "completed_ym": None}
    assert nimp._better_current_row(newer, done) is True


def test_best_nhis_row_prefers_completed_in_same_month():
    rows = [{"id": 42, "registered_ym": "2026-05", "completed_ym": None},
            {"id": 114, "registered_ym": "2026-05", "completed_ym": "2026-09"},
            {"id": 50, "registered_ym": "2025-10", "completed_ym": "2025-12"}]
    assert rp.best_nhis_row(rows)["id"] == 114


def test_match_and_apply_same_fetch_keeps_agreed(tmp_path):
    path = _build(tmp_path / "m.db")
    conn = _conn(path)
    nimp._ensure_schema(conn)
    rows = [  # 같은 수집에 같은 협상의 협상중·완료 행이 함께 → 완료(AGREED)
        {"list_type": "확대", "product_name": KT, "manufacturer": "한국엠에스디(유)", "efficacy_group": "항악성종양제",
         "registered_ym": "2026-05", "result": "-", "completed_ym": None, "source_url": L2, "content_hash": "h42",
         "is_in_progress": True},
        {"list_type": "확대", "product_name": KT, "manufacturer": "한국엠에스디(유)", "efficacy_group": "항악성종양제",
         "registered_ym": "2026-05", "result": "합의", "completed_ym": "2026-09", "source_url": L2,
         "content_hash": "h114", "is_in_progress": False},
    ]
    nimp.match_and_apply(conn, rows)
    d = conn.execute("SELECT negotiation_status, negotiation_complete_date FROM amjilsim_drugs WHERE drug_id = 2").fetchone()
    assert (d["negotiation_status"], d["negotiation_complete_date"]) == ("AGREED", "2026-09")
    conn.close()


# ── 마이그레이션 ─────────────────────────────────────────────────────────────

def test_dry_run_is_read_only_and_reports_plan(db):
    before = db.read_bytes()
    rep = slr.run(db, apply=False)
    assert db.read_bytes() == before  # 아무것도 쓰지 않음 (스키마도)
    assert rep["schema"] == {"rounds": False, "nhis_round_col": False}
    labels = [r["round_label"] for r in rep["keytruda"]["rounds"]]
    assert labels == ["2025 확대 · 11개 적응증", "2026 확대 · 파드셉 병용 요로상피암 1차"]
    r25, r26 = rep["keytruda"]["rounds"]
    assert sorted(r25["nhis_ids"]) == [41, 50] and r25["own_event_ids"] == [38]
    assert sorted(r26["nhis_ids"]) == [42, 114] and r26["shared_event_ids"] == [9]
    assert rep["keytruda"]["new_event"]["session_id"] == 31  # 파드셉 2025-10-29 암질심 이벤트 신규 예정
    dz = [g for g in rep["generic"] if g["drug_id"] == 18]
    assert dz and dz[0]["ambiguous"] and any("제품명" in w for w in dz[0]["reasons"])
    assert rep["summary"]["generic_ambiguous"] == 1 and rep["summary"]["generic_applicable"] == 0
    slr.print_report(rep)  # 출력 경로 smoke


def test_apply_splits_keytruda_without_stealing_from_padcev(db, tmp_path):
    rep = _apply(db, tmp_path)
    assert Path(rep["backup"]["path"]).exists()
    backup = json.loads(Path(rep["backup"]["path"]).read_text(encoding="utf-8"))
    assert {d["drug_id"] for d in backup["amjilsim_drugs"]} == {2, 9}
    k = _rounds(db, 2)
    r25, r26 = k["2025 확대 · 11개 적응증"], k["2026 확대 · 파드셉 병용 요로상피암 1차"]
    assert (r25["yakpyungwi_pass_date"], r25["negotiation_status"], r25["nhis_registered_ym"],
            r25["negotiation_complete_date"]) == ("2025-09-04", "AGREED", "2025-10", "2025-12")
    assert (r26["amjilsim_pass_date"], r26["yakpyungwi_pass_date"], r26["nhis_registered_ym"],
            r26["negotiation_complete_date"], r26["negotiation_status"], r26["reimb_start_date"]) == \
        ("2025-10-29", "2026-04-02", "2026-05", "2026-09", "AGREED", "2026-10-01")
    p = list(_rounds(db, 9).values())
    assert len(p) == 1 and p[0]["combo_key"] == r26["combo_key"] == slr.KEYTRUDA_SPEC["combo_key"]
    with _conn(db) as c:
        nh = {r["id"]: r["round_id"] for r in c.execute("SELECT id, round_id FROM nhis_negotiations")}
        assert nh[41] == nh[50] == r25["round_id"] and nh[42] == nh[114] == r26["round_id"]
        assert nh[2] == nh[63] == nh[92] == p[0]["round_id"]
        assert nh[53] is None and nh[106] is None  # 모호 브랜드는 미적용
        links = {(r["round_id"], r["queue_id"]) for r in c.execute("SELECT * FROM reimb_round_events")}
        new_amj = c.execute("SELECT id, drug_id FROM amjilsim_drug_queue_status WHERE session_id = 31").fetchone()
        assert new_amj["drug_id"] == 9  # 파드셉(신청사) 이벤트로 추가
        # 파드셉 이벤트는 파드셉 차수에 그대로 + 키트루다 2026 차수에 공유 링크 (빼앗지 않음)
        for qid in (9, new_amj["id"]):
            assert (p[0]["round_id"], qid) in links and (r26["round_id"], qid) in links
        assert (r25["round_id"], 38) in links
        # 한 공단 행·이벤트가 같은 브랜드 두 차수에 붙지 않음
        dup = c.execute("SELECT queue_id, round_drug_id, COUNT(*) n FROM reimb_round_events "
                        "GROUP BY queue_id, round_drug_id HAVING n > 1").fetchall()
        assert dup == []
        n_rounds = c.execute("SELECT COUNT(*) FROM reimb_listing_rounds").fetchone()[0]
        n_links = c.execute("SELECT COUNT(*) FROM reimb_round_events").fetchone()[0]
        n_events = c.execute("SELECT COUNT(*) FROM amjilsim_drug_queue_status").fetchone()[0]
    # 재실행 멱등
    _apply(db, tmp_path)
    with _conn(db) as c:
        assert c.execute("SELECT COUNT(*) FROM reimb_listing_rounds").fetchone()[0] == n_rounds
        assert c.execute("SELECT COUNT(*) FROM reimb_round_events").fetchone()[0] == n_links
        assert c.execute("SELECT COUNT(*) FROM amjilsim_drug_queue_status").fetchone()[0] == n_events


def test_apply_rolls_back_on_error(db, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(slr, "apply_keytruda", boom)
    with pytest.raises(RuntimeError):
        _apply(db, tmp_path)
    with _conn(db) as c:
        assert not rp.rounds_enabled(c)  # DDL 까지 한 트랜잭션으로 롤백
        assert c.execute("SELECT COUNT(*) FROM amjilsim_drug_queue_status").fetchone()[0] == len(QUEUE)


# ── 보드·admin ──────────────────────────────────────────────────────────────

def test_board_shows_one_card_per_open_round(tmp_path, monkeypatch):
    path = _build(tmp_path / "b.db", drop_nhis=(114, 92))  # 2026 협상이 아직 진행 중인 시점
    monkeypatch.setattr(rp, "DB_PATH", path)
    _apply(path, tmp_path)
    board = rp.get_pipeline()
    cards = [d for s in board["stages"] for d in s["drugs"] if d["drugId"] == 2]
    assert [c["roundLabel"] for c in cards] == ["2026 확대 · 파드셉 병용 요로상피암 1차"]
    c = cards[0]
    assert (c["status"], c["listType"], c["amjilsimPassDate"], c["yakpyungwiPassDate"], c["nhisRegisteredYm"]) == \
        ("negotiating", "확대", "2025-10-29", "2026-04-02", "2026-05")
    assert c["roundCount"] == 2 and c["cardKey"] == f"2:{c['roundId']}"
    assert [p["drugId"] for p in c["comboPartners"]] == [9]
    assert {h["committee"] for h in c["history"]} == {"암질심", "약평위"}  # 파드셉 공유 이벤트가 키트루다 차수 이력에
    # 2025 차수(타결)는 완료로 빠지고 카운트
    full = rp.get_pipeline(include_completed=True)
    labels = sorted(d["roundLabel"] for s in full["stages"] for d in s["drugs"] if d["drugId"] == 2)
    assert labels == ["2025 확대 · 11개 적응증", "2026 확대 · 파드셉 병용 요로상피암 1차"]


def test_virtual_round_for_unsplit_brand(db):
    board = rp.get_pipeline(include_completed=True)
    w = [d for s in board["stages"] for d in s["drugs"] if d["drugId"] == 1][0]
    assert w["roundId"] is None and w["roundCount"] == 1 and w["cardKey"] == "1:0"
    k = [d for s in board["stages"] for d in s["drugs"] if d["drugId"] == 2][0]
    assert k["roundLabel"] == "2025 확대 · 11개 적응증"  # 분할 전 — 약제 행 라벨 그대로


def test_admin_groups_rounds_under_brand(db, tmp_path):
    _apply(db, tmp_path)
    items = {d["drug_id"]: d for d in rp.list_drugs_admin()}
    k = items[2]
    assert [r["round_label"] for r in k["rounds"]] == ["2025 확대 · 11개 적응증", "2026 확대 · 파드셉 병용 요로상피암 1차"]
    assert k["round_count"] == 2
    r26 = k["rounds"][1]
    assert [e["drugId"] for e in r26["shared_events"]] == [9, 9]
    assert sorted(r26["nhis_ids"]) == [42, 114]
    assert {e["id"]: e["roundId"] for e in k["events"]}[38] == k["rounds"][0]["round_id"]
    assert len(items[1]["rounds"]) == 1 and items[1]["rounds"][0]["round_id"] is None
    nh = rp.list_nhis_negotiations(status="all", q="파드셉")["items"]
    assert any(n["drugId"] == 2 and n["roundLabel"] == "2026 확대 · 파드셉 병용 요로상피암 1차" for n in nh)


def test_admin_create_round_materializes_default_and_links(db):
    res = rp.create_round(1, {"round_label": "2027 신규 · VHL병 신세포암", "list_type": "신규",
                              "indication_scope": "VHL병 신세포암", "event_ids": [1]})
    assert res["default_round_id"]
    r = _rounds(db, 1)
    assert len(r) == 2 and r["2027 신규 · VHL병 신세포암"]["source"] == "admin"
    with _conn(db) as c:
        links = {q["queue_id"]: q["round_id"] for q in c.execute("SELECT * FROM reimb_round_events")}
    assert links[1] == res["round_id"]  # 명시 연결
    assert links[75] == res["default_round_id"]  # 나머지는 기본 차수에 그대로 (빼앗지 않음)
    with pytest.raises(ValueError):
        rp.create_round(1, {"round_label": "2027 신규 · VHL병 신세포암"})
    # 다른 브랜드 공단 행은 연결 거부
    with pytest.raises(ValueError):
        rp.set_nhis_round_admin(50, {"round_id": res["round_id"]})


# ── 적재 내구성 ─────────────────────────────────────────────────────────────

def _insert_nhis(path: Path, nid: int, lt: str, reg: str, result: str, done, drug_id: int, product: str = KT):
    with _conn(path) as c:
        c.execute("INSERT INTO nhis_negotiations (id, list_type, product_name, manufacturer, registered_ym, result,"
                  " completed_ym, source_url, content_hash, drug_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (nid, lt, product, "한국엠에스디(유)", reg, result, done, L2, f"h{nid}", drug_id))


def test_future_capture_attaches_by_sibling_combo_and_window(db, tmp_path):
    _apply(db, tmp_path)
    r = _rounds(db, 2)
    r25, r26 = r["2025 확대 · 11개 적응증"]["round_id"], r["2026 확대 · 파드셉 병용 요로상피암 1차"]["round_id"]
    # ① 같은 협상의 새 표기 행(형제) → 같은 차수
    _insert_nhis(db, 300, "확대", "2026-05", "합의", "2026-09", 2, product=KT.replace(",", ", "))
    # ② 병용 파트너(파드셉 2026-05)와 ±1개월 → 병용 차수 (날짜 창으론 갈 곳 없음 — 두 차수 모두 다른 협상 보유)
    _insert_nhis(db, 301, "확대", "2026-06", "-", None, 2)
    # ③ 새 적응증: 2027 약평위 통과 차수가 있으면 날짜 창으로 그 차수
    rid27 = rp.create_round(2, {"round_label": "2027 확대 · 신규 적응증 A", "list_type": "확대",
                                "yakpyungwi_pass_date": "2027-02-04"})["round_id"]
    _insert_nhis(db, 302, "확대", "2027-03", "-", None, 2)
    # ④ 갈 곳 없는 새 협상 → 자동 차수(needs_review)
    _insert_nhis(db, 303, "확대", "2028-06", "-", None, 2)
    with _conn(db) as c:
        res = rp.sync_drug_rounds(c, 2)
        c.commit()
        got = {n["id"]: n["round_id"] for n in c.execute("SELECT id, round_id FROM nhis_negotiations WHERE drug_id = 2")}
        rules = {x["nhis_id"]: x["rule"] for x in res["nhis"]}
        auto = c.execute("SELECT * FROM reimb_listing_rounds WHERE round_id = ?", (got[303],)).fetchone()
        r27 = c.execute("SELECT * FROM reimb_listing_rounds WHERE round_id = ?", (rid27,)).fetchone()
    assert got[300] == r26 and rules[300] == "sibling"
    assert got[301] == r26 and rules[301] == "combo"
    assert got[302] == rid27 and rules[302] == "window" and r27["negotiation_status"] == "IN_PROGRESS"
    assert rules[303] == "created" and auto["needs_review"] == 1 and auto["round_label"] == "2028 확대 · 공단 2028-06 협상"
    assert got[41] == got[50] == r25  # 기존 링크 불변


def test_window_never_double_assigns_and_ambiguous_tie_stays_unlinked(tmp_path):
    path = _build(tmp_path / "w.db")
    with _conn(path) as c:
        rp.ensure_round_schema(c)
        a = rp.create_round_row(c, 1, round_label="A", yakpyungwi_pass_date="2026-01-15")
        b = rp.create_round_row(c, 1, round_label="B", yakpyungwi_pass_date="2026-01-15")
        c.execute("INSERT INTO nhis_negotiations (id, list_type, product_name, registered_ym, result, source_url,"
                  " content_hash, drug_id) VALUES (500,'신규','웰리렉정40밀리그램','2026-03','-',?, 'h500', 1)", (L1,))
        rnd, why = rp.pick_round_for_nhis(c, rp.list_rounds(c, 1),
                                          c.execute("SELECT * FROM nhis_negotiations WHERE id = 500").fetchone())
        assert rnd is None and why == "ambiguous_window"
        # 이벤트 링크: 같은 브랜드 두 차수 → 교체(1개만), DB 제약도 막는다
        rp.link_event_to_round(c, 1, a)
        rp.link_event_to_round(c, 1, b)
        assert [r["round_id"] for r in c.execute("SELECT round_id FROM reimb_round_events WHERE queue_id = 1")] == [b]
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("INSERT INTO reimb_round_events (round_id, queue_id, round_drug_id) VALUES (?, 1, 1)", (a,))


def test_event_rule_prefers_exact_pass_then_next_anchor():
    r1 = {"round_id": 1, "yakpyungwi_pass_date": "2025-09-04", "amjilsim_pass_date": None, "negotiation_status": "AGREED"}
    r2 = {"round_id": 2, "yakpyungwi_pass_date": "2026-04-02", "amjilsim_pass_date": "2025-10-29",
          "negotiation_status": "AGREED"}
    r3 = {"round_id": 3, "yakpyungwi_pass_date": None, "amjilsim_pass_date": None, "negotiation_status": None}
    rounds = [r1, r2, r3]
    assert rp.pick_round_for_event(rounds, "2025-10-29", "AMJILSIM")["round_id"] == 2  # 통과일 일치
    assert rp.pick_round_for_event(rounds, "2025-06-01", "YAKPYUNGWI")["round_id"] == 1  # 다음 앵커
    assert rp.pick_round_for_event(rounds, "2026-03-20", "AMJILSIM")["round_id"] == 2
    assert rp.pick_round_for_event(rounds, "2026-11-01", "AMJILSIM")["round_id"] == 3  # 진행 중 차수
    assert rp.pick_round_for_event(rounds[:2], "2026-11-01", "AMJILSIM") is None  # 모두 종료 → 새 차수 후보


def test_generic_split_unambiguous_brand(tmp_path):
    path = _build(tmp_path / "g.db")
    with _conn(path) as c:
        c.execute("INSERT INTO amjilsim_drugs (drug_id, brand_kr, ingredient_inn, manufacturer, listing_type, indication,"
                  " yakpyungwi_pass_date, negotiation_status) VALUES (70,'테스트맙','testmab','A제약','확대','B 적응증',"
                  "'2025-07-03','IN_PROGRESS')")
        c.execute("INSERT INTO amjilsim_sessions (session_id, year, ordinal_assumed, session_date, status, committee_type)"
                  " VALUES (90, 2024, 1, '2024-01-10', 'COMPLETED', 'YAKPYUNGWI'),"
                  " (91, 2025, 7, '2025-07-03', 'COMPLETED', 'YAKPYUNGWI')")
        c.execute("INSERT INTO amjilsim_drug_queue_status (id, drug_id, session_id, queue_state, queue_entry_date,"
                  " committee_type) VALUES (700, 70, 90, 'APPROVED', '2024-01-10', 'YAKPYUNGWI'),"
                  " (701, 70, 91, 'APPROVED', '2025-07-03', 'YAKPYUNGWI')")
        for nid, lt, reg, res, done in ((710, "신규", "2024-03", "합의", "2024-05"), (711, "확대", "2025-09", "-", None)):
            c.execute("INSERT INTO nhis_negotiations (id, list_type, product_name, registered_ym, result, completed_ym,"
                      " source_url, content_hash, drug_id) VALUES (?,?,?,?,?,?,?,?,70)",
                      (nid, lt, "테스트맙주(testmab)", reg, res, done, L1, f"h{nid}"))
        c.commit()
    rep = slr.run(path, apply=False)
    g = [x for x in rep["generic"] if x["drug_id"] == 70][0]
    assert not g["ambiguous"], g["reasons"]
    assert [r["round_label"] for r in g["rounds"]] == ["2024 신규 · 공단 2024-03 협상", "2025 확대 · B 적응증"]
    assert g["rounds"][0]["needs_review"] == 1 and g["rounds"][1]["event_ids"] == [701]
    slr.run(path, apply=True, backup_dir=tmp_path / "bk")
    r = _rounds(path, 70)
    assert r["2024 신규 · 공단 2024-03 협상"]["negotiation_status"] == "AGREED"
    assert r["2025 확대 · B 적응증"]["negotiation_status"] == "IN_PROGRESS"


def test_committee_import_attaches_new_event_and_opens_new_round(db, tmp_path):
    import agents.ingest.reimb_committee_import as rci
    _apply(db, tmp_path)
    with _conn(db) as c:
        # 2025 차수 이전 날짜의 재심의 이력 → 2025 차수, 모든 차수 종료 뒤 새 암질심 상정 → 새 차수(needs_review)
        c.execute("INSERT INTO amjilsim_drug_queue_status (id, drug_id, session_id, queue_state, queue_entry_date,"
                  " committee_type) VALUES (800, 2, NULL, 'REJECTED_REQUEUE', '2025-03-05', 'YAKPYUNGWI'),"
                  " (801, 2, 8, 'QUEUE_PENDING', '2027-01-20', 'AMJILSIM')")
        n = rci._sync_event_rounds(c, {2})
        c.commit()
        links = {r["queue_id"]: r["round_id"] for r in c.execute(
            "SELECT * FROM reimb_round_events WHERE round_drug_id = 2")}
        new = c.execute("SELECT * FROM reimb_listing_rounds WHERE round_id = ?", (links[801],)).fetchone()
    r = _rounds(db, 2)
    assert n == 2
    assert links[800] == r["2025 확대 · 11개 적응증"]["round_id"]
    assert new["source"] == "auto_committee" and new["needs_review"] == 1
    assert new["round_label"] == "2027 확대 · 11개 적응증"
    board = rp.get_pipeline()
    cards = [d for s in board["stages"] for d in s["drugs"] if d["drugId"] == 2]
    assert [c["roundLabel"] for c in cards] == ["2027 확대 · 11개 적응증"] and cards[0]["roundNeedsReview"] is True


def test_round_payload_in_committee_json_links_events(db):
    import agents.ingest.reimb_committee_import as rci
    with _conn(db) as c:
        session_map = {(r["committee_type"], r["session_date"]): r["session_id"]
                       for r in c.execute("SELECT * FROM amjilsim_sessions")}
        d = {"brand_kr": "파드셉주 20·30mg", "events": [{"committee": "YAKPYUNGWI", "session_date": "2026-04-02"}],
             "round": {"round_label": "2026 신규 · 요로상피암 1차 (펨브로 병용)", "list_type": "신규",
                       "combo_key": "combo:keytruda+padcev:UC-1L:2026"}}
        rid = rci._apply_round_spec(c, 9, d, session_map)
        c.commit()
        assert [r["queue_id"] for r in c.execute("SELECT queue_id FROM reimb_round_events WHERE round_id = ?", (rid,))] == [9]


def test_default_round_keeps_manual_negotiation_but_not_nhis_copy(db):
    with _conn(db) as c:
        rp.ensure_round_schema(c)
        c.execute("UPDATE amjilsim_drugs SET negotiation_status = 'STALLED', negotiation_date_source = 'manual' "
                  "WHERE drug_id = 1")
        rid = rp.materialize_default_round(c, 1)
        r = c.execute("SELECT * FROM reimb_listing_rounds WHERE round_id = ?", (rid,)).fetchone()
        assert (r["negotiation_status"], r["negotiation_date_source"]) == ("STALLED", "manual")
        rid2 = rp.materialize_default_round(c, 18)  # 공단 출처 협상값은 복사하지 않음 — 연결된 공단 행에서만
        r2 = c.execute("SELECT * FROM reimb_listing_rounds WHERE round_id = ?", (rid2,)).fetchone()
        assert r2["negotiation_status"] is None and r2["round_label"] == "2026 신규 · AL 아밀로이드증"
