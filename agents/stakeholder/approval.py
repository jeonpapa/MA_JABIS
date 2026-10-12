"""감사 제안 승인 불변식 + 생성·승인·반려·스케줄 반영 — 소스 stakeholder_service.py 의 1:1 이식.

정책(MONITORING_AUDIT_AGENT_POLICY.md · agents/rules/stakeholder_verbatim_rules.md §3-A/§3-B):
에이전트는 제안만, 승인·반려는 사람(admin). source_expansion 은 official 이면 공식 E3+, 그 외 E2+; role_change 는
공식 E3+; new_person 은 E2+ 이고 등록은 항상 candidate. conflicts 가 있으면 승인 불가(반려만). 효력은 익일(KST).
scheduled → applied 는 소스에 없던 단계로, apply_scheduled(today) 가 매일 sync 잡 안에서 돈다.

구조
- 불변식은 **순수 함수**: has_evidence · normalize_domain · validate_proposal_input · check_decidable · plan_approval.
  plan_approval 은 DB 대신 `facts`(ApprovalFacts 프로토콜) 로 레지스트리 사실을 묻는다 — 표 기반 테스트·미리보기용.
- 실제 쓰기는 store.transaction(BEGIN IMMEDIATE) 한 번에: 부수 행(출처/직책 이력/신규 인물) + 상태 전이 + 이벤트.
  소스의 RLock+flock+원자 JSON 쓰기+.audit_transaction.json 저널+recover 가 이 트랜잭션 하나로 대체된다.
- 동시 결정: UPDATE … WHERE status IN ('needs_review','needs_evidence') 의 rowcount 0 → 409.
- 오류는 ApprovalError(status, message, code) — store.StoreError 의 하위 클래스라 라우트가 .status/.message/.code 를
  그대로 HTTP 로 옮긴다. 트랜잭션 안에서 난 store 오류(ValidationError/ConflictError/NotFoundError)도 ApprovalError 로
  바꿔 내보낸다. 메시지는 소스 한국어 문구 그대로(에이전트의 proposal_post_responses.json 이 422/409 문구를 기록한다).
"""
from __future__ import annotations

import logging
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any, Iterator, Protocol
from urllib.parse import urlparse

from . import store
from .store import DEFAULT_TOPIC_TAGS, OPEN_PROPOSAL_STATUSES, PRIORITIES, PROPOSAL_ORIGINS, PROPOSAL_TYPES, SOURCE_GROUPS

logger = logging.getLogger(__name__)

APPLY_ACTOR = "system:apply_scheduled"
CREATED_BY_DEFAULT = "Monitoring Coverage Auditor"       # 소스 AuditProposalInput.created_by 기본값
REVIEWER_MAX, REASON_MIN, REASON_MAX = 80, 2, 1000         # 소스 AuditDecisionInput
_OPEN_SQL = ",".join(f"'{s}'" for s in OPEN_PROPOSAL_STATUSES)

# AuditEvidence(pydantic) 필드: (기본값, 선택(None 허용) 여부)
_EVIDENCE_STR_FIELDS = {"publisher": "", "source_class": "secondary", "evidence_level": "E0", "quoted_text": "",
                        "locator": ""}
_EVIDENCE_OPT_FIELDS = ("publication_date", "effective_date", "retrieved_at", "content_hash", "independence_group")


class ApprovalError(store.StoreError):
    """라우트가 그대로 HTTP 로 옮기는 오류. status: 404 · 409 · 422."""

    def __init__(self, status: int, message: str, code: str = "APPROVAL"):
        super().__init__(message, code)
        self.status = status


@contextmanager
def _store_errors() -> Iterator[None]:
    try:
        yield
    except ApprovalError:
        raise
    except store.StoreError as exc:
        raise ApprovalError(exc.status, exc.message, exc.code) from exc


# ── 순수 불변식 (소스 has_evidence / normalize_domain 1:1) ─────────────────────────

def has_evidence(proposal: dict, levels: set[str] | frozenset[str], official: bool = False) -> bool:
    """levels 에 드는 증거수준 + (official 이면 source_class=='official') + publisher·locator 둘 다 있는 항목이 하나라도."""
    for item in proposal.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        if item.get("evidence_level") not in levels:
            continue
        if official and item.get("source_class") != "official":
            continue
        if not item.get("publisher") or not item.get("locator"):
            continue
        return True
    return False


