"""감사 제안 승인 불변식 (agents/stakeholder/approval.py) — 소스 stakeholder_service.py 의 모든 HTTPException 분기 대응.

소스 대응표(분기 → 테스트):
- create_audit_proposal: pydantic AuditProposalInput/AuditEvidence 422 → test_validate_proposal_input_*;
  열린 dedupe_key 409 → test_create_proposal_dedupes_open_keys / test_create_proposal_dedupe_blocks_scheduled_not_closed
- approve_audit_proposal: 404 / 상태 409 / 미확인 충돌 422(T2b) / source_expansion(근거 422·도메인 422·필드 422·중복 409·URL 422)
  / role_change(근거 422·인물 404·직책 422·대기 409) / new_person(근거 422·필드 422·동명 409)
- reject_audit_proposal: 404 / 상태 409
- AuditDecisionInput: reason 길이 422 → test_decision_reason_validation
실제 DB 는 쓰지 않는다 — 전부 tmp_path.
"""
from __future__ import annotations

import json
import threading
from datetime import date
from pathlib import Path

import pytest

from agents.stakeholder import approval as ap
from agents.stakeholder import store

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "stakeholder_src_mini"

E = lambda **kw: {"url": "https://www.hira.or.kr/x", "publisher": "심평원", "source_class": "official",  # noqa: E731
                  "evidence_level": "E3", "locator": "본문 1문단", **kw}


# ── 순수 함수 ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("evidence,levels,official,expected", [
    ([E()], {"E3", "E4"}, True, True),
    ([E(evidence_level="E2")], {"E3", "E4"}, True, False),
    ([E(source_class="healthcare_trade")], {"E3", "E4"}, True, False),      # official 요구
    ([E(locator="")], {"E3", "E4"}, False, False),                          # locator 필수
    ([E(publisher="")], {"E3", "E4"}, False, False),                        # publisher 필수
    ([E(evidence_level="E2", source_class="healthcare_trade")], {"E2", "E3", "E4"}, False, True),
    ([E(evidence_level="E1"), E(evidence_level="E4", locator="")], {"E2", "E3", "E4"}, False, False),  # 한 항목이 전부 충족
    ([E(evidence_level="E1"), E(evidence_level="E2")], {"E2", "E3", "E4"}, False, True),
    ([], {"E2"}, False, False),
])
def test_has_evidence(evidence, levels, official, expected):
    assert ap.has_evidence({"evidence": evidence}, levels, official=official) is expected


def test_has_evidence_tolerates_missing_evidence_key():
    assert ap.has_evidence({}, {"E3"}) is False
    assert ap.has_evidence({"evidence": None}, {"E3"}) is False


@pytest.mark.parametrize("raw,expected", [("NewsPrime.co.kr.", "newsprime.co.kr"),
                                          ("https://www.hira.or.kr/", "www.hira.or.kr"),
                                          ("  HIRA.or.kr ", "hira.or.kr"),
                                          ("http://example.com:8080", "example.com")])
def test_normalize_domain_ok(raw, expected):
    assert ap.normalize_domain(raw) == expected


@pytest.mark.parametrize("raw", ["hira.or.kr/path", "hira.or.kr?x=1", "", "https://a.b/#f", "   ", None])
def test_normalize_domain_rejects(raw):
    with pytest.raises(ap.ApprovalError) as ei:
        ap.normalize_domain(raw)
    assert ei.value.status == 422 and ei.value.message == "출처 도메인은 경로 없는 호스트명이어야 합니다."


_BODY = {"proposal_type": "new_person", "dedupe_key": "new_person:a:b:c", "summary": "요약문", "rationale": "근거문",
         "proposed_change": {"name": "a"}, "evidence": [E()]}


def test_validate_proposal_input_mirrors_pydantic():
    ok = ap.validate_proposal_input(dict(_BODY))
    assert ok["priority"] == "normal" and ok["created_by"] == "Monitoring Coverage Auditor" and ok["conflicts"] == []
    assert ok["audit_run_id"] is None
    ev = ok["evidence"][0]
    # AuditEvidence 기본값·선택 필드가 채워진다 (model_dump 와 같은 키 집합)
    assert set(ev) == {"url", "publisher", "source_class", "evidence_level", "publication_date", "effective_date",
                       "quoted_text", "locator", "retrieved_at", "content_hash", "independence_group"}
    assert ev["quoted_text"] == "" and ev["publication_date"] is None
    defaults = ap.validate_proposal_input({**_BODY, "evidence": [{"url": "https://x.test/a"}]})["evidence"][0]
    assert (defaults["publisher"], defaults["source_class"], defaults["evidence_level"], defaults["locator"]) == \
        ("", "secondary", "E0", "")


def test_validate_proposal_input_ignores_undeclared_fields():
    """pydantic 은 선언 밖 키를 버린다 — 에이전트가 status·proposal_id·reviewer 를 넣어도 무시."""
    ok = ap.validate_proposal_input({**_BODY, "status": "applied", "proposal_id": "AUD-X", "reviewer": "agent"})
    assert "status" not in ok and "proposal_id" not in ok and "reviewer" not in ok


