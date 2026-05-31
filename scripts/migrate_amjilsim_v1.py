"""
amjilsim_tracker v1 DB 마이그레이션 — drug_prices.db에 7개 테이블 추가.

대상 위원회: 중증(암)질환심의위원회(암질심).
약제급여평가위원회(약평위)는 별개 위원회로 본 schema 적용 밖.

실행
----
    python -m scripts.migrate_amjilsim_v1                   # CREATE IF NOT EXISTS
    python -m scripts.migrate_amjilsim_v1 --rollback        # DROP TABLE IF EXISTS (개발 전용)
    python -m scripts.migrate_amjilsim_v1 --seed            # 2026 차수 + MSD 5개 자산 seed

설계 원칙
---------
- 기존 drug_prices.db는 1.2GB. 변경은 IF NOT EXISTS 가드 + append-only 위주.
- WAL 모드 유지. 외래키는 soft join (CHECK constraint 없음) — indications_master.product slug 호환.
- amjilsim_drug_queue_status는 append-only audit. UPDATE 금지, 새 row INSERT만.
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
    CREATE TABLE IF NOT EXISTS amjilsim_sessions (
        session_id           INTEGER PRIMARY KEY AUTOINCREMENT,
        year                 INTEGER NOT NULL,
        ordinal_assumed      INTEGER NOT NULL,
        ordinal_official     INTEGER,
        session_date         DATE NOT NULL UNIQUE,
        status               TEXT NOT NULL DEFAULT 'SCHEDULED',
        official_minutes_url TEXT,
        note                 TEXT,
        created_at           TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS amjilsim_drugs (
        drug_id          INTEGER PRIMARY KEY AUTOINCREMENT,
        product_slug     TEXT,
        brand_kr         TEXT NOT NULL,
        brand_en         TEXT,
        ingredient_inn   TEXT,
        atc              TEXT,
        manufacturer     TEXT,
        msd_flag         INTEGER NOT NULL DEFAULT 0,
        competitor_class TEXT,
        first_seen_at    TEXT DEFAULT (datetime('now')),
        UNIQUE(brand_kr, ingredient_inn)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS amjilsim_drug_queue_status (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        drug_id          INTEGER NOT NULL REFERENCES amjilsim_drugs(drug_id),
        session_id       INTEGER REFERENCES amjilsim_sessions(session_id),
        queue_state      TEXT NOT NULL
            CHECK (queue_state IN ('QUEUE_PENDING','QUEUE_PROCESSED',
                                   'APPROVED','REJECTED_REQUEUE','WITHDRAWN')),
        queue_entry_date DATE,
        n_th_attempt     INTEGER NOT NULL DEFAULT 1,
        evidence_url     TEXT,
        observed_at      TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS amjilsim_media_signals (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        drug_id         INTEGER REFERENCES amjilsim_drugs(drug_id),
        session_id      INTEGER REFERENCES amjilsim_sessions(session_id),
        tier            TEXT NOT NULL CHECK (tier IN ('A','B','D','G')),
        outlet          TEXT NOT NULL,
        url             TEXT NOT NULL,
        title           TEXT,
        published_at    TEXT,
        snippet         TEXT,
        signal_type     TEXT CHECK (signal_type IN
            ('PRE_AGENDA_LEAK','QUEUE_INVENTORY','IR_RELEASE','GOV_STATEMENT',
             'PATIENT_PETITION','KOL_OPINION','RESULT_REPORT')),
        signal_phrases  TEXT,
        crossref_count  INTEGER NOT NULL DEFAULT 1,
        weight          REAL NOT NULL DEFAULT 1.0,
        crawled_at      TEXT DEFAULT (datetime('now')),
        UNIQUE(outlet, url)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS amjilsim_prediction_audit (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id       INTEGER NOT NULL REFERENCES amjilsim_sessions(session_id),
        drug_id          INTEGER NOT NULL REFERENCES amjilsim_drugs(drug_id),
        predicted_state  TEXT NOT NULL,
        predicted_score  REAL,
        actual_state     TEXT,
        match_type       TEXT CHECK (match_type IN
            ('TRUE_POSITIVE','FALSE_POSITIVE','TRUE_NEGATIVE','FALSE_NEGATIVE')),
        pattern_hits     TEXT,
        notes            TEXT,
        created_at       TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS amjilsim_kb_patch_candidates (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        source_session_id   INTEGER REFERENCES amjilsim_sessions(session_id),
        rule_id             TEXT,
        patch_type          TEXT NOT NULL,
        summary             TEXT,
        proposed_rule_diff  TEXT,
        rationale           TEXT,
        status              TEXT NOT NULL DEFAULT 'draft'
            CHECK (status IN ('draft','approved','merged','rejected')),
        created_at          TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS amjilsim_signature_lexicon (
        token               TEXT PRIMARY KEY,
        category            TEXT NOT NULL,
        signal_type         TEXT,
        weight              REAL NOT NULL DEFAULT 1.0,
        preferred_outlets   TEXT,
        last_calibrated_at  TEXT,
        notes               TEXT
    )
    """,
    # 인덱스
    "CREATE INDEX IF NOT EXISTS idx_aq_drug_session ON amjilsim_drug_queue_status(drug_id, session_id)",
    "CREATE INDEX IF NOT EXISTS idx_aq_state ON amjilsim_drug_queue_status(queue_state)",
    "CREATE INDEX IF NOT EXISTS idx_as_drug ON amjilsim_media_signals(drug_id)",
    "CREATE INDEX IF NOT EXISTS idx_as_session ON amjilsim_media_signals(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_as_outlet_date ON amjilsim_media_signals(outlet, published_at)",
    "CREATE INDEX IF NOT EXISTS idx_ad_msd ON amjilsim_drugs(msd_flag)",
]


