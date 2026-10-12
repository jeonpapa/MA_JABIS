# Stakeholder Verbatim 규칙 (인물·발언·모니터링 감사)

약가·건강보험 재정 분야 **정책 인물의 공개 발언**(Stakeholder & Verbatim Intelligence)을 대쉬보드로 이관하면서 적용하는 규칙.
소스 repo `jeonpapa/kr-drug-pricing-nhi-finance-intelligence` 의 운영 정책 2종(`MONITORING_SOURCE_AND_QUOTE_POLICY.md` mandatory-v1,
`MONITORING_AUDIT_AGENT_POLICY.md`)을 §2·§3 에 **문구 그대로** 옮기고, 대쉬보드 쪽 소유권·승인·인증·저작권·금지 규칙을 §1·§3-A~C·§4~§7 에 더한다.
구현 계획: `docs/superpowers/plans/2026-10-12-stakeholder-verbatim-migration.md`.

---

## 1. 범위 · 소유권

경계는 **"누가 쓰는가"** 로 나눈다. 같은 데이터를 두 곳에서 쓰지 않는다.

| 구분 | 대상 | 쓰는 주체 | 대쉬보드 처리 |
|------|------|-----------|---------------|
| 외부 에이전트 소유 (읽기 전용) | `sources/`(원문·추출텍스트·SHA-256), `source_manifest.json`, `monitoring-runs/`, `audit-runs/`, 정본 MD(타임라인·부서-인물지도 등), 관계도, 정책 문서 | 외부 에이전트(Key/Hermes, VPS)가 소스 repo 에 발행 | `stakeholder_data_sync` 잡이 git-sync 로 **내려받기만** 한다. 정본 MD 는 파서가 `origin='canonical_md'` 행만 교체 |
| 대쉬보드 소유 (DB 가 기록 원본) | `sv_person`, `sv_person_role_history`, `sv_source_domain`, `sv_audit_proposal`(+event) | 운영자(admin) 결정 | sync 는 **insert-if-absent 만**. 기존 행은 어떤 경우에도 UPDATE/덮어쓰기 하지 않는다 (승인·반려·효력일 보존) |
| 에이전트 → 대쉬보드 API | 모니터링 스콥 pull, 감사 제안 POST | `role='agent'` 서비스 계정 | `/api/agent/stakeholder/registry`·`/api/agent/stakeholder/proposals` 만 허용 |

- **insert-if-absent 규약**: 소스 repo 의 `people_registry.json`·`source_registry.json`·`audit_proposals.json` 은 최초 1회 import 시드이며, 이후 동일 키(`person_id`·`domain`·`proposal_id`)가 DB 에 있으면 소스 쪽 값은 무시한다. 결정(승인·직책 변경·상태)은 대쉬보드에서만 일어난다.
- 단방향 흐름: 외부 에이전트 산출물 → sync 잡 → DB/볼륨 → 대쉬보드. 대쉬보드 API 는 DB 만 읽는다. 파일 쓰기는 사진 업로드(볼륨 `photos/`) 하나뿐이다.
- 쓰기는 모두 한 sqlite 트랜잭션(`BEGIN IMMEDIATE`)으로 — 승인은 제안 상태 전이와 부수 행(출처 추가 / `role_history` scheduled / 신규 인물)을 한 트랜잭션에서 처리한다.

---

## 2. 원문 · 인용 규칙

> 출처: 소스 repo `MONITORING_SOURCE_AND_QUOTE_POLICY.md` (mandatory-v1). 아래 본문은 원문 그대로이며(절 안의 번호 `1.`~`7.`·`3.1`~`3.4` 는 **원문 번호**), 이 절의 변경은 소스 repo 정책 개정을 따라 일괄 교체한다.

- **상태:** mandatory-v1
- **시행:** 2026-10-09 07:00 KST 실행부터
- **적용대상:** 인물 레지스트리에서 실행일 기준 `status=active`, `effective_from<=실행일`인 전원
- **핵심 원칙:** 발언을 재작성하지 않고, 동일 행사에 대한 공개 보도를 출처별로 직접 열람하여 매체별로 추가 확인되는 발언을 합집합으로 보존한다.

