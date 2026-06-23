"""oncology_regimen_db.xlsx '레지멘DB' 시트 → onco_regimen / onco_regimen_drug 적재.

HIRA 항암요법 공고 기반 정본(144 레지멘 / 251 약제행). 레지멘 메타(레지멘ID·암종…)는
ID 채워진 행에만 있고 후속 약제행은 빈칸 → forward-fill. 단위 문자열 정규화(mg/m² → mg/m2).
재적재 idempotent(레지멘 단위 delete→insert).

CLI: python -m agents.scrapers.onco_regimen_ingest  (또는 server --ingest-onco)
"""
from __future__ import annotations

import logging
from pathlib import Path

import openpyxl

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[2]
XLSX_PATH = BASE_DIR / "data" / "onco_regimen_db.xlsx"
SHEET = "레지멘DB"


def _norm_unit(u: str | None) -> str | None:
    if u is None:
        return None
    return str(u).strip().replace("²", "2").replace("㎡", "m2")


def _to_num(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v):
    n = _to_num(v)
    return int(n) if n is not None else None


def parse_rows(xlsx_path: Path = XLSX_PATH) -> tuple[list[dict], list[dict]]:
    """엑셀 → (regimens, drugs). 각 레지멘 블록에 합성 ref 부여(원본 regimen_id 잘림 중복 대응)."""
    wb = openpyxl.load_workbook(str(xlsx_path), read_only=True, data_only=True)
    ws = wb[SHEET]
    rows = list(ws.iter_rows(values_only=True))[1:]  # skip header
    regimens: list[dict] = []
    drugs: list[dict] = []
    cur_ref = None
    seq = 0
    for r in rows:
        rid = (r[0] or "").strip() if r[0] else ""
        ingredient = (r[6] or "").strip() if r[6] else ""
        if rid:                       # 새 레지멘 블록 시작
            cur_ref = len(regimens) + 1
            seq = 0
            regimens.append({
                "ref": cur_ref, "regimen_id": rid, "cancer_no": _to_int(r[1]), "cancer": r[2],
                "regimen_name": r[3], "therapy": r[4], "line": r[5],
                "drug_group": str(r[7]) if r[7] else None,
            })
        if not cur_ref or not ingredient:
            continue
        seq += 1
        drugs.append({
            "regimen_ref": cur_ref, "seq": seq, "ingredient": ingredient,
            "drug_group": str(r[7]) if r[7] else None,
            "dose_value": _to_num(r[8]), "unit": _norm_unit(r[9]), "dose_days": r[10],
            "per_cycle": _to_num(r[11]), "cycle_days": _to_int(r[12]), "cycle_label": r[13],
            "total_cycles": _to_num(r[14]), "route": r[15], "note": r[16],
            "src": r[19], "verify": r[20],
        })
    return regimens, drugs


def ingest(db, xlsx_path: Path = XLSX_PATH) -> tuple[int, int]:
    """DrugPriceDB 에 적재. 반환 (레지멘수, 약제행수)."""
    regimens, drugs = parse_rows(xlsx_path)
    reg_cols = ("ref", "regimen_id", "cancer_no", "cancer", "regimen_name", "therapy", "line", "drug_group")
    drug_cols = ("regimen_ref", "seq", "ingredient", "drug_group", "dose_value", "unit", "dose_days",
                 "per_cycle", "cycle_days", "cycle_label", "total_cycles", "route", "note", "src", "verify")
    with db._connect() as conn:
        conn.execute("DELETE FROM onco_regimen_drug")
        conn.execute("DELETE FROM onco_regimen")
        conn.executemany(
            f"INSERT INTO onco_regimen ({','.join(reg_cols)}) VALUES ({','.join('?'*len(reg_cols))})",
            [tuple(r.get(c) for c in reg_cols) for r in regimens],
        )
        conn.executemany(
            f"INSERT INTO onco_regimen_drug ({','.join(drug_cols)}) VALUES ({','.join('?'*len(drug_cols))})",
            [tuple(d.get(c) for c in drug_cols) for d in drugs],
        )
    logger.info("[onco] 적재 완료: 레지멘 %d / 약제행 %d", len(regimens), len(drugs))
    return len(regimens), len(drugs)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from agents.db import DrugPriceDB
    db = DrugPriceDB(BASE_DIR / "data" / "db" / "drug_prices.db")
    n_reg, n_drug = ingest(db)
    print(f"OK — onco_regimen {n_reg} / onco_regimen_drug {n_drug}")
