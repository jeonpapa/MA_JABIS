# 헤르메스 KRPIA 큐레이션 파이프라인 — 설계 (Design)

- **작성일**: 2026-07-02
- **작성**: Joseph + Claude (brainstorming)
- **상태**: 설계 확정 대기 → 구현 계획(writing-plans)
- **관련 메모리**: [[project_gov_policy_news_archive]], [[feedback_deploy_confirm_gate]], [[project_prod_data_deploy]], [[feedback_verify_rules_not_just_write]], [[feedback_auto_proceed]]

---

## 1. 배경 / 문제

Policy Intelligence(KRPIA) 대쉬보드는 현재 **결정론적 규칙만으로** 원본 이메일을 구조화한다. 전 파이프라인에 LLM·수기 판단이 없고, 세 층으로 나뉜다:

| 대쉬보드에 보이는 것 | 실제 출처 | 위치 |
|---|---|---|
| topic 분류 / committee 라우팅(monthly vs 4 TF) / discussed_topics | 순수 키워드·마커 규칙 | `classify_topic` `policy_intelligence_ingest.py:296`, `_committee_classify` `policy_intelligence.py:564`, `_topics_in_text` `:609` |
| severity / status / rationale / MSD 시사점 / why_it_matters | **손으로 박아둔 `TOPIC_RULES` 상수 테이블**을 topic 키로 조회 | `policy_intelligence.py:79-136` |
| current_summary / before / after / evidence_quotes | 메일 **제목(subject) 문자열의 기계적 파생** (본문 요약·인용 아님) | `_event_summary` `policy_intelligence.py:217` |

즉 새 KRPIA 메일이 들어오면 **내용과 무관하게 topic별 캔드(canned) 시사점 한 문장**이 붙고, 요약은 제목이다. 이것이 현재 품질의 실제 상한이다.

### 운영 시나리오 (사용자 확정)

1. Joseph이 KRPIA 관련 메일을 **헤르메스에게 전달**한다.
2. **헤르메스(외부 GPT 베이스 에이전트)**가 "지금 완성한 기준"으로 메일 본문·첨부를 실제로 읽어 **메일별 진짜 요약·MSD 시사점·severity**를 생성한다.
3. 헤르메스가 결과를 **비공개 Git 채널**에 커밋 → prod가 자동 취득 → **대쉬보드에 반영**.

목표는 "발송 품질"이 아니라, **신규 메일이 계속 유입돼도 이 큐레이션 품질이 흔들리지 않게 기준을 명문화·자동화해 헤르메스가 재현**하는 것.

---

## 2. 목표 / 비목표

### 목표
- 캔드 상수를 은퇴시키고, **내용 기반 LLM 큐레이션**(요약·MSD 시사점·severity·근거)으로 대체.
- 큐레이션이 **멱등·감사가능·graceful fallback**하도록 설계 → 품질 유지가 코드로 담보.
- 헤르메스가 **GPT 베이스**라 Claude 전용 스킬을 못 쓰는 점을 감안, 기준을 **self-contained 문서**로 제공(가드레일·루브릭·용어표를 repo 안에 임베드).

### 비목표
- 원본 이메일 인제스트(`build_dashboard_manifest`)의 결정론적 라우팅 로직 변경 — **손대지 않는다**.
- 아웃바운드 이메일 발송 — 이 설계와 무관.
- topic/committee 분류를 LLM으로 이관 — 회귀 리스크로 명시적 제외(접근 3 기각).

---

## 3. 아키텍처 — 접근 1 (분석 사이드카 + sha256 게이트 + 규칙 폴백)

```
[Joseph] KRPIA 메일 전달
    │
    ▼
[헤르메스 / GPT 에이전트]
  1) 기존 결정론적 ingest 실행 → manifest (분류·라우팅은 규칙 그대로)
  2) analysis 없는/stale 이벤트만 큐레이션:
       본문+첨부 텍스트 read → 기준 스펙 적용 → analysis/<event_id>.json
  3) (선택) ReviewAgent 다수결 게이트
  4) 비공개 Git 채널(AccessRoutineAnalystic)에 analysis/*.json + index 커밋
    │
    ▼  (prod 02:00 + 부팅 시 sha256 변경감지 멱등 sync — reimb 채널과 동일)
[prod 볼륨 /opt/data/policy_intelligence/analysis/]
    │
    ▼
[리더 policy_intelligence.py] event별:
   analysis 있고 fingerprint 일치 → LLM 값 사용 (curation_source="hermes")
   없거나 stale               → TOPIC_RULES 상수 폴백 (curation_source="rule_fallback")
    │
    ▼
[대쉬보드] 큐레이션/미처리 이벤트 구분 표시 + 커버리지 카운트
```

