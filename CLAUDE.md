# MA AI Dossier — 약가 모니터링 대쉬보드 개발 규칙

## 프로젝트 개요

**목적**: MSD Korea MA(Market Access) 팀의 국내·해외 약가를 자동 수집·비교·모니터링하는 AI 기반 대쉬보드  
**사용자**: 글로벌 제약회사 Marketing & Market Access 담당자  
**기술 스택**: Python 3.9, Playwright, requests, Flask, SQLite, pandas, HTML/JS 대쉬보드

---

## 에이전트 아키텍처

```
사용자 요청
    │
    ▼
[OrchestratorAgent]  ← 요청 검토 · 룰 비교 · 작업 분배 (OpenAI GPT-4o)
    │
    ├─▶ [DomesticPriceAgent]   국내 약가 (HIRA)
    ├─▶ [ForeignPriceAgent]    해외 약가 (JP/IT/FR/CH/UK/DE/US)
    │       └─▶ [Scrapers]     국가별 스크레이퍼
    ├─▶ [DashboardAgent]       대쉬보드 생성·갱신
    ├─▶ [BackfillAgent]        과거 데이터 보완
    └─▶ [SchedulerAgent]       자동 스케줄링
    │
    ▼
[QualityGuardAgent]  ← 진행 상황 모니터링 · 편차 기록 · 보완
```

각 에이전트의 상세 규칙은 `agents/rules/*.md` 참조.

---

## 핵심 원칙

- **단방향 데이터 흐름**: 스크레이퍼 → DB → 대쉬보드. 대쉬보드는 DB만 읽음
- **용량별 가격 구분**: 동일 약제라도 용량/포장이 다르면 별도 레코드 (`dosage_strength` 필드)
- **비급여 처리**: `local_price=None`으로 명시. 임의 값 반환 금지
- **자격증명 관리**: `config/.env` 파일에만 저장. git 커밋 금지
- **Validation 기준**: Keytruda(pembrolizumab)로 신규 스크레이퍼 검증
- **배포 순서**: 기능 완성 → 로컬 검증 → 웹 배포 (역순 금지)

---

## 디렉터리 구조

```
MA_AI_Dossier/
├── CLAUDE.md                          # 이 파일 — 전체 프로젝트 진입점
├── config/
│   ├── .env                           # 자격증명 (git 제외)
│   └── foreign_credentials.json       # fallback 자격증명
├── agents/
│   ├── rules/                         # 에이전트별 상세 룰
│   │   ├── orchestrator_rules.md
│   │   ├── quality_guard_rules.md
│   │   ├── domestic_agent_rules.md
│   │   ├── foreign_agent_rules.md
│   │   └── scraper_rules.md
│   ├── orchestrator_agent.py
│   ├── quality_guard_agent.py
│   ├── domestic_price_agent.py
│   ├── foreign_price_agent.py
│   ├── exchange_rate.py
│   ├── db.py
│   └── scrapers/
│       ├── base.py
│       ├── jp_mhlw.py / it_aifa.py / fr_vidal.py
│       ├── ch_compendium.py / uk_mims.py / de_rote_liste.py
│       └── us_micromedex.py           # 예정
├── api/server.py                      # Flask REST API
├── data/
│   ├── raw/                           # HIRA Excel 원본
│   ├── foreign/                       # 국가별 캐시
│   └── db/drug_prices.db
├── dashboard/                         # HTML 대쉬보드
└── quality_guard/
    └── deviation_log.jsonl
```

---

## 개발 절차

1. **OrchestratorAgent** — 요청 분석, 기존 룰 충돌 검토, 작업 계획 수립
2. **개발** — BaseScraper 상속, DB 저장 형식 통일 (`agents/rules/scraper_rules.md` 준수)
3. **QualityGuardAgent** — 편차 감지·기록·보완
4. **Validation** — Keytruda로 신규 스크레이퍼 최종 확인

---

## 절대 금지

- `msd_only=True` 하드코딩 (모든 약제 검색 가능해야 함)
- `config/.env` git 커밋
- 가격 없을 때 임의 값 반환 (반드시 `None` 명시)
- 기능 미완성 상태에서 웹 배포 진행
