"""analog_reports ↔ yakpyungwi_meta 매칭 → 게시물 링크 + 약평위 결과 검증·보완.

매칭: ① 정규화 제품명 exact ② 성분(INN) + 브랜드 토큰/결과 보조 ③ 미매칭 audit.
메타(HIRA 공식 게시물)를 권위로 review_result 교정(원본 review_result_pdf 보존) +
post_url/post_blt_no 세팅. 변경은 jsonl 로그.

CLI: python -m agents.analog.yakpyungwi_match
"""
from __future__ import annotations

import json
import logging
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from agents.analog.store import ensure_schema, DB_PATH
from agents.ingest.yakpyungwi_meta_import import norm_brand, ingredient_from_name

logger = logging.getLogger(__name__)
LOG_PATH = Path(__file__).resolve().parents[2] / "data" / "cache" / "yakpyungwi_match_log.jsonl"

_AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS yakpyungwi_match_audit (
    report_id   INTEGER,
    brand_name  TEXT,
    norm_brand  TEXT,
    ingredient  TEXT,
    reason      TEXT,
    candidates  TEXT,
    created_at  TEXT
);
"""


def _brand_tokens(s: str) -> set:
    return set(t for t in (s or "").replace("정", " ").replace("주", " ").split() if len(t) >= 2)


def run() -> dict:
    ensure_schema()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.executescript(_AUDIT_SCHEMA)
    conn.execute("DELETE FROM yakpyungwi_match_audit")

    meta = list(conn.execute(
        "SELECT blt_no, product_name, norm_brand, ingredient_kr, result_raw, result_norm, post_url FROM yakpyungwi_meta"))
    by_norm = defaultdict(list)
    by_ingr = defaultdict(list)
    for m in meta:
        if m["norm_brand"]:
            by_norm[m["norm_brand"]].append(m)
        if m["ingredient_kr"]:
            by_ingr[m["ingredient_kr"]].append(m)

    reports = list(conn.execute(
        "SELECT id, brand_name, brand_name_raw, generic_name, review_result, session_date FROM analog_reports"))

    def pick(cands, rep):
        """후보 다수 시 결과 일치 → 최신 bltNo 순."""
        if len(cands) == 1:
            return cands[0]
        same = [c for c in cands if c["result_norm"] == rep["review_result"]]
        pool = same or cands
        return max(pool, key=lambda c: c["blt_no"])

    now = datetime.now().isoformat(timespec="seconds")
    matched = corrected = filled = 0
    audits = []
    changes = []
    for rep in reports:
        rb = norm_brand(rep["brand_name_raw"] or rep["brand_name"] or "")
        ri = ingredient_from_name(rep["brand_name_raw"] or rep["brand_name"] or "")
        hit = None
        if rb and rb in by_norm:
            hit = pick(by_norm[rb], rep)
        elif ri and ri in by_ingr:
            cands = by_ingr[ri]
            if len(cands) == 1:
                hit = cands[0]
            else:
                # 동일성분 다수 → 브랜드 토큰 겹침으로 단정, 없으면 audit
                rtok = _brand_tokens(rep["brand_name"])
                overlap = [c for c in cands if _brand_tokens(c["norm_brand"]) & rtok]
                hit = pick(overlap, rep) if overlap else None
        if not hit:
            audits.append((rep["id"], rep["brand_name"], rb, ri, "no_match",
                           json.dumps([dict(c) for c in by_ingr.get(ri, [])][:3], ensure_ascii=False), now))
            continue
        matched += 1
        old = rep["review_result"]
        new = hit["result_norm"]
        result_source = "pdf"
        if new and new != "UNKNOWN":
            if not old or old == "UNKNOWN":
                filled += 1; result_source = "meta"
            elif old != new:
                corrected += 1; result_source = "meta"
                changes.append({"report_id": rep["id"], "brand": rep["brand_name"],
                                "from": old, "to": new, "blt_no": hit["blt_no"]})
        final_result = new if result_source == "meta" else old
        conn.execute(
            "UPDATE analog_reports SET post_url=?, post_blt_no=?, result_meta=?, "
            "review_result_pdf=COALESCE(review_result_pdf, review_result), "
            "review_result=?, result_source=? WHERE id=?",
            (hit["post_url"], hit["blt_no"], hit["result_raw"], final_result, result_source, rep["id"]),
        )
    conn.executemany(
        "INSERT INTO yakpyungwi_match_audit (report_id, brand_name, norm_brand, ingredient, reason, candidates, created_at) "
        "VALUES (?,?,?,?,?,?,?)", audits)
    conn.commit()
    conn.close()

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("w", encoding="utf-8") as f:
        for ch in changes:
            f.write(json.dumps(ch, ensure_ascii=False) + "\n")

    res = {"reports": len(reports), "matched": matched, "unmatched": len(audits),
           "result_corrected": corrected, "result_filled": filled}
    logger.info("[yakpyungwi_match] %s", res)
    return res


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print(run())
