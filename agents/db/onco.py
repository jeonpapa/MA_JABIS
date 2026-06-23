"""항암 레지멘 정본 조회 (onco_regimen / onco_regimen_drug).

oncology_regimen_db.xlsx 적재본. 레지멘 검색·약제 로드·'구성가능 조합'(약제 포함 레지멘)
조회. 약가 계산은 agents/onco_dosing.py + price-as-of 가 담당.
"""
from __future__ import annotations

_DRUG_COLS = ("seq", "ingredient", "drug_group", "dose_value", "unit", "dose_days",
              "per_cycle", "cycle_days", "cycle_label", "total_cycles", "route",
              "note", "src", "verify")
_REG_COLS = ("ref", "regimen_id", "cancer_no", "cancer", "regimen_name", "therapy", "line", "drug_group")


class _OncoMixin:
    def _onco_drugs(self, conn, ref: int) -> list[dict]:
        rows = conn.execute(
            f"SELECT {','.join(_DRUG_COLS)} FROM onco_regimen_drug WHERE regimen_ref=? ORDER BY seq",
            (ref,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_onco_regimen(self, ref: int) -> dict | None:
        with self._connect() as conn:
            r = conn.execute(
                f"SELECT {','.join(_REG_COLS)} FROM onco_regimen WHERE ref=?", (ref,),
            ).fetchone()
            if not r:
                return None
            d = dict(r)
            d["drugs"] = self._onco_drugs(conn, ref)
            return d

    def search_onco_regimens(self, q: str, limit: int = 30) -> list[dict]:
        """레지멘명·암종·약제성분 부분일치. 약제 미리보기 포함."""
        q = (q or "").strip()
        if not q:
            return []
        kw = f"%{q}%"
        with self._connect() as conn:
            rows = conn.execute(
                f"""SELECT {','.join('r.'+c for c in _REG_COLS)}
                    FROM onco_regimen r
                    WHERE r.regimen_name LIKE ? OR r.cancer LIKE ? OR r.ref IN (
                        SELECT regimen_ref FROM onco_regimen_drug WHERE ingredient LIKE ?)
                    ORDER BY r.cancer_no, r.ref
                    LIMIT ?""",
                (kw, kw, kw, limit),
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                drugs = self._onco_drugs(conn, d["ref"])
                d["drug_count"] = len(drugs)
                d["drug_names"] = [x["ingredient"] for x in drugs]
                out.append(d)
            return out

    def onco_drug_default(self, inn: str) -> dict | None:
        """INN 의 항암 레지멘 DB 대표 dosing(최빈 용량값+단위). 단일약제 추가 기본값."""
        inn = (inn or "").strip()
        if not inn:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """SELECT ingredient, dose_value, unit, dose_days, per_cycle, cycle_days,
                          cycle_label, total_cycles, route, COUNT(*) c
                   FROM onco_regimen_drug WHERE ingredient LIKE ?
                   GROUP BY dose_value, unit, per_cycle, cycle_days
                   ORDER BY c DESC, (verify='NCCN확인') DESC LIMIT 1""",
                (f"%{inn}%",),
            ).fetchone()
        return dict(row) if row else None

    # ── 사용자 영구 저장 dosing (drug_key = lower INN) ──
    _UDOSE_COLS = ("drug_key", "ingredient", "dose_value", "unit", "dose_days", "per_cycle",
                   "cycle_days", "cycle_label", "total_cycles", "route", "updated_at")

    def get_user_dosing(self, drug_key: str) -> dict | None:
        if not drug_key:
            return None
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {','.join(self._UDOSE_COLS)} FROM user_drug_dosing WHERE drug_key=?",
                (drug_key.strip().lower(),),
            ).fetchone()
        return dict(row) if row else None

    def save_user_dosing(self, drug_key: str, rec: dict) -> None:
        from datetime import datetime
        if not drug_key:
            return
        rec = {**rec, "drug_key": drug_key.strip().lower(),
               "updated_at": datetime.now().isoformat(timespec="seconds")}
        vals = [rec.get(c) for c in self._UDOSE_COLS]
        ph = ",".join(["?"] * len(self._UDOSE_COLS))
        upd = ",".join(f"{c}=excluded.{c}" for c in self._UDOSE_COLS if c != "drug_key")
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO user_drug_dosing ({','.join(self._UDOSE_COLS)}) VALUES ({ph}) "
                f"ON CONFLICT(drug_key) DO UPDATE SET {upd}", vals,
            )

    def onco_regimens_with_drug(self, ingredient: str, limit: int = 30) -> list[dict]:
        """특정 약제(INN)를 포함하는 레지멘 = '구성가능 조합' 제시용."""
        ingredient = (ingredient or "").strip()
        if not ingredient:
            return []
        with self._connect() as conn:
            refs = conn.execute(
                "SELECT DISTINCT regimen_ref FROM onco_regimen_drug WHERE ingredient LIKE ? LIMIT ?",
                (f"%{ingredient}%", limit),
            ).fetchall()
            out = []
            for (ref,) in refs:
                reg = self.get_onco_regimen(ref)
                if reg:
                    reg["drug_names"] = [x["ingredient"] for x in reg["drugs"]]
                    out.append(reg)
            return out