def normalize_domain(value: str | None) -> str:
    """소문자·끝 '.' 제거 호스트명. 경로·쿼리·fragment 가 있거나 호스트가 없으면 422."""
    raw = (value or "").strip().lower().rstrip(".")
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = (parsed.hostname or "").rstrip(".")
    if not host or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ApprovalError(422, "출처 도메인은 경로 없는 호스트명이어야 합니다.", "BAD_DOMAIN")
    return host


def _url_host(url: Any) -> str:
    return (urlparse(str(url or "")).hostname or "").lower().rstrip(".")


def _text(change: dict, key: str) -> str:
    """proposed_change 의 텍스트 값. None 은 빈 값으로 본다(소스 str(None)='None' 오염 방지)."""
    v = change.get(key)
    return "" if v is None else str(v).strip()


# ── 입력 검증 (소스 AuditEvidence / AuditProposalInput pydantic 대응) ─────────────────

def _req_str(body: dict, key: str, lo: int, hi: int) -> str:
    if key not in body or body[key] is None:
        raise ApprovalError(422, f"{key} 가 필요합니다.", f"BAD_{key.upper()}")
    v = body[key]
    if not isinstance(v, str):
        raise ApprovalError(422, f"{key} 는 문자열이어야 합니다.", f"BAD_{key.upper()}")
    if not lo <= len(v) <= hi:
        raise ApprovalError(422, f"{key} 길이는 {lo}~{hi}자여야 합니다.", f"BAD_{key.upper()}")
    return v


def validate_evidence(item: Any) -> dict:
    """AuditEvidence: url 필수(http/https + host), 나머지는 문자열(기본값) 또는 선택 문자열. model_dump 와 같은 키."""
    if not isinstance(item, dict):
        raise ApprovalError(422, "evidence 항목은 객체여야 합니다.", "BAD_EVIDENCE")
    url = item.get("url")
    p = urlparse(url) if isinstance(url, str) else None
    if p is None or p.scheme not in {"http", "https"} or not p.netloc:
        raise ApprovalError(422, "근거 URL은 http 또는 https 주소여야 합니다.", "BAD_URL")
    out: dict[str, Any] = {"url": url}
    for key, default in _EVIDENCE_STR_FIELDS.items():
        v = item.get(key, default)
        if not isinstance(v, str):
            raise ApprovalError(422, f"evidence.{key} 는 문자열이어야 합니다.", "BAD_EVIDENCE")
        out[key] = v
    for key in _EVIDENCE_OPT_FIELDS:
        v = item.get(key)
        if v is not None and not isinstance(v, str):
            raise ApprovalError(422, f"evidence.{key} 는 문자열이어야 합니다.", "BAD_EVIDENCE")
        out[key] = v
    return out


