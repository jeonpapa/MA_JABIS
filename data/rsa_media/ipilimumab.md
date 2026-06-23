---
report_ids: [1136]
brand: 여보이주
ingredient: ipilimumab
listing_date: 2021-10-01
window: 2021-08-02 ~ 2021-11-30
found: true
---

```json
{
  "found": true,
  "report_ids": [1136],
  "brand": "여보이주",
  "ingredient": "ipilimumab",
  "listing_date": "2021-10-01",
  "window_from": "2021-08-02",
  "window_to": "2021-11-30",
  "rsa_types": ["refund", "expenditure_cap"],
  "rsa_type_primary": "combined",
  "conditions": [
    "약제 청구금액의 일정 비율을 제약사(한국BMS)가 국민건강보험공단에 환급 (환급형)",
    "실제 청구액이 사전 설정한 연간 예상 청구액 총액을 초과 시 초과분의 일정 비율을 환급 (총액제한형)",
    "예상청구액(연간) 87억원으로 합의",
    "급여 상한금액: 50mg 3,501,628원 / 200mg 14,006,513원"
  ],
  "monitoring": {
    "duration_months": null,
    "metrics": ["연간 청구액 총액 대비 실제 청구액 (예상청구액 87억원 기준 초과분 추적)"],
    "review": "예상청구액 총액 초과 여부에 따른 환급 정산 (계약상 정산주기는 매체 비공개)"
  },
  "patient_restrictions": {
    "indication": "재발성·전이성(진행성) 신세포암, 중간 또는 고위험군(IMDC 중등도/고위험), 투명세포암",
    "line": "1차 치료, 옵디보(니볼루맙)와 병용요법"
  },
  "confidence": "high",
  "sources": [
    {
      "title": "'여보이' 9월부터 보험 적용...'옵디보' 두경부암 급여 확대",
      "url": "https://www.doctorsnews.co.kr/news/articleView.html?idxno=140776",
      "media": "의협신문",
      "date": "2021-08-27"
    },
    {
      "title": "신세포암 치료제 '여보이' 200mg 1400만원 급여신설",
      "url": "http://www.bosa.co.kr/news/articleView.html?idxno=2157315",
      "media": "의학신문",
      "date": "2021-08-27"
    },
    {
      "title": "면역항암제 옵디보‧여보이 급여 확대…학회들도 환영",
      "url": "https://www.medicaltimes.com/Main/News/NewsView.html?ID=1142571",
      "media": "메디칼타임즈",
      "date": "2021-08-27"
    },
    {
      "title": "면역항암제 '여보이', RSA 환급대상 약제 명단에 추가",
      "url": "https://mdtoday.co.kr/news/view/179585081656300",
      "media": "메디컬투데이",
      "date": "2021-08~2021-09"
    }
  ]
}
```

## 한국어 요약

여보이주(ipilimumab, 한국BMS)는 신약으로 2021-09(고시 기준, 적용 2021-09-01)부터 건강보험 급여권에 **최초 진입**했다. 급여 등재 시점에 **위험분담제(RSA) 환급형 + 총액제한형 결합(combined)** 조건이 적용되었다.

- **RSA 유형**: 환급형(refund) + 총액제한형(expenditure_cap) 결합. 제약사가 약제 청구금액의 일정 비율을 건보공단에 환급하고, 실제 청구액이 사전 설정한 **연간 예상 청구액 총액(87억원)**을 초과할 경우 초과분의 일정 비율을 추가 환급한다.
- **적응증·환자 제한**: 재발성·전이성(진행성) **신세포암** 중 **IMDC 중등도 또는 고위험군**, **투명세포암** 환자 대상. **1차 치료**로 **옵디보(니볼루맙)와 병용요법**인 경우에 한정.
- **급여 상한금액**: 50mg 3,501,628원 / 200mg 14,006,513원 (200mg 신설).
- **모니터링**: 연간 예상청구액(87억원) 총액 초과 여부를 기준으로 환급 정산. 구체적 정산주기·계약기간(개월)은 매체에 비공개.

근거는 윈도우(2021-08-02~2021-11-30) 내 2021-08-27 보도 3건(의협신문·의학신문·메디칼타임즈) 및 RSA 환급대상 명단 추가 보도(메디컬투데이)로 교차 확인되었다. 표시가 기준 정보이며 실제 환급 net 가격·정산비율은 비공개(RSA invisible pricing). confidence: high.
