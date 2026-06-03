"""HIRA 약제급여목록 Excel 의 `주성분명` 컬럼을 drug_prices.ingredient 로 재적재.

배경: COL_CANDIDATES["ingredient"] 가 기존에 `주성분명` 을 포함하지 않아
전체 row 에서 ingredient=NULL 이었음 (2026-04-23 발견).
schema.py 수정 후 이 스크립트로 기존 Excel 을 재파싱하여 backfill 한다.

사용법:
    # 특정 파일만 재적재
    python -m scripts.backfill_ingredient --file data/raw/20260201_#224_...xlsx

    # data/raw/ 전체 재적재 (apply_date 는 파일명에서 YYYYMMDD → YYYY.MM.DD 로 추출)
    python -m scripts.backfill_ingredient --all
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import pandas as pd
from agents.db import DrugPriceDB

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

FILE_DATE_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})_")


def parse_apply_date(path: Path) -> str | None:
    m = FILE_DATE_RE.match(path.name)
    if not m:
        return None
    y, mo, d = m.groups()
    return f"{y}.{mo}.{d}"


def reingest(db: DrugPriceDB, path: Path, apply_date: str) -> int:
    # 제품코드/보험코드는 leading zero 를 보존해야 한다 (숫자 변환 시 07310... → 7310...).
    df = pd.read_excel(
        path,
        sheet_name=0,
        dtype={"제품코드": str, "보험코드": str},
    )
    logger.info("재파싱: %s | apply=%s | rows=%d | cols=%d", path.name, apply_date, len(df), len(df.columns))
    return db.upsert_prices(df, apply_date)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", type=Path, help="재적재할 단일 Excel")
    ap.add_argument("--apply-date", type=str, help="YYYY.MM.DD (파일 단독 모드에서 필수)")
    ap.add_argument("--all", action="store_true", help="data/raw/ 의 모든 HIRA Excel 재적재")
    args = ap.parse_args()

    db = DrugPriceDB(BASE_DIR / "data" / "db" / "drug_prices.db")

    targets: list[tuple[Path, str]] = []
    if args.all:
        raw_dir = BASE_DIR / "data" / "raw"
        for p in sorted(raw_dir.glob("*.xlsx")):
            ad = parse_apply_date(p)
            if ad and "급여상한금액" in p.name:
                targets.append((p, ad))
    elif args.file:
        ad = args.apply_date or parse_apply_date(args.file) or ""
        if not ad:
            ap.error("apply_date 를 파일명에서 추출 실패 — --apply-date 로 명시")
        targets.append((args.file, ad))
    else:
        ap.error("--file 또는 --all 지정")

    total = 0
    for path, apply_date in targets:
        try:
            n = reingest(db, path, apply_date)
            logger.info("✓ %s | %d rows", apply_date, n)
            total += n
        except Exception as e:
            logger.error("✗ %s: %s", path.name, e)

    logger.info("완료 — 총 %d rows upsert", total)


if __name__ == "__main__":
    main()