def validate_proposal_input(body: Any) -> dict:
    """소스 AuditProposalInput 과 같은 규칙·기본값. 선언되지 않은 키(status·proposal_id·reviewer 등)는 버린다."""
    if not isinstance(body, dict):
        raise ApprovalError(422, "요청 본문은 객체여야 합니다.", "BAD_BODY")
    if "proposal_type" not in body:
        raise ApprovalError(422, "proposal_type 가 필요합니다.", "BAD_TYPE")
    ptype = body["proposal_type"]
    if ptype not in PROPOSAL_TYPES:
        raise ApprovalError(422, "지원하지 않는 감사 제안 유형입니다.", "BAD_TYPE")
    dedupe = _req_str(body, "dedupe_key", 3, 240)
    priority = body.get("priority", "normal")
    if priority not in PRIORITIES:
        raise ApprovalError(422, "priority 값이 올바르지 않습니다.", "BAD_PRIORITY")
    summary = _req_str(body, "summary", 3, 1000)
    rationale = _req_str(body, "rationale", 3, 3000)
    change = body.get("proposed_change")
    if not isinstance(change, dict):
        raise ApprovalError(422, "proposed_change 는 객체여야 합니다.", "BAD_CHANGE")
    evidence = body.get("evidence")
    if evidence is None:
        evidence = []
    if not isinstance(evidence, list):
        raise ApprovalError(422, "evidence 는 목록이어야 합니다.", "BAD_EVIDENCE")
    conflicts = body.get("conflicts")
    if conflicts is None:
        conflicts = []
    if not isinstance(conflicts, list) or not all(isinstance(c, str) for c in conflicts):
        raise ApprovalError(422, "conflicts 는 문자열 목록이어야 합니다.", "BAD_CONFLICTS")
    run_id = body.get("audit_run_id")
    if run_id is not None and not isinstance(run_id, str):
        raise ApprovalError(422, "audit_run_id 는 문자열이어야 합니다.", "BAD_AUDIT_RUN_ID")
    created_by = body.get("created_by", CREATED_BY_DEFAULT)
    if not isinstance(created_by, str):
        raise ApprovalError(422, "created_by 는 문자열이어야 합니다.", "BAD_CREATED_BY")
    return {"proposal_type": ptype, "dedupe_key": dedupe, "priority": priority, "summary": summary,
            "rationale": rationale, "proposed_change": change, "evidence": [validate_evidence(e) for e in evidence],
            "conflicts": list(conflicts), "audit_run_id": run_id, "created_by": created_by}


def _validate_decision(reviewer: Any, reason: Any) -> tuple[str, str]:
    """소스 AuditDecisionInput: reviewer 1~80, reason 2~1000. reviewer 는 라우트가 인증 토큰 sub 로 넣는다."""
    reason_s = reason.strip() if isinstance(reason, str) else ""
    if not REASON_MIN <= len(reason_s) <= REASON_MAX:
        raise ApprovalError(422, f"검토 사유는 {REASON_MIN}~{REASON_MAX}자로 입력해 주세요.", "BAD_REASON")
    reviewer_s = reviewer.strip() if isinstance(reviewer, str) else ""
    if not 1 <= len(reviewer_s) <= REVIEWER_MAX:
        raise ApprovalError(422, "검토자 정보가 올바르지 않습니다.", "BAD_REVIEWER")
    return reviewer_s, reason_s


# ── 승인 계획 (순수) ────────────────────────────────────────────────────────────────

class ApprovalFacts(Protocol):
    """plan_approval 이 묻는 레지스트리 사실. DB 구현은 _ConnFacts, 테스트는 dict 기반."""

    def domain_exists(self, domain: str) -> bool: ...
    def get_person(self, person_id: str) -> dict | None: ...
    def has_pending_role_change(self, person_id: str) -> bool: ...
    def person_exists(self, name: str, organization: str) -> bool: ...


class _ConnFacts:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def domain_exists(self, domain: str) -> bool:
        return self.conn.execute("SELECT 1 FROM sv_source_domain WHERE domain=?", (domain,)).fetchone() is not None

    def get_person(self, person_id: str) -> dict | None:
        return store.get_person(person_id, conn=self.conn)

    def has_pending_role_change(self, person_id: str) -> bool:
        return self.conn.execute("SELECT 1 FROM sv_person_role_history WHERE person_id=? AND status='scheduled'",
                                 (person_id,)).fetchone() is not None

    def person_exists(self, name: str, organization: str) -> bool:
        return self.conn.execute("SELECT 1 FROM sv_person WHERE name=? AND organization=?",
                                 (name, organization)).fetchone() is not None


def check_decidable(proposal: dict | None, action: str) -> None:
    """소스 find_proposal 404 + 상태 409 (needs_review·needs_evidence 만 승인·반려 가능)."""
    if proposal is None:
        raise ApprovalError(404, "감사 제안을 찾을 수 없습니다.", "NOT_FOUND")
    if proposal.get("status") not in OPEN_PROPOSAL_STATUSES:
        verb = "승인" if action == "approve" else "반려"
        raise ApprovalError(409, f"현재 상태에서는 {verb}할 수 없습니다.", "STATE")


