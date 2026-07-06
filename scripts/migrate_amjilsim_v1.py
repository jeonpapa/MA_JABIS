"""
amjilsim_tracker v1 DB 마이그레이션 — drug_prices.db에 7개 테이블 추가.

대상 위원회: 중증(암)질환심의위원회(암질심).
약제급여평가위원회(약평위)는 별개 위원회로 본 schema 적용 밖.

실행
----
    python -m scripts.migrate_amjilsim_v1                   # CREATE IF NOT EXISTS
    python -m scripts.migrate_amjilsim_v1 --rollback        # DROP TABLE IF EXISTS (개발 전용)
    python -m scripts.migrate_amjilsim_v1 --seed            # 2026 차수 + MSD 5개 자산 seed

Access Insight B7/B6/B5 post-deploy 순서
---------------------------------------
1개 migrate 실행이 아래 전부를 멱등 셋업한다:
  - B7 lexicon 스키마(priority/is_active/match_mode) + 큐레이션 seed
  - B6 amjilsim_drugs.is_oncology 컬럼
  - amjilsim_media_signals.signal_type CHECK 에 'UNCLASSIFIED' 추가 (rebuild, 멱등)
  - amjilsim_sessions.committee_type CHECK 에 'BENEFIT_SUBCOMMITTEE' 추가 (rebuild, 멱등)
그 후 CLI 2개:
    python -m scripts.migrate_amjilsim_v1        # ① 스키마+seed+CHECK rebuild
    python scheduler.py --backfill-oncology-now  # ② is_oncology 캐스케이드 백필
    python scheduler.py --reclassify-signals-now # ③ 기존 신호 재분류 (IR 편중 해소)

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


def ensure_signal_type_unclassified(conn: sqlite3.Connection) -> bool:
    """amjilsim_media_signals.signal_type CHECK 에 'UNCLASSIFIED' enum 추가 (멱등).

    ⚠️ CONTROLLER FLAG — CHECK 제약 변경은 SQLite 특성상 테이블 rebuild 가 필요하다.
    대상 테이블 amjilsim_media_signals 는 소규모(~904행)이며 이 테이블을 참조하는
    외래키/트리거가 없어 rebuild 는 저위험이지만, **명시적 opt-in** 이다 (자동 실행 X).
    적용 후 reclassify 는 미분류 competitor 신호를 IR_RELEASE 대신 UNCLASSIFIED 로
    라우팅해 IR 편중을 해소한다. 미적용 시 코드가 IR_RELEASE 로 안전 폴백한다.

    반환: True(rebuild 수행) / False(이미 적용됨).
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='amjilsim_media_signals'"
    ).fetchone()
    if not row or not row[0]:
        return False
    create_sql = row[0]
    if "UNCLASSIFIED" in create_sql:
        return False
    if "'RESULT_REPORT')" not in create_sql:
        raise RuntimeError("예상치 못한 signal_type CHECK 형태 — 수동 검토 필요")

    idx_sqls = [
        r[0] for r in conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' "
            "AND tbl_name='amjilsim_media_signals' AND sql IS NOT NULL"
        )
    ]
    new_create = create_sql.replace(
        "amjilsim_media_signals", "amjilsim_media_signals_new", 1
    ).replace("'RESULT_REPORT')", "'RESULT_REPORT','UNCLASSIFIED')")

    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("BEGIN")
    conn.execute(new_create)
    conn.execute(
        "INSERT INTO amjilsim_media_signals_new SELECT * FROM amjilsim_media_signals"
    )
    conn.execute("DROP TABLE amjilsim_media_signals")
    conn.execute("ALTER TABLE amjilsim_media_signals_new RENAME TO amjilsim_media_signals")
    for isql in idx_sqls:
        conn.execute(isql)
    conn.execute("COMMIT")
    conn.execute("PRAGMA foreign_keys=ON")
    return True


