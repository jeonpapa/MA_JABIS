"""Cross-national reimbursement sync orchestrator.

Phase 6 의 분기 cron 이 호출하는 진입점. 각 product_slug 에 대해 4개 스크레이퍼
(NICE / PBS / CMS / 일본 中医協) 를 호출하고 결과를 reimbursement_xnational 에 저장.

indication_id 매칭 전략:
  - scraper 결과의 criteria_text 또는 title 을 anchor 로
    `find_matching_indication()` 호출 시도
  - 실패 시 product 단위 (indication_id=None) row 로 저장
  - product 의 첫 번째 indication_id 를 fallback 으로 attach (UI 표시용)

API 키 불필요 — 모두 공개 사이트.
"""
from __future__ import annotations

import logging
from pathlib import Path

from agents.db import DrugPriceDB
from agents.hta_scrapers.uk_nice import UKNICEScraper
from agents.hta_scrapers.au_pbs import AUPBSScraper
from agents.hta_scrapers.us_cms import USCMSScraper
from agents.hta_scrapers.jp_chuikyo import JPChuikyoScraper

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[2]


SCRAPERS = [
    ("UK", "NICE", UKNICEScraper),
    ("AU", "PBAC", AUPBSScraper),
    ("US", "CMS",  USCMSScraper),
    ("JP", "CHUIKYO", JPChuikyoScraper),
]


def _attach_indication_id(db: DrugPriceDB, product_slug: str, criteria: str | None) -> str | None:
    """criteria_text 안에 적응증 키워드가 있으면 매칭, 없으면 product 의 첫 indication.

    NICE TA 등 권고가 적응증별로 분리되어 있지 않은 경우 product 단위 row 가 정상.
    UI 는 indication 매트릭스가 아니라 국가 카드라 indication_id=None 도 OK.
    """
    if not criteria:
        return _first_indication(db, product_slug)

    # 단순 키워드 매칭 — disease/biomarker 키워드가 criteria 에 있는지
    text = criteria.lower()
    indications = db.get_indications(product_slug)
    for ind in indications:
        disease = (ind.get("disease") or "").lower()
        if disease and len(disease) > 3 and disease in text:
            return ind["indication_id"]
    return _first_indication(db, product_slug)


def _first_indication(db: DrugPriceDB, product_slug: str) -> str | None:
    indications = db.get_indications(product_slug)
    return indications[0]["indication_id"] if indications else None


def sync_for_product(product_slug: str, db: DrugPriceDB | None = None) -> dict:
    """단일 product 에 대해 4 scraper 실행 + DB 저장.

    Returns: {country/body: row_count, ...}
    """
    db = db or DrugPriceDB(BASE_DIR / "data" / "db" / "drug_prices.db")

    # alias_map 에서 INN 우선 (NICE/CMS 영문명, PBS 영문명 검색에 적합)
    alias = db.get_product_alias(product_slug)
    drug_name = (alias or {}).get("inn") or product_slug

    counts: dict[str, int] = {}
    for country, body, ScraperCls in SCRAPERS:
        key = f"{country}/{body}"
        try:
            scraper = ScraperCls()
            results = scraper.search_reimbursement(drug_name)
        except Exception as e:
            logger.exception("[%s] scraper 실패 (%s): %s", key, drug_name, e)
            counts[key] = 0
            continue

        n = 0
        for r in results:
            r.indication_id = _attach_indication_id(db, product_slug, r.criteria_text)
            if not r.indication_id:
                logger.info("[%s] %s: indication_id 매칭 실패 → 스킵", key, product_slug)
                continue
            try:
                db.save_xnational_reimbursement(r.to_db_record())
                n += 1
            except Exception as e:
                logger.exception("[%s] save 실패: %s", key, e)
        counts[key] = n
        logger.info("[%s] %s: %d rows 저장", key, drug_name, n)
    return counts


def sync_all(db: DrugPriceDB | None = None) -> dict:
    """indications_master 의 모든 product slug 대상 sync.
    Phase 6 cron 진입점.
    """
    db = db or DrugPriceDB(BASE_DIR / "data" / "db" / "drug_prices.db")
    with db._connect() as conn:
        slugs = [r[0] for r in conn.execute(
            "SELECT DISTINCT product FROM indications_master ORDER BY product"
        ).fetchall()]

    summary: dict[str, dict] = {}
    for slug in slugs:
        logger.info("[sync_all] %s 시작", slug)
        summary[slug] = sync_for_product(slug, db=db)
    return summary


if __name__ == "__main__":
    import argparse, json
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--product", default=None,
                    help="단일 product slug (생략 시 indications_master 전체)")
    args = ap.parse_args()

    if args.product:
        out = sync_for_product(args.product)
    else:
        out = sync_all()
    print(json.dumps(out, ensure_ascii=False, indent=2))
