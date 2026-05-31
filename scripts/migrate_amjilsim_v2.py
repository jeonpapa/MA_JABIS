"""
amjilsim_tracker v2 DB 마이그레이션 — v1 schema 위에 위원회 분리·신호 attribution 검증 컬럼 추가.

Plan v3 반영:
- 위원회 2개 분리 (AMJILSIM + YAKPYUNGWI)
- 약평위 약물 추적 priority 분류 (msd_asset / competitor_class / generic_new_drug)
- 매체 신호 fact attribution 검증 (source_verified, raw_html_path)
- 약평위 12차 + 암질심 5차(7/8) seed update

실행
----
    python -m scripts.migrate_amjilsim_v2                   # ALTER TABLE + seed
    python -m scripts.migrate_amjilsim_v2 --rollback        # 추가 컬럼 DROP (개발용)
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


DB_PATH = Path(__file__).resolve().parents[1] / "data" / "db" / "drug_prices.db"


# ─────────────────────────────────────────────────────────────────────────────
# v2 ALTER TABLE statements (idempotent — column 존재 시 skip)
# ─────────────────────────────────────────────────────────────────────────────

V2_COLUMNS = [
    # (table, column_name, DDL)
    ("amjilsim_sessions", "committee_type",
     "ALTER TABLE amjilsim_sessions ADD COLUMN committee_type TEXT NOT NULL DEFAULT 'AMJILSIM' "
     "CHECK (committee_type IN ('AMJILSIM','YAKPYUNGWI'))"),

    ("amjilsim_drug_queue_status", "committee_type",
     "ALTER TABLE amjilsim_drug_queue_status ADD COLUMN committee_type TEXT NOT NULL DEFAULT 'AMJILSIM' "
     "CHECK (committee_type IN ('AMJILSIM','YAKPYUNGWI'))"),

    ("amjilsim_drugs", "tracking_priority",
     "ALTER TABLE amjilsim_drugs ADD COLUMN tracking_priority TEXT NOT NULL DEFAULT 'generic_new_drug' "
     "CHECK (tracking_priority IN ('msd_asset','competitor_class','generic_new_drug'))"),

    ("amjilsim_media_signals", "committee_target",
     "ALTER TABLE amjilsim_media_signals ADD COLUMN committee_target TEXT NOT NULL DEFAULT 'UNKNOWN' "
     "CHECK (committee_target IN ('AMJILSIM','YAKPYUNGWI','UNKNOWN'))"),

    ("amjilsim_media_signals", "source_verified",
     "ALTER TABLE amjilsim_media_signals ADD COLUMN source_verified TEXT NOT NULL DEFAULT 'headline_only' "
     "CHECK (source_verified IN ('body_verified','snippet_match','headline_only'))"),

    ("amjilsim_media_signals", "raw_html_path",
     "ALTER TABLE amjilsim_media_signals ADD COLUMN raw_html_path TEXT"),

    # 암질심 통과 → 약평위 transition 추적 (logic 4)
    ("amjilsim_drugs", "amjilsim_pass_date",
     "ALTER TABLE amjilsim_drugs ADD COLUMN amjilsim_pass_date DATE"),

    ("amjilsim_drugs", "yakpyungwi_pass_date",
     "ALTER TABLE amjilsim_drugs ADD COLUMN yakpyungwi_pass_date DATE"),

    ("amjilsim_drugs", "negotiation_status",
     "ALTER TABLE amjilsim_drugs ADD COLUMN negotiation_status TEXT "
     "CHECK (negotiation_status IS NULL OR negotiation_status IN "
     "('NONE','IN_PROGRESS','STALLED','AGREED','REJECTED'))"),
]

# 신규 인덱스 (idempotent)
V2_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_as_committee ON amjilsim_sessions(committee_type)",
    "CREATE INDEX IF NOT EXISTS idx_aq_committee_state ON amjilsim_drug_queue_status(committee_type, queue_state)",
    "CREATE INDEX IF NOT EXISTS idx_ms_committee_target ON amjilsim_media_signals(committee_target)",
    "CREATE INDEX IF NOT EXISTS idx_ms_source_verified ON amjilsim_media_signals(source_verified)",
    "CREATE INDEX IF NOT EXISTS idx_ad_priority ON amjilsim_drugs(tracking_priority)",
    "CREATE INDEX IF NOT EXISTS idx_ad_amjilsim_pass ON amjilsim_drugs(amjilsim_pass_date)",
]

ROLLBACK_COLUMNS = [
    # SQLite DROP COLUMN 지원 (3.35+)
    ("amjilsim_sessions", "committee_type"),
    ("amjilsim_drug_queue_status", "committee_type"),
    ("amjilsim_drugs", "tracking_priority"),
    ("amjilsim_drugs", "amjilsim_pass_date"),
    ("amjilsim_drugs", "yakpyungwi_pass_date"),
    ("amjilsim_drugs", "negotiation_status"),
    ("amjilsim_media_signals", "committee_target"),
    ("amjilsim_media_signals", "source_verified"),
    ("amjilsim_media_signals", "raw_html_path"),
]


# ─────────────────────────────────────────────────────────────────────────────
# Seed 데이터 v2
# ─────────────────────────────────────────────────────────────────────────────

# 약평위 12차 (매월 첫 목요일, 사용자 확정)
# 1~5차 = 종료, 6~12차 = 예정
SEED_YAKPYUNGWI_2026 = [
    # (year, ordinal_assumed, session_date, status, committee_type, note)
    (2026, 1,  "2026-01-15", "COMPLETED",  "YAKPYUNGWI", None),
    (2026, 2,  "2026-02-05", "COMPLETED",  "YAKPYUNGWI", None),
    (2026, 3,  "2026-03-05", "COMPLETED",  "YAKPYUNGWI", None),
    (2026, 4,  "2026-04-02", "COMPLETED",  "YAKPYUNGWI", None),
    (2026, 5,  "2026-05-07", "COMPLETED",  "YAKPYUNGWI", None),
    (2026, 6,  "2026-06-04", "SCHEDULED",  "YAKPYUNGWI",
     "옵션 A 첫 매뉴얼 D-2 라이브 후보 (6/2)."),
    (2026, 7,  "2026-07-02", "SCHEDULED",  "YAKPYUNGWI",
     "옵션 B 첫 자동 D-2 라이브 후보 (6/30)."),
    (2026, 8,  "2026-08-06", "SCHEDULED",  "YAKPYUNGWI", None),
    (2026, 9,  "2026-09-03", "SCHEDULED",  "YAKPYUNGWI", None),
    (2026, 10, "2026-10-01", "SCHEDULED",  "YAKPYUNGWI", None),
    (2026, 11, "2026-11-05", "SCHEDULED",  "YAKPYUNGWI", None),
    (2026, 12, "2026-12-03", "SCHEDULED",  "YAKPYUNGWI", None),
]

# 암질심 1~3차 종료 처리 (v1 SEED에서 SCHEDULED로 두었음)
AMJILSIM_COMPLETED_UPDATE = [
    "UPDATE amjilsim_sessions SET status='COMPLETED' WHERE session_date IN "
    "('2026-01-21','2026-03-04','2026-04-15') AND committee_type='AMJILSIM'",
]

# MSD 자산 tracking_priority 갱신
MSD_PRIORITY_UPDATE = [
    "UPDATE amjilsim_drugs SET tracking_priority='msd_asset' WHERE msd_flag=1",
]


# ─────────────────────────────────────────────────────────────────────────────
def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def run_migrate(db_path: Path, rollback: bool = False) -> None:
    if not db_path.exists():
        print(f"⚠️  DB 없음: {db_path}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    cur = conn.cursor()

    if rollback:
        print("🔻 ROLLBACK — v2 추가 컬럼 DROP")
        for table, column in ROLLBACK_COLUMNS:
            if column_exists(conn, table, column):
                try:
                    cur.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
                    print(f"  ✓ {table}.{column} dropped")
                except sqlite3.OperationalError as e:
                    print(f"  ⚠️  {table}.{column} drop failed: {e}")
        conn.commit()
        print("✅ rollback 완료")
        return

    print(f"🔨 v2 MIGRATION — {db_path}")
    print()

    print("▶ ALTER TABLE — 신규 컬럼 추가")
    for table, column, ddl in V2_COLUMNS:
        if column_exists(conn, table, column):
            print(f"  ⏭️  {table}.{column} (이미 존재, skip)")
            continue
        cur.execute(ddl)
        print(f"  ✓ {table}.{column} added")
    conn.commit()

    print()
    print("▶ 인덱스 생성")
    for idx_ddl in V2_INDEXES:
        cur.execute(idx_ddl)
        idx_name = idx_ddl.split(" ON ")[0].split()[-1]
        print(f"  ✓ {idx_name}")
    conn.commit()

    print()
    print("▶ SEED — 약평위 12차")
    cur.executemany(
        "INSERT OR IGNORE INTO amjilsim_sessions "
        "(year, ordinal_assumed, session_date, status, committee_type, note) "
        "VALUES (?,?,?,?,?,?)",
        SEED_YAKPYUNGWI_2026,
    )
    inserted = cur.rowcount
    print(f"  ✓ 약평위 12차 INSERT (newly inserted: {inserted})")

    print()
    print("▶ UPDATE — 암질심 1~3차 status='COMPLETED'")
    for stmt in AMJILSIM_COMPLETED_UPDATE:
        cur.execute(stmt)
        print(f"  ✓ {cur.rowcount} rows updated")
    conn.commit()

    print()
    print("▶ UPDATE — MSD 자산 tracking_priority='msd_asset'")
    for stmt in MSD_PRIORITY_UPDATE:
        cur.execute(stmt)
        print(f"  ✓ {cur.rowcount} rows updated")
    conn.commit()

    print()
    print("📊 통계")
    counts = [
        ("amjilsim_sessions (전체)", "SELECT COUNT(*) FROM amjilsim_sessions"),
        ("amjilsim_sessions AMJILSIM", "SELECT COUNT(*) FROM amjilsim_sessions WHERE committee_type='AMJILSIM'"),
        ("amjilsim_sessions YAKPYUNGWI", "SELECT COUNT(*) FROM amjilsim_sessions WHERE committee_type='YAKPYUNGWI'"),
        ("amjilsim_drugs msd_asset", "SELECT COUNT(*) FROM amjilsim_drugs WHERE tracking_priority='msd_asset'"),
    ]
    for label, q in counts:
        n = cur.execute(q).fetchone()[0]
        print(f"  {label:35s} {n:>4}")

    conn.close()
    print()
    print("✅ v2 migration 완료")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--rollback", action="store_true")
    p.add_argument("--db", type=Path, default=DB_PATH)
    args = p.parse_args()
    run_migrate(args.db, rollback=args.rollback)


if __name__ == "__main__":
    main()
