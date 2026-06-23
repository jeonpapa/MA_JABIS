---
report_ids: [803]
brand: 벤리스타주
ingredient: belimumab
listing_date: 2021-02-01
window: 2020-12-03 ~ 2021-04-02
found: true
---

# 벤리스타주 (belimumab) — 급여 등재 시점 RSA·사후조건 조사

```json
{
  "found": true,
  "report_ids": [803],
  "brand": "벤리스타주",
  "ingredient": "belimumab",
  "listing_date": "2021-02-01",
  "window_from": "2020-12-03",
  "window_to": "2021-04-02",
  "rsa_types": ["refund", "expenditure_cap", "utilization"],
  "rsa_type_primary": "combined",
  "conditions": [
    "환급형(refund): 약제 청구금액의 일정 비율을 제약사가 건강보험공단에 환급",
    "초기치료 환급형(utilization/initial-treatment refund): 초기 일정 기간 치료분을 제약사가 공단에 환급",
    "총액제한형(expenditure_cap): 예상 청구금액 총액(cap) 초과분의 일정 비율을 제약사가 공단에 환급",
    "표시가(약가)는 유지하고 환급률 조정 방식 — 120mg 약 18만2,696원, 400mg 약 60만8,988원(등재 시점 표시가)",
    "대상환자 약 835명, 연 예상 재정소요액 약 88억3천만원(건보공단 예상청구액 기준)"
  ],
  "monitoring": {
    "duration_months": 18,
    "metrics": ["SELENA-SLEDAI 점수"],
    "review": "최초 투약 후 24주째 평가에서 SELENA-SLEDAI가 4 이상 감소 시 추가 6개월 인정; 12개월째 평가에서 24주째 결과 유지 시 추가 6개월 인정 (최대 76주/18개월)"
  },
  "patient_restrictions": {
    "indication": "표준요법(코르티코스테로이드·항말라리아약·면역억제제)으로 3개월 이상 치료 중인 자가항체 양성 활동성 전신홍반루푸스(SLE) 만 18세 이상 성인. 추가조건 모두 충족: SELENA-SLEDAI 10 이상, 항dsDNA항체 양성, 낮은 보체(C3 또는 C4). 중증 활성 중추신경계 SLE 및 중증 활성 루푸스신염은 제외(불인정)",
    "line": "표준요법 3개월 이상 후 add-on (병용)"
  },
  "confidence": "high",
  "sources": [
    {"title": "'더블 환급'에 총액제한까지...벤리스타 RSA로 중무장", "url": "https://www.newsthevoice.com/news/articleView.html?idxno=16949", "media": "뉴스더보이스헬스케어", "date": "2020-12-29"},
    {"title": "GSK 전신홍반루푸스 치료제 벤리스타, 2월 1일부터 건강보험 급여 등재", "url": "https://kr.gsk.com/ko-kr/media/press-releases/2021-02-01/", "media": "GSK Korea", "date": "2021-02-01"},
    {"title": "전신홍반루푸스 치료제 '벤리스타', 7년 만에 급여 등재", "url": "https://www.docdocdoc.co.kr/news/articleView.html?idxno=2007314", "media": "청년의사", "date": "2021-02-01"},
    {"title": "GSK 전신홍반루푸스 치료제 벤리스타, 7년만에 급여 등재", "url": "http://www.monews.co.kr/news/articleView.html?idxno=301009", "media": "메디칼업저버", "date": "2021-02-01"},
    {"title": "전신홍반루푸스 치료제 벤리스타, 2월부터 급여 등재", "url": "https://www.mdtoday.co.kr/news/view/179516701185732", "media": "메디컬투데이", "date": "2021-02-01"},
    {"title": "벤리스타주(벨리무맙) 약제급여평가위원회 회의자료(급여기준)", "url": "https://common.health.kr/shared/docs/healthkr/boh_result/%ED%9A%8C%EC%9D%98%EC%9E%90%EB%A3%8C_%EB%B2%A4%EB%A6%AC%EC%8A%A4%ED%83%80%EC%A3%BC(%EA%B8%80%EB%9D%BD%EC%86%8C%EC%8A%A4%EB%AF%B8%EC%8A%A4%ED%81%B4%EB%9D%BC%EC%9D%B8(%EC%A3%BC)).pdf", "media": "건강보험심사평가원(health.kr)", "date": "2018-02-21"}
  ]
}
```

## 한국어 요약

**벤리스타주(벨리무맙, GSK)** 는 2013년 6월 국내 허가 후 약 8년만인 **2021년 2월 1일** 건강보험 급여 목록에 등재됐다(전신홍반루푸스/SLE). 등재는 **위험분담제(RSA)** 를 수반했다.

**RSA 구조 (combined — 3종 동시 적용):**
- **환급형(refund)**: 약제 청구금액의 일정 비율을 제약사가 건보공단에 환급
- **초기치료 환급형(utilization, 초기치료비용 환급형)**: 초기 일정 기간 치료분을 제약사가 환급
- **총액제한형(expenditure cap)**: 예상 청구금액 총액(cap) 초과분의 일정 비율을 제약사가 환급

표시가는 유지(120mg 약 18만2,696원 / 400mg 약 60만8,988원)하고 환급률로 실질가를 조정하는 구조다. 대상환자 약 835명, 연 예상 재정소요액 약 88억3천만원으로 보도됐다. (출처: 뉴스더보이스헬스케어 2020-12-29 — 윈도우 내)

**환자/적응증 제한 (사후관리 기준):**
- 표준요법(스테로이드·항말라리아약·면역억제제)으로 3개월 이상 치료 중인 **자가항체 양성 활동성 SLE 만 18세 이상 성인**
- 추가조건 모두 충족: **SELENA-SLEDAI 10 이상 + 항dsDNA항체 양성 + 낮은 보체(C3 또는 C4)**
- **중증 활성 중추신경계 SLE, 중증 활성 루푸스신염은 불인정(제외)**

**모니터링 (사후 평가 조건):**
- 최초 투약 후 **24주째 평가** — SELENA-SLEDAI 4 이상 감소 시 추가 6개월 인정
- **12개월째 평가** — 24주 결과 유지 시 추가 6개월 인정
- **최대 76주(약 18개월)** 까지 사용 인정

희귀질환 산정특례 적용으로 환자 본인부담은 약제비의 10% 수준이라고 보도됐다.

**확신도: High.** RSA 3종 구조는 윈도우 내(2020-12-29) 뉴스더보이스헬스케어 보도로, 급여기준/모니터링은 등재일(2021-02-01) 다수 매체 및 HIRA 약평위 회의자료로 교차 확인됨.
