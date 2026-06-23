"""DREC 추가 폴더(신규 평가결과 PDF)만 타깃 적재 → analog_reports UPSERT.

기존 코퍼스(루트 DREC Raw/)는 건드리지 않고, `DREC Raw/DREC 추가/` 의 신규 PDF 만
parse_drec_pdf 로 파싱해 ingest_corpus(file_name UPSERT)로 추가한다.

신규 파일명은 차수/급여구분이 없어 PDF 본문(session_date·결과)으로 보강되며,
약평위 결과·게시물 링크의 최종 검증·보완은 yakpyungwi_match 단계에서 수행한다.

CLI: python -m agents.ingest.drec_addon_import [--embed]
"""
from __future__ import annotations

import logging
from pathlib import Path

from agents.analog.pdf_parser import parse_drec_pdf, _normalize
from agents.analog.store import ingest_corpus

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[2]
ADDON_DIR = BASE_DIR / "DREC Raw" / "DREC 추가"


def find_addon_pdfs(addon_dir: Path = ADDON_DIR) -> list[Path]:
    """추가 폴더의 평가결과 PDF 전체(회의자료 제외). 마커 필터 없이 포함."""
    if not addon_dir.exists():
        logger.error("추가 폴더 없음: %s", addon_dir)
        return []
    out = []
    for f in addon_dir.iterdir():
        norm = _normalize(f.name)
        if norm.lower().endswith(".pdf") and "회의자료" not in norm:
            out.append(f)
    return sorted(out, key=lambda p: _normalize(p.name))


def run(embed: bool = False) -> dict:
    pdfs = find_addon_pdfs()
    logger.info("[drec_addon] %d개 PDF 파싱", len(pdfs))
    reports, errors = [], 0
    for p in pdfs:
        try:
            reports.append(parse_drec_pdf(p))
        except Exception as e:
            logger.warning("[drec_addon] 파싱 실패 %s: %s", p.name, e)
            errors += 1
    res = ingest_corpus({"schema_version": 2, "source": "DREC 추가", "reports": reports}, embed=embed)
    res["parsed"] = len(reports)
    res["errors"] = errors
    return res


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run(embed="--embed" in sys.argv)
