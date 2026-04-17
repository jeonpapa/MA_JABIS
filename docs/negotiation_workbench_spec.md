# MA Negotiation Workbench — 설계 스펙 v1.1

**프로젝트**: MA AI Dossier — 해외약가 대시보드 재설계
**상태**: Phase 1 MVP 스펙 + Tier-3 AI 교차검증 아키텍처 확정 (2026-04-16)
**의사결정자**: Joseph Kim (MSD Korea MA)
**패널**: GPT-5, Gemini 2.5-pro, Claude Opus 4.6

---

## 1. 제품 정의

> **"MA 담당자가 심평원 협상 시나리오를 빌드하고, 감사가능한 xlsx 워크북으로 export 하는 도구"**

- **JTBD (Job-to-be-done)**: 심평원 협상용 '제안 상한가' 근거 자료 생성
- **산출물 주/종**: **xlsx 파일 (주)** · 대시보드 (시뮬레이션·검증용 보조)
- **UX 정신모델**: Workflow-centric — 협상 시나리오 빌더 (Negotiation Scenario Builder)
- **핵심 원칙**: 모든 숫자는 **감사 가능** (출처·조정 로직·가정치 추적)
- **데이터 수집 패러다임**: 단순 스크래핑 → **AI 기반 다중 소스 교차검증** (Tier 별 차등)

---

## 2. xlsx 워크북 구조 (9 sheets)

파일명: `MA_A8_Workbench_{제품명}_{YYYYMMDD}_{버전}.xlsx`

### Sheet 1. [Cover] — 결재/제출용 한 장 요약
- 프로젝트명, 대상 제품 (성분명·상품명·제조사·ATC)
- 국내 기준 SKU, 협상 유형 (신규 등재 / 약가 조정 / 사용범위 확대)
- 선정 시나리오, 제안 상한가 (KRW), 산정 기준 (예: "JP × 90%")
- 작성자 / 작성일 / 버전, 데이터 최신성

### Sheet 2. [A8 Summary] — 국가별 조정가 비교
- 컬럼: 국가 · 원통화 · 현지약가 · 환율 · KRW 환산 · 공장도가(KRW) · 조정가(KRW) · 포함여부
- 통계: A8 평균 · 최저 · **최저×N%** · **평균×N%** (선택 가능)
- 시각화: 막대 차트

### Sheet 3. [Adjustment Logic] — 11컬럼 계산 과정
- 국가 · 자료원 · 현지약가 · **환율 (이전월 기준 36mo rolling)** · KRW 환산 · 공장도비율 · 공장도가(현지) · 공장도가(KRW) · VAT · VAT 적용 · 유통마진 · **조정가(KRW)**
- 수식 노출: 환율/비율 변경 시 재계산

### Sheet 4. [Source Raw] — 원천 증빙
- 국가 · 사이트 · URL · 조회일시 · 검색어 · 매칭 제품 ID · 원본 가격 · 통화 · 비고
- 심평원 검증 요청 시 출처 추적용

### Sheet 5. [Product Matching] — 🔑 데이터 일관성 검증
- **용도**: 심평원 심사가 아닌 **내부 품질 검증** (apples-to-apples)
- 컬럼: 국가 · 소스 · 추출 제품명 · 제형 · 강도 · Pack · 국내기준 매칭 · 일관성 등급
- 등급:
  - 🟢 **Exact**: 국내와 동일 SKU
  - 🟡 **Strength-equivalent**: 강도/포장 환산 필요
  - 🔴 **Mismatch**: 매칭 실패 (사용자 확인 필요)

### Sheet 6. [HTA Matrix] — 허가·급여 + **평가 근거** (Phase 2)
- 적응증별 국가별 매트릭스 (FDA·EMA·PBAC·CADTH·NICE·SMC · HAS · G-BA · CDF·AIFA)
- 급여 O/X/조건부
- **평가 근거 컬럼 (필수)**: 각 HTA 기관이 "어떻게 평가했는지" 요약 (임상 근거 · ICER · PAS 여부 · 제한 조건 · 조건부 사유)
- **Tier-3 AI 교차검증 소스별 신뢰도 배지**: 🟢 2+소스 합의 / 🟡 단일 소스 / 🔴 소스 충돌
- A8 가격형성 영향 메모 (예: "FR은 2L 급여 있음 → 가격 높음")
- **Phase 1**: 빈 시트 유지 / **Phase 2**: 실 데이터 + AI 교차검증 결과 채움

### Sheet 7. [Assumptions] — 가정치 명세 (별도 설정 화면 전용)
- **환율**: 기본값 = 이전월 기준 **36개월 rolling 평균** (KEB하나은행)
- **공장도비율**: 국가별, HIRA 고시값 기본
- **VAT**: 국가별
- **유통마진**: 국가별
- **제외 국가** + 사유
- **설정 화면에서 편집** (메인 워크벤치 화면에는 노출 X). 변경 시 Audit Log 기록

