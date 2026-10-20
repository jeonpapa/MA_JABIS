# 데이터 계약 — 사람 정본 테이블 · 보호 행 · 불변식

> 2026-10-17 신설. 일회성 프로덕션 정정이 이후 스케줄 잡·스크레이퍼·재빌드·부팅 단계에서 **되돌아가거나(덮어쓰기)**
> 새 데이터에서 **재발하지 않게** 하는 계약. 쓰기 경로를 추가·수정할 때 이 표를 먼저 본다.
> 검사: `agents/quality_guard/data_contracts.py` (매일 06:00 `review_codebase`) + `agents/agent_tasks/freshness.py`
> (`reimbursement_checklist_mfds_gap`) + QG `scan_rule_drift` (approval_date UPDATE 가드). 테스트: `tests/test_data_contract_guards.py`.

## §1 `indication_reimbursement` — 사람 입력 정본 (Keytruda 급여 체크리스트)

- **쓰는 곳은 admin API 한 곳뿐** (`api/server.py` `/api/admin/reimbursement*`, 편집자 email·updated_at 기록). 스케줄 잡·스크레이퍼·
  ingest·부팅 시드는 이 테이블에 **쓰지 않는다** (INSERT/UPDATE/DELETE 금지). 새 자동 경로가 필요하면 별도 테이블 + 읽기 union.
- 키는 `indication_id`. 허가 재빌드·병합으로 id 가 바뀌면 행이 고아가 된다 →
  `merger._do_merge` 가 `UPDATE OR IGNORE` 로 dst 에 옮긴다(dst 에 이미 행이 있으면 둘 다 사람 값 — 고르지 않고 src 를 고아로 남김).
- 불변식: MFDS 허가일이 있는 Keytruda 적응증은 체크리스트 행이 있어야 한다 / 고아 행 0 →
  위반 시 신선도 `reimbursement_checklist_mfds_gap` 과제 "급여 체크리스트 입력 필요"(improvement, 담당 사람) + QG `checklist_orphan`.

## §2 `indications_by_agency.raw_source = 'manual_correction'` — 보호 행

- 사람이 근거(변경이력 원문·라벨)로 고친 허가일. **어떤 자동 경로도 approval_date·restriction_note·raw_source 를 바꾸지 않는다.**
  - `agents/db/indications.upsert_indication_agency` — ON CONFLICT CASE 가드 (builders·build_*_indications 전부 이 경로)
  - `scripts/apply_mfds_official_dates._apply_product` — manual 행 skip(`stats.protected`) + UPDATE WHERE 가드 (ForeignApprovalAgent._build_mfds 가 자동 호출)
  - `agents/research/per_indication_dates.py` — UPDATE WHERE 가드
  - `ForeignApprovalAgent.build(wipe=True)` — wipe 전 스냅샷 → 빌드 후 같은 (indication_id, agency) 에 복원 (마스터가 사라졌으면 경고 로그)
  - `merger._do_merge` — src 의 manual 행이 dst 자동 행을 덮는다(manual 은 병합으로 사라지지 않음)
  - admin PDF 업로드(`/api/admin/approval-document`)는 사람 경로 — 허용
- 새 코드가 `UPDATE indications_by_agency … approval_date` 를 쓰면 같은 파일에 manual_correction 가드가 있어야 한다 (QG drift WARNING, 테스트가 고정).
- 불변식: 후속(FDA 기준 제품 최초 허가 +1년 이후) 적응증의 MFDS 일자 = 제품 MFDS 최초 일자 이면 오매칭 의심 →
  QG `mfds_later_indication_first_date` (WARNING). 검증 결과 맞으면 그 행을 `manual_correction` 으로 확정한다(검증 표시 겸 보호).
  MFDS 행 `combination_label` 은 신뢰 불가(EV 병용 행도 'monotherapy') — 판정에 쓰지 않는다.

## §3 해외약가 강도·route — 코드가 정본, 저장 행은 현재 코드와 같아야 한다

- 정정 스크립트 `scripts/repair_foreign_strength.py` 는 **현재 코드**(배정·파싱)를 저장 행에 다시 돌린 것 → 새 스크레이프와 결과가 같다.
  파서 수정 없이 값만 고치는 정정 금지(다음 검색의 compact 가 정정 행을 지우고 버그 값을 다시 쓴다).
