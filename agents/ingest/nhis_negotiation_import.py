"""NHIS 약가협상 공개자료 적재 + amjilsim_drugs 매칭 교체.

원칙(project_nhis_negotiation_source):
  - nhis_negotiations 영구 아카이브: content_hash 멱등 INSERT(삭제 금지).
  - NHIS 공식 우선: 매칭 시 amjilsim_drugs.negotiation_status/완료일 자동 교체
    (negotiation_date_source='nhis_official'). 단, manual 로 수기 편집된 행은 보존 안 함 —
    NHIS 가 항상 우선이므로 nhis_official 로 덮어씀. (사용자 결정: NHIS 항상 우선)
  - 미매칭 행은 audit 리스트로 반환(자동 생성 안 함) → 수동 등록 대상.
  - 동일 약제에 협상중·완료 행 공존 시 '완료' 우선 적용(최종 상태).

매칭 보수성: brand_core 일치 AND 제조사 토큰 겹침일 때만 자동 적용. 그 외는 audit.

실행: python -m agents.ingest.nhis_negotiation_import [--dry-run]
"""
from __future__ import annotations

import argparse
import logging
import re
import sqlite3
from pathlib import Path

from agents.scrapers import nhis_negotiation

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "db" / "drug_prices.db"

# NHIS 결과 → negotiation_status (reimb_pipeline.NEGOTIATION_STATUSES)
_REJECT_RE = re.compile(r"결렬|불발|중단|철회")
_AGREE_RE = re.compile(r"합의|타결|완료")

# brand_core 추출 시 제거할 제형 접미어 (긴 것 우선)
_FORM_SUFFIXES = [
    "에어로스피어흡입제", "프리필드시린지", "흡입제", "주사액", "건조주사",
    "주사", "캡슐", "캅셀", "정", "주", "액", "시럽", "산", "겔", "크림",
]

# 제조사 정규화 시 제거 토큰
_MFR_NOISE_RE = re.compile(
    r"한국|\(주\)|\(유\)|㈜|㈜|유한회사|주식회사|코리아|korea|inc|ltd|co\.?|등|외\s*\d+\s*품목",
    re.IGNORECASE)

# 제조사 한↔영 표기 통일 (canonical, [keyword 들]). amjilsim 은 약식(GSK/BMS/AZ),
# NHIS 는 정식 한글(글락소스미스클라인 등) → 동일 회사 매칭 위해 정규화.
_MFR_ALIASES = [
    ("msd", ["엠에스디", "msd", "머크샤프"]),
    ("gsk", ["글락소스미스클라인", "glaxosmithkline", "gsk", "지에스케이"]),
    ("bms", ["비엠에스", "브리스톨마이어스", "브리스톨", "bristol", "bms"]),
    ("az", ["아스트라제네카", "astrazeneca", "az"]),
    ("ono", ["오노약품", "오노", "ono"]),
    ("lilly", ["릴리", "lilly", "eli"]),
    ("novartis", ["노바티스", "novartis"]),
    ("roche", ["로슈", "roche", "제넨텍"]),
    ("janssen", ["얀센", "janssen"]),
    ("sanofi", ["사노피", "sanofi"]),
    ("abbvie", ["애브비", "abbvie"]),
    ("amgen", ["암젠", "amgen"]),
    ("biogen", ["바이오젠", "biogen"]),
    ("gilead", ["길리어드", "gilead"]),
    ("takeda", ["다케다", "takeda"]),
    ("merck-de", ["머크", "merck"]),
    ("boehringer", ["베링거", "boehringer"]),
    ("astellas", ["아스텔라스", "astellas"]),
    ("tanabe", ["미쓰비시다나베", "타나베", "mitsubishi", "tanabe"]),
]


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """런타임 멱등 가드 — 마이그레이션 미적용 환경(프로덕션 재시드) 안전망."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS nhis_negotiations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            list_type TEXT NOT NULL, product_name TEXT NOT NULL,
            manufacturer TEXT, efficacy_group TEXT, registered_ym TEXT,
            result TEXT, completed_ym TEXT, source_url TEXT NOT NULL,
            content_hash TEXT NOT NULL, drug_id INTEGER,
            first_seen_at TEXT DEFAULT (datetime('now')),
            fetched_at TEXT DEFAULT (datetime('now')),
            UNIQUE(content_hash))""")
    for stmt in (
        "CREATE INDEX IF NOT EXISTS idx_nhis_list_type ON nhis_negotiations(list_type)",
        "CREATE INDEX IF NOT EXISTS idx_nhis_completed ON nhis_negotiations(completed_ym)",
        "CREATE INDEX IF NOT EXISTS idx_nhis_product ON nhis_negotiations(product_name)",
        "CREATE INDEX IF NOT EXISTS idx_nhis_drug ON nhis_negotiations(drug_id)",
    ):
        conn.execute(stmt)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(amjilsim_drugs)")}
    for col in ("negotiation_complete_date", "negotiation_date_source",
                "nhis_registered_ym", "efficacy_group"):
        if col not in cols:
            conn.execute(f"ALTER TABLE amjilsim_drugs ADD COLUMN {col} TEXT")
    conn.commit()