**핵심 불변식**: 원본 ingest는 결정론적이라 **헤르메스 환경과 prod 환경이 독립적으로 ingest해도 event_id·sha256이 동일**하다. 따라서 `content_fingerprint`가 두 환경에서 일치하며, 헤르메스는 **analysis 사이드카만** 커밋하면 된다(원본 manifest·raw 이메일을 git에 올릴 필요 없음).

### 왜 접근 1인가 (기각안 대비)
- **접근 2 (manifest 인플레이스)**: 재인제스트 시 분석이 덮여 소실, 결정론/LLM 산출 프로버넌스 혼재. 기각.
- **접근 3 (풀 LLM ingest)**: 분류까지 LLM화 → CLAUDE.md의 "키워드 커버리지 누락" 실수 계열이 변동으로 재발, 회귀 테스트·저렴한 폴백 상실. 기각.

---

## 4. 데이터 모델 — 분석 사이드카

**경로**: `data/policy_intelligence/analysis/<event_id>.json` (event_id = Gmail 메시지 ID → manifest 갱신에도 안정적으로 누적·재사용).

```jsonc
{
  "schema": "policy_analysis/v1",
  "event_id": "<gmail msg id>",
  "content_fingerprint": "sha256(body_sha256 + '|' + sorted(doc_sha256들).join('|'))",
  "criteria_version": "1.0",           // 어느 런북 버전이 생성했는지
  "analyst": "hermes",
  "model": "gpt-5.5",                  // 헤르메스 = GPT-5.5 베이스 에이전트
  "analyzed_at": "2026-07-02T04:00:00Z",

  "topic": "<manifest topic 복사 — 감사용, 재판단 금지>",
  "summary": "<본문·첨부 근거에 기반한 2~4문장. 제목 재서술 금지.>",
  "severity": "high|medium|low",
  "status": "진행중|모니터링|완료|정보",
  "msd_implication": {
    "rationale": "<왜 MSD에 중요한가. KR-RULE 인용.>",
    "next_action": "<구체 후속 조치>"
  },
  "evidence_quotes": [
    { "quote": "<본문/첨부에서 실제 발췌한 원문>", "source": "body|<파일명>", "loc": "p.3|para.2" }
  ],
  "kr_rules_cited": ["KR-RULE-028"],
  "data_gaps": ["<근거 부족·비공개로 확인 불가한 항목>"],
  "confidence": "high|medium|low",
  "review": { "gate": "none|passed", "consensus": null }   // 선택 게이트 결과
}
```

- `content_fingerprint`: 본문/첨부가 바뀌면 값이 달라져 **재분석 필요를 자동 감지**. 리더가 이 값으로 stale 판정.
- **index 파일** `analysis/analysis_manifest.json`: `{ event_id: {fingerprint, analyzed_at, criteria_version} }` 맵. sync·감사·커버리지 계산용(reimb의 `reports_manifest.json` 대응).

---

## 5. 리더 변경 (`policy_intelligence.py`)

- 신규 `_load_analysis(event_id, root)` — 사이드카 로드(없으면 None). 경로 traversal 가드 재사용(`_remap_private_path`).
- 신규 `_analysis_valid(analysis, event)` — manifest 이벤트의 body sha + doc sha들로 `content_fingerprint` 재계산해 대조. 불일치/부재 → invalid.
- `load_policy_intelligence` / `load_committee_workspace` 의 판단 필드 조립부에서:
  - valid analysis 존재 → `summary/severity/status/msd_implication/evidence_quotes`를 **analysis에서** 사용, `curation_source="hermes"`.
  - 아니면 → 기존 `TOPIC_RULES` 상수 폴백(현행 동작 유지), `curation_source="rule_fallback"`.
- `change_records.evidence_quotes`: valid analysis가 있으면 **실제 발췌**로 대체(현행 제목 파생 폐기), 없으면 현행 유지.
- **overview 확장**: `curated_event_count`, `pending_analysis_count`(analysis 부재/stale), `stale_analysis_count`. → 커버리지 가시화(조용한 캡 금지 원칙).
- 프론트: 이벤트/토픽 카드에 `curation_source` 뱃지(예: "AI 큐레이션" vs "규칙 기본값"), evidence_quotes에 source/loc 표시. overview 스탯 라인에 "큐레이션 N / 미처리 M".

---

