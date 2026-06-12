# Reimbursement 위원회 git-sync 채널 — 헤르메스 작업 가이드

헤르메스(또는 사람)는 **이 repo(`jeonpapa/AccessRoutineAnalystic`, 브랜치 `main`)에 커밋**만 하면 된다.
배포 앱이 **매일 02:00(KST) + 배포 부팅 시** 자동으로 가져와 프로덕션 DB·화면에 멱등 반영한다.
**재배포·재시딩·admin 업로드·DB 직접수정 불필요.**

작업은 2개 트랙:
- **트랙 A — 위원회 데이터** (약평위·암질심 결과): `agents/ingest/committee_results.json`
- **트랙 B — 위원회 보고서** (D-2 사전 / D+1 사후 / 월간): PDF 커밋 + `agents/ingest/reports_manifest.json`

전제: 이 두 파일이 GitHub `main` 에 올라가 있어야 git raw URL 이 동작한다 (최초 `git push` 후 활성).

---

## 트랙 A — 위원회 데이터

`agents/ingest/committee_results.json` 은 약평위·암질심 결과의 **단일 큐레이션 소스**다.
신규 위원회 결과를 이 파일에 추가·커밋하면 자동 반영된다.

```
헤르메스 → committee_results.json 편집 → git commit/push
                      │  (REIMB_DATA_URL = 이 파일의 git raw URL)
                      ▼
배포 앱 reimb_data_sync_job → fetch → sha256 변경 감지 → 멱등 import → 라이브 DB
```

- 소스: 환경변수 `REIMB_DATA_URL` (git raw URL). 미설정 시 이미지 내 로컬 파일 폴백.
- 비공개 repo: `REIMB_DATA_TOKEN` (Bearer) — fly secret.
- 해시 게이트: 직전 적용분과 같으면 skip (무중복). 마지막 해시 `data/reimb/.last_applied_hash`.

### JSON 스키마 (schema_version 1)

```jsonc
{
  "schema_version": 1,
  "missing_sessions": [[year, ord_assumed, ord_official, "YYYY-MM-DD", "AMJILSIM|YAKPYUNGWI", "근거 note"]],
  "session_status_updates": [["YYYY-MM-DD", "COMPLETED", "근거 note"]],
  "expected_next_session": { "브랜드명": "YYYY-MM-DD" },   // 상정 예정 차수 (미래 회차 date)
  "key_issues": { "브랜드명": ["핵심 쟁점 1", "..."] },      // D±1 보고서 전사
  "drugs": [
    {
      "brand_kr": "테빔브라주 100mg", "ingredient_inn": "tislelizumab",
      "manufacturer": "비원메디슨코리아", "msd_flag": 0,
      "tracking_priority": "competitor_class|msd_asset|generic_new_drug",
      "amjilsim_pass_date": "YYYY-MM-DD|null", "yakpyungwi_pass_date": "YYYY-MM-DD|null",
      "negotiation_status": "IN_PROGRESS|AGREED|null",
      "indication": "적응증 원문(전사)", "listing_type": "신규|확대", "submitted_date": null,
      "notes": "현황 노트(출처 brdBltNo/URL 포함)",
      "events": [
        { "committee": "AMJILSIM|YAKPYUNGWI", "state": "APPROVED|REJECTED_REQUEUE|WITHDRAWN",
          "session_date": "YYYY-MM-DD", "n_th_attempt": 1, "evidence_url": "HIRA brdBltNo / URL" }
      ]
    }
  ],
  "existing_drug_supplements": [   // 기존 DB 행(import 외) 보강 — 브랜드 LIKE 매칭
    { "brand_like": "웰리렉%", "events": [...], "key_issues": [...] }
  ]
}
```

### 트랙 A 체크리스트 (신규 차수 결과)
1. 신규 차수면 `missing_sessions` 에 그 회차를 **먼저** 추가 (안 하면 그 차수 이벤트가 link 안 됨).
2. `drugs[]` 에 통과·미설정 약제 추가 (위 스키마). 적응증·일자·회사는 **HIRA 보도자료 원문 그대로**.
3. 통과 직후 협상단계로 진입한 약제는 `expected_next_session` 에서 제거하거나 그대로 둠(통과일 채우면 자동 nhis).
4. 다음 차수 상정 예정이면 `expected_next_session["브랜드"] = "미래 회차 YYYY-MM-DD"`.
5. 핵심 쟁점은 `key_issues["브랜드"] = ["...", ...]` (D±1 보고서 전사).

---

## 트랙 B — 위원회 보고서 (D-2 / D+1 / 월간)

