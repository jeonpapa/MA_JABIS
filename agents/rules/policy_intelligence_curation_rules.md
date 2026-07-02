# Policy Intelligence 큐레이션 규칙 (헤르메스 기준)

대상: KRPIA/정부 메일 → 대쉬보드 판단 콘텐츠(요약·MSD시사점·severity·근거).
집행: `agents/policy_analysis.py`(fingerprint·검증). 산출: `analysis/<event_id>.json`.
헤르메스 = 외부 GPT-5.5 에이전트. Claude 전용 스킬 접근 불가 → 이 문서만으로 재현 가능해야 함.

## 1. 라우팅은 규칙, 재판단 금지
topic·committee 레인은 manifest 값을 복사한다. LLM이 재분류하지 않는다.

## 2. 근거 강제 (grounding)
모든 summary·msd_implication 은 evidence_quotes(본문/첨부에서 **실제 발췌**, source+loc)를
동반한다. 근거 없는 주장 금지. 근거 부족은 data_gaps 에 기록하고 지어내지 않는다.
(검증기 `validate_analysis` 가 인용을 소스 텍스트에서 substring 으로 확인 — 실재하지 않으면 실패.
빈 인용/비-object 인용도 경고 처리된다.)

## 3. MSD 시사점 루브릭 (5대 severe violation 금지)
- ① LOE 도래 자산 미래시제 분석 금지
- ② 단독품목 면제 자산에 generic 인하 자동적용 금지
- ③ 인하 여력 소진 자산에 추가 인하 가능성 제시 금지
- ④ 기체결 RSA 자산에 RSA 재조정을 가벼운 옵션으로 제시 금지
- ⑤ KB 사실 무시한 일반론 금지
- 미공개 RSA 수치·가격 카드 추정 금지. payer(HIRA/MOHW/NHIS) 관점 우선.
- 2026 개편안 인용 시 "고시 개정 진행 중 — 최종 확정 아님" 명시.
- 가능하면 KR-RULE 번호 인용(kr_rules_cited). 요지는 부록 A.

## 4. severity 루브릭
- high: MSD 핵심자산 급여/약가에 직접·단기 영향, 또는 법·고시 확정
- medium: 간접·중기 영향, 또는 초안/의견수렴 단계
- low: 정보성·모니터링

## 5. 용어 화이트리스트 + 금지 토큰
- 공식 결과 용어는 화이트리스트만: 급여 적정성 있음 / 평가금액 이하 수용 시 적정 /
  위험분담 확대 적정 / 재심의 / 급여기준 설정 / 급여기준 미설정
- 금지 토큰: brdBltNo, idxno, PR-, Precision, Recall, F1
- "조건부 통과" 금지 → 공식 HIRA 용어 사용

## 6. 출력 계약 (스키마)
`agents/policy_analysis.py` REQUIRED_FIELDS 준수: event_id, content_fingerprint,
summary, severity, msd_implication{rationale,next_action}. 권장: status, evidence_quotes[],
kr_rules_cited[], data_gaps[], confidence, analyst="hermes", model="gpt-5.5", analyzed_at.
content_fingerprint 는 `python -m agents.policy_analysis list-pending` 이 알려주는 값 사용.
severity 는 VALID_SEVERITY 집합 값만 (high/medium/low 권장).

## 부록 A. KR-RULE 요지 (헤르메스용 발췌)
(korea-drug-pricing-system 스킬 원천. 헤르메스는 스킬 접근 불가하므로 아래 요지를 사용.)
- KR-RULE-028 약가 유연계약제: 표시가 인상 가능한 유일 기전, 실제가 비공개.
- (신규 topic 등장 시 이 부록에 요지 추가.)