### 1. 발언 원문 우선 규칙

1. 직접인용은 원문에 나타난 문구를 **그대로** 가져온다.
2. 맞춤법·문법·조사·숫자·문장부호를 임의 교정하지 않는다.
3. 문장 일부만 쓸 때는 생략 위치를 `[…]`로 표시하고 의미가 바뀌지 않도록 앞뒤 문맥을 함께 확인한다.
4. 영상·음성은 공식 자막 또는 사람이 확인한 전사를 우선하며, 타임스탬프를 기록한다. 자동자막만 있으면 그 한계를 표시한다.
5. 각 인용에는 다음을 필수로 붙인다.
   - 발언자와 행사 당시 직책
   - 행사명·행사일
   - 매체/기관명·게시일
   - 원문 URL
   - 기사 문단·페이지·영상 타임스탬프 등 `source_locator`
   - 증거등급
6. 직접인용이 없고 기자나 기관의 귀속요약만 있으면 `summary`에만 기록한다. 요약을 따옴표로 감싸거나 발언처럼 바꾸지 않는다.
7. 키의 정책·MA 인사이트는 `analysis`에만 기록하고 `verbatim`·`summary`와 시각적으로 분리한다.

### 2. 동일 행사 다매체 전수·차집합 규칙

1. 한 매체에서 행사를 발견하면 그 기사만으로 조사를 종료하지 않는다.
2. 아래 의무 출처군과 추가 발견 매체를 직접 검색해 동일 행사의 다른 보도를 찾는다.
3. 발견한 모든 접근 가능한 원문을 열고 다음을 비교한다.
   - 같은 발언이 동일하게 실린 부분
   - 다른 문장·수치·질의응답 등 **한 매체에만 추가로 실린 발언**
   - 직접인용과 기자 귀속요약의 차이
   - 발언자·직책·행사일·정책상태의 불일치
4. 동일 문구는 대표 원출처 한 건에 연결하고 다른 보도는 `corroborating_sources`로 남긴다.
5. 매체 A에만 있는 독립 발언은 `source_unique_quotes[A]`, 매체 B에만 있는 독립 발언은 `source_unique_quotes[B]`로 각각 보존한다.
6. 동일 통신기사 재전재나 문장 일치율이 높은 복제기사는 독립 증거로 합산하지 않고 `syndicated_copy`로 묶는다.
7. 기사 접근이 차단되거나 삭제됐으면 검색 스니펫으로 인용을 확정하지 않는다. `Discovery-only`와 접근 실패 사유를 기록한다.
8. 결과 보고에는 행사별로 `공통 확인 발언 / 매체별 추가 발언 / 불일치·한계`를 구분한다.

### 3. 직접검색 의무 출처

#### 3.1 정부·공공기관 — 매일 필수

- 보건복지부: `mohw.go.kr`
- 건강보험심사평가원: `hira.or.kr`
- 국민건강보험공단: `nhis.or.kr`
- 국회·국회회의록·정책자료: `assembly.go.kr`, `likms.assembly.go.kr`, `record.assembly.go.kr`, `ampos.nanet.go.kr`
- 법령·고시·행정예고 보조 확인: `law.go.kr`

기관 검색 페이지 또는 `site:도메인 인물명 행사명/주제어`를 사용하고 실제 원문 페이지를 연다.

#### 3.2 탑티어 종합·통신·경제 매체 — 관련 행사 발견 시 전수 확인

- 연합뉴스 `yna.co.kr`
- 뉴시스 `newsis.com`
- KBS `news.kbs.co.kr`
- MBC `imnews.imbc.com`
- SBS `news.sbs.co.kr`
- 조선일보 `chosun.com`
- 중앙일보 `joongang.co.kr`
- 동아일보 `donga.com`
- 한겨레 `hani.co.kr`
- 경향신문 `khan.co.kr`
- 한국경제 `hankyung.com`
- 매일경제 `mk.co.kr`
- 서울경제 `sedaily.com`
- 머니투데이 `mt.co.kr`

#### 3.3 보건의료·제약 전문 핵심 매체 — 매일 필수

