"""product 별 기관 커버리지 매트릭스."""
from __future__ import annotations

from collections import defaultdict


class _MatrixMixin:
    def matrix(self, product_slug: str) -> dict:
        """product 의 indication 커버리지 매트릭스.

        Returns:
            {
              "product": str,
              "totals":  {"masters": int, "fda_agency": int, "ema_agency": int,
                          "both": int, "fda_only": int, "ema_only": int, ...},
              "by_disease": [
                  {"disease": str, "masters": int, "fda": int, "ema": int, ...}, ...
              ],
              "rows": [
                  {"indication_id": str, "disease": str, "lot": str, "stage": str,
                   "biomarker_class": str, "agencies": ["FDA","EMA"]}, ...
              ],
            }
        """
        rows = self.db.get_indications(product_slug)
        agency_sets = [
            {a["agency"] for a in (r.get("agencies") or [])}
            for r in rows
        ]
        fda_count  = sum(1 for s in agency_sets if "FDA"  in s)
        ema_count  = sum(1 for s in agency_sets if "EMA"  in s)
        pmda_count = sum(1 for s in agency_sets if "PMDA" in s)
        mfds_count = sum(1 for s in agency_sets if "MFDS" in s)
        mhra_count = sum(1 for s in agency_sets if "MHRA" in s)
        tga_count  = sum(1 for s in agency_sets if "TGA"  in s)
        both      = sum(1 for s in agency_sets if {"FDA", "EMA"} <= s)
        all_three = sum(1 for s in agency_sets if {"FDA", "EMA", "PMDA"} <= s)
        all_four  = sum(1 for s in agency_sets if {"FDA", "EMA", "PMDA", "MFDS"} <= s)
        all_five  = sum(1 for s in agency_sets if {"FDA", "EMA", "PMDA", "MFDS", "MHRA"} <= s)
        all_six   = sum(1 for s in agency_sets if {"FDA", "EMA", "PMDA", "MFDS", "MHRA", "TGA"} <= s)

        by_dx_acc: dict[str, dict] = defaultdict(
            lambda: {"masters": 0, "fda": 0, "ema": 0, "pmda": 0, "mfds": 0, "mhra": 0, "tga": 0}
        )
        for r, s in zip(rows, agency_sets):
            dx = r.get("disease") or "-"
            by_dx_acc[dx]["masters"] += 1
            if "FDA"  in s: by_dx_acc[dx]["fda"]  += 1
            if "EMA"  in s: by_dx_acc[dx]["ema"]  += 1
            if "PMDA" in s: by_dx_acc[dx]["pmda"] += 1
            if "MFDS" in s: by_dx_acc[dx]["mfds"] += 1
            if "MHRA" in s: by_dx_acc[dx]["mhra"] += 1
            if "TGA"  in s: by_dx_acc[dx]["tga"]  += 1
        by_disease = [{"disease": k, **v} for k, v in sorted(by_dx_acc.items())]

        out_rows = []
        for r, s in zip(rows, agency_sets):
            out_rows.append({
                "indication_id":   r["indication_id"],
                "disease":         r.get("disease"),
                "line_of_therapy": r.get("line_of_therapy"),
                "stage":           r.get("stage"),
                "biomarker_class": r.get("biomarker_class"),
                "pivotal_trial":   r.get("pivotal_trial"),
                "agencies":        sorted(s),
            })

        return {
            "product": product_slug,
            "totals": {
                "masters":     len(rows),
                "fda_agency":  fda_count,
                "ema_agency":  ema_count,
                "pmda_agency": pmda_count,
                "mfds_agency": mfds_count,
                "mhra_agency": mhra_count,
                "tga_agency":  tga_count,
                "both":        both,
                "all_three":   all_three,
                "all_four":    all_four,
                "all_five":    all_five,
                "all_six":     all_six,
                "fda_only":    sum(1 for s in agency_sets if s == {"FDA"}),
                "ema_only":    sum(1 for s in agency_sets if s == {"EMA"}),
                "pmda_only":   sum(1 for s in agency_sets if s == {"PMDA"}),
                "mfds_only":   sum(1 for s in agency_sets if s == {"MFDS"}),
                "mhra_only":   sum(1 for s in agency_sets if s == {"MHRA"}),
                "tga_only":    sum(1 for s in agency_sets if s == {"TGA"}),
            },
            "by_disease": by_disease,
            "rows":       out_rows,
        }
