"""DB 베이스 — 연결, 초기화, 마이그레이션."""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from .schema import DB_SCHEMA

logger = logging.getLogger(__name__)


class _DbBase:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.executescript(DB_SCHEMA)
            # FTS5 가상 테이블 — executescript 분리 (일부 SQLite 빌드 호환성)
            try:
                conn.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS fts_drug_names
                    USING fts5(
                        product_name_kr,
                        product_name_en,
                        ingredient,
                        insurance_code UNINDEXED,
                        tokenize='unicode61'
                    )
                """)
            except Exception as e:
                logger.warning("FTS5 가상 테이블 생성 실패 (LIKE 폴백 사용): %s", e)
        self._migrate_search_tables()
        self._migrate_indications()
        self._migrate_regimen()
        self._migrate_mail_subscription_scope()
        logger.info("DB 초기화 완료: %s", self.db_path)

    def _migrate_mail_subscription_scope(self) -> None:
        """Daily Mailing 스콥 확장 컬럼(브랜드/회사/정책토픽/질환영역) — 기존 DB ALTER."""
        with self._connect() as conn:
            try:
                existing = {row[1] for row in conn.execute("PRAGMA table_info(mail_subscription)").fetchall()}
            except Exception:
                return
            if not existing:
                return
            for col in ("companies_json", "brands_json", "policy_topics_json",
                        "disease_areas_json", "custom_sources_json"):
                if col not in existing:
                    conn.execute(f"ALTER TABLE mail_subscription ADD COLUMN {col} TEXT NOT NULL DEFAULT '[]'")
            conn.commit()

    def _migrate_regimen(self) -> None:
        """투약비용비교 레지멘 저장 테이블 (payload_json 스냅샷)."""
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS regimen_comparisons (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    name        TEXT NOT NULL,
                    owner_email TEXT,
                    payload_json TEXT NOT NULL,
                    created_at  TEXT,
                    updated_at  TEXT
                )
                """
            )

    def _migrate_indications(self) -> None:
        """기존 indications_master 에 biomarker_class 컬럼 없으면 추가."""
        with self._connect() as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(indications_master)")}
            if "biomarker_class" not in cols:
                conn.execute(
                    "ALTER TABLE indications_master ADD COLUMN biomarker_class TEXT"
                )
                logger.info("Migrated: indications_master.biomarker_class added")

            # indications_by_agency: label_full_text (허가 원문 전문)
            iba_cols = {row[1] for row in conn.execute("PRAGMA table_info(indications_by_agency)")}
            if "label_full_text" not in iba_cols:
                conn.execute(
                    "ALTER TABLE indications_by_agency ADD COLUMN label_full_text TEXT"
                )
                logger.info("Migrated: indications_by_agency.label_full_text added")

            # competitor_trend: source_type / importance 컬럼 (auto 크롤 지원)
            ct_cols = {row[1] for row in conn.execute("PRAGMA table_info(competitor_trend)")}
            if "source_type" not in ct_cols:
                conn.execute(
                    "ALTER TABLE competitor_trend ADD COLUMN source_type TEXT NOT NULL DEFAULT 'manual'"
                )
                logger.info("Migrated: competitor_trend.source_type added")
            if "importance" not in ct_cols:
                conn.execute(
                    "ALTER TABLE competitor_trend ADD COLUMN importance TEXT"
                )
                logger.info("Migrated: competitor_trend.importance added")

            # foreign_drug_dosing: default_pack_count (pack pricing 국가 fallback)
            fd_cols = {row[1] for row in conn.execute("PRAGMA table_info(foreign_drug_dosing)")}
            if "default_pack_count" not in fd_cols:
                conn.execute(
                    "ALTER TABLE foreign_drug_dosing ADD COLUMN default_pack_count INTEGER"
                )
                logger.info("Migrated: foreign_drug_dosing.default_pack_count added")

            # foreign_drug_prices: A8 per-unit 재구조화 (2026-04-21)
            # adjusted_price_krw 를 per-unit KRW 로 재정의. pack_count + daily_cost_krw 를 DB 에 저장.
            fp_cols = {row[1] for row in conn.execute("PRAGMA table_info(foreign_drug_prices)")}
            for col, ddl in (
                ("pack_count",      "ALTER TABLE foreign_drug_prices ADD COLUMN pack_count INTEGER"),
                ("per_unit_local",  "ALTER TABLE foreign_drug_prices ADD COLUMN per_unit_local REAL"),
                ("total_pkg_mg",    "ALTER TABLE foreign_drug_prices ADD COLUMN total_pkg_mg REAL"),
                ("daily_dose_mg",   "ALTER TABLE foreign_drug_prices ADD COLUMN daily_dose_mg REAL"),
                ("daily_cost_krw",  "ALTER TABLE foreign_drug_prices ADD COLUMN daily_cost_krw INTEGER"),
                ("daily_cost_note", "ALTER TABLE foreign_drug_prices ADD COLUMN daily_cost_note TEXT"),
                ("form_type",       "ALTER TABLE foreign_drug_prices ADD COLUMN form_type TEXT"),
                # 국가간 용량(strength) 정규화 (2026-06-21) — per-unit 비교가 공정성 확보.
                # 예: Prevymis JP 20mg vs 타국 240mg → reference 240mg 로 보정.
                ("unit_strength_mg",            "ALTER TABLE foreign_drug_prices ADD COLUMN unit_strength_mg REAL"),
                ("reference_strength_mg",       "ALTER TABLE foreign_drug_prices ADD COLUMN reference_strength_mg REAL"),
                ("dose_norm_factor",            "ALTER TABLE foreign_drug_prices ADD COLUMN dose_norm_factor REAL"),
                ("adjusted_price_krw_normalized", "ALTER TABLE foreign_drug_prices ADD COLUMN adjusted_price_krw_normalized INTEGER"),
                ("dose_norm_note",              "ALTER TABLE foreign_drug_prices ADD COLUMN dose_norm_note TEXT"),
                # 제형(formulation)별 구조화 (2026-06-23) — 강도×투여경로 식별, US canonical 기준.
                # dose_norm_factor 는 이제 *같은 제형 내* 표시단위 보정계수로 의미 재정의.
                ("formulation_key",       "ALTER TABLE foreign_drug_prices ADD COLUMN formulation_key TEXT"),
                ("formulation_label",     "ALTER TABLE foreign_drug_prices ADD COLUMN formulation_label TEXT"),
                ("canonical_strength_mg", "ALTER TABLE foreign_drug_prices ADD COLUMN canonical_strength_mg REAL"),
                ("route",                 "ALTER TABLE foreign_drug_prices ADD COLUMN route TEXT"),
                ("formulation_source",    "ALTER TABLE foreign_drug_prices ADD COLUMN formulation_source TEXT"),
                ("is_us_listed",          "ALTER TABLE foreign_drug_prices ADD COLUMN is_us_listed INTEGER"),
            ):
                if col not in fp_cols:
                    conn.execute(ddl)
                    logger.info("Migrated: foreign_drug_prices.%s added", col)

    def _migrate_search_tables(self) -> None:
        """drug_latest / FTS 인덱스 최초 1회 초기화."""
        with self._connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM drug_latest").fetchone()[0]
            if count > 0:
                return

            total = conn.execute("SELECT COUNT(*) FROM drug_prices").fetchone()[0]
            if total == 0:
                return

            logger.info("drug_latest / FTS 초기화 중... (원본 %d건, 잠시 대기)", total)

            conn.execute("""
                INSERT INTO drug_latest
                    (insurance_code, apply_date, product_name_kr, product_name_en,
                     company, ingredient, dosage_strength, dosage_form,
                     package_unit, max_price, coverage_start, remark)
                SELECT dp.insurance_code, dp.apply_date, dp.product_name_kr,
                       dp.product_name_en, dp.company, dp.ingredient,
                       dp.dosage_strength, dp.dosage_form, dp.package_unit,
                       dp.max_price, dp.coverage_start, dp.remark
                FROM drug_prices dp
                INNER JOIN (
                    SELECT insurance_code, MAX(apply_date) AS max_date
                    FROM drug_prices
                    GROUP BY insurance_code
                ) latest ON dp.insurance_code = latest.insurance_code
                        AND dp.apply_date = latest.max_date
            """)
            latest_count = conn.execute("SELECT COUNT(*) FROM drug_latest").fetchone()[0]
            logger.info("drug_latest 구축 완료: %d건", latest_count)

            try:
                conn.execute("""
                    INSERT INTO fts_drug_names
                        (product_name_kr, product_name_en, ingredient, insurance_code)
                    SELECT product_name_kr, product_name_en, ingredient, insurance_code
                    FROM drug_latest
                """)
                logger.info("FTS5 인덱스 구축 완료")
            except Exception as e:
                logger.warning("FTS5 인덱스 구축 실패 (LIKE 폴백 사용): %s", e)
