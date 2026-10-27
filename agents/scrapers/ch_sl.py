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


# 순수 파서는 공용 모듈 하나만 쓴다(Aside 워커 스크립트와 동일 구현).
from agents.foreign_sources.ch_sl import (  # noqa: E402,F401
    flatten_packs, match_pack, pack_unit_count, sl_search_url, PRICE_TOL,
)
