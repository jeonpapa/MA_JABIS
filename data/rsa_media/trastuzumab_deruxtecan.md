---
report_ids: [1122]
brand: 엔허투주
ingredient: trastuzumab deruxtecan
listing_date: "2024-05-01"
window: "2024-02-01 ~ 2024-06-01 (±2개월)"
found: true
anchor_corrected: true
---

```json
{
  "found": true,
  "report_ids": [1122],
  "brand": "엔허투주",
  "ingredient": "trastuzumab deruxtecan",
  "listing_date": "2024-05-01",
  "window_from": "2024-02-01",
  "window_to": "2024-06-01",
  "rsa_types": ["expenditure_cap"],
  "rsa_type_primary": "expenditure_cap",
  "conditions": [
    "총액제한형 위험분담제(expenditure_cap) 체결을 조건으로 약평위 통과 (2024-02-01 약평위)",
    "급여 상한금액 병당(100mg vial) 143만1,000원",
    "환자 본인부담 5% 적용 (산정특례 항암제) → 유방암 환자 연간 약 417만원 부담 (비급여 시 약 8,300만원)",
    "2024-02-01 제2차 암질환심의위원회 급여 적정성 인정 → NHIS 약가협상 → 건정심(제7차) 신규 급여 결정 → 2024-04-01 급여 적용"
  ],
  "monitoring": {
    "duration_months": null,
    "metrics": ["총 청구액(총액제한 캡 대비)"],
    "review": "총액제한형 RSA — 사전 합의된 연간 총 청구액 상한 초과분을 제약사가 환급. 구체적 캡 금액·정산주기는 비공개(협상 비공개 조건)"
  },
  "patient_restrictions": {
    "indication": "HER2 양성 절제불가능/전이성 유방암; HER2 과발현 전이성 위암·위식도접합부 선암",
    "line": "유방암: 트라스투주맙+탁산계 모두 실패한 2차 이상; 위암: 트라스투주맙 포함 2개 이상 요법 실패한 3차 이상"
  },
  "confidence": "high",
  "sources": [
    {"title": "엔허투·일라리스…우여곡절 끝에 약평위 넘어", "url": "https://www.k-health.com/news/articleView.html?idxno=70048", "media": "헬스경향", "date": "2024-02-05"},
    {"title": "말많고 탈많던 '엔허투' 결국 급여 \"약가는 얼마나?\"", "url": "http://www.kpanews.co.kr/news/articleView.html?idxno=248496", "media": "약사공론", "date": "2024-02-02"},
    {"title": "항제약물접합 항암제 '엔허투' 재도전끝 약평위 통과", "url": "https://www.medicaltimes.com/Mobile/News/NewsView.html?ID=1157281", "media": "메디칼타임즈", "date": "2024-02-02"},
    {"title": "전이성 유방암·위암 치료제 엔허투, 4월 1일 신규 급여 적용", "url": "http://www.kpanews.co.kr/news/articleView.html?idxno=249654", "media": "약사공론", "date": "2024-03-28"},
    {"title": "유방암 혁신신약 '엔허투' 143만원 급여 신설", "url": "http://www.bosa.co.kr/news/articleView.html?idxno=2219554", "media": "의학신문", "date": "2024-03-28"},
    {"title": "유방암 신약 '엔허투' 4월부터 급여, 부담 얼마나 줄어들까?", "url": "https://www.doctorsnews.co.kr/news/articleView.html?idxno=154066", "media": "의협신문", "date": "2024-03"}
  ],
  "note": "앵커 2017-08-01 trastuzumab(허셉틴) 오매칭→실제 등재일 정정. 엔허투주(T-DXd)는 별개 신약으로 국내 최초 급여 등재는 2024-04-01."
}
```

## 한국어 요약

**앵커 정정**: 시스템상 `listing_date=2017-08-01` 은 동일 성분명(trastuzumab) 인 허셉틴(trastuzumab) 오매칭으로 확인됨. 엔허투주(trastuzumab deruxtecan, T-DXd, 다이이찌산쿄/아스트라제네카)는 ADC 계열 별개 신약으로, 국내 **최초 급여 등재일은 2024년 4월 1일**임. 조사 윈도우는 ±2개월(2024-02-01 ~ 2024-06-01)로 설정.

**등재 경로 (윈도우 내)**
- 2024-02-01: 제2차 암질환심의위원회 급여 적정성 인정 + 동일 시기 약제급여평가위원회(약평위) 통과
- 약평위 통과 시 **총액제한형 위험분담제(expenditure cap) 체결**을 조건으로 부과 (헬스경향 2024-02-05 명시)
- 이후 NHIS 약가협상(60일) → 건강보험정책심의위원회(제7차) 신규 급여 결정
- 2024-04-01 급여 적용 개시

**RSA 유형**: 총액제한형(expenditure_cap). 사전 합의된 연간 총 청구액 상한을 초과하는 분에 대해 제약사가 환급하는 구조. 단, 구체적 캡 금액·정산주기·환급률은 협상 비공개 조건으로 매체에 공개되지 않음.

**약가/본인부담**: 급여 상한금액 100mg 1바이알당 143만1,000원(비급여 시 약 230만원). 항암제 산정특례로 본인부담 5% 적용 → 유방암 환자 연간 투약비용이 약 8,300만원에서 약 417만원으로 경감.

**환자/적응증 제한**
- 유방암: HER2 양성 절제불가능/전이성, 트라스투주맙+탁산계 모두 실패한 **2차 이상**
- 위암: HER2 과발현 전이성 위암/위식도접합부 선암, 트라스투주맙 포함 2개 이상 요법 실패한 **3차 이상**
- (참고) HER2 저발현 유방암·HER2 변이 비소세포폐암 적응증은 본 윈도우(2024-04) 시점에는 급여 제외 — 이후 별도 급여확대 신청 진행 중(윈도우 밖, 본 리포트 대상 아님)

**모니터링**: 총액제한형 RSA 특성상 총 청구액이 핵심 추적 지표이나, 구체적 기간(duration_months)·정산 주기·세부 모니터링 방안은 매체에 비공개. → `monitoring.duration_months=null`.

**신뢰도**: high (Tier1 전문지 4종 + 일반지 2종 교차 확인, RSA 유형은 헬스경향 단독 명시이나 약평위 통과 직후 보도로 일관성 있음).
