# Policy Intelligence 큐레이션 채널 — 헤르메스 작업 가이드

헤르메스(GPT-5.5)는 전달받은 KRPIA 메일을 **분석해 사이드카를 커밋**한다.
기준 전문: `agents/rules/policy_intelligence_curation_rules.md`.

## 절차
1. 결정론 ingest 실행 → manifest 생성(라우팅은 규칙, 변경 금지).
2. 분석 대상 확인 (**과거분 보호 하드 가드**): `python -m agents.policy_analysis list-pending --since 2026-07-01`
   → 컷오프 이후(포함) 이벤트만 반환. 각 event_id + received_utc + expected_fingerprint.
   - **원칙**: 기존에 로컬에서 구축한 항목(2026-06-29 파일럿 인제스트분)은 규칙값 그대로 유지하고
     **증분(새로 전달받은 메일)만** 큐레이션한다. `--since` 는 과거분을 대상에서 원천 제외한다.
   - **주의**: `received_utc` 는 원본 메일 발신일이 아니라 **ingest(포워딩 수신) 시점**이다. 따라서
     컷오프는 "마지막 기존 인제스트 다음 날"(현재 `2026-07-01`)로 잡는다. 새 배치 이후엔 그 배치일로 갱신.
3. 각 대상 이벤트의 본문(raw_folder/body.txt) + 첨부 추출텍스트(text_path)를 읽고,
   기준 문서(§2~§6)에 따라 `analysis/<event_id>.json` 작성.
   content_fingerprint 는 2번이 알려준 expected_fingerprint 를 그대로 넣는다.
4. 검증: `python -m agents.policy_analysis validate --file analysis/<event_id>.json`
   → `"ok": true` 여야 커밋. 경고가 있으면 수정.
5. (선택) ReviewAgent 다수결 게이트.
6. **AccessRoutineAnalystic `main` 에 커밋**: `policy_intelligence/analysis/<event_id>.json`
   + `policy_intelligence/analysis_manifest.json`(event_id→{fingerprint,analyzed_at,criteria_version}).

## 규칙
- 사이드카는 **비공개 repo(AccessRoutineAnalystic)에만** 커밋. evidence_quotes 에 메일
  본문 발췌가 있으므로 MA_JABIS(메인 repo) 커밋 절대 금지.
- 멱등: 같은 fingerprint 면 재작성 불필요. prod 가 02:10 + 부팅 시 자동 sync.
- 7개 topic 에 안 맞으면 대쉬보드 topic 을 임의 생성 금지 → data_gaps 에
  "new_topic_candidate: <제안명>" 만 기록.