보고서 **작성(분석)** 은 헤르메스/Claude 가 큐레이션한다(충실성·판단 필요). **전달만 자동**:
PDF 를 repo 에 커밋 + 매니페스트에 1줄 추가하면, 앱이 다운로드·적재해 **Intelligence Reports** 화면에 띄운다.

```
헤르메스 → 보고서 PDF 커밋 + reports_manifest.json 1줄 추가 → git push
                      │  (REPORTS_DATA_URL = 매니페스트의 git raw URL)
                      ▼
배포 앱 _reimb_reports_sync → 매니페스트 fetch → file_hash dedup → PDF download → blob 저장 → 화면
```

- 소스: `REPORTS_DATA_URL` (매니페스트 git raw URL). 미설정 시 이미지 로컬 매니페스트 폴백.
- 해시 게이트: 매니페스트 sha256 무변경 시 skip. 항목별 `file_hash` 가 DB 에 있으면 무재다운로드.
- PDF 는 repo 어디에 둬도 됨(매니페스트의 `url` 이 가리키면 됨). data/ 볼륨 가림과 무관(GitHub raw fetch).

### 2단계 작업
**① 보고서 PDF 를 repo 에 커밋** (예: `data/hira_pipeline/보고서/D+1_결과_리뷰/2026-07-03_yakpyungwi-7_d_plus_1.pdf`)

**② `agents/ingest/reports_manifest.json` 의 `reports[]` 에 항목 추가**:
```jsonc
{
  "schema_version": 1,
  "reports": [
    {
      "file_name": "2026-07-03_yakpyungwi-7_d_plus_1.pdf",
      "file_hash": "<PDF 의 sha1 16진>",
      "url": "https://raw.githubusercontent.com/jeonpapa/AccessRoutineAnalystic/main/<PDF repo경로, URL-encode>",
      "committee": "evaluation",      // evaluation(약평위) | cancer(암질심)
      "report_type": "post",          // pre(D-2 사전) | post(D+1 사후) | monthly(월간)
      "year": 2026, "cycle": 7,
      "session_date": "2026-07-02",
      "title": "2026년 7차 약제급여평가위원회 결과 리뷰 보고"
    }
  ]
}
```

### file_hash·url 자동 생성 (권장 — 손계산 불필요)
`repo_path` 만 주면 sha1·git raw URL 을 계산해 매니페스트를 갱신한다:
```python
from agents.reimb_reports import build_reports_manifest
build_reports_manifest([
  {"repo_path": "data/hira_pipeline/보고서/D+1_결과_리뷰/2026-07-03_yakpyungwi-7_d_plus_1.pdf",
   "committee": "evaluation", "report_type": "post",
   "year": 2026, "cycle": 7, "session_date": "2026-07-02",
   "title": "2026년 7차 약제급여평가위원회 결과 리뷰 보고"},
  # ... 보고서 여러 건을 목록으로 (canonical 1건/세션·종류) ...
])
```
> ⚠️ `build_reports_manifest` 는 매니페스트를 **통째로 재생성**한다 → 유지할 기존 보고서도 목록에 포함할 것.

---

## 원칙 (CLAUDE.md — 양 트랙 공통)

- **날조 금지**: 모든 결과·적응증·일자는 HIRA 공식 보도자료 원문 전사. 출처(brdBltNo/URL) 보존.
- 데이터: 멱등 UPSERT (brand_kr + ingredient_inn). 큐 이벤트 중복 자동 방지.
- 보고서: file_hash(내용 sha1) dedup — 같은 PDF 재커밋해도 1건. 메타(committee/type/cycle/date)는 매니페스트 권위값.
- 신규 차수는 데이터 `missing_sessions` 에 먼저 추가해야 그 차수 이벤트가 link 됨.
- 비밀(토큰)은 fly secrets — repo 에 커밋 금지.

## 헤르메스에게 요구하지 않는 것 (앱이 자동 처리)
- ❌ 서버 배포 / 재시딩 / fly ssh / DB 직접 수정 / admin 업로드 / 적용 타이밍 관리

## 수동 도구 (사람/디버그용)

- 누락 점검: `python -m agents.scrapers.hira_press 12` (지난 12개월 HIRA 보도자료 ↔ DB 대조 → 누락 차수 리포트).
- 데이터 JSON 재생성(파이썬 authoring → JSON): `python -m agents.ingest.reimb_committee_import export`.
- 보고서 매니페스트 생성: `build_reports_manifest([...])` (위 트랙 B).
- 즉시 sync(02:00 안 기다리고): `python scheduler.py --amjilsim-daily-crawl-now`
  또는 프로덕션 `fly ssh console -C "python scheduler.py --amjilsim-daily-crawl-now"`.
