---
report_ids: [1305]
brand: 젤보라프정
ingredient: vemurafenib
listing_date: 2017-07-01
window: 2017-05-02 ~ 2017-08-30
found: false
---

```json
{
  "found": false,
  "report_ids": [1305],
  "brand": "젤보라프정",
  "ingredient": "vemurafenib",
  "listing_date": "2017-07-01",
  "window_from": "2017-05-02",
  "window_to": "2017-08-30",
  "rsa_types": [],
  "rsa_type_primary": null,
  "conditions": [],
  "monitoring": {
    "duration_months": null,
    "metrics": [],
    "review": null
  },
  "patient_restrictions": {
    "indication": "BRAF V600 변이 양성 수술 불가능 또는 전이성 흑색종",
    "line": null
  },
  "confidence": "low",
  "sources": [
    {
      "title": "암 환자 사용약제 보장성 강화정책 효과분석 (발간등록번호 G000F8K-2017-138)",
      "url": "https://repository.hira.or.kr/handle/2019.oak/1513",
      "media": "건강보험심사평가원(HIRA)",
      "date": "2017"
    },
    {
      "title": "젤보라프정240mg Zelboraf Tab. 240mg 의약품 정보",
      "url": "https://www.health.kr/searchDrug/result_drug.asp?drug_cd=2012072400015",
      "media": "약학정보원(health.kr)",
      "date": "2017"
    }
  ]
}
```

## 한국어 요약

**결론: found = false** — 젤보라프정(vemurafenib, 한국로슈)의 급여 등재 시점(앵커 2017-07-01) 및 윈도우(2017-05-02 ~ 2017-08-30) 내에서, **위험분담제(RSA) 적용 사실·유형·계약 조건·모니터링 조건을 직접 확인할 수 있는 Tier1 전문지(데일리팜·약업신문·메디파나·히트뉴스) 또는 일반웹 보도를 찾지 못했다.** 추정·날조 금지 룰에 따라 found:false 로 처리한다.

### 확인된 사실 (참고)
- 젤보라프는 **2012-07-23 국내 시판 허가**(BRAF V600E 변이 양성 수술 불가능/전이성 흑색종)되었고, 초기에는 **비급여**였다.
- 흑색종 적응증에서 급여 적용 시 본인부담 5%(산정특례)로 월 ~50만원 수준이라는 일반 보도가 존재하나, 이는 중증암 산정특례에 따른 본인부담률 설명일 뿐 **RSA 계약 사실을 입증하지 못한다.**
- 2017년 9월 기준 국내 RSA 적용 약제는 17개 품목(대부분 환급형, 조건부 1개)이라는 HIRA 정책효과 분석 보고서가 존재하나, **17개 품목의 개별 명단(젤보라프 포함 여부)을 본 조사에서 직접 확인하지 못했다**(원문 PDF 접근 시 TLS 인증서 오류로 표 검증 불가).

### found:false 근거
1. **신약 최초 등재 룰 불일치 가능성**: 젤보라프는 2012년 허가/등재 이력이 있는 약제로, 2017-07-01 앵커가 "신약 최초 약가 등재 시점"에 해당하는지 윈도우 내 보도로 확정되지 않았다.
2. **윈도우 내 RSA 보도 부재**: 2017-05-02 ~ 2017-08-30 사이 Tier1·일반웹에서 젤보라프의 RSA 유형(refund/expenditure_cap/utilization/conditional/combined)·환급률·총액제한·사후관리 조건을 명시한 기사를 발견하지 못했다.
3. **근거 없는 항목 추정 금지**: 따라서 rsa_types, conditions, monitoring 은 공란으로 두고 found:false 로 보고한다.

### 후속 권고
- HIRA 보고서 G000F8K-2017-138 원문 PDF의 RSA 17개 품목 표를 직접 확보하여 젤보라프 포함 여부 및 유형 확정 필요.
- 보건복지부 「약제급여목록 및 급여상한금액표」 2017년 6~7월 고시(젤보라프 등재일·상한금액)로 앵커(2017-07-01) 검증 필요.
