"""약제별 미디어 신호 momentum 집계 + journey/leaderboard — Access Insight S2.

가설(사용자 핵심 가설): 위원회(암질심/약평위) 세션 직전 특정 약제 관련 미디어 활동
(뉴스·국회·환자단체·학회 발언 등)이 밀집되면 등재 가능성이 높다. 이 모듈은 그 가설을
검증하기 위한 **momentum 지표**(신호밀도 × 최근성 × 신호유형 가중치)를 산출한다.

momentum_score 는 어디까지나 **참고 신호(likelihood signal)** 이며 확정 예측이
아니다 — `record_prediction`/`reconcile_predictions` 로 `amjilsim_prediction_audit` 에
예측↔실제 결과를 지속 대조해야 한다 (설계 문서 위험 섹션 참조).

READ-ONLY: amjilsim_media_signals / amjilsim_drugs / amjilsim_sessions /
analog_reports / indication_reimbursement / indications_master.
WRITE: amjilsim_prediction_audit (record_prediction / reconcile_predictions 만).

wall-clock 금지: 순수 집계 함수는 `as_of`/`today` 를 주입받아야 하며 내부에서
`datetime.now()` 를 호출하지 않는다 (호출측 미지정 시에만 데이터 안의 최신 날짜로
결정론적 fallback).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional, Union

PathLike = Union[str, Path]

BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = BASE_DIR / "data" / "db" / "drug_prices.db"

# amjilsim_media_signals.signal_type CHECK enum 중 QUEUE_INVENTORY 는 S5 신선크롤러
# 전용이라 백필 데이터엔 등장하지 않지만, by_type 브레이크다운에는 늘 6개 유형을
# 0 으로라도 표시해 프론트에서 안정적으로 렌더링할 수 있게 한다.
SIGNAL_TYPES: tuple[str, ...] = (
    "GOV_STATEMENT",
    "PATIENT_PETITION",
    "KOL_OPINION",
    "IR_RELEASE",
    "RESULT_REPORT",
    "PRE_AGENDA_LEAK",
)

# momentum_score 정규화 기준 — window_days 를 30일(1개월) 단위 평균 강도로 스케일해
# leaderboard 호출마다 window_days 파라미터가 달라져도 상대비교가 유지되게 한다.
# 공식: momentum_score = weighted_sum / (window_days / 30)
_NORMALIZE_DAYS = 30.0

# 예측 버킷 임계값 (momentum_score 기준) — 단순 규칙, ReviewAgent 다수결을 대체하지
# 않는다. 향후 슬라이스에서 reconcile_predictions 결과로 재조정 가능.
_PREDICT_HIGH = 3.0
_PREDICT_MEDIUM = 1.0

# session_imminent 판정 기준 (일).
_SESSION_IMMINENT_DAYS = 45


def _connect(db_path: PathLike) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _parse_date(value) -> Optional[date]:
    """'YYYY-MM-DD' (또는 그 뒤에 시간이 붙은) 문자열 → date. 파싱 불가 시 None."""
    if not value:
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    text = text[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _recency_factor(pub: date, ref: date, window_days: int) -> float:
    """ref(세션일 또는 as_of)에 가까울수록 1.0 에 근접하는 선형 감쇠 계수.

    - pub == ref  → 1.0 (가장 최근 = 가장 강한 신호)
    - pub == ref - window_days → 0.0 (윈도우 시작점)
    - 윈도우 밖(과거/미래) → 0.0 — 호출측에서 이미 윈도우로 필터링되지만 방어적으로 재확인.
    """
    days_before = (ref - pub).days
    if days_before < 0 or days_before > window_days:
        return 0.0
    if window_days <= 0:
        return 1.0
    return round(1.0 - (days_before / window_days), 4)


def _get_drug(conn: sqlite3.Connection, drug_id: int) -> Optional[sqlite3.Row]:
    return conn.execute(
        """
        SELECT drug_id, product_slug, brand_kr, brand_en, expected_session_id,
               amjilsim_pass_date, yakpyungwi_pass_date
        FROM amjilsim_drugs WHERE drug_id = ?
        """,
        (drug_id,),
    ).fetchone()


def _get_session(conn: sqlite3.Connection, session_id: Optional[int]) -> Optional[sqlite3.Row]:
    if session_id is None:
        return None
    return conn.execute(
        """
        SELECT session_id, session_date, committee_type, ordinal_official, status
        FROM amjilsim_sessions WHERE session_id = ?
        """,
        (session_id,),
    ).fetchone()


def _reference_date(
    conn: sqlite3.Connection,
    drug_row: sqlite3.Row,
    as_of: Optional[str],
) -> tuple[Optional[date], Optional[sqlite3.Row]]:
    """(reference_date, session_row) 결정.

    우선순위: ① expected_session_id 의 session_date 가 있으면 그것을 기준(다가올 세션
    직전 momentum 이 핵심 가설이므로). ② 없으면 as_of(주입값). ③ 그것도 없으면 해당
    약제의 최신 signal published_at (wall-clock 호출 금지 — 결정론적 fallback).
    """
    session_row = _get_session(conn, drug_row["expected_session_id"]) if drug_row else None
    if session_row is not None and session_row["session_date"]:
        return _parse_date(session_row["session_date"]), session_row

    if as_of:
        return _parse_date(as_of), session_row

    latest = conn.execute(
        "SELECT MAX(published_at) AS m FROM amjilsim_media_signals WHERE drug_id = ?",
        (drug_row["drug_id"],),
    ).fetchone()
    latest_date = _parse_date(latest["m"]) if latest else None
    return latest_date, session_row


def drug_momentum(
    drug_id: int,
    db_path: Optional[PathLike] = None,
    window_days: int = 90,
    as_of: Optional[str] = None,
) -> dict:
    """약제 하나의 momentum 지표를 산출.

    반환 필드:
    - signal_count: 윈도우 내 신호 수
    - weighted_sum: Σ(weight × recency_factor)
    - momentum_score: weighted_sum 을 window_days 기준 30일 단위로 정규화한 값
      (공식: weighted_sum / (window_days/30)) — window_days 가 달라도 비교 가능.
    - by_type: SIGNAL_TYPES 6종 카운트
    - engage_diversity: count>0 인 signal_type 종류 수 (참여 폭)
    - trend: 기준일 기준 최근 30일 vs 그 이전 30일 신호 수 비교
    """
    path = str(db_path or DEFAULT_DB_PATH)
    conn = _connect(path)
    try:
        drug_row = _get_drug(conn, drug_id)
        if drug_row is None:
            raise ValueError(f"unknown drug_id={drug_id}")

        ref_date, session_row = _reference_date(conn, drug_row, as_of)

        by_type = {t: 0 for t in SIGNAL_TYPES}
        weighted_sum = 0.0
        signal_count = 0
        recent_30d = 0
        prior_30d = 0

        if ref_date is not None:
            window_start = ref_date - timedelta(days=window_days)
            rows = conn.execute(
                "SELECT published_at, signal_type, weight FROM amjilsim_media_signals "
                "WHERE drug_id = ?",
                (drug_id,),
            ).fetchall()
            for r in rows:
                pub = _parse_date(r["published_at"])
                if pub is None or pub < window_start or pub > ref_date:
                    continue
                signal_count += 1
                stype = r["signal_type"] or ""
                if stype in by_type:
                    by_type[stype] += 1
                weight = r["weight"] if r["weight"] is not None else 1.0
                weighted_sum += weight * _recency_factor(pub, ref_date, window_days)

                days_before = (ref_date - pub).days
                if days_before <= 30:
                    recent_30d += 1
                elif days_before <= 60:
                    prior_30d += 1

        weighted_sum = round(weighted_sum, 4)
        momentum_score = (
            round(weighted_sum / (window_days / _NORMALIZE_DAYS), 4) if window_days else weighted_sum
        )
        engage_diversity = sum(1 for c in by_type.values() if c > 0)

        if recent_30d > prior_30d:
            direction = "up"
        elif recent_30d < prior_30d:
            direction = "down"
        else:
            direction = "flat"

        return {
            "drug_id": drug_id,
            "brand_kr": drug_row["brand_kr"],
            "product_slug": drug_row["product_slug"],
            "reference_date": ref_date.isoformat() if ref_date else None,
            "expected_session": (
                {
                    "session_id": session_row["session_id"],
                    "session_date": session_row["session_date"],
                    "committee_type": session_row["committee_type"],
                    "status": session_row["status"],
                }
                if session_row is not None
                else None
            ),
            "window_days": window_days,
            "signal_count": signal_count,
            "weighted_sum": weighted_sum,
            "momentum_score": momentum_score,
            "by_type": by_type,
            "engage_diversity": engage_diversity,
            "trend": {
                "recent_30d": recent_30d,
                "prior_30d": prior_30d,
                "direction": direction,
            },
        }
    finally:
        conn.close()


def leaderboard(
    db_path: Optional[PathLike] = None,
    window_days: int = 90,
    limit: int = 30,
    today: Optional[str] = None,
) -> list[dict]:
    """signal 이 있는 전체 약제의 momentum 을 계산해 momentum_score 내림차순 반환.

    `session_imminent`: expected_session_id 의 session_date 가 `today` 로부터
    0~45일 이내(다가오는 세션)면 True. `today` 미지정 시 데이터의 최신 signal 날짜로
    결정론적 fallback (wall-clock 호출 금지 — 테스트 재현성).
    """
    path = str(db_path or DEFAULT_DB_PATH)
    conn = _connect(path)
    try:
        drug_ids = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT drug_id FROM amjilsim_media_signals WHERE drug_id IS NOT NULL"
            ).fetchall()
        ]
        if today:
            today_date = _parse_date(today)
        else:
            latest = conn.execute("SELECT MAX(published_at) FROM amjilsim_media_signals").fetchone()
            today_date = _parse_date(latest[0]) if latest else None
    finally:
        conn.close()

    items = []
    for drug_id in drug_ids:
        m = drug_momentum(drug_id, db_path=path, window_days=window_days)
        session_imminent = False
        exp = m.get("expected_session")
        if exp and exp.get("session_date") and today_date:
            sess_date = _parse_date(exp["session_date"])
            if sess_date is not None:
                delta = (sess_date - today_date).days
                session_imminent = 0 <= delta <= _SESSION_IMMINENT_DAYS
        m["session_imminent"] = session_imminent
        items.append(m)

    items.sort(key=lambda d: d["momentum_score"], reverse=True)
    return items[:limit]


def list_drugs_with_signals(db_path: Optional[PathLike] = None) -> list[dict]:
    """signal 이 하나라도 있는 약제 목록 (picker UI 용) — signal_count 내림차순."""
    path = str(db_path or DEFAULT_DB_PATH)
    conn = _connect(path)
    try:
        rows = conn.execute(
            """
            SELECT d.drug_id AS drug_id, d.brand_kr AS brand_kr, COUNT(s.id) AS signal_count
            FROM amjilsim_drugs d
            JOIN amjilsim_media_signals s ON s.drug_id = d.drug_id
            GROUP BY d.drug_id, d.brand_kr
            ORDER BY signal_count DESC
            """
        ).fetchall()
        return [
            {"drug_id": r["drug_id"], "brand_kr": r["brand_kr"], "signal_count": r["signal_count"]}
            for r in rows
        ]
    finally:
        conn.close()


def journey(drug_id: int, db_path: Optional[PathLike] = None) -> dict:
    """약제 1건의 전체 journey(신호 + 위원회 세션 + 급여 마일스톤), 시간순 정렬."""
    path = str(db_path or DEFAULT_DB_PATH)
    conn = _connect(path)
    try:
        drug_row = _get_drug(conn, drug_id)
        if drug_row is None:
            raise ValueError(f"unknown drug_id={drug_id}")

        signal_rows = conn.execute(
            "SELECT published_at, signal_type, title, url, weight, outlet, session_id "
            "FROM amjilsim_media_signals WHERE drug_id = ? ORDER BY published_at ASC, id ASC",
            (drug_id,),
        ).fetchall()
        signals = [
            {
                "published_at": r["published_at"],
                "signal_type": r["signal_type"],
                "title": r["title"],
                "url": r["url"],
                "weight": r["weight"],
                "outlet": r["outlet"],
            }
            for r in signal_rows
        ]

        session_ids = {r["session_id"] for r in signal_rows if r["session_id"] is not None}
        if drug_row["expected_session_id"] is not None:
            session_ids.add(drug_row["expected_session_id"])

        sessions = []
        for sid in session_ids:
            srow = _get_session(conn, sid)
            if srow is not None:
                sessions.append(
                    {
                        "session_id": srow["session_id"],
                        "session_date": srow["session_date"],
                        "committee_type": srow["committee_type"],
                        "ordinal": srow["ordinal_official"],
                        "status": srow["status"],
                    }
                )
        sessions.sort(key=lambda s: s["session_date"] or "")

        milestones = {
            "amjilsim_pass_date": drug_row["amjilsim_pass_date"],
            "yakpyungwi_pass_date": drug_row["yakpyungwi_pass_date"],
            "mfds_permit_date": None,
            "first_reimbursement_date": None,
            "reimbursement_effective_date": None,
        }

        brand_kr = drug_row["brand_kr"] or ""
        if brand_kr:
            analog_row = conn.execute(
                """
                SELECT mfds_permit_date, first_reimbursement_date FROM analog_reports
                WHERE brand_name = ? OR brand_name LIKE ?
                ORDER BY mfds_permit_date ASC LIMIT 1
                """,
                (brand_kr, f"{brand_kr}%"),
            ).fetchone()
            if analog_row is not None:
                milestones["mfds_permit_date"] = analog_row["mfds_permit_date"]
                milestones["first_reimbursement_date"] = analog_row["first_reimbursement_date"]

        product_slug = drug_row["product_slug"]
        if product_slug:
            eff_row = conn.execute(
                """
                SELECT MIN(ir.effective_date) AS eff
                FROM indication_reimbursement ir
                JOIN indications_master im ON im.indication_id = ir.indication_id
                WHERE im.product = ? AND ir.is_reimbursed = 1 AND ir.effective_date IS NOT NULL
                """,
                (product_slug,),
            ).fetchone()
            if eff_row is not None and eff_row["eff"]:
                milestones["reimbursement_effective_date"] = eff_row["eff"]

        return {
            "drug_id": drug_id,
            "brand_kr": drug_row["brand_kr"],
            "product_slug": product_slug,
            "signals": signals,
            "sessions": sessions,
            "milestones": milestones,
        }
    finally:
        conn.close()


def _predicted_state(momentum_score: float) -> str:
    """momentum_score → HIGH/MEDIUM/LOW 버킷. 단순 임계값 규칙 — 확정 예측 아님."""
    if momentum_score >= _PREDICT_HIGH:
        return "HIGH"
    if momentum_score >= _PREDICT_MEDIUM:
        return "MEDIUM"
    return "LOW"


def record_prediction(
    drug_id: int,
    db_path: Optional[PathLike] = None,
    as_of: Optional[str] = None,
    window_days: int = 90,
) -> dict:
    """momentum → predicted_state 를 `amjilsim_prediction_audit` 에 기록.

    이것은 **가설 검증 루프의 기록 단계**일 뿐이다: momentum 은 참고 신호이지 확정
    예측이 아니며, 실제 결과 대조는 `reconcile_predictions()` 가 세션 COMPLETED 이후
    수행한다. 대조 대상 세션(expected_session_id)이 없는 약제는 기록하지 않는다
    (session_id 가 NOT NULL 제약).
    """
    path = str(db_path or DEFAULT_DB_PATH)
    momentum = drug_momentum(drug_id, db_path=path, window_days=window_days, as_of=as_of)
    session = momentum.get("expected_session")
    if session is None:
        raise ValueError(
            f"drug_id={drug_id} 에 expected_session_id 가 없어 prediction_audit 기록 불가"
        )

    predicted_state = _predicted_state(momentum["momentum_score"])
    pattern_hits = json.dumps(
        {k: v for k, v in momentum["by_type"].items() if v > 0}, ensure_ascii=False
    )
    notes = (
        "momentum 은 참고 신호(likelihood signal)이며 확정 예측이 아님. "
        f"engage_diversity={momentum['engage_diversity']}, trend={momentum['trend']['direction']}"
    )

    conn = _connect(path)
    try:
        cur = conn.execute(
            """
            INSERT INTO amjilsim_prediction_audit
                (session_id, drug_id, predicted_state, predicted_score, pattern_hits, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                session["session_id"],
                drug_id,
                predicted_state,
                momentum["momentum_score"],
                pattern_hits,
                notes,
            ),
        )
        conn.commit()
        return {
            "id": cur.lastrowid,
            "session_id": session["session_id"],
            "drug_id": drug_id,
            "predicted_state": predicted_state,
            "predicted_score": momentum["momentum_score"],
        }
    finally:
        conn.close()


