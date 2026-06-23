---
report_ids: [1252]
brand: 일라리스주사액
ingredient: canakinumab
listing_date: 2026-02-01
window: 2025-12-03 ~ 2026-04-02
found: false
---

```json
{
  "found": false,
  "report_ids": [1252],
  "brand": "일라리스주사액",
  "ingredient": "canakinumab",
  "listing_date": "2026-02-01",
  "window_from": "2025-12-03",
  "window_to": "2026-04-02",
  "rsa_types": [],
  "rsa_type_primary": null,
  "conditions": [],
  "monitoring": {
    "duration_months": null,
    "metrics": [],
    "review": null
  },
  "patient_restrictions": {
    "indication": null,
    "line": null
  },
  "confidence": "high",
  "sources": []
}
```

## 한국어 요약

**결론: found = false (검색 윈도우 2025-12-03 ~ 2026-04-02 내 근거 보도 없음)**

일라리스주사액(canakinumab, 한국노바티스)의 위험분담제(RSA)·사후관리 조건에 관한
Tier1 전문지 보도는 **모두 2024년 등재 사이클에 집중되어 있으며, 본 검색 윈도우
(2025-12-03 ~ 2026-04-02) 안에 해당하는 보도는 확인되지 않았다.** 따라서 룰("윈도우 내
보도만 근거, 추정/날조 금지, 근거 없으면 found:false")에 따라 found:false 로 기록한다.

### 앵커 일자 불일치 (사용자 확인 필요)
- 본 작업의 앵커 listing_date 는 **2026-02-01** 로 지정되어 있으나, 실제 보도상 일라리스의
  급여 등재(고시 적용)는 **2024-08-01** 이며, 약평위 통과는 **2024-02-01**(조건부) → 4월 재상정
  → 약가협상 신속 마무리 경로였다.
- 2026년 제1회 약평위(2026-01-15 결과 공개) 안건은 **다잘렉스피하주사·옴짜라정·누칼라
  오토인젝터주** 3종이었고, **일라리스는 포함되지 않았다.**
- 즉, 2026-02-01 앵커에 대응하는 일라리스 RSA 보도 자체가 존재하지 않는다(이벤트가 없음).

### 참고: 윈도우 밖에서 확인된 실제 RSA 구조 (근거로는 미채택, 정보 제공용)
아래는 **윈도우 밖(2024~2025년)** 보도이므로 위 JSON 근거(sources)로 채택하지 않았다.
2026-02-01 앵커 기준 분석에는 사용 불가하나, 제도적 사실관계 참고용으로만 기록한다.
- RSA 유형: **총액제한형(expenditure_cap)** — 연간 청구액 상한 초과 시 제약사가 건보공단에 환급
  (cap + refund 결합 성격).
- 근거자료 제출 조건: 환자별 관찰 2년, 1년 단위 관찰자료·연구결과 제출. 계약기간 만료 평가 시
  CAPS 중 NOMID/CINCA 적응증의 임상적 유용성·비용효과성 자료 제출.
- 경제성평가 유예 약제로 등재(국내 CAPS 환자 ~14명, 대체약제 부재 고려).
- 상한금액: 1,102만 9,769원(0.15g/1병).
- 적응증: CAPS(FCAS/FCUS·MWS·CINCA), TRAPS, FMF.
- 관련 보도(모두 윈도우 밖):
  - 히트뉴스 "캡도 씌우고 근거자료 제출 조건에 급여된 '일라리스'" (2024-07-31)
    http://www.hitnews.co.kr/news/articleView.html?idxno=56472
  - 히트뉴스 "약평위 재상정 '일라리스', 경제성 평가 유예 첫 약제 될까" (2024-04-04)
    https://www.hitnews.co.kr/news/articleView.html?idxno=53779
  - 데일리팜 "노바티스 극희귀병 신약 '일라리스', 종병 랜딩 확대" (2025-03-19)
    https://www.dailypharm.com/Users/News/NewsView.html?ID=321337