### Sheet 8. [Scenarios] — 🆕 시나리오 비교
- A안/B안/C안 병렬 컬럼
- 각 시나리오:
  - 참조 국가 (포함/제외)
  - 환율 기준 (36mo default / 24mo / 12mo / custom)
  - **제안가 공식**: `A8최저 × N%` **또는** `A8평균 × N%` 선택
  - A8 평균/최저 · 제안 상한가 · 전략 근거
- **최종 선택** 표시

### Sheet 9. [Audit Log] — 변경 이력
- 타임스탬프 · 사용자 · 변경 시트 · 항목 · 이전 값 · 신규 값 · 사유
- 모든 가정치 수정·시나리오 생성·국가 포함/제외 변경·**AI 교차검증 소스 선택 이력** 기록

---

## 3. 대시보드 (화면) ↔ 시트 매핑

### 메인 화면 = Negotiation Workbench
| 대시보드 구성요소 | 대응 시트 | 사용자 액션 |
|---|---|---|
| 제품 검색·매칭 확인 패널 | [Source Raw] + [Product Matching] | 매칭 신뢰도 확인, 수동 override |
| 국가 포함/제외 토글 | [Scenarios] | A/B/C안 저장·비교 |
| 조정가 테이블 | [Adjustment Logic] | 11컬럼 수식 검증 |
| 제안가 패널 | [A8 Summary] | 최저×N% / **평균×N%** 선택 슬라이더 |
| HTA 근거 드릴다운 (Phase 2) | [HTA Matrix] | 국가별 "어떻게 평가했나" 확인 |
| **Export 버튼** | 전체 워크북 생성 | 1-click xlsx 다운로드 |

### 별도 설정 화면 (Settings — 메인에서 분리)
| 구성요소 | 대응 시트 | 비고 |
|---|---|---|
| 환율 기본값 편집 (36mo rolling, 통화별) | [Assumptions] | 고시값 변경 대응 |
| 공장도비율·VAT·유통마진 테이블 | [Assumptions] | HIRA 고시 기본값 |
| AI 교차검증 소스 on/off | — | Gemini / Perplexity / OpenAI / Claude |
| 국가 탭 관리 | — | **US = Phase 2 placeholder 탭 유지** |

---

## 4. Tier-3 AI 교차검증 아키텍처 (신규)

### 4.1 패러다임 전환
- **Before (v1.0)**: 국가별 스크래퍼 → DB → 대시보드
- **After (v1.1)**: 국가별 스크래퍼 (가격) + **다중 LLM 독립 조사** (허가사항·급여·HTA) → 필드 단위 교차검증 → 신뢰도 등급 → 플래그

### 4.2 3 Tier 전략

| Tier | 대상 | 데이터 소스 | 검증 방식 |
|---|---|---|---|
| **Tier 1** | 해외 약가 | 국가별 공식 사이트 스크래핑 (JP·IT·FR·CH·UK·DE) | Playwright 기반, 전용 스크래퍼 유지 |
| **Tier 2** | 제품 매칭 일관성 | 스크래핑 결과 + AI 매칭 분석 | 🟢/🟡/🔴 신뢰도 등급 자동 부여 |
| **Tier 3** | **HTA · 급여 · 평가 근거** | **3~4 LLM 독립 조사** | 필드 단위 합의/충돌/단일 판정 |

### 4.3 Tier-3 소스 구성

| 소스 | 모델 | 특성 | 상태 (2026-04-16) |
|---|---|---|---|
| Gemini | `gemini-2.5-pro` + Google Search grounding | 실시간 웹 grounding, citation 반환 | ✅ 프로그래매틱 호출 가능 |
| Perplexity | `sonar-pro` | Native citations, 실시간 검색 | ⚠️ TLS 차단 (LibreSSL 2.8.3) |
| OpenAI | `gpt-5` | 학습 지식 baseline (웹 검색 없음, 독립 비교용) | ⚠️ TLS 차단 (LibreSSL 2.8.3) |
| Claude | `claude-opus-4-6` + WebSearch | 본 세션에서 가능 (2번째 소스 fallback) | ✅ 본 세션 경유 |

### 4.4 교차검증 파이프라인 (구현 완료)
- `agents/research/clients.py`: 3 LLM 통합 클라이언트 (동일 시그니처)
- `agents/research/cross_validator.py`: 병렬 질의 → JSON 파싱 → 필드 단위 매트릭스 → 합의/충돌/단일/누락 판정
- `scripts/tier3_pilot_keytruda_nice.py`: Keytruda × NICE TA 파일럿
- 필드별 상태: ✅ agree / ❌ conflict / ⚠️ single / ∅ missing
- 플래그: 단일 소스·충돌 건 자동으로 사용자 리뷰 대기열로

