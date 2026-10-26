#!/usr/bin/env python3
"""독일(DE) Rote Liste AVP/UVP 재수집 결과를 새 행으로 추가한다 (2026-10-26, 사용자 결정).

배경: 기존 DE 행은 AVP (EB)/FB(협상 상환액 기준) 또는 출처 불명 값이었다. 별첨5 는 Rote Liste 약국판매가에서
공장도가를 역산하고(별도 산식), 사용자는 AVP/UVP(목록가)를 기준 가격으로 쓰기로 했다.

방식: 최신 행(source_id)을 복사해 searched_at 을 지금으로, local_price 를 AVP/UVP 로 바꾼 **새 행**을 넣는다.
- 읽기 쪽(get_foreign_presentations)은 (country, 강도, 제형, 포장) 별 최신 priced 행을 고르므로 새 행이 이전 행을 대체하고,
  이전 EB 행은 이력으로 남는다(삭제 없음).
- raw_data 에 EB/FB 참고값·PZN·Preisstand·증빙 경로를 보존한다. 임의 보정 없음(화면 값 그대로).
- 파생 저장값(공장도가·조정가 등)은 현재 계산기(별첨5 독일 산식)로 계산한다. daily_cost·normalized 는 source 행 값이 있으면
  조정가 비율로 스케일, 없으면 비운다(읽기 시 재계산).

사용:
    python scripts/insert_de_avp_uvp.py --plan plan.json            # DRY-RUN
    python scripts/insert_de_avp_uvp.py --plan plan.json --apply    # 적용 (undo: 새 행 id 목록 JSON)
plan.json = [{source_id, avp_uvp, avp_eb_fb, eb_label, pzn, pack_text, pack_count, preisstand, evidence_ref, source_url?}]
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

SOURCE_LABEL = "Rote Liste (AVP/UVP; DocCheck)"


def build_row(src: sqlite3.Row, e: dict, now: str, eur_fallback: dict) -> tuple[dict, dict]:
    row = dict(src)
    row.pop("id", None)
    old_adj = row.get("adjusted_price_krw")
    pack = int(e.get("pack_count") or row.get("pack_count") or 1)
    rate = row.get("exchange_rate") or eur_fallback["exchange_rate"]
    price = float(e["avp_uvp"])
    calc = PriceCalculator().calculate_adjusted_price("DE", price, rate, pack_count=pack, source_type=None)

    raw = {}
    try:
        raw = json.loads(src["raw_data"] or "{}")
    except ValueError:
        pass
    old_pzn = raw.get("pzn")
    for k in ("all_prices", "login_wall", "selection_note", "avp_uvp_reference", "effective_date_note", "vat_note"):
        raw.pop(k, None)
    raw.update({
        "pzn": e.get("pzn") or old_pzn, "pack_text": e.get("pack_text"),
        "price_tier": "pharmacy_retail", "price_tier_original": "AVP/UVP (Rote Liste Packungsangaben)",
        "price_column": "AVP/UVP", "avp_eb_fb_reference": e.get("avp_eb_fb"), "eb_label": e.get("eb_label"),
        "price_as_of": e.get("preisstand"), "vat_status": "unknown",
        "vat_note": "원문 화면에 VAT 포함 여부 별도 미표시(약국판매가 — 별첨5 산식이 부가세 19% 를 역산)",
        "effective_date": None, "effective_date_note": "Preisstand 는 자료 기준일로 개별 가격 시행일 아님",
        "selection_note": "사용자 결정(2026-10-26): AVP/UVP 를 기준 가격으로 사용. EB/FB 는 참고 보존.",
        "evidence_ref": e.get("evidence_ref"), "source_type": None,
        "replaces_row_id": src["id"], "collected_via": "Aside browser (DocCheck login)",
    })
    row.update({
        "searched_at": now, "local_price": price, "currency": "EUR", "exchange_rate": rate,
        "exchange_rate_from": row.get("exchange_rate_from") or eur_fallback["exchange_rate_from"],
        "exchange_rate_to": row.get("exchange_rate_to") or eur_fallback["exchange_rate_to"],
        "pack_count": pack, "pack_count_source": "scraper", "per_unit_local": calc["per_unit_listed"],
        "factory_price_krw": calc["factory_price_krw"], "adjusted_price_krw": calc["adjusted_price_krw"],
        "vat_rate": calc["vat_rate"], "distribution_margin": calc["distribution_margin"],
        "source_label": SOURCE_LABEL, "source_url": e.get("source_url") or row.get("source_url"),
        "form_type": e.get("form_type") or row.get("form_type"),
        "raw_data": json.dumps(raw, ensure_ascii=False),
    })
    scale = (calc["adjusted_price_krw"] / old_adj) if old_adj else None
    for col in ("adjusted_price_krw_normalized", "daily_cost_krw"):
        if col in row:
            row[col] = int(round(row[col] * scale)) if (scale and row[col] is not None) else None
    return row, calc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data/db/drug_prices.db"))
    ap.add_argument("--plan", required=True)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    plan = json.load(open(a.plan, encoding="utf-8"))
    conn = sqlite3.connect(f"file:{a.db}?mode=" + ("rw" if a.apply else "ro"), uri=True)
    conn.row_factory = sqlite3.Row
    eur = conn.execute("SELECT exchange_rate, exchange_rate_from, exchange_rate_to FROM foreign_drug_prices "
                       "WHERE country='DE' AND exchange_rate IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
    eur_fb = dict(eur)
    now = datetime.now().isoformat(timespec="seconds")
    new_rows = []
    for e in plan:
        src = conn.execute("SELECT * FROM foreign_drug_prices WHERE id=? AND country='DE'", (e["source_id"],)).fetchone()
        if src is None:
            print(f"SKIP source {e['source_id']}: 없음")
            continue
        row, calc = build_row(src, e, now, eur_fb)
        new_rows.append((src, row, calc))
        print(f"src #{src['id']} {src['query_name']:22} {src['local_price']} → AVP/UVP {row['local_price']} (pack {row['pack_count']}) "
              f"비율 {calc['factory_ratio']:.4f} 공장도가원 {src['factory_price_krw']} → {row['factory_price_krw']} "
              f"조정가 {src['adjusted_price_krw']} → {row['adjusted_price_krw']}")
    if not a.apply:
        print("DRY-RUN (적용하려면 --apply)")
        return 0
    cols = [r[1] for r in conn.execute("PRAGMA table_info(foreign_drug_prices)") if r[1] != "id"]
    ids = []
    with conn:
        for src, row, _ in new_rows:
            vals = [row.get(c) for c in cols]
            cur = conn.execute(f"INSERT INTO foreign_drug_prices ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)
            ids.append(cur.lastrowid)
    undo = Path(a.db).parent / f"de_avp_uvp_undo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    json.dump({"inserted_ids": ids}, open(undo, "w"), indent=1)
    print(f"INSERTED {len(ids)} rows {ids} / undo={undo}  (되돌리기: 이 id 행 삭제)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