- 히트뉴스 `hitnews.co.kr`
- 데일리팜 `dailypharm.com`
- 메디파나 `medipana.com`
- 뉴스더보이스 `newsthevoice.com`
- 약사공론 `kpanews.co.kr`
- 약업신문 `yakup.com`
- 메디칼타임즈 `medicaltimes.com`
- 데일리메디 `dailymedi.com`
- 메디게이트뉴스 `medigatenews.com`
- 의협신문 `doctorsnews.co.kr`

#### 3.4 추가 발견 출처

검색엔진·행사 주최기관·협회·학회·SNS는 후보 발견에 사용할 수 있다. 새 매체에서 관련 보도가 발견되면 도메인에 직접 들어가 원문을 열고 위와 같은 비교 규칙을 적용한다. 협회·기업·SNS 자료는 공식정부·독립언론과 동일 등급으로 승격하지 않는다.

### 4. 직접검색 증명 로그

각 실행은 `search_log`에 다음을 남긴다.

| 필드 | 의미 |
|---|---|
| `source_group` | official / top_tier_general / healthcare_trade / discovered_additional |
| `domain` | 직접 확인한 도메인 |
| `query` | 사용한 인물명·행사명·주제어 조합 |
| `method` | site search / internal search / archive search |
| `searched_at_kst` | 검색 시각 |
| `result_count_seen` | 확인 가능한 결과 수 |
| `opened_urls` | 실제로 연 원문 URL |
| `new_quote_count` | 해당 검색에서 새로 확인된 독립 발언 수 |
| `status` | checked / no_result / blocked / failed |

`search_log`가 없는 출처군은 검색 완료로 인정하지 않는다.

### 5. 행사별 출처 커버리지와 포화 기준

행사별 `coverage_matrix`에 모든 의무 출처와 발견 매체를 기록한다.

- `checked_no_result`: 직접 검색했지만 관련 보도 없음
- `opened_relevant`: 관련 원문 열람
- `duplicate_only`: 재전재·동일 발언만 확인
- `unique_quote_found`: 다른 독립 발언 확인
- `blocked`: 접근 제한
- `not_applicable`: 사유를 명시한 비적용

조사 완료는 다음을 모두 만족해야 한다.

1. 정부·공공기관 필수 도메인 확인
2. 보건의료 전문 핵심 매체 필수 도메인 확인
3. 행사 관련 탑티어 종합·통신·경제 매체 검색
4. 발견된 모든 관련 원문 열람 또는 접근실패 기록
5. 마지막 두 출처군에서 새 독립 발언이 더 나오지 않음

이를 `source_saturation.status=met`로 기록한다. 시간·접근 제한으로 충족하지 못하면 `partial`로 남기고 보고서에 미확보 범위를 명시한다. `partial`을 전수 확인 완료로 표현하지 않는다.

### 6. 행사별 결과 구조

```json
{
  "event_id": "...",
  "event_title": "...",
  "event_date": "YYYY-MM-DD",
  "publication_dates": ["YYYY-MM-DD"],
  "speakers": ["person_id"],
  "common_quotes": [],
  "source_unique_quotes": {
    "매체명": [
      {
        "quote_text": "원문 그대로",
        "speaker": "...",
        "source_url": "...",
        "source_locator": "본문 N문단",
        "evidence_grade": "C-quote"
      }
    ]
  },
  "attributed_summaries": [],
  "corroborating_sources": [],
  "syndicated_copies": [],
  "coverage_matrix": [],
  "source_saturation": {
    "status": "met|partial",
    "reason": "..."
  },
  "discrepancies": [],
  "analysis": {
    "insight": "원문과 분리된 해석",
    "confidence": "high|medium|low",
    "basis": []
  }
}
```

### 7. 최종 QC 게이트

