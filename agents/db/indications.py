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
                 label_excerpt, label_url, restriction_note, raw_source
        """
        rec.setdefault("fetched_at", datetime.now().isoformat())
        for key in ("biomarker_label", "combination_label", "approval_date",
                    "label_excerpt", "label_url", "restriction_note", "raw_source"):
            rec.setdefault(key, None)
        sql = """
            INSERT INTO indications_by_agency
                (indication_id, agency, biomarker_label, combination_label,
                 approval_date, label_excerpt, label_url, restriction_note,
                 raw_source, fetched_at)
            VALUES (:indication_id, :agency, :biomarker_label, :combination_label,
                    :approval_date, :label_excerpt, :label_url, :restriction_note,
                    :raw_source, :fetched_at)
            ON CONFLICT(indication_id, agency) DO UPDATE SET
                biomarker_label = excluded.biomarker_label,
                combination_label = excluded.combination_label,
                approval_date = excluded.approval_date,
                label_excerpt = excluded.label_excerpt,
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
