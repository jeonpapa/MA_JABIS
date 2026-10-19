"""식약처 '최초 허가일' 선택 — 같은 제품군 품목 중 가장 이른 허가일.

기존 lookup_permit 은 API 가 돌려준 품목 중 '정상' 상태인 **첫 번째**를 쓴다. 함량별 품목이 여러 개이고 허가일이 다르면
(또는 원 허가가 취하·유효기간만료된 뒤 재허가된 경우) 엉뚱한 날짜가 잡힌다. 실측(2026-10-19):
  네시나정 — 2013-05-31(취하·만료 3품목) 과 2023-10-30(정상 2품목). 기존 로직 결과 2023-10-30 → 10년 늦다.
  그러면 enrich_reimbursement_date 가 '약가 등재(2014-01-01)가 허가보다 앞선다' 며 최초 등재일까지 버렸다.
analog 의 '허가일' 은 약제가 처음 허가된 날이므로, 같은 제품군(이름 앞부분이 같은) 품목의 최소 허가일을 쓴다.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional


def _norm_name(s: str | None) -> str:
    """비교용 이름: NFC, 괄호 뒤(성분·함량) 제거, 공백·구두점 제거, 소문자."""
    s = unicodedata.normalize("NFC", s or "")
    s = s.split("(")[0]
    return re.sub(r"[\s\-_.,·]+", "", s).lower()


def _base_brand(brand: str) -> str:
    """브랜드에서 함량 꼬리(숫자부터) 제거 — '뉴프로패취 1, 2, 3mg' → '뉴프로패취'."""
    b = _norm_name(brand)
    m = re.search(r"\d", b)
    return b[:m.start()] if m and m.start() >= 2 else b


def _fmt(yyyymmdd: str | None) -> Optional[str]:
    d = re.sub(r"\D", "", yyyymmdd or "")
    return f"{d[:4]}.{d[4:6]}.{d[6:8]}" if len(d) == 8 else None


def earliest_permit(items: list[dict], brand: str) -> Optional[dict]:
    """API 원시 품목 목록 → 최초 허가일 정보. 같은 제품군 품목만 대상(다른 제품이 섞인 성분·부분일치 조회 결과 배제).

    반환: {permit_date:'YYYY.MM.DD', n_items, item_name, cancel_status, latest_permit_date} 또는 None.
    """
    base = _base_brand(brand)
    if not base:
        return None
    fam = [it for it in (items or []) if _norm_name(it.get("ITEM_NAME")).startswith(base) and _fmt(it.get("ITEM_PERMIT_DATE"))]
    if not fam:
        return None
    fam.sort(key=lambda it: re.sub(r"\D", "", it["ITEM_PERMIT_DATE"]))
    first, last = fam[0], fam[-1]
    return {"permit_date": _fmt(first["ITEM_PERMIT_DATE"]), "n_items": len(fam),
            "item_name": first.get("ITEM_NAME"), "cancel_status": (first.get("CANCEL_NAME") or "").strip() or None,
            "latest_permit_date": _fmt(last["ITEM_PERMIT_DATE"])}


def lookup_first_permit(brand: str, *, fetch=None) -> Optional[dict]:
    """브랜드명(변형 포함)으로 API 조회 → earliest_permit. fetch 는 테스트용 주입(item_name=, num_of_rows=)."""
    if fetch is None:
        from agents.scrapers.kr_mfds_permit import fetch_raw as fetch, _name_variants
    else:
        def _name_variants(n):  # noqa: E306 — 테스트 주입 시 단일 이름만
            return [n]
    for variant in _name_variants(brand):
        items = fetch(item_name=variant, num_of_rows=50)
        res = earliest_permit(items, brand)
        if res:
            return res
    return None