@pytest.mark.parametrize("bad,needle", [
    ({"proposal_type": "merge"}, "지원하지 않는 감사 제안 유형입니다."),
    ({"proposal_type": None}, "지원하지 않는 감사 제안 유형입니다."),
    ({"priority": "medium"}, "priority 값이 올바르지 않습니다."),
    ({"dedupe_key": "ab"}, "dedupe_key"),
    ({"dedupe_key": "x" * 241}, "dedupe_key"),
    ({"summary": "x"}, "summary"),
    ({"summary": "요약"}, "summary"),                    # 소스 min_length=3 (계획 예시의 2자 본문은 소스에서도 422)
    ({"rationale": "근거"}, "rationale"),
    ({"summary": "x" * 1001}, "summary"),
    ({"rationale": "xy"}, "rationale"),
    ({"rationale": "x" * 3001}, "rationale"),
    ({"proposed_change": None}, "proposed_change"),
    ({"proposed_change": ["a"]}, "proposed_change"),
    ({"evidence": [{"url": "ftp://x"}]}, "근거 URL은 http 또는 https 주소여야 합니다."),
    ({"evidence": [{"url": "https://"}]}, "근거 URL은 http 또는 https 주소여야 합니다."),
    ({"evidence": [{"publisher": "p"}]}, "근거 URL은 http 또는 https 주소여야 합니다."),   # url 필수
    ({"evidence": ["https://x.test"]}, "evidence"),
    ({"evidence": [{"url": "https://x.test", "publisher": 3}]}, "publisher"),
    ({"conflicts": "한 줄"}, "conflicts"),
    ({"summary": 123}, "summary"),
])
def test_validate_proposal_input_rejects(bad, needle):
    with pytest.raises(ap.ApprovalError) as ei:
        ap.validate_proposal_input({**_BODY, **bad})
    assert ei.value.status == 422 and needle in ei.value.message


def test_validate_proposal_input_missing_required():
    for key in ("proposal_type", "dedupe_key", "summary", "rationale", "proposed_change"):
        body = dict(_BODY)
        body.pop(key)
        with pytest.raises(ap.ApprovalError) as ei:
            ap.validate_proposal_input(body)
        assert ei.value.status == 422


# ── plan_approval: DB 없는 표 기반 ────────────────────────────────────────────────

class _Facts:
    def __init__(self, people=(), domains=(), pending=()):
        self.people = {p["person_id"]: p for p in people}
        self.domains, self.pending = set(domains), set(pending)

    def domain_exists(self, domain):
        return domain in self.domains

    def get_person(self, person_id):
        return self.people.get(person_id)

    def has_pending_role_change(self, person_id):
        return person_id in self.pending

    def person_exists(self, name, organization):
        return any(p["name"] == name and p["organization"] == organization for p in self.people.values())


_KIM = {"person_id": "P-HIRA-KIM", "name": "김국희", "organization": "건강보험심사평가원", "department": "희귀·중증질환성과평가실",
        "role": "실장 후보", "verification_status": "candidate", "effective_from": "2026-10-09"}


def _p(ptype, change, evidence, conflicts=(), status="needs_review", pid="AUD-T"):
    return {"proposal_id": pid, "proposal_type": ptype, "proposed_change": change, "evidence": evidence,
            "conflicts": list(conflicts), "status": status}


_OFF = [E(url="https://www.newsprime.co.kr/news/1", publisher="프라임경제", source_class="off_list_media",
          evidence_level="E2")]


@pytest.mark.parametrize("proposal,facts,status,needle", [
    # source_expansion
    (_p("source_expansion", {"name": "n", "domain": "newsprime.co.kr", "source_group": "official"}, _OFF),
     _Facts(), 422, "출처 승인에는 E2 이상의 근거가 필요합니다."),
    (_p("source_expansion", {"name": "n", "domain": "newsprime.co.kr"}, [E(evidence_level="E1")]),
     _Facts(), 422, "출처 승인에는 E2 이상의 근거가 필요합니다."),                       # 기본 discovered_additional 도 E2+
    (_p("source_expansion", {"name": "n", "domain": "newsprime.co.kr/a", "source_group": "healthcare_trade"}, _OFF),
     _Facts(), 422, "출처 도메인은 경로 없는 호스트명이어야 합니다."),
    (_p("source_expansion", {"name": " ", "domain": "newsprime.co.kr", "source_group": "healthcare_trade"}, _OFF),
     _Facts(), 422, "출처명, 도메인, 출처군을 확인해 주세요."),
    (_p("source_expansion", {"name": "n", "domain": "newsprime.co.kr", "source_group": "blog"}, _OFF),
     _Facts(), 422, "출처명, 도메인, 출처군을 확인해 주세요."),
    (_p("source_expansion", {"name": "n", "domain": "www.newsprime.co.kr", "source_group": "healthcare_trade"}, _OFF),
     _Facts(domains={"www.newsprime.co.kr"}), 409, "이미 출처 레지스트리에 있는 도메인입니다."),
    (_p("source_expansion", {"name": "n", "domain": "newsprime.co.kr", "source_group": "healthcare_trade"}, _OFF),
     _Facts(), 422, "제안 도메인과 일치하는 근거 URL이 필요합니다."),                   # www. 는 다른 호스트
    # role_change
    (_p("role_change", {"person_id": "P-HIRA-KIM", "role": "실장"}, [E(evidence_level="E2")]),
     _Facts(people=[_KIM]), 422, "현재 직책 변경에는 공식 E3 이상의 근거가 필요합니다."),
    (_p("role_change", {"person_id": "P-HIRA-KIM", "role": "실장"}, [E(source_class="healthcare_trade")]),
     _Facts(people=[_KIM]), 422, "현재 직책 변경에는 공식 E3 이상의 근거가 필요합니다."),
    (_p("role_change", {"person_id": "P-NOPE", "role": "실장"}, [E()]),
     _Facts(people=[_KIM]), 404, "인물을 찾을 수 없습니다."),
    (_p("role_change", {"person_id": "P-HIRA-KIM", "role": "  "}, [E()]),
     _Facts(people=[_KIM]), 422, "변경할 직책이 없습니다."),
    (_p("role_change", {"person_id": "P-HIRA-KIM", "role": "실장"}, [E()]),
     _Facts(people=[_KIM], pending={"P-HIRA-KIM"}), 409, "이미 적용 대기 중인 직책 변경이 있습니다."),
    # new_person
    (_p("new_person", {"name": "a", "organization": "b", "department": "c", "role": "d"}, [E(evidence_level="E1")]),
     _Facts(), 422, "신규 인물 승인에는 E2 이상의 근거가 필요합니다."),
    (_p("new_person", {"name": "a", "organization": "b", "department": "", "role": "d"}, [E()]),
     _Facts(), 422, "신규 인물의 이름, 기관, 부서, 직책이 모두 필요합니다."),
    (_p("new_person", {"name": "a", "organization": "b", "role": "d"}, [E()]),
     _Facts(), 422, "신규 인물의 이름, 기관, 부서, 직책이 모두 필요합니다."),
    (_p("new_person", {"name": "김국희", "organization": "건강보험심사평가원", "department": "c", "role": "d"}, [E()]),
     _Facts(people=[_KIM]), 409, "같은 기관에 동일한 이름의 인물이 이미 있습니다."),
])
def test_plan_approval_branches(proposal, facts, status, needle):
    with pytest.raises(ap.ApprovalError) as ei:
        ap.plan_approval(proposal, facts, effective_from="2026-10-13", ts="t", reviewer="admin@x")
    assert ei.value.status == status and needle in ei.value.message