- [ ] 직접인용이 원문과 글자 단위로 대조됐는가
- [ ] 요약을 인용문으로 바꾸지 않았는가
- [ ] 키의 인사이트가 사실·인용과 분리됐는가
- [ ] 동일 행사를 한 매체에서만 확인하고 종료하지 않았는가
- [ ] 매체별 추가 발언이 `source_unique_quotes`에 보존됐는가
- [ ] 재전재를 독립 증거로 중복 합산하지 않았는가
- [ ] 정부기관·탑티어·전문매체를 직접 검색한 로그가 있는가
- [ ] 원문 URL과 locator가 있는가
- [ ] 접근 실패와 미확보 범위를 명시했는가
- [ ] 출처 포화가 `met`인지, 아니면 `partial` 한계가 명시됐는가
- [ ] 검토안·향후 계획을 확정·시행 제도로 과장하지 않았는가

---

## 3. 감사 · 승인 규칙

> 출처: 소스 repo `MONITORING_AUDIT_AGENT_POLICY.md`. 아래 본문은 원문 그대로이다. 이어지는 §3-A~§3-C 가 대쉬보드 쪽 추가분이다.

- **역할명:** Monitoring Coverage Auditor
- **실행시점:** 일일 발언 모니터링 완료 후 매일 07:40 KST
- **목적:** 검색 출처의 공백, 모니터링 인물의 현재 직책 변동, 비등록 신규 발언자를 독립적으로 감사해 서비스 개선 제안을 만든다.
- **안전원칙:** 발견은 자동화하되 검증·승격·정본 변경은 근거 및 승인 게이트를 통과한다.

### 감사축 1 — 매체·출처 확장

다음을 확인한다.

1. 일일 결과의 `source_unique_quotes`에서 기존 승인 출처 외 도메인이 독립 발언을 제공했는가
2. 반복적으로 관련 행사를 단독·선행 취재하지만 현재 의무검색 목록에 없는 매체가 있는가
3. 공식 기관의 신규 하위도메인·자료실·회의록·RSS·영상 채널이 발견됐는가
4. 기존 출처가 폐쇄·이전·합병·리디렉션됐는가
5. 특정 출처군에서 계속 `partial`이 발생해 대체 또는 보조 출처가 필요한가

새 출처는 자동으로 `needs_review` 제안만 만든다. 승인 전에는 discovery-only이며 권위 출처나 의무검색 대상으로 승격하지 않는다. 공식 출처는 기관 소유를 확인할 수 있는 E3 이상 근거가 필요하다. 언론은 발행주체, 원문취재 여부, 최근 관련 기사 예시 2건을 요구한다.

### 감사축 2 — 현재 직책 변동

- 행사 당시 직책과 현재 직책을 분리한다.
- 보도기사 한 건만으로 현재 직책을 변경하지 않는다.
- 공식 인사발령, 조직도, 기관 보도자료, 공식 프로필 등 E3 이상의 근거를 요구한다.
- 발표일과 효력발생일을 별도 기록한다.
- 퇴직·교체·권한변동처럼 영향이 큰 변경은 가능한 경우 공식 근거 2건 또는 명시적 공식 인사기록 1건과 충돌검토를 요구한다.
- 승인되면 기존 직책을 `role_history`에 보존하고 새 현재 직책은 익일부터 적용한다.
- 과거 행사 발언의 당시 직책은 변경하지 않는다.

### 감사축 3 — 신규 관련 발언자

다음 조건이면 신규 인물 제안을 만든다.

- 약가·급여·약가협상·약품비·신약 접근성·RWE·성과평가·건보재정에 대해 귀속 가능한 직접발언을 함
- 정부·공공기관의 공식 행사, 국회, 공청회, 간담회, 기자설명회에서 실질 발언자로 확인됨
- 기존 모니터링 인물과 공동으로 등장하며 정책 의사결정·실행 역할이 확인됨

제안에는 발언 원문, 행사, 당시 직책, 기관, URL, locator, 증거등급, 기존 인물과의 관계를 포함한다. 승인된 신규 인물은 항상 `candidate`로 등록되고 익일부터 활성 모니터링한다. 공식 직책 근거가 별도 확인되기 전에는 `verified`로 자동 승격하지 않는다.

### 증거수준

