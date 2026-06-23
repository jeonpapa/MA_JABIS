"""약평위 전체목록 메타(xlsx) → yakpyungwi_meta 테이블 적재.

엑셀: DREC Raw/약제급여평가위원회_전체목록_메타정보.xlsx (871행)
컬럼(3행째 헤더): 제품명 · 회사명 · 결과 · 게시글링크 · bltNo

결과 메타는 HIRA 공식 게시물 기반 1차 권위 — 아날로그 결과 검증·보완 + 게시물 링크.
norm_brand(정규화 제품명)·ingredient_kr(괄호 성분)으로 analog_reports 매칭 보조.

CLI: python -m agents.ingest.yakpyungwi_meta_import
"""
from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path

import openpyxl

from agents.analog.pdf_parser import _clean_brand, _normalize

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BASE_DIR / "data" / "db" / "drug_prices.db"
XLSX = BASE_DIR / "DREC Raw" / "약제급여평가위원회_전체목록_메타정보.xlsx"

RESULT_NORM = {
    "급여": "APPROVED",
    "조건부급여": "CONDITIONAL_APPROVED",
    "비급여": "REJECTED",
    "조건부비급여": "REJECTED",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS yakpyungwi_meta (
    blt_no       INTEGER PRIMARY KEY,
    product_name TEXT NOT NULL,
    company      TEXT,
    result_raw   TEXT,
    result_norm  TEXT,
    post_url     TEXT,
    norm_brand   TEXT,
    ingredient_kr TEXT,
    created_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_ypw_norm_brand ON yakpyungwi_meta(norm_brand);
CREATE INDEX IF NOT EXISTS idx_ypw_ingredient ON yakpyungwi_meta(ingredient_kr);
"""

_RE_PAREN = re.compile(r"[(\[]([^()\[\]]*)[)\]]")  # () 또는 [] 성분 표기
_RE_STRENGTH = re.compile(
    r"\d+(?:[.,]\d+)?\s*(?:밀리그램|밀리그람|마이크로그램|그램|밀리리터|국제단위|만단위|단위|"
    r"mg|mcg|μg|㎍|ng|ml|mL|L|g|iu|i\.?u\.?|%)(?:\s*/\s*\d*(?:[.,]\d+)?\s*[a-zA-Z가-힣%]+)?",
    re.IGNORECASE,
)
_RE_DOSE_LIKE = re.compile(r"\d")  # 괄호 안에 숫자 있으면 함량/규격으로 간주


def norm_brand(product_name: str) -> str:
    """제품명 → 정규화 브랜드(괄호·함량·임베디드 강도·공백 제거)."""
    b = _clean_brand(_normalize(product_name or ""))
    b = _RE_STRENGTH.sub("", b)          # 임베디드 강도(4mg/0.8mL 등) 제거
    b = re.sub(r"[,/]", "", b)
    b = re.sub(r"\s+", "", b)
    return b.strip()


def ingredient_from_name(product_name: str) -> str | None:
    """제품명 괄호 안 성분명 추출(함량·회사 괄호 제외)."""
    name = _normalize(product_name or "")
    for c in reversed(_RE_PAREN.findall(name)):
        c = c.strip()
        if not c or _RE_DOSE_LIKE.search(c):  # 숫자 포함 → 함량/규격 괄호 skip
            continue
        if any(k in c for k in ("주식회사", "(주)", "㈜", "코리아", "제약", "약품", "유한", "등")):
            continue
        c = re.split(r"[,/]", c)[0].strip()       # '엡코리타맙,유전자재조합' → '엡코리타맙'
        c = c.replace("유전자재조합", "").strip()
        if c:
            return c
    return None


def run(xlsx: Path = XLSX) -> dict:
    wb = openpyxl.load_workbook(str(xlsx), read_only=True, data_only=True)
    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    # 1행 제목, 2행 헤더(제품명·회사명·결과·게시글링크·bltNo), 3행~ 데이터
    data = [r for r in rows[2:] if r and r[0]]
    now = datetime.now().isoformat(timespec="seconds")
    recs = []
    for r in data:
        product, company, result_raw, url, blt = r[0], r[1], r[2], r[3], r[4]
        try:
            blt_no = int(blt)
        except (TypeError, ValueError):
            continue
        recs.append((blt_no, _normalize(str(product)), company, result_raw,
                     RESULT_NORM.get((result_raw or "").strip(), "UNKNOWN"),
                     url, norm_brand(str(product)), ingredient_from_name(str(product)), now))
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.executescript(_SCHEMA)
        conn.execute("DELETE FROM yakpyungwi_meta")
        conn.executemany(
            "INSERT OR REPLACE INTO yakpyungwi_meta "
            "(blt_no, product_name, company, result_raw, result_norm, post_url, norm_brand, ingredient_kr, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)", recs,
        )
    logger.info("[yakpyungwi_meta] 적재 %d건", len(recs))
    return {"rows": len(recs)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print(run())
