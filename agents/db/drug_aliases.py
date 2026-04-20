"""Brand ↔ molecule alias map for foreign price canonicalization.

국내·해외 검색에서 welireg/belzutifan, keytruda/pembrolizumab 등이
동일 레코드로 집계되도록 canonical key (molecule 기준) 를 제공한다.

신규 약제 추가 시 BRAND_TO_MOLECULE 한 줄만 추가하면 된다.
"""
from __future__ import annotations

# 브랜드(소문자) → molecule(소문자). 새 제품 추가 시 여기만 확장.
BRAND_TO_MOLECULE: dict[str, str] = {
    "keytruda": "pembrolizumab",
    "welireg": "belzutifan",
    "opdivo": "nivolumab",
    "obdivo": "nivolumab",   # 오타/구표기 포용
    "lynparza": "olaparib",
    "lenvima": "lenvatinib",
    "januvia": "sitagliptin",
    "atozet": "ezetimibe_rosuvastatin",
    "repatha": "evolocumab",
    "aflibercept": "aflibercept",
}


def canonical(name: str) -> str:
    """입력을 canonical molecule key 로 변환. 알 수 없으면 소문자/strip 만 반환."""
    if not name:
        return ""
    key = name.strip().lower()
    return BRAND_TO_MOLECULE.get(key, key)


def aliases(name: str) -> list[str]:
    """canonical 과 동일 canonical 을 가진 모든 이름(브랜드 + molecule) 반환."""
    canon = canonical(name)
    out = {canon}
    for brand, mol in BRAND_TO_MOLECULE.items():
        if mol == canon:
            out.add(brand)
    return sorted(out)


def display_name(canon_or_name: str) -> str:
    """canonical molecule key → 표시용 이름. brand 가 있으면 brand 를 우선."""
    canon = canonical(canon_or_name)
    for brand, mol in BRAND_TO_MOLECULE.items():
        if mol == canon:
            return brand
    return canon
