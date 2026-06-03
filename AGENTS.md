# AGENTS.md — MA_AI_Dossier 에이전트 가이드

> 모든 AI 에이전트(Claude Code · Codex · Gemini · Antigravity 등) 공용 진입점.
> 상세 규칙은 `agents/rules/*.md` 참조. 본 파일은 **인덱스 + 최소 원칙** 만 유지.
> 인계 매뉴얼은 본 파일 끝의 **§7 신규 에이전트 온보딩** 참조.

---

## 0. 환경 / 자격증명

- **작업 디렉토리**: `/Users/kimjeong-ae/MA_AI_Dossier`
- **자격증명**: `config/.env` (gitignored). 키 종류:
  - `OPENAI_API_KEY` · `PERPLEXITY_API_KEY` · `GEMINI_API_KEY`
  - `MFDS_PATENT_API_KEY` (식약처 공공데이터 API — 특허 + 허가정보 양쪽 공유)
  - `NAVER_API_CLIENT_ID/SECRET` (Naver News)
  - `MIMS_UK_*`, `MICROMEDEX_US_*`, `ROTE_LISTE_DE_*` (해외 약가 사이트 자격증명)
- **런타임**: Python 3.12, Node (Vite dev server, Tailwind), sqlite3
- **로컬 네트워크 주의**: WARP 필요 (api.openai.com / api.perplexity.ai 가 ISP SNI 차단되는 경우 있음). 자세한 정책은 `.agent-context/memory/project_tls_remediation.md`

## 1. 작업 컨텍스트 어디서 읽나

| 영역 | 위치 | 비고 |
|---|---|---|
| **공용 룰 (모든 에이전트)** | `CLAUDE.md` (102줄) + `agents/rules/*.md` (14 카테고리 1,770줄) | 본 AGENTS.md 와 정보 mirror, CLAUDE.md 가 더 상세 |
| **사용자 합의 메모리** | `.agent-context/memory/` (symlink → ~/.claude/projects/.../memory/) | 인덱스: `MEMORY.md`. 30+ 개별 .md 파일 |
| **현재 진행 plan** | `.agent-context/plans/pure-napping-goose.md` (symlink → ~/.claude/plans/) | 두 plan 섹션 — plan 1: 국가별 통합 구조, plan 2: 멀티에이전트 동기화 |
| **자동 생성 보고서** | `quality_guard/review_*.md` (매일 06:00 QG) · `quality_guard/compliance_*.md` (매일 05:30 RuleCompliance) | 회귀 / 룰 drift 감지 |
| **DB** | `data/db/drug_prices.db` (1.2 GB sqlite, 단일 source) | 루트 stub `drug_prices.db` 는 사용 안 함 (`/drug_prices.db` gitignore 차단) |

## 2. 에이전트 아키텍처

```
사용자 / 스케줄러
    │
    ▼
[OrchestratorAgent] ─── 요청 분석 · 룰 비교 · 작업 분배
    │
    ├─▶ [DomesticPriceAgent]       국내 약가 (HIRA)
    ├─▶ [HiraAgent]                급여 SOP 평가
    ├─▶ [ForeignPriceAgent]        해외 약가 (JP/IT/FR/CH/UK/DE/US/CA)
    ├─▶ [ForeignApprovalAgent]     FDA/EMA/PMDA/MFDS/MHRA/TGA 적응증 단위 허가
    │     └─▶ [KR MFDS 공식일 교체]  변경이력 diff — itemSeq 자동조회 + 캐시
    ├─▶ [DrugEnrichmentAgent]      성분·ATC·mechanism 보강 (MFDS API 1차)
    ├─▶ [MarketIntelligenceAgent]  뉴스·컨센서스 수집
    ├─▶ [ReviewAgent]              LLM 리뷰 (다수결)
    └─▶ [DashboardAgent]           HTML 대쉬보드 · 워크벤치 생성
    │
    ▼
[QualityGuardAgent]    상시 감시 + 일일 리뷰 + 회귀 탐지 + 개선 제안
[RuleComplianceAgent]  메모리 ↔ 런타임 증거 대조 (매일 05:30, QG 직전)
```

신규 자동화 (2026-04 추가):
- `reimbursement_xnational_sync_job` (분기 1회) — NICE/PBAC/CMS/CHUIKYO
- `foreign_price_backfill_job` (주 1회 월요일 03:00) — 모든 indications_master product