- **E0:** 추론·검색 스니펫·AI 요약 — 후보 발견에만 사용
- **E1:** 단일 2차 보도 또는 날짜 불명 기관 페이지 — 우선순위 부여만 가능
- **E2:** 독립적인 신뢰매체 2건 또는 1차 문서를 연결한 우수 보도 — 제안 가능
- **E3:** 공식 인사자료·조직도·보도자료·회의록·공식 전사 — 직책 승인 최소기준
- **E4:** 독립 공식근거 2건 또는 인사발령과 현기관 공식페이지의 교차확인 — 고영향 변경 권장기준

재전재·동일 보도자료 복제는 독립 근거로 합산하지 않는다.

### 제안 공통 필드

`proposal_id`, `proposal_type`, `dedupe_key`, `status`, `priority`, `summary`, `rationale`, `proposed_change`, `evidence[]`, `conflicts[]`, `audit_run_id`, `created_at`, `updated_at`, `effective_from`, `reviewer`, `reviewed_at`, `decision_reason`.

증거에는 `url`, `publisher`, `source_class`, `evidence_level`, `publication_date`, `effective_date`, `quoted_text`, `locator`, `retrieved_at`, `content_hash`, `independence_group`을 둔다.

### 상태와 자동행위

- `needs_review`: 감사 에이전트가 생성
- `needs_evidence`: 근거 부족 또는 충돌
- `approved`: 운영자가 승인
- `scheduled`: 익일 반영 대기
- `applied`: 정본 반영 완료
- `rejected`: 사유와 함께 반려
- `superseded`: 더 최신 제안으로 대체

감사 에이전트가 할 수 있는 일: 후보 생성, URL 정규화, 중복 연결, 근거수준 분류, 우선순위 지정, 접근실패 기록.

감사 에이전트가 할 수 없는 일: 검증 인물 승격, 공식 직책 덮어쓰기, 권위 출처 승격, 인물 병합, 과거 발언 직책 수정, 기록 삭제, 자신의 제안 승인.

### 실패·저하모드

일일 모니터링이 실패·부분완료됐거나 레지스트리를 읽을 수 없으면 `degraded`로 실행한다. 이 경우 발견 후보만 만들고 상태 신뢰도를 높이거나 기존 정보를 변경하지 않는다. 타임아웃·로봇차단·유료벽은 기존 사실을 부정하는 근거로 사용하지 않는다.

### 일일 산출물

`audit-runs/YYYY-MM-DD/coverage_audit.json`과 `coverage_audit.md`에 다음을 기록한다.

1. 출처 확장 제안
2. 직책 변경 제안
3. 신규 발언자 제안
4. 충돌·접근실패
5. 운영상태와 감사 범위
6. 사람 검토가 필요한 우선순위 큐

변경이 없어도 확인한 모니터링 결과, 인물 수, 출처 수, 공식 직책 페이지 수, 발견 후보 수를 기록한다.

### 3-A. 대쉬보드 승인 불변식 (순수 함수 `agents/stakeholder/approval.py` 로 구현)