## 6. 헤르메스 기준 스펙 (핵심 산출물)

헤르메스는 GPT 베이스라 Claude 스킬을 못 읽는다. 따라서 기준은 **repo 안의 self-contained 문서**로 제공하고, 헤르메스는 Git으로 이 파일들을 읽어 그대로 적용한다.

### 6a. `agents/rules/policy_intelligence_curation_rules.md` (권위 소스)
CLAUDE.md 규칙 맵에 등록. 내용:

1. **라우팅은 규칙, 재판단 금지** — topic·committee 레인은 manifest 값을 복사한다. LLM이 재분류하지 않는다.
2. **근거 강제 (grounding)** — 모든 `summary`·`msd_implication`은 `evidence_quotes`(본문/첨부에서 **실제 발췌**, source+loc)를 동반해야 한다. 근거 없는 주장 금지. 근거 부족 시 `data_gaps`에 기록하고 지어내지 않는다(RSA 리서치의 `found:false` 패턴).
3. **MSD 시사점 루브릭 (임베드)** — 아래 KR-RULE 요지와 severe-violation 5종을 문서 안에 직접 수록(헤르메스가 파일만 읽고 재현 가능하도록):
   - 5대 severe violation 금지: ① LOE 도래 자산 미래시제 분석 ② 단독품목 면제 자산에 generic 인하 자동적용 ③ 인하 여력 소진 자산에 추가 인하 가능성 ④ 기체결 RSA에 RSA 재조정을 가벼운 옵션으로 제시 ⑤ KB 사실 무시한 일반론.
   - 미공개 RSA 수치·가격 카드 추정 금지. payer(HIRA/MOHW/NHIS) 관점 우선.
   - 2026 개편안 인용 시 "고시 개정 진행 중 — 최종 확정 아님" 명시.
   - (전체 KR-RULE·자산 fact는 `korea-drug-pricing-system` 스킬이 원천이나, 헤르메스용으로 **요지를 이 문서에 발췌 수록**한다. 스킬 파일 자체는 repo 밖이라 참조 불가.)
4. **severity 루브릭** — high: MSD 핵심자산 급여/약가에 직접·단기 영향 또는 법·고시 확정. medium: 간접·중기 영향 또는 초안/의견수렴 단계. low: 정보성·모니터링.
5. **용어 화이트리스트 + 금지 토큰 (HIRA 트랙 이식, `render_hira_email_draft.py:49-114`)**:
   - 공식 결과 용어는 `ALLOWED_TERMS`만 사용(급여 적정성 있음 / 평가금액 이하 수용 시 적정 / 위험분담 확대 적정 / 재심의 / 급여기준 설정·미설정 등).
   - 금지 토큰: `brdBltNo, idxno, PR-, Precision, Recall, F1`, 그리고 **"조건부 통과"**(공식 용어 사용).
6. **출력 계약** — §4 스키마를 정확히 따른다. `content_fingerprint`·`criteria_version`·`analyst`·`model` 필수.

### 6b. `agents/ingest/POLICY_INTEL_CURATION_README.md` (헤르메스 작업 런북)
`REIMB_DATA_README.md`와 동일 형식의 운영 가이드:
- 입력: 전달받은 KRPIA 메일(Gmail 계정).
- 절차: ① 결정론 ingest 실행 → manifest ② `analysis_manifest.json`과 대조해 **분석 없는/stale 이벤트만** 큐레이션 ③ §6a 규칙 적용해 `analysis/<event_id>.json` 작성 ④ (선택) 리뷰 게이트 ⑤ `analysis/*.json` + `analysis_manifest.json`을 **AccessRoutineAnalystic `main`에 커밋**.
- 멱등: 같은 fingerprint면 재작성 불필요. 재배포·DB 직접수정 불필요(prod가 자동 sync).

---

## 7. 발행 / 동기화 경로 (비공개 Git 채널)

- **채널**: `jeonpapa/AccessRoutineAnalystic` (private), 브랜치 `main` — reimb와 동일 repo/패턴.
- **경로**: repo 내 `policy_intelligence/analysis/<event_id>.json` + `policy_intelligence/analysis_manifest.json`.
- **prod sync**: 기존 `reimb_data_sync` 계열과 동일하게 **02:00(KST) + 부팅 시** git raw fetch → sha256 변경감지 → `/opt/data/policy_intelligence/analysis/`에 멱등 반영. 신규 잡 `policy_intel_analysis_sync` 추가(또는 기존 sync에 트랙 추가).
- **사생활**: `data/policy_intelligence/`는 **메인 repo(MA_JABIS)에서 gitignore**(`.gitignore:34`). analysis는 evidence_quotes에 메일 본문 발췌를 포함하므로 **반드시 비공개 repo에만** 커밋한다(MA_JABIS 절대 금지). 비공개 repo는 이미 reimb 민감데이터를 호스팅 중이라 민감도 일관.
- 원본 이메일 본문/첨부는 기존 Gmail ingest 경로로 prod 볼륨에 존재(변경 없음). analysis는 순수 additive 메타.