ROLLBACK_TABLES = [
    "amjilsim_signature_lexicon",
    "amjilsim_kb_patch_candidates",
    "amjilsim_prediction_audit",
    "amjilsim_media_signals",
    "amjilsim_drug_queue_status",
    "amjilsim_drugs",
    "amjilsim_sessions",
]


# ─────────────────────────────────────────────────────────────────────────────
# Seed 데이터
# ─────────────────────────────────────────────────────────────────────────────

SEED_SESSIONS_2026 = [
    (2026, 1, "2026-01-21", "SCHEDULED", None),
    (2026, 2, "2026-03-04", "SCHEDULED", None),
    (2026, 3, "2026-04-15", "SCHEDULED", None),
    (2026, 4, "2026-05-27", "COMPLETED",
     "5/27 케이스 스터디 baseline. 처리 5건 = 2 APPROVED(베이지노스·엘라히어) / 3 REJECTED(림카토·알렌시·키스칼리)."),
    (2026, 5, "2026-07-08", "SCHEDULED",
     "암질심 6차 라이브 타깃. Welireg 추적 우선 차수."),
    (2026, 6, "2026-08-19", "SCHEDULED", None),
    (2026, 7, "2026-09-30", "SCHEDULED", None),
    (2026, 8, "2026-11-11", "SCHEDULED", None),
    (2026, 9, "2026-12-23", "SCHEDULED", None),
]

# MSD 항암 자산 (암질심 대상). Bridion(NMBA reversal)·Zerbaxa(항생제)·Emend(항구토)는
# 비-항암제로 암질심 대상 밖 → 향후 약평위 트래커 확장 시 별도 추가.
SEED_DRUGS_MSD = [
    # (product_slug, brand_kr, brand_en, ingredient_inn, atc, manufacturer, msd_flag, competitor_class)
    ("welireg",  "웰리렉",   "Welireg",   "belzutifan",     "L01XX74", "한국MSD", 1, "HIF2A"),
    ("keytruda", "키트루다", "Keytruda",  "pembrolizumab",  "L01FF02", "한국MSD", 1, "PD-1"),
]

# Welireg 4차 신청 — 2026-03-20. 5/27(4차)까지 큐에 머묾 (PRJ-welireg-local-mvp 기준).
SEED_QUEUE_STATUS_WELIREG = [
    # (brand_kr, queue_state, queue_entry_date, n_th_attempt, session_date, evidence_url)
    ("웰리렉", "QUEUE_PENDING", "2026-03-20", 1, None, None),
]


def run_migrate(db_path: Path, rollback: bool = False, seed: bool = False) -> None:
    if not db_path.exists():
        print(f"⚠️  DB 없음: {db_path} (먼저 기존 DB 초기화 필요)", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    cur = conn.cursor()

    if rollback:
        print("🔻 ROLLBACK — amjilsim_* 테이블 DROP")
        for t in ROLLBACK_TABLES:
            cur.execute(f"DROP TABLE IF EXISTS {t}")
        conn.commit()
        print("✅ rollback 완료")
        return

    print(f"🔨 CREATE — {db_path}")
    for stmt in DDL:
        cur.execute(stmt)
    conn.commit()
    print(f"✅ DDL 적용 완료 ({len(DDL)} statements)")

    if seed:
        print("🌱 SEED — 2026 차수 9건 + MSD 항암 2개(Keytruda·Welireg) + Welireg 큐 상태")
        cur.executemany(
            "INSERT OR IGNORE INTO amjilsim_sessions "
            "(year, ordinal_assumed, session_date, status, note) VALUES (?,?,?,?,?)",
            SEED_SESSIONS_2026,
        )
        cur.executemany(
            "INSERT OR IGNORE INTO amjilsim_drugs "
            "(product_slug, brand_kr, brand_en, ingredient_inn, atc, manufacturer, "
            "msd_flag, competitor_class) VALUES (?,?,?,?,?,?,?,?)",
            SEED_DRUGS_MSD,
        )
        # Welireg 큐 상태 — drug_id 조회 후 INSERT
        for brand, state, entry, n_th, sess_date, ev_url in SEED_QUEUE_STATUS_WELIREG:
            drug_row = cur.execute(
                "SELECT drug_id FROM amjilsim_drugs WHERE brand_kr = ?", (brand,)
            ).fetchone()
            if drug_row is None:
                print(f"  ⚠️  drug 미존재: {brand} — 건너뜀")
                continue
            sess_id = None
            if sess_date:
                sess_row = cur.execute(
                    "SELECT session_id FROM amjilsim_sessions WHERE session_date = ?",
                    (sess_date,),
                ).fetchone()
                sess_id = sess_row[0] if sess_row else None
            cur.execute(
                "INSERT INTO amjilsim_drug_queue_status "
                "(drug_id, session_id, queue_state, queue_entry_date, n_th_attempt, evidence_url) "
                "VALUES (?,?,?,?,?,?)",
                (drug_row[0], sess_id, state, entry, n_th, ev_url),
            )
        conn.commit()
        print("✅ seed 완료")

    # verify
    counts = {}
    for t in ROLLBACK_TABLES:
        n = cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        counts[t] = n
    print("📊 row counts:")
    for t, n in counts.items():
        print(f"   {t:35s} {n:>6,}")

    conn.close()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--rollback", action="store_true", help="DROP all amjilsim_* tables")
    p.add_argument("--seed", action="store_true", help="Insert 2026 sessions + MSD assets")
    p.add_argument("--db", type=Path, default=DB_PATH, help="DB path override")
    args = p.parse_args()
    run_migrate(args.db, rollback=args.rollback, seed=args.seed)


if __name__ == "__main__":
    main()
