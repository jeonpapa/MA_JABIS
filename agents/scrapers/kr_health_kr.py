"""약학정보원 (health.kr) 국내 약제 상세정보 스크레이퍼.

http://www.health.kr 의 `/searchDrug/result_drug.asp` 페이지는 브라우저에서 JS 로
`/searchDrug/ajax/ajax_result_drug2.asp?drug_cd=<code>` AJAX 호출 결과를 렌더.
이 모듈은 AJAX 엔드포인트를 직접 호출해 JSON 을 파싱, 다음 필드를 회수:

- `item_permit_date` (YYYYMMDD) → **최초 허가일** (MFDS 공식 데이터)
- `dosage`           (HTML) → **용법·용량** 전문
- `effect`           (HTML) → **효능·효과** 전문
- `list_sunb_name`   → 성분/함량 (예: "Pembrolizumab 100mg/4mL")
- `drug_form`        → 제형 (주사제/정제 등)
- `dosage_route`     → 투여경로 (주사/경구)
- `cls_code`         → 약효분류 (항악성종양제)
- `atc_cd`           → ATC 코드
- `upso_name`        → 제조/수입사
- `boh_history`      → 급여 상한금액 이력
- `boh_hiracode`     → 보험코드 (HIRA insurance_code 와 일치)
- `reexam`           → 재심사대상 여부 + 기간

drug_cd 조회는 두 경로:
1. `DRUG_CD_MAP` 하드코딩 (주요 제품) — 빠름 · 확정적
2. `/searchDrug/ajax/ajax_getDrugName_base.asp?drugnm=<name>` 로 brand 존재 확인 후
   수동 등록 안내 (자동 drug_cd 추출은 JS 기반 검색 페이지 구조로 미구현)

추가 drug_cd 는 health.kr 에서 제품명 검색 → URL 의 `drug_cd=` 파라미터 복사로 획득.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

BASE = "https://www.health.kr"
AJAX_DETAIL = BASE + "/searchDrug/ajax/ajax_result_drug2.asp"
AJAX_NAME_CHECK = BASE + "/searchDrug/ajax/ajax_getDrugName_base.asp"
PAGE_DETAIL = BASE + "/searchDrug/result_drug.asp"

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


# ───────────────────────────────────────────────────────────────────────────
# 제품 slug (영문/소문자) → health.kr drug_cd 매핑
# 확장: health.kr 검색 → 상세 페이지 URL 의 `drug_cd=` 파라미터 복사 후 추가.
# ───────────────────────────────────────────────────────────────────────────
DRUG_CD_MAP: dict[str, str] = {
    # 항암 (검증 완료 — 허가일 매칭 확인)
    "keytruda":           "2015032400029",
    "키트루다주":            "2015032400029",
    # 대사
    "januvia":            "A11APPPPP2858",
    "자누비아정100mg":       "A11APPPPP2858",
    "자누비아정100밀리그램":  "A11APPPPP2858",
    "자누비아정50mg":        "A11APPPPP2857",
    "자누비아정50밀리그램":   "A11APPPPP2857",
    # HIV
    "biktarvy":           "2019012500090",
    "빅타비정":             "2019012500090",
}
# 신규 drug_cd 추가: health.kr 에서 제품명 검색 → 상세 URL 의 `drug_cd=XXX` 복사 후 이 dict 에 등록.
# 확인: `python -m agents.scrapers.kr_health_kr verify <drug_cd>` 로 brand 명/허가일 검증.


@dataclass
class HealthKrRecord:
    """health.kr 상세정보 파싱 결과."""
    drug_cd: str
    drug_name: str           # 한국어 제품명 (예: "키트루다주")
    drug_enm: str            # 영문 제품명 (예: "Keytruda Inj.")
    approval_date: Optional[str]  # YYYY-MM-DD (item_permit_date 8자리 → 포맷)
    ingredient_full: str     # "Pembrolizumab 100mg/4mL"
    dosage_form: str         # "주사제" / "정제"
    dosage_route: str        # "주사" / "경구"
    usage_text: str          # 용법·용량 전문 (HTML tags 제거)
    effect_text: str         # 효능·효과 전문 (HTML tags 제거)
    cls_code: str            # "항악성종양제"
    atc_cd: str              # "L01FF02|PEMBROLIZUMAB|..."
    company: str             # 제조/수입사 (첫 파트만)
    insurance_code: str      # HIRA 보험코드 (boh_hiracode)
    reexam: str              # 재심사 정보 문자열
    raw: dict                # 원본 JSON (전체 필드 보관)

    def to_enrichment_dict(self) -> dict:
        """DrugEnrichmentAgent 가 사용하는 표준 enrichment dict 로 변환.

        dose_schedule / daily_dose_units 는 usage_text 에서 별도 파싱.
        """
        from agents.scrapers.health_kr_dose_parser import parse_dose_schedule
        dose = parse_dose_schedule(self.usage_text, form=self.dosage_form)
        return {
            "approval_date":    self.approval_date or "",
            "usage_text":       self.usage_text[:4000],  # DB 용량 보호
            "daily_dose_units": dose.get("daily_dose_units"),
            "dose_schedule":    dose.get("schedule") or "as_needed",
            "cycle_days":       dose.get("cycle_days"),
            "doses_per_cycle":  dose.get("doses_per_cycle"),
            "sources_json":     [{"url": f"{PAGE_DETAIL}?drug_cd={self.drug_cd}",
                                  "title": self.drug_name,
                                  "media": "약학정보원 (health.kr)"}],
            "confidence":       "high",  # 공식 MFDS 자료원 기반
            "notes":            f"health.kr 자료원 · ATC={self.atc_cd.split('|')[0]}",
        }


class HealthKrScraper:
    """health.kr AJAX 기반 약제 상세정보 스크레이퍼."""

    def __init__(self, timeout: int = 15):
        self.timeout = timeout

    # ── 1) drug_cd 해결 ──────────────────────────────────────────────
    def resolve_drug_cd(self, product_slug: str) -> Optional[str]:
        """product_slug(영문 키) → health.kr drug_cd.

        하드코딩 dict 우선 — 없으면 None. 신규 제품 추가 시 dict 업데이트 필요.
        """
        return DRUG_CD_MAP.get((product_slug or "").strip().lower())

    # ── 2) brand 존재 확인 ──────────────────────────────────────────
    def check_brand_exists(self, brand_name: str) -> Optional[str]:
        """한국어 brand 명 → health.kr 정규 brand 명 반환. 없으면 None.

        `/searchDrug/ajax/ajax_getDrugName_base.asp?drugnm=...` 호출.
        """
        if not brand_name:
            return None
        url = AJAX_NAME_CHECK + "?" + urllib.parse.urlencode({"drugnm": brand_name})
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                        "Referer": PAGE_DETAIL})
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                body = r.read().decode("utf-8", errors="replace")
            arr = json.loads(body)
            if isinstance(arr, list) and arr:
                return arr[0].get("drug_name")
        except Exception as e:
            logger.debug("[health.kr] brand check 실패 %s: %s", brand_name, e)
        return None

    # ── 3) drug_cd 로 상세정보 회수 ─────────────────────────────────
    def fetch(self, drug_cd: str) -> Optional[HealthKrRecord]:
        """drug_cd 로 AJAX JSON 호출 + HealthKrRecord 반환."""
        if not drug_cd:
            return None
        url = AJAX_DETAIL + "?" + urllib.parse.urlencode({"drug_cd": drug_cd})
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": UA,
                    "Referer": f"{PAGE_DETAIL}?drug_cd={drug_cd}",
                },
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                body = r.read().decode("utf-8", errors="replace")
            arr = json.loads(body)
            if not isinstance(arr, list) or not arr:
                logger.warning("[health.kr] %s 응답 empty", drug_cd)
                return None
            data = arr[0]
            return self._build_record(drug_cd, data)
        except Exception as e:
            logger.error("[health.kr] %s fetch 실패: %s", drug_cd, e)
            return None

    # ── 4) enrich 통합 진입점 (slug → drug_cd → record) ────────────
    def enrich(
        self,
        product_slug: str,
        brand_name: str | None = None,
    ) -> Optional[HealthKrRecord]:
        """product_slug (또는 brand_name 보조) 로 enrichment 회수.

        1) DRUG_CD_MAP 조회
        2) 실패 시 brand_name 으로 존재만 확인 (로그 안내)
        """
        cd = self.resolve_drug_cd(product_slug)
        if not cd:
            if brand_name:
                canonical = self.check_brand_exists(brand_name)
                if canonical:
                    logger.warning(
                        "[health.kr] %s 는 DRUG_CD_MAP 미등록. "
                        "health.kr 에서 '%s' 검색 → 상세 URL 의 drug_cd 파라미터를 "
                        "DRUG_CD_MAP 에 추가하세요.",
                        product_slug, canonical,
                    )
            else:
                logger.warning("[health.kr] %s DRUG_CD_MAP 미등록", product_slug)
            return None
        return self.fetch(cd)

    # ── 내부: JSON → HealthKrRecord ─────────────────────────────────
    @staticmethod
    def _build_record(drug_cd: str, data: dict) -> HealthKrRecord:
        # item_permit_date: "20150320" → "2015-03-20"
        raw_date = (data.get("item_permit_date") or "").strip()
        approval_date = None
        if len(raw_date) == 8 and raw_date.isdigit():
            approval_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"

        return HealthKrRecord(
            drug_cd=drug_cd,
            drug_name=(data.get("drug_name") or "").strip(),
            drug_enm=(data.get("drug_enm") or "").strip(),
            approval_date=approval_date,
            ingredient_full=(data.get("list_sunb_name") or "").strip(),
            dosage_form=(data.get("drug_form") or "").strip(),
            dosage_route=(data.get("dosage_route") or "").strip(),
            usage_text=_strip_html(data.get("dosage") or ""),
            effect_text=_strip_html(data.get("effect") or ""),
            cls_code=(data.get("cls_code") or "").strip(),
            atc_cd=(data.get("atc_cd") or "").strip(),
            company=((data.get("upso_name") or "").split("|")[0]).strip(),
            insurance_code=(data.get("boh_hiracode") or "").strip(),
            reexam=(data.get("reexam") or "").strip(),
            raw=data,
        )


def _strip_html(s: str) -> str:
    """health.kr `dosage`/`effect` 필드는 "brbr" (tag 없이 br literal), <P></P>, &#xNN; 혼재."""
    if not s:
        return ""
    # health.kr 에서 "brbr" 이 line break 대용으로 들어가는 경우가 많음
    s = re.sub(r"br\s*br|<br\s*/?>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<[^>]+>", "\n", s)
    s = re.sub(r"&nbsp;", " ", s)
    s = re.sub(r"&#x([0-9a-fA-F]+);",
               lambda m: chr(int(m.group(1), 16)), s)
    s = re.sub(r"&#(\d+);",
               lambda m: chr(int(m.group(1))), s)
    s = re.sub(r"\s*\r?\n\s*", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


# ── CLI ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse, sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    ap = argparse.ArgumentParser(description="약학정보원 스크레이퍼 테스트 도구")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pv = sub.add_parser("verify", help="drug_cd 검증 — 브랜드명 + 허가일 확인")
    pv.add_argument("drug_cd")

    pe = sub.add_parser("enrich", help="slug 로 enrichment dict 출력")
    pe.add_argument("product_slug")
    pe.add_argument("--brand", default=None)

    pc = sub.add_parser("check", help="brand 명이 health.kr 에 있는지 확인")
    pc.add_argument("brand_name")

    args = ap.parse_args()
    scraper = HealthKrScraper()

    if args.cmd == "verify":
        rec = scraper.fetch(args.drug_cd)
        if not rec:
            print(f"FAIL: {args.drug_cd} 응답 없음")
            sys.exit(1)
        print(f"drug_cd:       {rec.drug_cd}")
        print(f"제품명:        {rec.drug_name} ({rec.drug_enm})")
        print(f"허가일:        {rec.approval_date}")
        print(f"성분/함량:     {rec.ingredient_full}")
        print(f"제형:          {rec.dosage_form} ({rec.dosage_route})")
        print(f"약효분류:      {rec.cls_code} / {rec.atc_cd}")
        print(f"제조사:        {rec.company}")
        print(f"보험코드:      {rec.insurance_code}")
        print(f"재심사:        {rec.reexam}")
        print(f"용법용량(앞 300자): {rec.usage_text[:300]}")

    elif args.cmd == "enrich":
        rec = scraper.enrich(args.product_slug, brand_name=args.brand)
        if not rec:
            print("FAIL: enrichment 회수 실패")
            sys.exit(1)
        import json as _j
        print(_j.dumps(rec.to_enrichment_dict(), ensure_ascii=False, indent=2))

    elif args.cmd == "check":
        name = scraper.check_brand_exists(args.brand_name)
        print(f"canonical: {name}" if name else "NOT FOUND")