def _result_to_status(result: str, is_in_progress: bool) -> str:
    if _REJECT_RE.search(result):
        return "REJECTED"
    if is_in_progress:
        return "IN_PROGRESS"
    if _AGREE_RE.search(result):
        return "AGREED"
    # 완료연월은 있으나 결과 문구 불명확 → 보수적으로 AGREED (등재 진행)
    return "AGREED"


def _brand_core(name: str) -> str:
    """제품명 → 브랜드 코어. '키트루다주(펨...)_(0.1g/4mL)' → '키트루다'."""
    s = name.split("(")[0].split("_")[0]
    # 첫 공백/숫자 앞까지 (함량·외 N품목 제거)
    s = re.split(r"[\s\d]", s, 1)[0]
    s = s.strip()
    for suf in _FORM_SUFFIXES:
        if s.endswith(suf) and len(s) > len(suf):
            s = s[: -len(suf)]
            break
    return s.strip()


def _mfr_core(name: str | None) -> str:
    if not name:
        return ""
    s = _MFR_NOISE_RE.sub("", name)
    s = re.sub(r"[·,/\s\(\)]", "", s).strip().lower()
    if not s:
        return ""
    for canonical, kws in _MFR_ALIASES:
        if any(kw in s for kw in kws):
            return canonical
    return s


def _brand_match(nhis_core: str, drug_brand: str) -> bool:
    a = nhis_core
    b = _brand_core(drug_brand)
    if not a or not b or len(a) < 2 or len(b) < 2:
        return False
    if a == b:
        return True
    # prefix 매칭은 3자 이상 공유 시만 (2자 접두 오탐 방지: 보노칸≠보노엠)
    return min(len(a), len(b)) >= 3 and (a.startswith(b) or b.startswith(a))


def _mfr_relation(nhis_mfr: str, drug_mfr: str | None) -> str:
    """'MATCH' | 'UNKNOWN'(한쪽 비거나 정규화 실패) | 'CONFLICT'(둘 다 알지만 다름)."""
    a, b = _mfr_core(nhis_mfr), _mfr_core(drug_mfr)
    if not a or not b:
        return "UNKNOWN"
    if a == b or a in b or b in a:
        return "MATCH"
    return "CONFLICT"


