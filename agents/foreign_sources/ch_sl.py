"""스위스 BAG Spezialitätenliste(SL) 공개 API — 팩별 공장도가(FAP)·판매가(PP). 표준 라이브러리만 사용.

GET https://epl.bag.admin.ch/api/sl/public/medicinal-products?search=<상표>&page=1&size=25   (page 는 1부터)
웹 화면 https://sl.bag.admin.ch 이 호출하는 것과 같은 공개 엔드포인트(로그인 불필요).
별첨5: 스위스 = SL 공장도가 × 0.73. SL 에서 약가가 확인되지 않을 때만 Compendium 약가 × 0.65.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Optional

SL_API = "https://epl.bag.admin.ch/api/sl/public/medicinal-products"
SL_PAGE = "https://sl.bag.admin.ch/sl"
HEADERS = {"accept": "application/json", "user-agent": "Mozilla/5.0 (MA-AI-Dossier price check)"}
PRICE_TOL = 0.006


def fetch_json(trademark: str, timeout: int = 30) -> dict:
    url = SL_API + "?" + urllib.parse.urlencode({"search": trademark, "page": 1, "size": 25})
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=timeout) as r:
        return json.loads(r.read())


def flatten_packs(data: dict) -> list[dict]:
    out = []
    for item in (data or {}).get("items", []) or []:
        for mp in item.get("medicinalProducts", []) or []:
            ing = mp.get("ingredients") or []
            for p in mp.get("packagedMedicinalProducts", []) or []:
                out.append({
                    "trademark": item.get("trademark") or mp.get("trademark"),
                    "strength": mp.get("dosageFormAndStrength"),
                    "substance": ", ".join(i.get("substance", "") for i in ing if i.get("substance")),
                    "pack_size": p.get("packSize"),
                    "ex_factory_price": p.get("exFactoryPrice"),
                    "retail_price": p.get("retailPrice"),
                    "gtin": p.get("gtin"),
                    "list_type": p.get("listType"),
                    "last_price_change": p.get("lastPriceChange"),
                    "last_price_change_type": p.get("lastPriceChangeType"),
                    "valid_from": p.get("validFrom"),
                    "valid_to": p.get("validTo"),
                })
    return out


def pack_unit_count(pack_size: Optional[str]) -> Optional[int]:
    """'2 Stk' / 'Durchstf 1 Stk' / 'Btl 30 Stk' → 팩 내 최소단위 수. 읽을 수 없으면 None."""
    m = re.search(r"(\d+)\s*Stk", pack_size or "")
    return int(m.group(1)) if m else None


def match_pack(packs: list[dict], retail_price: float, *, strength: Optional[str] = None) -> Optional[dict]:
    """판매가가 일치하는 팩 하나. 후보 0개 또는 서로 다른 FAP 를 가진 2개 이상이면 None(모호 — 채우지 않는다)."""
    if retail_price is None:
        return None
    cands = [p for p in packs
             if p.get("retail_price") is not None and p.get("ex_factory_price") is not None
             and abs(float(p["retail_price"]) - float(retail_price)) < PRICE_TOL]
    if strength and len(cands) > 1:
        narrowed = [p for p in cands if (p.get("strength") or "").lower().startswith(strength.lower()[:12])]
        cands = narrowed or cands
    if not cands:
        return None
    if len({round(float(p["ex_factory_price"]), 2) for p in cands}) != 1:
        return None
    return cands[0]


def sl_search_url(trademark: str) -> str:
    return f"{SL_PAGE}?search={urllib.parse.quote(trademark)}&page=1&size=25"
