"""RSA/사후조건 미디어 보완 대상 worklist 생성 (중복 약제 제거).

대상: analog_reports has_rsa=1 OR has_postmarket_condition=1 AND first_reimbursement_date 보유.
정제 브랜드+성분으로 중복 약제 묶음 → 약제당 1건 리서치(여러 report_id 매핑).
출력: data/rsa_media/_worklist.json (서브에이전트 입력 + 적재 매핑).
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from agents.analog.pdf_parser import _clean_brand, _normalize

BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BASE_DIR / "data" / "db" / "drug_prices.db"
OUT_DIR = BASE_DIR / "data" / "rsa_media"


def _clean(brand: str) -> str:
    b = _normalize(brand or "")
    b = re.sub(r"^평가결과[_\s]+", "", b)
    b = re.sub(r"_?\d{4}년.*$", "", b)        # '_2024년 제2,4차' 꼬리 제거
    b = _clean_brand(b)
    b = re.sub(r"[,(].*$", "", b)             # 잔여 함량/회사 꼬리
    return b.strip()


def _window(listing: str, months: int = 2) -> tuple[str, str]:
    d = datetime.strptime(listing, "%Y-%m-%d")
    return ((d - timedelta(days=months * 30)).strftime("%Y-%m-%d"),
            (d + timedelta(days=months * 30)).strftime("%Y-%m-%d"))


def build() -> dict:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, brand_name, generic_name_en, first_reimbursement_date, "
        "has_rsa, has_postmarket_condition, rsa_type_hint, postmarket_condition_detail "
        "FROM analog_reports WHERE (has_rsa=1 OR has_postmarket_condition=1) "
        "AND first_reimbursement_date IS NOT NULL"
    ).fetchall()
    groups: dict[str, dict] = {}
    for r in rows:
        brand = _clean(r["brand_name"])
        inn = (r["generic_name_en"] or "").strip()
        key = (inn.lower() or brand)            # 성분 우선 그룹핑(동일성분 묶음)
        g = groups.setdefault(key, {
            "key": key, "brand": brand, "ingredient": inn,
            "listing_date": r["first_reimbursement_date"], "report_ids": [],
            "has_rsa": 0, "has_postmarket": 0,
        })
        g["report_ids"].append(r["id"])
        g["has_rsa"] |= int(r["has_rsa"] or 0)
        g["has_postmarket"] |= int(r["has_postmarket_condition"] or 0)
        # 가장 이른 등재일을 앵커(최초 약가)
        if r["first_reimbursement_date"] < g["listing_date"]:
            g["listing_date"] = r["first_reimbursement_date"]
        if not g["brand"] and brand:
            g["brand"] = brand
    work = []
    for g in groups.values():
        wf, wt = _window(g["listing_date"])
        g["window_from"], g["window_to"] = wf, wt
        work.append(g)
    work.sort(key=lambda x: x["listing_date"], reverse=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "_worklist.json").write_text(
        json.dumps({"generated_at": "static", "count": len(work), "items": work},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    return {"unique_drugs": len(work), "total_reports": sum(len(g["report_ids"]) for g in work)}


if __name__ == "__main__":
    print(build())
