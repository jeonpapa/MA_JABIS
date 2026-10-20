"""신규 약평위 평가결과 → 등재 아날로그 자동 반영 파이프라인.

  발견(HIRA 게시판 번호 순차 탐색) → 평가결과 PDF 다운로드 → 파싱(parse_drec_pdf) → 헤더 보강(성분·함량·제조사·효능문구)
  → analog_reports UPSERT(+임베딩) → 게시물 링크·yakpyungwi_meta → 신규 행만 식약처 최초 허가일·최초 등재일·질환 분류·
  효과지표·태그 보강.

- 기본은 **dry-run**(DB 무변경): 새 게시물과 파싱 결과 미리보기만 만든다. apply=True 일 때만 쓴다.
- 신규 행에만 작동한다(기존 행·수동 정정은 건드리지 않는다). 허가일은 permit_first(최초 허가일), 등재일은 brand 매칭만 쓴다.
- 접속 실패는 '게시물 없음' 이 아니다 — 실패 번호는 결과에 남기고 cursor 를 그 직전에 둔다.
- 수집된 PDF 는 data/drec_incoming/ 에 보관(재파싱·증빙용).

CLI: python -m agents.analog.drec_sync [--apply] [--limit N]
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Callable

from agents.analog import drec_collector as col
from agents.analog.header_extract import (clean_company, extract_effect_text, extract_header_fields, extract_session_dates,
                                          korean_ingredient_from_name)
from agents.analog.store import BASE_DIR, DB_PATH, ensure_schema, ingest_corpus

logger = logging.getLogger(__name__)
INCOMING_DIR = BASE_DIR / "data" / "drec_incoming"


def known_cursor(db_path: Path = DB_PATH) -> int:
    """이미 아는 마지막 게시물 번호 = max(analog_reports.post_blt_no, yakpyungwi_meta.blt_no)."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        vals = []
        for sql in ("SELECT MAX(CAST(post_blt_no AS INTEGER)) FROM analog_reports",
                    "SELECT MAX(CAST(blt_no AS INTEGER)) FROM yakpyungwi_meta"):
            try:
                v = conn.execute(sql).fetchone()[0]
                if v:
                    vals.append(int(v))
            except sqlite3.Error:
                pass
        return max(vals, default=0)
    finally:
        conn.close()


def _page1(path: Path) -> str:
    from agents.analog.pdf_parser import _extract_pages
    pages = _extract_pages(path)
    return pages[0] if pages else ""


def _head_pages(path: Path, n: int = 2) -> str:
    """심의일은 상품이 여러 개인 보고서에서 1페이지를 넘겨 2페이지에 나오기도 한다(보카브리아·레캄비스)."""
    from agents.analog.pdf_parser import _extract_pages
    return "\n".join((_extract_pages(path) or [])[:n])


def build_report(post: col.Post, pdf_path: Path) -> dict:
    """PDF → ingest 용 report dict. 파서 결과에 헤더 보강을 얹는다(빈 칸만)."""
    from agents.analog.pdf_parser import parse_drec_pdf
    rep = parse_drec_pdf(pdf_path)
    page1 = _page1(pdf_path)
    hdr = extract_header_fields(page1)
    if not rep.get("dosage") and hdr["strength"]:
        rep["dosage"] = hdr["strength"]
    if not rep.get("generic_name_en") and hdr["inn_en"]:
        rep["generic_name_en"] = hdr["inn_en"]
    if not rep.get("manufacturer") and hdr["manufacturer"]:
        rep["manufacturer"] = hdr["manufacturer"]
    rep["manufacturer"] = clean_company(rep.get("manufacturer"))
    if not rep.get("generic_name"):
        rep["generic_name"] = korean_ingredient_from_name(rep.get("brand_name_raw"), pdf_path.name)
    sd = rep.get("session_date") or ""
    dates = hdr["session_dates"] or extract_session_dates(_head_pages(pdf_path))
    if (not sd or sd.endswith("-01-01")) and dates:
        rep["session_date"] = dates[-1]["date"]       # 헤더의 정확한 심의일(여러 개면 가장 마지막 회차)
    rep["_post"] = {"blt": post.blt, "url": post.url, "title": post.title, "post_date": post.post_date,
                    "report_effect_text": extract_effect_text(page1)}
    return rep


