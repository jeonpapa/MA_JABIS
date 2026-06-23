---
report_ids: [675]
brand: 레블리미드캡슐
ingredient: lenalidomide (레날리도마이드)
listing_date: 2014-04-01
window: 2014-01-31 ~ 2014-05-31
found: true
---

```json
{
  "found": true,
  "report_ids": [675],
  "brand": "레블리미드캡슐",
  "ingredient": "lenalidomide",
  "listing_date": "2014-04-01",
  "window_from": "2014-01-31",
  "window_to": "2014-05-31",
  "rsa_types": ["refund"],
  "rsa_type_primary": "refund",
  "conditions": [
    "위험분담제(RSA) 국내 1호 적용 약제 — 환급형(refund) 방식으로 급여 등재",
    "제약사(BMS)가 매출액(청구액) 중 정부와 약정한 할인율만큼을 건강보험공단에 환급하여 표시가는 유지",
    "급여 기준이 허가 적응증보다 엄격하게 제한됨 (벨케이드(bortezomib) 치료 실패 환자로 한정)"
  ],
  "monitoring": {
    "duration_months": null,
    "metrics": ["매출액(청구액) 기반 환급액 정산"],
    "review": "윈도우 내 보도에서 구체적 재평가 주기·기간 명시 없음 (환급형 정산 기반)"
  },
  "patient_restrictions": {
    "indication": "재발성 또는 불응성 다발골수종 (덱사메타손 병용); 급여기준은 벨케이드 치료 실패 환자로 제한",
    "line": "2차 이상 (한 가지 이상 치료 경험 후)"
  },
  "confidence": "high",
  "sources": [
    {
      "title": "레블리미드, '재발-불응 다발골수종 치료'에 급여 적용",
      "url": "https://bktimes.net/detail.php?number=49409&thread=08r07",
      "media": "보건타임즈",
      "date": "2014-03-16"
    },
    {
      "title": "위험분담제 1호 레블리미드 어떤약?",
      "url": "https://www.monews.co.kr/news/articleView.html?idxno=71617",
      "media": "메디칼업저버",
      "date": "2014-03-18"
    }
  ]
}
```

## 한국어 요약

- **성분(INN) 확인**: 레블리미드캡슐의 실제 성분은 **lenalidomide(레날리도마이드)** 로 웹 검증 완료 (식약처 2009-12 허가, BMS).
- **RSA 유형**: **환급형(refund)**. 레블리미드는 국내 **위험분담제(RSA) 1호** 적용 약제로, 제약사가 매출액(청구액) 중 정부와 약정한 할인율만큼을 건강보험공단에 환급하는 방식으로 급여를 유지(표시가 보전).
- **적응증·라인 제한**: 한 가지 이상 치료를 경험한 **재발성·불응성 다발골수종** 환자에 **덱사메타손 병용**(2차 이상). 단, 급여기준은 허가 적응증보다 엄격해 **벨케이드(bortezomib) 치료 실패 환자**로 제한됨.
- **모니터링/사후조건**: 윈도우 내 보도는 매출액 기반 환급 정산을 명시하나, 구체적 재평가 주기·기간(개월)은 보도에 없음 → `null` 처리.
- **윈도우 정합성**: 근거 보도 모두 등재 직전 시점(2014-03-16, 2014-03-18)으로 윈도우(2014-01-31~2014-05-31) 내. 앵커 등재일 2014-04-01 부합.
- **confidence: high** — 복수 Tier1 전문지(보건타임즈·메디칼업저버) + 일반 보도(데일리팜)가 환급형 RSA 1호·적응증·급여 제한을 일관되게 보도.
