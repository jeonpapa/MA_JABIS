"""아날로그 임베딩 백필 — `embedding` 컬럼만 채운다 (재적재 금지).

ingest_corpus 재실행은 _INSERT_COLS 에 LLM enrich 컬럼(disease_category 등)이
포함돼 코퍼스에 없는 값을 None 으로 덮어쓰므로 금지. 이 스크립트는 embedding
IS NULL 인 v2(.pdf) 행만 대상으로 text-embedding-3-small 배치 임베딩 후
embedding 컬럼만 UPDATE 한다.

실행: .venv/bin/python -m scripts.backfill_analog_embeddings
"""
from __future__ import annotations

import logging
import sqlite3

from agents.analog import store

logger = logging.getLogger(__name__)


def main() -> dict:
    store.ensure_schema()
    conn = store._connect()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM analog_reports "
        "WHERE file_name LIKE '%.pdf' AND embedding IS NULL"
    ).fetchall()
    conn.close()

    recs = [dict(r) for r in rows]
    pending = [(r["id"], store._embed_doc_text(r)) for r in recs]
    pending = [(rid, t) for rid, t in pending if t and t.strip()]

    logger.info("[embed-backfill] 대상 %d행 (빈 문서텍스트 제외 후 %d)",
                len(recs), len(pending))
    if not pending:
        return {"target": len(recs), "embedded": 0, "note": "nothing to embed"}

    vecs = store._embed_texts([t for _, t in pending])
    if not vecs:
        return {"target": len(pending), "embedded": 0,
                "note": "embed failed (no OPENAI key / API error)"}

    with store._connect() as c:
        for (rid, _), v in zip(pending, vecs):
            c.execute("UPDATE analog_reports SET embedding=? WHERE id=?",
                      (store._pack(v), rid))
        c.commit()

    return {"target": len(pending), "embedded": len(vecs)}


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print(json.dumps(main(), ensure_ascii=False, indent=2))
