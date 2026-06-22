"""해외 약가 저장/조회 (foreign_drug_prices) + cross-national reimbursement."""
from __future__ import annotations

import json
from datetime import datetime

from .drug_aliases import aliases, canonical, display_name


class _ForeignMixin:
    _FOREIGN_OPTIONAL_COLS = (
        "form_type", "pack_count", "per_unit_local", "total_pkg_mg",
        "daily_dose_mg", "daily_cost_krw", "daily_cost_note",
        # 국가간 용량 정규화 (dose_norm_factor 는 제형 내 표시단위 보정으로 의미 재정의)
        "unit_strength_mg", "reference_strength_mg", "dose_norm_factor",
        "adjusted_price_krw_normalized", "dose_norm_note",
        # 제형(formulation) 식별
        "formulation_key", "formulation_label", "canonical_strength_mg",
        "route", "formulation_source", "is_us_listed",
    )

    # 검색 후처리(제형 매칭 + 표시단위 보정)가 기존 행에 UPDATE 하는 컬럼
    _FORMULATION_UPDATE_COLS = (
        "unit_strength_mg", "reference_strength_mg", "dose_norm_factor",
        "adjusted_price_krw_normalized", "dose_norm_note",
        "formulation_key", "formulation_label", "canonical_strength_mg",
        "route", "formulation_source", "is_us_listed",
    )

    def save_foreign_price(self, record: dict) -> int:
        """해외 약가 검색 결과를 저장한다. 삽입된 row id 반환."""
        sql = """
            INSERT INTO foreign_drug_prices
                (searched_at, query_name, country, product_name, ingredient,
                 dosage_strength, dosage_form, package_unit,
                 local_price, currency,
                 exchange_rate, exchange_rate_from, exchange_rate_to,
                 factory_price_krw, vat_rate, distribution_margin, adjusted_price_krw,
                 pack_count, per_unit_local, total_pkg_mg,
                 daily_dose_mg, daily_cost_krw, daily_cost_note,
                 unit_strength_mg, reference_strength_mg, dose_norm_factor,
                 adjusted_price_krw_normalized, dose_norm_note,
                 formulation_key, formulation_label, canonical_strength_mg,
                 route, formulation_source, is_us_listed,
                 source_url, source_label, raw_data, form_type)
            VALUES
                (:searched_at, :query_name, :country, :product_name, :ingredient,
                 :dosage_strength, :dosage_form, :package_unit,
                 :local_price, :currency,
                 :exchange_rate, :exchange_rate_from, :exchange_rate_to,
                 :factory_price_krw, :vat_rate, :distribution_margin, :adjusted_price_krw,
                 :pack_count, :per_unit_local, :total_pkg_mg,
                 :daily_dose_mg, :daily_cost_krw, :daily_cost_note,
                 :unit_strength_mg, :reference_strength_mg, :dose_norm_factor,
                 :adjusted_price_krw_normalized, :dose_norm_note,
                 :formulation_key, :formulation_label, :canonical_strength_mg,
                 :route, :formulation_source, :is_us_listed,
                 :source_url, :source_label, :raw_data, :form_type)
        """
        rec = {**record}
        for col in self._FOREIGN_OPTIONAL_COLS:
            rec.setdefault(col, None)
        with self._connect() as conn:
            cur = conn.execute(sql, rec)
        return cur.lastrowid

    def update_foreign_dose_norm(self, row_id: int, fields: dict) -> None:
        """검색 후처리(제형 매칭 + 제형 내 표시단위 보정) 결과를 기존 행에 반영.

        fields: _FORMULATION_UPDATE_COLS 의 부분집합 (dose_norm_* + formulation_*).
        """
        sets = {k: fields[k] for k in self._FORMULATION_UPDATE_COLS if k in fields}
        if not sets:
            return
        assignments = ", ".join(f"{k}=:{k}" for k in sets)
        params = {**sets, "_id": row_id}
        with self._connect() as conn:
            conn.execute(
                f"UPDATE foreign_drug_prices SET {assignments} WHERE id=:_id", params
            )

    def get_foreign_prices(self, query_name: str) -> list[dict]:
        """특정 약제의 최신 해외 약가 조회 (국가별 가장 최근 검색 결과).
        브랜드/molecule alias 를 canonical key 로 묶어 함께 조회한다.

        국가별 대표 row = **가격 보유 row 중 최신** (없으면 그냥 최신).
        재검색이 가격벽/파싱 실패로 local_price=None 을 적재해도, 과거에 확보한
        실가격이 None 에 가려지지 않는다 (cache-db-first — 확보 데이터 보존).
        """
        names = aliases(query_name)
        placeholders = ",".join(["?"] * len(names))
        sql = f"""
            SELECT f.*
            FROM foreign_drug_prices f
            INNER JOIN (
                SELECT country,
                       COALESCE(
                           MAX(CASE WHEN local_price IS NOT NULL THEN searched_at END),
                           MAX(searched_at)
                       ) AS latest
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

    def recompute_foreign_fx(self, rates: dict, meta: dict | None = None,
                             tolerance: float = 1e-4) -> dict:
        """기존 최신 해외약가 행을 현재 환율(rates: {통화코드: rate})로 재계산.

        adjusted_price_krw 는 환율에 선형(per_unit_local × rate × factory_ratio
        × (1+VAT) × (1+margin))이므로 **재스크레이프 없이** new/old 비율로
        factory_price_krw·adjusted_price_krw·daily_cost_krw 를 스케일해 새 행을
        append 한다 (append-only 모델 유지 → get_foreign_prices 가 최신행을 노출).

        국가별 (query_name, country) 최신 가격보유 행만 대상. 환율 변화가 tolerance
        이내면 skip. 반환: {candidates, updated, skipped, details}.
        """
        from agents.exchange_rate import COUNTRY_CURRENCY

        meta = meta or {}
        new_from = meta.get("from", "")
        new_to = meta.get("to", "")
        now = datetime.now().isoformat(timespec="seconds")

        # (query_name, country) 별 최신 가격보유 행 (없으면 그냥 최신)
        sql = """
            SELECT f.*
            FROM foreign_drug_prices f
            INNER JOIN (
                SELECT query_name, country,
                       COALESCE(
                           MAX(CASE WHEN local_price IS NOT NULL THEN searched_at END),
                           MAX(searched_at)
                       ) AS latest
                FROM foreign_drug_prices
                GROUP BY query_name, country
            ) m ON f.query_name = m.query_name AND f.country = m.country
                   AND f.searched_at = m.latest
        """
        with self._connect() as conn:
            rows = [dict(r) for r in conn.execute(sql).fetchall()]

        updated, skipped, details = 0, 0, []
        for r in rows:
            old_rate = r.get("exchange_rate")
            cur = COUNTRY_CURRENCY.get(r.get("country"))
            new_rate = rates.get(cur) if cur else None
            if (not old_rate or old_rate <= 0 or not new_rate
                    or r.get("local_price") is None):
                skipped += 1
                continue
            scale = new_rate / old_rate
            if abs(scale - 1.0) < tolerance:
                skipped += 1
                continue
            rec = {**r}
            rec.pop("id", None)
            rec["searched_at"] = now
            rec["exchange_rate"] = new_rate
            rec["exchange_rate_from"] = new_from or r.get("exchange_rate_from")
            rec["exchange_rate_to"] = new_to or r.get("exchange_rate_to")
            for col in ("factory_price_krw", "adjusted_price_krw", "daily_cost_krw"):
                if rec.get(col) is not None:
                    rec[col] = round(rec[col] * scale, 4)
            self.save_foreign_price(rec)
            updated += 1
            details.append({
                "query": r.get("query_name"), "country": r.get("country"),
                "old_rate": old_rate, "new_rate": new_rate,
            })
        return {"candidates": len(rows), "updated": updated,
                "skipped": skipped, "details": details}

    # ── Cross-national reimbursement ──────────────────────────────────────

    def save_xnational_reimbursement(self, record: dict) -> int:
        """
        reimbursement_xnational row UPSERT.
        record: {indication_id, country, body, decision_type, decision_id,
                 decision_date, effective_date, criteria_text, pbs_code,
                 nhs_list_price, currency, source_url, raw_payload}
        UNIQUE (indication_id, country, body, decision_id) 기준 conflict resolve.
        """
        rec = {**record}
        rec.setdefault("fetched_at", datetime.now().isoformat(timespec="seconds"))
        # raw_payload 가 dict 면 JSON 직렬화
        rp = rec.get("raw_payload")
        if isinstance(rp, (dict, list)):
            rec["raw_payload"] = json.dumps(rp, ensure_ascii=False)
        for col in ("decision_type", "decision_id", "decision_date", "effective_date",
                    "criteria_text", "pbs_code", "nhs_list_price", "currency",
                    "source_url", "raw_payload"):
            rec.setdefault(col, None)

        sql = """
            INSERT INTO reimbursement_xnational
                (indication_id, country, body, decision_type, decision_id,
                 decision_date, effective_date, criteria_text, pbs_code,
                 nhs_list_price, currency, source_url, raw_payload, fetched_at)
            VALUES
                (:indication_id, :country, :body, :decision_type, :decision_id,
                 :decision_date, :effective_date, :criteria_text, :pbs_code,
                 :nhs_list_price, :currency, :source_url, :raw_payload, :fetched_at)
            ON CONFLICT(indication_id, country, body, decision_id) DO UPDATE SET
                decision_type   = excluded.decision_type,
                decision_date   = excluded.decision_date,
                effective_date  = excluded.effective_date,
                criteria_text   = excluded.criteria_text,
                pbs_code        = excluded.pbs_code,
                nhs_list_price  = excluded.nhs_list_price,
                currency        = excluded.currency,
                source_url      = excluded.source_url,
                raw_payload     = excluded.raw_payload,
                fetched_at      = excluded.fetched_at
        """
        with self._connect() as conn:
            cur = conn.execute(sql, rec)
        return cur.lastrowid or 0

    def get_xnational_reimbursement(self, indication_id: str) -> list[dict]:
        """
        reimbursement_xnational + indication_reimbursement (KR/HIRA 가상 행) union.
        한 indication 의 모든 국가 급여 결정을 반환.
        """
        with self._connect() as conn:
            xn_rows = [
                dict(r) for r in conn.execute(
                    "SELECT * FROM reimbursement_xnational WHERE indication_id = ? "
                    "ORDER BY country, body, decision_date DESC",
                    (indication_id,),
                ).fetchall()
            ]
            kr_rows = [
                dict(r) for r in conn.execute(
                    "SELECT * FROM indication_reimbursement WHERE indication_id = ?",
                    (indication_id,),
                ).fetchall()
            ]

        # KR 행을 xnational 형식으로 정규화
        kr_normalized = []
        for r in kr_rows:
            kr_normalized.append({
                "indication_id": r.get("indication_id"),
                "country": "KR",
                "body": "HIRA",
                "decision_type": "recommend" if r.get("is_reimbursed") else "not_listed",
                "decision_id": None,
                "decision_date": r.get("notice_date"),
                "effective_date": r.get("effective_date"),
                "criteria_text": r.get("criteria_text"),
                "pbs_code": None,
                "nhs_list_price": None,
                "currency": "KRW",
                "source_url": r.get("notice_url"),
                "raw_payload": None,
                "fetched_at": r.get("updated_at"),
            })
        return xn_rows + kr_normalized

    def get_xnational_reimbursement_for_product(self, product_slug: str) -> list[dict]:
        """product_slug (예: 'keytruda') 의 모든 indication 의 모든 국가 급여 결정."""
        with self._connect() as conn:
            xn_rows = [
                dict(r) for r in conn.execute(
                    """
                    SELECT xn.*
                    FROM reimbursement_xnational xn
                    JOIN indications_master m ON xn.indication_id = m.indication_id
                    WHERE m.product = ?
                    ORDER BY xn.country, xn.body, xn.decision_date DESC
                    """,
                    (product_slug,),
                ).fetchall()
            ]
            kr_rows = [
                dict(r) for r in conn.execute(
                    """
                    SELECT ir.*
                    FROM indication_reimbursement ir
                    JOIN indications_master m ON ir.indication_id = m.indication_id
                    WHERE m.product = ?
                    """,
                    (product_slug,),
                ).fetchall()
            ]
        kr_normalized = [{
            "indication_id": r.get("indication_id"),
            "country": "KR", "body": "HIRA",
            "decision_type": "recommend" if r.get("is_reimbursed") else "not_listed",
            "decision_id": None,
            "decision_date": r.get("notice_date"),
            "effective_date": r.get("effective_date"),
            "criteria_text": r.get("criteria_text"),
            "pbs_code": None, "nhs_list_price": None, "currency": "KRW",
            "source_url": r.get("notice_url"), "raw_payload": None,
            "fetched_at": r.get("updated_at"),
        } for r in kr_rows]
        return xn_rows + kr_normalized

    def get_reimbursement_xnational_freshness(self) -> dict:
        """마지막 fetched_at + 국가별 row 수. RuleCompliance 모니터링용."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MAX(fetched_at) as last_fetched, COUNT(*) as total "
                "FROM reimbursement_xnational"
            ).fetchone()
            by_country = [
                dict(r) for r in conn.execute(
                    "SELECT country, body, COUNT(*) as n, MAX(fetched_at) as last_fetched "
                    "FROM reimbursement_xnational GROUP BY country, body"
                ).fetchall()
            ]
        return {
            "last_fetched": row[0] if row else None,
            "total": row[1] if row else 0,
            "by_country": by_country,
        }

    # ── product_alias_map ─────────────────────────────────────────────────

    def upsert_product_alias(
        self,
        product_slug: str,
        inn: str | None = None,
        brand_aliases: list[str] | None = None,
        agency_brand_overrides: dict | None = None,
    ) -> None:
        """product_alias_map UPSERT — Phase 2 시드 + 향후 자동 추가에 사용."""
        sql = """
            INSERT INTO product_alias_map
                (product_slug, inn, brand_aliases_json, agency_brand_overrides_json, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(product_slug) DO UPDATE SET
                inn                         = excluded.inn,
                brand_aliases_json          = excluded.brand_aliases_json,
                agency_brand_overrides_json = excluded.agency_brand_overrides_json,
                updated_at                  = excluded.updated_at
        """
        with self._connect() as conn:
            conn.execute(sql, (
                product_slug,
                inn,
                json.dumps(brand_aliases or [], ensure_ascii=False),
                json.dumps(agency_brand_overrides or {}, ensure_ascii=False),
                datetime.now().isoformat(timespec="seconds"),
            ))

    def get_product_alias(self, product_slug: str) -> dict | None:
        """product_alias_map 단일 조회. 없으면 None."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM product_alias_map WHERE product_slug = ?",
                (product_slug.lower(),),
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["brand_aliases"] = json.loads(d.get("brand_aliases_json") or "[]")
        except Exception:
            d["brand_aliases"] = []
        try:
            d["agency_brand_overrides"] = json.loads(d.get("agency_brand_overrides_json") or "{}")
        except Exception:
            d["agency_brand_overrides"] = {}
        return d

    def list_product_aliases(self) -> list[dict]:
        """전 product_alias_map row 반환 (drug_aliases.py 확장용)."""
        with self._connect() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT product_slug, inn, brand_aliases_json, agency_brand_overrides_json "
                "FROM product_alias_map"
            ).fetchall()]
        out = []
        for r in rows:
            try:
                ba = json.loads(r.get("brand_aliases_json") or "[]")
            except Exception:
                ba = []
            try:
                ov = json.loads(r.get("agency_brand_overrides_json") or "{}")
            except Exception:
                ov = {}
            out.append({
                "product_slug": r["product_slug"],
                "inn": r.get("inn"),
                "brand_aliases": ba,
                "agency_brand_overrides": ov,
            })
        return out
