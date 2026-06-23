---
report_ids: [1101]
brand: 에볼트라주
ingredient: clofarabine (클로파라빈)
listing_date: 2014-01-01
window: 2013-11-02 ~ 2014-03-02
found: true
---

```json
{
  "found": true,
  "report_ids": [1101],
  "brand": "에볼트라주",
  "ingredient": "clofarabine",
  "listing_date": "2014-01-01",
  "window_from": "2013-11-02",
  "window_to": "2014-03-02",
  "rsa_types": ["coverage_with_evidence_development"],
  "rsa_type_primary": "coverage_with_evidence_development",
  "conditions": [
    "국내 최초 위험분담제(RSA) 적용 사례 — '근거생산 조건부 급여' 유형",
    "위험분담 계약기간 등재 후 4년(임상근거 생산 3년 + 평가기간 1년)",
    "소수 소아환자 특성상 임상시험 결과 확보가 어려워, 급여 유지 조건으로 추가 임상근거 생산 의무 부과",
    "전향적 연구(보험급여를 위한 유효성 입증) + 후향적 관찰연구(단일군 시험결과 해석) 병행 수행 조건",
    "보험상한가 병당 199만원(등재 시점)"
  ],
  "monitoring": {
    "duration_months": 48,
    "metrics": ["관해율(remission rate)", "전향적 연구 유효성 결과", "후향적 관찰연구 결과"],
    "review": "제약사(젠자임코리아)가 매 6개월마다 진행보고서 제출, 심평원이 관해율 등 평가지표로 재평가(3년 근거생산 + 1년 평가)"
  },
  "patient_restrictions": {
    "indication": "소아·청소년 급성 림프모구 백혈병(ALL), 재발성 또는 치료 불응성",
    "line": "기존 2차 이상 치료 실패 후(2회 이상 선행요법 후 재발/불응)"
  },
  "confidence": "high",
  "sources": [
    {
      "title": "소아 백혈병藥 '에볼트라주' 건강보험 적용",
      "url": "http://www.kpanews.co.kr/news/articleView.html?idxno=148367",
      "media": "약사공론",
      "date": "2013-12-04"
    },
    {
      "title": "에볼트라주 근거생산 조건부 급여 개요 (FDC법제연구 11권 1호)",
      "url": "https://www.earticle.net/Article/A292218",
      "media": "한국의약품법규학회지 (KHIDI 제약산업정보포털 수록)",
      "date": "2016"
    },
    {
      "title": "에볼트라주 근거생산 조건부 급여 개요 — 학회지 및 논문 자료실",
      "url": "https://www.khidi.or.kr/board/view?linkId=26603639&menuId=MENU01848",
      "media": "KHIDI 제약산업정보포털",
      "date": "2017-01-04"
    }
  ]
}
```

## 한국어 요약

**성분(INN) 확인**: 에볼트라주(Evoltra)의 실제 주성분은 **clofarabine(클로파라빈)** 으로 웹 확인 완료. 클로파라빈은 2세대 purine nucleoside 유사체(cladribine의 2'-fluoro 유도체)이며, 재발성·불응성 소아 급성 림프모구 백혈병(ALL) 치료제. 제조/공급사는 **젠자임코리아(Genzyme Korea, 현 Sanofi 계열)**.

**급여 등재 시점**: 1차 권위 소스(약사공론 2013-12-04, 윈도우 내)에 따르면 요양급여 시행일은 **2013년 12월 12일**. 보고용 앵커(2014-01-01)와는 약 3주 차이가 있으나, 윈도우(2013-11-02~2014-03-02) 내에 포함되며 등재 사실은 확정. (frontmatter/json의 listing_date는 지시값 2014-01-01 유지, 실제 검증값은 2013-12-12.)

**RSA 유형 — 핵심**: 에볼트라주는 **국내 최초의 위험분담제(RSA) 적용 사례**이며, 유형은 단순 환급형이 아니라 **'근거생산 조건부 급여'(Coverage with Evidence Development, CED)**. 2013~2014년은 한국 RSA 제도 도입 초기로, 환급형(refund) 사례는 이후(예: 2014-03-05 얼비툭스·레블리미드)에 등장. 따라서 rsa_type_primary는 환급형이 아닌 **근거생산 조건부형**으로 기록.

**조건·모니터링**:
- 위험분담 계약기간 **등재 후 4년**(임상근거 생산 3년 + 평가기간 1년).
- 소수 소아환자 특성상 임상시험 결과 확보가 어려워, 급여 유지 조건으로 **전향적 연구(유효성 입증)** + **후향적 관찰연구(단일군 결과 해석)** 병행 수행 의무.
- 제약사 **매 6개월 진행보고서 제출**, 심평원이 **관해율 등** 평가지표로 재평가.

**환자/적응증 제한**: 소아·청소년 ALL 중 **재발성 또는 치료 불응성**(2회 이상 선행요법 후 재발/불응, 다른 치료옵션으로 지속 반응을 기대하기 어려운 경우).

**비용 참고(등재 시점)**: 보험상한가 병당 199만원, 주기당 투약비용 약 3,980만원, 통상 2주기 투약 시 연간 1인당 약 7,960만원.

**confidence: high** — 윈도우 내 Tier1 전문지(약사공론) + 학술 개요 문헌 2종이 RSA 유형·조건·모니터링·적응증을 일관되게 뒷받침. 추정/날조 없음.
