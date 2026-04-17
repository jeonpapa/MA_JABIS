"""fragmented indication master 병합.

같은 (disease, biomarker_class) 그룹 내에서:
  1. pivotal_trial + LoT + combo + special-lot 키가 같은 row 통합
  2. LoT 없는 row 를 단일 palliative bucket 으로 흡수
  3. LoT 버킷 내 stage variant 병합 (adv_meta / resectable / adjuvant 군 → 대표 row)

병합 시 src 의 agency variant 중 dst 에 없는 것만 dst 로 이동.
"""
from __future__ import annotations

import logging
import sqlite3
from collections import defaultdict

logger = logging.getLogger(__name__)

_STAGE_GROUPS: dict[str, str] = {
    "metastatic": "adv_meta",
    "advanced": "adv_meta",
    "locally advanced": "adv_meta",
    "locally advanced or metastatic": "adv_meta",
    "locally advanced unresectable or metastatic": "adv_meta",
    "locally advanced unresectable": "adv_meta",
    "unresectable": "adv_meta",
    "unresectable, recurrent": "adv_meta",
    "unresectable or metastatic": "adv_meta",
    "recurrent or metastatic": "adv_meta",
    "recurrent": "adv_meta",
    "resectable": "resectable",
    "adjuvant": "adjuvant",
}

_SPECIAL_LOTS = {"adjuvant", "neoadjuvant", "perioperative"}
_SPECIAL_LOTS_LOW = {s.lower() for s in _SPECIAL_LOTS}