- 증거는 `publisher`·`locator` 가 **둘 다** 있어야 인정한다 (`has_evidence`).
- `source_expansion`: 공식 출처는 `source_class=='official'` + 증거수준 {E3, E4}, 그 외는 {E2, E3, E4}. 도메인(`normalize_domain`)은 경로·쿼리·fragment 를 거부하고, 근거 URL 의 hostname 과 일치해야 하며, 레지스트리에 이미 있는 도메인은 409.
- `role_change`: 공식 근거 {E3, E4} 필수, 같은 인물에 `scheduled` 직책 변경이 있으면 409, `role` 이 비어 있으면 422.
- `new_person`: 증거수준 {E2, E3, E4}, `name`·`organization`·`department`·`role` 전부 필요, 같은 기관 동명이인은 409, 등록은 **항상 `candidate`**.
- 공통: `needs_review`·`needs_evidence` 상태만 승인·반려 가능, 승인 효력(`effective_from`)은 **익일(KST)**. 동시 승인은 `UPDATE … WHERE status IN (…)` rowcount 0 → 409.
- **충돌별 확인 후 승인 (사용자 결정 2026-10-12, T2b)** — 소스 규칙("`conflicts` 가 비어 있지 않으면 무조건 422, 반려만")을 대체한다.
  - 규칙: `conflicts` 의 **모든 index** 를 admin 이 하나씩 사유(2~1000자, strip 후)와 함께 확인(`acknowledge_conflict`)해야 승인 게이트가 열린다. 하나라도 미확인이면 `422 CONFLICTS_UNACKNOWLEDGED`(메시지·`details.unacknowledged` 에 미확인 index). 검사 순서는 404 → 상태 409 → 충돌 확인 422 → 유형별 근거 규칙.
  - 확인은 충돌 게이트**만** 연다. 근거 불변식(E2/E3·공식 출처·도메인 일치·중복 409·대기 직책 409·필드 422)은 확인 여부와 무관하게 그대로 적용된다 — 예: 실데이터 `source_expansion`(E1 ×3)은 모든 충돌을 확인해도 `422 EVIDENCE`.
  - 기록: `sv_audit_conflict_ack`(proposal_id, conflict_index) 1행 = 확인 당시 `conflict_text` 스냅숏 + `reason` + `acknowledged_by` + `acknowledged_at`, 그리고 확인마다 `conflict_acknowledged` 이벤트(append-only). 스냅숏이 지금 그 index 의 문구와 다르면 그 확인은 **무효**(`stale`) — 확인 아래에서 충돌을 바꿔치기할 수 없다. 무효 확인은 재확인으로 교체되고(이벤트 추가), 유효한 확인은 덮어쓸 수 없다(`409 ALREADY_ACKNOWLEDGED`).
  - 열린 상태(`needs_review`·`needs_evidence`)에서만 확인 가능(아니면 `409 STATE`). 확인과 승인은 같은 `BEGIN IMMEDIATE` 로 직렬화되므로, 승인 트랜잭션이 읽은 시점에 빠진 확인이 있으면 승인은 422 이고 미확인 상태로 승인되는 경합은 없다. 반려는 충돌·확인을 보지 않는다(그대로).
  - 근거: 실제 감사 제안 6건 전부에 conflicts 가 있었고, 대부분이 모순이 아니라 검토 메모였다("pre-run context script 부재로 감사 상태 degraded", "직접발언은 … 매체 전사 수준으로 취급", "기사 게시일은 … 창 시작 전이므로 당일 실행 누락으로 분류하지 않음"). 소스 규칙대로면 어떤 제안도 승인할 수 없었다. 사용자는 충돌을 일괄 무시하지 않고 **충돌마다 사람이 읽고 사유를 남기는** 방식을 택했다 — 감사 에이전트가 적은 우려가 감사 기록에서 사라지지 않는다.
- 검증(`verified`) 인물의 소속·부서·직책은 PUT 으로 직접 수정할 수 없다(409). 직책 변경은 `role_change` 제안 경로로만 한다.
- 열린 `dedupe_key` 는 부분 유니크 인덱스로 1건만 허용한다 (에이전트 POST 는 중복 시 409).

### 3-B. 승인 이후 반영 (소스에 없던 `scheduled → applied` 단계)

- 승인 → `scheduled`(익일 효력) → 매일 sync 잡의 `apply_scheduled(today)` 가 `applied` 로 반영한다.
- `role_change` 적용: 현재 직책을 `sv_person_role_history(applied, effective_to=오늘)` 로 내리고 새 직책을 적용하며 `verification_status='verified'` 로 전환한다. 근거: 승인 자체가 공식 E3+ 근거를 요구하고(감사축 2 — 공식 인사자료가 직책 승인 최소기준), 후보 표기(`후보`·`candidate`)는 이 공식 근거로 해소된다. 과거 행사 발언의 당시 직책(`speaker_role_at_event`)은 바꾸지 않는다.
- `new_person` / `source_expansion`: 행이 승인 시점에 이미 만들어져 있으므로 제안 상태만 `applied` 로 바꾼다. 신규 인물은 `candidate` 로 시작하며 공식 직책 근거가 별도로 확인되기 전에는 `verified` 로 자동 승격하지 않는다.
- 반영 실패는 `application_failed` + 이벤트로 남기고 잡을 죽이지 않는다. `role_change` 반영 실패 시 그 제안의 `scheduled` 직책 이력 행은 `failed`(종결 상태)로 바꿔 대기를 해제한다 — 남겨 두면 그 인물의 이후 직책 변경이 `409 PENDING_ROLE_CHANGE` 로 영구히 막힌다. 실패 사실은 제안 상태·`application_failed` 이벤트(해제 행 수 포함)·`failed` 이력 행으로 계속 보인다. (`sv_person_role_history.status` 어휘: `scheduled|applied|superseded|failed`; 구 스키마 DB 는 `ensure_tables` 가 행·id 보존 재구성으로 마이그레이션.)

