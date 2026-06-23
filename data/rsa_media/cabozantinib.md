---
report_ids: [1341]
brand: 카보메틱스정
ingredient: cabozantinib
listing_date: 2019-02-01
window: 2018-12-03 ~ 2019-04-02
found: true
---

```json
{
  "found": true,
  "report_ids": [1341],
  "brand": "카보메틱스정",
  "ingredient": "cabozantinib",
  "listing_date": "2019-02-01",
  "window_from": "2018-12-03",
  "window_to": "2019-04-02",
  "rsa_types": ["refund", "utilization", "expenditure_cap", "combined"],
  "rsa_type_primary": "combined",
  "conditions": [
    "환급형(refund): 약제 청구금액의 일정 비율을 제약사(입센코리아)가 건보공단에 환급",
    "환자단위 치료기간 제한형(utilization): 환자당 계약된 일정 기간을 초과해 사용된 청구금액을 제약사가 건보공단에 전액 환급",
    "후환급형(expenditure_cap): RSA 계약기간 동안 발생한 실제 환급총액이 기대 환급총액에 미치지 못할 경우 그 차액만큼 제약사가 건보공단에 환급",
    "약가 상한금액 17만450원(20·40·60mg 동일)으로 등재"
  ],
  "monitoring": {
    "duration_months": 48,
    "metrics": ["청구금액(환급 정산)", "환자단위 치료기간 초과분", "기대 환급총액 대비 실제 환급총액 차액"],
    "review": "4년(최대 5년) 급여 적용 원칙. 계약 종료 1년 전부터 재계약을 위한 평가 및 협상 실시"
  },
  "patient_restrictions": {
    "indication": "이전에 VEGF(VEGFR) 표적요법 치료를 받은 적이 있는 진행성/전이성 신장세포암(신세포암)",
    "line": "2차 이상"
  },
  "confidence": "high",
  "sources": [
    {
      "title": "카보메틱스, RSA적용 (프라닥사 역전제 급여등재...카보메틱스는 RSA적용)",
      "url": "http://www.monews.co.kr/news/articleView.html?idxno=200657",
      "media": "메디칼업저버",
      "date": "2019-01-30"
    },
    {
      "title": "입센코리아 카보메틱스·한국아스텔라스 엑스탄디 '성공'",
      "url": "https://www.dailymedi.com/news/news_view.php?wr_id=839721",
      "media": "데일리메디",
      "date": "2019-01-31"
    },
    {
      "title": "카보메틱스, 17만450원 급여등재 결정",
      "url": "https://www.yakup.com/news/index.html?nid=226855&mode=view",
      "media": "약업신문",
      "date": "2019-01-30"
    }
  ]
}
```

## 한국어 요약

**카보메틱스정(성분 cabozantinib, 입센코리아)** 은 진행성 신장세포암(신세포암) 2차 이상 치료제로 **위험분담제(RSA)** 를 적용받아 2019년 2월 1일자로 급여 등재되었다(상한금액 20·40·60mg 모두 17만450원). 윈도우(2018-12-03 ~ 2019-04-02) 내 Tier1·전문지 보도(메디칼업저버 2019-01-30, 데일리메디 2019-01-31, 약업신문 2019-01-30)로 다음 사후조건이 확인된다.

- **RSA 유형(복합형, combined)**: ① **환급형** — 청구금액의 일정 비율을 제약사가 건보공단에 환급, ② **환자단위 치료기간 제한형(utilization)** — 환자당 계약 기간을 초과해 사용된 청구금액을 제약사가 전액 환급, ③ **후환급형(expenditure_cap 성격)** — 계약기간 중 실제 환급총액이 기대 환급총액에 못 미치면 그 차액을 제약사가 추가 환급.
- **환자/적응증 제한**: 이전에 VEGF(VEGFR) 표적요법(TKI)을 받은 적이 있는 진행성·전이성 신장세포암, 2차 이상 라인.
- **모니터링/재평가**: 4년(최대 5년) 급여 적용을 원칙으로 하며, 계약 종료 1년 전부터 재계약을 위한 평가 및 협상을 실시.

보험 적용 전 월 투약비용 약 530만원에서 적용 후 환자부담 약 25만원 수준으로 경감. RSA 유형 3종(환급형·환자단위 치료기간 제한형·후환급형)이 윈도우 내 보도(메디칼업저버 2019-01-30, 데일리메디 2019-01-31)로 명시되어 confidence: high.
