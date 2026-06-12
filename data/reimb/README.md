# Reimbursement 위원회 데이터 (헤르메스 git-sync 채널)

`committee_results.json` 은 약평위·암질심 위원회 결과의 **단일 큐레이션 소스**다.
헤르메스(또는 사람)가 이 파일에 신규 위원회 결과를 추가·커밋하면, 배포된 앱이
**매일 02:00(KST) + 배포 직후 부팅 시** 자동으로 가져와 프로덕션 DB 에 멱등 적재한다.
**재배포·수동 재시딩 불필요.**

## 흐름

```
헤르메스 → committee_results.json 편집 → git commit/push
                      │
   (REIMB_DATA_URL = 이 파일의 git raw URL)
                      ▼
배포 앱 스케줄러(reimb_data_sync_job) → fetch → sha256 해시 변경 감지 → 멱등 import → 라이브 DB
```

- 소스: 환경변수 `REIMB_DATA_URL` (git raw URL). 미설정 시 이미지 내 로컬 파일 사용.
- 비공개 repo: `REIMB_DATA_TOKEN` (Bearer) — fly secret.
- 해시 게이트: 직전 적용분과 같으면 skip (무중복). 마지막 해시는 `.last_applied_hash`.

## JSON 스키마 (schema_version 1)

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

## 원칙 (CLAUDE.md)

- **날조 금지**: 모든 결과·적응증·일자는 HIRA 공식 보도자료 원문 전사. 출처(brdBltNo/URL) 보존.
- 멱등 UPSERT (brand_kr + ingredient_inn). 큐 이벤트 중복 자동 방지.
- 신규 차수는 `missing_sessions` 에 먼저 추가해야 그 차수 이벤트가 링크됨 (없으면 해당 이벤트만 skip).

## 수동 도구

- 누락 점검: `python -m agents.scrapers.hira_press 12` (지난 12개월 HIRA 게시물 ↔ DB 대조).
- JSON 재생성(파이썬 authoring → JSON): `python -m agents.ingest.reimb_committee_import export`.
- 즉시 sync: `python scheduler.py --amjilsim-daily-crawl-now`.