def upsert_negotiations(conn: sqlite3.Connection, rows: list[dict]) -> int:
    """content_hash 멱등 INSERT. 기존 행은 fetched_at 갱신만. 반환: 신규 INSERT 수."""
    inserted = 0
    for r in rows:
        dup = conn.execute(
            "SELECT id FROM nhis_negotiations WHERE content_hash = ?",
            (r["content_hash"],)).fetchone()
        if dup:
            conn.execute(
                "UPDATE nhis_negotiations SET fetched_at = datetime('now') WHERE id = ?",
                (dup[0],))
            continue
        conn.execute(
            """INSERT INTO nhis_negotiations
               (list_type, product_name, manufacturer, efficacy_group,
                registered_ym, result, completed_ym, source_url, content_hash)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (r["list_type"], r["product_name"], r["manufacturer"],
             r["efficacy_group"], r["registered_ym"], r["result"],
             r["completed_ym"], r["source_url"], r["content_hash"]))
        inserted += 1
    conn.commit()
    return inserted


def match_and_apply(conn: sqlite3.Connection, rows: list[dict],
                    dry_run: bool = False) -> dict:
    """NHIS 행 ↔ amjilsim_drugs 매칭. 매칭 시 NHIS 공식값으로 교체.

    동일 drug 에 복수 NHIS 행 매칭 시 **최신 등록연월(가장 최근 협상)** 행이 현재 상태를
    결정한다. 과거 완료 건이 있어도 더 최근에 새 적응증(확대 등)으로 협상 중이면
    그 약제는 '협상 중'(IN_PROGRESS) — 보드에 노출되어야 한다.
    (예: 키트루다 확대 2025-10 완료 + 확대 2026-05 진행중 → 현재 협상중.)"""
    drugs = conn.execute(
        "SELECT drug_id, brand_kr, manufacturer FROM amjilsim_drugs").fetchall()

    # drug_id → 적용 후보 (최신 등록연월 = 현재 상태)
    apply_map: dict[int, dict] = {}
    matched_hashes: dict[str, int] = {}
    audit: list[dict] = []

    for r in rows:
        ncore = _brand_core(r["product_name"])
        # 1) 브랜드 매칭 후보 수집 → 제조사 관계로 confidence 판정
        brand_cands = [d for d in drugs if _brand_match(ncore, d["brand_kr"])]
        cand = None
        for d in brand_cands:
            rel = _mfr_relation(r["manufacturer"], d["manufacturer"])
            if rel == "MATCH":
                cand = d
                break
            if rel == "UNKNOWN" and cand is None:
                cand = d  # 제조사 불명확 — 브랜드 distinctive 하므로 잠정 채택(MATCH 우선)
        # CONFLICT 만(브랜드 일치하나 제조사 명백히 다름) → audit
        if cand is None:
            audit.append(r)
            continue
        did = cand["drug_id"]
        matched_hashes[r["content_hash"]] = did
        prev = apply_map.get(did)
        # 최신 등록연월(가장 최근 협상)이 현재 상태를 결정. 동일 연월이면 진행중 우선
        # (현재 활성 협상이 최종 상태보다 우선 — 보드 노출 보장).
        better = (
            prev is None
            or (r["registered_ym"] or "") > (prev["registered_ym"] or "")
            or ((r["registered_ym"] or "") == (prev["registered_ym"] or "")
                and r["is_in_progress"] and not prev["is_in_progress"])
        )
        if better:
            apply_map[did] = r

    if dry_run:
        return {"matched_drugs": len(apply_map), "matched_rows": len(matched_hashes),
                "unmatched": audit, "apply_map": apply_map}

    # nhis_negotiations.drug_id 백필
    for h, did in matched_hashes.items():
        conn.execute("UPDATE nhis_negotiations SET drug_id = ? WHERE content_hash = ?",
                     (did, h))

    # amjilsim_drugs 교체 (NHIS 공식 우선)
    for did, r in apply_map.items():
        status = _result_to_status(r["result"], r["is_in_progress"])
        conn.execute(
            """UPDATE amjilsim_drugs SET
                 negotiation_status = ?,
                 negotiation_complete_date = ?,
                 negotiation_date_source = 'nhis_official',
                 nhis_registered_ym = ?,
                 efficacy_group = ?
               WHERE drug_id = ?""",
            (status, r["completed_ym"], r["registered_ym"],
             r["efficacy_group"], did))
    conn.commit()

    return {"matched_drugs": len(apply_map), "matched_rows": len(matched_hashes),
            "unmatched": audit, "apply_map": apply_map}


def run(dry_run: bool = False, db_path: Path = DB_PATH) -> dict:
    rows = nhis_negotiation.fetch_all()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        _ensure_schema(conn)
        inserted = 0 if dry_run else upsert_negotiations(conn, rows)
        res = match_and_apply(conn, rows, dry_run=dry_run)
        out = {
            "fetched": len(rows),
            "inserted": inserted,
            "matched_drugs": res["matched_drugs"],
            "matched_rows": res["matched_rows"],
            "unmatched_count": len(res["unmatched"]),
            "unmatched": res["unmatched"],
        }
        return out
    finally:
        conn.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="DB 변경 없이 매칭만 확인")
    args = p.parse_args()
    res = run(dry_run=args.dry_run)
    print(f"\n{'[DRY-RUN] ' if args.dry_run else ''}NHIS 적재 결과")
    print(f"  수집 {res['fetched']}  |  신규 INSERT {res['inserted']}")
    print(f"  매칭: drug {res['matched_drugs']}개 / row {res['matched_rows']}건")
    print(f"  미매칭(audit): {res['unmatched_count']}건")
    for r in res["unmatched"][:30]:
        flag = "협상중" if r["is_in_progress"] else f"완료{r['completed_ym']}"
        print(f"    - [{r['list_type']}] {r['product_name'][:34]} | {r['manufacturer']} | {flag}")


if __name__ == "__main__":
    main()
