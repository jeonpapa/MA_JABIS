# Hermes(Key) 인계 — 2026-10-17

역할 분담 확정: **서버 크론 = 확실한 단순 잡**, **Aside = 해외약가 전담**(조사 요청 큐 + 원장 owner=aside), **Hermes = 그 외 판단·수집 전부**(원장 owner=hermes·server). 상세 매트릭스는 `docs/ops/OWNERSHIP.md`.

## 0. Aside 쪽 현황 (참고만, Hermes 조치 없음)
- `price_agent` 계정(`aside-price-agent@marketintel.kr`)으로 해외약가 요청 E2E 완료: KEYTRUDA·IT 요청 `d28a997a…` → AIFA DET 1185/2026 4행 → 화면 검증 → 결과 메일 → `completed`.
- 10분 루틴 활성: 요청 1건 → 없으면 owner=aside 원장 1건. Aside 과제(14·15·16)는 Hermes가 건드리지 않는다(서버도 409 OWNER_MISMATCH).
- 원장 14·15(JP/CH 서버 프록시 키 미설정)는 사용자 결정 대기로 blocked 처리.

## 1. 선행 조건 (사용자 조치)
| # | 내용 | 상태 |
|---|---|---|
| P1 | 로컬 브랜치 `ui/apple-design-p0-fixes`(owner 분리·디스패처 기본값·OWNERSHIP.md 포함)를 GitHub `main`에 병합·푸시 | 대기 — GitHub 토큰 필요. **푸시 전에는 VPS 디스패처(G4)를 설치하지 않는다** |
| P2 | `hermes-agent@marketintel.kr`(role=agent) 비밀번호를 VPS `.env`(600)의 `MA_AGENT_PASSWORD`로 직접 이동 | 대기 — 노트북 `config/.env` `HERMES_AGENT_PASSWORD`에만 있음. Slack·메일·커밋으로 전달 금지 |

## 2. Hermes 작업 (우선순위 순)
| # | 작업 | 완료 기준 |
|---|---|---|
| H1 | 두 저장소 push 권한 확인: `jeonpapa/AccessRoutineAnalystic`(2026-10-17 **비공개 전환**), `jeonpapa/kr-drug-pricing-nhi-finance-intelligence`. 기존 토큰이 비공개 전환 후에도 push 되는지 실제 테스트 | 두 저장소 각각 push 성공(또는 실패 사유·필요 권한 보고) |
| H2 (G1) | 정책 발언 모니터링 재개 — 2026-10-11 이후 런 커밋 없음. VPS 07:00/07:40 크론·로그 확인, 누락일 재생성·커밋 | `sv_monitoring_run` 최신 run_date ≥ 어제 |
| H3 (G3) | 감사 사전 스크립트 복구 — `/opt/data/scripts/stakeholder_audit_context.py` 부재. 소스 repo에 복구·커밋 후 크론 연결 | 감사 런이 매일 커밋(`audit_run_id`, `run_date_kst`) |
| H4 (G2) | KRPIA 정책 ingest 재개 — 2026-06-30 이후 정지(Gmail ingest 업로드 중단). 누적 ingest → prod 업로드 → 사이드카 큐레이션·커밋 | `latest_ingest_status.json ran_at` ≤ 21일, 신규 이벤트 화면 표시 |
| H5 (G4) | 디스패처 설치 (**P1·P2 후**) — VPS `.env`: `MA_API_BASE=https://ma-ai-dossier.fly.dev`, `MA_AGENT_EMAIL`, `MA_AGENT_PASSWORD`. `scripts/agent_dispatcher.py --dry-run --owner hermes,server` 결과 공유 → 승인 후 10분 크론(flock). `--owner aside` 금지 | `last_claimed_at` 24시간 이내, 대기 25건 정리 |
| H6 | 데이터 검증(원문 확보 후 보고만, DB 직접 수정 금지): ① 키트루다 요로상피암 1차 EV 병용 MFDS 허가일(현재 2024-07-25 언론 기준 추정) ② `keytruda_nsclc_2l_metastatic_pdl1_1_mono` MFDS 2017-09-23 ③ `lynparza_oc_1l_advanced_brca_mut_mono` MFDS 2019-10-29 ④ 키트루다 요로상피암 단독 급여 여부(HIRA 공고) | 건별 원문 URL·문서번호·원문 문장. 미확보는 미확보로 |
| H7 (G5) | 경쟁사 동향 큐레이션 입력 채널(파일·스키마) 제안 | 제안 1건 → 사용자 합의 |

## 3. 지켜야 할 규칙
- 프로덕션 DB 직접 쓰기·배포 금지. 원장은 API로만(claim → resolve, resolution에 커밋 SHA·근거).
- `raw_source='manual_correction'` 행(수동 정정)은 자동 수집으로 덮어쓰지 않는다.
- 미확인 값은 추정 대신 "미확보". 가격·날짜 임의값 금지.
- 비밀번호·토큰은 Slack/로그/커밋에 남기지 않는다.
- 완료·부분 완료 시 Slack 스레드에 항목 번호별로 회신.