- 쓰기 경로: live 검색·`--price-backfill-now` → `search_all`(새 행 + `_assign_formulations_and_persist` + compact),
  `recompute_foreign_fx`(최신 세션 행 복사 — 강도/제형 컬럼 그대로), `get_cached_results`(formulation_key 없는 행만 배정).
- 불변식(QG): `injection_concentration_as_unit`(기존) · `foreign_injection_route_as_oral`(route='oral' 인데 행 단서는 주사, ERROR) ·
  `foreign_continuation_fragment`(';' 로 시작하는 조각 행이 강도 없이 priced, WARNING).

## §4 `competitor_trend` badge

- `source_type='manual'` 카드는 자동 경로가 건드리지 않는다(`_upsert_trend` skip, `reclassify_competitor_trends.py` WHERE 가드).
  admin 생성은 DB 기본값 'manual', admin PATCH 는 `source_type='manual'` 로 승격(자동 카드를 사람이 고치면 그때부터 사람 정본).
- 자동 경로(주간 크롤·07:00 promote)는 LLM 출력 → `ALLOWED_BADGES` 검증 → `_apply_quality_guard`(결정론 가드) 후에만 UPSERT.
- 불변식(QG): badge ∈ ALLOWED_BADGES 8종 (`competitor_badge_not_allowed`, ERROR) · 비-manual 카드인데 가드가 '해외 승인' 으로 보는
  '신규 출시'/'급여 등재' 0건 (`competitor_foreign_as_launch`, ERROR — 조치: reclassify 스크립트 dry-run → --apply).

## §5 시드 · 캐시

- `onco_regimen`/`onco_regimen_drug`: 부팅 시드는 `--if-empty` 만(비어 있을 때만 적재). 전체 교체 ingest(`onco_regimen_ingest.ingest`)는
  파싱 결과가 비면 거부(ValueError) — 빈 xlsx 로 정본이 통째 삭제되지 않게.
- 키워드 클라우드: 쓰기 시 `keyword_cloud_filter` 적용 + 일자 캐시(`gov_summary_{날짜}.json`)도 읽을 때 필터(배포 당일 구 캐시 노출 방지).
  기관별 클라우드 캐시는 `_v2_` 키(필터 이후 버전).


## §6 `analog_reports` (등재 아날로그) — 보강 원천과 완성도

- **원천 구분**: `mfds_effect_text` = 식약처 허가 효능(API), `report_effect_text` = 약평위 보고서 1페이지 '▢ 효능·효과' 원문. 섞지 않는다.
  질환 분류(`enrich_disease`)는 `COALESCE(mfds_effect_text, report_effect_text)` 를 읽는다.
- **빈 칸은 빈 칸**: 원문이 비어 있으면(2007~2009 공개 제외 등) None. 틀린 값을 넣느니 비운다 — `header_extract` 는 값 검증 게이트
  (`_CLEAN_STRENGTH`·`_CLEAN_INN`·`_is_company`·`_not_an_ingredient`)를 통과한 값만 돌려준다.
- **식약처 허가일 = 같은 제품군의 최초 허가일**(`permit_first.earliest_permit`). '첫 정상 품목' 금지(함량별 재허가·취하로 수년 늦어짐).
  API 오류는 '미확인' 이 아니라 재시도 대상이다.
- **최초 등재일은 brand 매칭만 자동 적용**. 성분(ingredient) 매칭은 신뢰도가 낮아 사람 검토 후에만. 등재일 < 허가일이면 매칭 오류로 버린다.
- **신규 반영 경로**: `agents/analog/drec_sync.py` — HIRA 게시판 번호 순차 탐색 → 평가결과 PDF → 파싱 → 신규 행만 보강. 기본 dry-run.
  접속 실패를 '게시물 없음' 으로 세지 않는다(cursor 는 첫 실패 직전).
- **운영 DB 변경은 compare-and-set 패치**(기록된 이전 값과 같을 때만 쓴다) + 볼륨 스냅샷. `apply_analog_sync` 통째 교체는 운영에서 따로
  바뀐 값을 지우므로, 원본과 운영이 같은지 먼저 대조한 뒤에만 쓴다.
- 검사: QG `analog_*` 5종(성분명 오염·효능문구 오염 ERROR / 스캔 오판·노출 행 빈칸 비율·일자 미상 WARNING). 테스트 `tests/test_analog_*.py`.
