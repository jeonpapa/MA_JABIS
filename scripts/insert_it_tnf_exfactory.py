#!/usr/bin/env python3
"""이탈리아(IT) TrovaNorme 결정문의 'Prezzo ex-factory (IVA esclusa)' 를 새 행으로 추가한다 (2026-10-26, 사용자 결정).

배경: IT Winrevair 행은 AIFA 목록 Classe A '공중가(Prezzo al pubblico)'(예: 12,378.00)를 저장해 왔다. 별첨5 의 이탈리아
기준은 공장도가(Codifa/AIFA ex-factory) × 0.93 이므로, TrovaNorme 결정문(DET PRES/UPR 163/2026)의 ex-factory 가격으로 교체한다.

방식: insert_de_avp_uvp.py 와 동일 — 최신 행을 복사한 새 행(searched_at=지금)을 넣고 이전 행은 이력으로 남긴다.
계산은 현재 계산기(IT = 국가 기본 0.93)로 한다.

사용:
    python scripts/insert_it_tnf_exfactory.py --plan plan.json [--apply]
plan.json = [{source_id, ex_factory, pubblico, pack_count, aic, determina, document_date, publication_date,
              effective_date, source_url, evidence_ref}]
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

LABEL = "AIFA TrovaNorme (Prezzo ex-factory IVA esclusa)"


def build_row(src, e, now):
    row = dict(src)
    row.pop("id", None)
    pack = int(e["pack_count"])
    price = float(e["ex_factory"])
    calc = PriceCalculator().calculate_adjusted_price("IT", price, row["exchange_rate"], pack_count=pack, source_type="aifa_exfactory")
    try:
        raw = json.loads(src["raw_data"] or "{}")
    except ValueError:
        raw = {}
    for k in ("aifa_class", "price_kind", "price_tier_original"):
        raw.pop(k, None)
    raw.update({
        "channel": "tnf_determina", "price_kind": "Prezzo ex-factory (IVA esclusa)",
        "price_tier": "ex_factory", "price_tier_original": "Prezzo ex-factory (IVA esclusa)", "vat_status": "excluded",
        "prezzo_pubblico": e.get("pubblico"), "aic": e.get("aic"), "determina": e.get("determina"),
        "document_date": e.get("document_date"), "publication_date": e.get("publication_date"),
        "effective_date": e.get("effective_date"), "evidence_ref": e.get("evidence_ref"),
        "source_type": "aifa_exfactory", "replaces_row_id": src["id"],
        "selection_note": "사용자 결정(2026-10-26): TrovaNorme 공장도가 × 0.93 (별첨5). 기존 Classe A 공중가 행을 대체.",
        "collected_via": "Aside browser (TrovaNorme tnf-service)",
    })
    old_adj = row.get("adjusted_price_krw")
    row.update({
        "searched_at": now, "local_price": price, "pack_count": pack, "pack_count_source": "scraper",
        "per_unit_local": calc["per_unit_listed"], "factory_price_krw": calc["factory_price_krw"],
        "adjusted_price_krw": calc["adjusted_price_krw"], "vat_rate": calc["vat_rate"],
        "distribution_margin": calc["distribution_margin"], "source_label": f"{LABEL} {e.get('determina','')}".strip(),
        "source_url": e.get("source_url") or row.get("source_url"), "raw_data": json.dumps(raw, ensure_ascii=False),
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
    now = datetime.now().isoformat(timespec="seconds")
    out = []
    for e in plan:
        src = conn.execute("SELECT * FROM foreign_drug_prices WHERE id=? AND country='IT'", (e["source_id"],)).fetchone()
        if src is None:
            print(f"SKIP {e['source_id']}: 없음")
            continue
        row, calc = build_row(src, e, now)
        out.append(row)
        print(f"src #{src['id']} {(src['dosage_strength'] or '')[:46]:46} 공중가 {src['local_price']} → 공장도가 {row['local_price']} "
              f"(pack {row['pack_count']}) 비율 {calc['factory_ratio']} 조정가 {src['adjusted_price_krw']} → {row['adjusted_price_krw']}")
    if not a.apply:
        print("DRY-RUN (적용하려면 --apply)")
        return 0
    cols = [r[1] for r in conn.execute("PRAGMA table_info(foreign_drug_prices)") if r[1] != "id"]
    ids = []
    with conn:
        for row in out:
            cur = conn.execute(f"INSERT INTO foreign_drug_prices ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                               [row.get(c) for c in cols])
            ids.append(cur.lastrowid)
    undo = Path(a.db).parent / f"it_tnf_undo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    json.dump({"inserted_ids": ids}, open(undo, "w"), indent=1)
    print(f"INSERTED {len(ids)} rows {ids} / undo={undo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
