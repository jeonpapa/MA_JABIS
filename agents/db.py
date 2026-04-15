"""
SQLite 약가 데이터베이스 모듈
- 모든 약가 데이터의 중앙 저장소
- 누적 이력 관리 (적용일별 스냅샷 저장)
- 보험코드/제품명/성분명 인덱스로 빠른 검색 지원
"""

import sqlite3
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

DB_SCHEMA = """
-- 핵심 테이블: 적용일별 전체 약가 스냅샷
CREATE TABLE IF NOT EXISTS drug_prices (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    apply_date      TEXT    NOT NULL,   -- 적용 기준일 (예: 2026.04.01)
    insurance_code  TEXT    NOT NULL,   -- 보험코드
    product_name_kr TEXT,               -- 한글제품명
    product_name_en TEXT,               -- 영문제품명
    company         TEXT,               -- 업체명
    ingredient      TEXT,               -- 성분명(일반명)
    dosage_strength TEXT,               -- 함량
    dosage_form     TEXT,               -- 제형
    package_unit    TEXT,               -- 포장단위
    max_price       INTEGER,            -- 상한금액 (원)
    coverage_start  TEXT,               -- 급여개시일
    remark          TEXT                -- 비고
);

-- 적용일 + 보험코드 조합 중복 방지 인덱스
CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_date_code
    ON drug_prices(apply_date, insurance_code);

-- 검색용 인덱스
CREATE INDEX IF NOT EXISTS idx_code
    ON drug_prices(insurance_code);
CREATE INDEX IF NOT EXISTS idx_name
    ON drug_prices(product_name_kr);
CREATE INDEX IF NOT EXISTS idx_ingredient
    ON drug_prices(ingredient);
CREATE INDEX IF NOT EXISTS idx_date
    ON drug_prices(apply_date);

-- 검색 최적화: 보험코드별 최신 약가 레코드만 유지 (~22K 행)
-- drug_prices(3.78M 행) 풀스캔 대신 이 테이블에서 검색
CREATE TABLE IF NOT EXISTS drug_latest (
    insurance_code  TEXT PRIMARY KEY,
    apply_date      TEXT,
    product_name_kr TEXT,
    product_name_en TEXT,
    company         TEXT,
    ingredient      TEXT,
    dosage_strength TEXT,
    dosage_form     TEXT,
    package_unit    TEXT,
    max_price       INTEGER,
    coverage_start  TEXT,
    remark          TEXT
);

-- 다운로드 / 처리 이력 로그
CREATE TABLE IF NOT EXISTS download_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    post_number      INTEGER,            -- 게시물 번호 (226, 225, ...)
    brd_blt_no       INTEGER,            -- HIRA 내부 brdBltNo 파라미터
    apply_date       TEXT,               -- Excel 파일 기준일
    filename         TEXT,               -- 저장된 파일명
    file_path        TEXT,               -- 로컬 파일 경로
    download_status  TEXT DEFAULT 'pending',   -- pending / success / failed / skipped
    process_status   TEXT DEFAULT 'pending',   -- pending / success / failed
    record_count     INTEGER DEFAULT 0,
    downloaded_at    TEXT,
    processed_at     TEXT,
    error_msg        TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_log_brd
    ON download_log(brd_blt_no);

-- 해외 약가 검색 결과 저장 테이블
CREATE TABLE IF NOT EXISTS foreign_drug_prices (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    searched_at         TEXT    NOT NULL,   -- 검색 실행 일시
    query_name          TEXT    NOT NULL,   -- 검색어 (영문 제품명 또는 성분명)
    country             TEXT    NOT NULL,   -- 국가코드 (US/UK/DE/FR/IT/CH/JP/CA)
    product_name        TEXT,               -- 해당국 제품명
    ingredient          TEXT,               -- 성분명
    dosage_strength     TEXT,               -- 함량
    dosage_form         TEXT,               -- 제형
    package_unit        TEXT,               -- 포장단위
    local_price         REAL,               -- 현지 약가 (해당국 화폐)
    currency            TEXT,               -- 통화 (USD/GBP/EUR/CHF/JPY/CAD)
    exchange_rate       REAL,               -- 적용 환율 (36개월 평균)
    exchange_rate_from  TEXT,               -- 환율 적용 시작월 (YYYY-MM)
    exchange_rate_to    TEXT,               -- 환율 적용 종료월 (YYYY-MM)
    factory_price_krw   INTEGER,            -- 공장도출하가격 (원)
    vat_rate            REAL,               -- 부가가치세율 (소수점)
    distribution_margin REAL,               -- 유통거래폭 (소수점)
    adjusted_price_krw  INTEGER,            -- 조정가 (원)
    source_url          TEXT,               -- 자료 출처 URL
    source_label        TEXT,               -- 자료원 명칭 (예: Redbook, MIMS)
    raw_data            TEXT                -- 원본 데이터 JSON
);

CREATE INDEX IF NOT EXISTS idx_foreign_query
    ON foreign_drug_prices(query_name, country);
CREATE INDEX IF NOT EXISTS idx_foreign_date
    ON foreign_drug_prices(searched_at);

-- 약제 부가정보 캐시 (RSA / 용법용량 / 허가일)
CREATE TABLE IF NOT EXISTS drug_enrichment (
    normalized_name         TEXT PRIMARY KEY,
    representative_code     TEXT,                -- 대표 보험코드
    insurance_codes_json    TEXT,                -- 병합된 모든 보험코드 JSON array
    is_rsa                  INTEGER,             -- 1=RSA 대상, 0=아님, NULL=미확인
    rsa_type                TEXT,                -- 총액제한 / 환급 / 사용량연동 / 조건부 등
    rsa_note                TEXT,                -- RSA 관련 특이사항
    approval_date           TEXT,                -- YYYY.MM.DD 최초 품목허가일
    usage_text              TEXT,                -- 용법용량 자연어 원문
    daily_dose_units        REAL,                -- 1일 투여 단위 수 (정/바이알/mL)
    dose_schedule           TEXT,                -- 'continuous' / 'cycle' / 'as_needed'
    cycle_days              INTEGER,             -- 항암제 등 주기 (일)
    doses_per_cycle         REAL,                -- 1 주기당 투여 단위 수
    sources_json            TEXT,                -- [{url,title,media}]
    confidence              TEXT,                -- high|medium|low
    notes                   TEXT,
    fetched_at              TEXT,                -- ISO8601
    ttl_days                INTEGER DEFAULT 30
);
CREATE INDEX IF NOT EXISTS idx_enrichment_code
    ON drug_enrichment(representative_code);
"""

