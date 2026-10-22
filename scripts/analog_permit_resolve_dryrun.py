#!/usr/bin/env python3
"""등재 아날로그 — 식약처 허가일·효능효과 공백 해소 시험(dry-run, DB 무변경).

기존 조회는 '보고서 약제명 → 식약처 품목명 검색' 만 썼다. 이름이 다르면(함량·제형이 붙은 이름, 옛 상표) 0건이 나온다.
여기서는 HIRA 약가 파일(drug_prices)의 **정식 제품명·보험(EDI)코드** 를 다리로 쓴다.
  ① 보고서 약제명으로 시작하는 HIRA 제품(가장 먼저 등재된 것부터)을 찾는다.
  ② 그 보험코드로 식약처 API(edi_code)를 정확 조회한다 → 이름이 달라도 같은 품목을 잡는다.
  ③ 찾은 품목의 제품군(같은 이름 앞부분)에서 최초 허가일을 고른다(permit_first.earliest_permit).
  ④ 효능·효과(EE_DOC_DATA)는 정상 품목 우선으로 읽는다.

분류(브랜드 단위)
  found_edi      — 보험코드로 식약처 품목을 찾음(권장 경로)
  found_name     — 보험코드는 안 맞았지만 HIRA 정식명으로 이름 검색해 찾음
  api_zero       — 모든 조회에서 API 가 답했고 0건. '미허가' 를 뜻하지 않는다(취하·옛 품목·이름 불일치 가능)
  error          — 끝까지 API 오류(시간 초과 등)가 남음. api_zero 와 섞지 않고 재시도 대상으로 둔다
  no_hira        — 약가 파일에 보고서 약제명으로 시작하는 제품이 없음

적용 전 사람이 볼 것: name_differs(식약처 품목명이 보고서 약제명과 다름), permit_after_session(허가일이 약평위일보다 늦음).

사용: python scripts/analog_permit_resolve_dryrun.py --db /app/data/db/drug_prices.db --app-root /app --out /tmp/resolve_dry.json
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def _setup(app_root: str):
    sys.path.insert(0, app_root)
    from agents.analog.permit_first import _base_brand, _norm_name, earliest_permit
    from agents.scrapers.kr_mfds_permit import fetch_raw, to_summary
    return _base_brand, _norm_name, earliest_permit, fetch_raw, to_summary


def load_hira_products(db: str, norm_name) -> list[dict]:
    """HIRA 약가 파일에서 (보험코드, 제품명, 업체) 별 최초·최종 적용일. 한 번만 읽는다."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = conn.execute("SELECT insurance_code, product_name_kr, company, MIN(apply_date), MAX(apply_date) "
                        "FROM drug_prices WHERE product_name_kr IS NOT NULL GROUP BY insurance_code, product_name_kr").fetchall()
    conn.close()
    return [{"code": r[0], "name": r[1], "company": r[2], "first": r[3], "last": r[4], "norm": norm_name(r[1])} for r in rows]


def call(fetch_raw, retries: int = 3, **kw):
    """API 호출. 시간 초과·전송 오류는 지수 백오프로 재시도하고, 끝까지 실패하면 예외(=api_zero 와 구분)."""
    last = None
    for i in range(retries):
        try:
            return fetch_raw(timeout=25, **kw)
        except Exception as e:  # MfdsApiError / transport
            last = e
            time.sleep(2 ** i)
    raise last


def family_base(api_name: str) -> str:
    """식약처 품목명에서 제품군 이름: '키트루다주(펨브롤리주맙…)' → '키트루다주', '길레니아캡슐0.5밀리그램' → '길레니아캡슐'."""
    n = (api_name or "").split("(")[0]
    m = re.search(r"\d", n)
    return (n[:m.start()] if m and m.start() >= 2 else n).strip()