@pytest.mark.parametrize("status,code", [("scheduled", 409), ("applied", 409), ("rejected", 409),
                                         ("superseded", 409), ("application_failed", 409), ("approved", 409)])
def test_check_decidable_state(status, code):
    with pytest.raises(ap.ApprovalError) as ei:
        ap.check_decidable(_p("new_person", {}, [], status=status), "approve")
    assert ei.value.status == code and ei.value.message == "현재 상태에서는 승인할 수 없습니다."
    with pytest.raises(ap.ApprovalError) as ei:
        ap.check_decidable(_p("new_person", {}, [], status=status), "reject")
    assert ei.value.message == "현재 상태에서는 반려할 수 없습니다."


def test_check_decidable_open_and_missing():
    ap.check_decidable(_p("new_person", {}, [], status="needs_evidence"), "approve")
    with pytest.raises(ap.ApprovalError) as ei:
        ap.check_decidable(None, "approve")
    assert ei.value.status == 404 and ei.value.message == "감사 제안을 찾을 수 없습니다."


def test_plan_approval_conflicts_checked_before_type_rules():
    """순서: 상태 409 → 충돌 확인 422 → 유형별 근거. 근거가 없어도 미확인 충돌 문구가 먼저 나온다.
    (T2b: 소스의 무조건 422 'CONFLICTS' 는 '충돌별 확인 후 승인' 으로 바뀜 — tests/test_stakeholder_conflict_ack.py)"""
    with pytest.raises(ap.ApprovalError) as ei:
        ap.plan_approval(_p("new_person", {}, [], conflicts=["x"]), _Facts(), effective_from="d", ts="t",
                         reviewer="r")
    assert ei.value.status == 422 and ei.value.code == "CONFLICTS_UNACKNOWLEDGED"
    assert ei.value.message == "확인되지 않은 충돌이 있어 승인할 수 없습니다(미확인: #0). 충돌마다 사유와 함께 확인해 주세요."


def test_plan_approval_outputs():
    np = ap.plan_approval(_p("new_person", {"name": "이소희", "organization": "대한민국 국회", "department": "보건복지위원회",
                                            "role": "위원", "topic_tags": ["약가"]}, [E(evidence_level="E2")]),
                          _Facts(), effective_from="2026-10-13", ts="T", reviewer="admin@x")
    assert np["kind"] == "person"
    row = np["row"]
    assert row["person_id"].startswith("P-AUD-") and len(row["person_id"]) == len("P-AUD-") + 12
    assert row["verification_status"] == "candidate" and row["status"] == "active" and row["organization_code"] == "AUDIT"
    assert row["effective_from"] == "2026-10-13" and row["discovered_from_proposal"] == "AUD-T"
    assert row["topic_tags"] == ["약가"] and row["aliases"] == [] and row["discovery_evidence"][0]["evidence_level"] == "E2"
    # topic_tags 미지정 → 소스 기본값
    np2 = ap.plan_approval(_p("new_person", {"name": "a", "organization": "b", "department": "c", "role": "d"}, [E()]),
                           _Facts(), effective_from="d", ts="T", reviewer="r")
    assert np2["row"]["topic_tags"] == ["약가", "건강보험 재정"]

    rc = ap.plan_approval(_p("role_change", {"person_id": "P-HIRA-KIM", "role": " 실장 "}, [E()]), _Facts(people=[_KIM]),
                          effective_from="2026-10-13", ts="T", reviewer="r")
    assert rc["kind"] == "role_history"
    assert rc["row"] == {"person_id": "P-HIRA-KIM", "organization": "건강보험심사평가원", "department": "희귀·중증질환성과평가실",
                         "role": "실장", "effective_from": "2026-10-13", "effective_to": None, "status": "scheduled",
                         "proposal_id": "AUD-T", "evidence": [E()], "approved_at": "T", "applied_at": None}
    se = ap.plan_approval(_p("source_expansion", {"name": "프라임경제", "domain": "NewsPrime.co.kr.",
                                                  "source_group": "top_tier_general"},
                             [E(url="https://NEWSPRIME.co.kr/a", evidence_level="E2", source_class="x")]),
                          _Facts(), effective_from="2026-10-13", ts="T", reviewer="r")
    assert se["kind"] == "source_domain" and se["row"]["domain"] == "newsprime.co.kr"
    assert se["row"]["source_id"].startswith("SRC-AUD-") and len(se["row"]["source_id"]) == len("SRC-AUD-") + 10
    assert se["row"]["approved_from_proposal"] == "AUD-T" and se["row"]["status"] == "approved"


def test_role_change_keeps_current_org_dept_when_absent_or_blank():
    rc = ap.plan_approval(_p("role_change", {"person_id": "P-HIRA-KIM", "organization": "보건복지부", "department": None,
                                             "role": "국장"}, [E()]), _Facts(people=[_KIM]),
                          effective_from="d", ts="T", reviewer="r")
    assert (rc["row"]["organization"], rc["row"]["department"]) == ("보건복지부", "희귀·중증질환성과평가실")


# ── DB 경로 ───────────────────────────────────────────────────────────────────────

