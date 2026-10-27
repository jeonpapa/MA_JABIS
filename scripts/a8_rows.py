#!/usr/bin/env python3
"""A8 해외약가 요청 업로드 행 생성기 — DE / FR / CH (표준 라이브러리만, Aside 워커용).

같은 파서(agents/foreign_sources/*)로 화면 텍스트·공개 API 를 읽어 `price_request_client.mjs upload` 형식
{"status": "priced", "rows": [...]} 를 만든다. 규칙 원문: docs/foreign_price/README.md.
값은 원문 그대로이며 KRW·조정가는 넣지 않는다(서버 계산기 몫). 사람이 확인한 뒤 업로드한다.

  DE  (Rote Liste, DocCheck 로그인 후 상세 페이지 본문 텍스트를 파일로 저장)
      python3 scripts/a8_rows.py de --text page.txt --url URL --product "PREVYMIS 240 mg Filmtabletten" \\
          --ingredient letermovir --form oral --strength "240 mg" --dosage-form Filmtabletten [--pzn 13863501 ...]
  FR  (Légifrance 관보 공시 본문 텍스트 — 브라우저로 열 것)
      python3 scripts/a8_rows.py fr --text jo.txt --url URL --ingredient letermovir --form oral [--code 3400930127247 ...]
  CH  (BAG SL 공개 API 직접 조회)
      python3 scripts/a8_rows.py ch --trademark Winrevair --ingredient sotatercept --form injection [--strength "45 mg"]
공통: --observed-at ISO(기본 지금, KST) --evidence 증빙파일 --out 출력파일(기본 stdout)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.foreign_sources import ch_sl, de_rote_liste_table as de_tbl, fr_jo  # noqa: E402

KST = timezone(timedelta(hours=9))


def _now() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def _strength(text: str) -> str:
    m = re.search(r"\d+(?:[.,]\d+)?\s*(?:mg|µg|g)(?:\s*/\s*\d*(?:[.,]\d+)?\s*m[lL])?", text or "", re.I)
    return m.group(0) if m else ""


def _base(a, **kw) -> dict:
    row = {"ingredient": a.ingredient, "form_type": a.form, "observed_at": a.observed_at or _now(),
           "evidence_ref": a.evidence or "", "effective_date": None}
    row.update(kw)
    return row


def build_de(a) -> tuple[list, list]:
    text = Path(a.text).read_text(encoding="utf-8")
    rows, skipped = [], []
    stand = de_tbl.preisstand(text)
    for p in de_tbl.parse_packungsangaben(text):
        if a.pzn and p["pzn"] not in a.pzn:
            continue
        if p["avp_uvp"] is None:
            skipped.append(f"PZN {p['pzn']} {p['pack']}: AVP/UVP 없음(EB/FB 만) — 가격 미확정")
            continue
        n = de_tbl.pack_count(p["pack"])
        if not n:
            skipped.append(f"PZN {p['pzn']} {p['pack']}: 포장 수량을 읽지 못함 — 수동 확인")
            continue
        rows.append(_base(
            a, product_name=a.product, dosage_strength=a.strength or _strength(p["pack"]),
            dosage_form=a.dosage_form or "", package_unit=p["pack"], local_price=p["avp_uvp"], currency="EUR",
            pack_count=n, price_tier="pharmacy_retail", price_tier_original="AVP/UVP (Rote Liste Packungsangaben)",
            vat_status="included", source_url=a.url, source_label="Rote Liste (AVP/UVP; DocCheck)",
            extra={"pzn": p["pzn"], "n_size": p["n"], "avp_eb_fb_reference": p["avp_eb_fb"], "eb_label": p["eb_label"],
                   "price_as_of": stand, "effective_date_note": "Preisstand 는 자료 기준일 — 개별 가격 시행일 아님"},
        ))
    return rows, skipped


def build_fr(a) -> tuple[list, list]:
    text = Path(a.text).read_text(encoding="utf-8")
    notice = fr_jo.parse_notice(text)
    pub, eff = notice["publication"], notice["effective_date"]
    rows, skipped = [], []
    if not notice["kind"]:
        return [], ["관보 표 형식(PFHT 또는 Code UCD)을 찾지 못함 — 약가 공시 본문이 맞는지 확인"]
    for r in notice["rows"]:
        if a.code and r["code"] not in a.code:
            continue
        common = dict(currency="EUR", price_tier="ex_factory", vat_status="excluded", source_url=a.url,
                      source_label=f"Journal officiel ({'PFHT' if notice['kind'] == 'cip' else 'tarif HT par UCD'}) {pub['jorf'] or ''}".strip())
        jo = {"jo_ref": pub["jorf"], "nor": pub["nor"], "publication_date": pub["publication_date"],
              "effective_date_basis": "관보 시행 문구(Date d'effet 열 우선)"}
        if notice["kind"] == "cip":
            n = fr_jo.pack_count(r["presentation"])
            if not n:
                skipped.append(f"CIP {r['code']}: 포장 수량을 읽지 못함 — 수동 확인")
                continue
            rows.append(_base(
                a, product_name=r["presentation"].split("(laboratoires")[0].strip(), dosage_strength=_strength(r["presentation"]),
                dosage_form=a.dosage_form or "", package_unit=r["presentation"], local_price=r["pfht"], pack_count=n,
                price_tier_original="PFHT (prix fabricant hors taxe, Journal officiel)",
                effective_date=r["date_effet"] or eff, extra={"cip13": r["code"], "ppttc_reference": r["ppttc"], **jo}, **common))
        else:
            rows.append(_base(
                a, product_name=r["libelle"], dosage_strength=_strength(r["libelle"]), dosage_form=a.dosage_form or "",
                package_unit="1 UCD", local_price=r["tarif_ht"], pack_count=1,
                price_tier_original="Tarif de responsabilité HT par UCD (Journal officiel, L.162-16-6)",
                effective_date=eff, extra={"ucd13": r["code"], "plv_ht": r["plv_ht"], **jo}, **common))
    if eff is None and not any(r.get("effective_date") for r in rows):
        skipped.append("시행일 문구를 읽지 못함 — effective_date=null 로 둠")
    return rows, skipped


def build_ch(a) -> tuple[list, list]:
    data = json.loads(Path(a.json).read_text()) if a.json else ch_sl.fetch_json(a.trademark)
    rows, skipped = [], []
    today = datetime.now(KST).date().isoformat()
    for p in ch_sl.flatten_packs(data):
        if a.strength and a.strength.lower().replace(" ", "") not in (p["strength"] or "").lower().replace(" ", ""):
            continue
        n = ch_sl.pack_unit_count(p["pack_size"])
        if not p.get("ex_factory_price") or not n:
            skipped.append(f"{p['strength']} {p['pack_size']}: SL FAP 또는 포장 수량 없음 — Compendium × 0.65 대체 검토")
            continue
        eff = p.get("last_price_change") if (p.get("last_price_change") or "9999") <= today else None
        rows.append(_base(
            a, product_name=f"{p['trademark']} {p['strength']}", dosage_strength=_strength(p["strength"]),
            dosage_form=a.dosage_form or p["strength"], package_unit=p["pack_size"], local_price=float(p["ex_factory_price"]),
            currency="CHF", pack_count=n, price_tier="ex_factory", price_tier_original="Fabrikabgabepreis (FAP, BAG SL)",
            vat_status="excluded", effective_date=eff, source_url=ch_sl.sl_search_url(p["trademark"]),
            source_label="BAG Spezialitätenliste (FAP)",
            extra={"gtin": p["gtin"], "retail_price_reference": p["retail_price"], "list_type": p["list_type"],
                   "last_price_change_type": p["last_price_change_type"], "valid_to": p["valid_to"]},
        ))
    return rows, skipped


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cc", required=True)
    for cc in ("de", "fr", "ch"):
        sp = sub.add_parser(cc)
        sp.add_argument("--ingredient", required=True)
        sp.add_argument("--form", required=True, choices=["oral", "injection", "unknown"])
        sp.add_argument("--dosage-form", default="")
        sp.add_argument("--observed-at")
        sp.add_argument("--evidence")
        sp.add_argument("--out")
        if cc in ("de", "fr"):
            sp.add_argument("--text", required=True)
            sp.add_argument("--url", required=True)
        if cc == "de":
            sp.add_argument("--product", required=True)
            sp.add_argument("--strength", default="")
            sp.add_argument("--pzn", action="append")
        if cc == "fr":
            sp.add_argument("--code", action="append", help="CIP13 또는 UCD13 (반복 가능)")
        if cc == "ch":
            sp.add_argument("--trademark", required=True)
            sp.add_argument("--strength", default="")
            sp.add_argument("--json", help="SL API 응답 저장본(없으면 실시간 조회)")
    a = ap.parse_args()
    rows, skipped = {"de": build_de, "fr": build_fr, "ch": build_ch}[a.cc](a)
    out = {"status": "priced" if rows else "unconfirmed", "rows": rows}
    if not rows:
        out["reason"] = "; ".join(skipped) or "일치하는 행 없음"
    text = json.dumps(out, ensure_ascii=False, indent=1)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    else:
        print(text)
    for s in skipped:
        print("SKIP:", s, file=sys.stderr)
    print(f"rows={len(rows)} skipped={len(skipped)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
