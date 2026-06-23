# DosingResolver 규칙 — 허가사항 용법용량 → 구조화 dosing

MFDS 허가사항 '용법용량' 자유문장을 투약비용 계산용 구조화 JSON 으로 변환한다.
한국 약가/MA 관점. **추측 금지** — 불확실하면 confidence='low' + notes 에 사유.

## 출력 JSON (이 키만, 다른 텍스트 금지)
```
{
  "schedule": "continuous" | "cycle" | "as_needed",
  "daily_dose_units": <number|null>,      // 1일 투여 단위 수(정/캡슐/바이알). 경구 정제에 주로
  "daily_dose_mg": <number|null>,         // 1일 총 투여 mg(주성분). 강도독립 환산용
  "cycle_days": <int|null>,               // cycle 일 때 주기 일수 (예: 매 3주 → 21)
  "doses_per_cycle": <number|null>,       // 1 주기당 투여 횟수(단위)
  "per_kg_mg": <number|null>,             // 체중기반 mg/kg (유지용량)
  "per_m2_mg": <number|null>,             // BSA기반 mg/m² (유지용량)
  "representative_indication": "<string|null>",  // 계산에 쓴 대표 적응증/용법 1개
  "alternatives": [ {"indication": "...", "schedule": "...", "daily_dose_mg": <n|null>,
                     "cycle_days": <n|null>, "doses_per_cycle": <n|null>,
                     "per_kg_mg": <n|null>, "per_m2_mg": <n|null>} ],
  "confidence": "high" | "medium" | "low",
  "notes": "<불확실/가정 사유 또는 빈 문자열>"
}
```

## 대표용법 선택 (다적응증)
- 허가사항에 적응증이 여러 개면 **대표 1개**를 골라 위 최상위 필드에 산출하고, 나머지는 `alternatives` 에 담는다.
- 대표 = **가장 일반적/표준 유지(maintenance) 용법**. 단일 고정용량(예: 200mg Q3W)이 있으면 그것을 우선.
- 초기부하(loading)와 유지(maintenance)가 다르면 **유지 용량**을 대표로(loading 은 alternatives 또는 notes).

## 스케줄 판정
- `continuous`: 매일 투여(예 "1일 1회", "1일 2회"). daily_dose_units/daily_dose_mg 채움.
- `cycle`: 주기 반복(예 "매 3주마다", "주 1회", "격일"). cycle_days + doses_per_cycle 채움.
- `as_needed`: 필요시/증상시 투여 → confidence='low', notes="필요시 투여 — 정기 치료비 산정 부적합".

## mg 환산 원칙
- 가능하면 `daily_dose_mg`(1일 총 mg)를 채운다. 강도가 다른 제품에도 환산 가능하게.
  - 예 "1회 100mg 1일 2회" → daily_dose_mg=200, daily_dose_units=2.
  - 예 "매 3주 200mg" → schedule=cycle, cycle_days=21, doses_per_cycle=1 (200mg 1바이알 가정 시 daily_dose_mg=200/21 은 산출하지 말 것 — cycle 은 cycle 필드로).
- 체중/BSA: per_kg_mg/per_m2_mg + (cycle_days 또는 interval) 채움. mg/kg·mg/m² 은 표준환자 환산을 하지 말고 **원값** 그대로(비용 계산측이 60kg/1.7m² 적용).

## 금지
- 허가사항에 없는 수치 창작 금지. 모호하면 null + confidence='low'.
- 효능·효과/주의사항 텍스트를 용법으로 오인 금지(용법용량 항목만).
