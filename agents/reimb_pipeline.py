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
        # 등재 차수 테이블 (추가 전용). 읽기 전용 DB(테스트 가드·마운트)에서는 실패해도 import 를 막지 않는다 —
        # 조회 경로는 테이블이 없으면 약제 행 = 가상 차수로 동작.
        try:
            ensure_round_schema(conn)
            conn.commit()
        except sqlite3.OperationalError:
            conn.rollback()



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


STALE_DAYS = 60  # 카드 '최근 갱신' 이 이보다 오래되면 경고 (공단 자료로 최근 확인된 건 제외)

_DECISIVE_STATES = ("APPROVED", "REJECTED_REQUEUE", "WITHDRAWN", "QUEUE_PROCESSED")


def _event_date(q) -> str:
    """큐 이벤트의 '사건일' — 연결 차수 회의일 → 큐 등재일 → 관측일(observed_at). 없으면 ''."""
    return (_row_get(q, "session_date") or _row_get(q, "queue_entry_date")
            or (_row_get(q, "observed_at") or "")[:10] or "")


def _latest_event_key(q) -> tuple:
    """'최근 상태' 정렬 키 — 사건일이 가장 늦은 이벤트가 최신.

    tie-break (같은 사건일): ① 늦게 관측(observed_at)된 기록 ② 결과(통과/재심의/철회/심의완료) > 대기 ③ id.
    이전엔 observed_at(수집 시각)만으로 골라, 나중에 백필된 과거 차수(웰리렉 2025-12-10 재심의, 2026-06-12 수집)가
    더 최근 사건(2026-03-20 암질심 대기, 2026-05-28 수집)을 덮었다.
    """
    return (_event_date(q), _row_get(q, "observed_at") or "",
            1 if _row_get(q, "queue_state") in _DECISIVE_STATES else 0, _row_get(q, "id") or 0)


def latest_queue_row(queue_rows: list, committee: Optional[str] = None):
    """(위원회별) 가장 최근 큐 이벤트. 없으면 None."""
    rows = [q for q in queue_rows if committee is None or q["committee_type"] == committee]
    return max(rows, key=_latest_event_key) if rows else None


_BRD_RE = re.compile(r"\s*(?:HIRA\s*)?(?:5034계열\s*)?brdBltNo\s*=\s*\d+", re.I)
_CYCLE_DATE_RE = re.compile(r"(?<!년 )(?<![년\d])(\d{1,2})차\s*(암질심|약평위)\s*\((\d{4})-")
_UNSET_RE = re.compile(r"(?<!급여기준 )(?<!급여기준)미설정")


