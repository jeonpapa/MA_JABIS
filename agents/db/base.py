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
        logger.info("DB 초기화 완료: %s", self.db_path)

    def _migrate_indications(self) -> None:
        """기존 indications_master 에 biomarker_class 컬럼 없으면 추가."""
        with self._connect() as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(indications_master)")}
            if "biomarker_class" not in cols:
                conn.execute(
                    "ALTER TABLE indications_master ADD COLUMN biomarker_class TEXT"
                )
                logger.info("Migrated: indications_master.biomarker_class added")

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