def _seed(db):
    store.insert_person_if_absent({"person_id": "P-HIRA-KIM", "name": "김국희", "organization": "건강보험심사평가원",
        "organization_code": "HIRA", "department": "희귀·중증질환성과평가실", "role": "실장 후보", "status": "active",
        "verification_status": "candidate", "effective_from": "2026-10-09", "aliases": [], "topic_tags": [],
        "photo_url": None, "photo_path": None, "created_at": "t", "updated_at": "t"}, origin="import", db_path=db)
    store.insert_source_domain_if_absent({"source_id": "SRC-HIRA", "name": "심평원", "domain": "hira.or.kr",
        "source_group": "official", "status": "approved", "effective_from": "2026-10-09"}, origin="import", db_path=db)


def _proposal(db, ptype, change, evidence, conflicts=(), pid="AUD-1", status="needs_review"):
    store.insert_proposal_if_absent({"proposal_id": pid, "proposal_type": ptype, "dedupe_key": f"{ptype}:{pid}",
        "priority": "high", "summary": "s", "rationale": "r", "proposed_change": change, "evidence": evidence,
        "conflicts": list(conflicts), "audit_run_id": "coverage-audit-2026-10-10", "created_by": "auditor",
        "status": status, "effective_from": None, "reviewer": None, "reviewed_at": None, "decision_reason": None,
        "created_at": "t", "updated_at": "t"}, origin="import", db_path=db)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "today_kst", lambda: date(2026, 10, 12))
    p = tmp_path / "a.db"
    _seed(p)
    return p


def _counts(db):
    c = store.counts(db_path=db)
    with store.transaction(db) as conn:
        c["sv_person_role_history"] = conn.execute("SELECT COUNT(*) FROM sv_person_role_history").fetchone()[0]
        c["sv_audit_proposal_event"] = conn.execute("SELECT COUNT(*) FROM sv_audit_proposal_event").fetchone()[0]
    return c


def test_role_change_requires_official_e3_and_schedules(db):
    _proposal(db, "role_change", {"person_id": "P-HIRA-KIM", "role": "실장"}, [E(evidence_level="E2")])
    with pytest.raises(ap.ApprovalError) as ei:
        ap.approve("AUD-1", reviewer="admin@x", reason="ok", db_path=db)
    assert ei.value.status == 422 and "공식 E3" in ei.value.message
    _proposal(db, "role_change", {"person_id": "P-HIRA-KIM", "role": "실장"}, [E()], pid="AUD-2")
    out = ap.approve("AUD-2", reviewer="admin@x", reason="공식 인사 확인", db_path=db)
    assert out["status"] == "scheduled" and out["effective_from"] == "2026-10-13" and out["reviewer"] == "admin@x"
    assert out["decision_reason"] == "공식 인사 확인" and out["reviewed_at"]
    hist = store.list_role_history("P-HIRA-KIM", db_path=db)
    assert len(hist) == 1 and hist[0]["status"] == "scheduled" and hist[0]["role"] == "실장"
    assert hist[0]["proposal_id"] == "AUD-2" and hist[0]["evidence"] == [E()]
    assert store.get_person("P-HIRA-KIM", db_path=db)["role"] == "실장 후보"          # 익일 전 미반영
    _proposal(db, "role_change", {"person_id": "P-HIRA-KIM", "role": "실장2"}, [E()], pid="AUD-3")
    with pytest.raises(ap.ApprovalError) as ei:
        ap.approve("AUD-3", reviewer="admin@x", reason="x2", db_path=db)
    assert ei.value.status == 409 and ei.value.message == "이미 적용 대기 중인 직책 변경이 있습니다."
    assert [e["event"] for e in store.list_proposal_events("AUD-2", db_path=db)] == ["approved"]
    assert store.list_proposal_events("AUD-1", db_path=db) == []                      # 실패는 기록 없음(롤백)


def test_role_change_unknown_person_404(db):
    _proposal(db, "role_change", {"person_id": "P-NOPE", "role": "실장"}, [E()])
    with pytest.raises(ap.ApprovalError) as ei:
        ap.approve("AUD-1", reviewer="a", reason="r1", db_path=db)
    assert ei.value.status == 404


def test_apply_scheduled_moves_role_to_history_and_verifies(db):
    _proposal(db, "role_change", {"person_id": "P-HIRA-KIM", "role": "실장"}, [E()])
    ap.approve("AUD-1", reviewer="admin@x", reason="ok", db_path=db)
    assert ap.apply_scheduled(today=date(2026, 10, 12), db_path=db)["applied"] == 0
    res = ap.apply_scheduled(today=date(2026, 10, 13), db_path=db)
    assert res["applied"] == 1 and res["failed"] == 0 and res["applied_ids"] == ["AUD-1"]
    p = store.get_person("P-HIRA-KIM", db_path=db)
    assert p["role"] == "실장" and p["verification_status"] == "verified" and p["effective_from"] == "2026-10-13"
    assert p["organization"] == "건강보험심사평가원" and p["department"] == "희귀·중증질환성과평가실"
    hist = store.list_role_history("P-HIRA-KIM", db_path=db)
    assert {h["status"] for h in hist} == {"applied", "superseded"}                # 새 직책 applied + 옛 직책 superseded
    old = next(h for h in hist if h["status"] == "superseded")
    new = next(h for h in hist if h["status"] == "applied")
    assert old["role"] == "실장 후보" and old["effective_from"] == "2026-10-09" and old["effective_to"] == "2026-10-13"
    assert new["role"] == "실장" and new["applied_at"] and new["effective_to"] is None
    prop = store.get_proposal("AUD-1", db_path=db)
    assert prop["status"] == "applied" and prop["applied_at"]
    assert [e["event"] for e in store.list_proposal_events("AUD-1", db_path=db)] == ["approved", "applied"]
    # 멱등: 같은 날 두 번째 → 0
    again = ap.apply_scheduled(today=date(2026, 10, 13), db_path=db)
    assert again["applied"] == 0 and again["failed"] == 0
    assert len(store.list_role_history("P-HIRA-KIM", db_path=db)) == 2


