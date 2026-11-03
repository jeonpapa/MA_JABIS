"""해외 약가 저장/조회 (foreign_drug_prices) + cross-national reimbursement."""
from __future__ import annotations

import json
from datetime import datetime

from .drug_aliases import aliases, canonical, display_name

# 확인일이 이보다 오래되면 화면에 '오래됨' 으로 표시한다 (2026-11-03 사용자 승인 개선안).
FOREIGN_STALE_DAYS = 30
_ARCHIVE_TABLE = "foreign_drug_prices_archive"


def _session_bucket(ts) -> str:
    """같은 스크레이프의 채널별 타임스탬프 차이(수십 ms)를 한 세션으로 묶는다 — 분 단위."""
    return str(ts or "")[:16]


def _norm_label(label) -> str:
    return " ".join(str(label or "").lower().split())


def classify_price_sessions(rows: list[dict]) -> dict[str, list[dict]]:
    """국가별 수집 세션을 시간순으로 묶고, 직전 '가격 있는' 세션 대비 변경 유형을 붙인다.

    kind:
      first    — 첫 가격 세션
      same     — 가격·출처 표기 모두 동일 (재확인)
      relabel  — 가격 동일, 출처/단계 표기만 바뀜
      packs    — 겹치는 제형의 가격은 같고 포장/제형 구성만 추가·제외
      basis    — 가격이 바뀌었고 출처/가격 단계 표기도 바뀜 → 직접 비교 불가 (가격 인하로 읽지 않음)
      price    — 같은 출처·단계에서 가격이 바뀜 (실제 가격 변동)
      unpriced — 이 세션에 가격이 없음 (수집 실패·미확정). 비교 기준에서 제외.
    비교는 현지 통화 원가격(local_price) 기준 — 원화 조정가는 환율·산식 변경이 섞여 쓰지 않는다.
    """
    by_country: dict[str, dict[str, list[dict]]] = {}
    for r in rows:
        cc = r.get("country")
        if not cc:
            continue
        by_country.setdefault(cc, {}).setdefault(_session_bucket(r.get("searched_at")), []).append(r)

    out: dict[str, list[dict]] = {}
    for cc, sessions in by_country.items():
        prev_sig = None
        prev_labels = None
        events = []
        for bucket in sorted(sessions):
            srows = sessions[bucket]
            priced = [r for r in srows if r.get("local_price") is not None]
            sig: dict[str, list] = {}
            for r in priced:
                sig.setdefault(r.get("formulation_key") or "_", []).append(round(float(r["local_price"]), 2))
            sig = {k: sorted(v) for k, v in sig.items()}
            labels = frozenset(_norm_label(r.get("source_label")) for r in priced)
            if not priced:
                kind = "unpriced"
            elif prev_sig is None:
                kind = "first"
            else:
                common = set(sig) & set(prev_sig)
                changed = [k for k in common if sig[k] != prev_sig[k]]
                added_or_removed = set(sig) ^ set(prev_sig)
                if changed:
                    kind = "basis" if labels != prev_labels else "price"
                elif added_or_removed:
                    kind = "packs"
                else:
                    kind = "same" if labels == prev_labels else "relabel"
            if priced:
                prev_sig, prev_labels = sig, labels
            events.append({
                "bucket": bucket,
                "searched_at": max(str(r.get("searched_at") or "") for r in srows),
                "kind": kind,
                "rows": srows,
            })
        out[cc] = events
    return out


