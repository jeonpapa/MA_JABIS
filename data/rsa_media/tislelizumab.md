---
report_ids: [1431]
brand: 테빔브라주
ingredient: tislelizumab
listing_date: 2025-04-01
window: 2025-01-31 ~ 2025-05-31
found: true
---

```json
{
  "found": true,
  "report_ids": [1431],
  "brand": "테빔브라주",
  "ingredient": "tislelizumab",
  "listing_date": "2025-04-01",
  "window_from": "2025-01-31",
  "window_to": "2025-05-31",
  "rsa_types": ["refund", "expenditure_cap"],
  "rsa_type_primary": "combined",
  "conditions": [
    "환급형(refund) + 총액제한형(expenditure_cap) RSA 계약 체결",
    "상한금액 병당 1,206,000원으로 등재",
    "신약 위험분담 환급대상 약제로 등재 (위험분담 환급대상 품목 리스트 포함)",
    "식도암·위암·비소세포폐암 등 5개 적응증에서 RSA를 조건으로 급여 적정성 인정",
    "2023.11 식약처 허가 이후 두 번째 도전 만에 급여 등재 성공"
  ],
  "monitoring": {
    "duration_months": null,
    "metrics": ["total_expenditure_cap"],
    "review": "총액제한형 계약에 따른 사용금액 상한 관리. 구체적 모니터링 기간·지표는 윈도우 내 매체에서 비공개"
  },
  "patient_restrictions": {
    "indication": "절제 불가능, 재발성, 국소진행성 또는 전이성 식도편평세포암(ESCC) — 이전 백금 기반 화학요법 치료 도중 또는 이후 재발/진행. 선행화학요법 또는 수술 후 보조요법 종료 후 6개월 이내 재발 환자 포함. 이전에 PD-1 억제제 등 면역관문억제제 치료를 받지 않은 환자에 한해 급여",
    "line": "2차 이상 (second-line or later)"
  },
  "confidence": "high",
  "sources": [
    {
      "title": "텝메코·테빔브라 급여등재…자이티가 본인부담 축소",
      "url": "https://www.dailypharm.com/News/321407",
      "media": "데일리팜",
      "date": "2025-03-20"
    },
    {
      "title": "표적항암제 '텝메코'·면역항암제 '테빔브라', 내달부터 급여 적용",
      "url": "https://www.koreahealthlog.com/news/articleView.html?idxno=50983",
      "media": "코리아헬스로그",
      "date": "2025-03-21"
    },
    {
      "title": "테빔브라, 식도암 영역 첫 면역항암제 급여약 되나",
      "url": "https://m.dailypharm.com/newsView.html?ID=320501",
      "media": "데일리팜",
      "date": "2025-02-19"
    }
  ]
}
```

## 한국어 요약

베이진코리아(현 비원메디슨)의 면역항암제 **테빔브라주(성분 tislelizumab)** 는 **2025년 4월 1일** 식도편평세포암(ESCC) 영역 면역항암제로는 최초로 건강보험 급여에 등재되었다.

**위험분담제(RSA) 유형**: 윈도우 내 데일리팜(2025-03-20) 보도에서 "**환급형, 총액제한형 RSA 계약을 맺었다**"고 명시 — 즉 **환급형(refund) + 총액제한형(expenditure_cap)** 의 결합형(combined) 계약이다. 상한금액은 **병당 1,206,000원**으로 등재되었다. 신약 위험분담 환급대상 약제 리스트에 포함되었으며, 식도암·위암·비소세포폐암 등 5개 적응증에서 RSA를 조건으로 급여 적정성을 인정받았다.

**환자·적응증 제한**: 이전 백금 기반 화학요법 치료 도중 또는 이후에 재발·진행된 절제 불가능, 재발성, 국소진행성 또는 전이성 ESCC의 **2차 이상 치료**. 선행화학요법 또는 수술 후 보조요법 종료 후 6개월 이내 재발 환자도 포함되며, **이전에 PD-1 억제제 등 면역관문억제제 치료를 받지 않은 환자에 한해** 급여가 적용된다.

**사후관리/모니터링**: 총액제한형 계약 구조상 사용금액 상한 관리가 적용되나, 구체적 모니터링 기간·환급률·사용량 검토 주기는 윈도우 내 전문지 보도에서 공개되지 않았다(비공개).

신뢰도: **high** — RSA 유형(환급형+총액제한형)과 상한금액이 윈도우 내 Tier1 전문지에서 명시적으로 확인됨. 단, 환급률·모니터링 기간 등 세부 계약 조건은 비공개로 추정 없이 null/공백 처리.
