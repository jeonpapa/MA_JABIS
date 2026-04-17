"""약제 부가정보 캐시 (drug_enrichment): RSA / 용법용량 / 허가일."""
from __future__ import annotations

import json as _json
from datetime import datetime as _dt


class _EnrichmentMixin:
    def get_enrichment(self, normalized_name: str):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM drug_enrichment WHERE normalized_name = ?",
                (normalized_name,),
            ).fetchone()
        return dict(row) if row else None

    def save_enrichment(self, rec: dict) -> None:
        fields = [
            "normalized_name", "representative_code", "insurance_codes_json",
            "is_rsa", "rsa_type", "rsa_note", "approval_date",
            "usage_text", "daily_dose_units", "dose_schedule",
            "cycle_days", "doses_per_cycle", "sources_json",
            "confidence", "notes", "fetched_at", "ttl_days",
        ]
        rec.setdefault("fetched_at", _dt.utcnow().isoformat() + "Z")
        rec.setdefault("ttl_days", 30)
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
