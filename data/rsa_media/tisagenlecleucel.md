---
report_ids: [1404]
brand: 킴리아주
ingredient: tisagenlecleucel
listing_date: 2022-04-01
window: 2022-02-01 ~ 2022-05-31
found: true
---

```json
{
  "found": true,
  "report_ids": [1404],
  "brand": "킴리아주",
  "ingredient": "tisagenlecleucel",
  "listing_date": "2022-04-01",
  "window_from": "2022-02-01",
  "window_to": "2022-05-31",
  "rsa_types": ["performance_based_refund", "expenditure_cap", "refund"],
  "rsa_type_primary": "performance_based_refund",
  "conditions": [
    "환급형(refund): 약제 청구금액의 일정 비율을 제약사가 건강보험공단에 환급",
    "총액제한형(expenditure cap): 예상 청구액 총액(연간 약 709억원) 초과분 전액을 제약사가 공단에 환급",
    "성과기반 환급(performance-based): DLBCL 환자 대상, 환자단위 치료성과 미달 시 제약사가 추가 환급(임상성과가 ALL 대비 미흡한 DLBCL에 적용)",
    "급여상한금액 1팩당 3억6,003만9,359원(환자당 평생 1회)",
    "투여 시술행위료(의사행위료) 별도 신설 — 세포채취·처리·주입 수가 적용"
  ],
  "monitoring": {
    "duration_months": 12,
    "metrics": ["치료성과(반응/치료혜택 여부)", "환자단위 outcome 추적"],
    "review": "DLBCL 환자 대상 주입 후 6개월·12개월 시점 치료성과 모니터링, 치료혜택 없는 환자에 대해 제약사 환급. 적합 의료기관·전문의 지시하 투여 및 환자관리 의무"
  },
  "patient_restrictions": {
    "indication": "B세포 급성림프성백혈병(ALL) 및 미만성 거대 B세포 림프종(DLBCL), 재발성/불응성. 환자당 평생 1회만 급여 인정, 적합 의료기관·전문의 관리하 투여",
    "line": "ALL 2차/3차, DLBCL 2차 이상(재발성·불응성)"
  },
  "confidence": "high",
  "sources": [
    {"title": "'원샷' 킴리아 약가 3억6003만9359원...4월부터 급여", "url": "https://www.newsthevoice.com/news/articleView.html?idxno=26166", "media": "뉴스더보이스헬스케어", "date": "2022-03-31"},
    {"title": "CAR-T 세포치료제 '킴리아', 1회 투여 3억6000만원", "url": "http://www.docdocdoc.co.kr/news/articleView.html?idxno=2021410", "media": "청년의사", "date": "2022-03-31"},
    {"title": "킴리아 약가 3억 6천만원 등재…의사행위료 신설", "url": "https://www.medicaltimes.com/Mobile/News/NewsView.html?ID=1146515", "media": "메디칼타임즈", "date": "2022-03-31"},
    {"title": "약가 협상 타결된 킴리아 4월 등재 유력…급여 기준 구체화", "url": "https://www.medicaltimes.com/Mobile/News/NewsView.html?ID=1146382", "media": "메디칼타임즈", "date": "2022-03"},
    {"title": "한국노바티스, CAR-T '킴리아' 이달부터 급여적용", "url": "https://www.biospectator.com/news/view/15960", "media": "바이오스펙테이터", "date": "2022-04"}
  ]
}
```

## 한국어 요약

**성분(INN):** tisagenlecleucel (티사젠렉류셀) — 국내 최초로 급여 등재된 CAR-T 세포치료제. 웹 확인 결과 추정값과 일치.

**급여 등재:** 2022년 4월 1일 시행. 1회 투여 급여상한금액 3억6,003만9,359원, 환자당 평생 1회만 인정. 비급여 시 환자부담 약 4억원 → 급여화로 최대 약 598만원으로 경감.

**RSA 구조 (3종 중복 적용):**
1. **환급형(refund)** — 약제 청구금액의 일정 비율을 제약사가 공단에 환급.
2. **총액제한형(expenditure cap)** — 예상 청구액 총액(연간 약 709억원) 초과분 전액을 제약사가 환급.
3. **성과기반 환급(performance-based refund, 핵심)** — 임상성과가 ALL 대비 상대적으로 미흡한 **DLBCL** 적응증에 대해, 환자단위 치료성과 달성 여부에 따라 제약사가 추가 환급. 심평원 암질심 의견을 복지부가 수용한 결과.

**모니터링/사후관리:** DLBCL 환자 대상 주입 후 **6개월·12개월** 시점 치료성과를 모니터링하고, 치료혜택이 없는 환자에 대해 제약사가 환급. 적합 의료기관 및 전문의 지시·관리하에서만 투여 시 급여 인정. CAR-T 시술행위료(세포채취·처리·주입) 수가 별도 신설.

**환자/적응증 제한:** B세포 급성림프성백혈병(ALL, 2차/3차)과 미만성 거대 B세포 림프종(DLBCL, 2차 이상, 재발성·불응성). 환자당 평생 1회.

**신뢰도:** high — Tier1 전문지(메디칼타임즈·청년의사·뉴스더보이스·바이오스펙테이터) 다수가 윈도우(2022-02~05) 내 일관 보도. RSA 3종·성과기반(DLBCL)·6/12개월 모니터링·평생 1회 모두 교차 확인.
