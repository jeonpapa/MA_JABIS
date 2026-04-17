"""국내 약가 (drug_prices / drug_latest / FTS) — 삽입/검색/이력/통계."""
from __future__ import annotations

import logging

import pandas as pd

from .schema import COL_CANDIDATES

logger = logging.getLogger(__name__)


class _PricesMixin:
    def map_columns(self, df_columns: list[str]) -> dict[str, str]:
        """DataFrame 컬럼명을 DB 컬럼명으로 매핑한다."""
        normalized = {c.strip().replace("\n", "").replace(" ", ""): c for c in df_columns}
        mapping = {}

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

    def upsert_prices(self, df: pd.DataFrame, apply_date: str) -> int:
        """DataFrame을 약가 DB에 삽입. 동일 날짜+코드는 업데이트."""
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

    def search_drug(self, keyword: str, limit: int = 50) -> list[dict]:
        """제품명/성분명으로 약제 검색 (최신 날짜 기준).

        FTS5 인덱스(drug_latest 기반) 우선, 없으면 drug_latest LIKE 폴백.
        drug_prices(3.78M) 풀스캔 없이 drug_latest(~22K)만 사용.
        """
        cols = ("insurance_code, product_name_kr, company, ingredient, "
                "dosage_strength, dosage_form, max_price, apply_date")

        # 접두사 매칭: "키트루다*" → 키트루다, 키트루다주, 키트루다주100mg
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

        # drug_latest LIKE 폴백 (~22K 행, 풀스캔도 <50ms)
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
