"""적응증(Indication) 마스터 + 국가별 variant CRUD."""
from __future__ import annotations

from datetime import datetime
from typing import Optional


class _IndicationsMixin:
    def upsert_indication_master(self, rec: dict) -> str:
        """anchor row upsert. indication_id 반환.

        rec 필수: indication_id, product
        rec 선택: pivotal_trial, disease, stage, line_of_therapy, population,
                 title, fda_indication_code, biomarker_class
        """
        now = datetime.now().isoformat()
        rec.setdefault("created_at", now)
        rec["updated_at"] = now
        sql = """
            INSERT INTO indications_master
                (indication_id, product, pivotal_trial, disease, stage,
                 line_of_therapy, population, biomarker_class,
                 title, fda_indication_code, created_at, updated_at)
            VALUES (:indication_id, :product, :pivotal_trial, :disease, :stage,
                    :line_of_therapy, :population, :biomarker_class,
                    :title, :fda_indication_code, :created_at, :updated_at)
            ON CONFLICT(indication_id) DO UPDATE SET
                product = excluded.product,
                pivotal_trial = excluded.pivotal_trial,
                disease = excluded.disease,
                stage = excluded.stage,
                line_of_therapy = excluded.line_of_therapy,
                population = excluded.population,
                biomarker_class = excluded.biomarker_class,
                title = excluded.title,
                fda_indication_code = excluded.fda_indication_code,
                updated_at = excluded.updated_at
        """
        for key in ("pivotal_trial", "disease", "stage", "line_of_therapy",
                    "population", "biomarker_class", "title", "fda_indication_code"):
            rec.setdefault(key, None)
        with self._connect() as conn:
            conn.execute(sql, rec)
        return rec["indication_id"]

    def upsert_indication_agency(self, rec: dict) -> None:
        """variant row upsert (indication_id+agency 유니크).

        rec 필수: indication_id, agency
        rec 선택: biomarker_label, combination_label, approval_date,
                 label_excerpt, label_full_text, label_url, restriction_note, raw_source
        """
        rec.setdefault("fetched_at", datetime.now().isoformat())
        for key in ("biomarker_label", "combination_label", "approval_date",
                    "label_excerpt", "label_full_text", "label_url",
                    "restriction_note", "raw_source"):
            rec.setdefault(key, None)
        sql = """
            INSERT INTO indications_by_agency
                (indication_id, agency, biomarker_label, combination_label,
                 approval_date, label_excerpt, label_full_text, label_url,
                 restriction_note, raw_source, fetched_at)
            VALUES (:indication_id, :agency, :biomarker_label, :combination_label,
                    :approval_date, :label_excerpt, :label_full_text, :label_url,
                    :restriction_note, :raw_source, :fetched_at)
            ON CONFLICT(indication_id, agency) DO UPDATE SET
                biomarker_label = excluded.biomarker_label,
                combination_label = excluded.combination_label,
                approval_date = excluded.approval_date,
                label_excerpt = excluded.label_excerpt,
                label_full_text = excluded.label_full_text,
                label_url = excluded.label_url,
                restriction_note = excluded.restriction_note,
                raw_source = excluded.raw_source,
                fetched_at = excluded.fetched_at
        """
        with self._connect() as conn:
            conn.execute(sql, rec)

    def get_indications(self, product: str) -> list[dict]:
        """product slug 로 마스터 적응증 목록 + 각 agency variant 묶음 반환."""
        with self._connect() as conn:
            masters = conn.execute(
                "SELECT * FROM indications_master WHERE product = ? ORDER BY indication_id",
                (product,),
            ).fetchall()
            out = []
            for m in masters:
                m = dict(m)
                variants = conn.execute(
                    "SELECT * FROM indications_by_agency WHERE indication_id = ? ORDER BY agency",
                    (m["indication_id"],),
                ).fetchall()
                m["agencies"] = [dict(v) for v in variants]
                out.append(m)
        return out

    def find_matching_indication(self, product: str, anchor: dict) -> Optional[str]:
        """anchor dict 기반으로 기존 indications_master row 를 찾아 indication_id 반환.

        다른 기관(EMA 등) 이 같은 적응증을 다른 trial 이름 없이 구조화했을 때
        FDA 가 먼저 등록한 row 에 agency variant 만 붙일 수 있도록 돕는 퍼지 매처.

        핵심 규칙:
          - disease 와 biomarker_class 는 필수 anchor.
          - line_of_therapy 는 하드 제약. "adjuvant" 와 "None(palliative)" 는 섞이면 안 됨.
          - stage 는 soft anchor — 좁히기만 하고, 일치 안 하면 stage 무시한 tier 로 폴백.

        Tier 순서 (엄격 → 관대):
          1. (disease, bio, lot, stage)  — 전부 일치
          2. (disease, bio, lot)         — stage 관대
          3. (disease, bio, stage)       — lot 없을 때만
          4. (disease, bio)              — lot 없을 때만
        각 단계에서 단일 row 일 때만 매칭 확정. 다수면 ambiguous → None.
        """
        anchor = anchor or {}
        disease = anchor.get("disease")
        bio     = anchor.get("biomarker_class")
        lot     = anchor.get("line_of_therapy")
        stage   = anchor.get("stage")

        if not disease or not bio:
            return None

        def _query(pairs: list[tuple[str, object]]) -> list[str]:
            where_sql = " AND ".join(f"LOWER({k}) = LOWER(?)" for k, _ in pairs)
            params    = [v for _, v in pairs]
            sql = (
                "SELECT indication_id FROM indications_master "
                f"WHERE product = ? AND {where_sql}"
            )
            with self._connect() as conn:
                rows = conn.execute(sql, [product, *params]).fetchall()
            return [r[0] for r in rows]

        tiers: list[list[tuple[str, object]]] = []
        if lot and stage:
            tiers.append([("disease", disease), ("biomarker_class", bio),
                          ("line_of_therapy", lot), ("stage", stage)])
        if lot:
            tiers.append([("disease", disease), ("biomarker_class", bio),
                          ("line_of_therapy", lot)])
        if not lot and stage:
            tiers.append([("disease", disease), ("biomarker_class", bio),
                          ("stage", stage)])
        if not lot:
            tiers.append([("disease", disease), ("biomarker_class", bio)])

        for tier in tiers:
            matches = _query(tier)
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                return None
        return None

    def get_indication(self, indication_id: str) -> Optional[dict]:
        """단일 적응증 + 모든 agency variant."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM indications_master WHERE indication_id = ?",
                (indication_id,),
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            variants = conn.execute(
                "SELECT * FROM indications_by_agency WHERE indication_id = ? ORDER BY agency",
                (indication_id,),
            ).fetchall()
            d["agencies"] = [dict(v) for v in variants]
        return d

    # ── Approval documents (PDF 업로드) ───────────────────────────────────

    def insert_approval_document(self, rec: dict) -> int:
        """approval_documents 신규 row. 반환: id."""
        rec.setdefault("uploaded_at", datetime.now().isoformat(timespec="seconds"))
        for k in ("original_filename", "file_size", "content_type",
                  "approval_date", "label_excerpt", "label_url",
                  "notes", "uploaded_by"):
            rec.setdefault(k, None)
        sql = """
            INSERT INTO approval_documents
                (indication_id, agency, file_path, original_filename, file_size,
                 content_type, approval_date, label_excerpt, label_url, notes,
                 uploaded_by, uploaded_at)
            VALUES
                (:indication_id, :agency, :file_path, :original_filename, :file_size,
                 :content_type, :approval_date, :label_excerpt, :label_url, :notes,
                 :uploaded_by, :uploaded_at)
        """
        with self._connect() as conn:
            cur = conn.execute(sql, rec)
        return cur.lastrowid or 0

    def list_approval_documents(self, indication_id: str | None = None,
                                 agency: str | None = None,
                                 product: str | None = None) -> list[dict]:
        """업로드된 PDF 메타 리스트. indication_id/agency/product 로 필터."""
        sql = """
            SELECT ad.*, m.product, m.disease, m.title
              FROM approval_documents ad
              JOIN indications_master m ON ad.indication_id = m.indication_id
        """
        clauses = []
        params: list = []
        if indication_id:
            clauses.append("ad.indication_id = ?")
            params.append(indication_id)
        if agency:
            clauses.append("ad.agency = ?")
            params.append(agency)
        if product:
            clauses.append("m.product = ?")
            params.append(product.lower())
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY ad.uploaded_at DESC"
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def get_approval_document(self, doc_id: int) -> Optional[dict]:
        with self._connect() as conn:
            r = conn.execute(
                "SELECT * FROM approval_documents WHERE id = ?", (doc_id,)
            ).fetchone()
        return dict(r) if r else None

    def delete_approval_document(self, doc_id: int) -> Optional[str]:
        """row 삭제 + file_path 반환 (호출자가 파일 unlink). 없으면 None."""
        with self._connect() as conn:
            r = conn.execute(
                "SELECT file_path FROM approval_documents WHERE id = ?", (doc_id,)
            ).fetchone()
            if not r:
                return None
            file_path = r[0]
            conn.execute("DELETE FROM approval_documents WHERE id = ?", (doc_id,))
        return file_path

    def get_approval_grid(self, product: str) -> list[dict]:
        """
        product 의 적응증 grid — 각 적응증 row + 6국 agency 별 PDF count + approval_date.
        UI 매트릭스용 (row=indication, col=agency).
        """
        with self._connect() as conn:
            indications = [dict(r) for r in conn.execute(
                """
                SELECT m.indication_id, m.title, m.disease, m.line_of_therapy,
                       m.stage, m.biomarker_class
                  FROM indications_master m
                 WHERE m.product = ?
                 ORDER BY m.disease, m.line_of_therapy, m.indication_id
                """,
                (product.lower(),),
            ).fetchall()]

            # 각 indication × agency 의 by_agency row + PDF count 집계
            agency_data: dict[tuple[str, str], dict] = {}
            for r in conn.execute(
                """
                SELECT m.indication_id, ia.agency, ia.approval_date,
                       ia.label_url, ia.label_excerpt
                  FROM indications_master m
                  JOIN indications_by_agency ia ON m.indication_id = ia.indication_id
                 WHERE m.product = ?
                """,
                (product.lower(),),
            ).fetchall():
                agency_data[(r[0], r[1])] = {
                    "approval_date": r[2],
                    "label_url":     r[3],
                    "label_excerpt": (r[4] or "")[:200],
                }

            doc_counts: dict[tuple[str, str], int] = {}
            for r in conn.execute(
                """
                SELECT m.indication_id, ad.agency, COUNT(*)
                  FROM approval_documents ad
                  JOIN indications_master m ON ad.indication_id = m.indication_id
                 WHERE m.product = ?
                 GROUP BY m.indication_id, ad.agency
                """,
                (product.lower(),),
            ).fetchall():
                doc_counts[(r[0], r[1])] = r[2]

        agencies = ["FDA", "EMA", "MHRA", "PMDA", "TGA", "MFDS"]
        out = []
        for ind in indications:
            cells = {}
            for ag in agencies:
                cell = dict(agency_data.get((ind["indication_id"], ag), {}))
                cell["doc_count"] = doc_counts.get((ind["indication_id"], ag), 0)
                cells[ag] = cell
            ind["agencies"] = cells
            out.append(ind)
        return out

    def get_indication_pricing(self, indication_id: str) -> dict:
        """
        한 적응증의 다국가 가격 dict.
        indications_master.product → product_alias_map.brand_aliases (또는 in-memory aliases)
        → foreign_drug_prices LATEST per (country, form_type) join.

        Returns: {country: [price_row, ...], ...}
        """
        with self._connect() as conn:
            mrow = conn.execute(
                "SELECT product FROM indications_master WHERE indication_id = ?",
                (indication_id,),
            ).fetchone()
            if not mrow:
                return {}
            product = mrow[0]

            # product_alias_map 조회 — 없으면 in-memory aliases() fallback
            alias_row = conn.execute(
                "SELECT brand_aliases_json, inn FROM product_alias_map "
                "WHERE product_slug = ?",
                (product.lower(),),
            ).fetchone()

        import json as _json
        candidate_names: set[str] = {product.lower()}
        if alias_row:
            try:
                ba = _json.loads(alias_row[0] or "[]")
                for x in ba:
                    if x:
                        candidate_names.add(str(x).lower())
            except Exception:
                pass
            if alias_row[1]:
                candidate_names.add(str(alias_row[1]).lower())
        else:
            # fallback: in-memory aliases (drug_aliases.py)
            try:
                from .drug_aliases import aliases as _aliases
                for x in _aliases(product):
                    candidate_names.add(x.lower())
            except Exception:
                pass

        if not candidate_names:
            return {}

        placeholders = ",".join(["?"] * len(candidate_names))
        sql = f"""
            SELECT f.*
            FROM foreign_drug_prices f
            INNER JOIN (
                SELECT country, form_type, MAX(searched_at) AS latest
                FROM foreign_drug_prices
                WHERE LOWER(query_name) IN ({placeholders})
                GROUP BY country, form_type
            ) m ON f.country = m.country
                  AND COALESCE(f.form_type,'') = COALESCE(m.form_type,'')
                  AND f.searched_at = m.latest
            WHERE LOWER(f.query_name) IN ({placeholders})
            ORDER BY f.country
        """
        params = tuple(candidate_names) * 2
        with self._connect() as conn:
            rows = [dict(r) for r in conn.execute(sql, params).fetchall()]

        out: dict[str, list[dict]] = {}
        for r in rows:
            out.setdefault(r["country"], []).append(r)
        return out