def humanize_issue(text: Optional[str]) -> Optional[str]:
    """쟁점/근거 문구의 내부 키를 읽을 수 있는 문장으로.

    - 'brdBltNo=11684' (심평원 게시판 내부 번호) 제거
    - '9차 암질심(2025-12-10' → '2025년 9차 암질심(2025-12-10' (연도 없는 차수는 해마다 반복되어 모호)
    - '미설정' → '급여기준 미설정' (무엇이 미설정인지 명시 — 암질심이 급여기준을 설정하지 않음 = 불인정)
    """
    if not text:
        return text
    s = _BRD_RE.sub("", str(text))
    s = re.sub(r"\(\s*\)", "", s)
    s = re.sub(r"\(\s+", "(", s)
    s = re.sub(r"\s+\)", ")", s)
    s = _CYCLE_DATE_RE.sub(lambda m: f"{m.group(3)}년 {m.group(1)}차 {m.group(2)}({m.group(3)}-", s)
    s = _UNSET_RE.sub("급여기준 미설정", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    s = re.sub(r"^[—\-·\s]+", "", s)
    return s or None


def _evidence_fields(raw: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """evidence_url 컬럼 → (링크 URL | None, 사람이 읽는 근거 문구 | None). http(s) 가 아니면 링크로 쓰지 않는다."""
    if not raw:
        return None, None
    s = str(raw).strip()
    if re.match(r"^https?://", s, re.I):
        return s, None
    m = re.search(r"brdBltNo\s*=\s*(\d+)", s, re.I)
    text = humanize_issue(s)
    if m:
        text = f"심평원 공개 결과 #{m.group(1)}" + (f" — {text}" if text and text.upper() != "HIRA" else "")
    return None, text


def _days_between(a: Optional[str], b: str) -> Optional[int]:
    try:
        return (date.fromisoformat(b[:10]) - date.fromisoformat(str(a)[:10])).days if a else None
    except ValueError:
        return None


def _drug_stage(drug: sqlite3.Row, queue_rows: list[sqlite3.Row]) -> str:
    """파이프라인 단계: nhis > evaluation > cancer (실데이터 기반).

    협상 타결(AGREED)은 약평위를 거친 뒤에만 가능하므로 약평위 통과일이 비어 있어도 nhis(→ status completed).
    (자카비: 암질심 통과 + AGREED 인데 약평위 통과일 미확인 → '약평위' 단계로 보이던 문제)
    """
    if drug["yakpyungwi_pass_date"] or drug["negotiation_status"] == "AGREED":
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

    latest = latest_queue_row(queue_rows, committee)
    if latest is not None:
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
        cycle = _session_cycle(q) if q["session_date"] else None
        year = _row_get(q, "session_year") if q["session_date"] else None
        evidence_url, evidence_text = _evidence_fields(q["evidence_url"])
        items.append({
            "id": q["id"],
            "date": d,
            "committee": COMMITTEE_KR.get(q["committee_type"], q["committee_type"]),
            "committeeType": q["committee_type"],
            "state": q["queue_state"],
            "stateLabel": STATE_LABEL_KR.get(q["queue_state"], q["queue_state"]),
            "sessionId": q["session_id"],
            "sessionDate": q["session_date"],
            "cycle": cycle,
            "year": year,
            # 연도 포함 차수 라벨 — '9차' 는 해마다 반복되어 모호 ('2025년 9차')
            "cycleLabel": (f"{year}년 {cycle}차" if year and cycle is not None
                           else f"{cycle}차" if cycle is not None else None),
            "attempt": q["n_th_attempt"],
            "evidenceUrl": evidence_url,     # http(s) 링크일 때만
            "evidenceText": evidence_text,   # 링크가 아닌 근거 메모(내부 키 제거)
            "observedAt": q["observed_at"],
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
                    "year": None,
                    "cycleLabel": None,
                    "attempt": None,
                    "evidenceUrl": None,
                    "evidenceText": None,
                    "synthetic": True,  # 통과일 컬럼 기반 (큐 이벤트 미수집 차수)
                })
    items.sort(key=lambda x: (x["date"] or "9999-99-99"))
    return items


def _ym_to_date(ym: Optional[str]) -> Optional[str]:
    return f"{ym[:7]}-01" if ym and re.match(r"^\d{4}-\d{2}", ym) else None


def last_result(drug, queue_rows: list) -> Optional[dict]:
    """'마지막 결과' — 위원회 이벤트(큐·통과일 합성)와 공단 협상 결과 중 사건일이 가장 늦은 것.

    '다음 단계'(stage)와 다른 축이다: 자카비처럼 단계=약평위인데 마지막 결과=암질심 통과는 모순이 아니라
    '암질심을 통과했고 다음은 약평위' 라는 뜻이다.
    """
    cands: list[dict] = []
    q = latest_queue_row(queue_rows)
    if q is not None:
        cycle = _session_cycle(q) if q["session_date"] else None
        year = _row_get(q, "session_year") if q["session_date"] else None
        cands.append({
            "kind": "committee", "committee": COMMITTEE_DB_TO_API.get(q["committee_type"]),
            "committeeLabel": COMMITTEE_KR.get(q["committee_type"], q["committee_type"]),
            "state": q["queue_state"], "label": STATE_LABEL_KR.get(q["queue_state"], q["queue_state"]),
            "date": _event_date(q) or None, "sessionLinked": q["session_id"] is not None,
            "cycleLabel": (f"{year}년 {cycle}차" if year and cycle is not None else None),
            "source": "queue", "_key": (_event_date(q), 1)})
    for committee_type, col in (("AMJILSIM", "amjilsim_pass_date"), ("YAKPYUNGWI", "yakpyungwi_pass_date")):
        d = _row_get(drug, col)
        if d:
            cands.append({
                "kind": "committee", "committee": COMMITTEE_DB_TO_API[committee_type],
                "committeeLabel": COMMITTEE_KR[committee_type], "state": "APPROVED", "label": "통과",
                "date": d, "sessionLinked": False, "cycleLabel": None, "source": "pass_date",
                "_key": (d, 0)})  # 같은 날이면 큐 이벤트(차수 연결 정보 보유)가 우선
    neg = _row_get(drug, "negotiation_status")
    if neg in ("AGREED", "REJECTED", "IN_PROGRESS", "STALLED"):
        ym = _row_get(drug, "negotiation_complete_date") if neg in ("AGREED", "REJECTED") else None
        ym = ym or _row_get(drug, "nhis_registered_ym")
        d = _ym_to_date(ym)
        label = {"AGREED": "협상 타결", "REJECTED": "협상 결렬",
                 "IN_PROGRESS": "협상 진행 중", "STALLED": "협상 교착"}[neg]
        if d or neg in ("AGREED", "REJECTED"):
            cands.append({
                "kind": "negotiation", "committee": None, "committeeLabel": "건보공단",
                "state": neg, "label": label, "date": d, "month": ym[:7] if ym else None,
                "sessionLinked": False, "cycleLabel": None,
                "source": _row_get(drug, "negotiation_date_source") or "manual",
                # 결과 확정(타결/결렬)은 같은 달 위원회 이벤트보다 뒤 — 협상은 약평위 이후 단계
                "_key": ((d or "")[:7] + "-99" if neg in ("AGREED", "REJECTED") else (d or ""), 2)})
    if not cands:
        return None
    best = max(cands, key=lambda c: c["_key"])
    best.pop("_key", None)
    return best


def next_step_label(stage: str, status: str) -> str:
    """'다음 단계' — 약제가 지금 기다리는 절차 (마지막 결과와 구분)."""
    if status == "completed":
        return "협상 완료"
    if stage == "nhis":
        return "건보공단 협상"
    if stage == "evaluation":
        return "약평위 심의" + (" (상정예정)" if status == "scheduled" else "")
    return "암질심 심의" + (" (상정예정)" if status == "scheduled" else "")


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


def _expected_session(conn: sqlite3.Connection, session_id) -> Optional[sqlite3.Row]:
    if not session_id:
        return None
    return conn.execute(
        "SELECT session_date, committee_type FROM amjilsim_sessions WHERE session_id = ?",
        (session_id,)).fetchone()


ONCOLOGY_EFFICACY_GROUP = "항악성종양제"


def oncology_track(drug, *, expected_committee: Optional[str] = None,
                   queue_committees: tuple[str, ...] = ()) -> Optional[bool]:
    """약제가 항암(암질심 선행) 트랙인가 — True / False(비항암 확정) / None(미상).

    access_insight 의 판정을 재사용한다 (backfill_oncology 캐스케이드 + aggregate 의 'unknown' 트랙 증거 규칙):
      ① is_oncology 컬럼(백필 결과)이 채워져 있으면 그것이 우선
      ② 실측 증거 — 암질심 통과일, 암질심 차수 예정(expected_session)·암질심 큐 이력 → 항암
      ③ ATC L01/L02 · efficacy_group '항악성종양제' · indication 암 키워드 · KNOWN_ONCOLOGY_INN → 항암
      ④ efficacy_group 이 다른 효능군(공단 자료)이면 비항암 확정
      ⑤ 그 외 미상 — 호출자는 암질심 예상을 달지 않는다 (날조 금지)
    drug 는 sqlite3.Row 또는 dict (컬럼 부재 허용).
    """
    from agents.access_insight.backfill import _indication_is_cancer, _inn_is_oncology

    flag = _row_get(drug, "is_oncology")
    if flag is not None and flag != "":
        return bool(int(flag))
    if _row_get(drug, "amjilsim_pass_date"):
        return True
    if expected_committee == "AMJILSIM" or "AMJILSIM" in queue_committees:
        return True
    atc = (_row_get(drug, "atc") or "").upper()
    eff = (_row_get(drug, "efficacy_group") or "").strip()
    if atc.startswith("L01") or atc.startswith("L02") or eff == ONCOLOGY_EFFICACY_GROUP:
        return True
    if _indication_is_cancer(_row_get(drug, "indication")) or _inn_is_oncology(_row_get(drug, "ingredient_inn")):
        return True
    if eff:
        return False
    return None


def _expected_status(expected_date: Optional[str], today: str) -> Optional[str]:
    """예상일의 성격 — 'forecast'(미래 차수) / 'overdue'(차수가 지났는데 결과 미반영). 날짜 없으면 None."""
    if not expected_date:
        return None
    return "overdue" if expected_date < today else "forecast"


def _timeline(drug: sqlite3.Row, conn: Optional[sqlite3.Connection] = None,
              today: Optional[str] = None,
              queue_rows: Optional[list[sqlite3.Row]] = None) -> list[dict]:
    """단계 타임라인 — 실측일은 date, 미도래 단계는 위원회 일정 기반 '예상일'(expectedDate).

    날조 금지 원칙 유지: 실제 통과일(date)과 일정 기반 예상일(expectedDate)을
    분리된 필드로 명시. 예상일 출처 = expected_session_id 차수 일정 / 다음 차수 일정.

    - expectedStatus: 'forecast'(미래) / 'overdue'(예상 차수가 이미 지났는데 결과가 반영되지 않음 — 과거 날짜를
      예보처럼 보여주지 않는다. expected_session_id 가 지난 차수를 가리키는 약제 6종, 2026-10-06 감사).
    - 암질심 예상은 **항암 트랙이고 아직 약평위를 통과하지 않은** 약제에만 붙는다 (oncology_track). 비항암 확정이면
      암질심 단계는 'not_applicable', 미상이면 예상 없이 'upcoming' — 당뇨·소화성궤양·PAH 약제와 이미 약평위를
      통과한 약제에 "암질심 통과 예상" 이 붙던 결함.
    """
    today = today or date.today().isoformat()
    amj_pass = drug["amjilsim_pass_date"]
    yak_pass = drug["yakpyungwi_pass_date"]

    # 미도래 위원회 단계의 예상일: expected_session_id 일정 → 없으면 다음 차수 일정
    exp_amj = exp_yak = None
    exp_committee = None
    if conn is not None:
        exp_row = _expected_session(conn, drug["expected_session_id"])
        exp_date = exp_row["session_date"] if exp_row else None
        exp_committee = exp_row["committee_type"] if exp_row else None
        if queue_rows is None:
            queue_rows = conn.execute(_QUEUE_JOIN_SQL, (drug["drug_id"],)).fetchall()
        onco = oncology_track(drug, expected_committee=exp_committee,
                              queue_committees=tuple(q["committee_type"] for q in queue_rows))
        nxt = _next_session_ids(conn, today)
        if not amj_pass and not yak_pass and onco is True:
            exp_amj = (exp_date if exp_committee == "AMJILSIM" else None) \
                or _expected_session_date(conn, nxt.get("AMJILSIM"))
        if amj_pass and not yak_pass:
            exp_yak = (exp_date if exp_committee == "YAKPYUNGWI" else None) \
                or _expected_session_date(conn, nxt.get("YAKPYUNGWI"))
    else:
        onco = oncology_track(drug)

    amj_status = ("done" if amj_pass else "expected" if exp_amj
                  else "not_applicable" if onco is False else "upcoming")
    out = [
        {"phase": "급여 신청(접수)", "date": drug["submitted_date"],
         "status": "done" if drug["submitted_date"] else "upcoming"},
        {"phase": "암질심 통과", "date": amj_pass,
         "expectedDate": exp_amj if not amj_pass else None,
         "expectedStatus": _expected_status(exp_amj, today) if not amj_pass else None,
         "status": amj_status},
        {"phase": "약평위 통과", "date": yak_pass,
         "expectedDate": exp_yak if not yak_pass else None,
         "expectedStatus": _expected_status(exp_yak, today) if not yak_pass else None,
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
    SELECT q.*, s.session_date, s.ordinal_official, s.ordinal_assumed, s.year AS session_year
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


def _nhis_list_type_for_drug(conn: sqlite3.Connection, drug_id: int) -> Optional[str]:
    """매칭된 NHIS 공개자료의 신규/확대 구분. 보드 카드는 진행 중이므로
    in_progress(완료연월 빈값) 행을 우선, 없으면 최신 등록 행 기준. 완료 행으로 대체된(superseded) 진행중 행은 제외."""
    if not _nhis_table_exists(conn):
        return None
    superseded = superseded_nhis_ids(conn)
    rows = conn.execute(
        "SELECT id, list_type FROM nhis_negotiations WHERE drug_id = ? "
        "ORDER BY (completed_ym IS NULL OR completed_ym='') DESC, "
        "registered_ym DESC", (drug_id,)).fetchall()
    for r in rows:
        if r["id"] not in superseded:
            return r["list_type"]
    return None


def _nhis_drug_dates(conn: sqlite3.Connection, drug_id: int,
                     round_id: Optional[int] = None) -> tuple[Optional[str], Optional[str]]:
    """(공단 자료로 상태가 바뀐 최근 시각 = max first_seen_at, 공단 목록에서 최근 확인 = max fetched_at)."""
    if not _nhis_table_exists(conn):
        return None, None
    if round_id is not None and _nhis_has_round_col(conn):  # 차수 카드: 그 차수에 연결된 공단 행만
        row = conn.execute(
            "SELECT MAX(first_seen_at) AS fs, MAX(fetched_at) AS fa FROM nhis_negotiations WHERE round_id = ?",
            (round_id,)).fetchone()
        return (row["fs"], row["fa"]) if row else (None, None)
    row = conn.execute(
        "SELECT MAX(first_seen_at) AS fs, MAX(fetched_at) AS fa FROM nhis_negotiations WHERE drug_id = ?",
        (drug_id,)).fetchone()
    return (row["fs"], row["fa"]) if row else (None, None)


def _freshness(drug_id: int, queue_rows: list, conn: sqlite3.Connection, today: str,
               round_id: Optional[int] = None) -> dict:
    """카드 날짜 라벨 — '최근 갱신'(상태 기록이 마지막으로 바뀐 날)과 '공단 자료 확인'(NHIS 목록 재확인)을 분리."""
    observed = [q["observed_at"] for q in queue_rows if q["observed_at"]]
    nhis_seen, nhis_checked = _nhis_drug_dates(conn, drug_id, round_id)
    updated = max([x for x in observed + [nhis_seen] if x], default=None)
    stale_days = _days_between(updated, today)
    checked_days = _days_between(nhis_checked, today)
    is_stale = bool(stale_days is not None and stale_days > STALE_DAYS
                    and not (checked_days is not None and checked_days <= STALE_DAYS))
    return {"updatedDate": updated, "nhisCheckedAt": nhis_checked,
            "staleDays": stale_days, "isStale": is_stale}


def _pipeline_drug_dict(drug: sqlite3.Row, queue_rows: list[sqlite3.Row],
                        today: str, conn: sqlite3.Connection,
                        next_session_ids: dict[str, Optional[int]],
                        round_meta: Optional[dict] = None) -> tuple[str, dict]:
    """카드 1장. round_meta 가 있으면 drug 는 _round_view(차수 필드로 덮어쓴 약제 행), queue_rows 는 그 차수 이벤트."""
    round_meta = round_meta or _virtual_round_meta(drug)
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
    timeline = _timeline(drug, conn, today, queue_rows=queue_rows)
    # 카드 수준 예상 상태: 타임라인의 미도래 위원회 단계 중 예상일이 있는 것의 성격 ('overdue' 가 하나라도 있으면 overdue)
    exp_statuses = [t.get("expectedStatus") for t in timeline if t.get("expectedStatus")]
    expected_status = ("overdue" if "overdue" in exp_statuses else exp_statuses[0] if exp_statuses else None)
    fresh = _freshness(drug["drug_id"], queue_rows, conn, today, round_meta.get("roundId"))
    raw_issues = _key_issues(drug)
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
        # 'forecast' | 'overdue' | None — overdue 는 예상 차수가 지났는데 결과가 반영되지 않은 상태 (예보가 아님)
        "expectedStatus": expected_status,
        "submittedDate": drug["submitted_date"],
        "amjilsimPassDate": drug["amjilsim_pass_date"],
        "yakpyungwiPassDate": drug["yakpyungwi_pass_date"],
        "negotiationStatus": drug["negotiation_status"],
        # NHIS 공식 협상자료 (건강보험공단 공개자료 매칭 시 채워짐)
        "negotiationCompleteDate": _row_get(drug, "negotiation_complete_date"),
        "negotiationDateSource": _row_get(drug, "negotiation_date_source"),
        "nhisRegisteredYm": _row_get(drug, "nhis_registered_ym"),
        "efficacyGroup": _row_get(drug, "efficacy_group"),
        # 신규/확대 구분 (차수 유형 → 매칭된 NHIS 공개자료 기준 — 보드 필터칩용)
        "listType": (round_meta.get("listType") if round_meta.get("roundId") is not None
                     else _nhis_list_type_for_drug(conn, drug["drug_id"])),
        "notes": drug["notes"],
        # 핵심 쟁점 — 내부 키(brdBltNo)·연도 없는 차수를 사람이 읽는 문장으로 (원문은 keyIssuesRaw)
        "keyIssues": [x for x in (humanize_issue(i) for i in raw_issues) if x],
        "keyIssuesRaw": raw_issues,
        # 최근 갱신 = 이 약제 상태 기록(큐 관측·공단 자료 변화)이 마지막으로 바뀐 날. 60일 초과 시 isStale.
        "updatedDate": fresh["updatedDate"],
        "nhisCheckedAt": fresh["nhisCheckedAt"],
        "staleDays": fresh["staleDays"],
        "isStale": fresh["isStale"] and status != "completed",  # 협상 완료 건은 갱신 대상 아님
        "nextStep": next_step_label(stage, status),
        "lastResult": last_result(drug, queue_rows),
        "history": _history(queue_rows, drug),
        "timeline": timeline,
    }
    # 등재 차수 — 브랜드(drugId) 아래 차수별 카드. 차수 미분할 브랜드는 roundId=None(약제 행 = 가상 차수)
    item.update({k: v for k, v in round_meta.items() if k != "listType"})
    item["drugId"] = drug["drug_id"]
    item["cardKey"] = f"{drug['drug_id']}:{round_meta.get('roundId') or 0}"
    return stage, item


def _virtual_round_meta(drug) -> dict:
    """차수가 없는 브랜드 — 약제 행 자체가 차수 (하위 호환)."""
    return {"roundId": None, "roundLabel": round_label_for(drug), "roundSeq": 1, "roundCount": 1,
            "roundSource": None, "roundNeedsReview": False, "comboKey": None, "comboPartners": [],
            "reimbStartDate": None, "indicationScope": _row_get(drug, "indication")}


def _round_view(drug, rnd, is_primary: bool) -> dict:
    """약제 행 + 차수 필드 덮어쓰기 — 기존 판정 함수(_drug_stage/_drug_status/_timeline/last_result)를 그대로 재사용.
    상정 예정 차수·핵심 쟁점은 대표(진행 중) 차수에만 남긴다."""
    v = dict(drug)
    for col in ("amjilsim_pass_date", "yakpyungwi_pass_date", "submitted_date", "negotiation_status",
                "negotiation_complete_date", "negotiation_date_source", "nhis_registered_ym"):
        v[col] = rnd[col]
    v["indication"] = rnd["indication_scope"]
    v["listing_type"] = rnd["list_type"]
    if rnd["notes"]:
        v["notes"] = rnd["notes"]
    if not is_primary:
        v["expected_session_id"] = None
        v["key_issues"] = None
    return v


def _round_meta(rnd, seq: int, count: int, combo_index: dict) -> dict:
    partners = [p for p in combo_index.get(rnd["combo_key"], []) if p["drugId"] != rnd["drug_id"]] \
        if rnd["combo_key"] else []
    return {"roundId": rnd["round_id"], "roundLabel": rnd["round_label"], "roundSeq": seq, "roundCount": count,
            "roundSource": rnd["source"], "roundNeedsReview": bool(rnd["needs_review"]),
            "comboKey": rnd["combo_key"], "comboPartners": partners,
            "reimbStartDate": rnd["reimb_start_date"], "indicationScope": rnd["indication_scope"],
            "listType": rnd["list_type"]}


def _combo_index(conn: sqlite3.Connection) -> dict:
    out: dict[str, list[dict]] = {}
    if not rounds_enabled(conn):
        return out
    for r in conn.execute("SELECT r.round_id, r.drug_id, r.round_label, r.combo_key, d.brand_kr "
                          "FROM reimb_listing_rounds r LEFT JOIN amjilsim_drugs d ON d.drug_id = r.drug_id "
                          "WHERE r.combo_key IS NOT NULL AND r.combo_key != ''"):
        out.setdefault(r["combo_key"], []).append({"drugId": r["drug_id"], "name": r["brand_kr"],
                                                   "roundId": r["round_id"], "roundLabel": r["round_label"]})
    return out


def drug_round_cards(conn: sqlite3.Connection, drug, today: str, next_ids: dict,
                     rounds: Optional[list] = None, links: Optional[dict] = None,
                     combo_index: Optional[dict] = None) -> list[dict]:
    """브랜드 1개 → 차수별 카드 [{round, stage, item, queue_rows}]. 차수 없으면 가상 차수 1장(기존과 동일).

    차수 이벤트 = 명시 링크(다른 브랜드의 병용 공유 이벤트 포함) + 아직 링크 없는 자기 이벤트는 규칙(pick_round_for_event)
    으로 조회 시점 배정(쓰기 없음), 그래도 못 정하면 대표 차수.
    """
    did = drug["drug_id"]
    own_rows = conn.execute(_QUEUE_JOIN_SQL, (did,)).fetchall()
    rounds = list_rounds(conn, did) if rounds is None else rounds
    if not rounds:
        stage, item = _pipeline_drug_dict(drug, own_rows, today, conn, next_ids)
        return [{"round": None, "stage": stage, "item": item, "queue_rows": own_rows}]
    links = _event_links(conn) if links is None else links
    combo_index = _combo_index(conn) if combo_index is None else combo_index
    prim = primary_round(rounds)
    per_round: dict[int, list] = {r["round_id"]: _round_queue_rows(conn, r["round_id"]) for r in rounds}
    for q in own_rows:
        if any(rd == did for _, rd, _ in links.get(q["id"], [])):
            continue
        target = pick_round_for_event(rounds, _event_date(q) or None, q["committee_type"]) or prim
        per_round[target["round_id"]].append(q)
    out = []
    for i, r in enumerate(rounds, 1):
        view = _round_view(drug, r, r["round_id"] == prim["round_id"])
        qrows = per_round[r["round_id"]]
        stage, item = _pipeline_drug_dict(view, qrows, today, conn, next_ids,
                                          _round_meta(r, i, len(rounds), combo_index))
        out.append({"round": r, "stage": stage, "item": item, "queue_rows": qrows,
                    "is_primary": r["round_id"] == prim["round_id"]})
    return out


def get_pipeline(include_completed: bool = False) -> dict:
    """파이프라인 보드 3단계(cancer/evaluation/nhis).

    include_completed=False (기본): 공단 협상 완료(negotiation_status='AGREED') 약제 제외 —
    이미 등재 마무리된 건은 '진행 중' 보드에서 빼고 별도 completed 카운트로만 노출.

    등재 차수가 분할된 브랜드는 차수마다 카드 1장(roundLabel). 완료 제외도 차수 단위 — 키트루다 2025 차수가 끝났어도
    2026 차수가 진행 중이면 그 카드만 보인다. completedExcluded = 제외된 카드(차수) 수.
    """
    today = date.today().isoformat()
    stages: dict[str, list[dict]] = {"cancer": [], "evaluation": [], "nhis": []}
    completed_count = 0
    with _connect() as conn:
        next_ids = _next_session_ids(conn, today)
        drugs = conn.execute(
            "SELECT * FROM amjilsim_drugs ORDER BY brand_kr"
        ).fetchall()
        all_rounds = list_rounds(conn)
        rounds_by_drug: dict[int, list] = {}
        for r in all_rounds:
            rounds_by_drug.setdefault(r["drug_id"], []).append(r)
        links = _event_links(conn)
        combo_index = _combo_index(conn)
        for drug in drugs:
            rounds = rounds_by_drug.get(drug["drug_id"], [])
            if not rounds:  # 가상 차수 — 기존 동작 그대로
                if not include_completed and drug["negotiation_status"] == "AGREED":
                    completed_count += 1
                    continue
            elif not include_completed:
                n_done = sum(1 for r in rounds if r["negotiation_status"] == "AGREED")
                completed_count += n_done
                if n_done == len(rounds):
                    continue
            for card in drug_round_cards(conn, drug, today, next_ids, rounds, links, combo_index):
                rnd = card["round"]
                if rnd is not None and not include_completed and rnd["negotiation_status"] == "AGREED":
                    continue
                stages[card["stage"]].append(card["item"])
    return {
        "stages": [
            {"id": sid, "count": len(items), "drugs": items}
            for sid, items in stages.items()
        ],
        "completedExcluded": completed_count,
    }


def _nhis_row_dict(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"],
        "listType": r["list_type"],
        "productName": r["product_name"],
        "manufacturer": r["manufacturer"],
        "efficacyGroup": r["efficacy_group"],
        "registeredYm": r["registered_ym"],
        "result": r["result"],
        "completedYm": r["completed_ym"],
        "inProgress": r["completed_ym"] in (None, ""),
        "matched": r["drug_id"] is not None,
        "drugId": r["drug_id"],
        "sourceUrl": r["source_url"],
        "fetchedAt": r["fetched_at"],
        # 매칭된 추적 약제 (병용 등재 등 제품명만으로 알 수 없는 적응증 맥락)
        "drugName": _row_get(r, "drug_name"),
        "drugIndication": _row_get(r, "drug_indication"),
        # 등재 차수 (nhis_negotiations.round_id 연결 시) — '키트루다 · 2026 확대 · 파드셉 병용 요로상피암 1차'
        "roundId": _row_get(r, "round_id"),
        "roundLabel": _row_get(r, "round_label"),
        "roundLinkSource": _row_get(r, "round_link_source"),
    }


def _nhis_table_exists(conn: sqlite3.Connection) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='nhis_negotiations'"
    ).fetchone() is not None


def _nhis_product_key(name: Optional[str]) -> str:
    """제품명 표기 변형 흡수 키 — '파드셉주 20밀리그램,30밀리그램_(엔포투맙베도틴)' == '...밀리그램(엔포투맙베도틴)'."""
    s = (name or "").split("(")[0].split("_")[0]
    return re.sub(r"[\s,·/]", "", s).lower()


def superseded_nhis_ids(conn: sqlite3.Connection) -> set[int]:
    """공단 목록에서 완료 행으로 대체된 '협상 중' 아카이브 행 id.

    nhis_negotiations 는 content_hash(결과·완료연월 포함) 멱등 INSERT + 삭제 금지 아카이브라, 협상이 끝나면
    '완료' 행이 새로 들어오고 예전 '협상 중' 행(completed_ym NULL)이 그대로 남는다. 그래서 in_progress 조회가
    이미 끝난 협상까지 세었다 (2026-10-17 프로덕션: in_progress 56건 중 41건 — 키트루다 확대 2026-05, 파드셉 신규
    2026-05 포함). 판정(삭제 없이 조회 시점에만):
      - 협상 중 행이고
      - 같은 목록(list_type)·같은 등록연월에 같은 약제(drug_id) 또는 같은 제품 키의 완료 행이 있고
      - 그 협상 중 행이 해당 목록의 최신 수집에서 더 이상 확인되지 않음 (fetched_at 이 목록 최신 수집일보다 1일 넘게 과거)
        — 공단 목록에 아직 '협상 중' 으로 떠 있는 행은 절대 숨기지 않는다.
    """
    if not _nhis_table_exists(conn):
        return set()
    rows = conn.execute(
        "SELECT id, list_type, product_name, registered_ym, completed_ym, drug_id, source_url, fetched_at "
        "FROM nhis_negotiations").fetchall()
    latest_fetch: dict[str, str] = {}
    for r in rows:
        f = (r["fetched_at"] or "")[:10]
        if f > latest_fetch.get(r["source_url"], ""):
            latest_fetch[r["source_url"]] = f
    done_by_drug: set[tuple] = set()
    done_by_key: set[tuple] = set()
    for r in rows:
        if r["completed_ym"]:
            if r["drug_id"] is not None:
                done_by_drug.add((r["list_type"], r["registered_ym"], r["drug_id"]))
            done_by_key.add((r["list_type"], r["registered_ym"], _nhis_product_key(r["product_name"])))
    out: set[int] = set()
    for r in rows:
        if r["completed_ym"]:
            continue
        last = latest_fetch.get(r["source_url"])
        gap = _days_between(r["fetched_at"], last) if last and r["fetched_at"] else None
        if gap is None or gap <= 1:
            continue  # 최신 수집에서도 협상 중으로 확인된 행 (또는 판단 불가) — 유지
        if ((r["drug_id"] is not None and (r["list_type"], r["registered_ym"], r["drug_id"]) in done_by_drug)
                or (r["list_type"], r["registered_ym"], _nhis_product_key(r["product_name"])) in done_by_key):
            out.add(r["id"])
    return out


def list_nhis_negotiations(status: str = "completed",
                           list_type: Optional[str] = None,
                           q: Optional[str] = None,
                           limit: int = 1000) -> dict:
    """건강보험공단 약가협상 공개자료 조회 (nhis_negotiations 아카이브 직접).

    status: 'completed'(협상완료연월 채워짐) | 'in_progress'(빈값) | 'all'.
    list_type: '신규' | '확대' | None(전체).
    q: 제품명·제약사명 부분일치(대소문자/공백 무시).
    반환: {items, counts:{completed,in_progress,total}}.
    """
    if status not in ("completed", "in_progress", "all"):
        raise ValueError(f"status invalid: {status}")
    if list_type and list_type not in LISTING_TYPES:
        raise ValueError(f"list_type invalid: {list_type}")

    where, params = [], []
    if status == "completed":
        where.append("n.completed_ym IS NOT NULL AND n.completed_ym != ''")
    elif status == "in_progress":
        where.append("(n.completed_ym IS NULL OR n.completed_ym = '')")
    if list_type:
        where.append("n.list_type = ?")
        params.append(list_type)
    if q:
        # 제품·제약사 + 매칭된 추적 약제의 적응증·메모 — 병용 등재('파드셉+키트루다')는 공단 제품명에
        # 병용 약제가 없어 '키트루다' 로 찾으면 파드셉 행이 안 나왔다 (메모: '펨브로(키트루다) 병용').
        needle = f"%{q.strip()}%"
        where.append("(n.product_name LIKE ? OR n.manufacturer LIKE ? OR d.indication LIKE ? OR d.notes LIKE ?)")
        params.extend([needle, needle, needle, needle])
    clause = (" WHERE " + " AND ".join(where)) if where else ""

    with _connect() as conn:
        if not _nhis_table_exists(conn):
            return {"items": [], "counts": {"completed": 0, "in_progress": 0, "total": 0, "superseded": 0}}
        superseded = superseded_nhis_ids(conn)
        round_join = (" LEFT JOIN reimb_listing_rounds rr ON rr.round_id = n.round_id"
                      if rounds_enabled(conn) and _nhis_has_round_col(conn) else "")
        round_cols = ", rr.round_label AS round_label, rr.indication_scope AS round_scope" if round_join else ""
        if round_join and q:  # 차수 라벨·범위도 검색 — '파드셉' 으로 키트루다 병용 차수 행이 나온다
            clause = clause.replace("OR d.notes LIKE ?)", "OR d.notes LIKE ? OR rr.round_label LIKE ? OR rr.indication_scope LIKE ?)")
            params = params + [needle, needle]
        rows = conn.execute(
            f"SELECT n.*, d.brand_kr AS drug_name, d.indication AS drug_indication{round_cols} "
            f"FROM nhis_negotiations n LEFT JOIN amjilsim_drugs d ON d.drug_id = n.drug_id{round_join}{clause} "
            f"ORDER BY (n.completed_ym IS NULL OR n.completed_ym='') ASC, "
            f"n.completed_ym DESC, n.registered_ym DESC, n.product_name ASC",
            params).fetchall()
        # 완료 행으로 대체된 협상 중 아카이브 행은 목록·카운트에서 제외 (삭제는 하지 않음)
        rows = [r for r in rows if r["id"] not in superseded][:limit]
        n_completed = conn.execute(
            "SELECT COUNT(*) FROM nhis_negotiations "
            "WHERE completed_ym IS NOT NULL AND completed_ym != ''").fetchone()[0]
        n_in_progress = conn.execute(
            "SELECT COUNT(*) FROM nhis_negotiations "
            "WHERE completed_ym IS NULL OR completed_ym = ''").fetchone()[0] - len(superseded)
        counts = {"completed": n_completed, "in_progress": n_in_progress,
                  "total": n_completed + n_in_progress, "superseded": len(superseded)}
    return {"items": [_nhis_row_dict(r) for r in rows], "counts": counts}


def list_nhis_unmatched(limit: int = 1000) -> dict:
    """미매칭 NHIS 행(audit) — amjilsim_drugs 와 매칭 안 된 공개자료. 수동 등록 대상."""
    with _connect() as conn:
        if not _nhis_table_exists(conn):
            return {"items": [], "count": 0}
        superseded = superseded_nhis_ids(conn)
        rows = conn.execute(
            "SELECT * FROM nhis_negotiations WHERE drug_id IS NULL "
            "ORDER BY (completed_ym IS NULL OR completed_ym='') ASC, "
            "completed_ym DESC, product_name ASC").fetchall()
        rows = [r for r in rows if r["id"] not in superseded][:limit]
    return {"items": [_nhis_row_dict(r) for r in rows], "count": len(rows)}


def _linked_report(conn: sqlite3.Connection, meeting: dict) -> Optional[dict]:
    """차수 결과에 연결할 post 리포트 — 규칙은 reimb_reports.report_covers_session 하나(freshness.committee_reports 와 공유).

    위원회 지정 보고서는 회의일 일치 또는 (연도+차수) 일치, combined(committee NULL + committee_scope='combined') 는
    두 위원회 어느 회차든 session_date ±COMBINED_LINK_WINDOW_DAYS. 같은 회차에 둘 다 있으면 위원회 지정 보고서가 먼저.
    """
    from agents.reimb_reports import report_covers_session, report_scope
    has_scope = "committee_scope" in {r[1] for r in conn.execute("PRAGMA table_info(reimb_reports)")}
    scope_col = "committee_scope" if has_scope else "NULL AS committee_scope"
    rows = conn.execute(
        f"""SELECT id, title, summary, highlights_json, committee, {scope_col}, session_date, year, cycle, analyzed
            FROM reimb_reports WHERE report_type = 'post' ORDER BY analyzed DESC, id DESC""").fetchall()
    candidates = [r for r in rows if report_covers_session(dict(r), meeting)]
    if not candidates:
        return None
    row = min(candidates, key=lambda r: (report_scope(dict(r)) == "combined", -(r["analyzed"] or 0), -r["id"]))
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

    results_status, results_note = _results_status(meeting, len(drugs), date.today().isoformat())
    return {"meeting": meeting, "totals": totals, "drugs": drugs, "report": report,
            "resultsStatus": results_status, "resultsNote": results_note}


def _results_status(meeting: dict, n_rows: int, today: str) -> tuple[str, Optional[str]]:
    """회차 결과의 상태 — '아직 적재되지 않음' 을 '심의 약제 0건' 과 구분한다.

      upcoming     : 회차가 아직 오지 않음
      not_ingested : 회차는 지났는데 status 가 SCHEDULED (결과 미반영) — drugs=[] 는 '없음' 이 아니라 '모름'
      available    : 큐 결과 행이 있음
      empty        : COMPLETED 로 닫혔고 행이 0건 — 이때만 '심의 약제 없음' 이라 말할 수 있다
    (2026-10-06 감사: 지난 SCHEDULED 세션 7개가 빈 결과 모달로 '심의 약제 없음' 처럼 보였다.)
    """
    session_date = meeting.get("date") or ""
    if session_date > today:
        return "upcoming", None
    if n_rows > 0:
        return "available", None
    if meeting.get("status") != "COMPLETED":
        return "not_ingested", (f"{session_date} 회차는 지났지만 심의 결과가 아직 미반영(status={meeting.get('status')}) — "
                                "약제 0건이 아니라 결과를 모르는 상태입니다. 런북: reimb_data_sync")
    return "empty", None


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


def _latest_queue_dict(latest) -> Optional[dict]:
    if latest is None:
        return None
    return {"id": latest["id"],
            "committee": COMMITTEE_DB_TO_API.get(latest["committee_type"]),
            "state": latest["queue_state"],
            "session_id": latest["session_id"],
            "session_date": latest["session_date"],
            "queue_entry_date": latest["queue_entry_date"],
            "attempt": latest["n_th_attempt"],
            "evidence_url": latest["evidence_url"],
            "observed_at": latest["observed_at"],
            "event_date": _event_date(latest) or None}


def _admin_nhis_rows(conn: sqlite3.Connection, drug_id: int, superseded: set[int]) -> list[dict]:
    if not _nhis_table_exists(conn):
        return []
    has_round = _nhis_has_round_col(conn) and rounds_enabled(conn)
    sql = ("SELECT n.*" + (", rr.round_label AS round_label" if has_round else "") +
           " FROM nhis_negotiations n" +
           (" LEFT JOIN reimb_listing_rounds rr ON rr.round_id = n.round_id" if has_round else "") +
           " WHERE n.drug_id = ? ORDER BY n.registered_ym, n.id")
    out = []
    for r in conn.execute(sql, (drug_id,)).fetchall():
        d = _nhis_row_dict(r)
        d["superseded"] = r["id"] in superseded
        out.append(d)
    return out


def list_drugs_admin() -> list[dict]:
    """admin 목록 — 브랜드 1행(그룹) + rounds[] (등재 차수별 단계·결과·연결 이벤트/공단 행).

    브랜드 수준 필드(stage/status/next_step/last_result …)는 대표 차수(진행 중 차수 중 최신, 없으면 최신 차수) 기준.
    차수가 없는 브랜드는 rounds = [가상 차수 1개(round_id None)] — 약제 행 그대로.
    """
    today = date.today().isoformat()
    out = []
    with _connect() as conn:
        next_ids = _next_session_ids(conn, today)
        drugs = conn.execute(
            "SELECT * FROM amjilsim_drugs ORDER BY drug_id"
        ).fetchall()
        links = _event_links(conn)
        combo_index = _combo_index(conn)
        superseded = superseded_nhis_ids(conn)
        for drug in drugs:
            own_rows = conn.execute(_QUEUE_JOIN_SQL, (drug["drug_id"],)).fetchall()
            cards = drug_round_cards(conn, drug, today, next_ids, None, links, combo_index)
            prim = next((c for c in cards if c.get("is_primary")), cards[-1])
            item = prim["item"]
            d = dict(drug)
            d["msd_flag"] = bool(d["msd_flag"])
            d["stage"] = prim["stage"]
            d["status"] = item["status"]
            d["next_step"] = item["nextStep"]
            d["last_result"] = item["lastResult"]
            d["updated_date"] = item["updatedDate"]
            d["stale_days"] = item["staleDays"]
            d["is_stale"] = item["isStale"]
            d["key_issues_display"] = item["keyIssues"]
            d["unlinked_events"] = sum(1 for q in own_rows if q["session_id"] is None)
            d["round_label"] = item.get("roundLabel")
            d["round_count"] = len([c for c in cards if c["round"] is not None])
            # 최근 상태 = 사건일(회의일→큐 등재일→관측일)이 가장 늦은 이벤트 (수집 시각 아님) — 브랜드 전체 기준
            d["latest_queue"] = _latest_queue_dict(latest_queue_row(own_rows))
            events = _history(own_rows)
            for ev in events:  # 이벤트별 차수 링크 (자기 브랜드 차수 = roundId, 병용 공유 포함 전체 = linkedRoundIds)
                ls = links.get(ev["id"], []) if ev["id"] is not None else []
                own = [rid for rid, rd, _ in ls if rd == drug["drug_id"]]
                ev["roundId"] = own[0] if own else None
                ev["linkedRoundIds"] = [rid for rid, _, _ in ls]
            d["events"] = events
            d["nhis_rows"] = _admin_nhis_rows(conn, drug["drug_id"], superseded)
            rounds_out = []
            for c in cards:
                r, it = c["round"], c["item"]
                rounds_out.append({
                    "round_id": r["round_id"] if r is not None else None,
                    "round_label": it.get("roundLabel"),
                    "list_type": (r["list_type"] if r is not None else drug["listing_type"]),
                    "indication_scope": it.get("indicationScope"),
                    "combo_key": it.get("comboKey"),
                    "combo_partners": it.get("comboPartners") or [],
                    "source": it.get("roundSource"),
                    "needs_review": it.get("roundNeedsReview"),
                    "is_primary": bool(c.get("is_primary")) or r is None,
                    "submitted_date": it["submittedDate"],
                    "amjilsim_pass_date": it["amjilsimPassDate"],
                    "yakpyungwi_pass_date": it["yakpyungwiPassDate"],
                    "negotiation_status": it["negotiationStatus"],
                    "nhis_registered_ym": it["nhisRegisteredYm"],
                    "negotiation_complete_date": it["negotiationCompleteDate"],
                    "negotiation_date_source": it["negotiationDateSource"],
                    "reimb_start_date": it.get("reimbStartDate"),
                    "notes": (r["notes"] if r is not None else None),
                    "stage": c["stage"],
                    "status": it["status"],
                    "next_step": it["nextStep"],
                    "last_result": it["lastResult"],
                    "event_ids": [q["id"] for q in c["queue_rows"]],
                    # 다른 브랜드 이벤트(병용 공유 링크) — 예: 키트루다 2026 차수의 파드셉 약평위 2026-04-02
                    "shared_events": [
                        {**h, "drugId": q["drug_id"]}
                        for q, h in ((q, _history([q])[0]) for q in c["queue_rows"])
                        if q["drug_id"] != drug["drug_id"]],
                    "nhis_ids": [n["id"] for n in d["nhis_rows"]
                                 if r is not None and n.get("roundId") == r["round_id"]]
                                if r is not None else [n["id"] for n in d["nhis_rows"]],
                })
            d["rounds"] = rounds_out
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
        if rounds_enabled(conn):  # 차수·링크 정리 (공단 아카이브 행은 보존, 차수 연결만 해제)
            rids = [r["round_id"] for r in list_rounds(conn, drug_id)]
            conn.execute("DELETE FROM reimb_round_events WHERE round_drug_id = ? OR queue_id IN "
                         "(SELECT id FROM amjilsim_drug_queue_status WHERE drug_id = ?)", (drug_id, drug_id))
            if rids and _nhis_has_round_col(conn):
                conn.execute(f"UPDATE nhis_negotiations SET round_id = NULL, round_link_source = NULL "
                             f"WHERE round_id IN ({','.join('?' for _ in rids)})", rids)
            conn.execute("DELETE FROM reimb_listing_rounds WHERE drug_id = ?", (drug_id,))
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
    round_id = payload.get("round_id")  # 선택: 등재 차수 명시 연결
    if round_id is not None and not isinstance(round_id, int):
        raise ValueError("round_id 는 정수 또는 null")

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
        event_id = cur.lastrowid
        if round_id is not None:
            ensure_round_schema(conn)
            if _get_round(conn, round_id)["drug_id"] != drug_id:
                raise ValueError("round_id 가 이 약제의 차수가 아님 (다른 브랜드 공유는 차수 패널에서 연결)")
            link_event_to_round(conn, event_id, round_id, "explicit")
        elif rounds_enabled(conn) and list_rounds(conn, drug_id):
            attach_unlinked_events(conn, drug_id, allow_create=False)
        conn.commit()
    return {"event_id": event_id, "drug_id": drug_id, "round_id": round_id}


def delete_event(event_id: int) -> dict:
    with _connect() as conn:
        if rounds_enabled(conn):
            conn.execute("DELETE FROM reimb_round_events WHERE queue_id = ?", (event_id,))
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


# ══════════════════════════════════════════════════════════════════════════════
# 등재 차수(listing round) — 브랜드(amjilsim_drugs) 1 : N 차수
# ══════════════════════════════════════════════════════════════════════════════
#
# 문제: 브랜드당 1행이라 같은 브랜드의 서로 다른 등재 차수가 한 카드에 섞였다.
#   키트루다 = '확대 · 11개 적응증 · 약평위 2025-09-04' 행에 2026 파드셉 병용 요로상피암 1차 확대
#   (암질심 2025-10-29 → 약평위 2026-04-02 → 공단 확대 2026-05 → 합의 2026-09 → 급여 2026-10-01)의 공단 협상이
#   덮어써지고, 위원회 이력은 파드셉 행에만 있었다.
#
# 모델 (agents/db/schema.py REIMB_ROUNDS_DDL_STATEMENTS — 추가 전용):
#   reimb_listing_rounds  : 차수 1행 = (브랜드 drug_id, round_label, list_type, indication_scope, 통과일·협상 필드, combo_key)
#   reimb_round_events    : 위원회 큐 이벤트 ↔ 차수 (다대다; 같은 브랜드 안에서는 이벤트 1건 = 차수 1개)
#   nhis_negotiations.round_id : 공단 협상 행 → 차수 (단일 컬럼 — 한 행이 두 차수에 붙지 않음)
# 차수가 하나도 없는 브랜드는 기존처럼 약제 행 자체가 '가상 차수'(roundId=None) — 하위 호환.
#
# 매칭 규칙 (명시 링크 > 형제 행 > 병용 파트너 > 날짜 창):
#   공단 행: ① round_id 명시 ② 같은 협상(같은 약제·목록·등록연월)의 다른 행이 붙은 차수 ③ combo_key 공유 차수의 파트너
#            공단 행이 ±1개월 안에 있음 ④ 통과일(약평위, 없으면 암질심)이 등록연월 이전·12개월 이내이고 다른 협상을 아직
#            갖지 않은 차수 중 통과일이 가장 늦은 것(동률 = 모호 → 미연결) ⑤ 없으면 새 차수 자동 생성(needs_review)
#   위원회 이벤트: ① 명시 링크 ② 통과일과 같은 날·같은 위원회 ③ 이벤트일 이후 첫 통과일(앵커)을 가진 차수 — 진행 중(통과일 없음)
#            차수는 맨 뒤 ④ 없으면 (허용 시) 새 차수 자동 생성
# ──────────────────────────────────────────────────────────────────────────────

NHIS_WINDOW_MONTHS = 12        # 위원회 통과 → 공단 등록 허용 간격
NHIS_NEAR_CYCLE_MONTHS = 3     # 분할 제안 시 이보다 가까운 두 협상은 같은 차수(재등록)일 수 있어 모호
ROUND_SOURCES = ("legacy", "migration", "auto_nhis", "auto_committee", "admin", "committee_import")
_ROUND_FIELDS = {  # payload key → (column, kind)
    "round_label": "text", "list_type": "list_type", "indication_scope": "text", "combo_key": "text",
    "submitted_date": "date", "amjilsim_pass_date": "date", "yakpyungwi_pass_date": "date",
    "negotiation_status": "neg", "nhis_registered_ym": "ym", "negotiation_complete_date": "ym",
    "reimb_start_date": "date", "notes": "text", "needs_review": "bool",
}
_YM_RE = re.compile(r"^\d{4}-\d{2}$")
_REJECT_RESULT_RE = re.compile(r"결렬|불발|중단|철회")


def _load_rounds_ddl() -> tuple[str, ...]:
    """DDL 단일 소스 = agents/db/schema.py. agents.db 패키지 __init__(pandas 등 무거운 의존)를 피해 파일 직접 로드."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("_ma_db_schema_rounds", BASE_DIR / "agents" / "db" / "schema.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return tuple(mod.REIMB_ROUNDS_DDL_STATEMENTS)


REIMB_ROUNDS_DDL_STATEMENTS = _load_rounds_ddl()


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def rounds_enabled(conn: sqlite3.Connection) -> bool:
    return _table_exists(conn, "reimb_listing_rounds") and _table_exists(conn, "reimb_round_events")


def _nhis_has_round_col(conn: sqlite3.Connection) -> bool:
    return _nhis_table_exists(conn) and "round_id" in {r[1] for r in conn.execute("PRAGMA table_info(nhis_negotiations)")}


def ensure_round_schema(conn: sqlite3.Connection) -> None:
    """차수 테이블 + nhis_negotiations.round_id 멱등 보강 (CREATE/ALTER IF NOT EXISTS 성격, 삭제 없음).
    커밋하지 않는다 — 호출자 트랜잭션 안에서 실행 가능 (Python sqlite3 는 DDL 앞에서 암묵 커밋하지 않음)."""
    for stmt in REIMB_ROUNDS_DDL_STATEMENTS:
        conn.execute(stmt)
    if _nhis_table_exists(conn):
        cols = {r[1] for r in conn.execute("PRAGMA table_info(nhis_negotiations)")}
        if "round_id" not in cols:
            conn.execute("ALTER TABLE nhis_negotiations ADD COLUMN round_id INTEGER")
        if "round_link_source" not in cols:
            conn.execute("ALTER TABLE nhis_negotiations ADD COLUMN round_link_source TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_nhis_round ON nhis_negotiations(round_id)")


# ── 날짜 유틸 ────────────────────────────────────────────────────────────────

def _ym_of(value: Optional[str]) -> Optional[str]:
    return value[:7] if value and re.match(r"^\d{4}-\d{2}", str(value)) else None


def _month_index(ym: str) -> int:
    return int(ym[:4]) * 12 + int(ym[5:7]) - 1


def _months_between(a_ym: str, b_ym: str) -> int:
    """b - a (개월)."""
    return _month_index(b_ym) - _month_index(a_ym)


def _round_closed(r) -> bool:
    return _row_get(r, "negotiation_status") in ("AGREED", "REJECTED")


def _round_anchor(r) -> Optional[str]:
    """차수의 위원회 종착 통과일 — 약평위, 없으면 암질심."""
    return _row_get(r, "yakpyungwi_pass_date") or _row_get(r, "amjilsim_pass_date")


def _round_sort_key(r) -> tuple:
    return (_round_anchor(r) or (f"{r['nhis_registered_ym']}-01" if _row_get(r, "nhis_registered_ym") else None)
            or _row_get(r, "submitted_date") or (_row_get(r, "created_at") or "")[:10] or "9999", r["round_id"])


def round_label_for(drug_like, *, year_hint: Optional[str] = None) -> Optional[str]:
    """'2025 확대 · 11개 적응증' — 연도(약평위→암질심→공단 등록→힌트) + 유형 + 적응증 범위. 없는 부분은 생략."""
    year = (_round_anchor(drug_like) or _row_get(drug_like, "nhis_registered_ym")
            or _row_get(drug_like, "submitted_date") or year_hint or "")[:4]
    lt = _row_get(drug_like, "list_type") or _row_get(drug_like, "listing_type")
    scope = _row_get(drug_like, "indication_scope") or _row_get(drug_like, "indication")
    head = " ".join(x for x in (year, lt) if x)
    label = " · ".join(x for x in (head, scope) if x)
    return label or None


def nhis_result_to_status(result: Optional[str], in_progress: bool) -> str:
    """공단 결과 → negotiation_status (nhis_negotiation_import._result_to_status 와 동일 규칙)."""
    if _REJECT_RESULT_RE.search(result or ""):
        return "REJECTED"
    return "IN_PROGRESS" if in_progress else "AGREED"


def best_nhis_row(rows: list):
    """한 협상 묶음에서 현재 상태를 대표하는 행.

    최신 등록연월 우선. 같은 등록연월이면 **완료 행이 협상 중 행보다 우선** — 같은 협상의 '협상 중' 아카이브 행이
    완료 행을 덮던 tie-break 버그 수정(2026-10-17). 그다음 완료연월·id.
    """
    if not rows:
        return None
    return max(rows, key=lambda r: (_row_get(r, "registered_ym") or "",
                                    1 if _row_get(r, "completed_ym") else 0,
                                    _row_get(r, "completed_ym") or "", _row_get(r, "id") or 0))


# ── 조회 ─────────────────────────────────────────────────────────────────────

def list_rounds(conn: sqlite3.Connection, drug_id: Optional[int] = None) -> list[sqlite3.Row]:
    if not rounds_enabled(conn):
        return []
    if drug_id is None:
        rows = conn.execute("SELECT * FROM reimb_listing_rounds").fetchall()
    else:
        rows = conn.execute("SELECT * FROM reimb_listing_rounds WHERE drug_id = ?", (drug_id,)).fetchall()
    return sorted(rows, key=lambda r: (r["drug_id"], _round_sort_key(r)))


def _get_round(conn: sqlite3.Connection, round_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM reimb_listing_rounds WHERE round_id = ?", (round_id,)).fetchone() \
        if rounds_enabled(conn) else None
    if not row:
        raise LookupError(f"차수 없음: {round_id}")
    return row


def primary_round(rounds: list):
    """브랜드 요약에 쓰는 대표 차수 — 진행 중(협상 미종결) 차수 중 가장 늦은 것, 없으면 가장 늦은 차수."""
    if not rounds:
        return None
    ordered = sorted(rounds, key=_round_sort_key)
    open_ = [r for r in ordered if not _round_closed(r)]
    return (open_ or ordered)[-1]


def _event_links(conn: sqlite3.Connection) -> dict[int, list[tuple[int, int, str]]]:
    """queue_id → [(round_id, round_drug_id, link_source)]."""
    out: dict[int, list[tuple[int, int, str]]] = {}
    if not rounds_enabled(conn):
        return out
    for r in conn.execute("SELECT round_id, queue_id, round_drug_id, link_source FROM reimb_round_events"):
        out.setdefault(r["queue_id"], []).append((r["round_id"], r["round_drug_id"], r["link_source"]))
    return out


def _round_queue_rows(conn: sqlite3.Connection, round_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT q.*, s.session_date, s.ordinal_official, s.ordinal_assumed, s.year AS session_year
           FROM reimb_round_events l
           JOIN amjilsim_drug_queue_status q ON q.id = l.queue_id
           LEFT JOIN amjilsim_sessions s ON s.session_id = q.session_id
           WHERE l.round_id = ? ORDER BY q.observed_at, q.id""", (round_id,)).fetchall()


def _round_nhis_rows(conn: sqlite3.Connection, round_id: int) -> list[sqlite3.Row]:
    if not _nhis_has_round_col(conn):
        return []
    return conn.execute("SELECT * FROM nhis_negotiations WHERE round_id = ? ORDER BY registered_ym, id",
                        (round_id,)).fetchall()


def _nhis_cycles(conn: sqlite3.Connection, drug_id: int) -> list[dict]:
    """약제의 공단 협상 묶음 — (목록, 등록연월) 키. 같은 협상의 협상중·완료 아카이브 행은 한 묶음."""
    if not _nhis_table_exists(conn):
        return []
    rows = conn.execute("SELECT * FROM nhis_negotiations WHERE drug_id = ? ORDER BY registered_ym, id",
                        (drug_id,)).fetchall()
    cycles: dict[tuple, dict] = {}
    for r in rows:
        key = (r["list_type"], r["registered_ym"])
        c = cycles.setdefault(key, {"list_type": r["list_type"], "registered_ym": r["registered_ym"], "rows": []})
        c["rows"].append(r)
    out = []
    for c in cycles.values():
        best = best_nhis_row(c["rows"])
        c.update({"nhis_ids": [r["id"] for r in c["rows"]], "completed_ym": best["completed_ym"],
                  "result": best["result"], "product_keys": sorted({_nhis_product_key(r["product_name"]) for r in c["rows"]}),
                  "round_ids": sorted({_row_get(r, "round_id") for r in c["rows"] if _row_get(r, "round_id")})})
        out.append(c)
    return sorted(out, key=lambda c: (c["registered_ym"] or "", c["list_type"]))


# ── 매칭 규칙 ────────────────────────────────────────────────────────────────

def pick_round_for_event(rounds: list, event_date: Optional[str], committee: Optional[str]):
    """위원회 이벤트 → 차수 (같은 브랜드 차수 목록 대상). 결정 불가 시 None."""
    if not rounds or not event_date:
        return None
    d = event_date[:10]
    col = "amjilsim_pass_date" if committee == "AMJILSIM" else "yakpyungwi_pass_date"
    exact = [r for r in rounds if _row_get(r, col) == d]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return None
    # 이벤트일 이후 첫 위원회 통과(앵커)를 가진 차수 — 이벤트는 그 차수의 심의 과정. 통과일 없는 진행 중 차수는 맨 뒤.
    cands = []
    for r in rounds:
        anchor = _round_anchor(r)
        if anchor is None:
            if _round_closed(r):
                continue
            anchor = "9999-12-31"
        if anchor >= d:
            cands.append((anchor, r))
    if not cands:
        return None
    best_anchor = min(a for a, _ in cands)
    tied = [r for a, r in cands if a == best_anchor]
    if len(tied) == 1:
        return tied[0]
    if best_anchor == "9999-12-31":
        return max(tied, key=lambda r: r["round_id"])  # 진행 중 차수 여럿 — 가장 최근 생성
    return None


def pick_round_for_nhis(conn: sqlite3.Connection, rounds: list, row) -> tuple[Optional[Any], str]:
    """공단 협상 행 → (차수, 근거). 결정 불가 시 (None, 사유)."""
    if not rounds:
        return None, "no_rounds"
    by_id = {r["round_id"]: r for r in rounds}
    rid = _row_get(row, "round_id")
    if rid and rid in by_id:
        return by_id[rid], "explicit"
    reg = _row_get(row, "registered_ym")
    # ② 같은 협상 묶음(형제 행)
    sib = conn.execute(
        "SELECT round_id FROM nhis_negotiations WHERE drug_id = ? AND list_type = ? AND registered_ym IS ? "
        "AND round_id IS NOT NULL AND id != ? LIMIT 1",
        (row["drug_id"], row["list_type"], reg, row["id"])).fetchone()
    if sib and sib["round_id"] in by_id:
        return by_id[sib["round_id"]], "sibling"
    if not reg or not _YM_RE.match(reg):
        return None, "no_registered_ym"
    # ③ 병용 파트너 — combo_key 공유 차수의 파트너 공단 행이 ±1개월
    for r in rounds:
        key = _row_get(r, "combo_key")
        if not key:
            continue
        partner = conn.execute(
            """SELECT n.registered_ym FROM nhis_negotiations n JOIN reimb_listing_rounds pr ON pr.round_id = n.round_id
               WHERE pr.combo_key = ? AND pr.drug_id != ?""", (key, r["drug_id"])).fetchall()
        if any(p["registered_ym"] and _YM_RE.match(p["registered_ym"])
               and abs(_months_between(p["registered_ym"], reg)) <= 1 for p in partner):
            return r, "combo"
    # ④ 날짜 창 — 통과일이 등록연월 이전·12개월 이내, 다른 협상을 아직 갖지 않은 차수 중 통과일이 가장 늦은 것
    held: dict[int, set] = {}
    for n in conn.execute("SELECT round_id, list_type, registered_ym FROM nhis_negotiations "
                          "WHERE drug_id = ? AND round_id IS NOT NULL", (row["drug_id"],)):
        held.setdefault(n["round_id"], set()).add((n["list_type"], n["registered_ym"]))
    cands = []
    for r in rounds:
        anchor = _round_anchor(r)
        if not anchor:
            continue
        other = held.get(r["round_id"], set()) - {(row["list_type"], reg)}
        if other:
            continue
        gap = _months_between(anchor[:7], reg)
        if 0 <= gap <= NHIS_WINDOW_MONTHS:
            cands.append(r)
    if not cands:
        return None, "no_window_match"
    typed = [r for r in cands if not _row_get(r, "list_type") or r["list_type"] == row["list_type"]]
    cands = typed or cands
    latest = max(_round_anchor(r) for r in cands)
    top = [r for r in cands if _round_anchor(r) == latest]
    if len(top) > 1:
        return None, "ambiguous_window"
    return top[0], "window"


# ── 쓰기 ─────────────────────────────────────────────────────────────────────

def create_round_row(conn: sqlite3.Connection, drug_id: int, *, round_label: str,
                     source: str = "admin", **fields) -> int:
    """차수 INSERT (같은 브랜드·같은 라벨이 있으면 그 round_id 반환 — 멱등). 커밋하지 않음."""
    if not (round_label or "").strip():
        raise ValueError("round_label 필수")
    if source not in ROUND_SOURCES:
        raise ValueError(f"source 는 {ROUND_SOURCES} 중 하나")
    ex = conn.execute("SELECT round_id FROM reimb_listing_rounds WHERE drug_id = ? AND round_label = ?",
                      (drug_id, round_label.strip())).fetchone()
    if ex:
        return ex["round_id"]
    cols = ["drug_id", "round_label", "source"]
    vals: list[Any] = [drug_id, round_label.strip(), source]
    for k, v in fields.items():
        if (k in _ROUND_FIELDS or k == "negotiation_date_source") and k != "round_label" and v is not None:
            cols.append(k)
            vals.append(int(bool(v)) if _ROUND_FIELDS.get(k) == "bool" else v)
    cur = conn.execute(f"INSERT INTO reimb_listing_rounds ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                       vals)
    return cur.lastrowid


def _unique_label(conn: sqlite3.Connection, drug_id: int, label: str) -> str:
    base, n = label, 2
    while conn.execute("SELECT 1 FROM reimb_listing_rounds WHERE drug_id = ? AND round_label = ?",
                       (drug_id, label)).fetchone():
        label = f"{base} #{n}"
        n += 1
    return label


def link_event_to_round(conn: sqlite3.Connection, queue_id: int, round_id: int, source: str = "explicit") -> None:
    """이벤트 ↔ 차수. 같은 브랜드의 다른 차수 링크는 교체(한 이벤트가 한 브랜드의 두 차수에 붙지 않음),
    다른 브랜드 차수 링크(병용 공유)는 유지. 커밋하지 않음."""
    rnd = _get_round(conn, round_id)
    if not conn.execute("SELECT 1 FROM amjilsim_drug_queue_status WHERE id = ?", (queue_id,)).fetchone():
        raise LookupError(f"이벤트 없음: {queue_id}")
    conn.execute("DELETE FROM reimb_round_events WHERE queue_id = ? AND round_drug_id = ?", (queue_id, rnd["drug_id"]))
    conn.execute("INSERT INTO reimb_round_events (round_id, queue_id, round_drug_id, link_source) VALUES (?,?,?,?)",
                 (round_id, queue_id, rnd["drug_id"], source))


def link_nhis_to_round(conn: sqlite3.Connection, nhis_id: int, round_id: Optional[int],
                       source: str = "explicit", with_siblings: bool = True) -> list[int]:
    """공단 행 → 차수 (round_id None = 연결 해제). 같은 협상 묶음의 형제 행(협상중 아카이브 등)도 함께. 커밋하지 않음."""
    row = conn.execute("SELECT * FROM nhis_negotiations WHERE id = ?", (nhis_id,)).fetchone()
    if not row:
        raise LookupError(f"공단 협상 행 없음: {nhis_id}")
    if round_id is not None:
        rnd = _get_round(conn, round_id)
        if row["drug_id"] is None:
            raise ValueError("미매칭 공단 행(drug_id 없음)은 차수에 연결할 수 없음 — 먼저 약제 매칭")
        if rnd["drug_id"] != row["drug_id"]:
            raise ValueError(f"공단 행 약제({row['drug_id']}) ≠ 차수 브랜드({rnd['drug_id']}) — 병용은 각 브랜드 차수에 연결")
    ids = [nhis_id]
    if with_siblings and row["drug_id"] is not None:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM nhis_negotiations WHERE drug_id = ? AND list_type = ? AND registered_ym IS ?",
            (row["drug_id"], row["list_type"], row["registered_ym"]))]
    for i in ids:
        conn.execute("UPDATE nhis_negotiations SET round_id = ?, round_link_source = ? WHERE id = ?",
                     (round_id, source if round_id is not None else None, i))
    return ids