---

## 8. 신규 topic 확장 (회귀 방지)

- 7개 topic에 안 맞는 메일은 헤르메스가 **임의로 대쉬보드 topic을 만들지 않는다.** `data_gaps`에 `"new_topic_candidate: <제안명>"`만 기록.
- 사람(Joseph/Claude)이 검토 후 `TOPIC_KEYWORDS`(`policy_intelligence_ingest.py:53` + `policy_intelligence.py:553`)·`TOPIC_RULES`(`:79`) 권위 테이블에 추가. CLAUDE.md "신규 추가 시 dict 전량 비교" 규율.
- RuleCompliance `checks.py`에 체크 등록: (a) 큐레이션 이벤트의 evidence_quotes가 소스 텍스트에 실재(substring), (b) `pending_analysis_count` 보고, (c) `new_topic_candidate` 미해결 건 알림. ([[project_rule_compliance_agent]] — 신규 메모리·규칙은 checks.py 등록 필수.)

---

## 9. 선택 레이어 — ReviewAgent 다수결 게이트

- 기본 **off**. 활성 시 헤르메스가 초안 analysis를 `ReviewAgent`(OpenAI gpt-4o + Gemini 패널, `agents/review_agent.py`)로 다수결 검토 후 publish.
- `review.gate="passed"` + `consensus` 기록. reject면 커밋 보류하고 `data_gaps`에 사유.
- 메모리 [[feedback_auto_proceed]]("애매하면 LLM 리뷰어 다수결") 선호와 정합. 필요 시 topic·severity별로 선택 활성.

---

## 10. 검증 (로컬)

1. **grounding 자동 체크**: 각 `evidence_quotes[].quote`가 해당 source(body 또는 파일 text_path)에 substring으로 실재하는지 스크립트 검증. 불일치 = 실패.
2. **fingerprint 정합**: 헤르메스 산출 fingerprint == 리더 재계산 fingerprint. 불일치 시 stale 처리 확인.
3. **폴백**: analysis 삭제 → 대쉬보드가 TOPIC_RULES 폴백으로 안 깨짐(`curation_source="rule_fallback"`). sha 손상 → pending 상태 표시.
4. **커버리지 가시화**: overview `curated/pending/stale` 카운트가 실제와 일치.
5. **용어 가드**: 금지 토큰/비화이트리스트 결과 용어 포함 시 검증 실패.
6. 프론트 build + API가 신규 필드 반환.

---

## 11. 리스크

- **(고) evidence_quotes 사생활**: 메일 본문 발췌가 git에 올라감 → 반드시 비공개 repo만. 메인 repo 커밋 방지 가드(경로/CI 체크) 필요.
- **(중) GPT 산출 스키마 이탈**: 자유서술로 스키마 깨질 위험 → README에 JSON 계약 명시 + 리더가 필수필드 검증, 불량 analysis는 폴백 처리(대쉬보드 안 깨짐).
- **(중) 두 환경 ingest 비결정성**: 만약 ingest에 비결정 요소(예: 시각·정렬 불안정)가 있으면 fingerprint 불일치 → ingest의 body/doc sha 계산이 순수 내용 기반인지 확인 필요(설계 전제).
- **(중) 헤르메스 판단 품질**: 근거 강제·용어 가드·(선택)리뷰 게이트로 통제. found:false 정직성 프롬프트 엄수.
- **(저) 신규 파일/잡/스키마**: RuleCompliance 등록으로 drift 방지.

---

## 12. 미해결 / 후속 결정

- prod sync를 기존 `reimb_data_sync` 잡에 트랙 추가할지 vs 신규 `policy_intel_analysis_sync` 잡 신설할지 (구현 단계에서 스케줄러 구조 보고 결정).
- `criteria_version` 올릴 때 기존 analysis 일괄 재분석 트리거 정책(현재는 fingerprint만 stale 기준; 버전 bump도 stale로 볼지).
- 프론트 뱃지·evidence 표시의 정확한 UI 형태(구현 시 확정).