### 3-C. 감사 에이전트 권한 경계

- 하는 일: 후보 생성, URL 정규화, 중복 연결, 근거수준 분류, 우선순위 지정, 접근실패 기록.
- 못 하는 일(대쉬보드가 API 에서도 막는다): 검증 인물 승격, 공식 직책 덮어쓰기, 권위 출처 승격, 인물 병합, 과거 발언 직책 수정, 기록 삭제, **자신의 제안 승인**. 승인·반려는 `role='admin'` 만.
- 제안 POST 는 `role='agent'` 토큰만(기존 `X-Operator-Key`·무인증 로컬 POST 는 폐기).

---

## 4. 인증 · 접근 등급

### 4.1 인증 매핑

| 소스 서비스 | 대쉬보드 |
|---|---|
| 페이지·목록 GET (무인증) | `require_auth()` — 로그인 user·admin |
| `X-Operator-Key` 운영자 행위 (인물 CRUD·상태·사진, 승인·반려) | `require_auth(role="admin")`, `reviewer = request.user["sub"]` (이메일; 하드코딩 이름 금지) |
| 제안 POST (무인증, 루프백) | `require_auth(role="agent")` `/api/agent/stakeholder/proposals`. 같은 토큰으로 `GET /api/agent/stakeholder/registry`·`GET …/proposals?audit_run_id=` |
| 사진·관계도 정적 마운트 | 인증 라우트 (`/api/stakeholder/people/<id>/photo`, `/api/stakeholder/artifacts/relationship-map.svg`) — 정적 마운트·공개 라우트 금지 |

### 4.2 접근 등급

| 등급 | 볼 수 있는 것 |
|---|---|
| user | 인용문(verbatim)·귀속요약·해석·증거등급·locator·URL·SHA-256·`integrity_status`, **locator 창 발췌 ≤600자** (`GET /sources/<id>/excerpt?locator=`) |
| admin | 추가로 추출 전문(`/text`)·원본 다운로드(`/raw`)·인물/출처/제안 관리 |
| agent | `/api/agent/stakeholder/*` 한정 (스콥 pull·제안 POST). 그 외 라우트는 403 |

- 원문 전문·원본 HTML/HWP 는 볼륨(`data/stakeholder/`)에만 두고, 외부 공개 라우트는 없다 (모두 `require_auth`).
- provenance 사슬: `sv_quote.locator` → `sv_statement.source_ids_json` → `sv_source.sha256/text_sha256/url/last_live_check_json` → 볼륨 파일. sync 가 파일 SHA-256 을 재계산해 `integrity_status`(`verified|mismatch|missing|no_local`)를 쓰고, UI 의 "원문 SHA-256 일치" 배지는 `verified` 일 때만 켠다.
- 인용문은 파서가 추출 텍스트에 대한 정확 부분문자열 검사 결과(`locator_match`: `range|file|none`)를 남긴다. `none` 이 나오면 파서·원문 결함으로 본다.

---

## 5. 저작권 · 개인정보