## 3. 규칙 맵 (권위 소스)

| 영역 | 파일 |
|------|------|
| Orchestrator / 작업 분배 | `agents/rules/orchestrator_rules.md` |
| Quality Guard (감시·리뷰·제안) | `agents/rules/quality_guard_rules.md` |
| Rule Compliance (메모리 ↔ 런타임 감사) | `agents/rules/rule_compliance_rules.md` |
| 스크레이퍼 공통 | `agents/rules/scraper_rules.md` |
| 국내 약가 (HIRA Excel) | `agents/rules/domestic_agent_rules.md` |
| 해외 약가 | `agents/rules/foreign_agent_rules.md` |
| HIRA 급여 SOP | `agents/rules/hira_agent_rules.md` |
| 해외 허가 (적응증 단위) + Cross-national reimbursement | `agents/rules/foreign_approval_agent_rules.md` |
| MFDS 공식 승인일 파이프라인 | `agents/rules/kr_mfds_approval_agent_rules.md` |
| MFDS 공공데이터 API 통합 (특허/허가) | `agents/rules/mfds_api_integration_rules.md` |
| 성분 enrichment | `agents/rules/drug_enrichment_rules.md` |
| Market Intelligence | `agents/rules/market_intelligence_rules.md` |
| Competitor Trends (주 1회 자동 크롤 + LLM 필터) | `agents/rules/competitor_trends_rules.md` |
| Review (LLM 다수결) | `agents/rules/review_agent_rules.md` |

## 4. 최소 원칙 (모든 에이전트 공통)

- **단방향 데이터 흐름**: 스크레이퍼 → DB → 대쉬보드. 대쉬보드는 DB만 읽음
- **적응증 단위 수집**: 허가는 브랜드 단위 금지. FDA 1.x / EMA 4.1 / MFDS 번호블록 sub-split 후 anchor(disease+LoT+stage+biomarker+combo+trial) 로 master 통합
- **데이터 출처 구분 필수**: MFDS `approval_date` 는 `date_source` 로 `mfds_official`/`unverified_estimate` 명시. 비급여는 `local_price=None` 명시
- **자격증명**: `config/.env` 외 어디에도 하드코딩 금지
- **LLM 판단 애매 시**: 단독 결정 대신 `ReviewAgent` 다수결
- **배포 순서**: 기능 완성 → 로컬 검증 → 웹 배포 (역순 금지)
- **Keytruda baseline**: 모든 신규 스크레이퍼/구조화 로직은 Keytruda 로 최종 검증

## 5. 절대 금지

- `msd_only=True` 하드코딩
- `config/.env` git 커밋
- 가격 없을 때 임의 값 반환
- US Micromedex AWP 를 local_price 로 사용 (WAC 만 허용 — factory_ratio 와 double-count)
- injection 에서 `total_mg/unit_mg` ratio 로 pack_count 추론 (농도×volume 이라 volume 반환 → 위험)
- injection 에서 `_extract_mg` (per-mL 농도) 를 daily_cost 분모로 사용 (per-vial 총량 O)
- 기능 미완성 상태에서 웹 배포
- MFDS 변경이력 매칭을 segment-blob / 단순 문자열 매칭으로 처리 (peri/adj/neo 붕괴)
- 허가 master 에 anchor 없이 brand+code 만으로 slug 생성

추가 도메인 금지:
- **Bridion 미래시제 분석** (KR-RULE-008) — 정상 단가 등재 약품, 정량 추정 X
- **단독품목 면제** 인지 못한 평균가 제시 (KR-RULE-007)
- **미공개 RSA 수치 / 내부 가격 카드** 추정 — `feedback_rsa_invisible_pricing.md` 참조

## 6. 과거 실수 (회귀 방지) — 핵심 5건

전체 이력은 `CLAUDE.md` 의 "과거 실수" 섹션. 본 AGENTS.md 는 핵심만:

1. **2026-04-17 MFDS NSCLC adj 오매칭** — segment-blob 매칭이 peri 문단을 adj 로 인식. `kr_mfds_approval_agent_rules.md` §8 패턴 룰 + QG 8개 baseline 자동 검증으로 회귀 차단
2. **2026-04-21 해외 일일투약비용 분모 오류** (Welireg UK ₩46M/day) — `_extract_mg` 가 단위강도(per-tablet 40mg)만 반환, tablet count(90) 무시 → 90× 과대. `_extract_total_pkg_mg` + sanity cap(₩10M/day)
3. **2026-04-22 injection 최소단위 오인식 (Keytruda IT/US pack_count 오류)** — `total_mg/unit_mg` ratio 가 injection 에서 volume 을 반환. **최소단위는 form_type 이 결정한다** (oral=tablet, injection=vial)
4. **2026-04-27 MFDS 공공 API 1차 권위 소스 통합** — 이전 enrichment(허가일/용법) 가 Perplexity 추정값 의존 → 식약처 공공데이터 API 두 endpoint(`DrugPrdtPrmsnInfoService07/getDrugPrdtPrmsnDtlInq06`, `MdcinPatentInfoService2/getMdcinPatentInfoList2`) 1차 소스. drug_enrichment 보조
5. **2026-04-27 해외 허가/급여/가격 통합 구조 신설** — `indication_id × country × body` axis. `reimbursement_xnational` + `product_alias_map` 신규. `query_name` ↔ `product` 브릿지 해소

## 7. 신규 에이전트 온보딩 체크리스트

새로운 에이전트(Codex / Gemini / Antigravity 등) 가 본 프로젝트로 처음 들어올 때:

1. **본 AGENTS.md 와 CLAUDE.md** 읽기 — 룰·아키텍처 파악
2. **`.agent-context/memory/MEMORY.md`** 읽기 — 사용자 합의 사항 30+ 항목 인덱스
3. **`.agent-context/plans/pure-napping-goose.md`** 읽기 — 현재 진행 plan (Plan 1: 국가별 통합 / Plan 2: 멀티에이전트 동기화)
4. **작업 영역 룰** 읽기: `agents/rules/<영역>_rules.md`
5. **최근 RuleCompliance 결과**: `quality_guard/compliance_YYYY-MM-DD.md` (가장 최근 파일)
6. **자격증명** 필요 시: `config/.env` 확인. 키 없으면 사용자에게 발급 요청 (특히 OPENAI / MFDS_PATENT / GEMINI)

작업 시작 전 sanity check:
- DB 정상: `sqlite3 data/db/drug_prices.db "SELECT COUNT(*) FROM indications_master;"` → 99
- API 서버 살아있나: `curl http://127.0.0.1:5001/api/health` → `{"status":"ok"}`
- Vite dev: `lsof -ti:3001` 또는 `:5173`

## 8. 작업 시 주의

- **양방향 sync**: 신규 메모리/plan 추가 시 `.agent-context/` symlink 가 직접 ~/.claude/projects/ 에 쓰인다. Claude Code 와 즉시 양방향 반영 — 충돌 주의
- **`.agent-context/` 는 gitignored** — 메모리/plan 은 사용자 개인 합의 (이메일·내부 결정) 포함. repo 에 commit 되면 안 됨
- **동시 편집 lockfile 없음** — 다른 에이전트가 작업 중이면 사용자에게 확인 후 진행
- **약명 영문 ↔ 한글 변형**: MFDS API 호출 시 `밀리그람 ↔ 밀리그램`, 괄호 prefix 축약 등 6단계 fallback. `agents/scrapers/kr_mfds_permit.py:_name_variants`
- **물질특허 LOE 판정**: PATENT_GB_CODE='물질*' core / 학술기관·ADC·biosimilar 후속은 secondary. PAGE_GB_NM 필터 금지 (트라스투주맙 = 제3자 제넨테크 보유 활성성분도 LOE 결정 포함)

## 9. 자주 쓰는 CLI

```bash
# 스케줄러 manual 실행
python scheduler.py --compliance-now      # RuleCompliance 감사
python scheduler.py --review-now          # QualityGuard 리뷰
python scheduler.py --approval-sync-now   # 허가↔가격 비대칭 sync
python scheduler.py --price-backfill-now  # 8개국 가격 백필
python scheduler.py --reimb-sync-now      # NICE/PBAC/CMS/CHUIKYO

# 단일 약 sync
python -m agents.foreign_approval.reimbursement_sync --product keytruda

# 식약처 API 직접 호출
python -m agents.scrapers.kr_mfds_patent "키트루다주" --refresh
python -m agents.scrapers.kr_mfds_permit "자누비아정100밀리그램"

# Frontend
cd data/dashboard_v2 && npm run dev   # → :3001 (또는 :3000)

# API 서버
python api/server.py                  # → :5001
```
