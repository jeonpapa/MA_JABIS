#!/usr/bin/env python3
"""프랑스(FR) 공장도가(PFHT) — 관보(Journal officiel, Légifrance) 가격 공시값을 새 행으로 추가한다 (2026-10-27, 사용자 승인).

배경: 별첨5 의 프랑스 기준은 BdM_IT(Assurance Maladie)의 공장도가(Prix Fabricant HT) × 0.77 이다. BdM_IT(codage.ext.cnamts.fr)는
2026-10 중순부터 파리 포함 전 세계에서 응답하지 않는다(외부 점검 13개국 시간초과). BdM_IT 의 가격은 CEPS 협약에 따라 관보
「Avis relatif aux prix de spécialités pharmaceutiques」 로 공시된 PFHT/PPTTC 와 같은 값이므로 관보 원문을 자료원으로 쓴다.
기존 FR 행은 BDPM 공중가(PPTTC, 일부는 조제료 1.02 € 포함) × 0.77 이었다.

방식: insert_de_avp_uvp.py 와 동일 — 최신 행 복사 + searched_at=지금 + local_price=PFHT. source_type=None → FR 국가 기본 0.77.
이전 행은 이력으로 남는다(삭제 없음). 값은 관보 화면 그대로, 임의 보정 없음.

사용:
    python scripts/insert_fr_jo_pfht.py --plan plan.json [--apply]
plan.json = [{source_id, pfht, ppttc, pack_count, cip13, jo_ref, jo_date, effective_date, source_url, evidence_ref}]
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

LABEL = "Journal officiel (PFHT, avis de prix CEPS)"


def build_row(src, e, now):
    row = dict(src)
    row.pop("id", None)
    pack = int(e["pack_count"])
    price = float(e["pfht"])
    calc = PriceCalculator().calculate_adjusted_price("FR", price, row["exchange_rate"], pack_count=pack, source_type=None)
    try:
        raw = json.loads(src["raw_data"] or "{}")
    except ValueError:
        raw = {}
    for k in ("price_note", "price_including_dispensing_fee", "taux_remboursement"):
        raw.pop(k, None)
    raw.update({
        "price_tier": "ex_factory", "price_tier_original": e.get("price_tier_original") or "PFHT (prix fabricant hors taxe)",
        "vat_status": "excluded", "ucd13": e.get("ucd13"),
        "pfht": price, "ppttc_reference": e.get("ppttc"), "cip13": e.get("cip13") or raw.get("cip13"),
        "jo_ref": e.get("jo_ref"), "publication_date": e.get("jo_date"), "effective_date": e.get("effective_date"),
        "evidence_ref": e.get("evidence_ref"), "source_type": None, "replaces_row_id": src["id"],
        "selection_note": ("사용자 승인(2026-10-27): 별첨5 FR = 공장도가 × 0.77. BdM_IT 접속 불가로 같은 공식 가격을 관보에서 확인. "
                           "이전 행은 BDPM 공중가 × 0.77."),
        "collected_via": "Aside browser (Légifrance)",
    })
    old_adj = row.get("adjusted_price_krw")
    row.update({
        "searched_at": now, "local_price": price, "pack_count": pack, "pack_count_source": "scraper",
        "per_unit_local": calc["per_unit_listed"], "factory_price_krw": calc["factory_price_krw"],
        "adjusted_price_krw": calc["adjusted_price_krw"], "vat_rate": calc["vat_rate"],
        "distribution_margin": calc["distribution_margin"], "source_label": f"{LABEL} {e.get('jo_ref', '')}".strip(),
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
        src = conn.execute("SELECT * FROM foreign_drug_prices WHERE id=? AND country='FR'", (e["source_id"],)).fetchone()
        if src is None:
            print(f"SKIP {e['source_id']}: 없음")
            continue
        raw_cip = json.loads(src["raw_data"] or "{}").get("cip13")
        if raw_cip and e.get("cip13") and raw_cip != e["cip13"]:
            print(f"SKIP {e['source_id']}: CIP 불일치 저장 {raw_cip} vs 관보 {e['cip13']}")
            continue
        row, calc = build_row(src, e, now)
        out.append(row)
        print(f"src #{src['id']} {(src['product_name'] or '')[:34]:34} pack {row['pack_count']} 공중가 {src['local_price']} → PFHT {row['local_price']} "
              f"비율 {calc['factory_ratio']} 조정가 {src['adjusted_price_krw']} → {row['adjusted_price_krw']}")
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
    undo = Path(a.db).parent / f"fr_jo_undo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    json.dump({"inserted_ids": ids}, open(undo, "w"), indent=1)
    print(f"INSERTED {len(ids)} rows {ids} / undo={undo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