# 엑셀 컬럼명과 DB 컬럼명 매핑 후보 (다양한 HIRA 파일 포맷 대응)
COL_CANDIDATES = {
    # 현행(2010년대~) 포맷
    "insurance_code":  ["보험코드", "보험\n코드", "급여코드", "코드",
                        # 구형(2008~2009) 포맷
                        "제품코드"],
    "product_name_kr": ["한글제품명", "제품명", "한글\n제품명", "품목명(한글)", "한글 제품명"],
    "product_name_en": ["영문제품명", "영문\n제품명", "품목명(영문)", "영문 제품명"],
    "company":         ["업체명", "제조(수입)업체", "회사명", "제약사",
                        "업소명"],           # 구형 포맷
    "ingredient":      ["성분명(일반명)", "성분명", "일반명", "주성분"],
    "dosage_strength": ["함량", "규격", "함량/규격"],
    "dosage_form":     ["제형", "剂型"],
    "package_unit":    ["포장단위", "포장", "단위"],
    "max_price":       ["상한금액", "상한\n금액", "최고가격", "보험약가", "약가(원)"],
    "coverage_start":  ["급여개시일", "급여\n개시일", "급여적용일"],
    "remark":          ["비고"],
}


class DrugPriceDB:
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
            # FTS5 가상 테이블 (executescript 에서 분리 — 일부 SQLite 빌드 호환성)
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
        logger.info("DB 초기화 완료: %s", self.db_path)

    def _migrate_search_tables(self) -> None:
        """
        drug_latest / FTS 인덱스를 최초 1회 초기화한다.
        drug_prices(3.78M) → GROUP BY → drug_latest(22K) → FTS5 인덱스.
        이미 채워진 경우 즉시 반환.
        """
        with self._connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM drug_latest").fetchone()[0]
            if count > 0:
                return  # 이미 초기화됨

            total = conn.execute("SELECT COUNT(*) FROM drug_prices").fetchone()[0]
            if total == 0:
                return  # 아직 데이터 없음

            logger.info("drug_latest / FTS 초기화 중... (원본 %d건, 잠시 대기)", total)

            # 보험코드별 최신 날짜 레코드만 추출
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

            # FTS5 인덱스 구축
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

    # ──────────────────────────────────────────────────────────────────────
    # 컬럼 자동 매핑
    # ──────────────────────────────────────────────────────────────────────

    def map_columns(self, df_columns: list[str]) -> dict[str, str]:
        """DataFrame 컬럼명을 DB 컬럼명으로 매핑한다. 유연하게 처리."""
        normalized = {c.strip().replace("\n", "").replace(" ", ""): c for c in df_columns}
        mapping = {}  # db_col → df_col

        for db_col, candidates in COL_CANDIDATES.items():
            for cand in candidates:
                norm_cand = cand.strip().replace("\n", "").replace(" ", "")
                if norm_cand in normalized:
                    mapping[db_col] = normalized[norm_cand]
                    break

        missing = [k for k in COL_CANDIDATES if k not in mapping]
        if missing:
            logger.warning("컬럼 매핑 실패: %s", missing)
        logger.info("컬럼 매핑 완료: %s", {v: k for k, v in mapping.items()})
        return mapping

    # ──────────────────────────────────────────────────────────────────────
    # 약가 데이터 삽입
    # ──────────────────────────────────────────────────────────────────────

    def upsert_prices(self, df: pd.DataFrame, apply_date: str) -> int:
        """DataFrame을 약가 DB에 삽입한다. 동일 날짜+코드는 업데이트."""
        col_map = self.map_columns(list(df.columns))

        if "insurance_code" not in col_map:
            logger.error("보험코드 컬럼을 찾지 못해 삽입 불가")
            return 0

        rows = []
        for _, row in df.iterrows():
            code = str(row.get(col_map.get("insurance_code", ""), "")).strip()
            if not code or code in ("nan", "None", "보험코드"):
                continue

            def get(key):
                col = col_map.get(key, "")
                if col and col in row.index:
                    val = str(row[col]).strip()
                    return None if val in ("nan", "None", "") else val
                return None

            # 상한금액 정수 변환
            price_str = get("max_price")
            try:
                max_price = int(float(price_str.replace(",", ""))) if price_str else None
            except (ValueError, AttributeError):
                max_price = None

            rows.append((
                apply_date,
                code,
                get("product_name_kr"),
                get("product_name_en"),
                get("company"),
                get("ingredient"),
                get("dosage_strength"),
                get("dosage_form"),
                get("package_unit"),
                max_price,
                get("coverage_start"),
                get("remark"),
            ))

        if not rows:
            logger.warning("삽입할 유효 행이 없습니다.")
            return 0

        sql = """
            INSERT OR REPLACE INTO drug_prices
                (apply_date, insurance_code, product_name_kr, product_name_en,
                 company, ingredient, dosage_strength, dosage_form,
                 package_unit, max_price, coverage_start, remark)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """
        with self._connect() as conn:
            conn.executemany(sql, rows)

            # drug_latest 증분 업데이트 (이번 적용일이 기존보다 최신인 경우만 교체)
            try:
                conn.execute("""
                    INSERT OR REPLACE INTO drug_latest
                        (insurance_code, apply_date, product_name_kr, product_name_en,
                         company, ingredient, dosage_strength, dosage_form,
                         package_unit, max_price, coverage_start, remark)
                    SELECT d.insurance_code, d.apply_date, d.product_name_kr, d.product_name_en,
                           d.company, d.ingredient, d.dosage_strength, d.dosage_form,
                           d.package_unit, d.max_price, d.coverage_start, d.remark
                    FROM drug_prices d
                    LEFT JOIN drug_latest dl ON d.insurance_code = dl.insurance_code
                    WHERE d.apply_date = ?
                      AND (dl.insurance_code IS NULL OR ? >= dl.apply_date)
                """, (apply_date, apply_date))

                updated = conn.execute("SELECT changes()").fetchone()[0]
                if updated > 0:
                    # FTS5 인덱스 전체 재구축 (drug_latest 22K 행 기준, 1~2초)
                    conn.execute("DELETE FROM fts_drug_names")
                    conn.execute("""
                        INSERT INTO fts_drug_names
                            (product_name_kr, product_name_en, ingredient, insurance_code)
                        SELECT product_name_kr, product_name_en, ingredient, insurance_code
                        FROM drug_latest
                    """)
                    logger.info("drug_latest / FTS 갱신: %d건 (기준일: %s)", updated, apply_date)
            except Exception as e:
                logger.warning("drug_latest / FTS 갱신 실패 (무시): %s", e)

        logger.info("DB 삽입 완료: %d건 (기준일: %s)", len(rows), apply_date)
        return len(rows)

    # ──────────────────────────────────────────────────────────────────────
    # 다운로드 로그 관리
    # ──────────────────────────────────────────────────────────────────────

    def log_download(self, brd_blt_no: int, post_number: int, apply_date: str = None,
                     filename: str = None, file_path: str = None,
                     status: str = "pending", error_msg: str = None):
        sql = """
            INSERT OR IGNORE INTO download_log
                (brd_blt_no, post_number, apply_date, filename, file_path,
                 download_status, downloaded_at)
            VALUES (?,?,?,?,?,'pending',NULL)
        """
        update_sql = """
            UPDATE download_log
            SET apply_date=?, filename=?, file_path=?,
                download_status=?, downloaded_at=?, error_msg=?
            WHERE brd_blt_no=?
        """
        with self._connect() as conn:
            conn.execute(sql, (brd_blt_no, post_number, apply_date, filename, file_path))
            conn.execute(update_sql, (
                apply_date, filename, file_path, status,
                datetime.now().isoformat(), error_msg, brd_blt_no,
            ))

    def log_process(self, brd_blt_no: int, status: str, record_count: int = 0, error_msg: str = None):
        sql = """
            UPDATE download_log
            SET process_status=?, record_count=?, processed_at=?, error_msg=?
            WHERE brd_blt_no=?
        """
        with self._connect() as conn:
            conn.execute(sql, (status, record_count, datetime.now().isoformat(), error_msg, brd_blt_no))

    def is_downloaded(self, brd_blt_no: int) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT download_status FROM download_log WHERE brd_blt_no=?",
                (brd_blt_no,)
            ).fetchone()
        return row is not None and row["download_status"] == "success"

    def is_processed(self, brd_blt_no: int) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT process_status FROM download_log WHERE brd_blt_no=?",
                (brd_blt_no,)
            ).fetchone()
        return row is not None and row["process_status"] == "success"

    def get_pending_files(self) -> list[dict]:
        """다운로드는 됐지만 아직 DB에 처리되지 않은 파일 목록"""
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT * FROM download_log
                WHERE download_status='success' AND process_status!='success'
                ORDER BY post_number ASC
            """).fetchall()
        return [dict(r) for r in rows]

    # ──────────────────────────────────────────────────────────────────────
    # 검색 쿼리
    # ──────────────────────────────────────────────────────────────────────

    def search_drug(self, keyword: str, limit: int = 50) -> list[dict]:
        """
        제품명 또는 성분명으로 약제 검색 (최신 날짜 기준).
        FTS5 인덱스(drug_latest 기반) 우선 — 없으면 drug_latest LIKE 폴백.
        drug_prices(3.78M) 풀스캔 없이 drug_latest(~22K)만 사용.
        """
        cols = ("insurance_code, product_name_kr, company, ingredient, "
                "dosage_strength, dosage_form, max_price, apply_date")

        # ── FTS5 검색 ─────────────────────────────────────────────────────────
        # 접두사 매칭: "키트루다*" → 키트루다, 키트루다주, 키트루다주100mg 모두 매칭
        safe_kw = keyword.replace('"', '""')
        fts_query = f'"{safe_kw}"*'
        try:
            fts_sql = f"""
                SELECT dl.{cols}
                FROM fts_drug_names fts
                JOIN drug_latest dl ON dl.insurance_code = fts.insurance_code
                WHERE fts MATCH ?
                ORDER BY dl.apply_date DESC, dl.insurance_code
                LIMIT ?
            """
            with self._connect() as conn:
                rows = conn.execute(fts_sql, (fts_query, limit)).fetchall()
            if rows:
                return [dict(r) for r in rows]
        except Exception as e:
            logger.debug("[search_drug] FTS5 실패: %s", e)

        # ── drug_latest LIKE 폴백 (~22K 행, 풀스캔도 <50ms) ──────────────────
        kw = f"%{keyword}%"
        sql = f"""
            SELECT {cols}
            FROM drug_latest
            WHERE product_name_kr LIKE ? OR ingredient LIKE ? OR insurance_code LIKE ?
            ORDER BY apply_date DESC, insurance_code
            LIMIT ?
        """
        with self._connect() as conn:
            rows = conn.execute(sql, (kw, kw, kw, limit)).fetchall()
        return [dict(r) for r in rows]

    def get_price_history(self, insurance_code: str) -> list[dict]:
        """특정 보험코드의 전체 가격 이력 (날짜 오름차순)"""
        sql = """
            SELECT apply_date, insurance_code, product_name_kr, company,
                   ingredient, dosage_strength, max_price, coverage_start, remark
            FROM drug_prices
            WHERE insurance_code = ?
            ORDER BY apply_date ASC
        """
        with self._connect() as conn:
            rows = conn.execute(sql, (insurance_code,)).fetchall()
        return [dict(r) for r in rows]

    # ─────────────────────────────────────────────────────────────
    # 약제 부가정보 캐시 (drug_enrichment) — RSA / 용법용량 / 허가일
    # ─────────────────────────────────────────────────────────────
    def get_enrichment(self, normalized_name: str):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM drug_enrichment WHERE normalized_name = ?",
                (normalized_name,),
            ).fetchone()
        return dict(row) if row else None

    def save_enrichment(self, rec: dict) -> None:
        import json as _json
        from datetime import datetime as _dt
        fields = [
            "normalized_name", "representative_code", "insurance_codes_json",
            "is_rsa", "rsa_type", "rsa_note", "approval_date",
            "usage_text", "daily_dose_units", "dose_schedule",
            "cycle_days", "doses_per_cycle", "sources_json",
            "confidence", "notes", "fetched_at", "ttl_days",
        ]
        rec.setdefault("fetched_at", _dt.utcnow().isoformat() + "Z")
        rec.setdefault("ttl_days", 30)
        # list/dict → json
        if isinstance(rec.get("insurance_codes_json"), (list, dict)):
            rec["insurance_codes_json"] = _json.dumps(rec["insurance_codes_json"], ensure_ascii=False)
        if isinstance(rec.get("sources_json"), (list, dict)):
            rec["sources_json"] = _json.dumps(rec["sources_json"], ensure_ascii=False)
        values = [rec.get(f) for f in fields]
        sql = f"""
            INSERT INTO drug_enrichment ({','.join(fields)})
            VALUES ({','.join('?' * len(fields))})
            ON CONFLICT(normalized_name) DO UPDATE SET
            {','.join(f + '=excluded.' + f for f in fields if f != 'normalized_name')}
        """
        with self._connect() as conn:
            conn.execute(sql, values)
            conn.commit()

    def get_stats(self) -> dict:
        """DB 통계"""
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM drug_prices").fetchone()[0]
            dates = conn.execute(
                "SELECT COUNT(DISTINCT apply_date) FROM drug_prices"
            ).fetchone()[0]
            latest = conn.execute(
                "SELECT MAX(apply_date) FROM drug_prices"
            ).fetchone()[0]
            oldest = conn.execute(
                "SELECT MIN(apply_date) FROM drug_prices"
            ).fetchone()[0]
            log_total = conn.execute("SELECT COUNT(*) FROM download_log").fetchone()[0]
            log_success = conn.execute(
                "SELECT COUNT(*) FROM download_log WHERE download_status='success'"
            ).fetchone()[0]
        return {
            "total_records": total,
            "total_dates": dates,
            "latest_date": latest,
            "oldest_date": oldest,
            "downloaded_files": f"{log_success}/{log_total}",
        }

    def get_available_dates(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT apply_date FROM drug_prices ORDER BY apply_date ASC"
            ).fetchall()
        return [r[0] for r in rows]

    # ──────────────────────────────────────────────────────────────────────
    # 해외 약가 저장 / 조회
    # ──────────────────────────────────────────────────────────────────────

    def save_foreign_price(self, record: dict) -> int:
        """해외 약가 검색 결과를 저장한다. 삽입된 row id 반환."""
        sql = """
            INSERT INTO foreign_drug_prices
                (searched_at, query_name, country, product_name, ingredient,
                 dosage_strength, dosage_form, package_unit,
                 local_price, currency,
                 exchange_rate, exchange_rate_from, exchange_rate_to,
                 factory_price_krw, vat_rate, distribution_margin, adjusted_price_krw,
                 source_url, source_label, raw_data)
            VALUES
                (:searched_at, :query_name, :country, :product_name, :ingredient,
                 :dosage_strength, :dosage_form, :package_unit,
                 :local_price, :currency,
                 :exchange_rate, :exchange_rate_from, :exchange_rate_to,
                 :factory_price_krw, :vat_rate, :distribution_margin, :adjusted_price_krw,
                 :source_url, :source_label, :raw_data)
        """
        with self._connect() as conn:
            cur = conn.execute(sql, record)
        return cur.lastrowid

    def get_foreign_prices(self, query_name: str) -> list[dict]:
        """특정 약제의 최신 해외 약가 조회 (국가별 가장 최근 검색 결과)."""
        sql = """
            SELECT f.*
            FROM foreign_drug_prices f
            INNER JOIN (
                SELECT country, MAX(searched_at) AS latest
                FROM foreign_drug_prices
                WHERE LOWER(query_name) = LOWER(?)
                GROUP BY country
            ) m ON f.country = m.country AND f.searched_at = m.latest
            WHERE LOWER(f.query_name) = LOWER(?)
            ORDER BY f.country
        """
        with self._connect() as conn:
            rows = conn.execute(sql, (query_name, query_name)).fetchall()
        return [dict(r) for r in rows]

    def get_foreign_drug_list(self) -> list[dict]:
        """
        지금까지 검색된 모든 약제 목록 반환 (검색 히스토리 사이드바용).
        반환: [{"query_name", "last_searched_at", "country_count", "has_price"}]
        """
        sql = """
            SELECT
                query_name,
                MAX(searched_at)                        AS last_searched_at,
                COUNT(DISTINCT country)                 AS country_count,
                MAX(CASE WHEN local_price IS NOT NULL THEN 1 ELSE 0 END) AS has_price
            FROM foreign_drug_prices
            GROUP BY LOWER(query_name)
            ORDER BY last_searched_at DESC
        """
        with self._connect() as conn:
            rows = conn.execute(sql).fetchall()
        return [dict(r) for r in rows]

    def get_foreign_search_history(self, query_name: str, country: str = None) -> list[dict]:
        """특정 약제의 해외 약가 검색 이력 (시간순)."""
        if country:
            sql = """
                SELECT * FROM foreign_drug_prices
                WHERE LOWER(query_name) = LOWER(?) AND country = ?
                ORDER BY searched_at DESC
            """
            params = (query_name, country)
        else:
            sql = """
                SELECT * FROM foreign_drug_prices
                WHERE LOWER(query_name) = LOWER(?)
                ORDER BY searched_at DESC
            """
            params = (query_name,)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