def resolve_brand(brand: str, hira: list[dict], helpers) -> dict:
    _base_brand, _norm_name, earliest_permit, fetch_raw, to_summary = helpers
    base = _base_brand(brand)
    out: dict = {"brand": brand, "base": base, "tried": [], "errors": 0}
    cands = [h for h in hira if base and h["norm"].startswith(base)]
    if not cands:
        out["status"] = "no_hira"
        return out
    cands.sort(key=lambda h: (h["first"] or "9999"))
    seen_codes, picked = set(), []
    for h in cands:                                   # 가장 먼저 등재된 제품부터, 보험코드 중복 제거, 최대 6개
        if h["code"] and h["code"] not in seen_codes:
            seen_codes.add(h["code"]); picked.append(h)
        if len(picked) >= 6:
            break
    out["hira"] = [{"code": h["code"], "name": h["name"], "company": h["company"], "first": h["first"], "last": h["last"]} for h in picked]
    items: list[dict] = []
    via = None
    answered_zero = 0
    for h in picked:                                  # ② 보험코드로 정확 조회
        try:
            got = call(fetch_raw, edi_code=h["code"], num_of_rows=10)
        except Exception as e:
            out["errors"] += 1; out["tried"].append(f"edi:{h['code']}:ERR:{str(e)[:40]}")
            continue
        out["tried"].append(f"edi:{h['code']}:{len(got)}")
        if got:
            items = got; via = "found_edi"; out["edi_code"] = h["code"]
            break
        answered_zero += 1
    if not items:                                     # 보험코드로 못 찾으면 HIRA 정식명으로 이름 검색
        for name in dict.fromkeys(family_base(h["name"]) for h in picked):
            if not name:
                continue
            try:
                got = call(fetch_raw, item_name=name, num_of_rows=50)
            except Exception as e:
                out["errors"] += 1; out["tried"].append(f"name:{name}:ERR:{str(e)[:40]}")
                continue
            out["tried"].append(f"name:{name}:{len(got)}")
            if got:
                items = got; via = "found_name"
                break
            answered_zero += 1
    if not items:
        # 한 번이라도 오류가 있었으면 '0건' 으로 단정하지 않는다
        out["status"] = "error" if out["errors"] else "api_zero"
        return out
    # ③ 제품군 확장 — 찾은 품목의 이름 앞부분으로 다시 조회해 최초 허가일을 고른다
    api_name = items[0].get("ITEM_NAME") or ""
    fam = family_base(api_name)
    try:
        wide = call(fetch_raw, item_name=fam, num_of_rows=50) if fam else items
    except Exception:
        wide = items
        out["errors"] += 1
    first = earliest_permit(wide or items, fam or brand) or earliest_permit(items, api_name)
    out["status"] = via
    out["api_item_name"] = api_name
    out["name_differs"] = _base_brand(api_name) != base and not _norm_name(api_name).startswith(base)
    if first:
        out["permit_date"] = first["permit_date"]; out["n_items"] = first["n_items"]
        out["cancel_status"] = first["cancel_status"]; out["latest_permit_date"] = first["latest_permit_date"]
    # ④ 효능·효과: 정상 품목 우선
    pool = [it for it in (wide or items) if _norm_name(it.get("ITEM_NAME")).startswith(_base_brand(fam or api_name))] or items
    pool.sort(key=lambda it: ((it.get("CANCEL_NAME") or "").strip() != "정상", -int(re.sub(r"\D", "", it.get("ITEM_PERMIT_DATE") or "0") or 0)))
    eff = (to_summary(pool[0]).get("effect_text") or "").strip() if pool else ""
    out["effect_len"] = len(eff); out["effect_head"] = eff[:90]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/db/drug_prices.db")
    ap.add_argument("--app-root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    helpers = _setup(a.app_root)
    out_path = Path(a.out)
    done: dict = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {}
    conn = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id, brand_name, session_date, mfds_permit_date, mfds_effect_text FROM analog_reports "
                        "WHERE brand_name IS NOT NULL AND brand_name!='' AND (mfds_permit_date IS NULL OR mfds_permit_date='' "
                        "OR mfds_effect_text IS NULL OR trim(mfds_effect_text)='') ORDER BY id").fetchall()
    conn.close()
    brands: dict[str, list[dict]] = {}
    for r in rows:
        brands.setdefault(r["brand_name"], []).append(dict(r))
    todo = [b for b in brands if not (b in done and done[b].get("status") != "error")]   # error 는 재시도
    if a.limit:
        todo = todo[:a.limit]
    print(f"대상 브랜드 {len(brands)}개({len(rows)}행), 이번 조회 {len(todo)}개", flush=True)
    hira = load_hira_products(a.db, helpers[1])
    print(f"HIRA 제품 {len(hira)}개 로드", flush=True)
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(resolve_brand, b, hira, helpers): b for b in todo}
        for n, f in enumerate(as_completed(futs), 1):
            b = futs[f]
            try:
                res = f.result()
            except Exception as e:
                res = {"brand": b, "status": "error", "errors": 1, "tried": [f"EXC:{str(e)[:60]}"]}
            res["rows"] = [{"id": r["id"], "session_date": r["session_date"], "has_permit": bool(r["mfds_permit_date"]),
                            "has_effect": bool((r["mfds_effect_text"] or "").strip())} for r in brands[b]]
            sd = [r["session_date"] for r in brands[b] if r["session_date"]]
            pd = (res.get("permit_date") or "").replace(".", "-")
            res["permit_after_session"] = bool(pd and sd and min(sd) < pd)
            done[b] = res
            out_path.write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")
            print(f"[{n}/{len(todo)}] {b[:18]} → {res['status']} {res.get('permit_date') or ''}", flush=True)
    from collections import Counter
    print("요약", dict(Counter(v["status"] for v in done.values())), flush=True)


if __name__ == "__main__":
    main()
