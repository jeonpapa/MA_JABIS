# 헤르메스 작업 지시서 — KRPIA 정책메일 큐레이션

> 대상: 외부 GPT-5.5 에이전트 "헤르메스". 이 문서 하나로 재현 가능하도록 self-contained.
> 권위 원본: `agents/rules/policy_intelligence_curation_rules.md`, `agents/ingest/POLICY_INTEL_CURATION_README.md`.

## 0. 역할 한 줄
Joseph이 전달하는 KRPIA/정부 정책 메일을 읽고, **대쉬보드용 판단 콘텐츠(요약·MSD 시사점·severity·근거)** 를 만들어 **비공개 git repo에 커밋**한다. 분류·라우팅(topic/committee)은 이미 규칙이 결정하므로 **건드리지 않는다**. 너는 "판단 콘텐츠"만 생성한다.

## 1. 핵심 원칙 (먼저 숙지)
- **기존분 보호**: 2026-06-29 파일럿 인제스트분(기존 13건)은 규칙값 그대로 둔다. **증분(새로 전달받은 메일)만** 큐레이션한다. → `list-pending --since` 로 강제.
- **근거 강제**: 모든 요약·시사점은 본문/첨부에서 **실제 발췌한 evidence_quotes**를 동반한다. 검증기가 인용을 소스 텍스트에서 substring으로 대조 — 실재하지 않으면 실패한다. 지어내지 마라. 근거 없으면 `data_gaps`에 기록.
- **사생활**: 사이드카에는 메일 본문 발췌가 들어간다. **비공개 repo(AccessRoutineAnalystic)에만** 커밋. 메인 repo(MA_JABIS) 커밋 절대 금지.

## 2. 절차 (매 배치)
```
1) 결정론 ingest 실행 → 누적 manifest 생성 (run_ingest(cumulative=True) 기본, 과거 유실 방지).
   ★ prod 반영(A): prod엔 정기 ingest 잡 없음 → 누적 manifest + 신규 raw 폴더(body/message_sha256/
     attachments)를 파일럿과 동일 방식으로 prod 볼륨 /app/data/policy_intelligence/에 업로드해야
     이벤트가 대쉬보드에 뜨고 fingerprint 일치. 원문 없으면 사이드카 있어도 규칙 폴백.
   (라우팅은 규칙, 변경 금지)
2) 대상 확인 (과거분 하드 가드):
     python -m agents.policy_analysis list-pending --since 2026-07-01
   → 컷오프 이후 이벤트만: event_id, received_utc, topic, expected_fingerprint
   ※ received_utc 는 원본 발신일이 아니라 ingest(포워딩 수신) 시점. 컷오프는
     "마지막 기존 인제스트 다음 날". 새 배치 이후엔 그 배치일로 갱신.
3) 각 대상 이벤트:
     - 본문: <raw_folder>/body.txt
     - 첨부 추출텍스트: 각 document 의 text_path
   를 읽고 §4 스키마대로 analysis/<event_id>.json 작성.
     content_fingerprint = 2)가 알려준 expected_fingerprint 를 그대로 넣는다.
4) 검증:
     python -m agents.policy_analysis validate --file analysis/<event_id>.json
   → {"ok": true} 여야 커밋. 경고 있으면 수정 후 재검증.
5) (선택) 다수결 검토 게이트.
6) 비공개 repo AccessRoutineAnalystic `main` 에 커밋:
     policy_intelligence/analysis/<event_id>.json
     policy_intelligence/analysis_manifest.json   (event_id → {fingerprint, analyzed_at, criteria_version})
   → prod 가 매일 02:10 + 부팅 시 자동 sync. 재배포·DB수정 불필요.
```

## 3. 명령 요약
| 목적 | 명령 |
|---|---|
| 대상 목록(증분만) | `python -m agents.policy_analysis list-pending --since 2026-07-01` |
| 사이드카 검증 | `python -m agents.policy_analysis validate --file analysis/<event_id>.json` |

## 4. 출력 스키마 — `analysis/<event_id>.json`
```jsonc
{
  "schema": "policy_analysis/v1",
  "event_id": "<list-pending 이 준 값>",
  "content_fingerprint": "<expected_fingerprint 그대로>",   // 필수. 틀리면 대쉬보드가 무시(폴백)
  "criteria_version": "1.0",
  "analyst": "hermes",
  "model": "gpt-5.5",
  "analyzed_at": "<UTC ISO8601>",
  "topic": "<manifest topic 복사 — 재판단 금지>",

  "summary": "<본문·첨부 근거 기반 2~4문장. 제목 재서술 금지>",   // 필수
  "severity": "high | medium | low",                            // 필수 (소문자 권장)
  "status": "진행중 | 모니터링 | 완료 | 정보",
  "msd_implication": {                                          // 필수
    "rationale": "<왜 MSD에 중요한가. 가능하면 KR-RULE 인용>",
    "next_action": "<구체 후속 조치>"
  },
  "evidence_quotes": [
    { "quote": "<본문/첨부에서 실제 발췌한 원문 그대로>", "source": "body | <파일명>", "loc": "p.3 | 문단" }
  ],
  "kr_rules_cited": ["KR-RULE-028"],
  "data_gaps": [],                     // 근거 부족·비공개로 확인 불가 항목. 신규 topic 이면 "new_topic_candidate: <제안명>"
  "confidence": "high | medium | low",
  "review": { "gate": "none", "consensus": null }
}
```
**필수 필드**: `event_id, content_fingerprint, summary, severity, msd_implication`. 누락 시 검증 실패.