def refresh_round_negotiation(conn: sqlite3.Connection, round_id: int) -> None:
    """연결된 공단 행에서 차수 협상 필드 재계산 (공단 공식 우선). 연결 행이 없고 이전 값이 공단 출처면 비운다."""
    rnd = _get_round(conn, round_id)
    rows = _round_nhis_rows(conn, round_id)
    if rows:
        best = best_nhis_row(rows)
        conn.execute(
            """UPDATE reimb_listing_rounds SET negotiation_status = ?, nhis_registered_ym = ?,
                 negotiation_complete_date = ?, negotiation_date_source = 'nhis_official',
                 list_type = COALESCE(list_type, ?), updated_at = datetime('now') WHERE round_id = ?""",
            (nhis_result_to_status(best["result"], not best["completed_ym"]), best["registered_ym"],
             best["completed_ym"] or None, best["list_type"], round_id))
    elif rnd["negotiation_date_source"] == "nhis_official":
        conn.execute(
            """UPDATE reimb_listing_rounds SET negotiation_status = NULL, nhis_registered_ym = NULL,
                 negotiation_complete_date = NULL, negotiation_date_source = NULL, updated_at = datetime('now')
               WHERE round_id = ?""", (round_id,))


def _drug_row(conn: sqlite3.Connection, drug_id: int) -> sqlite3.Row:
    d = conn.execute("SELECT * FROM amjilsim_drugs WHERE drug_id = ?", (drug_id,)).fetchone()
    if not d:
        raise LookupError(f"약제 없음: {drug_id}")
    return d


