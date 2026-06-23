---
report_ids: [862]
brand: 빅시오스리포좀주
ingredient: daunorubicin + cytarabine (리포좀 CPX-351, 5:1 몰비)
listing_date: 2024-12-01
window: 2024-10-02 ~ 2025-01-30
found: true
---

```json
{
  "found": true,
  "report_ids": [862],
  "brand": "빅시오스리포좀주",
  "ingredient": "daunorubicin + cytarabine (liposomal CPX-351, 5:1 molar ratio)",
  "listing_date": "2024-12-01",
  "window_from": "2024-10-02",
  "window_to": "2025-01-30",
  "rsa_types": ["refund"],
  "rsa_type_primary": "refund",
  "conditions": [
    "환급형 위험분담 계약(공단-한독). 계약조건에 따른 사후 환급·사후관리 — 윈도우 내 보도는 위험분담 적용 사실을 명시하지 않으며, 환급형 분류는 HIRA 환급대상 약제 목록(2026-01-07, 윈도우 외)으로 확인됨",
    "표시가 720만원/바이알, 암환자 산정특례 적용으로 환자 본인부담 5%"
  ],
  "monitoring": {
    "duration_months": null,
    "metrics": [],
    "review": "윈도우 내 보도에 모니터링 기간·지표·재평가 조건 명시 없음"
  },
  "patient_restrictions": {
    "indication": "성인 신규 진단 치료관련 급성골수성백혈병(t-AML) 또는 골수이형성증 관련 변화 동반 급성골수성백혈병(AML-MRC)",
    "line": "1차 관해유도요법 또는 관해공고요법 (보도상 60세 이상 성인 대상)"
  },
  "confidence": "medium",
  "sources": [
    {"title": "한독, 고위험 급성 골수성 백혈병 치료제 '빅시오스리포좀주' 급여", "url": "https://medifonews.com/news/article.html?no=197796", "media": "메디파나뉴스", "date": "2024-12-02"},
    {"title": "백혈병藥 '빅시오스' 약가 720만원···한독, 도입신약으로 매출 늘릴까", "url": "https://www.sisajournal-e.com/news/articleView.html?idxno=407477", "media": "시사저널e", "date": "2024-11-22"}
  ]
}
```

## 한국어 요약

빅시오스리포좀주(Vyxeos)는 재즈 파마슈티컬이 개발하고 한독이 국내 독점 공급하는 고위험 급성골수성백혈병 치료제로, **성분(INN)은 daunorubicin + cytarabine**을 리포좀에 5:1 몰비로 동시 봉입한 CPX-351 제형이다.

- **급여 등재일: 2024-12-01** (앵커 일치). 2022년 12월 품목허가 → 2024년 8월 약평위 통과(윈도우 외) → 2024년 11월 공단 약가협상 완료 → 12월 1일 급여 적용.
- **약가·본인부담:** 표시가 **720만원/바이알**, 암환자 산정특례로 **환자 본인부담 5%**.
- **환자/적응증 제한:** 성인 신규 진단 **t-AML 또는 AML-MRC**의 1차 관해유도/관해공고요법 (보도상 60세 이상 성인 대상).
- **위험분담(RSA):** 윈도우 내 보도(2024-11~12)는 신약·신규 등재·약가협상 완료 사실을 다루나 **위험분담 유형을 명시적으로 보도하지 않았다.** 다만 HIRA의 위험분담 환급대상 약제 목록(2026-01-07, 윈도우 외 자료)에서 빅시오스리포좀주가 한독의 **환급형(refund)** 위험분담 약제로 분류된 것이 확인되어, rsa_type_primary를 `refund`로 기록하되 **confidence는 medium**으로 설정한다(윈도우 내 1차 출처가 RSA 유형을 직접 명시하지 못함).
- **모니터링:** 윈도우 내 보도에 모니터링 기간·지표·재평가 주기에 대한 구체 정보 없음(null).

> 룰 준수: rsa_types 및 monitoring 세부는 윈도우 내 1차 보도에서 직접 확인되지 않아 추정/날조하지 않았으며, sources에는 윈도우(2024-10-02~2025-01-30) 내 보도만 포함했다. 환급형 분류 근거(2026-01-07 뉴스더보이스헬스케어)는 윈도우 외이므로 sources에서 제외했다.