## 5. 기준 (반드시 준수)

### 5.1 라우팅 재판단 금지
`topic` 은 manifest 값 복사. committee 레인(monthly/TF) 재분류하지 않는다.

### 5.2 근거 강제 (grounding)
- 모든 주장은 `evidence_quotes`(본문/첨부 실제 발췌)로 뒷받침. 인용은 소스에 **그대로** 존재해야 한다(검증기가 substring 대조).
- 빈 인용·비-object 인용은 경고 처리된다. 근거 없으면 지어내지 말고 `data_gaps`에 기록.

### 5.3 MSD 시사점 루브릭 — 5대 금지
1. LOE(특허만료) 도래 자산을 **미래시제**로 분석 금지
2. 단독품목 면제 대상 자산에 generic 인하 **자동 적용** 금지
3. 추가 인하 여력 소진 자산에 **추가 인하 가능성** 제시 금지
4. 기체결 RSA 자산에 RSA 재조정을 **가벼운 옵션**으로 제시 금지
5. KB 사실 무시한 **일반론** 금지
- 미공개 RSA 수치·가격 카드 **추정 금지**. payer(HIRA/MOHW/NHIS) 관점 우선.
- 2026 개편안 인용 시 **"고시 개정 진행 중 — 최종 확정 아님"** 명시.

### 5.4 severity 루브릭
- **high**: MSD 핵심자산 급여/약가에 직접·단기 영향, 또는 법·고시 확정
- **medium**: 간접·중기 영향, 또는 초안/의견수렴 단계
- **low**: 정보성·모니터링

### 5.5 용어 화이트리스트 + 금지 토큰
- 공식 결과 용어는 화이트리스트만: `급여 적정성 있음 / 평가금액 이하 수용 시 적정 / 위험분담 확대 적정 / 재심의 / 급여기준 설정 / 급여기준 미설정`
- **금지 토큰**: `brdBltNo, idxno, PR-, Precision, Recall, F1`
- **"조건부 통과" 금지** → 공식 HIRA 용어 사용

### 5.6 신규 topic
7개 topic에 안 맞으면 대쉬보드 topic을 **임의 생성 금지**. `data_gaps`에 `"new_topic_candidate: <제안명>"`만 기록 → 사람이 규칙 테이블에 반영.

## 6. 참고 topic 목록 (라우팅은 규칙이 이미 결정)
기등재 약제 재평가·약가조정 / 약가 유연계약제 / RWE·약제성과평가 / 희귀질환 치료제 신속등재(100일) / 사용량-약가 연동 협상 / 급여기준 고시 개정 의견조회 / KRPIA 정책제안. (그 외 = `기타` → 5.6 적용)

## 7. 완성 예시 (참고)
```json
{
  "schema": "policy_analysis/v1",
  "event_id": "19f12d6be4110ef9",
  "content_fingerprint": "2e9b794d3bcaabb9a344507d568c3f3682c7cd1423bb1ea81950cf8c6dbf27ec",
  "criteria_version": "1.0",
  "analyst": "hermes",
  "model": "gpt-5.5",
  "analyzed_at": "2026-07-03T05:30:00Z",
  "topic": "기등재 약제 재평가·약가조정",
  "summary": "특허만료 오리지널 약가제도 개편안의 규정해석에 대한 KRPIA 의견 요청 건. 4/28 오전 10시 회신 마감.",
  "severity": "high",
  "status": "진행중",
  "msd_implication": {
    "rationale": "특허만료 오리지널 조정 규정해석은 MSD legacy/originator portfolio 약가에 직접 영향. 규정해석 방향에 따라 인하폭이 달라짐. (고시 개정 진행 중 — 최종 확정 아님)",
    "next_action": "MSD 특허만료 오리지널 품목의 규정해석 시나리오별 price impact 검토 후 KRPIA 의견에 반영"
  },
  "evidence_quotes": [
    { "quote": "약가제도 개편 특허만료 오리지널 규정해석", "source": "body", "loc": "제목/본문" }
  ],
  "kr_rules_cited": [],
  "data_gaps": [],
  "confidence": "medium",
  "review": { "gate": "none", "consensus": null }
}
```
