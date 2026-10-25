"""스위스 BAG 의약품 목록(SL, Spezialitätenliste) 공개 API — 실제 공장도가(FAP) 조회.

배경 (2026-10, 개선 요청 #3): Compendium 의 Publikumspreis(PP, 약국 판매가) 에 고정 비율 0.73 을 곱해 공장도가를
추정해 왔는데, 공식 SL 은 팩별 Fabrikabgabepreis(exFactoryPrice, FAP) 를 직접 공개한다. 윈레바이어 45mg 은
추정 4,640 대 실제 5,895.82 CHF(약 21% 차이)였다.

공개 엔드포인트(로그인 불필요, 웹 화면 https://sl.bag.admin.ch 이 호출하는 것과 동일):
    GET https://epl.bag.admin.ch/api/sl/public/medicinal-products?search=<상표>&page=1&size=25   (page 는 1부터)

원칙:
- 판매가(retailPrice)가 저장된 Publikumspreis 와 일치하고, 같은 팩이 하나로 특정될 때만 FAP 를 채택한다.
  불일치·모호·조회 실패는 None — 호출측은 기존 추정 비율(CH 0.73)로 되돌아가되 라벨에 '추정' 을 남긴다.
- 값은 CHF, VAT 제외 공장도가(원포장 단위). 임의 보정 없음.
"""
from __future__ import annotations

import logging
from typing import Optional

import requests

from ._proxy import requests_proxies  # noqa: F401  (프록시 정책 일관성 — 필요 시 호출측에서 사용)

logger = logging.getLogger(__name__)

SL_API = "https://epl.bag.admin.ch/api/sl/public/medicinal-products"
SL_PAGE = "https://sl.bag.admin.ch/sl"
HEADERS = {
    "accept": "application/json",
    "user-agent": "Mozilla/5.0 (MA-AI-Dossier price check)",
}
PRICE_TOL = 0.006   # 소수 둘째 자리 반올림 오차


def fetch_sl_packs(trademark: str, *, timeout: int = 30, session=None) -> list[dict]:
    """상표 검색 결과를 팩 단위로 평탄화. 실패 시 빈 리스트(예외 전파 안 함)."""
    getter = session or requests
    try:
        r = getter.get(
            SL_API,
            params={"search": trademark, "page": 1, "size": 25},
            headers=HEADERS,
            timeout=timeout,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:  # 네트워크·형식 오류 모두 '조회 실패' 로 취급
        logger.warning("[CH-SL] 조회 실패 %s: %s", trademark, str(e)[:100])
        return []
    return flatten_packs(data)


def flatten_packs(data: dict) -> list[dict]:
    out = []
    for item in (data or {}).get("items", []) or []:
        for mp in item.get("medicinalProducts", []) or []:
            for p in mp.get("packagedMedicinalProducts", []) or []:
                out.append({
                    "trademark": item.get("trademark") or mp.get("trademark"),
                    "strength": mp.get("dosageFormAndStrength"),
                    "pack_size": p.get("packSize"),
                    "ex_factory_price": p.get("exFactoryPrice"),
                    "retail_price": p.get("retailPrice"),
                    "gtin": p.get("gtin"),
                    "last_price_change": p.get("lastPriceChange"),
                    "valid_from": p.get("validFrom"),
                    "valid_to": p.get("validTo"),
                })
    return out


def pack_unit_count(pack_size: Optional[str]) -> Optional[int]:
    """'2 Stk' / 'Durchstf 1 Stk' / 'Btl 30 Stk' → 팩 내 최소단위 수. 읽을 수 없으면 None."""
    import re
    m = re.search(r"(\d+)\s*Stk", pack_size or "")
    return int(m.group(1)) if m else None


def match_pack(packs: list[dict], retail_price: float, *, strength: Optional[str] = None) -> Optional[dict]:
    """저장된 판매가와 일치하는 팩 하나를 고른다. 규격 문자열이 주어지면 앞부분 일치로 후보를 좁힌다.
    후보가 0개거나 서로 다른 FAP 를 가진 2개 이상이면 None(모호 — 채우지 않는다)."""
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
    fap = {round(float(p["ex_factory_price"]), 2) for p in cands}
    if len(fap) != 1:
        return None
    return cands[0]


def sl_search_url(trademark: str) -> str:
    from urllib.parse import quote
    return f"{SL_PAGE}?search={quote(trademark)}&page=1&size=25"