### 4.5 운영 제약 (Plan A — Python-only)
- 시스템 요건: Python 3.9 + Playwright Python + `openai` 패키지 (현재 시스템 충족)
- MCP / Node 도구 미사용 (Node.js·uv·Homebrew 미설치)
- TLS 제약: macOS 시스템 Python 의 LibreSSL 2.8.3 ↔ Cloudflare TLS 호환성 이슈로 OpenAI / Perplexity 프로그래매틱 호출 일시 차단
- **권장 복구 경로**: `brew install python@3.12` 또는 python.org 공식 installer → `openai` 재설치

---

## 5. 단계적 릴리즈 계획

### Phase 1 — Pricing Workbench (MVP)
**포함**: Sheet 1·2·3·4·5·7·8·9 (HTA Matrix 빈 시트)
**화면**:
- 메인 "Negotiation Workbench" (시나리오 빌더 중심)
- 별도 설정 화면 (Assumptions, 소스 on/off)
- **US 탭 = placeholder** (Phase 2에서 micromedex 로그인 해결 후 활성)
- 1-click xlsx export

**목표**: 협상 근거 자료가 xlsx로 1-click 추출됨

### Phase 2 — HTA Integration + US 활성
- Sheet 6 [HTA Matrix] 실 데이터
- **Tier-3 AI 교차검증 결과 임베드** (소스별 신뢰도 배지)
- 대시보드에 "어떻게 평가되었나" 드릴다운 레이어
- **US micromedex 스크래퍼 복구** (기존 로그인 이슈 해결)
- 협상 논리: "FR 가격이 높은 이유는 2L까지 급여되기 때문"

### Phase 3 — Advanced (선택)
- 경쟁 제품 동시 비교
- 가격 변동 알림 (Monitoring 기능 흡수)
- 과거 협상 성공률 기반 제안가 추천

---

## 6. 확정된 설계 결정

| # | 결정 | 확정 여부 | 버전 |
|---|---|---|---|
| 1 | JTBD = 심평원 협상 근거 생성 | ✅ | v1.0 |
| 2 | 산출물 주/종 = xlsx / 대시보드 | ✅ | v1.0 |
| 3 | UX = Workflow-centric (시나리오 빌더) | ✅ | v1.0 |
| 4 | 가격 + 허가 = 한 화면 통합 (Phase 2) | ✅ | v1.0 |
| 5 | Pack matching = 내부 일관성 검증 (심평원 심사 X) | ✅ | v1.0 |
| 6 | 가정치 = 편집 가능 (HIRA 고시 = 기본값) | ✅ | v1.0 |
| 7 | 시나리오 저장·비교 기능 필수 | ✅ | v1.0 |
| 8 | Phase 1 MVP: 가격만, HTA는 Phase 2 | ✅ | v1.0 |
| 9 | 협상 유형 = 신규 등재 / 약가 조정 / 사용범위 확대 | ✅ | v1.0 |
| 10 | **Assumptions 는 메인 화면 분리 → 별도 설정 화면** | ✅ | **v1.1** |
| 11 | **US 탭 = Phase 2 placeholder 유지** | ✅ | **v1.1** |
| 12 | **HTA 평가 근거 ("어떻게 평가했나") = 필수 컬럼** | ✅ | **v1.1** |
| 13 | **환율 기본값 = 이전월 기준 36개월 rolling** | ✅ | **v1.1** |
| 14 | **시나리오 공식 = 최저×N% + 평균×N% 선택 가능** | ✅ | **v1.1** |
| 15 | **데이터 수집 = 다중 LLM 교차검증 (Tier-3)** | ✅ | **v1.1** |
| 16 | **Plan A (Python-only) 채택 — Node/MCP 보류** | ✅ | **v1.1** |

---

## 7. 변경 이력

- **2026-04-16 v1.0**: 초기 스펙 (Joseph 승인) — 이전 설계 (11컬럼 조정가 테이블 중심) 전면 폐기, Workbench 모델로 피벗
- **2026-04-16 v1.1**: 추가 피드백 반영 — Assumptions 분리, US placeholder, HTA 평가 근거, FX 36mo rolling, 평균×N% 시나리오, 3-Tier AI 교차검증, Plan A 런타임 제약

---

## 8. 부속 산출물

| 파일 | 용도 |
|---|---|
| `scripts/generate_workbench_sample.py` | Keytruda 기반 xlsx 템플릿 샘플 생성 |
| `data/design_panel/MA_A8_Workbench_Keytruda_SAMPLE.xlsx` | 템플릿 샘플 (실 DB 데이터 반영) |
| `agents/research/clients.py` | 3 LLM 통합 클라이언트 (Gemini/Perplexity/OpenAI) |
| `agents/research/cross_validator.py` | 필드 단위 교차검증 오케스트레이터 |
| `scripts/tier3_pilot_keytruda_nice.py` | Tier-3 파일럿 (Keytruda × NICE TA) |
| `data/design_panel/tier3_pilot_result.json` | 파일럿 실행 결과 (매트릭스·플래그) |
| `data/design_panel/tier3_pilot_cross_check.md` | 2-source 교차검증 데모 리포트 |
