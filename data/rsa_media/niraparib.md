---
report_ids: [1298]
brand: 제줄라캡슐
ingredient: niraparib
listing_date: 2019-11-01
window: 2019-09-02 ~ 2019-12-31
found: true
---

```json
{
  "found": true,
  "report_ids": [1298],
  "brand": "제줄라캡슐",
  "ingredient": "niraparib",
  "listing_date": "2019-11-01",
  "window_from": "2019-09-02",
  "window_to": "2019-12-31",
  "rsa_types": ["expenditure_cap"],
  "rsa_type_primary": "expenditure_cap",
  "conditions": [
    "총액제한형(expenditure cap) 위험분담계약(RSA) 체결 — 건강보험공단 약가협상 타결",
    "대체약물(비교약물)로 린파자(olaparib) 지정 → 급여 대상이 BRCA 변이 환자로 한정",
    "생식세포(germline) BRCA 변이 환자에 한해 급여 인정, 체세포(somatic) BRCA 변이는 추가 자료 제출 시 재논의",
    "백금계 항암제 완료 후 8주 이내 투약 시작 시 건강보험 적용"
  ],
  "monitoring": {
    "duration_months": null,
    "metrics": ["청구금액(총액제한 기준 대비 초과 여부)"],
    "review": "총액제한 기준 예상청구액 초과분에 대한 사후 환급 정산 (구체 모니터링 기간·지표는 윈도우 내 보도에 미공개)"
  },
  "patient_restrictions": {
    "indication": "2차 이상의 백금기반요법에 반응한 백금민감성 재발성 고도장액성 난소암(난관암·일차복막암 포함) 성인 환자의 단독 유지요법, 생식세포 BRCA 변이 한정",
    "line": "2차 이상 백금민감성 재발 단독 유지요법"
  },
  "confidence": "high",
  "sources": [
    {
      "title": "제줄라캡슐, '생식세포 BRCA 변이 난소암'에만 급여",
      "url": "http://www.hitnews.co.kr/news/articleView.html?idxno=12476",
      "media": "히트뉴스",
      "date": "2019.10.28"
    },
    {
      "title": "난소암 치료제 '제줄라' 내달 1일부터 급여",
      "url": "https://www.monews.co.kr/news/articleView.html?idxno=206771",
      "media": "메디칼업저버",
      "date": "2019.11.28"
    },
    {
      "title": "BRCA 변이군 난소암 2차 항암제 제줄라캡슐 급여 등재",
      "url": "http://m.medipana.com/index_sub.asp?NewsNum=247554",
      "media": "메디파나뉴스",
      "date": "2019.11.28"
    },
    {
      "title": "치료옵션 늘려줄 베스폰사·제줄라 급여 '잰걸음'",
      "url": "http://www.hitnews.co.kr/news/articleView.html?idxno=10243",
      "media": "히트뉴스",
      "date": "2019.07.29"
    }
  ]
}
```

## 한국어 요약

제줄라캡슐(성분 niraparib, 한국다케다/GSK)은 **2019년 12월 1일부터** 난소암 단독 유지요법에 급여가 적용되었으며(앵커 2019-11-01), 등재 시점에 **위험분담계약(RSA)** 이 체결되었다.

**RSA 유형 — 총액제한형(expenditure_cap)**
- 히트뉴스(2019.10.28) 보도에 따르면 건강보험공단과의 **약가협상(총액제한형 RSA)이 타결**되어 건정심에 급여등재안이 상정될 예정이었다.
- 선행 보도(히트뉴스 2019.07.29 약평위 통과)에서도 경제성평가면제 트랙으로 등재된 대체약물 **린파자(olaparib)** 의 총액제한 RSA에 준해 **제줄라도 총액제한 RSA 대상**임을 명시.
- 윈도우 내 보도에서 환급형(refund) 결합·성과기반 조항은 확인되지 않아 **총액제한 단일형**으로 분류.

**환자/적응증 제한**
- 대상: 2차 이상 백금기반요법에 반응한 백금민감성 재발성 고도장액성 난소암(난관암·일차복막암 포함) 성인의 **단독 유지요법**
- **생식세포(germline) BRCA 변이** 환자로 급여 한정 (허가는 BRCA 무관이나 급여는 비교약물 린파자에 맞춰 BRCA 변이로 축소)
- 체세포(somatic) BRCA 변이는 추가 자료 제출 시 **재논의** 보류

**사후관리/모니터링**
- 총액제한 기준 예상청구액 **초과분에 대한 사후 환급 정산** 구조 (총액제한형 RSA 본질)
- 백금계 항암제 완료 후 **8주 이내 투약 시작** 조건으로 급여 적용
- 구체적 모니터링 기간·지표는 윈도우 내 전문지 보도에 공개되지 않음

근거 기사는 모두 Tier1 전문지(히트뉴스·메디파나) 및 일반 의료지(메디칼업저버) 보도이며, 핵심 in-window 근거(히트뉴스 2019.10.28, 메디칼업저버/메디파나 2019.11.28)가 총액제한형 RSA + germline BRCA 한정 급여를 일관되게 확인한다. 히트뉴스 2019.07.29(약평위 통과)는 윈도우 직전이나 RSA 유형 확정의 보조 근거로 첨부. confidence: **high**.