def test_apply_scheduled_late_run_uses_scheduled_effective_date(db):
    """잡이 며칠 밀려도 효력일은 승인 시 정한 익일 — 실행일이 아니다."""
    _proposal(db, "role_change", {"person_id": "P-HIRA-KIM", "role": "실장"}, [E()])
    ap.approve("AUD-1", reviewer="admin@x", reason="ok", db_path=db)
    assert ap.apply_scheduled(today=date(2026, 10, 20), db_path=db)["applied"] == 1
    assert store.get_person("P-HIRA-KIM", db_path=db)["effective_from"] == "2026-10-13"


def test_second_role_change_supersedes_previous_applied_row(db, monkeypatch):
    _proposal(db, "role_change", {"person_id": "P-HIRA-KIM", "role": "실장"}, [E()])
    ap.approve("AUD-1", reviewer="a", reason="r1", db_path=db)
    ap.apply_scheduled(today=date(2026, 10, 13), db_path=db)
    # 다음 승인 가능(대기 해소) → 이전 applied 행이 superseded 로, 새 행 applied. 현재 직책 행은 언제나 1개.
    monkeypatch.setattr(store, "today_kst", lambda: date(2026, 11, 1))
    _proposal(db, "role_change", {"person_id": "P-HIRA-KIM", "department": "급여전략실", "role": "실장"}, [E()],
              pid="AUD-2")
    ap.approve("AUD-2", reviewer="a", reason="r2", db_path=db)
    ap.apply_scheduled(today=date(2026, 11, 2), db_path=db)
    hist = store.list_role_history("P-HIRA-KIM", db_path=db)
    assert len(hist) == 3
    applied = [h for h in hist if h["status"] == "applied"]
    superseded = sorted((h for h in hist if h["status"] == "superseded"), key=lambda h: h["effective_from"])
    assert len(applied) == 1 and applied[0]["department"] == "급여전략실" and applied[0]["proposal_id"] == "AUD-2"
    assert [(h["role"], h["effective_from"], h["effective_to"]) for h in superseded] == \
        [("실장 후보", "2026-10-09", "2026-10-13"), ("실장", "2026-10-13", "2026-11-02")]
    assert store.get_person("P-HIRA-KIM", db_path=db)["department"] == "급여전략실"


def test_source_expansion_rules(db):
    ev = [E(url="https://www.newsprime.co.kr/news/1", publisher="프라임경제", source_class="off_list_media",
            evidence_level="E2")]
    _proposal(db, "source_expansion", {"name": "프라임경제", "domain": "newsprime.co.kr", "source_group": "official"}, ev)
    with pytest.raises(ap.ApprovalError) as ei:
        ap.approve("AUD-1", reviewer="a", reason="r1", db_path=db)
    assert ei.value.status == 422                                                      # official 은 E3+official 필요
    _proposal(db, "source_expansion", {"name": "심평원", "domain": "hira.or.kr", "source_group": "healthcare_trade"}, [E()],
              pid="AUD-2")
    with pytest.raises(ap.ApprovalError) as ei:
        ap.approve("AUD-2", reviewer="a", reason="r1", db_path=db)
    assert ei.value.status == 409                                                      # 이미 있는 도메인
    _proposal(db, "source_expansion", {"name": "프라임경제", "domain": "newsprime.co.kr", "source_group": "top_tier_general"},
              [E(url="https://www.other.com/1", evidence_level="E2", source_class="x")], pid="AUD-3")
    with pytest.raises(ap.ApprovalError) as ei:
        ap.approve("AUD-3", reviewer="a", reason="r1", db_path=db)
    assert ei.value.status == 422 and "일치하는 근거 URL" in ei.value.message
    _proposal(db, "source_expansion", {"name": "프라임경제", "domain": "www.newsprime.co.kr",
                                       "source_group": "top_tier_general"}, ev, pid="AUD-4")
    out = ap.approve("AUD-4", reviewer="a", reason="r1", db_path=db)
    dom = [s for s in store.list_source_domains(db_path=db) if s["domain"] == "www.newsprime.co.kr"][0]
    assert dom["status"] == "approved" and dom["effective_from"] == "2026-10-13" and dom["approved_from_proposal"] == "AUD-4"
    assert dom["source_id"].startswith("SRC-AUD-") and out["status"] == "scheduled" and dom["origin"] == "audit"
    assert dom["evidence"] == ev
    res = ap.apply_scheduled(today=date(2026, 10, 13), db_path=db)
    assert res["applied"] == 1 and store.get_proposal("AUD-4", db_path=db)["status"] == "applied"


def test_new_person_rules_and_conflicts_block(db):
    change = {"name": "이소희", "organization": "대한민국 국회", "department": "보건복지위원회", "role": "제22대 국회의원(위원)"}
    _proposal(db, "new_person", change, [E(evidence_level="E2")], conflicts=["미해결"])
    with pytest.raises(ap.ApprovalError) as ei:
        ap.approve("AUD-1", reviewer="a", reason="r1", db_path=db)
    assert ei.value.status == 422 and "충돌" in ei.value.message
    _proposal(db, "new_person", change, [E(evidence_level="E2")], pid="AUD-2")
    ap.approve("AUD-2", reviewer="a", reason="r1", db_path=db)
    p = [x for x in store.list_people(db_path=db) if x["name"] == "이소희"][0]
    assert p["verification_status"] == "candidate" and p["origin"] == "audit" and p["person_id"].startswith("P-AUD-")
    assert p["discovered_from_proposal"] == "AUD-2" and p["effective_from"] == "2026-10-13"
    assert p["organization_code"] == "AUDIT" and p["status"] == "active"
    _proposal(db, "new_person", change, [E(evidence_level="E2")], pid="AUD-3")
    with pytest.raises(ap.ApprovalError) as ei:
        ap.approve("AUD-3", reviewer="a", reason="r1", db_path=db)
    assert ei.value.status == 409                                                       # 같은 기관 동명
    # 적용해도 candidate 유지 (공식 직책 근거 별도 확인 전 verified 자동 승격 금지)
    assert ap.apply_scheduled(today=date(2026, 10, 13), db_path=db)["applied"] == 1
    p = store.get_person(p["person_id"], db_path=db)
    assert p["verification_status"] == "candidate"
    assert store.get_proposal("AUD-2", db_path=db)["status"] == "applied"


