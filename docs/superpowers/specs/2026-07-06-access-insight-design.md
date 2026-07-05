# Access Insight — 급여 journey 누적 관찰 뷰 — 설계 (Phase 4)

- **작성일**: 2026-07-06
- **상위 계획**: `.claude/plans/streamed-sniffing-salamander.md` Phase 4
- **관련**: [[project_analog_committee_timeline]], hira-pipeline-tracker 스킬, [[project_hermes_krpia_curation]]

## Context / 가설
약제의 '신규 급여'·'급여확대'는 임상효과뿐 아니라 **국회·복지부·환자단체·의료진단체 engage 등 미디어 공개 활동**으로 급여환경을 조성한다. 이 활동의 **1년 journey를 약제축으로 누적 관찰**하면 — 암질심/약평위 **직전 특정 약제 관련 뉴스·활동이 많아지는 패턴** = 그 회사의 등재 노력이 크다 = **등재 가능성↑**. 흩어진 사실(뉴스 · 위원회 일정 · 급여 이벤트)을 연결해 가능성·의미를 보고, 회사 의사결정에서 journey를 참고한다.

## 기반 (이미 존재 — 재사용)
- `amjilsim_media_signals`(스키마 존재·**적재 0건**): `drug_id`+`session_id`, `signal_type`(GOV_STATEMENT/PATIENT_PETITION/KOL_OPINION/IR_RELEASE/PRE_AGENDA_LEAK/RESULT_REPORT), `weight`, `crossref_count`, `published_at`, `source_verified`, media tier.
- `amjilsim_signature_lexicon`(신호어 가중치), `amjilsim_prediction_audit`(신호밀도→등재가능성 예측 vs 실제 TP/FP).
- `amjilsim_sessions`(위원회 일정·차수), `amjilsim_drug_queue_status`, `amjilsim_drugs`(brand_kr/ingredient_inn/product_slug/*_pass_date).
- `competitor_news`/`gov_policy_news`(brand·pub_date·365일 보존 미디어 아카이브), `product_alias_map`(brand_aliases_json ↔ product_slug ↔ inn = 연결 허브).
- `analog_reports`(허가→약평위→급여 lag), `indication_reimbursement`(급여 상태).
- UI 패턴: 아날로그 `Timeline`(analog-search/page.tsx:90), `DrugDetailModal` 타임라인, policy `topic_ledger`.

## 병목 (신규 구현)
사용자 확정: **풀 구현(신규 크롤러 포함)**. 단 구현 순서는 "기존 데이터로 즉시 동작 → 이후 신선 크롤러".

---

## 슬라이스 (구현 순서)

### S1 — 뉴스↔약제 매핑 + 기존 아카이브 signal 백필 (기반, 즉시 데이터)
- **뉴스↔약제 키**: `competitor_news`/`gov_policy_news`의 `brand` 문자열을 `product_alias_map.brand_aliases_json`로 매핑해 `product_slug`/`drug_id`(amjilsim_drugs) 부여. 신규 모듈 `agents/access_insight/link.py` (`resolve_drug(brand|text) -> drug_id?`).
- **signal 백필**: 기존 아카이브 기사를 `signal_type`으로 분류(gov_policy→GOV_STATEMENT 등, IR/보도자료 패턴, 환자단체/학회 키워드 = `amjilsim_signature_lexicon` 활용)해 `amjilsim_media_signals`로 적재(drug_id + 가장 가까운 예정 session_id). 신규 `agents/access_insight/backfill.py`.
- 산출: 실데이터 signal 수백 건. 테스트: 대표 약제 매핑·분류 정확도.

### S2 — 집계 지표 (가설의 핵심)
- `agents/access_insight/aggregate.py` — 약제별 **위원회(amjilsim_sessions) D-N 윈도우 내 signal 밀도·가중합**(weight×recency×tier). engage 유형별(GOV/PATIENT/KOL/IR) 분해. `momentum_score` 산출.
- `amjilsim_prediction_audit`에 (신호밀도→등재가능성) 예측 기록 → 이후 실제 결과와 대조 루프.
- API `GET /api/access-insight/drug/<slug>`(journey), `GET /api/access-insight/leaderboard`(momentum 상위).

### S3 — Overlay 타임라인 UI
- 약제 1건: **뉴스 밀도(막대) + engage 유형(색) + 위원회 차수(수직선) + 급여 마일스톤(허가/암질심/약평위/급여등재)**을 1년축에 합성. competitor-trends에 'Access Insight' 탭 + 약제 검색 → journey 뷰. 재사용: analog Timeline + DrugDetailModal + recharts.
- leaderboard: momentum 상위 약제 카드(위원회 임박 + 신호 급증).

### S4 — 소스 확장 (국회·환자단체·의료진)
- Phase 2 `news_keyword_factor`(gov_seed)에 국회·환자단체·학회 검색 seed 추가(admin 편집). `gov_policy_news` 크롤이 자동 수집 → S1 매핑·분류에 반영.

### S5 — 신선 신호 크롤러 (풀 구현 완성)
- `agents/amjilsim_tracker/crawlers/tier_a|b|d`(빈 stub) + `signal_extractor`(약물 거명·signal_type·lexicon) 구현 → `amjilsim_media_signals` 신규 INSERT. 스케줄러 잡(주기). base = `base_amjilsim_crawler.py:38-44`.

---

## 시퀀싱 / 위험
- 순서 **S1→S2→S3→S4→S5**. S1~S3로 기존 데이터 기반 Access Insight가 즉시 동작(가치 조기 실현), S4~S5로 소스·신선도 확장.
- 위험: signal_type 분류 정확도(휴리스틱+lexicon, LLM 보조 선택), 뉴스↔약제 오매칭(alias 정규화·제목 관련성 가드 재사용), 예측 과신(prediction_audit로 지속 검증, "가능성 신호"로만 표기 — 확정 예측 아님).
- 규칙: media는 official-source의 fallback(hira_access phase_1). 확정적 등재 예측 금지 — momentum은 참고 신호.

## 검증
- S1: 대표 약제(키트루다·경쟁사) signal 백필 건수·매핑 정확. S2: momentum_score 산출 + 위원회 전 증가 패턴. S3: overlay 렌더. 슬라이스별 pytest + 프론트 build.