def reconcile_predictions(db_path: Optional[PathLike] = None) -> dict:
    """세션이 COMPLETED 된 예측행에 대해 실제 결과와 대조해 actual_state/match_type 채움.

    실제 결과 소스: `amjilsim_drugs.yakpyungwi_pass_date` (해당 약제가 약평위를
    통과했는지 여부 — 값이 있으면 PASSED). 단순화: predicted_state=='HIGH' 를
    양성(positive) 클래스로 취급 (MEDIUM/LOW 는 '고가능성 아님'으로 묶음).
    - TRUE_POSITIVE : HIGH 예측 + 통과
    - FALSE_POSITIVE: HIGH 예측 + 미통과
    - FALSE_NEGATIVE: MEDIUM/LOW 예측 + 통과
    - TRUE_NEGATIVE : MEDIUM/LOW 예측 + 미통과
    이미 actual_state 가 채워진 행은 재처리하지 않는다(멱등).
    """
    path = str(db_path or DEFAULT_DB_PATH)
    conn = _connect(path)
    try:
        rows = conn.execute(
            """
            SELECT pa.id, pa.predicted_state, d.yakpyungwi_pass_date
            FROM amjilsim_prediction_audit pa
            JOIN amjilsim_sessions s ON s.session_id = pa.session_id
            JOIN amjilsim_drugs d ON d.drug_id = pa.drug_id
            WHERE pa.actual_state IS NULL AND s.status = 'COMPLETED'
            """
        ).fetchall()

        updated = 0
        for r in rows:
            passed = bool(r["yakpyungwi_pass_date"])
            actual_state = "PASSED" if passed else "NOT_PASSED"
            predicted_high = r["predicted_state"] == "HIGH"
            if predicted_high and passed:
                match_type = "TRUE_POSITIVE"
            elif predicted_high and not passed:
                match_type = "FALSE_POSITIVE"
            elif not predicted_high and passed:
                match_type = "FALSE_NEGATIVE"
            else:
                match_type = "TRUE_NEGATIVE"

            conn.execute(
                "UPDATE amjilsim_prediction_audit SET actual_state = ?, match_type = ? WHERE id = ?",
                (actual_state, match_type, r["id"]),
            )
            updated += 1

        conn.commit()
        return {"reconciled": updated}
    finally:
        conn.close()


if __name__ == "__main__":
    print(json.dumps(leaderboard(limit=10), ensure_ascii=False, indent=2, default=str))
