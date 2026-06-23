"""항암 레지멘 정본 조회 (onco_regimen / onco_regimen_drug).

oncology_regimen_db.xlsx 적재본. 레지멘 검색·약제 로드·'구성가능 조합'(약제 포함 레지멘)
조회. 약가 계산은 agents/onco_dosing.py + price-as-of 가 담당.
"""
from __future__ import annotations

import json
from datetime import datetime

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

    # ── 사용자 커스텀 레지멘 라이브러리 (영구·검색 공유) ──
    _CR_DRUG_KEYS = ("ingredient", "dose_value", "unit", "dose_days", "per_cycle",
                     "cycle_days", "cycle_label", "total_cycles", "route", "note", "verify",
                     "price_inn", "price_source", "price_ref")

    def english_inn_from_kr(self, name_kr: str) -> str | None:
        """한글 제품명 → 영문 INN(가격조회용). drug_latest.product_name_kr 매칭 → 영문 ingredient 첫 단어."""
        import re as _re
        name_kr = (name_kr or "").strip()
        if not name_kr:
            return None
        # 괄호/제형 suffix 제거한 핵심 브랜드명으로 매칭
        core = _re.sub(r"[(_].*$", "", name_kr).strip() or name_kr
        with self._connect() as conn:
            row = conn.execute(
                "SELECT ingredient FROM drug_latest WHERE product_name_kr LIKE ? "
                "AND ingredient IS NOT NULL ORDER BY (max_price IS NULL), max_price DESC LIMIT 1",
                (f"%{core}%",),
            ).fetchone()
        if not row or not row[0]:
            return None
        m = _re.match(r"[A-Za-z][A-Za-z-]+", row[0].strip())
        return m.group(0) if m else None

    def save_custom_regimen(self, name: str, rows: list[dict], owner_email: str = "",
                            cancer: str = "") -> int:
        """커스텀 레지멘 저장. (name, owner) 동일 시 갱신, 아니면 신규. ref 반환."""
        name = (name or "").strip()
        if not name or not rows:
            return 0
        clean = [{k: r.get(k) for k in self._CR_DRUG_KEYS} for r in rows]
        rows_json = json.dumps(clean, ensure_ascii=False)
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            ex = conn.execute(
                "SELECT ref FROM custom_regimen WHERE name=? AND IFNULL(owner_email,'')=?",
                (name, owner_email or ""),
            ).fetchone()
            if ex:
                conn.execute(
                    "UPDATE custom_regimen SET rows_json=?, cancer=?, updated_at=? WHERE ref=?",
                    (rows_json, cancer, now, ex[0]),
                )
                return ex[0]
            cur = conn.execute(
                "INSERT INTO custom_regimen (name, cancer, owner_email, rows_json, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?)",
                (name, cancer, owner_email or "", rows_json, now, now),
            )
            return cur.lastrowid

    def _cr_rows(self, rows_json: str) -> list[dict]:
        try:
            return json.loads(rows_json or "[]")
        except (json.JSONDecodeError, TypeError):
            return []

    def get_custom_regimen(self, ref: int) -> dict | None:
        with self._connect() as conn:
            r = conn.execute(
                "SELECT ref, name, cancer, owner_email, rows_json FROM custom_regimen WHERE ref=?", (ref,),
            ).fetchone()
        if not r:
            return None
        d = dict(r)
        d["drugs"] = self._cr_rows(d.pop("rows_json"))
        return d

    def search_custom_regimens(self, q: str, limit: int = 30) -> list[dict]:
        """커스텀 레지멘 검색(이름·암종·약제 부분일치). 전체 공유."""
        q = (q or "").strip()
        if not q:
            return []
        kw = f"%{q}%"
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT ref, name, cancer, owner_email, rows_json FROM custom_regimen "
                "WHERE name LIKE ? OR cancer LIKE ? OR rows_json LIKE ? ORDER BY updated_at DESC LIMIT ?",
                (kw, kw, kw, limit),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            drugs = self._cr_rows(d.pop("rows_json"))
            out.append({"ref": d["ref"], "regimen_id": f"c{d['ref']}", "cancer": d.get("cancer") or "내 레지멘",
                        "regimen_name": d["name"], "line": "", "therapy": "",
                        "drug_count": len(drugs), "drug_names": [x.get("ingredient") for x in drugs],
                        "source_kind": "custom"})
        return out

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
