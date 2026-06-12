"""약제 등재 아날로그 검색 — DB store (스키마·ingest·임베딩·검색).

analog_reports 테이블 + FTS5 + 임베딩 BLOB. 검색은 패싯+FTS 코어 / 임베딩 시맨틱 부가층.
임베딩: OpenAI text-embedding-3-small (1536d, float32 BLOB). 537 brute-force cosine(numpy) — 벡터DB 불필요.

함수:
  ensure_schema()
  ingest_corpus(payload) — file_hash dedup UPSERT + 신규/변경만 임베딩
  search(filters, fts, semantic, limit) — 패싯/FTS/시맨틱 하이브리드
  facet_values() — 드롭다운 옵션
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import struct
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BASE_DIR / "data" / "db" / "drug_prices.db"

EMBED_MODEL = "text-embedding-3-small"
EMBED_DIM = 1536

_FACET_COLS = [
    "disease_category", "cancer_type", "line_of_therapy", "committee",
    "review_result", "reimbursement_track", "coverage_gap_type",
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS analog_reports (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    file_name         TEXT UNIQUE NOT NULL,
    file_hash         TEXT NOT NULL,
    title             TEXT,
    brand_name        TEXT,
    generic_name      TEXT,
    manufacturer      TEXT,
    disease_category  TEXT,
    disease_name      TEXT,
    cancer_type       TEXT,
    line_of_therapy   TEXT,
    committee         TEXT,
    session_date      TEXT,
    ordinal           INTEGER,
    review_result     TEXT,
    reimbursement_track TEXT,
    rsa_types         TEXT,   -- JSON
    policy_drivers    TEXT,   -- JSON
    mfds_approval_date TEXT,
    application_date  TEXT,
    amjilsim_date     TEXT,
    lag_days_approval_to_reimb INTEGER,
    wikilinks         TEXT,   -- JSON
    body_text         TEXT,
    embedding         BLOB,   -- float32 x1536
    -- enrich (agents/analog/enrich.py)
    mfds_permit_date  TEXT,
    mfds_effect_text  TEXT,
    coverage_gap_type TEXT,   -- 축소|확대|구체화|동일|비교불가
    coverage_gap_evidence TEXT,
    requeue_count     INTEGER,
    first_session_date TEXT,
    pass_session_date TEXT,
    sessions_to_pass  INTEGER,
    enriched_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_analog_generic ON analog_reports(generic_name);
CREATE INDEX IF NOT EXISTS idx_analog_disease ON analog_reports(disease_category, cancer_type);
CREATE VIRTUAL TABLE IF NOT EXISTS analog_fts USING fts5(
    brand_name, generic_name, disease_name, body_text, mfds_effect_text,
    content='analog_reports', content_rowid='id'
);
CREATE TABLE IF NOT EXISTS analog_brief_cache (
    cache_key TEXT PRIMARY KEY,
    brief     TEXT,
    created_at TEXT
);
"""

_INSERT_COLS = [
    "file_name", "file_hash", "title", "brand_name", "generic_name", "manufacturer",
    "disease_category", "disease_name", "cancer_type", "line_of_therapy", "committee",
    "session_date", "ordinal", "review_result", "reimbursement_track", "rsa_types",
    "policy_drivers", "mfds_approval_date", "application_date", "amjilsim_date",
    "lag_days_approval_to_reimb", "wikilinks", "body_text",
]


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema() -> None:
    with _connect() as conn:
        conn.executescript(_SCHEMA)
        conn.commit()


