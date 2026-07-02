# Policy Intelligence 큐레이션 채널 — 헤르메스 작업 가이드

헤르메스(GPT-5.5)는 전달받은 KRPIA 메일을 **분석해 사이드카를 커밋**한다.
기준 전문: `agents/rules/policy_intelligence_curation_rules.md`.

## 절차
1. 결정론 ingest 실행 → manifest 생성(라우팅은 규칙, 변경 금지).
2. 분석 대상 확인: `python -m agents.policy_analysis list-pending`
   → 각 event_id + expected_fingerprint.
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
