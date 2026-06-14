"""약제 등재 아날로그 검색 — 코퍼스 파서 v2 (DREC Raw PDF 소스).

DREC Raw/ 의 평가결과 PDF 651개를 파싱해 analog_corpus.json 생성.
v1(HIRA_보도자료/*.md) 대비 실제 HIRA 평가 데이터 사용.

실행: python -m agents.analog.corpus [limit]
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from agents.analog.pdf_parser import find_evaluation_pdfs, parse_drec_pdf

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[2]
OUT_PATH = BASE_DIR / "agents" / "ingest" / "analog_corpus.json"


def build_analog_corpus(out: Path = OUT_PATH, limit: int = None) -> dict:
    """DREC Raw/ PDF → analog_corpus.json.

    Returns {path, total, reports, scan_pdf, error, sha256}.
    """
    pdfs = find_evaluation_pdfs()
    if limit:
        pdfs = pdfs[:limit]

    logger.info("[analog.corpus] 평가결과 PDF %d개 처리 시작", len(pdfs))

    reports = []
    scan_count = error_count = 0

    for i, pdf_path in enumerate(pdfs):
        if i % 50 == 0 and i > 0:
            logger.info("[analog.corpus] 진행 %d/%d (스캔=%d, 오류=%d)",
                        i, len(pdfs), scan_count, error_count)
        try:
            rec = parse_drec_pdf(pdf_path)
            if not rec.get("pdf_extractable", True):
                scan_count += 1
            reports.append(rec)
        except Exception as e:
            logger.warning("[analog.corpus] 파싱 실패 %s: %s", pdf_path.name, e)
            error_count += 1

    logger.info("[analog.corpus] 완료: %d건 (스캔=%d, 오류=%d)",
                len(reports), scan_count, error_count)

    payload = {
        "schema_version": 2,
        "source": "DREC Raw PDF",
        "reports": reports,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    sha = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()

    return {
        "path": str(out),
        "total": len(pdfs),
        "reports": len(reports),
        "scan_pdf": scan_count,
        "error": error_count,
        "sha256": sha,
    }


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    lim = int(sys.argv[1]) if len(sys.argv) > 1 else None
    print(json.dumps(build_analog_corpus(limit=lim), ensure_ascii=False, indent=2))