class _MergerMixin:
    _STAGE_GROUPS = _STAGE_GROUPS  # 외부 참조용 (backward compat)

    def merge(self, product_slug: str, dry_run: bool = False) -> dict:
        """같은 적응증으로 판단되는 fragmented masters를 병합.

        Returns: {"merged": int, "details": [...]}
        """
        rows = self.db.get_indications(product_slug)
        by_dx_bio: dict[tuple, list[dict]] = defaultdict(list)
        for r in rows:
            key = ((r.get("disease") or "").lower(),
                   (r.get("biomarker_class") or "").lower())
            by_dx_bio[key].append(r)

        merged_count = 0
        details: list[dict] = []

        for (_dx, _bio), group in by_dx_bio.items():
            if len(group) < 2:
                continue
            merged_count += self._merge_group(group, dry_run, details)

        if not dry_run:
            logger.info("[merge %s] %d건 병합 완료", product_slug, merged_count)
        return {"merged": merged_count, "details": details}

    def _merge_group(self, group: list[dict], dry_run: bool, details: list) -> int:
        from agents.research.combo_normalizer import normalize_combo as _nrm

        merged = 0
        lot_buckets: dict[str, list[dict]] = {}
        no_lot: list[dict] = []
        for r in group:
            lot = (r.get("line_of_therapy") or "").strip()
            if not lot or lot == "-":
                no_lot.append(r)
            else:
                lot_buckets.setdefault(lot.lower(), []).append(r)

        palliative_buckets = {k: v for k, v in lot_buckets.items()
                              if k not in _SPECIAL_LOTS}
        for r in no_lot:
            if len(palliative_buckets) == 1:
                target_lot, targets = next(iter(palliative_buckets.items()))
                target = self._pick_best_target(targets)
                if target and target["indication_id"] != r["indication_id"]:
                    merged += self._do_merge(r, target, dry_run, details,
                                             reason=f"lot=- → {target_lot}")

        def _combo_key(r: dict) -> str:
            keys = sorted({_nrm(a.get("combination_label"))
                           for a in (r.get("agencies") or [])
                           if a.get("combination_label")})
            keys = [k for k in keys if k]
            return "|".join(keys) if keys else ""

        def _is_special_lot(r: dict) -> bool:
            lot = (r.get("line_of_therapy") or "").lower().strip()
            return any(s in lot for s in _SPECIAL_LOTS_LOW)

        def _lot_norm(r: dict) -> str:
            return (r.get("line_of_therapy") or "").lower().strip().rstrip("+")

        trial_buckets: dict[tuple, list[dict]] = {}
        for r in group:
            trial = (r.get("pivotal_trial") or "").strip()
            if not trial:
                continue
            key = (trial, _lot_norm(r), _combo_key(r), _is_special_lot(r))
            trial_buckets.setdefault(key, []).append(r)

        merged_ids: set[str] = set()
        for rows in trial_buckets.values():
            if len(rows) < 2:
                continue
            target = self._pick_best_target(rows)
            for r in rows:
                if r["indication_id"] != target["indication_id"]:
                    merged += self._do_merge(r, target, dry_run, details,
                                             reason=f"same trial {r.get('pivotal_trial')}")
                    merged_ids.add(r["indication_id"])

        group = [r for r in group if r["indication_id"] not in merged_ids]
        lot_buckets = {k: [r for r in v if r["indication_id"] not in merged_ids]
                       for k, v in lot_buckets.items()}
        lot_buckets = {k: v for k, v in lot_buckets.items() if v}
        no_lot = [r for r in no_lot if r["indication_id"] not in merged_ids]

        def _merge_stage_variants(bucket: list[dict]) -> int:
            if len(bucket) < 2:
                return 0
            count = 0
            stage_groups: dict[str, list[dict]] = {}
            for r in bucket:
                sg = self._stage_group(r.get("stage") or "")
                stage_groups.setdefault(sg, []).append(r)
            for sg_key, sg_rows in stage_groups.items():
                if len(sg_rows) < 2 or not sg_key:
                    continue
                combo_buckets: dict[str, list[dict]] = {}
                for r in sg_rows:
                    ck = _combo_key(r)
                    combo_buckets.setdefault(ck, []).append(r)
                for cb_rows in combo_buckets.values():
                    if len(cb_rows) < 2:
                        continue
                    target = self._pick_best_target(cb_rows)
                    for r in cb_rows:
                        if r["indication_id"] != target["indication_id"]:
                            count += self._do_merge(r, target, dry_run, details,
                                                    reason=f"stage variant ({sg_key}) → {target['stage']}")
            return count

        for _lot_key, bucket in lot_buckets.items():
            merged += _merge_stage_variants(bucket)

        if len(no_lot) >= 2:
            merged += _merge_stage_variants(no_lot)

        return merged

    def _stage_group(self, stage: str) -> str:
        return _STAGE_GROUPS.get(stage.lower().strip(), stage.lower().strip())

    def _pick_best_target(self, candidates: list[dict]) -> dict:
        return max(candidates, key=lambda r: len(r.get("agencies") or []))

    def _do_merge(self, src: dict, dst: dict, dry_run: bool, details: list,
                  reason: str) -> int:
        src_id = src["indication_id"]
        dst_id = dst["indication_id"]
        src_agencies = {a["agency"] for a in (src.get("agencies") or [])}
        dst_agencies = {a["agency"] for a in (dst.get("agencies") or [])}

        details.append({
            "src": src_id, "dst": dst_id, "reason": reason,
            "src_agencies": sorted(src_agencies),
            "dst_agencies": sorted(dst_agencies),
        })

        if dry_run:
            return 1

        with sqlite3.connect(str(self.db_path)) as c:
            existing = {r[0] for r in c.execute(
                "SELECT agency FROM indications_by_agency WHERE indication_id = ?",
                (dst_id,),
            ).fetchall()}
            for a in (src.get("agencies") or []):
                if a["agency"] not in existing:
                    c.execute(
                        "UPDATE indications_by_agency SET indication_id = ? "
                        "WHERE indication_id = ? AND agency = ?",
                        (dst_id, src_id, a["agency"]),
                    )
            c.execute("DELETE FROM indications_by_agency WHERE indication_id = ?",
                      (src_id,))
            c.execute("DELETE FROM indications_master WHERE indication_id = ?",
                      (src_id,))
            c.commit()
        return 1
