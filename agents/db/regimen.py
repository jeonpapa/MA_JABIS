"""투약비용비교 레지멘 저장/조회 (regimen_comparisons).

payload_json = {base: Regimen, comparators: Regimen[], snapshot_date} 통째 직렬화.
레지멘 구조가 가변(약제 2~5)이고 비용 스냅샷성이라 정규화 대신 단일 컬럼 저장
(workbench 시나리오 패턴). owner_email 로 사용자 스코프.
"""
from __future__ import annotations

import json
from datetime import datetime


class _RegimenMixin:
    def list_regimens(self, owner_email: str | None = None) -> list[dict]:
        sql = "SELECT id, name, owner_email, payload_json, created_at, updated_at FROM regimen_comparisons"
        params: tuple = ()
        if owner_email:
            sql += " WHERE owner_email = ?"
            params = (owner_email,)
        sql += " ORDER BY updated_at DESC, id DESC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._regimen_row(r) for r in rows]

    def get_regimen(self, regimen_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, name, owner_email, payload_json, created_at, updated_at "
                "FROM regimen_comparisons WHERE id = ?", (regimen_id,),
            ).fetchone()
        return self._regimen_row(row) if row else None

    def create_regimen(self, name: str, payload: dict, owner_email: str = "") -> int:
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO regimen_comparisons (name, owner_email, payload_json, created_at, updated_at) "
                "VALUES (?,?,?,?,?)",
                (name, owner_email or "", json.dumps(payload, ensure_ascii=False), now, now),
            )
            return cur.lastrowid

    def update_regimen(self, regimen_id: int, name: str, payload: dict) -> bool:
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE regimen_comparisons SET name=?, payload_json=?, updated_at=? WHERE id=?",
                (name, json.dumps(payload, ensure_ascii=False), now, regimen_id),
            )
            return cur.rowcount > 0

    def delete_regimen(self, regimen_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM regimen_comparisons WHERE id=?", (regimen_id,))
            return cur.rowcount > 0

    @staticmethod
    def _regimen_row(r) -> dict:
        d = dict(r)
        try:
            d["payload"] = json.loads(d.pop("payload_json") or "{}")
        except Exception:
            d["payload"] = {}
        return d
