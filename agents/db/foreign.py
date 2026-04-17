"""해외 약가 저장/조회 (foreign_drug_prices)."""
from __future__ import annotations


class _ForeignMixin:
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
        """지금까지 검색된 모든 약제 목록 (검색 히스토리 사이드바용).

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