def materialize_default_round(conn: sqlite3.Connection, drug_id: int, source: str = "legacy") -> Optional[int]:
    """차수가 없는 브랜드의 약제 행(가상 차수)을 실제 차수 1행으로 고정. 이미 차수가 있으면 None.
    공단 출처 협상 필드는 복사하지 않는다 — 협상 상태는 연결된 공단 행에서만(refresh_round_negotiation)."""
    if list_rounds(conn, drug_id):
        return None
    d = _drug_row(conn, drug_id)
    manual_neg = d["negotiation_date_source"] != "nhis_official" if "negotiation_date_source" in d.keys() else True
    label = round_label_for(d) or f"{d['brand_kr']} 등재"
    return create_round_row(
        conn, drug_id, round_label=label, source=source,
        list_type=d["listing_type"] if d["listing_type"] in LISTING_TYPES else None,
        indication_scope=d["indication"], submitted_date=d["submitted_date"],
        amjilsim_pass_date=d["amjilsim_pass_date"], yakpyungwi_pass_date=d["yakpyungwi_pass_date"],
        negotiation_status=d["negotiation_status"] if manual_neg else None,
        negotiation_date_source="manual" if manual_neg and d["negotiation_status"] else None)


def attach_unlinked_events(conn: sqlite3.Connection, drug_id: int, allow_create: bool = False) -> list[dict]:
    """브랜드에 차수가 있을 때, 차수 미연결 자기 이벤트를 규칙으로 연결. allow_create 면 갈 곳 없는 통과·대기 이벤트로
    새 차수(auto_committee, needs_review) 생성 — 모든 차수가 끝난 뒤 새 위원회 심의가 시작된 경우."""
    rounds = list_rounds(conn, drug_id)
    if not rounds:
        return []
    linked = {r["queue_id"] for r in conn.execute(
        "SELECT queue_id FROM reimb_round_events WHERE round_drug_id = ?", (drug_id,))}
    out = []
    for q in conn.execute(_QUEUE_JOIN_SQL, (drug_id,)).fetchall():
        if q["id"] in linked:
            continue
        d = _event_date(q) or None
        rnd = pick_round_for_event(rounds, d, q["committee_type"])
        if rnd is None and allow_create and d and q["queue_state"] in ("APPROVED", "QUEUE_PENDING", "REJECTED_REQUEUE"):
            drug = _drug_row(conn, drug_id)
            col = "amjilsim_pass_date" if q["committee_type"] == "AMJILSIM" else "yakpyungwi_pass_date"
            label = _unique_label(conn, drug_id, round_label_for(
                {"listing_type": drug["listing_type"], "indication": drug["indication"]}, year_hint=d) or f"{d[:4]} 등재")
            rid = create_round_row(conn, drug_id, round_label=label, source="auto_committee", needs_review=1,
                                   list_type=drug["listing_type"] if drug["listing_type"] in LISTING_TYPES else None,
                                   indication_scope=drug["indication"],
                                   **({col: d} if q["queue_state"] == "APPROVED" else {}))
            rounds = list_rounds(conn, drug_id)
            rnd = next(r for r in rounds if r["round_id"] == rid)
            out.append({"queue_id": q["id"], "round_id": rid, "created": True})
        if rnd is not None:
            link_event_to_round(conn, q["id"], rnd["round_id"], "auto")
            if not out or out[-1].get("queue_id") != q["id"]:
                out.append({"queue_id": q["id"], "round_id": rnd["round_id"], "created": False})
    return out