def test_approve_state_guards_and_404(db):
    with pytest.raises(ap.ApprovalError) as ei:
        ap.approve("AUD-NOPE", reviewer="a", reason="r1", db_path=db)
    assert ei.value.status == 404 and ei.value.message == "감사 제안을 찾을 수 없습니다."
    for i, st in enumerate(("scheduled", "applied", "rejected", "superseded", "application_failed")):
        _proposal(db, "new_person", {}, [], pid=f"AUD-S{i}", status=st)
        with pytest.raises(ap.ApprovalError) as ei:
            ap.approve(f"AUD-S{i}", reviewer="a", reason="r1", db_path=db)
        assert ei.value.status == 409 and ei.value.message == "현재 상태에서는 승인할 수 없습니다."
        with pytest.raises(ap.ApprovalError) as ei:
            ap.reject(f"AUD-S{i}", reviewer="a", reason="r1", db_path=db)
        assert ei.value.status == 409 and ei.value.message == "현재 상태에서는 반려할 수 없습니다."


def test_needs_evidence_is_decidable(db):
    change = {"name": "이소희", "organization": "대한민국 국회", "department": "보건복지위원회", "role": "위원"}
    _proposal(db, "new_person", change, [E(evidence_level="E2")], status="needs_evidence")
    assert ap.approve("AUD-1", reviewer="a", reason="r1", db_path=db)["status"] == "scheduled"
    _proposal(db, "new_person", {}, [], pid="AUD-2", status="needs_evidence")
    assert ap.reject("AUD-2", reviewer="a", reason="r1", db_path=db)["status"] == "rejected"


@pytest.mark.parametrize("reviewer,reason,needle", [
    ("a", "", "검토 사유"), ("a", " x ", "검토 사유"), ("a", "x" * 1001, "검토 사유"), ("", "사유", "검토자"),
    ("x" * 81, "사유", "검토자")])
def test_decision_reason_validation(db, reviewer, reason, needle):
    _proposal(db, "new_person", {}, [])
    for fn in (ap.approve, ap.reject):
        with pytest.raises(ap.ApprovalError) as ei:
            fn("AUD-1", reviewer=reviewer, reason=reason, db_path=db)
        assert ei.value.status == 422 and needle in ei.value.message
    assert store.get_proposal("AUD-1", db_path=db)["status"] == "needs_review"


def test_reject_and_state_guard(db):
    _proposal(db, "new_person", {}, [])
    out = ap.reject("AUD-1", reviewer="a", reason="근거 부족", db_path=db)
    assert out["status"] == "rejected" and out["decision_reason"] == "근거 부족" and out["reviewer"] == "a"
    assert out["effective_from"] is None and out["reviewed_at"]
    with pytest.raises(ap.ApprovalError) as ei:
        ap.reject("AUD-1", reviewer="a", reason="again", db_path=db)
    assert ei.value.status == 409
    events = store.list_proposal_events("AUD-1", db_path=db)
    assert [e["event"] for e in events] == ["rejected"] and events[0]["actor"] == "a" and events[0]["detail"] == "근거 부족"
    with pytest.raises(ap.ApprovalError) as ei:
        ap.reject("AUD-NOPE", reviewer="a", reason="r1", db_path=db)
    assert ei.value.status == 404


def test_reject_with_conflicts_is_allowed(db):
    """소스 reject 는 conflicts 를 보지 않는다 — 충돌 제안은 반려로만 닫힌다."""
    _proposal(db, "new_person", {}, [], conflicts=["미해결"])
    assert ap.reject("AUD-1", reviewer="a", reason="충돌", db_path=db)["status"] == "rejected"


def test_approve_failure_leaves_no_side_rows(db, monkeypatch):
    """완료 기준: 승인 중 예외 → 부수 행·상태 전이·이벤트 모두 롤백."""
    change = {"name": "이소희", "organization": "대한민국 국회", "department": "보건복지위원회", "role": "위원"}
    _proposal(db, "new_person", change, [E()])
    before = _counts(db)
    real = ap._plan_new_person

    def boom(*a, **kw):
        raise RuntimeError("boom")
    monkeypatch.setattr(ap, "_plan_new_person", boom)
    with pytest.raises(RuntimeError):
        ap.approve("AUD-1", reviewer="a", reason="r1", db_path=db)
    assert _counts(db) == before and store.get_proposal("AUD-1", db_path=db)["status"] == "needs_review"

    # 부수 행 삽입 뒤(상태 전이 직전) 실패해도 행이 남지 않는다
    def late_boom(conn, *a, **kw):
        raise RuntimeError("late")
    monkeypatch.setattr(ap, "_plan_new_person", real)
    monkeypatch.setattr(store, "add_proposal_event_row", late_boom)
    with pytest.raises(RuntimeError):
        ap.approve("AUD-1", reviewer="a", reason="r1", db_path=db)
    assert _counts(db) == before and store.get_proposal("AUD-1", db_path=db)["status"] == "needs_review"
    assert not [p for p in store.list_people(db_path=db) if p["name"] == "이소희"]


