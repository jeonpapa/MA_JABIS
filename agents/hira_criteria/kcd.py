"""심평원 상병마스터(KCD) 적재·조회.

- 원천: 보험인정기준 > 자료실 '배포용 상병마스터_YYMMDD.xlsx' (sources.kcd_master_latest 로 최신 탐색).
- 시트 '상병분류기호(전체)': 표제어 행 다음에 같은 코드의 동의어 행이 반복된다 → 첫 행 = name_kr, 나머지 = kcd_alias.
- 코드는 점 없이 저장(C349). 표시용 dotted(): C34.9 / C34.90.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Iterable

SHEET_ALL = "상병분류기호(전체)"


def load_master_rows(xlsx: Path) -> list[dict]:
    import openpyxl
    wb = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
    ws = wb[SHEET_ALL] if SHEET_ALL in wb.sheetnames else next(
        s for s in wb.worksheets if "전체" in s.title)
    rows, header = [], None
    for row in ws.iter_rows(values_only=True):
        cells = [("" if c is None else str(c)).strip() for c in row]
        if header is None:
            if "상병기호" in cells:
                header = {name: i for i, name in enumerate(cells) if name}
            continue
        code = cells[header["상병기호"]] if header.get("상병기호") is not None else ""
        if not re.fullmatch(r"[A-Z][0-9A-Z]{2,6}", code or ""):
            continue
        def col(*names):
            for n in names:
                for k, i in header.items():
                    if k.replace(" ", "").startswith(n):
                        return cells[i]
            return ""
        rows.append({
            "code": code,
            "name_kr": col("한글명"),
            "name_en": col("영문영", "영문명"),
            "is_complete": 0 if col("완전코드구분") == "N" else 1,
            "main_dx_ok": 0 if col("주상병사용구분") == "N" else 1,
        })
    if len(rows) < 10000:
        raise ValueError(f"상병마스터 행 수 비정상: {len(rows)}")
    return rows


def store(conn: sqlite3.Connection, rows: Iterable[dict], version: str) -> dict:
    conn.execute("DELETE FROM kcd_code")
    conn.execute("DELETE FROM kcd_alias")
    n_code = n_alias = 0
    seen = set()
    for r in rows:
        if r["code"] not in seen:
            seen.add(r["code"])
            conn.execute("INSERT INTO kcd_code(code,name_kr,name_en,is_complete,main_dx_ok,version) VALUES (?,?,?,?,?,?)",
                         (r["code"], r["name_kr"], r["name_en"], r["is_complete"], r["main_dx_ok"], version))
            n_code += 1
        if r["name_kr"]:
            cur = conn.execute("INSERT OR IGNORE INTO kcd_alias(code,name_kr,name_en) VALUES (?,?,?)",
                               (r["code"], r["name_kr"], r["name_en"]))
            n_alias += cur.rowcount
    return {"codes": n_code, "aliases": n_alias, "version": version}


def dotted(code: str) -> str:
    return code if len(code) <= 3 else f"{code[:3]}.{code[3:]}"


def expand_range(spec: str) -> list[str]:
    """'C18-C20' → ['C18','C19','C20'] (3단). 'C34' → ['C34']. 'C220' → ['C220']."""
    spec = spec.strip().replace(".", "")
    m = re.fullmatch(r"([A-Z])(\d{2})-(?:[A-Z])?(\d{2})", spec)
    if m:
        a, b = int(m.group(2)), int(m.group(3))
        return [f"{m.group(1)}{i:02d}" for i in range(a, b + 1)]
    return [spec]


def exists(conn: sqlite3.Connection, code: str) -> bool:
    return conn.execute("SELECT 1 FROM kcd_code WHERE code=?", (code,)).fetchone() is not None


def name(conn: sqlite3.Connection, code: str) -> str | None:
    r = conn.execute("SELECT name_kr FROM kcd_code WHERE code=?", (code,)).fetchone()
    return r[0] if r else None


def search_name(conn: sqlite3.Connection, term: str, limit: int = 20) -> list[tuple[str, str]]:
    """한글 질환명으로 KCD 후보 검색(표제어+동의어). 3단(짧은 코드) 우선."""
    q = f"%{term}%"
    rows = conn.execute(
        "SELECT DISTINCT a.code, k.name_kr FROM kcd_alias a JOIN kcd_code k USING(code) "
        "WHERE a.name_kr LIKE ? ORDER BY length(a.code), a.code LIMIT ?", (q, limit)).fetchall()
    return [(r[0], r[1]) for r in rows]