def attach_unlinked_nhis(conn: sqlite3.Connection, drug_id: int, allow_create: bool = True) -> list[dict]:
    """브랜드에 차수가 있을 때, 차수 미연결 공단 행을 규칙으로 연결 (등록연월 오름차순 — 앞 협상이 먼저 자리를 잡는다).
    갈 곳이 없으면(allow_create) '공단 YYYY-MM 협상' 차수 자동 생성(needs_review). 모호(동률)면 미연결 유지."""
    if not _nhis_has_round_col(conn):
        return []
    out = []
    rows = conn.execute("SELECT * FROM nhis_negotiations WHERE drug_id = ? AND round_id IS NULL "
                        "ORDER BY registered_ym, id", (drug_id,)).fetchall()
    for row in rows:
        cur = conn.execute("SELECT * FROM nhis_negotiations WHERE id = ?", (row["id"],)).fetchone()
        if cur["round_id"] is not None:
            continue  # 앞선 형제 행 연결로 이미 붙음
        rounds = list_rounds(conn, drug_id)
        rnd, why = pick_round_for_nhis(conn, rounds, cur)
        if rnd is None and why == "no_window_match" and allow_create:
            reg = cur["registered_ym"]
            label = _unique_label(conn, drug_id, f"{reg[:4]} {cur['list_type']} · 공단 {reg} 협상")
            rid = create_round_row(conn, drug_id, round_label=label, source="auto_nhis", needs_review=1,
                                   list_type=cur["list_type"], nhis_registered_ym=reg)
            link_nhis_to_round(conn, cur["id"], rid, "auto_new")
            refresh_round_negotiation(conn, rid)
            out.append({"nhis_id": cur["id"], "round_id": rid, "rule": "created"})
            continue
        if rnd is None:
            out.append({"nhis_id": cur["id"], "round_id": None, "rule": why})
            continue
        link_nhis_to_round(conn, cur["id"], rnd["round_id"], why)
        refresh_round_negotiation(conn, rnd["round_id"])
        out.append({"nhis_id": cur["id"], "round_id": rnd["round_id"], "rule": why})
    return out