def test_approve_race_guard_rowcount(db, monkeypatch):
    """읽은 상태가 stale 이어도 UPDATE … WHERE status IN (open) 의 rowcount 0 → 409, 부수 행 롤백."""
    change = {"name": "이소희", "organization": "대한민국 국회", "department": "보건복지위원회", "role": "위원"}
    _proposal(db, "new_person", change, [E()])
    ap.approve("AUD-1", reviewer="a", reason="r1", db_path=db)
    stale = {**store.get_proposal("AUD-1", db_path=db), "status": "needs_review",
             "proposed_change": {**change, "name": "다른사람"}}
    monkeypatch.setattr(store, "get_proposal", lambda *a, **kw: stale)
    with pytest.raises(ap.ApprovalError) as ei:
        ap.approve("AUD-1", reviewer="b", reason="r2", db_path=db)
    assert ei.value.status == 409 and ei.value.code == "RACE"
    monkeypatch.undo()
    assert not [p for p in store.list_people(db_path=db) if p["name"] == "다른사람"]
    assert store.get_proposal("AUD-1", db_path=db)["reviewer"] == "a"


def test_concurrent_approvals_exactly_one_wins(db):
    change = {"name": "이소희", "organization": "대한민국 국회", "department": "보건복지위원회", "role": "위원"}
    _proposal(db, "new_person", change, [E()])
    barrier = threading.Barrier(2)
    results: list = []

    def worker(name):
        barrier.wait()
        try:
            results.append(("ok", ap.approve("AUD-1", reviewer=name, reason="동시 승인", db_path=db)["reviewer"]))
        except ap.ApprovalError as exc:
            results.append(("err", exc.status))

    threads = [threading.Thread(target=worker, args=(n,)) for n in ("admin1@x", "admin2@x")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    oks = [r for r in results if r[0] == "ok"]
    errs = [r for r in results if r[0] == "err"]
    assert len(oks) == 1 and errs == [("err", 409)]
    assert len([p for p in store.list_people(db_path=db) if p["name"] == "이소희"]) == 1
    assert [e["event"] for e in store.list_proposal_events("AUD-1", db_path=db)] == ["approved"]
    assert store.get_proposal("AUD-1", db_path=db)["reviewer"] == oks[0][1]


def test_concurrent_approve_and_reject_exactly_one_wins(db):
    change = {"name": "이소희", "organization": "대한민국 국회", "department": "보건복지위원회", "role": "위원"}
    _proposal(db, "new_person", change, [E()])
    barrier = threading.Barrier(2)
    results: list = []

    def run(fn):
        barrier.wait()
        try:
            results.append(fn("AUD-1", reviewer="a", reason="동시 결정", db_path=db)["status"])
        except ap.ApprovalError as exc:
            results.append(exc.status)

    threads = [threading.Thread(target=run, args=(f,)) for f in (ap.approve, ap.reject)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert sorted(map(str, results)) in (["409", "scheduled"], ["409", "rejected"])
    assert len(store.list_proposal_events("AUD-1", db_path=db)) == 1


def test_apply_scheduled_failure_marks_application_failed(db):
    """scheduled role_change 인데 대기 직책 행이 없음(예: 외부 import) → application_failed + 이벤트, 잡은 계속."""
    _proposal(db, "role_change", {"person_id": "P-HIRA-KIM", "role": "실장"}, [E()], pid="AUD-BAD", status="scheduled")
    with store.transaction(db) as conn:
        conn.execute("UPDATE sv_audit_proposal SET effective_from='2026-10-13' WHERE proposal_id='AUD-BAD'")
    change = {"name": "이소희", "organization": "대한민국 국회", "department": "보건복지위원회", "role": "위원"}
    _proposal(db, "new_person", change, [E()], pid="AUD-OK")
    ap.approve("AUD-OK", reviewer="a", reason="r1", db_path=db)
    res = ap.apply_scheduled(today=date(2026, 10, 13), db_path=db)
    assert res["applied"] == 1 and res["failed"] == 1 and res["failed_ids"] == ["AUD-BAD"]
    bad = store.get_proposal("AUD-BAD", db_path=db)
    assert bad["status"] == "application_failed"
    ev = store.list_proposal_events("AUD-BAD", db_path=db)
    assert [e["event"] for e in ev] == ["application_failed"] and ev[0]["detail"]
    assert store.get_person("P-HIRA-KIM", db_path=db)["role"] == "실장 후보"          # 부분 반영 없음
    # 실패한 제안은 다음 실행에서 다시 집계되지 않는다
    assert ap.apply_scheduled(today=date(2026, 10, 14), db_path=db) == {
        "applied": 0, "failed": 0, "applied_ids": [], "failed_ids": [], "today": "2026-10-14"}


def test_apply_scheduled_new_person_missing_row_fails(db):
    _proposal(db, "new_person", {"name": "x"}, [], pid="AUD-NP", status="scheduled")
    with store.transaction(db) as conn:
        conn.execute("UPDATE sv_audit_proposal SET effective_from='2026-10-13' WHERE proposal_id='AUD-NP'")
    res = ap.apply_scheduled(today=date(2026, 10, 13), db_path=db)
    assert res["failed"] == 1 and store.get_proposal("AUD-NP", db_path=db)["status"] == "application_failed"


def test_apply_scheduled_defaults_today_kst(db):
    _proposal(db, "role_change", {"person_id": "P-HIRA-KIM", "role": "실장"}, [E()])
    ap.approve("AUD-1", reviewer="a", reason="r1", db_path=db)
    assert ap.apply_scheduled(db_path=db)["applied"] == 0                                # today_kst=10-12
    assert ap.apply_scheduled(today="2026-10-13", db_path=db)["applied"] == 1


def test_create_proposal_dedupes_open_keys(tmp_path):
    db = tmp_path / "a.db"
    body = {"proposal_type": "new_person", "dedupe_key": "new_person:a:b:c", "summary": "요약문", "rationale": "근거문",
            "proposed_change": {"name": "a"}, "evidence": [E()]}
    first = ap.create_proposal(body, origin="agent_api", db_path=db)
    assert first["proposal_id"].startswith("AUD-") and first["status"] == "needs_review"
    assert len(first["proposal_id"]) == len("AUD-") + 12 and first["origin"] == "agent_api"
    with pytest.raises(ap.ApprovalError) as ei:
        ap.create_proposal(body, origin="agent_api", db_path=db)
    assert ei.value.status == 409 and first["proposal_id"] in ei.value.message
    assert ei.value.message == f"같은 감사 제안이 이미 열려 있습니다: {first['proposal_id']}"
    ev = store.list_proposal_events(first["proposal_id"], db_path=db)
    assert [(e["event"], e["actor"], e["detail"]) for e in ev] == [("created", "Monitoring Coverage Auditor", "agent_api")]


def test_create_proposal_dedupe_blocks_scheduled_not_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "today_kst", lambda: date(2026, 10, 12))
    db = tmp_path / "a.db"
    body = {"proposal_type": "new_person", "dedupe_key": "new_person:이소희:국회:위원", "summary": "요약문", "rationale": "근거문",
            "proposed_change": {"name": "이소희", "organization": "대한민국 국회", "department": "보건복지위원회",
                                "role": "위원"}, "evidence": [E()]}
    first = ap.create_proposal(body, origin="agent_api", db_path=db)
    ap.approve(first["proposal_id"], reviewer="a", reason="r1", db_path=db)            # scheduled 도 dedupe 를 막는다
    with pytest.raises(ap.ApprovalError) as ei:
        ap.create_proposal(body, origin="agent_api", db_path=db)
    assert ei.value.status == 409
    ap.apply_scheduled(today=date(2026, 10, 13), db_path=db)                           # applied → 닫힘
    second = ap.create_proposal(body, origin="agent_api", db_path=db)
    ap.reject(second["proposal_id"], reviewer="a", reason="중복", db_path=db)          # rejected → 닫힘
    third = ap.create_proposal(body, origin="dashboard", db_path=db)
    assert third["status"] == "needs_review" and third["proposal_id"] not in {first["proposal_id"], second["proposal_id"]}


def test_create_proposal_rejects_bad_origin_and_input(tmp_path):
    db = tmp_path / "a.db"
    with pytest.raises(ap.ApprovalError) as ei:
        ap.create_proposal(dict(_BODY), origin="audit", db_path=db)
    assert ei.value.status == 422
    with pytest.raises(ap.ApprovalError) as ei:
        ap.create_proposal({**_BODY, "priority": "x"}, origin="agent_api", db_path=db)
    assert ei.value.status == 422
    assert store.list_proposals(db_path=db) == []


def test_create_then_approve_roundtrip_keeps_agent_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "today_kst", lambda: date(2026, 10, 12))
    db = tmp_path / "a.db"
    body = {"proposal_type": "source_expansion", "dedupe_key": "source_expansion:newsfake.test:healthcare_trade",
            "priority": "low", "summary": "가상 매체", "rationale": "가상 근거",
            "proposed_change": {"name": "가상", "domain": "newsfake.test", "source_group": "healthcare_trade"},
            "evidence": [{"url": "https://newsfake.test/a", "publisher": "가상", "source_class": "off_list_media",
                          "evidence_level": "E2", "locator": "1문단"}], "audit_run_id": "coverage-audit-x",
            "created_by": "auditor-x"}
    p = ap.create_proposal(body, origin="agent_api", db_path=db)
    assert p["priority"] == "low" and p["audit_run_id"] == "coverage-audit-x" and p["created_by"] == "auditor-x"
    out = ap.approve(p["proposal_id"], reviewer="admin@x", reason="확인", db_path=db)
    assert out["status"] == "scheduled"
    assert [s["domain"] for s in store.list_source_domains(db_path=db)] == ["newsfake.test"]


