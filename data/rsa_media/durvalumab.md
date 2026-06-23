---
report_ids: [1255]
brand: 임핀지주
ingredient: durvalumab
listing_date: 2020-04-01
window: 2020-02-01 ~ 2020-05-31
found: true
---

```json
{
  "found": true,
  "report_ids": [1255],
  "brand": "임핀지주",
  "ingredient": "durvalumab",
  "listing_date": "2020-04-01",
  "window_from": "2020-02-01",
  "window_to": "2020-05-31",
  "rsa_types": ["refund", "expenditure_cap"],
  "rsa_type_primary": "combined",
  "conditions": [
    "청구금액의 일정비율 환급형(refund) 계약 체결",
    "예상 청구액(연간 약 220억원 추산) 초과 시 초과분의 일정비율 환급형(expenditure_cap) 계약 체결",
    "급여 인정기간을 타 면역항암제 최대 2년 기준이 아닌 임상근거(PACIFIC)에 따라 최대 1년(12개월)으로 제한",
    "치료 실패 시 고식적요법의 다른 면역항암제를 급여로 투여 불가"
  ],
  "monitoring": {
    "duration_months": 12,
    "metrics": ["연간 청구금액(예상 청구액 대비 실청구액)", "급여 인정기간 12개월 준수"],
    "review": "예상 청구액 초과 시 초과분 환급(총액제한형) — 청구실적 기반 사후 정산"
  },
  "patient_restrictions": {
    "indication": "절제 불가능한 국소진행성(3기) 비소세포폐암 중 PD-L1 발현율 1% 이상, 백금기반 동시적 항암화학방사선요법(CCRT) 2주기 이상 시행 후 42일(6주) 내 질병 미진행 환자에서 근치적 목적 투여",
    "line": "CCRT 후 공고요법(consolidation), 1차 유지 단계"
  },
  "confidence": "high",
  "sources": [
    {
      "title": "임핀지·벤클렉스타 4월 급여권 진입…건정심 통과",
      "url": "https://www.doctorsnews.co.kr/news/articleView.html?idxno=133939",
      "media": "의협신문",
      "date": "2020-03-19"
    },
    {
      "title": "'임핀지' 급여 신설…다른 면역항암제 기준도 변경",
      "url": "https://m.dailypharm.com/user/news/64492",
      "media": "데일리팜",
      "date": "2020-03-20"
    },
    {
      "title": "면역항암제 임핀지 '절제불가 3기 폐암' 4월부터 급여권",
      "url": "https://www.medicaltimes.com/Mobile/News/NewsView.html?ID=1132856",
      "media": "메디칼타임즈",
      "date": "2020-03-30"
    }
  ]
}
```

## 한국어 요약

**임핀지주(durvalumab, 한국아스트라제네카)** 는 2020년 4월 1일 절제 불가능한 국소진행성(3기) 비소세포폐암(NSCLC) 적응증으로 건강보험 급여에 신규 등재되었으며, **위험분담제(RSA)** 가 적용된 신약이다.

**RSA 구조 (combined):**
- **환급형(refund)** — 청구금액의 일정비율을 공단에 환급
- **총액제한형(expenditure_cap)** — 예상 청구액(연간 약 220억원 추산) 초과 시 초과분의 일정비율을 추가 환급

이 두 트랙을 병행하는 형태로, 한국에서 신약(면역항암제)에 적용되는 전형적인 환급형+총액제한형 결합 RSA 구조다.

**급여 일정:**
- 약제급여평가위원회(약평위) 통과: 2019년 11월
- 건강보험정책심의위원회(건정심) 서면심의: 2020년 3월 17~19일
- 의견조회 마감: 2020년 3월 24일
- 급여 시행: 2020년 4월 1일

**환자/적응증 제한 및 사후관리:**
- 대상: 절제 불가능한 3기 NSCLC 중 **PD-L1 발현율 ≥ 1%**, 백금기반 **CCRT 2주기 이상** 시행 후 42일(6주) 내 질병 미진행 환자
- 단계: CCRT 후 **공고요법(consolidation)** 의 근치적 목적 투여
- **급여 인정기간 최대 1년(12개월)** — 타 면역항암제 최대 2년 기준을 적용하지 않고 PACIFIC 임상근거에 따라 1년으로 제한
- 치료 실패 시 고식적요법의 다른 면역항암제를 급여로 투여 불가

**참고 약가(등재 시점):** 120mg 바이알 804,223원 / 500mg 바이알 3,350,930원 (데일리팜·의협신문 보도 기준).

> 주: PD-L1 발현율 조건은 메디칼타임즈·캔서앤서 등 다수 매체가 ≥1%로 보도했으나, 의협신문 건정심 기사는 PD-L1 요건 미명시로 보도. 급여기준 본문은 PD-L1 ≥1%(VENTANA 동반진단) 적용이 다수 견해.