# ── 분할 제안 (마이그레이션·공단 적재 공용) ───────────────────────────────────

_FORM_TAIL_RE = re.compile(r"(주사액|주사|주|정|캡슐|캅셀|액|시럽)$")


def brand_short(name: Optional[str]) -> str:
    """'파드셉주 20·30mg' → '파드셉', '키트루다' → '키트루다', '다잘렉스 PV' → '다잘렉스'."""
    s = re.split(r"[\s\d(_·,/+]", (name or "").strip(), 1)[0]
    s2 = _FORM_TAIL_RE.sub("", s)
    return s2 if len(s2) >= 2 else s


def _strip_combo_note(indication: Optional[str]) -> Optional[str]:
    """'요로상피암 1차 (펨브로 병용)' → '요로상피암 1차'."""
    if not indication:
        return indication
    return re.sub(r"\s*\([^)]*병용[^)]*\)", "", indication).strip() or indication


def _find_combo_partner(conn: sqlite3.Connection, drug, registered_ym: str) -> Optional[dict]:
    """같은 시기(±1개월) 공단 협상이 있고, 적응증·메모에 이 브랜드명이 나오는 다른 추적 약제 = 병용 파트너."""
    short = brand_short(drug["brand_kr"])
    if len(short) < 2 or not _nhis_table_exists(conn):
        return None
    hits = []
    for p in conn.execute("SELECT * FROM amjilsim_drugs WHERE drug_id != ?", (drug["drug_id"],)):
        text = " ".join(x for x in (p["indication"], p["notes"], p["brand_kr"]) if x)
        if short not in text or "병용" not in text:
            continue
        for c in _nhis_cycles(conn, p["drug_id"]):
            reg = c["registered_ym"]
            if reg and _YM_RE.match(reg) and abs(_months_between(reg, registered_ym)) <= 1:
                hits.append({"drug": p, "cycle": c})
    return hits[0] if len(hits) == 1 else None