def run(*, apply: bool = False, get: Callable[[str], bytes] = col.http_get, limit: int | None = None,
        embed: bool = True, db_path: Path = DB_PATH, incoming: Path = INCOMING_DIR, blts: list[int] | None = None) -> dict:
    """blts 를 주면 발견 모드 대신 그 게시물 번호만 처리한다(과거 누락분 보충). 기존 행과 같은 제품·약평위는 흡수된다."""
    cursor = known_cursor(db_path)
    if blts:
        posts, failed = [], []
        for b in blts:
            try:
                p = col.fetch_post(b, get=get)
            except Exception:
                failed.append(b)
                continue
            if p:
                posts.append(p)
        new_cursor = cursor            # 보충 모드는 cursor 를 움직이지 않는다
    else:
        posts, new_cursor, failed = col.discover(cursor, get=get)
    if limit:
        posts = posts[:limit]
    out: dict = {"mode": "apply" if apply else "dry-run", "cursor_before": cursor, "cursor_after": new_cursor,
                 "posts_found": len(posts), "failed_fetch": failed, "no_result_pdf": [], "errors": [], "preview": []}
    incoming.mkdir(parents=True, exist_ok=True)
    reports: list[dict] = []
    for post in posts:
        att = post.result_pdf
        if not att:
            out["no_result_pdf"].append(post.blt)
            continue
        try:
            data = col.download(att, get=get)
            path = incoming / col.safe_filename(att.name)
            path.write_bytes(data)
            rep = build_report(post, path)
        except Exception as e:  # 한 건 실패가 전체를 막지 않게 — 실패는 결과에 남겨 재시도 대상으로
            out["errors"].append({"blt": post.blt, "name": att.name[:60], "error": str(e)[:120]})
            continue
        reports.append(rep)
        out["preview"].append({k: rep.get(k) for k in ("brand_name", "dosage", "generic_name_en", "generic_name", "manufacturer",
                                                       "session_date", "review_result", "pdf_extractable")}
                              | {"blt": post.blt, "post_date": post.post_date, "title": post.title,
                                 "effect": (rep["_post"]["report_effect_text"] or "")[:80]})
    if not apply or not reports:
        return out
    ensure_schema()
    from agents.analog import dedupe
    from agents.analog.store import _INSERT_COLS
    fresh, absorbed, batch_dups, seen = [], [], [], set()
    conn = sqlite3.connect(str(db_path))
    try:
        for r in reports:
            exists = conn.execute("SELECT id FROM analog_reports WHERE file_name=?", (r["file_name"],)).fetchone()
            existing = None if exists else dedupe.find_existing(conn, r)
            if existing:                  # 같은 제품·같은 약평위 — 새 행을 만들지 않고 흡수(씨트렐린 같은 중복 방지)
                with conn:
                    filled = dedupe.absorb_report(conn, existing, r, _INSERT_COLS)
                absorbed.append({"file": r["file_name"][:60], "into": existing, "filled": filled})
            else:
                key = (dedupe.product_key(r.get("brand_name")), r.get("session_date"))
                if key[0] and key[1] and key in seen:
                    batch_dups.append(r)          # 같은 배치의 같은 제품·약평위 — 첫 건이 들어간 뒤 흡수
                elif r["file_name"] in {x["file_name"] for x in fresh}:
                    continue
                else:
                    seen.add(key)
                    fresh.append(r)
    finally:
        conn.close()
    out["absorbed_duplicates"] = absorbed
    if fresh:
        payload = {"schema_version": 2, "source": "HIRA 게시판 자동 수집",
                   "reports": [{k: v for k, v in r.items() if k != "_post"} for r in fresh]}
        out["ingest"] = ingest_corpus(payload, embed=embed)
        out["enrich"] = _post_ingest(fresh, db_path)
    if batch_dups:
        conn = sqlite3.connect(str(db_path))
        try:
            for r in batch_dups:
                cid = dedupe.find_existing(conn, r)
                if cid:
                    with conn:
                        absorbed.append({"file": r["file_name"][:60], "into": cid, "filled": dedupe.absorb_report(conn, cid, r, _INSERT_COLS)})
        finally:
            conn.close()
    return out


