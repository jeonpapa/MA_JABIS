"""허가사항 기반 용법용량 파싱 결과 캐시 (dosing_resolved).

투약비용비교 치료비 계산을 위해 MFDS 허가사항 usage_text 를 구조화한 dosing 을
영구 저장(캐시-DB-first). cache_key: 국내=normalized_name, WAP=main_ingredient_code.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

_DOSING_COLS = (
    "cache_key", "usage_text", "schedule", "daily_dose_units", "daily_dose_mg",
    "cycle_days", "doses_per_cycle", "per_kg_mg", "per_m2_mg",
    "representative_indication", "alternatives_json", "confidence",
    "source", "model", "resolved_at", "ttl_days",
)


class _DosingMixin:
    def get_dosing(self, cache_key: str, *, fresh_only: bool = True) -> dict | None:
        """cache_key 의 파싱된 dosing. fresh_only 면 TTL 초과 시 None."""
        if not cache_key:
            return None
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {','.join(_DOSING_COLS)} FROM dosing_resolved WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["alternatives"] = json.loads(d.pop("alternatives_json") or "[]")
        except (json.JSONDecodeError, TypeError):
            d["alternatives"] = []
        if fresh_only and not self._dosing_fresh(d):
            return None
        return d

    def save_dosing(self, rec: dict) -> None:
        """dosing_resolved upsert. rec 는 cache_key 필수 + dosing 필드."""
        if not rec.get("cache_key"):
            return
        rec = dict(rec)
        if "alternatives" in rec and "alternatives_json" not in rec:
            rec["alternatives_json"] = json.dumps(rec.pop("alternatives") or [], ensure_ascii=False)
        rec.setdefault("resolved_at", datetime.now().isoformat(timespec="seconds"))
        rec.setdefault("ttl_days", 30)
        vals = [rec.get(c) for c in _DOSING_COLS]
        placeholders = ",".join(["?"] * len(_DOSING_COLS))
        updates = ",".join(f"{c}=excluded.{c}" for c in _DOSING_COLS if c != "cache_key")
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO dosing_resolved ({','.join(_DOSING_COLS)}) VALUES ({placeholders}) "
                f"ON CONFLICT(cache_key) DO UPDATE SET {updates}",
                vals,
            )

    @staticmethod
    def _dosing_fresh(d: dict) -> bool:
        try:
            resolved = datetime.fromisoformat(d["resolved_at"])
        except (ValueError, TypeError, KeyError):
            return False
        ttl = int(d.get("ttl_days") or 30)
        return datetime.now() < resolved + timedelta(days=ttl)
