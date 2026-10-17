"""2026-10-17 일회성 정정이 이후 잡·재빌드·스크레이프에서 되돌아오지 않는지 — 쓰기 경로 가드 + QG/신선도 불변식.

agents/rules/data_contracts.md 의 보호 행:
  §1 indication_reimbursement (사람 입력 정본) — 병합 시 옮겨지고, MFDS 허가 적응증 누락·고아는 신선도 과제
  §2 indications_by_agency raw_source='manual_correction' — apply_mfds_official_dates · wipe 재빌드 · 병합이 보존
  §3 해외약가 강도/route — QG 데이터 계약(route_as_oral, 연속 조각)
  §4 competitor_trend badge — 허용 집합 · 해외 승인 오분류
  §5 onco_regimen 정본 — 빈 파싱 적재 거부 / 키워드 클라우드 캐시도 필터
실제 DB·네트워크 금지 — tmp_path 의 최소 스키마.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

IBA_DDL = """CREATE TABLE indications_by_agency (id INTEGER PRIMARY KEY AUTOINCREMENT, indication_id TEXT NOT NULL,
    agency TEXT NOT NULL, biomarker_label TEXT, combination_label TEXT, approval_date TEXT, label_excerpt TEXT,
    label_full_text TEXT, label_url TEXT, restriction_note TEXT, raw_source TEXT, fetched_at TEXT, date_source TEXT,
    UNIQUE(indication_id, agency));"""
MASTER_DDL = "CREATE TABLE indications_master (indication_id TEXT PRIMARY KEY, product TEXT, disease TEXT);"
REIMB_DDL = ("CREATE TABLE indication_reimbursement (indication_id TEXT PRIMARY KEY, is_reimbursed INTEGER, "
             "effective_date TEXT, criteria_text TEXT, updated_by TEXT, updated_at TEXT);")


def _db(tmp_path: Path, *stmts: str) -> Path:
    p = tmp_path / "d.db"
    conn = sqlite3.connect(p)
    for s in stmts:
        conn.executescript(s)
    conn.commit()
    conn.close()
    return p


def _q(p: Path, sql: str, args=()):
    with sqlite3.connect(p) as c:
        return c.execute(sql, args).fetchall()


# ── §2 apply_mfds_official_dates 는 manual_correction 행을 건너뛴다 ───────────────────────────

def test_apply_mfds_official_dates_skips_manual_correction(tmp_path):
    from scripts import apply_mfds_official_dates as amod
    p = _db(tmp_path, IBA_DDL,
            "INSERT INTO indications_by_agency (indication_id, agency, approval_date, raw_source, fetched_at) VALUES "
            "('k_ev','MFDS','2024-07-25','manual_correction','t'), ('k_mono','MFDS','2017-01-01',NULL,'t');")
    res = [SimpleNamespace(indication_id=i, official_date="2018-01-24", confidence="high", matched_excerpt="x")
           for i in ("k_ev", "k_mono")] + [SimpleNamespace(indication_id="k_ev", official_date=None,
                                                           confidence="unmatched", matched_excerpt=None)]
    with sqlite3.connect(p) as conn:
        stats = amod._apply_product(conn, "123", res, apply=True)
        conn.commit()
    rows = dict((r[0], r[1:]) for r in _q(p, "SELECT indication_id, approval_date, raw_source, date_source "
                                              "FROM indications_by_agency"))
    assert rows["k_ev"] == ("2024-07-25", "manual_correction", None)      # 날짜·출처·date_source 모두 그대로
    assert rows["k_mono"][0] == "2018-01-24" and rows["k_mono"][2] == "mfds_official"
    assert stats["protected"] == 1 and stats["updated"] == 1


# ── §2 wipe 재빌드는 manual_correction 을 복원한다 ──────────────────────────────────────────

def test_wipe_rebuild_restores_manual_corrections(tmp_path):
    from agents.foreign_approval.agent import ForeignApprovalAgent
    p = _db(tmp_path, MASTER_DDL, IBA_DDL,
            "INSERT INTO indications_master VALUES ('k_ev','keytruda','uc'), ('w1','welireg','rcc');"
            "INSERT INTO indications_by_agency (indication_id, agency, approval_date, restriction_note, raw_source, "
            "fetched_at) VALUES ('k_ev','MFDS','2024-07-25','변경이력 근거','manual_correction','t'),"
            "('k_ev','FDA','2023-12-15',NULL,'{}','t'), ('w1','MFDS','2023-05-01',NULL,'manual_correction','t');")
    agent = ForeignApprovalAgent.__new__(ForeignApprovalAgent)
    agent.db_path = p
    snap = agent._snapshot_manual_corrections("keytruda")
    assert [(r["indication_id"], r["agency"]) for r in snap] == [("k_ev", "MFDS")]   # 다른 제품 행은 대상 아님
    agent._wipe_product("keytruda")
    # 재빌드가 자동 매칭으로 제품 최초 허가일을 다시 넣는다 (EV 병용 1차 오매칭 재현)
    with sqlite3.connect(p) as c:
        c.execute("INSERT INTO indications_master VALUES ('k_ev','keytruda','uc')")
        c.execute("INSERT INTO indications_by_agency (indication_id, agency, approval_date, raw_source, fetched_at) "
                  "VALUES ('k_ev','MFDS','2017-03-21','{\"auto\":1}','t2')")
    assert agent._restore_manual_corrections("keytruda", snap) == 1
    assert _q(p, "SELECT approval_date, restriction_note, raw_source FROM indications_by_agency "
                 "WHERE indication_id='k_ev' AND agency='MFDS'") == [("2024-07-25", "변경이력 근거", "manual_correction")]
    assert _q(p, "SELECT approval_date FROM indications_by_agency WHERE indication_id='w1'") == [("2023-05-01",)]


def test_restore_reinserts_when_rebuild_skipped_agency_and_skips_missing_master(tmp_path):
    from agents.foreign_approval.agent import ForeignApprovalAgent
    p = _db(tmp_path, MASTER_DDL, IBA_DDL, "INSERT INTO indications_master VALUES ('k_ev','keytruda','uc');")
    agent = ForeignApprovalAgent.__new__(ForeignApprovalAgent)
    agent.db_path = p
    snap = [{"id": 9, "indication_id": "k_ev", "agency": "MFDS", "approval_date": "2024-07-25", "label_url": None,
             "restriction_note": None, "raw_source": "manual_correction", "fetched_at": "t"},
            {"id": 10, "indication_id": "gone", "agency": "MFDS", "approval_date": "2020-01-01", "label_url": None,
             "restriction_note": None, "raw_source": "manual_correction", "fetched_at": "t"}]
    assert agent._restore_manual_corrections("keytruda", snap) == 1
    assert _q(p, "SELECT indication_id, approval_date FROM indications_by_agency") == [("k_ev", "2024-07-25")]


# ── §1·§2 병합은 체크리스트를 옮기고 manual_correction 을 잃지 않는다 ─────────────────────────

def test_merge_moves_checklist_and_keeps_manual_correction(tmp_path):
    from agents.foreign_approval.merger import _MergerMixin
    p = _db(tmp_path, MASTER_DDL, IBA_DDL, REIMB_DDL,
            "INSERT INTO indications_master VALUES ('src','keytruda','uc'), ('dst','keytruda','uc');"
            "INSERT INTO indications_by_agency (indication_id, agency, approval_date, raw_source, fetched_at) VALUES "
            "('src','MFDS','2024-07-25','manual_correction','t'), ('dst','MFDS','2017-03-21','{}','t'),"
            "('src','EMA','2024-08-01','{}','t');"
            "INSERT INTO indication_reimbursement VALUES ('src',1,'2025-01-01','조건','me','2026-10-17');")
    m = _MergerMixin.__new__(_MergerMixin)
    m.db_path = p
    src = {"indication_id": "src", "agencies": [
        {"agency": "MFDS", "approval_date": "2024-07-25", "raw_source": "manual_correction", "label_url": None,
         "restriction_note": None}, {"agency": "EMA", "approval_date": "2024-08-01", "raw_source": "{}"}]}
    dst = {"indication_id": "dst", "agencies": [{"agency": "MFDS"}]}
    assert m._do_merge(src, dst, False, [], "test") == 1
    assert _q(p, "SELECT agency, approval_date, raw_source FROM indications_by_agency WHERE indication_id='dst' "
                 "ORDER BY agency") == [("EMA", "2024-08-01", "{}"), ("MFDS", "2024-07-25", "manual_correction")]
    assert _q(p, "SELECT indication_id, criteria_text FROM indication_reimbursement") == [("dst", "조건")]


# ── §1 신선도: MFDS 허가 Keytruda 적응증에 체크리스트 없음 / 고아 → '급여 체크리스트 입력 필요' 과제 ─────

def test_checklist_mfds_gap_flags_missing_and_orphan_rows(tmp_path):
    from agents.agent_tasks import freshness as fr
    p = _db(tmp_path, MASTER_DDL, IBA_DDL, REIMB_DDL,
            "INSERT INTO indications_master VALUES ('k1','keytruda','x'),('k2','keytruda','x'),('k3','keytruda','x'),"
            "('w1','welireg','x');"
            "INSERT INTO indications_by_agency (indication_id, agency, approval_date, fetched_at) VALUES "
            "('k1','MFDS','2017-03-21','t'),('k2','MFDS','2024-07-25','t'),('k3','FDA','2025-01-01','t'),"
            "('w1','MFDS','2023-05-01','t');"
            "INSERT INTO indication_reimbursement (indication_id, is_reimbursed, updated_at) VALUES "
            "('k1',1,'2026-10-17'),('old_id',0,'2026-10-17');")
    ctx = fr.Ctx(db_path=p, base_dir=tmp_path, today=date(2026, 10, 17))
    r = fr.check_reimbursement_checklist_mfds_gap(ctx)
    assert r.stale and r.kind == "improvement" and r.runbook is None
    assert "급여 체크리스트 입력 필요" in r.evidence and "k2" in r.evidence and "old_id" in r.evidence
    assert "k3" not in r.evidence and "w1" not in r.evidence           # MFDS 미허가·타 제품은 제외
    assert r.observed == "missing=1,orphan=1"
    assert fr.DOMAIN_CHECKS["reimbursement_checklist_mfds_gap"] is fr.check_reimbursement_checklist_mfds_gap
    ids = fr.record_freshness_problems([r], today=ctx.today, db_path=tmp_path / "ledger.db")
    from agents.agent_tasks.store import get_task
    assert "급여 체크리스트 입력 필요" in get_task(ids[0], db_path=tmp_path / "ledger.db")["title"]
    with sqlite3.connect(p) as c:
        c.execute("INSERT INTO indication_reimbursement (indication_id, is_reimbursed, updated_at) VALUES ('k2',0,'x')")
        c.execute("UPDATE indication_reimbursement SET indication_id='k3' WHERE indication_id='old_id'")
    assert fr.check_reimbursement_checklist_mfds_gap(ctx).stale is False


# ── QG 데이터 계약 ────────────────────────────────────────────────────────────────

FDP_DDL = """CREATE TABLE foreign_drug_prices (id INTEGER PRIMARY KEY, country TEXT, query_name TEXT, product_name TEXT,
    dosage_strength TEXT, dosage_form TEXT, package_unit TEXT, form_type TEXT, raw_data TEXT, route TEXT,
    formulation_key TEXT, unit_strength_mg REAL, local_price REAL);"""
CT_DDL = ("CREATE TABLE competitor_trend (id INTEGER PRIMARY KEY, badge TEXT, headline TEXT, detail TEXT, "
          "source_type TEXT);")


def _contracts_db(tmp_path: Path, extra: str = "") -> Path:
    return _db(tmp_path, MASTER_DDL, IBA_DDL, REIMB_DDL, FDP_DDL, CT_DDL, extra)


def _checks(p: Path) -> dict:
    from agents.quality_guard.data_contracts import check_data_contracts
    return {f["check"]: f for f in check_data_contracts(p, write_deviation=False)}


def test_data_contracts_clean_db_has_no_findings(tmp_path):
    p = _contracts_db(tmp_path, """
        INSERT INTO competitor_trend VALUES (1,'해외 승인','美 FDA, 키트루다 신규 적응증 승인','승인','promoted'),
                                            (2,'신규 출시','키트루다 국내 허가','식약처 허가','promoted');
        INSERT INTO indications_master VALUES ('k_mono','keytruda','x'), ('k_ev','keytruda','uc');
        INSERT INTO indications_by_agency (indication_id, agency, approval_date, combination_label, raw_source, fetched_at)
        VALUES ('k_mono','MFDS','2017-03-21',NULL,'{}','t'), ('k_mono','FDA','2014-09-04',NULL,'{}','t'),
               ('k_ev','MFDS','2024-07-25','enfortumab vedotin','manual_correction','t'),
               ('k_ev','FDA','2023-12-15','enfortumab vedotin','{}','t');
        INSERT INTO indication_reimbursement (indication_id, is_reimbursed, updated_at) VALUES ('k_ev',1,'x');
        INSERT INTO foreign_drug_prices VALUES (1,'UK','winrevair','Winrevair','45mg powder for solution for injection',
            'powder','1-vial kit','injection',NULL,'injection','winrevair_45mg_injection',45,1000);
    """)
    assert _checks(p) == {}


def test_data_contracts_detect_regressions(tmp_path):
    p = _contracts_db(tmp_path, """
        INSERT INTO competitor_trend VALUES (1,'해외 허가','x','y','promoted'),
                                            (2,'신규 출시','美 FDA, 키트루다 신규 적응증 승인','승인','promoted'),
                                            (3,'신규 출시','美 FDA, 키트루다 신규 적응증 승인','승인','manual');
        INSERT INTO indications_master VALUES ('k_mono','keytruda','x'), ('k_ev','keytruda','uc'),
                                              ('k_nmibc','keytruda','uc');
        INSERT INTO indications_by_agency (indication_id, agency, approval_date, combination_label, raw_source, fetched_at)
        VALUES ('k_mono','MFDS','2017-03-21',NULL,'{}','t'), ('k_mono','FDA','2014-09-04',NULL,'{}','t'),
               ('k_ev','MFDS','2017-03-21','enfortumab vedotin','{}','t'),
               ('k_ev','FDA','2023-12-15','enfortumab vedotin','{}','t'),
               ('k_nmibc','MFDS',NULL,NULL,'manual_correction','t');
        INSERT INTO indication_reimbursement (indication_id, is_reimbursed, updated_at) VALUES ('gone',1,'x');
        INSERT INTO foreign_drug_prices VALUES
            (1,'US','winrevair','Winrevair','45 mg','powder for solution for injection','KIT','unknown',NULL,'oral',
             'winrevair_45mg_oral',45,1000),
            (2,'UK','winrevair','Winrevair','; 2-vial kit','','','injection',NULL,'injection','unmatched',NULL,2000);
    """)
    f = _checks(p)
    assert f["competitor_badge_not_allowed"]["ids"] == [1]
    assert f["competitor_foreign_as_launch"]["ids"] == [2]                 # manual 카드(3)는 사람 편집 보존
    assert f["mfds_later_indication_first_date"]["ids"] == ["k_ev"]
    assert f["manual_correction_missing_date"]["ids"] == ["k_nmibc:MFDS"]
    assert f["foreign_injection_route_as_oral"]["count"] == 1 and f["foreign_injection_route_as_oral"]["severity"] == "ERROR"
    assert f["foreign_continuation_fragment"]["count"] == 1
    assert f["checklist_orphan"]["ids"] == ["gone"]
    assert all(x["kind"] == "data" and x["file"] == f"data_contract:{k}" for k, x in f.items())


def test_review_codebase_includes_data_contracts():
    import inspect
    from agents.quality_guard import agent as qg_agent
    src = inspect.getsource(qg_agent.QualityGuardAgent.review_codebase)
    assert "check_data_contracts()" in src and "contract_issues" in src


def test_rule_drift_has_no_unguarded_approval_date_update():
    """indications_by_agency.approval_date 를 직접 UPDATE 하는 agents/·scripts/ 파일은 manual_correction 을 알아야 한다."""
    import re
    root = Path(__file__).resolve().parent.parent
    pat = re.compile(r"UPDATE\s+indications_by_agency[\s\S]{0,300}?approval_date", re.IGNORECASE)
    bad = [str(py.relative_to(root)) for d in ("agents", "scripts") for py in (root / d).rglob("*.py")
           if "__pycache__" not in str(py) and py.parent.name != "quality_guard"
           and pat.search(py.read_text(encoding="utf-8", errors="ignore"))
           and "manual_correction" not in py.read_text(encoding="utf-8", errors="ignore")]
    assert bad == []


# ── §5 레지멘 정본 · 키워드 캐시 ─────────────────────────────────────────────────────

def test_onco_ingest_refuses_empty_parse(monkeypatch, tmp_path):
    from agents.scrapers import onco_regimen_ingest as oi
    monkeypatch.setattr(oi, "parse_rows", lambda path: ([], []))

    class _Boom:
        def _connect(self):
            raise AssertionError("빈 파싱인데 DB 에 접근했다 (DELETE 위험)")
    with pytest.raises(ValueError):
        oi.ingest(_Boom(), tmp_path / "x.xlsx")


def test_gov_summary_cache_read_is_filtered():
    from agents import government_keyword_summary as g
    cached = {"keywords": [{"text": "등을", "weight": 60}, {"text": "건강보험 재정", "weight": 70}],
              "newsByKeyword": {"등을": [{"t": 1}], "건강보험 재정": [{"t": 2}]}}
    out = g._filter_cached_keywords(json.loads(json.dumps(cached)))
    assert [k["text"] for k in out["keywords"]] == ["건강보험 재정"]
    assert list(out["newsByKeyword"]) == ["건강보험 재정"]


def test_admin_competitor_patch_promotes_card_to_manual():
    """admin 이 고친 promoted/auto_naver 카드는 manual 로 — 다음 크롤·promote 의 같은 url UPSERT 가 되돌리지 않는다(§4).
    (api/server.py 는 flask 의존이라 소스로 고정 — 동작 테스트는 _upsert_trend 의 manual skip 이 담당)"""
    import re
    src = (Path(__file__).resolve().parent.parent / "api" / "server.py").read_text(encoding="utf-8")
    body = re.search(r"def competitor_update\(item_id: int\):([\s\S]*?)\n@app\.", src).group(1)
    assert 'fields.setdefault("source_type", "manual")' in body
