# MA AI Dossier — 진입점

MSD Korea MA 팀의 국내·해외 약가·허가·HTA 모니터링 자동화 플랫폼.
상세 규칙은 각 `agents/rules/*.md` 참조. 이 파일은 **링크 맵 + 최소 원칙** 만 유지한다.

---

## 에이전트 아키텍처

```
사용자 / 스케줄러
    │
    ▼
[OrchestratorAgent] ─── 요청 분석 · 룰 비교 · 작업 분배
    │
    ├─▶ [DomesticPriceAgent]       국내 약가 (HIRA)
    ├─▶ [HiraAgent]                급여 SOP 평가
    ├─▶ [ForeignPriceAgent]        해외 약가 (JP/IT/FR/CH/UK/DE/US)
    ├─▶ [ForeignApprovalAgent]     FDA/EMA/PMDA/MFDS/MHRA/TGA 적응증 단위 허가
    │     └─▶ [KR MFDS 공식일 교체]  변경이력 diff — itemSeq 자동조회 + 캐시
    ├─▶ [DrugEnrichmentAgent]      성분·ATC·mechanism 보강
    ├─▶ [MarketIntelligenceAgent]  뉴스·컨센서스 수집
    ├─▶ [ReviewAgent]              LLM 리뷰 (다수결)
    └─▶ [DashboardAgent]           HTML 대쉬보드 · 워크벤치 생성
    │
    ▼
[QualityGuardAgent] ── 상시 감시 + 일일 리뷰 + 회귀 탐지 + 개선 제안
```

---

## 규칙 맵 (권위 소스)

| 영역 | 파일 |
|------|------|
| Orchestrator / 작업 분배 | `agents/rules/orchestrator_rules.md` |
| Quality Guard (감시·리뷰·제안) | `agents/rules/quality_guard_rules.md` |
| 스크레이퍼 공통 | `agents/rules/scraper_rules.md` |
| 국내 약가 (HIRA Excel) | `agents/rules/domestic_agent_rules.md` |
| 해외 약가 | `agents/rules/foreign_agent_rules.md` |
| HIRA 급여 SOP | `agents/rules/hira_agent_rules.md` |
| 해외 허가 (적응증 단위) | `agents/rules/foreign_approval_agent_rules.md` |
| MFDS 공식 승인일 파이프라인 | `agents/rules/kr_mfds_approval_agent_rules.md` |
| 성분 enrichment | `agents/rules/drug_enrichment_rules.md` |
| Market Intelligence | `agents/rules/market_intelligence_rules.md` |
| Competitor Trends (주 1회 자동 크롤 + LLM 필터) | `agents/rules/competitor_trends_rules.md` |
| Review (LLM 다수결) | `agents/rules/review_agent_rules.md` |

---

## 최소 원칙 (모든 에이전트 공통)

- **단방향 데이터 흐름**: 스크레이퍼 → DB → 대쉬보드. 대쉬보드는 DB만 읽음
- **적응증 단위 수집**: 허가는 브랜드 단위 금지. FDA 1.x / EMA 4.1 / MFDS 번호블록 sub-split 후 anchor(disease+LoT+stage+biomarker+combo+trial) 로 master 통합
- **데이터 출처 구분 필수**: MFDS `approval_date` 는 `date_source` 컬럼으로 `mfds_official`/`unverified_estimate` 명시. 비급여는 `local_price=None` 명시
- **자격증명**: `config/.env` 외 어디에도 하드코딩 금지
- **LLM 판단 애매 시**: 단독 결정 대신 `ReviewAgent` 다수결
- **배포 순서**: 기능 완성 → 로컬 검증 → 웹 배포 (역순 금지)
- **Keytruda baseline**: 모든 신규 스크레이퍼/구조화 로직은 Keytruda 로 최종 검증

---

## 절대 금지

- `msd_only=True` 하드코딩
- `config/.env` git 커밋
- 가격 없을 때 임의 값 반환
- 기능 미완성 상태에서 웹 배포
- MFDS 변경이력 매칭을 segment-blob / 단순 문자열 매칭으로 처리 (peri/adj/neo 붕괴)
- 허가 master 에 anchor 없이 brand+code 만으로 slug 생성

---

## 과거 실수 (회귀 방지)

- **2026-04-17 MFDS NSCLC adj 오매칭** (2023-12-19 → 2024-05-14): segment-blob 매칭이 peri 문단을 adj 로 인식. 이후 과도 exclude 로 Lynparza BC adj 회귀. 세부: `agents/rules/kr_mfds_approval_agent_rules.md` §8. 회귀 체크는 `QualityGuardAgent.review_codebase()` 가 8개 baseline 자동 검증.
- **MFDS 키워드 누락**: 신규 product 추가 시 `DISEASE_KR` 커버리지 미확인 → disease_layer 비어 매칭 0. 신규 product 추가 시 반드시 `indications_master.disease` 전량을 dict 와 비교.

---

## 감시 · 리뷰 · 제안 (QualityGuardAgent)

QualityGuard 는 사후 기록자가 아니라 **상시 감시자 + 제안자**.

- 파이프라인 실행 후 자동 트리거: 스키마 / 가격 이상값 / 환율 검증
- **매일 06:00 코드베이스 리뷰**: 규칙 위반 패턴 스캔 + MFDS 8개 baseline 회귀 체크 + 개선 제안 생성
- 결과: `quality_guard/review_YYYY-MM-DD.md` (사용자 확인용) + `deviation_log.jsonl` (기록)
- 상세: `agents/rules/quality_guard_rules.md`

신규 규칙 추가 / 대규모 변경 시 반드시 `QualityGuardAgent.review_codebase()` 수동 실행으로 회귀 확인.
