---
report_ids: [790, 1617]
brand: 베스레미주
ingredient: ropeginterferon alfa-2b
listing_date: 2025-09-01
window: 2025-07-03 ~ 2025-10-31
found: true
---

```json
{
  "found": true,
  "report_ids": [790, 1617],
  "brand": "베스레미주",
  "ingredient": "ropeginterferon alfa-2b",
  "listing_date": "2025-09-01",
  "window_from": "2025-07-03",
  "window_to": "2025-10-31",
  "rsa_types": ["risk_sharing_agreement"],
  "rsa_type_primary": "risk_sharing_agreement_undisclosed",
  "conditions": [
    "약평위가 위험분담안 적용 시 비용효과비(ICER)를 수용 가능하다고 판단하여 급여 적정성 인정",
    "고가 대체약제 대비 경제성평가 트랙 진입 약제",
    "구체적 RSA 유형(환급형/총액제한 등)은 윈도우 내 매체에 비공개"
  ],
  "monitoring": {
    "duration_months": 36,
    "metrics": ["완전혈액학적반응(CHR) 12개월 평가"],
    "review": "투여 12개월째 완전혈액학적 반응이 나타나지 않으면 투여 중단, 최대 투여기간 3년(36개월)"
  },
  "patient_restrictions": {
    "indication": "진성적혈구증가증(PV) — 하이드록시우레아에 불응성 또는 불내성이면서 비장비대증이 없거나 비장 크기 17cm 이하인 환자",
    "line": "2차 치료(second-line)"
  },
  "confidence": "high",
  "sources": [
    {
      "title": "적혈구 증가증 치료제 베스레미, 9월 급여등재 유력",
      "url": "https://m.dailypharm.com/newsView.html?ID=326059",
      "media": "데일리팜",
      "date": "2025-08-20"
    },
    {
      "title": "5만명 국회 청원 갔던 '베스레미' 9월부터 급여",
      "url": "https://www.doctorsnews.co.kr/news/articleView.html?idxno=160839",
      "media": "의협신문",
      "date": "2025-08-22"
    },
    {
      "title": "파마에센시아 '베스레미' 진성적혈구증가증 2차치료에 급여",
      "url": "https://www.medifonews.com/news/article.html?no=206119",
      "media": "메디포뉴스",
      "date": "2025-08-27"
    },
    {
      "title": "진성적혈구증가증 치료제 '베스레미', 9월부터 급여 적용",
      "url": "http://www.docdocdoc.co.kr/news/articleView.html?idxno=3031361",
      "media": "청년의사",
      "date": "2025-08-27"
    },
    {
      "title": "PV 인터페론 치료제 베스레미주 급여 문턱 넘은 비결은?",
      "url": "http://m.dailypharm.com/newsView.html?ID=327484",
      "media": "데일리팜",
      "date": "2025-10-01"
    }
  ]
}
```

## 한국어 요약

**브랜드/성분**: 베스레미주(Besremi) — 성분 INN **ropeginterferon alfa-2b**(로페그인터페론 알파-2b). 장기지속형 단일 PEG화 3세대 인터페론으로, 진성적혈구증가증(PV) 치료제. 웹 확인으로 추정 성분이 정확함을 검증.

**급여 등재 시점**: 2025년 9월 1일자 건강보험 급여 적용(앵커 일치). 약평위 통과(2025-05-08, 제5차 약제급여평가위원회)는 윈도우 이전이라 본 RSA 리포트의 1차 근거로는 윈도우 내(2025-07-03~2025-10-31) 보도 5건만 사용.

**RSA(위험분담제)**: 윈도우 내 매체 다수가 "위험분담안 적용 시 비용효과비가 수용 가능하다고 판단"되어 급여 적정성을 인정받았다고 보도. 고가 대체약제 대비 경제성평가 트랙에 진입한 약제로, **RSA가 적용되었다는 사실은 확인**되나 **구체적 유형(환급형/총액제한/근거생산 조건부 등)은 윈도우 내 매체에 비공개**. 따라서 rsa_type_primary 는 `risk_sharing_agreement_undisclosed`로 표기(룰: 추정/날조 금지).

**모니터링·사후조건**:
- 최대 투여기간 **3년(36개월)**.
- 투여 **12개월째 완전혈액학적 반응(CHR)이 없으면 투여 중단** — 임상 반응 기반 지속 조건.

**환자/적응증 제한**:
- 적응증: 진성적혈구증가증(PV) 중 **하이드록시우레아에 불응성 또는 불내성**인 환자.
- 추가 제한: **비장비대증이 없거나 비장 크기 17cm 이하**.
- 치료 차수: **2차 치료제**(2023년 1차 도전 시 2차 유효성 근거 부족으로 암질심 부결 → 국내 임상근거 보완 후 2024-07 암질심 통과, 2025-05 약평위 통과, 2025-09 등재).

**신뢰도**: high (등재일·적응증·치료기간·12개월 모니터링·RSA 적용 사실은 윈도우 내 복수 매체 교차확인). RSA 세부 유형만 비공개로 미확정.