def _pack(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _unpack(blob: bytes):
    import numpy as np
    return np.frombuffer(blob, dtype="<f4")


# ── 임베딩 ────────────────────────────────────────────────────────────────────

def _embed_texts(texts: list[str]) -> Optional[list[list[float]]]:
    """OpenAI 임베딩 배치. 키 없거나 실패 시 None (시맨틱만 graceful 제외)."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        _load_env_key()
        key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key)
        out: list[list[float]] = []
        for i in range(0, len(texts), 256):  # 배치 256
            batch = [t[:8000] for t in texts[i:i + 256]]
            resp = client.embeddings.create(model=EMBED_MODEL, input=batch)
            out.extend(d.embedding for d in resp.data)
        return out
    except Exception as e:
        logger.warning("[analog.store] 임베딩 실패: %s", e)
        return None


def _load_env_key() -> None:
    env = BASE_DIR / "config" / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("OPENAI_API_KEY="):
            os.environ.setdefault("OPENAI_API_KEY", line.split("=", 1)[1].strip())


def _embed_doc_text(r: dict) -> str:
    """임베딩 입력 — 검색 의미가 큰 필드 결합."""
    parts = [r.get("brand_name") or "", r.get("generic_name") or "",
             r.get("disease_name") or "", r.get("cancer_type") or "",
             r.get("line_of_therapy") or "", r.get("review_result") or "",
             r.get("reimbursement_track") or "", (r.get("body_text") or "")[:2000]]
    return " ".join(p for p in parts if p)


# ── ingest ────────────────────────────────────────────────────────────────────

def ingest_corpus(payload: dict, embed: bool = True) -> dict:
    """corpus payload → analog_reports UPSERT (file_name 기준). 신규/변경만 임베딩."""
    ensure_schema()
    reports = payload.get("reports", [])
    inserted = updated = skipped = 0
    to_embed: list[tuple[int, dict]] = []

    with _connect() as conn:
        existing = {r["file_name"]: (r["id"], r["file_hash"],
                                     r["embedding"] is not None)
                    for r in conn.execute(
                        "SELECT id, file_name, file_hash, embedding FROM analog_reports")}
        for r in reports:
            fn, fh = r["file_name"], r["file_hash"]
            vals = {
                **{k: r.get(k) for k in _INSERT_COLS},
                "rsa_types": json.dumps(r.get("rsa_types") or [], ensure_ascii=False),
                "policy_drivers": json.dumps(r.get("policy_drivers") or [], ensure_ascii=False),
                "wikilinks": json.dumps(r.get("wikilinks") or [], ensure_ascii=False),
            }
            if fn in existing:
                rid, old_hash, has_emb = existing[fn]
                if old_hash == fh and has_emb:
                    skipped += 1
                    continue
                cols = [c for c in _INSERT_COLS if c != "file_name"]
                conn.execute(
                    f"UPDATE analog_reports SET {', '.join(f'{c}=?' for c in cols)} WHERE id=?",
                    [vals[c] for c in cols] + [rid])
                conn.execute("UPDATE analog_fts SET brand_name=?, generic_name=?, "
                             "disease_name=?, body_text=? WHERE rowid=?",
                             (vals["brand_name"], vals["generic_name"], vals["disease_name"],
                              vals["body_text"], rid))
                if old_hash != fh or not has_emb:
                    to_embed.append((rid, r))
                updated += 1
            else:
                cur = conn.execute(
                    f"INSERT INTO analog_reports ({', '.join(_INSERT_COLS)}) "
                    f"VALUES ({', '.join('?' for _ in _INSERT_COLS)})",
                    [vals[c] for c in _INSERT_COLS])
                rid = cur.lastrowid
                conn.execute("INSERT INTO analog_fts (rowid, brand_name, generic_name, "
                             "disease_name, body_text) VALUES (?,?,?,?,?)",
                             (rid, vals["brand_name"], vals["generic_name"],
                              vals["disease_name"], vals["body_text"]))
                to_embed.append((rid, r))
                inserted += 1
        conn.commit()

    embedded = 0
    if embed and to_embed:
        vecs = _embed_texts([_embed_doc_text(r) for _, r in to_embed])
        if vecs:
            with _connect() as conn:
                for (rid, _), v in zip(to_embed, vecs):
                    conn.execute("UPDATE analog_reports SET embedding=? WHERE id=?",
                                 (_pack(v), rid))
                conn.commit()
            embedded = len(vecs)

    return {"reports": len(reports), "inserted": inserted, "updated": updated,
            "skipped": skipped, "embedded": embedded}


# ── 검색 ──────────────────────────────────────────────────────────────────────

def facet_values() -> dict:
    """드롭다운 옵션 — 각 패싯 distinct 값(빈도순)."""
    ensure_schema()
    out: dict[str, list] = {}
    with _connect() as conn:
        for col in _FACET_COLS:
            rows = conn.execute(
                f"SELECT {col} v, COUNT(*) n FROM analog_reports "
                f"WHERE {col} IS NOT NULL AND {col} != '' GROUP BY {col} ORDER BY n DESC").fetchall()
            out[col] = [{"value": r["v"], "count": r["n"]} for r in rows]
    return out


def _row_to_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    for k in ("rsa_types", "policy_drivers", "wikilinks"):
        try:
            d[k] = json.loads(d.get(k) or "[]")
        except (ValueError, TypeError):
            d[k] = []
    d.pop("embedding", None)
    d.pop("body_text", None)  # 목록선 제외 (detail 에서)
    return d


def search(filters: dict = None, fts: str = None, semantic: str = None,
           limit: int = 50) -> dict:
    """패싯 필터 + 선택 FTS + 선택 시맨틱 랭킹. 항상 패싯이 신뢰 코어."""
    ensure_schema()
    filters = filters or {}
    where, params = [], []
    for col in _FACET_COLS + ["generic_name", "brand_name"]:
        val = filters.get(col)
        if val:
            where.append(f"a.{col} = ?")
            params.append(val)

    base = "SELECT a.* FROM analog_reports a"
    if fts:
        base += " JOIN analog_fts f ON f.rowid = a.id"
        where.append("analog_fts MATCH ?")
        params.append(_fts_query(fts))
    if where:
        base += " WHERE " + " AND ".join(where)
    # 시맨틱이면 후보를 넉넉히 뽑아 재랭킹, 아니면 최신순
    cand_limit = max(limit, 200) if semantic else limit
    base += " ORDER BY a.session_date DESC LIMIT ?"
    params.append(cand_limit)

    with _connect() as conn:
        rows = conn.execute(base, params).fetchall()

    results = [_row_to_dict(r) for r in rows]
    mode = "facet"

    if semantic and rows:
        ranked = _semantic_rerank(semantic, rows, limit)
        if ranked is not None:
            results, mode = ranked, "semantic"
    else:
        results = results[:limit]

    return {"mode": mode, "count": len(results), "results": results}


def _semantic_rerank(query: str, rows: list[sqlite3.Row], limit: int):
    """쿼리 임베딩 ↔ 후보 임베딩 코사인 재랭킹. 임베딩 불가 시 None(폴백)."""
    qv = _embed_texts([query])
    if not qv:
        return None
    import numpy as np
    q = np.asarray(qv[0], dtype="<f4")
    q = q / (np.linalg.norm(q) + 1e-9)
    scored = []
    for r in rows:
        blob = r["embedding"]
        if not blob:
            continue
        v = _unpack(blob)
        sim = float(np.dot(q, v) / (np.linalg.norm(v) + 1e-9))
        d = _row_to_dict(r)
        d["similarity"] = round(sim, 4)
        scored.append((sim, d))
    if not scored:
        return None
    scored.sort(key=lambda x: -x[0])
    return [d for _, d in scored[:limit]]


def _fts_query(text: str) -> str:
    """안전한 FTS5 쿼리 — 토큰 접두 매칭."""
    toks = [t for t in __import__("re").findall(r"[\w가-힣]+", text) if len(t) >= 2]
    return " OR ".join(f'"{t}"*' for t in toks) if toks else '""'


def get_detail(report_id: int) -> Optional[dict]:
    ensure_schema()
    with _connect() as conn:
        r = conn.execute("SELECT * FROM analog_reports WHERE id=?", (report_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    for k in ("rsa_types", "policy_drivers", "wikilinks"):
        try:
            d[k] = json.loads(d.get(k) or "[]")
        except (ValueError, TypeError):
            d[k] = []
    d.pop("embedding", None)
    return d


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if len(sys.argv) > 1 and sys.argv[1] == "ingest":
        payload = json.loads((BASE_DIR / "agents" / "ingest" / "analog_corpus.json")
                             .read_text(encoding="utf-8"))
        print(json.dumps(ingest_corpus(payload, embed="--no-embed" not in sys.argv),
                         ensure_ascii=False, indent=2))
    else:
        print(json.dumps(facet_values(), ensure_ascii=False, indent=2)[:800])
