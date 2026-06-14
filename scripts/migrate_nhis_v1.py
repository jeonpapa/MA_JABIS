"""
NHIS 약가협상 v1 DB 마이그레이션 — drug_prices.db에 nhis_negotiations 테이블 추가
+ amjilsim_drugs 협상결과 보강 컬럼 4종.

소스: 건강보험공단 공개자료
  - 신약        https://www.nhis.or.kr/nhis/together/retrieveMediList.do  → list_type='신규'
  - 사용범위확대 https://www.nhis.or.kr/nhis/together/retrieveMediList2.do → list_type='확대'

원칙(project_nhis_negotiation_source 메모리):
  - nhis_negotiations 는 원시 영구 아카이브. 등록 후 1년만 공개되므로 삭제 금지, content_hash 멱등 UPSERT.
  - NHIS 공식이 항상 우선 — 매칭 시 amjilsim_drugs.negotiation_status/완료일 자동 교체
    (negotiation_date_source='nhis_official'). 미매칭은 audit 후 수동 등록.
  - 협상완료연월(completed_ym) 빈 행 = 협상중(보드 노출), 채워진 행 = 완료(보드 제외).

실행
----
    python -m scripts.migrate_nhis_v1                # CREATE/ALTER IF NOT EXISTS (멱등)
    python -m scripts.migrate_nhis_v1 --rollback    # DROP nhis_negotiations (개발 전용)

설계 원칙
---------
- 기존 drug_prices.db 변경은 IF NOT EXISTS 가드 + ALTER 멱등(PRAGMA 체크) 위주.
- WAL 모드 유지. drug_id 는 soft FK (CHECK 없음) — 미매칭 NULL 허용.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


DB_PATH = Path(__file__).resolve().parents[1] / "data" / "db" / "drug_prices.db"


# ─────────────────────────────────────────────────────────────────────────────
# DDL
# ─────────────────────────────────────────────────────────────────────────────

DDL = [
    """
    CREATE TABLE IF NOT EXISTS nhis_negotiations (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        list_type       TEXT NOT NULL CHECK (list_type IN ('신규','확대')),
        product_name    TEXT NOT NULL,
        manufacturer    TEXT,
        efficacy_group  TEXT,
        registered_ym   TEXT,              -- 등록연월 'YYYY-MM'
        result          TEXT,              -- 협상결과 (합의/결렬/진행 등 원문)
        completed_ym    TEXT,              -- 협상완료연월 'YYYY-MM' (NULL/'' = 협상중)
        source_url      TEXT NOT NULL,
        content_hash    TEXT NOT NULL,     -- 멱등 UPSERT 키 (행 내용 해시)
        drug_id         INTEGER REFERENCES amjilsim_drugs(drug_id),  -- 매칭 시, soft FK
        first_seen_at   TEXT DEFAULT (datetime('now')),
        fetched_at      TEXT DEFAULT (datetime('now')),
        UNIQUE(content_hash)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_nhis_list_type ON nhis_negotiations(list_type)",
    "CREATE INDEX IF NOT EXISTS idx_nhis_completed ON nhis_negotiations(completed_ym)",
    "CREATE INDEX IF NOT EXISTS idx_nhis_product ON nhis_negotiations(product_name)",
    "CREATE INDEX IF NOT EXISTS idx_nhis_drug ON nhis_negotiations(drug_id)",
]


# amjilsim_drugs 보강 컬럼 (멱등 ALTER) — (컬럼명, 타입)
AMJILSIM_NEW_COLS = [
    ("negotiation_complete_date", "TEXT"),   # 협상완료연월 'YYYY-MM' (NHIS 공식)
    ("negotiation_date_source", "TEXT"),     # 'nhis_official' | 'manual'
    ("nhis_registered_ym", "TEXT"),          # 공단 등록연월 'YYYY-MM'
    ("efficacy_group", "TEXT"),              # 공단 효능군
]


ROLLBACK_TABLES = ["nhis_negotiations"]


def run_migrate(db_path: Path, rollback: bool = False) -> None:
    if not db_path.exists():
        print(f"⚠️  DB 없음: {db_path}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    cur = conn.cursor()

    if rollback:
        print("🔻 ROLLBACK — nhis_negotiations DROP (amjilsim_drugs 컬럼은 보존)")
        for t in ROLLBACK_TABLES:
            cur.execute(f"DROP TABLE IF EXISTS {t}")
        conn.commit()
        print("✅ rollback 완료")
        conn.close()
        return

    print(f"🔨 CREATE/ALTER — {db_path}")
    for stmt in DDL:
        cur.execute(stmt)
    conn.commit()
    print(f"✅ DDL 적용 완료 ({len(DDL)} statements)")

    # amjilsim_drugs 멱등 ALTER
    cols_now = {r[1] for r in cur.execute("PRAGMA table_info(amjilsim_drugs)")}
    added = 0
    for col, typ in AMJILSIM_NEW_COLS:
        if col not in cols_now:
            cur.execute(f"ALTER TABLE amjilsim_drugs ADD COLUMN {col} {typ}")
            added += 1
            print(f"   + amjilsim_drugs.{col} ({typ})")
    conn.commit()
    print(f"✅ amjilsim_drugs 컬럼 {added}개 추가 (기존 {len(AMJILSIM_NEW_COLS) - added}개 존재)")

    # verify
    n = cur.execute("SELECT COUNT(*) FROM nhis_negotiations").fetchone()[0]
    print(f"📊 nhis_negotiations rows: {n:,}")

    conn.close()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--rollback", action="store_true", help="DROP nhis_negotiations")
    p.add_argument("--db", type=Path, default=DB_PATH, help="DB path override")
    args = p.parse_args()
    run_migrate(args.db, rollback=args.rollback)


if __name__ == "__main__":
    main()