def _post_ingest(reports: list[dict], db_path: Path) -> dict:
    """신규 행 보강: 게시물 링크·메타, 효능문구, 최초 허가일, 최초 등재일(brand 매칭), 질환·효과·태그."""
    from agents.analog import enrich
    from agents.analog.permit_first import lookup_first_permit
    res = {"rows": 0, "permit": 0, "reimb": 0}
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        for rep in reports:
            row = conn.execute("SELECT id, brand_name, mfds_permit_date, first_reimbursement_date FROM analog_reports WHERE file_name=?",
                               (rep["file_name"],)).fetchone()
            if not row:
                continue
            post = rep["_post"]
            res["rows"] += 1
            conn.execute("UPDATE analog_reports SET post_url=?, post_blt_no=?, report_effect_text=COALESCE(report_effect_text, ?) WHERE id=?",
                         (post["url"], str(post["blt"]), post["report_effect_text"], row["id"]))
            exists = conn.execute("SELECT 1 FROM yakpyungwi_meta WHERE CAST(blt_no AS INTEGER)=?", (post["blt"],)).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO yakpyungwi_meta (blt_no, product_name, company, result_raw, result_norm, post_url, norm_brand, ingredient_kr, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (str(post["blt"]), rep.get("brand_name_raw"), rep.get("manufacturer"), None, rep.get("review_result"),
                     post["url"], rep.get("brand_name"), rep.get("generic_name"), datetime.now().isoformat(timespec="seconds")))
            permit_now = row["mfds_permit_date"]
            if not permit_now:
                try:   # 식약처 최초 허가일 — API 오류는 빈 칸으로 두고 다음 실행/수동 보강에서 재시도
                    first = lookup_first_permit(row["brand_name"])
                    if first and first.get("permit_date"):
                        permit_now = first["permit_date"]
                        conn.execute("UPDATE analog_reports SET mfds_permit_date=? WHERE id=?", (permit_now, row["id"]))
                        res["permit"] += 1
                except Exception as e:
                    logger.warning("[drec_sync] 허가일 조회 실패 %s: %s", row["brand_name"], e)
            nd = _first_listing_by_brand(conn, row["brand_name"], permit_now)
            if nd and not row["first_reimbursement_date"]:
                conn.execute("UPDATE analog_reports SET first_reimbursement_date=?, reimbursement_match_key='brand' WHERE id=?", (nd, row["id"]))
                res["reimb"] += 1
        conn.commit()
    finally:
        conn.close()
    # 질환·효과지표·태그: 모두 '미보강 행만' 처리하는 증분 함수(LLM 결과는 file_hash 캐시)
    res["disease"] = enrich.enrich_disease()
    res["efficacy"] = enrich.enrich_efficacy()
    res["tags"] = enrich.enrich_tags()
    return res


def _first_listing_by_brand(conn: sqlite3.Connection, brand: str | None, permit: str | None) -> str | None:
    """국내약가에서 브랜드(prefix) 최초 등재일. 허가일보다 앞서면 매칭 오류로 보고 버린다. 성분 매칭은 신뢰도가 낮아 쓰지 않는다."""
    from agents.analog.enrich import _clean_brand_for_price, _norm_date
    b = _clean_brand_for_price(brand)
    if not b:
        return None
    x = conn.execute("SELECT MIN(apply_date) FROM drug_prices WHERE product_name_kr >= ? AND product_name_kr < ? AND apply_date IS NOT NULL",
                     (b, b + "\uffff")).fetchone()
    nd = _norm_date(x[0]) if x and x[0] else None
    pm = _norm_date(permit)
    if nd and pm and nd < pm:
        return None
    return nd


if __name__ == "__main__":
    import argparse
    import json
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--blts", default=None, help="게시물 번호 JSON 배열 파일(과거 누락분 보충)")
    a = ap.parse_args()
    blts = json.loads(Path(a.blts).read_text()) if a.blts else None
    print(json.dumps(run(apply=a.apply, limit=a.limit, blts=blts), ensure_ascii=False, indent=1, default=str))
