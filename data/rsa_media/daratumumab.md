---
report_ids: [611]
brand: 다잘렉스주
ingredient: daratumumab
listing_date: 2019-05-01
window: 2019-03-02 ~ 2019-06-30
found: true
---

```json
{
  "found": true,
  "report_ids": [611],
  "brand": "다잘렉스주",
  "ingredient": "daratumumab",
  "listing_date": "2019-05-01",
  "window_from": "2019-03-02",
  "window_to": "2019-06-30",
  "rsa_types": ["refund", "expenditure_cap"],
  "rsa_type_primary": "combined",
  "conditions": [
    "환급형(refund) 위험분담: 제약사가 청구금액의 일정 비율을 국민건강보험공단에 환급",
    "총액제한형(expenditure_cap) 위험분담: 연간 총 청구액 상한 설정, 초과분 제약사 부담",
    "보험상한액 0.1g/5mL = 391,653원, 0.4g/20mL = 1,566,612원 (등재 시점 기준)",
    "건강보험정책심의위원회(건정심) 통과 후 2019-04-08 급여 적용 (윈도우 내)"
  ],
  "monitoring": {
    "duration_months": null,
    "metrics": ["청구액/사용량(총액제한 정산)", "환급 정산"],
    "review": "위험분담 계약기간 종료 시 재계약·재평가 (구체 기간 매체 비공개)"
  },
  "patient_restrictions": {
    "indication": "재발성 또는 불응성 다발골수종 (삼중 불응성, proteasome inhibitor + 면역조절제 등 3종 이상 치료 경험)",
    "line": "4차 이상 단독요법 (monotherapy)"
  },
  "confidence": "high",
  "sources": [
    {"title": "스핀라자·다잘렉스 건정심 통과…8일 급여적용", "url": "https://www.doctorsnews.co.kr/news/articleView.html?idxno=128513", "media": "의협신문", "date": "2019-04-03"},
    {"title": "다잘렉스®, 삼중 불응성 다발골수종 단독요법 치료제로 급여 승인", "url": "http://www.mdon.co.kr/news/article.html?no=21112", "media": "메디포뉴스/MDON", "date": "2019-04"},
    {"title": "RSA로 급여되는 다잘렉스, 사용범위 확대 본 관문 통과", "url": "http://www.kpanews.co.kr/news/articleView.html?idxno=259273", "media": "약사공론", "date": "2019"}
  ]
}
```

## 한국어 요약

**다잘렉스주(성분 daratumumab, 한국얀센)** 는 2019년 다발골수종 치료제로 최초 급여 등재되었으며, 등재 시점의 보험은 **위험분담제(RSA)** 를 조건으로 적용되었다.

- **RSA 유형**: 환급형(refund) + 총액제한형(expenditure_cap) 결합 — 주 유형은 `combined`. 제약사가 청구금액의 일정 비율을 건보공단에 환급하고, 동시에 연간 총 청구액 상한을 두어 초과분을 제약사가 부담하는 구조.
- **절차/시점**: 약가협상을 거쳐 건강보험정책심의위원회(건정심)를 통과, **2019-04-08 급여 적용**(앵커 2019-05-01, 윈도우 2019-03-02~2019-06-30 내). 동일 차수에 스핀라자와 함께 RSA 계약으로 통과.
- **보험상한액(등재 시점)**: 0.1g/5mL = 391,653원, 0.4g/20mL = 1,566,612원.
- **환자/적응증 제한**: 신약 공개 정보 기준, 최초 급여는 **재발·불응성 다발골수종(삼중 불응성, PI·면역조절제 등 3종 이상 치료 경험)** 환자의 **4차 이상 단독요법**에 한정. 병용·1차 등 확대 적응증은 이 시점에 급여 미적용(이후 2020~2021년 확대).
- **모니터링/사후관리**: 총액제한 정산 및 환급 정산이 핵심 사후관리 메커니즘. 단, 계약 기간(개월 수)·세부 정산 지표는 매체에 비공개되어 추정하지 않음(found=true, duration_months=null).

비고: 일부 검색 결과에 등장한 "2019-05-01 / 5월1일부터"는 2021년 병용요법 급여 확대건이며, 본 보고서의 2019년 최초 단독요법 등재(급여적용 4-08)와 구분하였다. 앵커일(2019-05-01)은 윈도우 내 4-08 등재 이벤트로 매칭됨.
