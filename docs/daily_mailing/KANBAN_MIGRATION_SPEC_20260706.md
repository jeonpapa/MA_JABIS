# Daily Mailing 칸반 — 헤르메스 신규 번들 스키마 마이그레이션 스펙 (2026-07-06)

헤르메스(GPT-5.5, 실제 작성·발송 에이전트)가 전달한 실제 run 번들 스키마를 대쉬보드 Daily Mailing 운영 칸반에 반영한다.
샘플: `docs/daily_mailing/ma_daily_mailing_final_refine_20260706_015250/` (draft / review_board / run_bundle).

## 번들 계약 (입력)
`run_bundle.json = { "payload": {...}, "articles": [...] }`
- `payload`: run 메타 + `quality_report`, `personas`(3), `reviewer_roles`(6), `persona_ids`, `reviewer_role_ids`, `items`(초안 브리프 6건: title/description/article_body/evidence_quotes), `dashboard_scope`, `keywords`, `media`, `owner_email`, `recipients`, `discovered_count`/`recent_count`/`selected_count`, `status`, `delivery_status`, `approval_status`.
- `articles`(193 리치 리뷰카드): `article_id, title, publisher_url, naver_url, official_url, publisher_verified_url, source_name, source_tier, source_status(media_report_only|publisher_verified), priority(High|Medium|Watch), ma_relevance, review_status(needs_review|ready_for_writer), tracking_lane(daily_monitoring|keytruda_source_verification|policy_pricing_tracker), score, selected_for_draft, quality_flags[], matched_keywords[], tracker_tags[], verification_caveat, verification_method, next_action, reviewer_findings{reviewer,label,decision,rationale,required_fix} 또는 [그 배열], reviewer_note, persona_ids[], content_completeness{}, published_at, keyword`.
- `operating_policy`/`counts`/`lanes`(6 stage명)는 review_board.json에만 존재 → payload엔 없음. operating_policy는 상수/review_board 빌더 산출, counts는 파생, lanes 6단계는 고정.

## 6 레인 (고정)
`Dashboard Scope → Source Intake → Triage/Verify → Writer Agent → Review Board → Delivery/History`

레인 배정(기사, load_admin_kanban):
- `selected_for_draft == true` → **Writer Agent**
- `review_status == 'ready_for_writer'`(미선택) → **Review Board**
- `source_status in {publisher_verified, official_verified}`(위 미해당) → **Triage/Verify**
- 그 외 → **Source Intake**
- **Dashboard Scope** / **Delivery/History** 는 run-level 정보 레인(기사 아님): Dashboard Scope = 최신 run 스콥 요약(keywords/media/personas/reviewer_roles), Delivery/History = run별 발송상태·산출물.

## DB 스키마 확장 (storage.py, 멱등 ALTER)
`daily_mailing_run` 추가: `quality_report_json`, `personas_json`, `reviewer_roles_json`, `operating_policy_json`, `counts_json`, `draft_items_json`(payload.items), `dashboard_scope_json`.
`daily_mailing_article` 추가: `tracking_lane`, `reviewer_findings_json`, `next_action`, `tracker_tags_json`, `verification_method`, `official_url`, `content_completeness_json`, `persona_ids_json`, `reviewer_note`.

## API 계약 (`GET /api/admin/daily-mailing/kanban`)
```jsonc
{
  "status": "admin_operational_board",
  "retention_days": 183,
  "article_approval_required": false,
  "operating_policy": { "board_purpose", "article_approval_required", "live_send_allowed",
     "reviewer_roles_are_advisory", "personas_are_audience_targeting_metadata", "sendable_requires": [] },
  "personas": [ {"persona_id","label","description","default_keywords","priority_terms","watch_terms","content_requirements"} ],
  "reviewer_roles": [ {"role_id","label","description","required_checks"} ],
  "runs": [ { ...기존 run 필드...,
     "quality_report": {"status","sendable","live_send_allowed","total_articles","top_signal_count","watchlist_count","min_total_articles","min_top_signals","blocking_reasons","warnings"},
     "counts": {"discovered","recent","selected","needs_review","ready_for_writer"},
     "draft_items": [ {"title","description","publisher_url","evidence_quotes","monitoring_point","work_note"} ],
     "dashboard_scope": {...} } ],
  "lanes": [ {"name","items": [ richArticleCard ]} ]   // 6 lanes
}
```
`richArticleCard = { article_id, run_id, title, publisher_url, naver_url, official_url, source_name, source_tier, source_status, priority, ma_relevance, review_status, tracking_lane, score, selected_for_draft, quality_flags[], matched_keywords[], tracker_tags[], verification_caveat, verification_method, next_action, reviewer_findings[{reviewer,label,decision,rationale,required_fix}], persona_ids[], published_at, generated_at, html_path }`

## 프론트 (admin/daily-mailing-kanban/page.tsx)
- 6 레인 렌더. 4개 기사 레인(Source Intake/Triage/Verify/Review Board/Writer Agent) + Dashboard Scope(스콥 요약 카드) + Delivery/History(run별 발송/산출물).
- **Quality Report 패널**: 최신 run status 배지 + sendable/live_send_allowed + counts(발굴/최신/선택/needs_review/ready_for_writer) + warnings 리스트.
- **Personas 패널**(audience targeting, advisory 명시) + **Reviewer Roles 패널**(advisory 렌즈 + required_checks).
- 리치 ArticleCard: 기존 + `review_status`, `score`, `priority`(High/Medium/Watch 톤), `next_action`, `tracker_tags`, `reviewer_findings` 요약(역할별 pass/fix), `verification_method`.
- **초안 브리프 미리보기**: 선택된 run의 draft_items(한눈에 보기 대체 — title/description/evidence_quotes/monitoring_point/work_note). Writer Agent 레인 또는 별도 '오늘의 브리프' 패널.
- operating_policy.board_purpose를 헤더 설명으로.

## 불변/원칙
- 기사별 approve workflow 금지(operating_policy). reviewer_roles=advisory, personas=audience metadata. sendable은 서비스 레벨.
- 읽기전용 운영 보드. 멱등 재적재(run_id INSERT OR REPLACE).
- 다크 테마 유지(#0D1117/#161B27/#00E5CC). Admin 전용(require_auth).