def propose_split(conn: sqlite3.Connection, drug_id: int) -> dict:
    """공단 협상 묶음이 2개 이상인 브랜드의 차수 분할 제안 (읽기 전용).

    - 협상 묶음마다 차수 1개. 위원회 이벤트·통과일은 '그 날짜 이후 첫 공단 등록 묶음'에 귀속, 마지막 묶음 뒤의 것은
      아직 공단 단계 전인 진행 중 차수.
    - 약제 행의 적응증·유형 라벨은 약제 행 통과일(약평위→암질심)이 귀속된 차수가 갖는다.
    - 위원회 근거가 없는 묶음은 병용 파트너(같은 시기 공단 협상 + 메모에 이 브랜드명)가 있으면 '<파트너> 병용 <적응증>',
      없으면 '공단 YYYY-MM 협상' + needs_review.
    모호(적용 금지): 등록연월 없음 / 두 묶음이 3개월 미만 간격 / 묶음별 제품명이 서로 다름(다른 제형·제품 가능) /
      약제 행에 통과일이 없어 라벨 귀속 불가 / 라벨 유형(신규·확대)이 귀속 묶음 유형과 다름.
    """
    drug = _drug_row(conn, drug_id)
    cycles = _nhis_cycles(conn, drug_id)
    plan: dict[str, Any] = {"drug_id": drug_id, "brand": drug["brand_kr"], "cycles": [], "rounds": [],
                            "partner_rounds": [], "ambiguous": False, "reasons": [],
                            "existing_rounds": len(list_rounds(conn, drug_id))}
    plan["cycles"] = [{k: c[k] for k in ("list_type", "registered_ym", "completed_ym", "result", "nhis_ids",
                                         "product_keys")} for c in cycles]
    if plan["existing_rounds"]:
        plan["reasons"].append("이미 차수가 있음 — 분할 대신 attach 규칙 적용")
        return plan
    if len(cycles) < 2:
        plan["reasons"].append("공단 협상 묶음 1개 이하 — 분할 불필요")
        return plan
    regs = [c["registered_ym"] for c in cycles]
    if any(not r or not _YM_RE.match(r) for r in regs):
        plan["ambiguous"] = True
        plan["reasons"].append("등록연월 없는 공단 행")
        return plan
    for a, b in zip(cycles, cycles[1:]):
        if _months_between(a["registered_ym"], b["registered_ym"]) < NHIS_NEAR_CYCLE_MONTHS:
            plan["ambiguous"] = True
            plan["reasons"].append(f"근접 협상 {a['list_type']} {a['registered_ym']} / {b['list_type']} {b['registered_ym']} "
                                   "— 같은 차수 재등록인지 별도 차수인지 불명")
    keys = {k for c in cycles for k in c["product_keys"]}
    if len(keys) > 1:
        plan["ambiguous"] = True
        plan["reasons"].append(f"묶음별 공단 제품명 상이 {sorted(keys)} — 같은 브랜드의 다른 제품(제형)일 수 있음")

    def target(d: Optional[str]) -> Optional[int]:
        """날짜 → 귀속 묶음 index (len(cycles) = 공단 전 진행 중 차수)."""
        if not d:
            return None
        ym = d[:7]
        for i, c in enumerate(cycles):
            if ym <= c["registered_ym"]:
                return i
        return len(cycles)

    slots: dict[int, dict] = {i: {"events": [], "amj": None, "yak": None} for i in range(len(cycles) + 1)}
    for q in conn.execute(_QUEUE_JOIN_SQL, (drug_id,)).fetchall():
        d = _event_date(q) or None
        i = target(d)
        if i is None:
            plan["ambiguous"] = True
            plan["reasons"].append(f"사건일 없는 이벤트 #{q['id']}")
            continue
        slots[i]["events"].append(q["id"])
        if q["queue_state"] == "APPROVED":
            k = "amj" if q["committee_type"] == "AMJILSIM" else "yak"
            slots[i][k] = max(filter(None, (slots[i][k], d)))
    owner_date = drug["yakpyungwi_pass_date"] or drug["amjilsim_pass_date"]
    owner = target(owner_date)
    if owner is None:
        owner = len(cycles) if slots[len(cycles)]["events"] else None
    if owner is None:
        plan["ambiguous"] = True
        plan["reasons"].append("약제 행에 통과일이 없어 적응증 라벨을 어느 차수에 둘지 불명")
    for col, k in (("amjilsim_pass_date", "amj"), ("yakpyungwi_pass_date", "yak")):
        i = target(drug[col])
        if i is not None and not slots[i][k]:
            slots[i][k] = drug[col]

    for i in range(len(cycles) + 1):
        c = cycles[i] if i < len(cycles) else None
        s = slots[i]
        if c is None and not s["events"] and owner != i:
            continue  # 공단 전 진행 중 차수 없음
        r: dict[str, Any] = {"cycle_index": i, "list_type": c["list_type"] if c else drug["listing_type"],
                             "nhis_registered_ym": c["registered_ym"] if c else None,
                             "nhis_ids": c["nhis_ids"] if c else [], "event_ids": s["events"],
                             "partner_event_ids": [], "amjilsim_pass_date": s["amj"], "yakpyungwi_pass_date": s["yak"],
                             "combo_key": None, "combo_partner_drug_id": None, "needs_review": 0,
                             "owns_drug_label": owner == i, "indication_scope": None}
        if owner == i:
            r["indication_scope"] = drug["indication"]
            if c and drug["listing_type"] in LISTING_TYPES and drug["listing_type"] != c["list_type"]:
                plan["ambiguous"] = True
                plan["reasons"].append(f"약제 행 유형 {drug['listing_type']} ≠ 귀속 공단 묶음 유형 {c['list_type']} ({c['registered_ym']})")
        elif c is not None and not s["amj"] and not s["yak"]:
            partner = _find_combo_partner(conn, drug, c["registered_ym"])
            if partner:
                p = partner["drug"]
                r["indication_scope"] = f"{brand_short(p['brand_kr'])} 병용 {_strip_combo_note(p['indication']) or ''}".strip()
                r["combo_partner_drug_id"] = p["drug_id"]
                r["combo_key"] = f"combo:{'+'.join(str(x) for x in sorted((drug_id, p['drug_id'])))}:{c['registered_ym']}"
                r["amjilsim_pass_date"] = p["amjilsim_pass_date"] if (p["amjilsim_pass_date"] or "9")[:7] <= c["registered_ym"] else None
                r["yakpyungwi_pass_date"] = p["yakpyungwi_pass_date"] if (p["yakpyungwi_pass_date"] or "9")[:7] <= c["registered_ym"] else None
                lo = f"{_month_index(c['registered_ym']) // 12 - 1}"  # 대략 1년 전부터
                r["partner_event_ids"] = [
                    q["id"] for q in conn.execute(_QUEUE_JOIN_SQL, (p["drug_id"],)).fetchall()
                    if (_event_date(q) or "")[:7] <= c["registered_ym"] and (_event_date(q) or "0")[:4] >= lo]
                plan["partner_rounds"].append({"drug_id": p["drug_id"], "brand": p["brand_kr"],
                                               "combo_key": r["combo_key"], "registered_ym": c["registered_ym"],
                                               "existing_rounds": len(list_rounds(conn, p["drug_id"])),
                                               "partner_cycles": len(_nhis_cycles(conn, p["drug_id"]))})
            else:
                r["needs_review"] = 1
        else:
            r["needs_review"] = 0 if (s["amj"] or s["yak"]) and r["indication_scope"] else 1
        year_src = r["yakpyungwi_pass_date"] or r["amjilsim_pass_date"] or r["nhis_registered_ym"]
        label_scope = r["indication_scope"] or (f"공단 {c['registered_ym']} 협상" if c else "진행 중")
        r["round_label"] = " · ".join(x for x in (" ".join(x for x in ((year_src or "")[:4], r["list_type"]) if x),
                                                     label_scope) if x)
        plan["rounds"].append(r)
    labels = [r["round_label"] for r in plan["rounds"]]
    if len(set(labels)) != len(labels):
        plan["ambiguous"] = True
        plan["reasons"].append(f"차수 라벨 중복 {labels}")
    return plan


