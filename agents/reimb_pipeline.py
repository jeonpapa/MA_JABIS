"""약평위·암질심 급여 파이프라인 비즈니스 로직 (meetings / pipeline / results + admin CRUD).

데이터 소스 (기존 테이블 — 재생성 금지, 읽기/UPSERT 만):
  - amjilsim_sessions          : 위원회 일정 (committee_type AMJILSIM|YAKPYUNGWI)
  - amjilsim_drugs             : 추적 약제 (pass date / negotiation / 파이프라인 보드 필드)
  - amjilsim_drug_queue_status : 약제 × 차수 큐 이벤트
  - reimb_reports              : 분석 리포트 (post 리포트를 차수 결과에 링크)

원칙 (CLAUDE.md 데이터 정직성):
  - DB 실값에서만 파생. 날짜 날조 금지 — 없으면 null.
  - timeline 은 실제 날짜 컬럼(submitted/amjilsim_pass/yakpyungwi_pass)만 사용.

오류 규약: ValueError → 400, LookupError → 404 (server.py 핸들러에서 매핑).
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any, Optional

BASE_DIR = Path(__file__).resolve().parents[1]
DB_PATH = BASE_DIR / "data" / "db" / "drug_prices.db"

# ── 매핑 상수 ────────────────────────────────────────────────────────────────
COMMITTEE_DB_TO_API = {"AMJILSIM": "cancer", "YAKPYUNGWI": "evaluation"}
COMMITTEE_API_TO_DB = {v: k for k, v in COMMITTEE_DB_TO_API.items()}
COMMITTEE_KR = {"AMJILSIM": "암질심", "YAKPYUNGWI": "약평위"}

QUEUE_STATES = ("QUEUE_PENDING", "QUEUE_PROCESSED", "APPROVED",
                "REJECTED_REQUEUE", "WITHDRAWN")
STATE_LABEL_KR = {
    "APPROVED": "통과",
    "REJECTED_REQUEUE": "재심의",
    "WITHDRAWN": "철회",
    "QUEUE_PROCESSED": "심의 완료",
    "QUEUE_PENDING": "대기",
}
NEGOTIATION_STATUSES = ("NONE", "IN_PROGRESS", "STALLED", "AGREED", "REJECTED")
TRACKING_PRIORITIES = ("msd_asset", "competitor_class", "generic_new_drug")
LISTING_TYPES = ("신규", "확대")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_columns() -> None:
    """expected_session_id 컬럼 멱등 보강 (상정 예정 평가 로직 입력).
    amjilsim_drugs 가 아직 없는 빈 DB(최초 배포 볼륨)에선 스킵 — import 를 막지 않는다."""
    with _connect() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(amjilsim_drugs)")}
        if not cols:
            return  # 테이블 미존재 (빈 DB) — ingest 후 재호출 시 보강
        if "expected_session_id" not in cols:
            conn.execute("ALTER TABLE amjilsim_drugs ADD COLUMN expected_session_id INTEGER")
            conn.commit()


_ensure_columns()


def _valid_date(value: Any, field: str) -> Optional[str]:
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not _DATE_RE.match(value):
        raise ValueError(f"{field} 는 YYYY-MM-DD 형식이어야 함: {value!r}")
    return value


def _session_cycle(row: sqlite3.Row | dict) -> Optional[int]:
    """공식 차수 우선, 없으면 가정 차수."""
    official = row["ordinal_official"] if row["ordinal_official"] is not None else None
    return official if official is not None else row["ordinal_assumed"]


def _meeting_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["session_id"],
        "committee": COMMITTEE_DB_TO_API.get(row["committee_type"], row["committee_type"]),
        "year": row["year"],
        "cycle": _session_cycle(row),
        "date": row["session_date"],
        "status": row["status"],
        "note": row["note"],
        "minutes_url": row["official_minutes_url"],
    }


# ── 사용자 조회 ──────────────────────────────────────────────────────────────

def list_meetings() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM amjilsim_sessions ORDER BY session_date"
        ).fetchall()
    return [_meeting_dict(r) for r in rows]


def _drug_stage(drug: sqlite3.Row, queue_rows: list[sqlite3.Row]) -> str:
    """파이프라인 단계: nhis > evaluation > cancer (실데이터 기반)."""
    if drug["yakpyungwi_pass_date"]:
        return "nhis"
    has_yakpyungwi_queue = any(q["committee_type"] == "YAKPYUNGWI" for q in queue_rows)
    if drug["amjilsim_pass_date"] or has_yakpyungwi_queue:
        return "evaluation"
    return "cancer"


def _next_session_ids(conn: sqlite3.Connection, today: str) -> dict[str, Optional[int]]:
    """위원회별 다음(가장 이른 미래) 차수 session_id. 상정 예정 판정 기준."""
    out: dict[str, Optional[int]] = {}
    for committee in ("AMJILSIM", "YAKPYUNGWI"):
        row = conn.execute(
            "SELECT session_id FROM amjilsim_sessions "
            "WHERE committee_type = ? AND session_date >= ? "
            "ORDER BY session_date LIMIT 1",
            (committee, today),
        ).fetchone()
        out[committee] = row["session_id"] if row else None
    return out


def _drug_status(stage: str, drug: sqlite3.Row, queue_rows: list[sqlite3.Row],
                 today: str, next_session_ids: dict[str, Optional[int]]) -> str:
    """파이프라인 단계별 상태 (평가 로직).

    - nhis (약평위 통과 → 건보공단 협상 단계):
        negotiation_status == 'AGREED' → 'completed'(협상 완료, 실제 등재)
        그 외 → 'negotiating'(협상 중)
    - evaluation/cancer (심의 대기):
        expected_session_id == 해당 위원회 '다음 차수' → 'scheduled'(심의 상정예정)
        또는 큐가 미래 차수에 링크(미통과) → 'scheduled'
        그 외 → 'waiting'(심의 대기)
    """
    if stage == "nhis":
        return "completed" if drug["negotiation_status"] == "AGREED" else "negotiating"

    committee = "AMJILSIM" if stage == "cancer" else "YAKPYUNGWI"
    expected = drug["expected_session_id"]
    if expected is not None and expected == next_session_ids.get(committee):
        return "scheduled"

    in_committee = [q for q in queue_rows if q["committee_type"] == committee]
    if in_committee:
        latest = max(in_committee, key=lambda q: ((q["observed_at"] or ""), q["id"]))
        if (latest["queue_state"] != "APPROVED" and latest["session_date"]
                and latest["session_date"] > today):
            return "scheduled"
    return "waiting"


def _history(queue_rows: list[sqlite3.Row], drug: Optional[sqlite3.Row] = None) -> list[dict]:
    """큐 이벤트 이력 + (큐에 없는) pass_date 컬럼 기반 합성 이벤트 보강.

    다수 약제가 pass_date 만 있고 큐 이벤트가 없어 이력이 비어 보이는 문제 해결 —
    합성 이벤트는 synthetic=True 로 명시 (출처: amjilsim_drugs 통과일 컬럼).
    """
    items = []
    covered: set[tuple[str, str]] = set()  # (committee_type, APPROVED date)
    for q in queue_rows:
        observed_date = (q["observed_at"] or "")[:10] or None
        d = q["queue_entry_date"] or q["session_date"] or observed_date
        if q["queue_state"] == "APPROVED" and q["session_date"]:
            covered.add((q["committee_type"], q["session_date"]))
        items.append({
            "id": q["id"],
            "date": d,
            "committee": COMMITTEE_KR.get(q["committee_type"], q["committee_type"]),
            "state": q["queue_state"],
            "stateLabel": STATE_LABEL_KR.get(q["queue_state"], q["queue_state"]),
            "sessionId": q["session_id"],
            "sessionDate": q["session_date"],
            "cycle": _session_cycle(q) if q["session_date"] else None,
            "attempt": q["n_th_attempt"],
            "evidenceUrl": q["evidence_url"],
        })
    if drug is not None:
        for committee_type, pass_col in (("AMJILSIM", "amjilsim_pass_date"),
                                          ("YAKPYUNGWI", "yakpyungwi_pass_date")):
            d = drug[pass_col]
            if d and (committee_type, d) not in covered:
                items.append({
                    "id": None,
                    "date": d,
                    "committee": COMMITTEE_KR.get(committee_type, committee_type),
                    "state": "APPROVED",
                    "stateLabel": STATE_LABEL_KR.get("APPROVED", "통과"),
                    "sessionId": None,
                    "sessionDate": d,
                    "cycle": None,
                    "attempt": None,
                    "evidenceUrl": None,
                    "synthetic": True,  # 통과일 컬럼 기반 (큐 이벤트 미수집 차수)
                })
    items.sort(key=lambda x: (x["date"] or "9999-99-99"))
    return items


def _negotiation_phase_status(negotiation_status: Optional[str]) -> str:
    if negotiation_status == "AGREED":
        return "done"
    if negotiation_status in ("IN_PROGRESS", "STALLED"):
        return "in_progress"
    if negotiation_status == "REJECTED":
        return "rejected"
    return "upcoming"  # NULL / 'NONE'


def _expected_session_date(conn: sqlite3.Connection, session_id) -> Optional[str]:
    if not session_id:
        return None
    row = conn.execute(
        "SELECT session_date FROM amjilsim_sessions WHERE session_id = ?",
        (session_id,)).fetchone()
    return row["session_date"] if row else None


def _timeline(drug: sqlite3.Row, conn: Optional[sqlite3.Connection] = None,
              today: Optional[str] = None) -> list[dict]:
    """단계 타임라인 — 실측일은 date, 미도래 단계는 위원회 일정 기반 '예상일'(expectedDate).

    날조 금지 원칙 유지: 실제 통과일(date)과 일정 기반 예상일(expectedDate)을
    분리된 필드로 명시. 예상일 출처 = expected_session_id 차수 일정 / 다음 차수 일정.
    """
    today = today or date.today().isoformat()
    amj_pass = drug["amjilsim_pass_date"]
    yak_pass = drug["yakpyungwi_pass_date"]

    # 미도래 위원회 단계의 예상일: expected_session_id 일정 → 없으면 다음 차수 일정
    exp_amj = exp_yak = None
    if conn is not None:
        exp_date = _expected_session_date(conn, drug["expected_session_id"])
        nxt = _next_session_ids(conn, today)
        if not amj_pass:
            exp_amj = exp_date or _expected_session_date(conn, nxt.get("AMJILSIM"))
        if amj_pass and not yak_pass:
            exp_yak = exp_date or _expected_session_date(conn, nxt.get("YAKPYUNGWI"))

    out = [
        {"phase": "급여 신청(접수)", "date": drug["submitted_date"],
         "status": "done" if drug["submitted_date"] else "upcoming"},
        {"phase": "암질심 통과", "date": amj_pass,
         "expectedDate": exp_amj if not amj_pass else None,
         "status": "done" if amj_pass else ("expected" if exp_amj else "upcoming")},
        {"phase": "약평위 통과", "date": yak_pass,
         "expectedDate": exp_yak if not yak_pass else None,
         "status": "done" if yak_pass else ("expected" if exp_yak else "upcoming")},
    ]
    out.append({
        "phase": "건보공단 협상",
        "date": None,
        # 약평위 통과 후 협상: 국민건강보험법령상 60일 협상 기한 — 일정 기반 예상 구간
        "expectedDate": None,
        "status": _negotiation_phase_status(drug["negotiation_status"]),
        "negotiationStatus": drug["negotiation_status"],
    })
    return out


_QUEUE_JOIN_SQL = """
    SELECT q.*, s.session_date, s.ordinal_official, s.ordinal_assumed
    FROM amjilsim_drug_queue_status q
    LEFT JOIN amjilsim_sessions s ON s.session_id = q.session_id
    WHERE q.drug_id = ?
    ORDER BY q.observed_at, q.id