def _plan_source_expansion(proposal: dict, change: dict, facts: ApprovalFacts, effective_from: str, ts: str) -> dict:
    group = str(change.get("source_group", "discovered_additional"))
    levels = {"E3", "E4"} if group == "official" else {"E2", "E3", "E4"}
    if not has_evidence(proposal, levels, official=group == "official"):
        raise ApprovalError(422, "출처 승인에는 E2 이상의 근거가 필요합니다.", "EVIDENCE")
    domain = normalize_domain(_text(change, "domain"))
    name = _text(change, "name")
    if not domain or not name or group not in SOURCE_GROUPS:
        raise ApprovalError(422, "출처명, 도메인, 출처군을 확인해 주세요.", "BAD_SOURCE")
    if facts.domain_exists(domain):
        raise ApprovalError(409, "이미 출처 레지스트리에 있는 도메인입니다.", "DUPLICATE_DOMAIN")
    evidence = [e for e in proposal.get("evidence") or [] if isinstance(e, dict)]
    if not any(_url_host(e.get("url")) == domain for e in evidence):
        raise ApprovalError(422, "제안 도메인과 일치하는 근거 URL이 필요합니다.", "EVIDENCE_DOMAIN")
    return {"source_id": f"SRC-AUD-{uuid.uuid4().hex[:10].upper()}", "name": name, "domain": domain,
            "source_group": group, "status": "approved", "effective_from": effective_from,
            "approved_from_proposal": proposal["proposal_id"], "evidence": proposal.get("evidence") or [],
            "created_at": ts, "updated_at": ts}


def _plan_role_change(proposal: dict, change: dict, facts: ApprovalFacts, effective_from: str, ts: str) -> dict:
    if not has_evidence(proposal, {"E3", "E4"}, official=True):
        raise ApprovalError(422, "현재 직책 변경에는 공식 E3 이상의 근거가 필요합니다.", "EVIDENCE")
    person = facts.get_person(_text(change, "person_id"))
    if person is None:
        raise ApprovalError(404, "인물을 찾을 수 없습니다.", "NOT_FOUND")
    new_organization = _text(change, "organization") or person["organization"]
    new_department = _text(change, "department") or person["department"]
    new_role = _text(change, "role")
    if not new_role:
        raise ApprovalError(422, "변경할 직책이 없습니다.", "BAD_ROLE")
    if facts.has_pending_role_change(person["person_id"]):
        raise ApprovalError(409, "이미 적용 대기 중인 직책 변경이 있습니다.", "PENDING_ROLE_CHANGE")
    return {"person_id": person["person_id"], "organization": new_organization, "department": new_department,
            "role": new_role, "effective_from": effective_from, "effective_to": None, "status": "scheduled",
            "proposal_id": proposal["proposal_id"], "evidence": proposal.get("evidence") or [], "approved_at": ts,
            "applied_at": None}


def _plan_new_person(proposal: dict, change: dict, facts: ApprovalFacts, effective_from: str, ts: str,
                     reviewer: str) -> dict:
    if not has_evidence(proposal, {"E2", "E3", "E4"}):
        raise ApprovalError(422, "신규 인물 승인에는 E2 이상의 근거가 필요합니다.", "EVIDENCE")
    required = {key: _text(change, key) for key in ("name", "organization", "department", "role")}
    if not all(required.values()):
        raise ApprovalError(422, "신규 인물의 이름, 기관, 부서, 직책이 모두 필요합니다.", "BAD_PERSON")
    if facts.person_exists(required["name"], required["organization"]):
        raise ApprovalError(409, "같은 기관에 동일한 이름의 인물이 이미 있습니다.", "DUPLICATE_PERSON")
    with _store_errors():
        aliases = store._str_list(change.get("aliases", []), "aliases")
        topic_tags = (store._str_list(change["topic_tags"], "topic_tags") if "topic_tags" in change
                      else list(DEFAULT_TOPIC_TAGS))
    return {"person_id": f"P-AUD-{uuid.uuid4().hex[:12].upper()}", **required, "organization_code": "AUDIT",
            "status": "active", "verification_status": "candidate", "effective_from": effective_from,
            "aliases": aliases, "topic_tags": topic_tags, "photo_url": None, "photo_path": None,
            "discovered_from_proposal": proposal["proposal_id"], "discovery_evidence": proposal.get("evidence") or [],
            "updated_by": reviewer, "created_at": ts, "updated_at": ts}


