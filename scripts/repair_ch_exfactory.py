#!/usr/bin/env python3
"""저장된 스위스(CH) 행에 BAG SL 공식 공장도가(FAP)를 채우고 출처 링크 slug 를 바로잡는다 (개선 요청 #3, 2026-10).

1) 출처 링크: 과거 스크레이퍼가 모든 제품에 slug="keytruda" 를 박았다 → 제품명 기반 slug 로 교정(MNR 은 그대로).
2) 공식 공장도가: SL 공개 API 의 판매가(retailPrice)가 저장된 local_price 와 일치하는 팩이 하나로 특정될 때만
   raw_data.ex_factory_price(원포장, CHF, VAT 제외) + 출처/GTIN/팩 구성을 기록하고, 저장 파생값을 다시 계산한다.
   - pack_count 는 SL 팩 구성(예: Keytruda 100mg/4ml = "2 Stk")과 다를 때만 정정(source='scraper').
   - factory_price_krw / adjusted_price_krw / adjusted_price_krw_normalized / per_unit_local / daily_cost_krw 갱신
     (daily·normalized 는 조정가에 선형이므로 새/구 조정가 비율로 스케일).
   불일치·모호·조회 실패는 건드리지 않는다(기존 추정 0.73 유지, 임의 보정 없음).
3) 값 변경은 compare-and-set(id + 구 local_price + 구 adjusted_price_krw). 구값은 undo JSON 으로 DB 옆에 남긴다.

사용:
    python scripts/repair_ch_exfactory.py                      # DRY-RUN (읽기 전용), 표 출력 + --plan-out JSON
    python scripts/repair_ch_exfactory.py --apply              # 적용 (SL 조회 → 같은 실행에서 적용)
    python scripts/repair_ch_exfactory.py --apply --plan plan.json   # 다른 DB 에 같은 계획 적용(키: query_name+product_name+local_price)
    python scripts/repair_ch_exfactory.py --url-only [--apply]       # 링크 slug 만(FAP·pack 변경 없음)
옵션: --db PATH (기본 data/db/drug_prices.db)
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.exchange_rate import PriceCalculator  # noqa: E402
from agents.scrapers.ch_compendium import product_slug  # noqa: E402
from agents.scrapers.ch_sl import (fetch_sl_packs, match_pack, pack_unit_count, sl_search_url)  # noqa: E402

BAD_SLUG = "/keytruda"


def fixed_url(row) -> str | None:
    """'.../product/<mnr>/keytruda' 인데 제품이 키트루다가 아니면 제품명 slug 로. 이미 맞으면 None."""
    url = row["source_url"] or ""
    if not url.endswith(BAD_SLUG):
        return None
    if "keytruda" in (row["product_name"] or "").lower():
        return None            # 진짜 키트루다 — 그대로
    return url[: -len("keytruda")] + product_slug(row["product_name"])


def build_plan(conn, url_only: bool) -> list[dict]:
    rows = conn.execute("SELECT * FROM foreign_drug_prices WHERE country='CH' ORDER BY id").fetchall()
    packs_by_q: dict[str, list] = {}
    plan = []
    for r in rows:
        item = {"id": r["id"], "query_name": r["query_name"], "product_name": r["product_name"],
                "local_price": r["local_price"], "old_url": r["source_url"], "new_url": fixed_url(r)}
        if not url_only:
            q = (r["query_name"] or "").split("/")[0].strip()
            if q not in packs_by_q:
                packs_by_q[q] = fetch_sl_packs(q)
            hit = match_pack(packs_by_q[q], r["local_price"], strength=r["dosage_strength"] or r["product_name"])
            if hit:
                item.update({
                    "fap": float(hit["ex_factory_price"]), "sl_pack_size": hit["pack_size"], "gtin": hit["gtin"],
                    "sl_trademark": hit["trademark"], "sl_strength": hit["strength"],
                    "sl_pack_count": pack_unit_count(hit["pack_size"]),
                    "fap_url": sl_search_url(hit["trademark"] or q),
                })
        plan.append(item)
    return plan


def derive(row, entry) -> dict | None:
    """행 하나의 새 저장값. FAP 가 없으면 None."""
    if "fap" not in entry:
        return None
    calc = PriceCalculator()
    n = entry.get("sl_pack_count") or row["pack_count"] or 1
    res = calc.calculate_adjusted_price("CH", row["local_price"], row["exchange_rate"], pack_count=n,
                                        source_type="compendium", exfactory_price=entry["fap"])
    old_adj = row["adjusted_price_krw"]
    new_adj = res["adjusted_price_krw"]
    scale = (new_adj / old_adj) if old_adj else None
    raw = json.loads(row["raw_data"] or "{}")
    raw.update({"ex_factory_price": entry["fap"],
                "ex_factory_source": "BAG SL (Fabrikabgabepreis, VAT 제외)",
                "ex_factory_url": entry["fap_url"], "sl_pack_size": entry["sl_pack_size"], "sl_gtin": entry["gtin"]})
    new = {"raw_data": json.dumps(raw, ensure_ascii=False),
           "pack_count": n, "pack_count_source": "scraper",
           "per_unit_local": res["per_unit_listed"],
           "factory_price_krw": res["factory_price_krw"], "adjusted_price_krw": new_adj}
    cols = row.keys()
    if scale is not None:
        if "adjusted_price_krw_normalized" in cols and row["adjusted_price_krw_normalized"] is not None:
            new["adjusted_price_krw_normalized"] = int(round(row["adjusted_price_krw_normalized"] * scale))
        if row["daily_cost_krw"] is not None:
            new["daily_cost_krw"] = int(round(row["daily_cost_krw"] * scale))
    return new


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data/db/drug_prices.db"))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--url-only", action="store_true")
    ap.add_argument("--plan", help="미리 만든 계획 JSON 사용(SL 재조회 안 함)")
    ap.add_argument("--plan-out", help="계획 JSON 저장")
    a = ap.parse_args()

    uri = f"file:{a.db}?mode=" + ("rw" if a.apply else "ro")
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    rows = {r["id"]: r for r in conn.execute("SELECT * FROM foreign_drug_prices WHERE country='CH'")}

    if a.plan:
        saved = json.load(open(a.plan, encoding="utf-8"))
        key = lambda e: (e["query_name"], e["product_name"], e["local_price"])  # noqa: E731
        by_key = {key(e): e for e in saved}
        plan = []
        for r in rows.values():
            e = by_key.get((r["query_name"], r["product_name"], r["local_price"]))
            base = {"id": r["id"], "query_name": r["query_name"], "product_name": r["product_name"],
                    "local_price": r["local_price"], "old_url": r["source_url"], "new_url": fixed_url(r)}
            if e and not a.url_only:
                base.update({k: v for k, v in e.items() if k in ("fap", "sl_pack_size", "gtin", "sl_trademark",
                                                                 "sl_strength", "sl_pack_count", "fap_url")})
            plan.append(base)
    else:
        plan = build_plan(conn, a.url_only)
    if a.plan_out:
        json.dump(plan, open(a.plan_out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    changes, undo = [], []
    for e in plan:
        r = rows[e["id"]]
        new = derive(r, e) or {}
        if e.get("new_url"):
            new["source_url"] = e["new_url"]
        if not new:
            continue
        old = {k: r[k] for k in new}
        changes.append((e, r, old, new))
        undo.append({"id": r["id"], "expect_local_price": r["local_price"], "expect_adjusted": r["adjusted_price_krw"],
                     "old": old})

    print(f"CH 행 {len(rows)} / 변경 {len(changes)} / FAP 채움 {sum(1 for c in changes if 'raw_data' in c[3])} / 링크 교정 {sum(1 for c in changes if 'source_url' in c[3])}")
    for e, r, old, new in changes:
        bits = []
        if "raw_data" in new:
            bits.append(f"FAP {e['fap']} ({e['sl_pack_size']}) pack {old['pack_count']}→{new['pack_count']} "
                        f"공장도가원 {old['factory_price_krw']}→{new['factory_price_krw']} 조정가 {old['adjusted_price_krw']}→{new['adjusted_price_krw']}"
                        + (f" 일일 {old.get('daily_cost_krw')}→{new.get('daily_cost_krw')}" if 'daily_cost_krw' in new else ""))
        if "source_url" in new:
            bits.append(f"링크 …{old['source_url'][-34:]} → …{new['source_url'][-40:]}")
        print(f"#{r['id']} {(r['product_name'] or '')[:36]:36} " + " | ".join(bits))

    if not a.apply:
        print("DRY-RUN (적용하려면 --apply)")
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    undo_path = Path(a.db).parent / f"ch_exfactory_undo_{stamp}.json"
    json.dump(undo, open(undo_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    applied = skipped = 0
    with conn:
        for e, r, old, new in changes:
            sets = ", ".join(f"{k}=:{k}" for k in new)
            cur = conn.execute(
                f"UPDATE foreign_drug_prices SET {sets} WHERE id=:_id AND local_price IS :_lp AND adjusted_price_krw IS :_adj",
                {**new, "_id": r["id"], "_lp": r["local_price"], "_adj": r["adjusted_price_krw"]})
            applied += cur.rowcount
            skipped += 1 - cur.rowcount
    print(f"APPLIED {applied} / SKIPPED(불일치) {skipped} / undo={undo_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
