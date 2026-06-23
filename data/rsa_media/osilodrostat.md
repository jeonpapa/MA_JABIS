---
report_ids: [1227]
brand: 이스투리사필름코팅정
ingredient: osilodrostat (오실로드로스타트)
listing_date: 2025-12-01
window: 2025-10-02 ~ 2026-01-30
found: true
---

```json
{
  "found": true,
  "report_ids": [1227],
  "brand": "이스투리사필름코팅정",
  "ingredient": "osilodrostat",
  "listing_date": "2025-12-01",
  "window_from": "2025-10-02",
  "window_to": "2026-01-30",
  "rsa_types": ["refund", "expenditure_cap"],
  "rsa_type_primary": "refund",
  "conditions": [
    "위험분담제 환급형 + 총액제한형 2개 유형이 적용된 가격으로 등재",
    "급여 적용일 2025-12-01 (레코르다티코리아)",
    "약가: 5mg(1.431mg)/1정 34,492원, 7.155mg/1정 131,467원",
    "최초 투여 전 4주 이내 측정한 평균 24시간 소변 유리코티솔(mUFC)이 정상 상한선(ULN)의 1.3배 초과 환자에 한정"
  ],
  "monitoring": {
    "duration_months": null,
    "metrics": ["mUFC (평균 24시간 소변 유리코티솔)", "ULN 대비 배수"],
    "review": "최초 투여 전 4주 이내 mUFC > 1.3×ULN 충족 여부로 급여 개시 판단 (윈도우 내 보도상 사후 재평가 주기 미명시)"
  },
  "patient_restrictions": {
    "indication": "뇌하수체 수술이 어렵거나 수술 후에도 충분한 효과를 보지 못한 성인 쿠싱병 환자 (mUFC > 1.3×ULN)",
    "line": "수술 불가 또는 수술 후 비관해(2차적 약물치료)"
  },
  "confidence": "high",
  "sources": [
    {"title": "칼슘·비타민D 복합제 무더기 신규 등재...이스투리사, RSA로", "url": "https://www.newsthevoice.com/news/articleView.html?idxno=44191", "media": "뉴스더보이스헬스케어", "date": "2025-11-24"},
    {"title": "레코르다티코리아, 이스투리사® 건강보험 급여 적용 확정", "url": "https://www.mdtoday.co.kr/news/view/1065586434471429", "media": "메디컬투데이", "date": "2025-11-27"},
    {"title": "상급종병 12월 등재 약제, 내분비·당뇨·신경계 치료제 비중 확대", "url": "https://www.kpanews.co.kr/news/articleView.html?idxno=266624", "media": "약사공론", "date": "2025-12-29"}
  ]
}
```

## 한국어 요약

**성분(INN) 확인**: 이스투리사필름코팅정의 성분은 웹 확인 결과 **osilodrostat(오실로드로스타트)** 으로 확정. 11β-hydroxylase(CYP11B1) 억제제 계열의 경구 코르티솔 생합성 억제제이며, 레코르다티코리아가 국내 판매(국내 유일 쿠싱병 치료제, 2024년 11월 허가).

**급여 등재 시점**: 앵커와 일치하는 **2025년 12월 1일** 건강보험 급여 적용. 윈도우(2025-10-02~2026-01-30) 내 보도(2025-11-24, 11-27, 12-29)로 모두 교차 확인.

**RSA 유형**: **위험분담제 환급형(refund) + 총액제한형(expenditure cap)** 2개 유형이 동시 적용된 가격으로 등재됨(뉴스더보이스, 2025-11-24). 희귀질환(쿠싱병) 단일 치료제 특성상 1차 기전을 환급형으로 판단. 약가는 5mg(1.431mg)/정 34,492원, 7.155mg/정 131,467원.

**환자/적응증 제한**: 뇌하수체 수술이 어렵거나 수술 후에도 충분히 조절되지 않은 **성인 쿠싱병** 환자 중, **최초 투여 전 4주 이내 측정 평균 24시간 소변 유리코티솔(mUFC)이 정상 상한선(ULN)의 1.3배를 초과**하는 경우로 급여 개시 제한(메디컬투데이, 2025-11-27).

**모니터링**: 보도 기준 핵심 지표는 mUFC(ULN 대비 배수). 윈도우 내 보도에서는 급여 개시 판단 기준(mUFC>1.3×ULN)만 명시되고, 사후 재평가 주기/기간(duration_months)은 명확히 제시되지 않아 null 처리.

**신뢰도**: high — RSA 2개 유형·등재일·환자 제한이 윈도우 내 복수 매체로 일관 확인됨.
