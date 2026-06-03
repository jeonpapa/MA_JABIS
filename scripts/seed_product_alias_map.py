"""product_alias_map 시드 — brand ↔ INN ↔ 국가별 표기 정규화.

Phase 2 작업 (pure-napping-goose plan).

기존 indications_master 에 등록된 모든 product slug + plan 의 7약 (keytruda /
welireg / lynparza / lenvima / januvia / gardasil / prevymis) 을 hand-curated 로 적재.

실행:
  python -m scripts.seed_product_alias_map
"""
from __future__ import annotations

from pathlib import Path

from agents.db import DrugPriceDB
from agents.db.drug_aliases import invalidate_db_cache

BASE_DIR = Path(__file__).resolve().parents[1]


# (product_slug, inn, brand_aliases, agency_brand_overrides)
# brand_aliases: 검색 매칭에 쓰일 모든 표기 (영문/한글/구표기/연구코드 포함)
# agency_brand_overrides: agency 별 권위 표기 (NICE/PMDA/CMS/PBAC 검색 시 우선)
SEED: list[tuple[str, str | None, list[str], dict[str, str]]] = [
    (
        "keytruda", "pembrolizumab",
        ["Keytruda", "키트루다", "키트루다주", "MK-3475", "펨브롤리주맙", "pembrolizumab"],
        {"EMA": "Keytruda", "PMDA": "キイトルーダ", "MFDS": "키트루다"},
    ),
    (
        "welireg", "belzutifan",
        ["Welireg", "웰리렉", "웰리렉정", "MK-6482", "PT2977", "belzutifan", "벨주티판"],
        {"EMA": "Welireg", "PMDA": "ウェリレグ"},
    ),
    (
        "lynparza", "olaparib",
        ["Lynparza", "린파자", "린파자정", "AZD2281", "olaparib", "올라파립"],
        {"EMA": "Lynparza", "PMDA": "リムパーザ"},
    ),
    (
        "lenvima", "lenvatinib",
        ["Lenvima", "렌비마", "렌비마캡슐", "E7080", "lenvatinib", "렌바티닙", "Kisplyx"],
        {"EMA": "Lenvima", "PMDA": "レンビマ"},
    ),
    (
        "januvia", "sitagliptin",
        ["Januvia", "자누비아", "자누비아정", "MK-0431", "sitagliptin", "시타글립틴",
         "Tesavel", "Xelevia"],
        {"EMA": "Januvia", "PMDA": "ジャヌビア"},
    ),
    (
        "gardasil", "human_papillomavirus_vaccine",
        ["Gardasil", "Gardasil 9", "가다실", "가다실9", "HPV vaccine",
         "9vHPV", "Silgard"],
        {"EMA": "Gardasil 9", "PMDA": "ガーダシル"},
    ),
    (
        "prevymis", "letermovir",
        ["Prevymis", "프레비미스", "프리비미스", "MK-8228", "letermovir", "레터모비르"],
        {"EMA": "Prevymis", "PMDA": "プレバイミス"},
    ),
    (
        "opdivo", "nivolumab",
        ["Opdivo", "옵디보", "옵디보주", "BMS-936558", "nivolumab", "니볼루맙"],
        {"EMA": "Opdivo", "PMDA": "オプジーボ"},
    ),
    (
        "atozet", "ezetimibe_rosuvastatin",
        ["Atozet", "아토젯", "ezetimibe", "rosuvastatin",
         "에제티미브", "로수바스타틴"],
        {"EMA": "Atozet"},
    ),
    (
        "repatha", "evolocumab",
        ["Repatha", "레파타", "evolocumab", "에볼로쿠맙", "AMG-145"],
        {"EMA": "Repatha", "PMDA": "レパーサ"},
    ),
]


def main() -> None:
    db = DrugPriceDB(BASE_DIR / "data" / "db" / "drug_prices.db")
    n = 0
    for slug, inn, brand_aliases, overrides in SEED:
        db.upsert_product_alias(slug, inn, brand_aliases, overrides)
        n += 1
        print(f"  ✓ {slug:12s} | inn={inn:30s} | aliases={len(brand_aliases)}")
    invalidate_db_cache()
    print(f"\n총 {n}건 업서트.")

    # 검증: 각 slug 의 aliases() 출력
    from agents.db.drug_aliases import aliases as a
    print("\n=== aliases() 검증 ===")
    for slug, _, _, _ in SEED[:3]:
        print(f"  {slug}: {a(slug)}")


if __name__ == "__main__":
    main()