def ensure_benefit_subcommittee_check(conn: sqlite3.Connection) -> bool:
    """amjilsim_sessions.committee_type CHECK 에 'BENEFIT_SUBCOMMITTEE' 추가 (멱등).

    B5 — 비항암 약제의 예상 진입 위원회는 급여기준소위(BENEFIT_SUBCOMMITTEE)다. 현재
    스키마는 세션에 BSC 일정을 넣지 않아도 되지만, 향후 급여기준소위 세션 행을 INSERT
    하면 기존 CHECK(('AMJILSIM','YAKPYUNGWI'))를 위반한다. 미리 enum 을 확장해 둔다.

    amjilsim_sessions 는 여러 테이블이 FK 로 참조하지만 rebuild 중 foreign_keys=OFF +
    session_id 값 보존(INSERT SELECT *) 으로 참조 무결성이 유지된다. 멱등.

    반환: True(rebuild 수행) / False(이미 허용).
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='amjilsim_sessions'"
    ).fetchone()
    if not row or not row[0]:
        return False
    create_sql = row[0]
    if "BENEFIT_SUBCOMMITTEE" in create_sql:
        return False
    target = "committee_type IN ('AMJILSIM','YAKPYUNGWI')"
    if target not in create_sql:
        raise RuntimeError("예상치 못한 committee_type CHECK 형태 — 수동 검토 필요")

    idx_sqls = [
        r[0] for r in conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' "
            "AND tbl_name='amjilsim_sessions' AND sql IS NOT NULL"
        )
    ]
    new_create = create_sql.replace(
        "amjilsim_sessions", "amjilsim_sessions_new", 1
    ).replace(target, "committee_type IN ('AMJILSIM','YAKPYUNGWI','BENEFIT_SUBCOMMITTEE')")

    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("BEGIN")
    conn.execute(new_create)
    conn.execute("INSERT INTO amjilsim_sessions_new SELECT * FROM amjilsim_sessions")
    conn.execute("DROP TABLE amjilsim_sessions")
    conn.execute("ALTER TABLE amjilsim_sessions_new RENAME TO amjilsim_sessions")
    for isql in idx_sqls:
        conn.execute(isql)
    conn.execute("COMMIT")
    conn.execute("PRAGMA foreign_keys=ON")
    return True


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

    # ── B7/B6/B5 (2026-07-06) 멱등 스키마 보강 — 1회 migrate 로 전량 셋업 ──
    #   B7: amjilsim_signature_lexicon 에 priority/is_active/match_mode 컬럼 + 큐레이션 seed.
    #   B6: amjilsim_drugs.is_oncology INTEGER (백필은 --backfill-oncology-now 로 별도 수행).
    #   B7: amjilsim_media_signals.signal_type CHECK 에 'UNCLASSIFIED' (rebuild, 멱등).
    #   B5: amjilsim_sessions.committee_type CHECK 에 'BENEFIT_SUBCOMMITTEE' (rebuild, 멱등).
    try:
        from agents.access_insight.classify import ensure_lexicon_schema, seed_lexicon
        from agents.access_insight.backfill import (
            ensure_oncology_column,
            ensure_prominence_column,
        )

        ensure_lexicon_schema(conn)
        seeded = seed_lexicon(conn)
        ensure_oncology_column(conn)
        # A1 — amjilsim_media_signals.prominence TEXT (title/body_strong/passing).
        #   값 채우기는 scheduler.py --backfill-prominence-now (UPDATE-only, 멱등).
        ensure_prominence_column(conn)
        conn.commit()
        unc = ensure_signal_type_unclassified(conn)
        bsc = ensure_benefit_subcommittee_check(conn)
        conn.commit()
        print(
            f"✅ B7 lexicon 스키마+seed (seed rows: {seeded}) / B6 is_oncology 컬럼 / "
            f"A1 prominence 컬럼 / "
            f"UNCLASSIFIED CHECK rebuild={unc} / BENEFIT_SUBCOMMITTEE CHECK rebuild={bsc}"
        )
    except Exception as e:  # pragma: no cover - 마이그레이션 편의
        print(f"⚠️  B7/B6/B5 보강 skip: {e}")

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
