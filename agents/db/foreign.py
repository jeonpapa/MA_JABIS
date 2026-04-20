"""해외 약가 저장/조회 (foreign_drug_prices)."""
from __future__ import annotations

from .drug_aliases import aliases, canonical, display_name


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
                 source_url, source_label, raw_data, form_type)
            VALUES
                (:searched_at, :query_name, :country, :product_name, :ingredient,
                 :dosage_strength, :dosage_form, :package_unit,
                 :local_price, :currency,
                 :exchange_rate, :exchange_rate_from, :exchange_rate_to,
                 :factory_price_krw, :vat_rate, :distribution_margin, :adjusted_price_krw,
                 :source_url, :source_label, :raw_data, :form_type)
        """
        rec = {**record}
        rec.setdefault("form_type", None)
        with self._connect() as conn:
            cur = conn.execute(sql, rec)
        return cur.lastrowid

    def get_foreign_prices(self, query_name: str) -> list[dict]:
        """특정 약제의 최신 해외 약가 조회 (국가별 가장 최근 검색 결과).
        브랜드/molecule alias 를 canonical key 로 묶어 함께 조회한다."""
        names = aliases(query_name)
        placeholders = ",".join(["?"] * len(names))
        sql = f"""
            SELECT f.*
            FROM foreign_drug_prices f
            INNER JOIN (
                SELECT country, MAX(searched_at) AS latest
                FROM foreign_drug_prices
                WHERE LOWER(query_name) IN ({placeholders})
                GROUP BY country
            ) m ON f.country = m.country AND f.searched_at = m.latest
            WHERE LOWER(f.query_name) IN ({placeholders})
            ORDER BY f.country
        """
        params = tuple(names) * 2
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get_foreign_drug_list(self) -> list[dict]:
        """지금까지 검색된 모든 약제 목록 (검색 히스토리 사이드바용).

        반환: [{"query_name", "last_searched_at", "country_count", "has_price"}]
        canonical (molecule) 기준으로 브랜드/대체표기를 병합한다.
        """
        sql = """
            SELECT
                query_name,
                country,
                searched_at,
                local_price
            FROM foreign_drug_prices
        """
        with self._connect() as conn:
            rows = [dict(r) for r in conn.execute(sql).fetchall()]

        buckets: dict[str, dict] = {}
        for r in rows:
            raw = r.get("query_name") or ""
            canon = canonical(raw)
            b = buckets.setdefault(canon, {
                "query_name": display_name(canon),
                "canonical": canon,
                "aliases": set(),
                "countries": set(),
                "last_searched_at": None,
                "has_price": 0,
            })
            b["aliases"].add(raw)
            if r.get("country"):
                b["countries"].add(r["country"])
            ts = r.get("searched_at")
            if ts and (b["last_searched_at"] is None or ts > b["last_searched_at"]):
                b["last_searched_at"] = ts
            if r.get("local_price") is not None:
                b["has_price"] = 1

        out = []
        for b in buckets.values():
            out.append({
                "query_name": b["query_name"],
                "canonical": b["canonical"],
                "aliases": sorted(b["aliases"]),
                "last_searched_at": b["last_searched_at"],
                "country_count": len(b["countries"]),
                "has_price": b["has_price"],
            })
        out.sort(key=lambda x: x["last_searched_at"] or "", reverse=True)
        return out

    def delete_foreign_drug(self, query_name: str) -> int:
        """query_name 에 해당하는 모든 해외 약가 레코드 삭제. alias 전부 함께 제거."""
        names = aliases(query_name)
        placeholders = ",".join(["?"] * len(names))
        with self._connect() as conn:
            cur = conn.execute(
                f"DELETE FROM foreign_drug_prices WHERE LOWER(query_name) IN ({placeholders})",
                tuple(names),
            )
            return cur.rowcount

    def get_foreign_search_history(self, query_name: str, country: str = None) -> list[dict]:
        """특정 약제의 해외 약가 검색 이력 (시간순). alias 전부 포함."""
        names = aliases(query_name)
        placeholders = ",".join(["?"] * len(names))
        if country:
            sql = f"""
                SELECT * FROM foreign_drug_prices
                WHERE LOWER(query_name) IN ({placeholders}) AND country = ?
                ORDER BY searched_at DESC
            """
            params = tuple(names) + (country,)
        else:
            sql = f"""
                SELECT * FROM foreign_drug_prices
                WHERE LOWER(query_name) IN ({placeholders})
                ORDER BY searched_at DESC
            """
            params = tuple(names)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
