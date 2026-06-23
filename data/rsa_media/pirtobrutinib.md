---
report_ids: [1297]
brand: 제이퍼카
ingredient: pirtobrutinib
listing_date: 2025-10-01
window: 2025-08-02 ~ 2025-11-30
found: true
---

```json
{
  "found": true,
  "report_ids": [1297],
  "brand": "제이퍼카",
  "ingredient": "pirtobrutinib",
  "listing_date": "2025-10-01",
  "window_from": "2025-08-02",
  "window_to": "2025-11-30",
  "rsa_types": [],
  "rsa_type_primary": null,
  "conditions": [
    "단독요법(monotherapy)에 한해 급여",
    "BTK 억제제를 포함한 2가지 이상의 치료를 받은 적이 있는 재발성 또는 불응성 외투세포림프종(MCL) 성인 환자로 급여 대상 제한"
  ],
  "monitoring": {
    "duration_months": null,
    "metrics": [],
    "review": null
  },
  "patient_restrictions": {
    "indication": "재발성 또는 불응성 외투세포림프종(MCL), 성인, BTK 억제제 포함 2가지 이상 치료 경험",
    "line": "3차 이상 (이전 1가지 이상 BTK 억제제 치료 후 재발/불응)"
  },
  "confidence": "medium",
  "sources": [
    {
      "title": "한국릴리 '제이퍼카', 국내 첫 가역적 BTK 억제제 보험급여 적용",
      "url": "https://www.kpanews.co.kr/news/articleView.html?idxno=263721",
      "media": "약사공론",
      "date": "2025-10-02"
    },
    {
      "title": "한국릴리, 재발성 외투세포림프종 치료제 '제이퍼카' 급여 적용",
      "url": "https://medicalworldnews.co.kr/m/view.php?idx=1510970436",
      "media": "메디칼월드뉴스",
      "date": "2025-10-02"
    }
  ]
}
```

## 한국어 요약

제이퍼카정(성분명 pirtobrutinib, 한국릴리)은 **2025년 10월 1일부로 건강보험 급여**가 적용되었다. 급여 대상은 **브루톤 티로신 키나제(BTK) 억제제를 포함한 두 가지 이상의 치료를 받은 적이 있는 재발성 또는 불응성 외투세포림프종(MCL) 성인 환자에서의 단독요법**으로 제한된다. 즉, 이전에 한 가지 이상의 BTK 억제제 치료 후 재발/불응한 환자(3차 이상 라인)가 대상이며, 국내 최초·유일의 가역적 BTK 억제제로 등재되었다.

### RSA / 사후조건 관련 (윈도우 내)
- **윈도우(2025-08-02 ~ 2025-11-30) 내 Tier1 전문지 보도에서는 위험분담제(RSA) 유형, 환급/총액제한, 사후관리·모니터링 조건에 대한 구체적 보도가 확인되지 않았다.** 등재 시점 보도(약사공론·메디칼월드뉴스, 2025-10-02)는 급여 시작일과 적응증·치료라인 제한만 명시했다.
- 따라서 `rsa_types`는 빈 배열로 기록(보도 부재). 제이퍼카가 신약·희귀의약품이라는 점에서 RSA 적용 개연성은 있으나, **추정·날조 금지 원칙에 따라 보도 근거가 없으므로 기재하지 않음.**

### 윈도우 밖 참고 신호 (평가 대상 아님, 기록만)
- 약평위(2025-05) 심의에서 제이퍼카는 **"향후 추가 임상(근거)자료 제출을 조건으로 급여적정성 인정"** 으로 보도됨(히트뉴스 idxno=64340, 헬스오 idx=146918, 2025-05-09). 이는 conditional(근거자료 제출 조건부) 성격의 사후조건 신호이나, **앵커 윈도우(2025-08-02~) 이전 보도이므로 본 윈도우 평가에는 포함하지 않음.**

### confidence
- listing_date·적응증·치료라인 제한: 다수 Tier1 보도 일치로 **high**.
- RSA 구체 유형·모니터링: 윈도우 내 보도 부재로 **미확인** → 종합 confidence **medium**.