def apply_split_plan(conn: sqlite3.Connection, plan: dict, source: str = "migration") -> dict:
    """propose_split 결과 적용 (모호 plan 거부). 커밋하지 않음 — 호출자 트랜잭션."""
    if plan.get("ambiguous"):
        raise ValueError(f"모호한 분할 제안은 적용 불가: {plan.get('reasons')}")
    if plan.get("existing_rounds") or not plan.get("rounds"):
        return {"drug_id": plan["drug_id"], "created": [], "skipped": True}
    did = plan["drug_id"]
    created = []
    for r in plan["rounds"]:
        rid = create_round_row(
            conn, did, round_label=r["round_label"], source=source, list_type=r["list_type"],
            indication_scope=r["indication_scope"], combo_key=r["combo_key"],
            amjilsim_pass_date=r["amjilsim_pass_date"], yakpyungwi_pass_date=r["yakpyungwi_pass_date"],
            needs_review=r["needs_review"],
            submitted_date=_drug_row(conn, did)["submitted_date"] if r["owns_drug_label"] else None)
        for nid in r["nhis_ids"]:
            link_nhis_to_round(conn, nid, rid, source, with_siblings=False)
        for qid in r["event_ids"]:
            link_event_to_round(conn, qid, rid, source)
        for qid in r["partner_event_ids"]:
            link_event_to_round(conn, qid, rid, "combo")
        refresh_round_negotiation(conn, rid)
        created.append(rid)
        # 공단 전 진행 중 차수는 약제 행의 수기 협상 상태를 이어받는다 (공단 행이 없으므로)
        if not r["nhis_ids"]:
            d = _drug_row(conn, did)
            if d["negotiation_status"] and d["negotiation_date_source"] != "nhis_official":
                conn.execute("UPDATE reimb_listing_rounds SET negotiation_status = ?, negotiation_date_source = 'manual' "
                             "WHERE round_id = ?", (d["negotiation_status"], rid))
    for pr in plan.get("partner_rounds", []):
        _ensure_partner_combo_round(conn, pr, source)
    return {"drug_id": did, "created": created, "skipped": False}


def _ensure_partner_combo_round(conn: sqlite3.Connection, pr: dict, source: str) -> Optional[int]:
    """병용 파트너 브랜드 쪽 차수에 같은 combo_key 를 단다 (파트너 차수가 없고 협상 묶음이 1개면 기본 차수로 고정)."""
    pid = pr["drug_id"]
    rounds = list_rounds(conn, pid)
    if not rounds:
        if len(_nhis_cycles(conn, pid)) > 1:
            return None  # 파트너도 분할 대상 — 별도 검토
        rid = materialize_default_round(conn, pid, source=source)
        for q in conn.execute(_QUEUE_JOIN_SQL, (pid,)).fetchall():
            link_event_to_round(conn, q["id"], rid, source)
        for c in _nhis_cycles(conn, pid):
            for nid in c["nhis_ids"]:
                link_nhis_to_round(conn, nid, rid, source, with_siblings=False)
        refresh_round_negotiation(conn, rid)
        rounds = list_rounds(conn, pid)
    target = None
    for r in rounds:
        regs = {n["registered_ym"] for n in _round_nhis_rows(conn, r["round_id"])}
        if any(x and _YM_RE.match(x) and abs(_months_between(x, pr["registered_ym"])) <= 1 for x in regs):
            target = r
    if target is None:
        return None
    if not target["combo_key"]:
        conn.execute("UPDATE reimb_listing_rounds SET combo_key = ?, updated_at = datetime('now') WHERE round_id = ?",
                     (pr["combo_key"], target["round_id"]))
    return target["round_id"]


def sync_drug_rounds(conn: sqlite3.Connection, drug_id: int, *, allow_split: bool = True,
                     allow_create: bool = True) -> dict:
    """적재 후 호출 — 차수가 있으면 미연결 공단 행·이벤트를 규칙으로 연결, 없고 공단 협상이 2묶음 이상이면
    (모호하지 않을 때만) 자동 분할. 커밋하지 않음."""
    ensure_round_schema(conn)
    if not list_rounds(conn, drug_id):
        if not allow_split or len(_nhis_cycles(conn, drug_id)) < 2:
            return {"drug_id": drug_id, "action": "none"}
        plan = propose_split(conn, drug_id)
        if plan["ambiguous"]:
            return {"drug_id": drug_id, "action": "ambiguous", "reasons": plan["reasons"]}
        res = apply_split_plan(conn, plan, source="auto_nhis")
        return {"drug_id": drug_id, "action": "split", "rounds": res["created"]}
    nhis = attach_unlinked_nhis(conn, drug_id, allow_create=allow_create)
    events = attach_unlinked_events(conn, drug_id, allow_create=False)
    for r in list_rounds(conn, drug_id):
        refresh_round_negotiation(conn, r["round_id"])
    return {"drug_id": drug_id, "action": "attached", "nhis": nhis, "events": events}


# ── Admin CRUD: rounds ──────────────────────────────────────────────────────

def _validate_round_field(key: str, value: Any) -> Any:
    kind = _ROUND_FIELDS[key]
    if value in (None, ""):
        if key == "round_label":
            raise ValueError("round_label 은 비울 수 없음")
        return 0 if kind == "bool" else None
    if kind == "date":
        return _valid_date(value, key)
    if kind == "ym":
        if not isinstance(value, str) or not _YM_RE.match(value[:7]):
            raise ValueError(f"{key} 는 YYYY-MM 형식이어야 함: {value!r}")
        return value[:7]
    if kind == "list_type" and value not in LISTING_TYPES:
        raise ValueError(f"list_type 은 {LISTING_TYPES} 중 하나여야 함")
    if kind == "neg" and value not in NEGOTIATION_STATUSES:
        raise ValueError(f"negotiation_status 는 {NEGOTIATION_STATUSES} 중 하나여야 함")
    if kind == "bool":
        return 1 if value in (1, True, "1", "true") else 0
    return str(value).strip()


def create_round(drug_id: int, payload: dict) -> dict:
    """admin 차수 추가. 브랜드에 차수가 없으면 먼저 약제 행을 기본 차수로 고정(기존 이벤트·공단 행 전부 기본 차수에
    연결 — 빼앗지 않음)한 뒤 추가.
    payload: round_label(필수), list_type, indication_scope, combo_key, 통과일·협상 필드, notes,
             event_ids[](명시 연결), nhis_ids[](명시 연결)."""
    if not isinstance(payload, dict) or not (payload.get("round_label") or "").strip():
        raise ValueError("round_label 필수")
    fields = {k: _validate_round_field(k, payload[k]) for k in _ROUND_FIELDS if k in payload and k != "round_label"}
    with _connect() as conn:
        ensure_round_schema(conn)
        _drug_row(conn, drug_id)
        if conn.execute("SELECT 1 FROM reimb_listing_rounds WHERE drug_id = ? AND round_label = ?",
                        (drug_id, payload["round_label"].strip())).fetchone():
            raise ValueError(f"같은 라벨의 차수가 이미 있음: {payload['round_label']}")
        default_id = materialize_default_round(conn, drug_id, source="legacy")
        if fields.get("negotiation_status") and "negotiation_date_source" not in fields:
            fields["negotiation_date_source"] = "manual"
        rid = create_round_row(conn, drug_id, round_label=payload["round_label"], source="admin", **fields)
        if default_id:  # 기존 이벤트·공단 행은 전부 기본 차수에 남긴다 (새 차수로 옮길 것만 아래 명시 링크/관리 화면에서)
            for q in conn.execute("SELECT id FROM amjilsim_drug_queue_status WHERE drug_id = ?", (drug_id,)).fetchall():
                link_event_to_round(conn, q["id"], default_id, "legacy")
            if _nhis_has_round_col(conn):
                conn.execute("UPDATE nhis_negotiations SET round_id = ?, round_link_source = 'legacy' "
                             "WHERE drug_id = ? AND round_id IS NULL", (default_id, drug_id))
        for qid in payload.get("event_ids") or []:
            link_event_to_round(conn, int(qid), rid, "explicit")
        for nid in payload.get("nhis_ids") or []:
            link_nhis_to_round(conn, int(nid), rid, "explicit")
        for r in list_rounds(conn, drug_id):
            refresh_round_negotiation(conn, r["round_id"])
        conn.commit()
    return {"round_id": rid, "drug_id": drug_id, "default_round_id": default_id}


def update_round(round_id: int, payload: dict) -> dict:
    if not isinstance(payload, dict) or not payload:
        raise ValueError("수정할 필드 없음")
    sets, vals = [], []
    for key in _ROUND_FIELDS:
        if key in payload:
            sets.append(f"{key} = ?")
            vals.append(_validate_round_field(key, payload[key]))
    if not sets:
        raise ValueError(f"허용 필드 없음. 허용: {sorted(_ROUND_FIELDS)}")
    if "negotiation_status" in payload and "negotiation_date_source" not in payload:
        sets.append("negotiation_date_source = ?")
        vals.append("manual")
    with _connect() as conn:
        ensure_round_schema(conn)
        _get_round(conn, round_id)
        try:
            conn.execute(f"UPDATE reimb_listing_rounds SET {', '.join(sets)}, updated_at = datetime('now') "
                         f"WHERE round_id = ?", vals + [round_id])
        except sqlite3.IntegrityError as e:
            raise ValueError(f"무결성 제약 위반 (같은 브랜드 라벨 중복): {e}")
        conn.commit()
    return {"round_id": round_id, "updated_fields": len(sets)}


def delete_round(round_id: int) -> dict:
    """차수 삭제 — 링크만 해제(이벤트·공단 행 원본은 보존)."""
    with _connect() as conn:
        ensure_round_schema(conn)
        _get_round(conn, round_id)
        n_ev = conn.execute("DELETE FROM reimb_round_events WHERE round_id = ?", (round_id,)).rowcount
        n_nh = conn.execute("UPDATE nhis_negotiations SET round_id = NULL, round_link_source = NULL "
                            "WHERE round_id = ?", (round_id,)).rowcount if _nhis_has_round_col(conn) else 0
        conn.execute("DELETE FROM reimb_listing_rounds WHERE round_id = ?", (round_id,))
        conn.commit()
    return {"round_id": round_id, "unlinked_events": n_ev, "unlinked_nhis": n_nh}


def link_event_admin(round_id: int, payload: dict) -> dict:
    """POST rounds/<id>/events {event_id} — 이벤트를 차수에 명시 연결 (다른 브랜드 이벤트 = 병용 공유 링크)."""
    qid = (payload or {}).get("event_id")
    if not isinstance(qid, int):
        raise ValueError("event_id 정수 필수")
    with _connect() as conn:
        ensure_round_schema(conn)
        link_event_to_round(conn, qid, round_id, "explicit")
        conn.commit()
    return {"round_id": round_id, "event_id": qid}


def unlink_event_admin(round_id: int, event_id: int) -> dict:
    with _connect() as conn:
        ensure_round_schema(conn)
        n = conn.execute("DELETE FROM reimb_round_events WHERE round_id = ? AND queue_id = ?",
                         (round_id, event_id)).rowcount
        conn.commit()
    if n == 0:
        raise LookupError(f"연결 없음: round {round_id} ↔ event {event_id}")
    return {"round_id": round_id, "event_id": event_id}


def set_nhis_round_admin(nhis_id: int, payload: dict) -> dict:
    """PUT nhis/<id>/round {round_id|null} — 공단 행(+같은 협상 형제 행)을 차수에 명시 연결/해제."""
    rid = (payload or {}).get("round_id")
    if rid is not None and not isinstance(rid, int):
        raise ValueError("round_id 는 정수 또는 null")
    with _connect() as conn:
        ensure_round_schema(conn)
        before = conn.execute("SELECT round_id FROM nhis_negotiations WHERE id = ?", (nhis_id,)).fetchone()
        ids = link_nhis_to_round(conn, nhis_id, rid, "explicit")
        for r in {x for x in (rid, before["round_id"] if before else None) if x}:
            refresh_round_negotiation(conn, r)
        conn.commit()
    return {"nhis_id": nhis_id, "round_id": rid, "rows": ids}


# 모듈 로드 시 컬럼·차수 테이블 멱등 보강 (차수 함수 정의 이후에 호출해야 한다)
# REIMB_PIPELINE_NO_AUTOMIGRATE=1 이면 건너뛴다 — scripts/split_listing_rounds.py dry-run 이 프로덕션 DB 에 쓰지 않도록.
import os as _os

if _os.environ.get("REIMB_PIPELINE_NO_AUTOMIGRATE") != "1":
    try:
        _ensure_columns()
    except sqlite3.OperationalError:  # 읽기 전용 마운트 — 조회는 가능하다
        pass