- 언론 원문(HTML·추출 텍스트)의 저작권은 각 발행기관·언론사에 있다. 내부 검증과 provenance 보존 목적으로만 저장하며 (`sv_meta.copyright_notice`, UI 각주), 메인 repo(공개 취급)에 **커밋하지 않는다**. 테스트 픽스처(`tests/fixtures/stakeholder_src_mini/`)는 가짜 매체·가짜 인물·가짜 문장의 **합성 데이터**만 쓴다.
- 대상은 공직자의 **공개 직책·공개 발언**뿐이다. `sv_person` 은 이름·소속·부서·직책·별칭·주제·사진 경로만 가진다. 삭제 API 는 없다 (`paused` 로만 전환).
- 사진: admin 업로드만 허용, JPG/PNG/WEBP ≤5MB. Pillow 로 EXIF 회전 보정 · 640×640 크롭 · WEBP 재인코딩(메타데이터 제거) 후 `data/stakeholder/photos/<person_id>-<hex>.webp` 로 저장하고, 인증 라우트로만 서빙한다. 교체 시 구 파일은 삭제한다.
- **언론사 CDN `photo_url` 은 렌더링하지 않는다** (핫링크 금지). 레지스트리 메타로만 보존하고 admin 에게만 노출한다. 사진이 없으면 이니셜 아바타.
- 자격증명(`STAKEHOLDER_SYNC_TOKEN` 등)은 `config/.env` / fly secrets 에만 둔다. 코드·테스트·문서·픽스처에 값 금지.

---

## 6. 절대 금지

- 언론 원문(HTML·TXT)·인물 사진·`data/stakeholder/` 산출물을 git 에 커밋 (`.gitignore` 로 차단)
- 언론사 CDN `photo_url` 핫링크·렌더링
- sync 가 `sv_person` / `sv_source_domain` / `sv_audit_proposal` 기존 행을 UPDATE·덮어쓰기 (insert-if-absent 위반)
- 정본 MD 재파싱이 `origin='canonical_md'` 외 행을 건드리는 것
- 제안 자동 승인, 에이전트 계정의 승인·반려, 자기 제안 승인
- 기록(인물·제안·발언·런) 삭제, 과거 발언 `speaker_role_at_event` 수정
- 직접인용 재작성·교정, 요약을 따옴표 인용으로 승격, 검색 스니펫으로 인용 확정 (§2 원문 1.6)
- 검토안·시범사업·향후 계획을 확정·시행 제도로 표기 (§2 원문 7. 최종 QC 게이트)
- `source_saturation=partial` 을 전수 확인 완료로 표현
- 재전재·동일 보도자료를 독립 증거로 합산
- 모듈 import 시 실제 DB(`data/db/drug_prices.db`)에 `sv_*` 테이블 생성 (`ensure_tables` 는 첫 호출에서 지연 실행)
- 테스트에서 실제 DB·실제 볼륨(`data/stakeholder/`) 쓰기, 테스트 픽스처에 실제 매체명·실제 인물·실제 기사 문장 사용

---

## 7. 회귀 체크

- **손실 없음 검증**: `scripts/stakeholder_verify_import.py --root <볼륨 root> --db <db> [--source-dir <소스>] [--json]` (Task 10) — 소스 `verify_repository.py` 의미론(JSON 파싱, manifest 경로·SHA-256, artifact 바이트) + 소스↔볼륨 SHA-256 미러 + DB 카운트(`sv_person`, `sv_source_domain`, `sv_audit_proposal`, 정본 인용 블록 수) 대조. 실패 문구 `missing manifest file` / `SHA-256 mismatch` / `artifact byte-size mismatch` 는 유지한다.
- **합성 픽스처 회귀**: `tests/test_stakeholder_lossless.py` 가 `tests/fixtures/stakeholder_src_mini/` 로 같은 검증을 CI 에서 돈다. 픽스처를 고쳤으면 `python -I tests/fixtures/stakeholder_src_mini/_build.py` 로 해시를 재계산하고, 픽스처 루트에서 `python -I scripts/verify_repository.py` 가 `VALIDATION OK` 인지 확인한다.
- **불변식 표 테스트**: `tests/test_stakeholder_approval.py` — §3-A 각 항목에 대응. 재sync 가 운영자 결정을 덮어쓰지 않는지(`test_resync_never_overwrites_operator_decision`)는 `tests/test_stakeholder_sync.py`.
- 신규 규칙·대규모 변경 시 `QualityGuardAgent.review_codebase()` 수동 실행으로 회귀 확인 (루트 `CLAUDE.md` 원칙).
