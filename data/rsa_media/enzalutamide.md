---
report_ids: [1113]
brand: 엑스탄디연질캡슐
ingredient: enzalutamide (엔잘루타마이드)
listing_date: 2014-12-01
window: 2014-10-02 ~ 2015-01-30
found: true
---

```json
{
  "found": true,
  "report_ids": [1113],
  "brand": "엑스탄디연질캡슐",
  "ingredient": "enzalutamide (엔잘루타마이드)",
  "listing_date": "2014-12-01",
  "window_from": "2014-10-02",
  "window_to": "2015-01-30",
  "rsa_types": ["refund"],
  "rsa_type_primary": "refund",
  "conditions": [
    "환급형(refund) 위험분담계약: 제약사가 청구금액의 일정 비율을 건강보험공단에 사후 환급",
    "환급률(rebate rate)은 비공개 — 양 당사자 합의로 미공표 (표시가는 유지, net 가격 비공개)",
    "국내 위험분담제(RSA) 도입 초기 적용 약제 중 하나(환급형 1호 계열)"
  ],
  "monitoring": {
    "duration_months": null,
    "metrics": [],
    "review": "계약 종료 후 재계약 구조(추후 4년 단위 연장 보도). 등재 시점 윈도우 내 구체적 모니터링 지표·재평가 주기는 비공개로 확인 불가"
  },
  "patient_restrictions": {
    "indication": "전이성 거세저항성 전립선암(mCRPC)",
    "line": "도세탁셀(docetaxel) 치료 경험이 있는 환자(2차 치료)"
  },
  "confidence": "high",
  "sources": [
    {
      "title": "전립선암치료제 '엑스탄디' 월 400만원→16만원",
      "url": "https://www.medicaltimes.com/Main/News/NewsView.html?ID=1094867",
      "media": "메디칼타임즈",
      "date": "2015"
    },
    {
      "title": "위험분담제 계약 환급 대상 1개 약제 추가 …총 34개 약제",
      "url": "http://www.kpanews.co.kr/news/articleView.html?idxno=241507",
      "media": "약사공론",
      "date": "비공개(환급형 RSA 약제 목록 보도)"
    },
    {
      "title": "'엑스탄디·키트루다', 사용범위 확대로 내달부터 약가 조정",
      "url": "https://www.docdocdoc.co.kr/news/articleView.html?idxno=2025264",
      "media": "청년의사",
      "date": "2022-07-21"
    },
    {
      "title": "엑스탄디연질캡슐40mg(엔잘루타마이드) 제품 상세정보",
      "url": "https://nedrug.mfds.go.kr/pbp/CCBBB01/getItemDetailCache?cacheSeq=201307883aupdateTs2025-04-16+09:38:32.394547b",
      "media": "식약처 의약품통합정보시스템(MFDS)",
      "date": "2013-06(허가)"
    }
  ]
}
```

## 한국어 요약

**성분(INN) 확인**: 엑스탄디연질캡슐의 실제 성분은 **enzalutamide(엔잘루타마이드)** 로 식약처 의약품통합정보시스템(nedrug.mfds.go.kr) 제품 상세정보에서 직접 확인했다. 40mg 연질캡슐, 1일 160mg(4캡슐) 경구 투여.

**급여 등재 시점 및 적응증**: 엑스탄디는 **2014년 11~12월** 국내 건강보험 급여에 등재되었다(앵커 2014-12-01 = 상한금액 고시 적용일과 정합). 최초 급여 적응증은 **도세탁셀(docetaxel) 치료 경험이 있는 전이성 거세저항성 전립선암(mCRPC) 환자의 2차 치료**로 제한되었다. 급여 적용으로 환자 부담은 월 약 400만원 → 약 16만원 수준으로 크게 감소했다(메디칼타임즈).

**RSA 유형 — 환급형(refund)**: 엑스탄디는 **환급형(refund) 위험분담계약**으로 등재되었다. 제약사(한국아스텔라스)가 청구금액의 일정 비율을 건강보험공단에 사후 환급하는 구조다. 국내 위험분담제 도입 초기 적용 약제 중 하나로, 이후에도 환급형 RSA 약제 목록에 포함되어 운영되었다(약사공론). 최초 등재 시 상한금액은 캡슐당 약 22,210원(청년의사, 후속 보도 기준).

**비공개 항목**: 구체적 **환급률(rebate rate)은 양 당사자 합의로 공표되지 않았다**. RSA 표시가는 유지되나 net 가격은 비공개이며, 윈도우(2014-10-02~2015-01-30) 내 보도에서 환급률·정량 모니터링 지표·재평가 주기는 확인되지 않았다. 따라서 monitoring 필드(duration/metrics)는 비워 두었다(추정·날조 배제).

**confidence: high** — INN, 등재 시점, RSA 환급형 유형, 적응증 제한(mCRPC, docetaxel 후 2차)이 복수 Tier1/일반웹 보도 및 MFDS 공식 출처로 교차 확인됨. 단, 환급률·모니터링 세부는 비공개로 found 불가.
