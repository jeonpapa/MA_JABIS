---
report_ids: [1310]
brand: 젬퍼리주
ingredient: dostarlimab
listing_date: 2023-12-01
window: 2023-10-02 ~ 2024-01-30
found: true
---

```json
{
  "found": true,
  "report_ids": [1310],
  "brand": "젬퍼리주",
  "ingredient": "dostarlimab",
  "listing_date": "2023-12-01",
  "window_from": "2023-10-02",
  "window_to": "2024-01-30",
  "rsa_types": ["utilization", "conditional"],
  "rsa_type_primary": "utilization",
  "conditions": [
    "위험분담계약제(RSA)로 급여 등재 — 윈도우 내 보도는 본인부담 상한·투여기간 제한 형태의 사용량/조건부 관리만 명시(환급형·총액제한 세부 표현은 보도에 없음)",
    "투여기간 1년 인정, 전체생존기간(OS) 미입증 시 임상결과에 따라 최대 2년까지 자동 연장",
    "질병 진행(progression)이 확인되면 급여 처방 중단",
    "환자 1인 연간 약값 약 5,029만원 기준 본인부담 5% 적용(연 약 251만~291만원)",
    "급여 대상: 백금기반 화학요법 치료 중 또는 치료 후 진행한 재발성·진행성(FIGO stage IIIB 이상) 자궁내막암",
    "제외: 이전 PD-1/PD-L1/PD-L2 치료 경험자, 암육종(carcinosarcoma)"
  ],
  "monitoring": {
    "duration_months": 24,
    "metrics": ["disease_progression", "overall_survival"],
    "review": "투여 1년 인정 후 질병 진행 미확인·OS 미입증 시 최대 2년 자동 연장, 진행 확인 시 급여 중단"
  },
  "patient_restrictions": {
    "indication": "백금기반 화학요법 중/후 진행한 재발성·진행성 dMMR/MSI-H 자궁내막암(ECOG PS 0~1)",
    "line": "2차 이상 단독요법"
  },
  "confidence": "medium",
  "sources": [
    {"title": "젬퍼리, 자궁내막암 2차치료 환자부담 200만원...최대 2년까지 적용", "url": "https://www.pharmnews.com/news/articleView.html?idxno=236332", "media": "팜뉴스", "date": "2023-12-12"},
    {"title": "12월부터 '젬퍼리' 급여‥소외됐던 '자궁내막암' 치료 진일보 예고", "url": "https://www.medipana.com/article/view.php?news_idx=319602", "media": "메디파나", "date": "2023-12"}
  ]
}
```

## 한국어 요약

**젬퍼리주(dostarlimab, 한국GSK)** 는 **2023년 12월 1일** 백금기반 화학요법 치료 중 또는 치료 후 진행한 **재발성·진행성 dMMR/MSI-H 자궁내막암**의 **2차 이상 단독요법**으로 최초 건강보험 급여 등재되었다. 윈도우(2023-10-02~2024-01-30) 내 보도 기준으로 확인된 내용은 다음과 같다.

- **RSA 적용**: 젬퍼리는 위험분담계약제(RSA)로 등재되었다. 다만 윈도우 내 Tier1·일반웹 보도에서는 "환급형(refund)" 또는 "총액제한(expenditure cap)" 이라는 명시적 RSA 유형 표현은 확인되지 않았고, **사용량·조건부 관리(본인부담 상한 + 투여기간 제한 + 진행 시 중단)** 형태로만 보도되었다. 따라서 rsa_type은 보도 근거에 한정해 utilization/conditional로 분류했다(refund/expenditure_cap 단정 회피).
- **투여기간(모니터링)**: 1년 인정 후, OS 미입증·질병 미진행 시 임상결과에 따라 **최대 2년까지 자동 연장**. **질병 진행 확인 시 급여 처방 중단**.
- **환자 부담**: 연간 약값 약 5,029만원 기준 **본인부담 5%(연 약 251만~291만원)** 로 경감.
- **환자/적응증 제한**: dMMR/MSI-H 변이 확인 + ECOG PS 0~1. 이전 PD-1/PD-L1/PD-L2 치료 경험자 및 암육종 환자 제외. 국내 대상 환자는 연 약 100~150명 규모.

**근거 한계**: 환급형·총액제한 등 정량적 RSA 재정장치(환급률, 캡 금액)는 윈도우 내 공개 보도에서 확인되지 않아 confidence=medium. 약평위 급여적정성 판정 보도(데일리팜 2023-08-03)는 윈도우 이전이라 sources에서 제외(참고용).