def plan_approval(proposal: dict | None, facts: ApprovalFacts, *, effective_from: str, ts: str,
                  reviewer: str) -> dict:
    """소스 approve_audit_proposal 의 검사 순서 그대로: 404 → 상태 409 → conflicts 422 → 유형별 분기.

    반환: {"kind": "source_domain"|"role_history"|"person", "row": <삽입할 행>}. DB 를 건드리지 않는다."""
    check_decidable(proposal, "approve")
    assert proposal is not None
    if proposal.get("conflicts"):
        raise ApprovalError(422, "미해결 충돌이 있는 제안은 승인할 수 없습니다.", "CONFLICTS")
    change = proposal.get("proposed_change") or {}
    ptype = proposal.get("proposal_type")
    if ptype == "source_expansion":
        return {"kind": "source_domain", "row": _plan_source_expansion(proposal, change, facts, effective_from, ts)}
    if ptype == "role_change":
        return {"kind": "role_history", "row": _plan_role_change(proposal, change, facts, effective_from, ts)}
    return {"kind": "person", "row": _plan_new_person(proposal, change, facts, effective_from, ts, reviewer)}


# ── 쓰기 (한 트랜잭션) ─────────────────────────────────────────────────────────────

def create_proposal(body: Any, *, origin: str, actor: str | None = None,
                    db_path: str | Path = store.DEFAULT_DB_PATH) -> dict:
    """소스 create_audit_proposal: 검증 → 열린(needs_review|needs_evidence|scheduled) 같은 dedupe_key 면 409 →
    `AUD-<12hex>` needs_review 로 삽입 + 'created' 이벤트(actor 기본 created_by, detail=origin). 반환: 저장된 제안."""
    if origin not in PROPOSAL_ORIGINS:
        raise ApprovalError(422, f"origin 값이 올바르지 않습니다: {origin!r}", "BAD_ORIGIN")
    data = validate_proposal_input(body)
    ts = store._ts()
    proposal_id = f"AUD-{uuid.uuid4().hex[:12].upper()}"
    row = {**data, "proposal_id": proposal_id, "status": "needs_review", "effective_from": None, "reviewer": None,
           "reviewed_at": None, "decision_reason": None, "applied_at": None, "created_at": ts, "updated_at": ts}
    with _store_errors(), store.transaction(db_path) as conn:
        dup = store.find_open_proposal(data["dedupe_key"], conn=conn)
        if dup:
            raise ApprovalError(409, f"같은 감사 제안이 이미 열려 있습니다: {dup['proposal_id']}", "DUPLICATE")
        store.insert_proposal_row(conn, row, origin=origin)
        store.add_proposal_event_row(conn, proposal_id, actor=actor or data["created_by"], event="created",
                                     detail=origin)
        return store.get_proposal(proposal_id, conn=conn)


def approve(proposal_id: str, *, reviewer: str, reason: str, db_path: str | Path = store.DEFAULT_DB_PATH) -> dict:
    """소스 approve_audit_proposal. 부수 행 삽입 + scheduled 전이(effective_from=익일) + 'approved' 이벤트를 한 트랜잭션으로.
    반환: 갱신된 제안. 오류: 404 · 409(상태·중복·대기·동시결정) · 422(사유·충돌·근거·필드)."""
    reviewer, reason = _validate_decision(reviewer, reason)
    with _store_errors(), store.transaction(db_path) as conn:
        proposal = store.get_proposal(proposal_id, conn=conn)
        effective_from, ts = store.tomorrow_kst(), store._ts()
        plan = plan_approval(proposal, _ConnFacts(conn), effective_from=effective_from, ts=ts, reviewer=reviewer)
        if plan["kind"] == "source_domain":
            store.insert_source_domain_row(conn, plan["row"], origin="audit")
        elif plan["kind"] == "role_history":
            store.insert_role_history_row(conn, plan["row"])
            conn.execute("UPDATE sv_person SET updated_at=? WHERE person_id=?", (ts, plan["row"]["person_id"]))
        else:
            store.insert_person_row(conn, plan["row"], origin="audit")
        cur = conn.execute(f"UPDATE sv_audit_proposal SET status='scheduled', effective_from=?, reviewer=?, "
                           f"reviewed_at=?, decision_reason=?, updated_at=? "
                           f"WHERE proposal_id=? AND status IN ({_OPEN_SQL})",
                           (effective_from, reviewer, ts, reason, ts, proposal_id))
        if cur.rowcount != 1:
            raise ApprovalError(409, "현재 상태에서는 승인할 수 없습니다.", "RACE")
        store.add_proposal_event_row(conn, proposal_id, actor=reviewer, event="approved", detail=reason)
        return store.get_proposal(proposal_id, conn=conn)