# ── 합성 픽스처 (소스 audit_proposals.json 형태 그대로) ─────────────────────────────

def test_fixture_proposals_follow_source_rules(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "today_kst", lambda: date(2026, 10, 12))
    db = tmp_path / "a.db"
    data = FIXTURE / "data"
    for p in json.loads((data / "people_registry.json").read_text(encoding="utf-8"))["people"]:
        store.insert_person_if_absent(p, origin="import", db_path=db)
    for s in json.loads((data / "source_registry.json").read_text(encoding="utf-8"))["sources"]:
        store.insert_source_domain_if_absent(s, origin="import", db_path=db)
    props = json.loads((data / "audit_proposals.json").read_text(encoding="utf-8"))["proposals"]
    for p in props:
        store.insert_proposal_if_absent(p, origin="import", db_path=db)
    by_type = {p["proposal_type"]: p["proposal_id"] for p in props}

    with pytest.raises(ap.ApprovalError) as ei:                                         # conflicts 있음 → 422
        ap.approve(by_type["new_person"], reviewer="a", reason="r1", db_path=db)
    assert ei.value.status == 422 and "충돌" in ei.value.message
    with pytest.raises(ap.ApprovalError) as ei:                                         # conflicts 있음 → 422
        ap.approve(by_type["source_expansion"], reviewer="a", reason="r1", db_path=db)
    assert ei.value.status == 422 and "충돌" in ei.value.message
    # 충돌이 풀렸다고 가정해도 E1 ×2(off_list_media) 는 E2 미달 → 422 (순수 plan 으로 확인)
    se = next(p for p in props if p["proposal_type"] == "source_expansion")
    with store.transaction(db) as conn:
        with pytest.raises(ap.ApprovalError) as ei:
            ap.plan_approval({**se, "conflicts": []}, ap._ConnFacts(conn), effective_from="d", ts="t", reviewer="r")
    assert ei.value.status == 422 and ei.value.message == "출처 승인에는 E2 이상의 근거가 필요합니다."
    out = ap.approve(by_type["role_change"], reviewer="admin@x", reason="공식 인사 보도자료", db_path=db)
    assert out["status"] == "scheduled"
    ap.apply_scheduled(today=date(2026, 10, 13), db_path=db)
    park = store.get_person("P-FAKE-INST-PARK", db_path=db)
    assert (park["role"], park["verification_status"]) == ("부장", "verified")
    # 반려로만 닫히는 충돌 제안
    assert ap.reject(by_type["new_person"], reviewer="a", reason="충돌 미해결", db_path=db)["status"] == "rejected"
