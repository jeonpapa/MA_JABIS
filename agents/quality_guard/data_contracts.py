"""데이터 계약(agents/rules/data_contracts.md) 불변식 — 매일 06:00 `review_codebase` 가 호출한다.

2026-10-17 일회성 정정(해외약가 강도·EV 병용 허가일·경쟁사 badge·Keytruda 급여 체크리스트)이 이후 스케줄 잡·스크레이퍼·
재빌드로 되돌아가거나 새 데이터에서 같은 버그가 재발하면 **값으로** 잡는다(코드 grep 이 아니라 저장 행 검사).

항목 (check → severity):
  - competitor_badge_not_allowed      : competitor_trend.badge ∉ ALLOWED_BADGES → ERROR
  - competitor_foreign_as_launch      : 비-manual 카드인데 결정론 가드가 '해외 승인' 으로 보는 행이 '신규 출시'/'급여 등재' → ERROR
  - mfds_later_indication_first_date  : 후속(FDA 기준 제품 최초 허가보다 1년+ 뒤) 적응증의 MFDS 허가일이 제품 MFDS 최초
                                        일자와 같고 raw_source != 'manual_correction' (EV 병용 1차 오매칭 재발) → WARNING.
                                        MFDS 행 combination_label 은 신뢰 불가(EV 행도 'monotherapy')라 조건에 쓰지 않는다
  - manual_correction_missing_date    : raw_source='manual_correction' MFDS/FDA 행의 approval_date 가 비어 있음 → WARNING
  - foreign_injection_route_as_oral   : priced 행 route='oral' 인데 행 단서(route_of_item)는 주사 → ERROR (Winrevair '45mg 경구')
  - foreign_continuation_fragment     : dosage_strength 가 ';' 등 구분자로 시작하는 조각 행이 formulation_key 'unmatched'
                                        또는 unit_strength NULL 로 priced → WARNING (UK MIMS '; 2-vial kit')
  - checklist_orphan                  : indication_reimbursement 행이 indications_master 에 없는 indication_id → WARNING

결과는 check 당 1건(건수·표본 id) — record_quality_review 가 내용 키(숫자 제거)로 원장 1행에 모은다(예산 ≤3건/일).
DB 는 read-only 로 연다. 테이블/컬럼이 없으면 그 항목만 건너뛴다.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from .deviations import _write_deviation

logger = logging.getLogger(__name__)

RULE = "data_contracts.md"
COMBO_LATER_DAYS = 365          # FDA 기준 제품 최초 허가보다 이만큼 뒤면 '후속' 적응증
SAMPLE = 8


def _table_cols(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _finding(check: str, severity: str, rule: str, ids: list, detail: str) -> dict:
    sample = ", ".join(str(i) for i in ids[:SAMPLE]) + (" …" if len(ids) > SAMPLE else "")
    return {"kind": "data", "contract": True, "check": check, "severity": severity, "rule": rule,
            "file": f"data_contract:{check}", "count": len(ids), "ids": list(ids),
            "detail": f"{len(ids)}건 — {detail} [표본: {sample}]"}


def _competitor(conn, findings: list) -> None:
    cols = _table_cols(conn, "competitor_trend")
    if not cols:
        return
    from agents.competitor_trend_quality import ALLOWED_BADGES, BADGE_FOREIGN, BADGE_LAUNCH, BADGE_LISTED, guard_badge
    st = "source_type" if "source_type" in cols else "NULL"
    rows = conn.execute(f"SELECT id, badge, headline, detail, {st} FROM competitor_trend").fetchall()
    bad = [r[0] for r in rows if (r[1] or "") not in ALLOWED_BADGES]
    if bad:
        findings.append(_finding("competitor_badge_not_allowed", "ERROR",
                                 f"{RULE} §4 — competitor_trend.badge 는 ALLOWED_BADGES 8종만", bad,
                                 "허용 밖 badge (LLM 출력·admin 입력 확인)"))
    foreign = [r[0] for r in rows
               if (r[4] or "") != "manual" and r[1] in (BADGE_LAUNCH, BADGE_LISTED)
               and guard_badge(r[2] or "", r[3] or "", r[1])[0] == BADGE_FOREIGN]
    if foreign:
        findings.append(_finding("competitor_foreign_as_launch", "ERROR",
                                 f"{RULE} §4 — 해외 허가 뉴스는 '해외 승인' ('신규 출시'·'급여 등재' 금지)", foreign,
                                 "결정론 가드가 해외 승인으로 보는 비-manual 카드 — "
                                 "scripts/reclassify_competitor_trends.py (dry-run → --apply)"))


def _indications(conn, findings: list) -> None:
    cols = _table_cols(conn, "indications_by_agency")
    if not cols or not _table_cols(conn, "indications_master"):
        return
    rows = conn.execute("""
        WITH mf AS (SELECT m.product, MIN(a.approval_date) d FROM indications_by_agency a
                    JOIN indications_master m ON m.indication_id = a.indication_id
                    WHERE a.agency='MFDS' AND COALESCE(a.approval_date,'') != '' GROUP BY m.product),
             ff AS (SELECT m.product, MIN(a.approval_date) d FROM indications_by_agency a
                    JOIN indications_master m ON m.indication_id = a.indication_id
                    WHERE a.agency='FDA' AND COALESCE(a.approval_date,'') != '' GROUP BY m.product)
        SELECT a.indication_id FROM indications_by_agency a
        JOIN indications_master m ON m.indication_id = a.indication_id
        JOIN mf ON mf.product = m.product
        JOIN ff ON ff.product = m.product
        JOIN indications_by_agency f ON f.indication_id = a.indication_id AND f.agency = 'FDA'
        WHERE a.agency = 'MFDS' AND a.approval_date = mf.d
          AND COALESCE(a.raw_source, '') != 'manual_correction'
          AND julianday(f.approval_date) - julianday(ff.d) >= ?
        ORDER BY a.indication_id""", (COMBO_LATER_DAYS,)).fetchall()
    if rows:
        findings.append(_finding("mfds_later_indication_first_date", "WARNING",
                                 f"{RULE} §2 — 후속 적응증 MFDS 허가일 ≠ 제품 최초 허가일 (segment 오매칭)",
                                 [r[0] for r in rows],
                                 "FDA 로는 후속인 적응증의 MFDS 일자가 제품 MFDS 최초 일자와 같다 — 변경이력 원문 확인 후 정정, "
                                 "맞으면 raw_source='manual_correction' 로 확정(검증 표시)"))
    empty = conn.execute("SELECT indication_id || ':' || agency FROM indications_by_agency "
                         "WHERE raw_source='manual_correction' AND agency IN ('MFDS','FDA') "
                         "AND COALESCE(approval_date,'') = '' ORDER BY 1").fetchall()
    if empty:
        findings.append(_finding("manual_correction_missing_date", "WARNING",
                                 f"{RULE} §2 — manual_correction 행은 날짜가 정본", [r[0] for r in empty],
                                 "수동 정정 행의 허가일이 비었다 (재빌드·병합 복원 실패 의심)"))


def _foreign_prices(conn, findings: list) -> None:
    cols = _table_cols(conn, "foreign_drug_prices")
    if not {"route", "formulation_key", "form_type"} <= cols:
        return
    from agents.foreign_dose_normalize import route_of_item
    rows = conn.execute(
        "SELECT id, country, query_name, product_name, dosage_strength, dosage_form, package_unit, form_type, "
        "raw_data, route, formulation_key, unit_strength_mg FROM foreign_drug_prices "
        "WHERE local_price IS NOT NULL").fetchall()
    keys = ("id", "country", "query_name", "product_name", "dosage_strength", "dosage_form", "package_unit",
            "form_type", "raw_data", "route", "formulation_key", "unit_strength_mg")
    as_oral, fragments = [], []
    for raw in rows:
        r = dict(zip(keys, raw))
        if (r["route"] or "") == "oral" and (r["form_type"] or "").lower() != "oral" \
                and route_of_item({k: r[k] for k in keys if k not in ("route",)}) == "injection":
            as_oral.append(f"{r['id']}({r['country']} {r['query_name']})")
        ds = (r["dosage_strength"] or "").lstrip()
        if ds[:1] in (";", ",", ":", "+", "&") and (
                r["unit_strength_mg"] is None or "unmatched" in str(r["formulation_key"] or "")):
            fragments.append(f"{r['id']}({r['country']} {r['query_name']})")
    if as_oral:
        findings.append(_finding("foreign_injection_route_as_oral", "ERROR",
                                 "foreign_agent_rules.md §투여경로 — 주사 단서 행을 경구 탭에 넣지 않는다", as_oral,
                                 "route='oral' 인데 행 텍스트는 주사 — scripts/repair_foreign_strength.py 로 재배정"))
    if fragments:
        findings.append(_finding("foreign_continuation_fragment", "WARNING",
                                 "foreign_agent_rules.md §강도 파싱 — 분할 조각 행은 직전 행 강도를 잇는다", fragments,
                                 "구분자로 시작하는 조각 행이 강도 없이 priced — 스크레이퍼 가격줄 분할 확인"))


def _checklist(conn, findings: list) -> None:
    if not _table_cols(conn, "indication_reimbursement") or not _table_cols(conn, "indications_master"):
        return
    orphans = conn.execute("SELECT r.indication_id FROM indication_reimbursement r LEFT JOIN indications_master m "
                           "ON m.indication_id = r.indication_id WHERE m.indication_id IS NULL ORDER BY 1").fetchall()
    if orphans:
        findings.append(_finding("checklist_orphan", "WARNING",
                                 f"{RULE} §1 — 급여 체크리스트는 현재 indication_id 에 붙어 있어야 한다",
                                 [r[0] for r in orphans],
                                 "마스터에 없는 indication_id (재빌드·병합으로 id 변경) — /admin/reimbursement 에서 옮겨 입력"))


CHECKS = (_competitor, _indications, _foreign_prices, _checklist)


def check_data_contracts(db_path: Path | str, *, write_deviation: bool = True) -> list[dict]:
    db_path = Path(db_path)
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as e:
        logger.warning("[QualityGuard] 데이터 계약 검사 — DB 열기 실패: %s", e)
        return []
    findings: list[dict] = []
    try:
        for fn in CHECKS:
            try:
                fn(conn, findings)
            except (sqlite3.Error, ImportError) as e:      # 한 항목 실패가 다른 항목을 막지 않는다
                logger.warning("[QualityGuard] 데이터 계약 %s 생략: %s", fn.__name__, e)
    finally:
        conn.close()
    if write_deviation:
        for f in findings:
            _write_deviation({
                "severity": f["severity"], "agent": "QualityGuard", "file": f["file"],
                "deviation_type": f["check"], "description": f"{f['rule']} → {f['detail']}",
                "corrective_action": "agents/rules/data_contracts.md 참조",
            })
    return findings