class _ForeignMixin:
    _FOREIGN_OPTIONAL_COLS = (
        "form_type", "pack_count", "pack_count_source", "per_unit_local", "total_pkg_mg",
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
                 pack_count, pack_count_source, per_unit_local, total_pkg_mg,
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
                 :pack_count, :pack_count_source, :per_unit_local, :total_pkg_mg,
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

    def compact_foreign_prices(self, query_name: str) -> int:
        """동일 약제(alias 포함)에 대해 **국가별 '최신 1세션'만 남기고** 과거 누적 행을 보관 테이블로 옮긴다.

        append-only 모델이라 재검색·백필마다 행이 쌓이므로, 검색 직후 호출해
        국가당 최신 세션만 유지한다 (한 번의 스크레이프는 국가별로 동일 searched_at 공유).

        keep 기준: **가격 보유 세션 우선** — MAX(searched_at WHERE local_price NOT NULL),
        없으면 MAX(searched_at). 실패 재검색(local_price=None)이 과거 확보 실가격을
        지우지 않도록 보존한다 (cache-db-first 원칙, get_foreign_prices 노출 로직과 일치).

        반환: 현재 테이블에서 빠진(보관으로 옮긴) 행 수. 2026-11-03 이전에는 삭제했다.
        """
        names = aliases(query_name)
        placeholders = ",".join(["?"] * len(names))
        keep_sql = f"""
            SELECT country,
                   COALESCE(
                       MAX(CASE WHEN local_price IS NOT NULL THEN searched_at END),
                       MAX(searched_at)
                   ) AS keep_ts
            FROM foreign_drug_prices
            WHERE LOWER(query_name) IN ({placeholders})
            GROUP BY country
        """
        with self._connect() as conn:
            keep = {
                r["country"]: r["keep_ts"]
                for r in conn.execute(keep_sql, tuple(names)).fetchall()
                if r["country"] and r["keep_ts"]
            }
            deleted = 0
            cols = self._ensure_price_archive(conn)
            col_sql = ",".join(cols)
            archived_at = datetime.now().isoformat(timespec="seconds")
            for country, keep_ts in keep.items():
                where = f"""LOWER(query_name) IN ({placeholders})
                          AND country = ?
                          AND searched_at <> ?"""
                params = (*names, country, keep_ts)
                # 2026-11-03: 과거 세션은 지우지 않고 보관 테이블로 옮긴다 — 화면 '변경 이력' 의 원천.
                conn.execute(
                    f"INSERT INTO {_ARCHIVE_TABLE} ({col_sql}, archived_at) "
                    f"SELECT {col_sql}, ? FROM foreign_drug_prices WHERE {where}",
                    (archived_at, *params),
                )
                cur = conn.execute(f"DELETE FROM foreign_drug_prices WHERE {where}", params)
                deleted += cur.rowcount
        return deleted

    def _ensure_price_archive(self, conn) -> list[str]:
        """보관 테이블을 현재 foreign_drug_prices 컬럼과 맞춘다(없으면 생성, 늘어난 컬럼은 추가). 컬럼 목록 반환."""
        cols = [r[1] for r in conn.execute("PRAGMA table_info(foreign_drug_prices)").fetchall()]
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (_ARCHIVE_TABLE,)
        ).fetchone()
        if not exists:
            conn.execute(f"CREATE TABLE {_ARCHIVE_TABLE} AS SELECT * FROM foreign_drug_prices WHERE 0")
            conn.execute(f"ALTER TABLE {_ARCHIVE_TABLE} ADD COLUMN archived_at TEXT")
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_fdp_archive_qc ON {_ARCHIVE_TABLE}(query_name, country)"
            )
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({_ARCHIVE_TABLE})").fetchall()}
        for c in cols:
            if c not in have:
                conn.execute(f'ALTER TABLE {_ARCHIVE_TABLE} ADD COLUMN "{c}"')
        return cols

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

    def get_foreign_presentations(self, query_name: str) -> list[dict]:
        """제형별 탭용 — 국가×제형(강도·투여경로·포장)별 최신 가격보유 행 전부 반환.

        get_foreign_prices 는 국가당 1건으로 collapse 하지만, 여기선
        (country, dosage_strength, form_type, package_unit) 조합별 최신 priced 행을
        모두 반환해 대시보드가 제형(formulation)별로 그룹핑할 수 있게 한다.
        (US 주사 '20mg/1ml' 12ml=240mg vs 24ml=480mg 처럼 같은 강도문자열이라도
        package_unit 으로 구분 — 다른 제형.)
        """
        names = aliases(query_name)
        placeholders = ",".join(["?"] * len(names))
        sql = f"""
            SELECT f.*
            FROM foreign_drug_prices f
            INNER JOIN (
                SELECT country,
                       IFNULL(dosage_strength,'') AS ds,
                       IFNULL(form_type,'')       AS ft,
                       IFNULL(package_unit,'')    AS pu,
                       COALESCE(
                           MAX(CASE WHEN local_price IS NOT NULL THEN searched_at END),
                           MAX(searched_at)
                       ) AS latest
                FROM foreign_drug_prices
                WHERE LOWER(query_name) IN ({placeholders})
                GROUP BY country, ds, ft, pu
            ) m ON f.country = m.country
               AND IFNULL(f.dosage_strength,'') = m.ds
               AND IFNULL(f.form_type,'')       = m.ft
               AND IFNULL(f.package_unit,'')    = m.pu
               AND f.searched_at = m.latest
            WHERE LOWER(f.query_name) IN ({placeholders})
            ORDER BY f.country
        """
        params = tuple(names) * 2
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get_foreign_drug_list(self, today: datetime | None = None) -> list[dict]:
        """지금까지 검색된 모든 약제 목록 (검색 히스토리 사이드바용).

        반환: [{"query_name", "last_searched_at", "country_count", "has_price"}]
        canonical (molecule) 기준으로 브랜드/대체표기를 병합한다.
        """
        sql = """
            SELECT
                query_name,
                country,
                searched_at,
                local_price,
                source_label,
                formulation_key
            FROM foreign_drug_prices
        """
        with self._connect() as conn:
            rows = [dict(r) for r in conn.execute(sql).fetchall()]
            archived = self._archived_rows(conn, None)

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

        # 국가별 현재 상태(가격 있음/미확정, 확인일) + 변경 이력 요약 — 가로 제품 카드용 (2026-11-03)
        today = (today or datetime.now()).date()
        by_canon_rows: dict[str, list[dict]] = {}
        for r in rows:
            by_canon_rows.setdefault(canonical(r.get("query_name") or ""), []).append(r)
        by_canon_hist: dict[str, list[dict]] = {k: list(v) for k, v in by_canon_rows.items()}
        for r in archived:
            by_canon_hist.setdefault(canonical(r.get("query_name") or ""), []).append(r)

        out = []
        for canon_key, b in buckets.items():
            live = by_canon_rows.get(canon_key, [])
            countries = []
            stale = []
            for cc in sorted(b["countries"]):
                crow = [r for r in live if r.get("country") == cc]
                checked = max((str(r.get("searched_at") or "") for r in crow), default="")
                priced = any(r.get("local_price") is not None for r in crow)
                countries.append({"country": cc, "status": "priced" if priced else "unpriced",
                                  "checked_at": checked[:10] or None})
                try:
                    if checked and (today - datetime.fromisoformat(checked[:10]).date()).days > FOREIGN_STALE_DAYS:
                        stale.append(cc)
                except ValueError:
                    pass
            events = classify_price_sessions(by_canon_hist.get(canon_key, []))
            change_counts = {
                "basis": sum(1 for ev in events.values() if any(e["kind"] == "basis" for e in ev)),
                "price": sum(1 for ev in events.values() if any(e["kind"] == "price" for e in ev)),
            }
            out.append({
                "query_name": b["query_name"],
                "canonical": b["canonical"],
                "aliases": sorted(b["aliases"]),
                "last_searched_at": b["last_searched_at"],
                "country_count": len(b["countries"]),
                "has_price": b["has_price"],
                "countries": countries,
                "change_counts": change_counts,
                "stale_countries": stale,
            })
        out.sort(key=lambda x: x["last_searched_at"] or "", reverse=True)
        return out

    def _archived_rows(self, conn, names: list[str] | None) -> list[dict]:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (_ARCHIVE_TABLE,)).fetchone():
            return []
        if names is None:
            return [dict(r) for r in conn.execute(f"SELECT * FROM {_ARCHIVE_TABLE}").fetchall()]
        ph = ",".join(["?"] * len(names))
        return [dict(r) for r in conn.execute(
            f"SELECT * FROM {_ARCHIVE_TABLE} WHERE LOWER(query_name) IN ({ph})", tuple(names)).fetchall()]

    def get_foreign_price_history(self, query_name: str) -> dict:
        """제품(alias 포함)의 국가별 가격 수집 이력 — 현재 행 + 보관 행, 세션 단위 변경 유형 포함.

        결과 메일로 확정된 값(price_request_price_snapshots)은 행마다 표시한다. 요청자·이메일은 내보내지 않는다.
        """
        names = aliases(query_name)
        ph = ",".join(["?"] * len(names))
        with self._connect() as conn:
            live = [dict(r) for r in conn.execute(
                f"SELECT * FROM foreign_drug_prices WHERE LOWER(query_name) IN ({ph})", tuple(names)).fetchall()]
            for r in live:
                r["archived"] = False
            arch = self._archived_rows(conn, names)
            for r in arch:
                r["archived"] = True
            rows = live + arch
            confirmed: dict[int, list[dict]] = {}
            ids = [r["id"] for r in rows if r.get("id") is not None]
            if ids:
                try:
                    for chunk_start in range(0, len(ids), 500):
                        chunk = ids[chunk_start:chunk_start + 500]
                        q = ",".join(["?"] * len(chunk))
                        for s in conn.execute(
                            f"""SELECT s.row_id, s.captured_at, r.notification_status, r.notified_at
                                FROM price_request_price_snapshots s
                                JOIN price_requests r ON r.id = s.request_id
                                WHERE s.row_id IN ({q})""", tuple(chunk)).fetchall():
                            confirmed.setdefault(s[0], []).append({
                                "captured_at": s[1],
                                "emailed": s[2] == "sent",
                                "notified_at": s[3] if s[2] == "sent" else None,
                            })
                except Exception:  # 요청 테이블이 없는 DB(테스트·구버전)
                    confirmed = {}
        keep = ("id", "searched_at", "product_name", "dosage_strength", "package_unit", "pack_count",
                "local_price", "currency", "source_label", "source_url", "formulation_key",
                "formulation_label", "adjusted_price_krw", "archived")
        events = classify_price_sessions(rows)
        countries = {}
        for cc, evs in events.items():
            countries[cc] = []
            for e in evs:
                out_rows = []
                for r in e["rows"]:
                    item = {k: r.get(k) for k in keep}
                    pc = r.get("pack_count")
                    lp = r.get("local_price")
                    item["per_unit_local"] = (round(lp / pc, 4) if (lp is not None and pc and pc > 0) else lp)
                    item["confirmed"] = confirmed.get(r.get("id"), [])
                    out_rows.append(item)
                countries[cc].append({"searched_at": e["searched_at"], "kind": e["kind"], "rows": out_rows})
        return {"query": query_name, "countries": countries}

    def delete_foreign_drug(self, query_name: str) -> int:
        """query_name 에 해당하는 모든 해외 약가 레코드 삭제. alias 전부 함께 제거."""
        names = aliases(query_name)
        placeholders = ",".join(["?"] * len(names))
        with self._connect() as conn:
            cur = conn.execute(
                f"DELETE FROM foreign_drug_prices WHERE LOWER(query_name) IN ({placeholders})",
                tuple(names),
            )
            n = cur.rowcount
            # 사용자가 이력을 지우면 보관된 과거 세션도 함께 지운다(복구 불가 안내와 일치).
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (_ARCHIVE_TABLE,)).fetchone():
                conn.execute(
                    f"DELETE FROM {_ARCHIVE_TABLE} WHERE LOWER(query_name) IN ({placeholders})", tuple(names)
                )
            return n

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
        from agents.exchange_rate import COUNTRY_CURRENCY, per_unit_rate

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
            cur = COUNTRY_CURRENCY.get(r.get("country"))
            # JPY: 저장 행은 100엔당(923.21), 새 캐시는 per-1(9.39) 일 수 있다 — 둘 다 per-1 로 맞춘 뒤 비율을 낸다
            # (안 맞추면 scale 0.0102 → 100× 축소). 새 행에는 per-1 값을 저장한다.
            old_rate = per_unit_rate(cur, r.get("exchange_rate"))
            new_rate = per_unit_rate(cur, rates.get(cur)) if cur else None
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