def reject(proposal_id: str, *, reviewer: str, reason: str, db_path: str | Path = store.DEFAULT_DB_PATH) -> dict:
    """소스 reject_audit_proposal: 404 → 상태 409 → rejected(+사유) + 'rejected' 이벤트. conflicts 는 보지 않는다."""
    reviewer, reason = _validate_decision(reviewer, reason)
    with _store_errors(), store.transaction(db_path) as conn:
        check_decidable(store.get_proposal(proposal_id, conn=conn), "reject")
        ts = store._ts()
        cur = conn.execute(f"UPDATE sv_audit_proposal SET status='rejected', reviewer=?, reviewed_at=?, "
                           f"decision_reason=?, updated_at=? WHERE proposal_id=? AND status IN ({_OPEN_SQL})",
                           (reviewer, ts, reason, ts, proposal_id))
        if cur.rowcount != 1:
            raise ApprovalError(409, "현재 상태에서는 반려할 수 없습니다.", "RACE")
        store.add_proposal_event_row(conn, proposal_id, actor=reviewer, event="rejected", detail=reason)
        return store.get_proposal(proposal_id, conn=conn)


# ── 스케줄 반영 (소스에 없던 scheduled → applied) ─────────────────────────────────────

def _apply_role_change(conn: sqlite3.Connection, proposal: dict, ts: str) -> None:
    """현재 직책을 이력으로 내리고(superseded, effective_to=새 효력일) 대기 행을 applied 로, 인물에 새 직책 + verified.

    '현재 직책' 이력 행은 인물당 최대 1개(status='applied'). 첫 변경이면 import 된 현재 직책을 superseded 스냅숏으로 남긴다."""
    sched = conn.execute("SELECT * FROM sv_person_role_history WHERE proposal_id=? AND status='scheduled' "
                         "ORDER BY id DESC LIMIT 1", (proposal["proposal_id"],)).fetchone()
    if sched is None:
        raise ApprovalError(409, "적용 대기 중인 직책 이력이 없습니다.", "APPLY_NO_SCHEDULED_ROLE")
    person = store.get_person(sched["person_id"], conn=conn)
    if person is None:
        raise ApprovalError(404, "인물을 찾을 수 없습니다.", "NOT_FOUND")
    eff = sched["effective_from"] or proposal["effective_from"]
    current = conn.execute("SELECT id FROM sv_person_role_history WHERE person_id=? AND status='applied' "
                           "ORDER BY id DESC LIMIT 1", (person["person_id"],)).fetchone()
    if current is not None:
        conn.execute("UPDATE sv_person_role_history SET status='superseded', effective_to=? WHERE id=?",
                     (eff, current["id"]))
    else:
        store.insert_role_history_row(conn, {
            "person_id": person["person_id"], "organization": person["organization"],
            "department": person["department"], "role": person["role"], "effective_from": person["effective_from"],
            "effective_to": eff, "status": "superseded", "proposal_id": None, "evidence": None,
            "approved_at": None, "applied_at": None})
    conn.execute("UPDATE sv_person_role_history SET status='applied', applied_at=? WHERE id=?", (ts, sched["id"]))
    conn.execute("UPDATE sv_person SET organization=?, department=?, role=?, verification_status='verified', "
                 "effective_from=?, updated_at=? WHERE person_id=?",
                 (sched["organization"], sched["department"], sched["role"], eff, ts, person["person_id"]))


