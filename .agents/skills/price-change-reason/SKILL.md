---
name: price-change-reason
description: 한국 약가 변동 사유를 4대 기전(적응증 확대·특허만료·사용량연동·실거래가)으로 분류하고, 변동 시점 ±6개월 윈도우 내 근거만으로 근거 리포트를 생성·검증할 때 사용. MA AI Dossier 프로젝트의 MarketIntelligenceAgent 워크플로 전반에 적용.
---

# Price Change Reason — 분석 & 검증 스킬

## 언제 이 스킬을 쓰는가

- 사용자가 특정 약제의 약가 인하/인상 사유를 물을 때
- MarketIntelligenceAgent, ReviewAgent, GeminiReviewer 관련 코드 수정
- [market_intelligence_rules.md](agents/rules/market_intelligence_rules.md) 룰 변경
- 대시보드의 "약가 변동 사유" 캐시/결과가 이상할 때 디버깅

## 핵심 불변식 (절대 지켜야 함)

1. **윈도우 절대성**: `change_date ± 6개월` (특허만료만 ±12개월) 밖 근거는 **무조건 제외**.
   허용 연도 집합 = `{window.from.year, window.to.year}`.
2. **`published_at` 필수**: 모든 `references[i]`에 `YYYY.MM.DD` 형식. 없으면 포함 금지.
3. **4대 기전만**: `indication_expansion | patent_expiration | volume_price | actual_transaction | unknown`.
4. **의심되면 unknown**: 근거 부족 시 `mechanism="unknown"`, `confidence="low"`, `references=[]`, `reason="추정: ..."` 접두.
5. **룰은 rules/*.md에만**: 코드에 하드코딩 금지. 프롬프트에 원문 주입.

## 파이프라인 (엔진 2단계 + 보강 + 검증)

```
analyze_price_change
 ├─ 1단계: Perplexity sonar-pro → JSON 결과
 │   └─ _deep_research_if_low → _enforce_rules
 │      └─ weak(refs≤1 또는 mechanism=unknown) → _augment_with_naver → _enforce_rules
 ├─ 2단계(폴백): _collect_news(Naver) + _openai_analyze(GPT-4o)
 │   └─ _enforce_rules → _deep_research_if_low → _enforce_rules
 └─ ReviewAgent.review_price_change_reason
     ├─ _mechanical_check (하드 규칙)
     └─ panel: OpenAI + GeminiReviewer → _merge_panel
         └─ 1명이라도 approve → approve (split 시 score ≤75)
         └─ 전원 reject → server 에서 "추정:" 접두로 강제 하향
```

## 자주 나오는 실수 & 대응

| 실수 | 원인 | 대응 |
|---|---|---|
| `\b20\d{2}\b`가 `2022년`을 못 잡음 | 한국어 문자가 word 경계에 영향 | `(?<!\d)(19\d{2}|20\d{2})(?!\d)` 사용 |
| Gemini 2.5-flash 빈 응답 | thinking 토큰이 출력 예산 소진 | `thinkingConfig.thinkingBudget=0` + `maxOutputTokens≥2048` |
| Perplexity citations 부실 (카테고리/PDF/깃헙) | 한국 의약뉴스 커버리지 약함 | `_augment_with_naver`로 보강 (윈도우 필터 후 GPT-4o 재분석) |
| OpenAI 리뷰어 false-positive reject | 보수적으로 윈도우 외 인용 오판 | 패널 합의를 "any approve + split" 로, mechanical blocker만 강제 거부 |
| .env에 키 추가 후 반영 안 됨 | `os.environ.setdefault` 는 없을 때만 set | 서버 재시작 필수 |
| Server 다운그레이드 후 reason 불일치 | mechanism=unknown인데 reason 단정 | [api/server.py](api/server.py)에서 `"추정: ..."` 로 재작성 |

## 테스트 체크 포인트

신규 스크레이퍼/룰 변경 후 반드시 돌려봐야 할 케이스:

- **레파타(에볼로쿠맙) 2020.01.01 −9.78%** → `indication_expansion` (2019.12 PCSK9 급여확대)
- **Keytruda(펨브롤리주맙)** — 프로젝트 표준 검증 약제
- **특허만료 케이스** — 윈도우 ±12개월 적용 확인

curl 예시:
```
curl -s "http://127.0.0.1:5001/api/domestic/change-reason?drug=레파타주프리필드펜&date=2020.01.01&ingredient=에볼로쿠맙&delta_pct=-9.78&refresh=1"
```

## 관련 파일

- [agents/market_intelligence_agent.py](agents/market_intelligence_agent.py) — 2단계 엔진, `_enforce_rules`, `_augment_with_naver`
- [agents/review_agent.py](agents/review_agent.py) — 패널 리뷰어 (OpenAI + Gemini 합의)
- [agents/gemini_reviewer.py](agents/gemini_reviewer.py) — REST 기반 Gemini 호출
- [agents/rules/market_intelligence_rules.md](agents/rules/market_intelligence_rules.md) — v3 룰 원문
- [api/server.py](api/server.py) — reject 시 "추정:" 다운그레이드 경로
- [data/dashboard/reason_cache/](data/dashboard/reason_cache/) — 결과 캐시 (refresh=1 로 무효화)

## 편집 가드

- 룰 수정은 **.md 파일만**. 코드에 복제/변형 금지.
- `_enforce_rules` 는 합성·보강 **직후마다** 재실행 (윈도우 외 연도가 reason에 재유입되는 것 차단).
- 패널 합의 규칙 변경 시 [review_agent.py:_merge_panel](agents/review_agent.py) 한 곳만 수정.
- 환경변수 추가 후 서버 재시작 필수.