"""


def _key_issues(drug: sqlite3.Row) -> list[str]:
    raw = _row_get(drug, "key_issues")
    if not raw:
        return []
    try:
        val = json.loads(raw)
        return [str(x) for x in val] if isinstance(val, list) else []
    except (ValueError, TypeError):
        return []


def _row_get(row: sqlite3.Row, key: str):
    """sqlite3.Row 에 컬럼이 없을 수도 있는 환경 안전 접근."""
    try:
        return row[key]
    except (IndexError, KeyError):
        return None


def _pipeline_drug_dict(drug: sqlite3.Row, queue_rows: list[sqlite3.Row],
                        today: str, conn: sqlite3.Connection,
                        next_session_ids: dict[str, Optional[int]]) -> tuple[str, dict]:
    stage = _drug_stage(drug, queue_rows)
    observed = [q["observed_at"] for q in queue_rows if q["observed_at"]]
    status = _drug_status(stage, drug, queue_rows, today, next_session_ids)
    # 상정예정 차수 정보 (배지: "심의 상정예정 · 7/2 7차")
    expected_date = expected_cycle = None
    if status == "scheduled" and drug["expected_session_id"]:
        srow = conn.execute(
            "SELECT session_date, COALESCE(ordinal_official, ordinal_assumed) AS ord "
            "FROM amjilsim_sessions WHERE session_id = ?",
            (drug["expected_session_id"],)).fetchone()
        if srow:
            expected_date = srow["session_date"]
            expected_cycle = srow["ord"]
    item = {
        "id": drug["drug_id"],
        "name": drug["brand_kr"],
        "nameEn": drug["brand_en"],
        "ingredient": drug["ingredient_inn"],
        "company": drug["manufacturer"],
        "indication": drug["indication"],
        "type": drug["listing_type"],
        "msdFlag": bool(drug["msd_flag"]),
        "trackingPriority": drug["tracking_priority"],
        "status": status,
        "expectedSessionId": drug["expected_session_id"],
        "expectedSessionDate": expected_date,
        "expectedSessionCycle": expected_cycle,
        "submittedDate": drug["submitted_date"],
        "amjilsimPassDate": drug["amjilsim_pass_date"],
        "yakpyungwiPassDate": drug["yakpyungwi_pass_date"],
        "negotiationStatus": drug["negotiation_status"],
        "notes": drug["notes"],
        "keyIssues": _key_issues(drug),
        "updatedDate": max(observed) if observed else None,
        "history": _history(queue_rows, drug),
        "timeline": _timeline(drug, conn, today),
    }
    return stage, item


def get_pipeline(include_completed: bool = False) -> dict:
    """파이프라인 보드 3단계(cancer/evaluation/nhis).

    include_completed=False (기본): 공단 협상 완료(negotiation_status='AGREED') 약제 제외 —
    이미 등재 마무리된 건은 '진행 중' 보드에서 빼고 별도 completed 카운트로만 노출.
    """
    today = date.today().isoformat()
    stages: dict[str, list[dict]] = {"cancer": [], "evaluation": [], "nhis": []}
    completed_count = 0
    with _connect() as conn:
        next_ids = _next_session_ids(conn, today)
        drugs = conn.execute(
            "SELECT * FROM amjilsim_drugs ORDER BY brand_kr"
        ).fetchall()
        for drug in drugs:
            if not include_completed and drug["negotiation_status"] == "AGREED":
                completed_count += 1
                continue
            queue_rows = conn.execute(_QUEUE_JOIN_SQL, (drug["drug_id"],)).fetchall()
            stage, item = _pipeline_drug_dict(drug, queue_rows, today, conn, next_ids)
            stages[stage].append(item)
    return {
        "stages": [
            {"id": sid, "count": len(items), "drugs": items}
            for sid, items in stages.items()
        ],
        "completedExcluded": completed_count,
    }


def _linked_report(conn: sqlite3.Connection, meeting: dict) -> Optional[dict]:
    """차수 결과에 연결할 post 리포트: 회의일 일치 또는 (연도+차수) 일치."""
    row = conn.execute(
        """SELECT id, title, summary, highlights_json FROM reimb_reports
           WHERE report_type = 'post' AND committee = ?
             AND (session_date = ? OR (year = ? AND cycle = ?))
           ORDER BY analyzed DESC, id DESC LIMIT 1""",
        (meeting["committee"], meeting["date"], meeting["year"], meeting["cycle"]),
    ).fetchone()
    if not row:
        return None
    try:
        highlights = json.loads(row["highlights_json"] or "[]")
    except Exception:
        highlights = []
    return {"id": row["id"], "title": row["title"],
            "summary": row["summary"], "highlights": highlights}


def get_meeting_results(session_id: int) -> dict:
    with _connect() as conn:
        srow = conn.execute(
            "SELECT * FROM amjilsim_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if not srow:
            raise LookupError(f"세션 없음: {session_id}")
        meeting = _meeting_dict(srow)

        rows = conn.execute(
            """SELECT q.queue_state, q.n_th_attempt, q.evidence_url,
                      d.brand_kr, d.brand_en, d.ingredient_inn, d.manufacturer,
                      d.indication, d.drug_id
               FROM amjilsim_drug_queue_status q
               JOIN amjilsim_drugs d ON d.drug_id = q.drug_id
               WHERE q.session_id = ?
               ORDER BY q.id""",
            (session_id,),
        ).fetchall()

        totals = {"reviewed": 0, "approved": 0, "rejected": 0,
                  "withdrawn": 0, "pending": 0}
        drugs = []
        for r in rows:
            state = r["queue_state"]
            if state == "APPROVED":
                totals["approved"] += 1
            elif state == "REJECTED_REQUEUE":
                totals["rejected"] += 1
            elif state == "WITHDRAWN":
                totals["withdrawn"] += 1
            elif state == "QUEUE_PENDING":
                totals["pending"] += 1
            if state != "QUEUE_PENDING":
                totals["reviewed"] += 1
            drugs.append({
                "drugId": r["drug_id"],
                "name": r["brand_kr"],
                "nameEn": r["brand_en"],
                "ingredient": r["ingredient_inn"],
                "company": r["manufacturer"],
                "indication": r["indication"],
                "state": state,
                "stateLabel": STATE_LABEL_KR.get(state, state),
                "attempt": r["n_th_attempt"],
                "evidenceUrl": r["evidence_url"],
            })

        report = _linked_report(conn, meeting)

    return {"meeting": meeting, "totals": totals, "drugs": drugs, "report": report}


# ── Admin CRUD: drugs ────────────────────────────────────────────────────────

# (payload key, db column) — 생성/수정 공통 허용 필드
_DRUG_FIELDS = {
    "brand_kr": "brand_kr",
    "brand_en": "brand_en",
    "ingredient_inn": "ingredient_inn",
    "atc": "atc",
    "manufacturer": "manufacturer",
    "product_slug": "product_slug",
    "competitor_class": "competitor_class",
    "msd_flag": "msd_flag",
    "tracking_priority": "tracking_priority",
    "amjilsim_pass_date": "amjilsim_pass_date",
    "yakpyungwi_pass_date": "yakpyungwi_pass_date",
    "negotiation_status": "negotiation_status",
    "indication": "indication",
    "listing_type": "listing_type",
    "submitted_date": "submitted_date",
    "notes": "notes",
}
_DRUG_DATE_FIELDS = {"amjilsim_pass_date", "yakpyungwi_pass_date", "submitted_date"}


def _validate_drug_field(key: str, value: Any) -> Any:
    if key in _DRUG_DATE_FIELDS:
        return _valid_date(value, key)
    if value in (None, ""):
        return None
    if key == "msd_flag":
        return 1 if value in (1, True, "1", "true") else 0
    if key == "tracking_priority" and value not in TRACKING_PRIORITIES:
        raise ValueError(f"tracking_priority 는 {TRACKING_PRIORITIES} 중 하나여야 함")
    if key == "negotiation_status" and value not in NEGOTIATION_STATUSES:
        raise ValueError(f"negotiation_status 는 {NEGOTIATION_STATUSES} 중 하나여야 함")
    if key == "listing_type" and value not in LISTING_TYPES:
        raise ValueError(f"listing_type 은 {LISTING_TYPES} 중 하나여야 함")
    return value


def list_drugs_admin() -> list[dict]:
    today = date.today().isoformat()
    out = []
    with _connect() as conn:
        next_ids = _next_session_ids(conn, today)
        drugs = conn.execute(
            "SELECT * FROM amjilsim_drugs ORDER BY drug_id"
        ).fetchall()
        for drug in drugs:
            queue_rows = conn.execute(_QUEUE_JOIN_SQL, (drug["drug_id"],)).fetchall()
            d = dict(drug)
            d["msd_flag"] = bool(d["msd_flag"])
            stage, _ = _pipeline_drug_dict(drug, queue_rows, today, conn, next_ids)
            d["stage"] = stage
            latest = (max(queue_rows, key=lambda q: ((q["observed_at"] or ""), q["id"]))
                      if queue_rows else None)
            d["latest_queue"] = (
                {"id": latest["id"],
                 "committee": COMMITTEE_DB_TO_API.get(latest["committee_type"]),
                 "state": latest["queue_state"],
                 "session_id": latest["session_id"],
                 "session_date": latest["session_date"],
                 "queue_entry_date": latest["queue_entry_date"],
                 "attempt": latest["n_th_attempt"],
                 "evidence_url": latest["evidence_url"],
                 "observed_at": latest["observed_at"]}
                if latest else None
            )
            d["events"] = _history(queue_rows)
            out.append(d)
    return out


def create_drug(payload: dict) -> dict:
    if not isinstance(payload, dict) or not (payload.get("brand_kr") or "").strip():
        raise ValueError("brand_kr 필수")
    cols, vals = [], []
    for key, col in _DRUG_FIELDS.items():
        if key in payload:
            cols.append(col)
            vals.append(_validate_drug_field(key, payload[key]))
    try:
        with _connect() as conn:
            cur = conn.execute(
                f"INSERT INTO amjilsim_drugs ({', '.join(cols)}) "
                f"VALUES ({', '.join('?' for _ in cols)})",
                vals,
            )
            conn.commit()
            drug_id = cur.lastrowid
    except sqlite3.IntegrityError as e:
        raise ValueError(f"무결성 제약 위반 (brand_kr+ingredient_inn 중복 등): {e}")
    return {"drug_id": drug_id}


def update_drug(drug_id: int, payload: dict) -> dict:
    if not isinstance(payload, dict) or not payload:
        raise ValueError("수정할 필드 없음")
    sets, vals = [], []
    for key, col in _DRUG_FIELDS.items():
        if key in payload:
            if key == "brand_kr" and not (payload[key] or "").strip():
                raise ValueError("brand_kr 는 비울 수 없음")
            sets.append(f"{col} = ?")
            vals.append(_validate_drug_field(key, payload[key]))
    if not sets:
        raise ValueError(f"허용 필드 없음. 허용: {sorted(_DRUG_FIELDS)}")
    vals.append(drug_id)
    try:
        with _connect() as conn:
            cur = conn.execute(
                f"UPDATE amjilsim_drugs SET {', '.join(sets)} WHERE drug_id = ?", vals)
            conn.commit()
    except sqlite3.IntegrityError as e:
        raise ValueError(f"무결성 제약 위반: {e}")
    if cur.rowcount == 0:
        raise LookupError(f"약제 없음: {drug_id}")
    return {"drug_id": drug_id, "updated_fields": len(sets)}


def delete_drug(drug_id: int) -> dict:
    with _connect() as conn:
        n_events = conn.execute(
            "DELETE FROM amjilsim_drug_queue_status WHERE drug_id = ?", (drug_id,)
        ).rowcount
        n = conn.execute(
            "DELETE FROM amjilsim_drugs WHERE drug_id = ?", (drug_id,)).rowcount
        conn.commit()
    if n == 0:
        raise LookupError(f"약제 없음: {drug_id}")
    return {"drug_id": drug_id, "deleted_events": n_events}


# ── Admin CRUD: queue events ────────────────────────────────────────────────

def add_event(drug_id: int, payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("JSON body 필요")
    committee_api = payload.get("committee")
    committee_db = COMMITTEE_API_TO_DB.get(committee_api)
    if not committee_db:
        raise ValueError("committee 는 'cancer' | 'evaluation' 중 하나여야 함")
    state = payload.get("state")
    if state not in QUEUE_STATES:
        raise ValueError(f"state 는 {QUEUE_STATES} 중 하나여야 함")
    queue_entry_date = _valid_date(payload.get("queue_entry_date"), "queue_entry_date")
    attempt = payload.get("attempt", 1)
    if not isinstance(attempt, int) or attempt < 1:
        raise ValueError("attempt 는 1 이상 정수여야 함")
    session_id = payload.get("session_id")

    with _connect() as conn:
        drug = conn.execute(
            "SELECT drug_id FROM amjilsim_drugs WHERE drug_id = ?", (drug_id,)
        ).fetchone()
        if not drug:
            raise LookupError(f"약제 없음: {drug_id}")
        if session_id is not None:
            srow = conn.execute(
                "SELECT committee_type FROM amjilsim_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if not srow:
                raise ValueError(f"session_id 없음: {session_id}")
            if srow["committee_type"] != committee_db:
                raise ValueError(
                    f"세션 위원회 불일치: session {session_id} 는 "
                    f"{COMMITTEE_DB_TO_API[srow['committee_type']]}")
        cur = conn.execute(
            """INSERT INTO amjilsim_drug_queue_status
               (drug_id, session_id, queue_state, queue_entry_date,
                n_th_attempt, evidence_url, committee_type)
               VALUES (?,?,?,?,?,?,?)""",
            (drug_id, session_id, state, queue_entry_date, attempt,
             payload.get("evidence_url"), committee_db),
        )
        conn.commit()
        event_id = cur.lastrowid
    return {"event_id": event_id, "drug_id": drug_id}


def delete_event(event_id: int) -> dict:
    with _connect() as conn:
        n = conn.execute(
            "DELETE FROM amjilsim_drug_queue_status WHERE id = ?", (event_id,)
        ).rowcount
        conn.commit()
    if n == 0:
        raise LookupError(f"이벤트 없음: {event_id}")
    return {"event_id": event_id}


# ── Admin CRUD: sessions ────────────────────────────────────────────────────

_SESSION_PATCH_FIELDS = {"status", "ordinal_official", "note", "official_minutes_url"}


def update_session(session_id: int, payload: dict) -> dict:
    if not isinstance(payload, dict) or not payload:
        raise ValueError("수정할 필드 없음")
    sets, vals = [], []
    for key in _SESSION_PATCH_FIELDS:
        if key in payload:
            value = payload[key]
            if key == "ordinal_official" and value is not None \
                    and not isinstance(value, int):
                raise ValueError("ordinal_official 은 정수 또는 null")
            sets.append(f"{key} = ?")
            vals.append(value)
    if not sets:
        raise ValueError(f"허용 필드 없음. 허용: {sorted(_SESSION_PATCH_FIELDS)}")
    vals.append(session_id)
    with _connect() as conn:
        cur = conn.execute(
            f"UPDATE amjilsim_sessions SET {', '.join(sets)} WHERE session_id = ?",
            vals)
        conn.commit()
    if cur.rowcount == 0:
        raise LookupError(f"세션 없음: {session_id}")
    return {"session_id": session_id, "updated_fields": len(sets)}


def create_session(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("JSON body 필요")
    committee_db = COMMITTEE_API_TO_DB.get(payload.get("committee"))
    if not committee_db:
        raise ValueError("committee 는 'cancer' | 'evaluation' 중 하나여야 함")
    year = payload.get("year")
    ordinal_assumed = payload.get("ordinal_assumed")
    if not isinstance(year, int) or not isinstance(ordinal_assumed, int):
        raise ValueError("year / ordinal_assumed 정수 필수")
    session_date = _valid_date(payload.get("session_date"), "session_date")
    if not session_date:
        raise ValueError("session_date (YYYY-MM-DD) 필수")
    try:
        with _connect() as conn:
            cur = conn.execute(
                """INSERT INTO amjilsim_sessions
                   (year, ordinal_assumed, session_date, note, committee_type)
                   VALUES (?,?,?,?,?)""",
                (year, ordinal_assumed, session_date, payload.get("note"),
                 committee_db),
            )
            conn.commit()
            session_id = cur.lastrowid
    except sqlite3.IntegrityError as e:
        raise ValueError(f"무결성 제약 위반 (session_date 중복 등): {e}")
    return {"session_id": session_id}