def _apply_one(conn: sqlite3.Connection, proposal_id: str, today: str, actor: str) -> bool:
    proposal = store.get_proposal(proposal_id, conn=conn)
    if (proposal is None or proposal["status"] != "scheduled" or not proposal.get("effective_from")
            or proposal["effective_from"] > today):
        return False                                          # 다른 실행이 먼저 처리했거나 아직 효력 전
    ts = store._ts()
    ptype = proposal["proposal_type"]
    if ptype == "role_change":
        _apply_role_change(conn, proposal, ts)
    elif ptype == "source_expansion":
        if conn.execute("SELECT 1 FROM sv_source_domain WHERE approved_from_proposal=?",
                        (proposal_id,)).fetchone() is None:
            raise ApprovalError(409, "승인 시 추가된 출처 행이 없습니다.", "APPLY_MISSING_ROW")
    else:
        if conn.execute("SELECT 1 FROM sv_person WHERE discovered_from_proposal=?",
                        (proposal_id,)).fetchone() is None:
            raise ApprovalError(409, "승인 시 등록된 인물 행이 없습니다.", "APPLY_MISSING_ROW")
    cur = conn.execute("UPDATE sv_audit_proposal SET status='applied', applied_at=?, updated_at=? "
                       "WHERE proposal_id=? AND status='scheduled'", (ts, ts, proposal_id))
    if cur.rowcount != 1:
        raise ApprovalError(409, "현재 상태에서는 반영할 수 없습니다.", "RACE")
    store.add_proposal_event_row(conn, proposal_id, actor=actor, event="applied",
                                 detail=f"effective_from={proposal['effective_from']}")
    return True


def _mark_failed(proposal_id: str, exc: BaseException, actor: str, db_path: str | Path) -> bool:
    detail = f"{getattr(exc, 'code', type(exc).__name__)}: {getattr(exc, 'message', None) or exc}"
    try:
        with store.transaction(db_path) as conn:
            ts = store._ts()
            cur = conn.execute("UPDATE sv_audit_proposal SET status='application_failed', updated_at=? "
                               "WHERE proposal_id=? AND status='scheduled'", (ts, proposal_id))
            if cur.rowcount != 1:
                return False
            store.add_proposal_event_row(conn, proposal_id, actor=actor, event="application_failed",
                                         detail=detail[:1000])
            return True
    except Exception:  # noqa: BLE001 — 실패 기록 실패도 잡을 죽이지 않는다
        logger.warning("apply_scheduled: %s 실패 기록 불가", proposal_id, exc_info=True)
        return False


def apply_scheduled(*, today: date | str | None = None, actor: str = APPLY_ACTOR,
                    db_path: str | Path = store.DEFAULT_DB_PATH) -> dict:
    """effective_from <= today 인 scheduled 제안을 applied 로(제안마다 BEGIN IMMEDIATE 1개 — 하나의 실패가 다른 반영을
    막지 않는다). role_change 는 현재 직책을 이력(superseded)으로 내리고 새 직책 + verified(승인이 공식 E3+ 를 요구했으므로).
    new_person·source_expansion 은 행이 승인 때 이미 있으므로 존재만 확인하고 상태만 applied — 신규 인물은 candidate 유지.
    실패는 application_failed + 이벤트, 예외를 밖으로 내지 않는다. 멱등: 같은 날 두 번째 실행은 applied=0.

    반환: {"applied": int, "failed": int, "applied_ids": [...], "failed_ids": [...], "today": "YYYY-MM-DD"}"""
    t = today or store.today_kst()
    t_iso = t.isoformat() if isinstance(t, date) else str(t)
    due = [p["proposal_id"] for p in store.list_proposals(status="scheduled", db_path=db_path)
           if p.get("effective_from") and p["effective_from"] <= t_iso]
    applied: list[str] = []
    failed: list[str] = []
    for pid in sorted(due):
        try:
            with store.transaction(db_path) as conn:
                if _apply_one(conn, pid, t_iso, actor):
                    applied.append(pid)
        except Exception as exc:  # noqa: BLE001 — 반영 실패는 기록하고 다음 제안으로
            logger.warning("apply_scheduled: %s 반영 실패: %s", pid, exc)
            if _mark_failed(pid, exc, actor, db_path):
                failed.append(pid)
    return {"applied": len(applied), "failed": len(failed), "applied_ids": applied, "failed_ids": failed,
            "today": t_iso}
