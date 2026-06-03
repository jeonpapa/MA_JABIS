# Rule Compliance 감사 — 2026-04-26 10:14

## 요약
- ✅ PASS: **8건**
- ❌ FAIL: **1건**
- ⏭ SKIP: **20건** (런타임 검증 불가)
- 전체 메모리: 29건

## ❌ FAIL — 즉시 확인 필요
- **Project: Price↔Approval coverage** — 1건 가격만 있고 허가 없음: 웰리렉 — `python -m agents.foreign_approval sync-from-prices` 실행 필요
  - 수치: `{'gaps': ['웰리렉']}`

## ✅ PASS — 실행 증명 확보
- **Feedback: Cache as permanent DB** — 영구 캐시 총 28 파일 (reason_cache=22, gov_summary=6)
- **Project: Indication-level Approval** — indications_master 99건 · Keytruda 66건 분해 유지
- **Project: MFDS Official Date Pipeline** — MFDS 40/40 (100%) 공식일 매핑
- **Feedback: MFDS pattern matching** — 8개 baseline 모두 일치
- **Project: Foreign scraper form_type** — form_type 47/47 (100%) 채움
- **Feedback: Foreign daily cost total_pkg_mg** — ₩10M/day 초과 0건 (sanity cap 유효)
- **Project: Comparator drug structure** — drug_latest ingredient 21937/67591 (32.5%) — 비교약제 필터 재가동
- **Project: Price change reason quality** — 최근 22건 중 n_refs=0 5건(23%), low 5건(23%)

## ⏭ SKIP — 런타임 신호 없음
- _User Profile: Joseph_ — 사용자 프로파일 (정적)
- _Project: Drug Price Dashboard_ — 프로젝트 프레임 (정적)
- _Feedback: Web deployment order_ — 개발 순서 원칙
- _Project: Workbench Pivot_ — 피벗 결정 (process state)
- _Project: Deployment Architecture_ — 배포 원칙 (개발 관행)
- _Project: TLS Remediation_ — TLS 환경 설정 (네트워크 런타임)
- _Project: KR 허가(MFDS) vs 급여(HIRA) 분리_ — 데이터 모델링 원칙 (정적)
- _Feedback: Auto-proceed with recommendations_ — 협업 스타일
- _Project: Readdy Mockup Migration (v2)_ — UI 마이그레이션 계획 (process)
- _Reference: Naver News API_ — 외부 시스템 참조
- _Reference: HIRA 항암 공고_ — 외부 시스템 참조
- _Project: Competitor+Mailing Plan_ — 프로세스 결정
- _Feedback: Daily Mailing 리플랜 대기_ — 사용자 대기 상태
- _Project: 급여 관리 admin 체크리스트_ — 수동 UI 체크리스트
- _Project: Competitor Trends 자동 크롤_ — 주 1회 크론 — 별도 모니터
- _Feedback: Micromedex session reuse_ — 개발 관행
- _Feedback: Verify rules actually fire_ — 이 에이전트 자체가 구현체
- _Project: RuleComplianceAgent_ — 자기 참조 (재귀 방지)
- _Project: health.kr primary source_ — 신규 메모리 — rule_compliance/checks.py 에 체크 함수 추가 검토
- _Feedback: RSA invisible pricing_ — 신규 메모리 — rule_compliance/checks.py 에 체크 함수 추가 검토
