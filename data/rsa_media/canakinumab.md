---
report_ids: [1252]
brand: 일라리스주
ingredient: canakinumab
listing_date: 2024-08-01
window: 2024-06-02 ~ 2024-09-30
found: true
---

```json
{
  "found": true,
  "report_ids": [1252],
  "brand": "일라리스주",
  "ingredient": "canakinumab",
  "listing_date": "2024-08-01",
  "window_from": "2024-06-02",
  "window_to": "2024-09-30",
  "rsa_types": ["expenditure_cap", "refund", "conditional"],
  "rsa_type_primary": "combined",
  "conditions": [
    "총액제한형(expenditure_cap): 제약사가 연간 청구 총액을 사전 설정, 지출이 상한(cap)을 초과하면 건보공단에 환급",
    "환급형(refund): 사용량 초과분에 대한 환급 의무 (cap + refund 결합 성격)",
    "조건부 급여(conditional): '캡 씌우고 근거자료 제출 조건'으로 등재",
    "위험분담 계약기간 만료 평가 시 CAPS 중 신생아 발현 다발성 염증질환(NOMID/CINCA) 적응증에 대해 임상적 유용성 및 비용효과성 자료 제출",
    "경제성평가 유예 약제로 등재(국내 CAPS 환자 극소수, 대체약제 부재 고려) — 윈도우 밖(2024-04) 보도 기반 제도 맥락"
  ],
  "monitoring": {
    "duration_months": 24,
    "metrics": ["환자별 관찰자료", "연구결과(임상적 유용성)", "비용효과성 자료(계약만료 시 NOMID/CINCA)"],
    "review": "환자별 관찰기간 2년, 1년 단위로 관찰자료 및 연구결과 제출. 위험분담 계약기간 만료 시 NOMID/CINCA 적응증의 임상적 유용성·비용효과성 재평가"
  },
  "patient_restrictions": {
    "indication": "유전성 재발열 증후군 3개 적응증 — CAPS(크리오피린 관련 주기적 증후군), TRAPS(종양괴사인자 수용체 관련 주기적 증후군), FMF(가족성 지중해열). 만 2세 이상 소아 및 성인",
    "line": "FMF는 콜키신 금기 또는 효과 불충분 환자로 제한"
  },
  "confidence": "high",
  "sources": [
    {
      "title": "캡도 씌우고 근거자료 제출 조건에 급여된 '일라리스'",
      "url": "http://www.hitnews.co.kr/news/articleView.html?idxno=56472",
      "media": "히트뉴스",
      "date": "2024-07-31"
    },
    {
      "title": "한국노바티스 '일라리스', CAPS·TRAPS·FMF 등 3개 적응증에 허가 9년만에 급여 적용",
      "url": "https://healtho.co.kr/news/view.php?idx=143695",
      "media": "헬스오",
      "date": "2024-08-09"
    },
    {
      "title": "약제 요양급여의 적정성 평가결과 canakinumab 0.15g (일라리스주사액)",
      "url": "https://common.health.kr/shared/docs/healthkr/boh_result/평가결과_일라리스주사액(한국노바티스(주))202409.pdf",
      "media": "건강보험심사평가원(health.kr 게재)",
      "date": "2024-09"
    },
    {
      "title": "유전성 재발열 치료제 '일라리스' 1100만원대 급여 눈 앞",
      "url": "https://www.kpanews.co.kr/article/show.asp?idx=252144&category=C",
      "media": "약사공론",
      "date": "2024"
    }
  ]
}
```

## 한국어 요약

**일라리스주(canakinumab, 한국노바티스)** 는 국내 허가 9년 만인 **2024-08-01** 자로 유전성 재발열 증후군 3개 적응증(CAPS·TRAPS·FMF)에 건강보험 급여가 적용되었다. 상한금액은 0.15g/1병당 약 1억 1,029만 원(1,100만 원대)으로 결정되었다.

**위험분담제(RSA) 유형 — combined (총액제한형 + 환급형 + 조건부):**
건보공단은 제약사가 연간 청구 총액을 사전 설정하고 그 상한(cap)을 초과하면 환급하는 **총액제한형(expenditure_cap)** 과 **환급형(refund)** 을 결합 적용했다. 또한 "캡을 씌우고 근거자료 제출 조건"으로 등재된 **조건부 급여(conditional)** 성격을 갖는다. (경제성평가 유예 약제로 등재 — 국내 환자 극소수·대체약제 부재 고려, 윈도우 밖 2024-04 보도 기반 제도 맥락.)

**사후관리:**
- 환자별 **관찰기간 2년(24개월)**
- **1년 단위**로 관찰자료 및 연구결과 제출
- 위험분담 **계약기간 만료 평가** 시 CAPS 중 신생아 발현 다발성 염증질환(**NOMID/CINCA**) 적응증에 대해 **임상적 유용성 및 비용효과성 자료** 제출 의무

**환자/적응증 제한:**
만 2세 이상 소아 및 성인 대상. FMF는 콜키신 금기 또는 효과 불충분 환자로 제한.

**신뢰도: high** — 히트뉴스(2024-07-31)와 헬스오(2024-08-09) 등 Tier1 전문지 보도가 RSA 유형(총액제한·환급)·관찰기간 2년·NOMID/CINCA 재평가 조건을 일관되게 확인. 심평원 평가결과 PDF(2024-09)가 1차 권위 소스로 존재(바이너리 파싱 불가하나 출처 기록). 모든 핵심 근거는 보정된 검색 윈도우(2024-06-02 ~ 2024-09-30) 내 보도.

> 참고: 직전 버전은 잘못된 앵커(2026-02-01)로 found:false 였으나, 정정된 앵커(2024-08-01)에서는 실제 등재 이벤트가 윈도우 내에 위치하여 found:true 로 갱신됨.
