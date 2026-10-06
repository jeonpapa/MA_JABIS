# MA AI Dashboard — KRPIA Policy Intelligence Service Implementation Handoff

> **For Claude Code:** implement from this document in `jeonpapa/MA_JABIS`. Do not rely on Slack context. This plan reflects Joseph’s requested dashboard improvements and Hermes’ maintenance requirements.
>
> **For Hermes:** maintain this service by enforcing the privacy/data-lane rules, validating curation sidecars, and keeping the dashboard implementation aligned with the user-facing operating model below.

**Created:** 2026-08-03

**Goal:** Add/finish a Korean-first `KRPIA Policy Intelligence` tab in the MA AI Dashboard that manages cumulative KRPIA / MOHW / HIRA / NHIS policy-TF communication history, shows topic-level “latest position / what changed / next action,” identifies MSD implications, and supports future impact-assessment report drafting without exposing private email bodies, attachment paths, or internal notes in public dashboard payloads.

**Architecture:** Keep deterministic Gmail/attachment ingest separate from analyst judgment. The MA_JABIS app owns public dashboard code, API contracts, tests, and safe metadata. Private policy email evidence and Hermes-authored analysis sidecars live only in the private `AccessRoutineAnalystic` data lane and sync into runtime storage through validated, authenticated, checksum-gated jobs. The dashboard reads sanitized API payloads only.

**Primary existing references:**
- `docs/superpowers/specs/2026-07-02-hermes-krpia-curation-design.md`
- `docs/superpowers/plans/2026-07-02-hermes-krpia-curation.md`
- `docs/hermes_krpia_curation_handoff.md`
- `docs/dashboard_improvement_audit_2026-07-03.md`
- `agents/rules/policy_intelligence_curation_rules.md`
- `agents/ingest/POLICY_INTEL_CURATION_README.md`

---

## 1. Product contract

### 1.1 User-facing tab

Add or finalize a dashboard tab named **`KRPIA Policy Intelligence`**.

The tab must answer, in Korean, four questions:

1. **현재 어떤 KRPIA/정부 정책 주제가 진행 중인가?**
   - Topic ledger by theme, not a flat inbox.
   - Each topic has current status, latest summary, first/latest dates, event count, and severity.

2. **이전 대비 무엇이 바뀌었나?**
   - Chronological change records per topic.
   - Latest change is prominent; older timeline is available but not the default focus.

3. **MSD에 어떤 직·간접 영향이 있을 수 있나?**
   - `msd_implication_latest.rationale`
   - `msd_implication_latest.next_action`
   - Explicit data gaps when internal MSD product/price/sales/contract data is required.

4. **Impact assessment 보고서 작성이 가능한가?**
   - `impact_assessment_ready` badge for topics ready for structured report drafting.
   - Report/template links only when artifacts are available and sanitized.

### 1.2 Topic model

Use the existing policy topics as the controlled taxonomy:

- `기등재 약제 재평가·약가조정`
- `약가 유연계약제`
- `RWE·약제성과평가`
- `희귀질환 치료제 신속등재(100일)`
- `사용량-약가 연동 협상`
- `급여기준 고시 개정 의견조회`
- `KRPIA 정책제안`
- `기타`

Do **not** let LLMs create dashboard topics ad hoc. If a forwarded email does not fit the taxonomy, Hermes must write `data_gaps: ["new_topic_candidate: ..."]`; a human/maintainer then updates the controlled keyword/rule tables and tests.

### 1.3 Lane separation

The KRPIA policy tab is **not** a daily-news, Prain/KEYTRUDA media, or general article-forwarding lane.

Required behavior:

- Exclude Prain/KEYTRUDA article shares and daily-mailing artifacts from `Policy Intelligence` payloads.
- Keep `excluded_general_media_event_count` visible as a small KPI, so Joseph can verify separation without surfacing confidential/media-lane content.
- Daily Mailing dashboard settings remain a separate service concern: monitoring scope + delivery settings + recipients. Those settings should not be mixed into KRPIA TF history.

---

## 2. Data and privacy contract

### 2.1 Public repo vs private data lane

**MA_JABIS public/main app repo may contain:**

- Code
- Tests
- API schemas/types
- Sanitized mock data
- Product/implementation docs
- Rule summaries that do not quote private emails

**MA_JABIS must not contain:**

- Raw forwarded emails
- Email body text
- HWP/HWPX/PDF attachment originals from company/KRPIA communication
- Extracted private attachment text
- `evidence_quotes` from private mails
- Local absolute paths in API payloads

**AccessRoutineAnalystic private lane may contain:**

- `policy_intelligence/analysis/<event_id>.json`
- `policy_intelligence/analysis_manifest.json`
- Private evidence quotes and analyst notes required for auditability

### 2.2 Required sidecar schema

Hermes curation sidecars must keep the existing schema:

```jsonc
{
  "schema": "policy_analysis/v1",
  "event_id": "<stable event id>",
  "content_fingerprint": "<expected fingerprint from list-pending>",
  "criteria_version": "1.0",
  "analyst": "hermes",
  "model": "gpt-5.5",
  "analyzed_at": "<UTC ISO8601>",
  "topic": "<manifest topic copied, not reclassified>",
  "summary": "<2-4 Korean sentences grounded in body/attachment>",
  "severity": "high | medium | low",
  "status": "진행중 | 모니터링 | 완료 | 정보",
  "msd_implication": {
    "rationale": "<why this matters to MSD, no unsupported internal-price claims>",
    "next_action": "<specific follow-up>"
  },
  "evidence_quotes": [
    { "quote": "<exact substring from source>", "source": "body | filename", "loc": "optional page/paragraph" }
  ],
  "kr_rules_cited": [],
  "data_gaps": [],
  "confidence": "high | medium | low",
  "review": { "gate": "none | passed", "consensus": null }
}
```

### 2.3 Fail-closed rules

- If sidecar is missing: dashboard falls back to controlled `TOPIC_RULES` and marks `curation_source="rule_fallback"`.
- If sidecar fingerprint is stale/mismatched: do not use it; mark pending/stale in overview.
- If sidecar validation fails: quarantine or ignore it; do not silently display unvalidated judgment.
- If private sync/auth fails: show operational warning in logs/quality guard; do not expose partial raw data.

---

## 3. Backend implementation requirements

### Task B1 — Verify existing API payload contract

**Files:**
- `agents/policy_intelligence.py`
- `agents/policy_analysis.py`
- `api/server.py`
- `tests/test_policy_intelligence.py`
- `tests/test_policy_analysis.py`

**Endpoint:** `GET /api/policy-intelligence/overview`

Ensure the response contains these top-level keys:

```jsonc
{
  "overview": {
    "event_count": 0,
    "topic_count": 0,
    "document_count": 0,
    "high_impact_count": 0,
    "curated_event_count": 0,
    "pending_analysis_count": 0,
    "stale_analysis_count": 0,
    "excluded_general_media_event_count": 0,
    "source_batch_id": "..."
  },
  "topics": [],
  "topic_ledgers": [],
  "change_records": [],
  "events": [],
  "documents": [],
  "impact_candidates": [],
  "report_artifacts": []
}
```

`topic_ledgers[]` must include:

```jsonc
{
  "topic_id": "...",
  "topic_name": "...",
  "first_seen_at": "...",
  "latest_seen_at": "...",
  "current_status": "...",
  "current_summary": "...",
  "latest_change": { "event_id": "...", "change_type": "new_topic | updated", "after": "..." },
  "severity": "high | medium | low | legacy rule labels",
  "curation_source": "hermes | rule_fallback",
  "msd_implication_latest": { "rationale": "...", "next_action": "..." },
  "events": ["event_id"],
  "impact_assessment_ready": true,
  "data_gaps": []
}
```

**Verification:**

```bash
python3 -m py_compile agents/policy_intelligence.py agents/policy_analysis.py api/server.py
python3 -m pytest tests/test_policy_analysis.py tests/test_policy_intelligence.py -q
```

### Task B2 — Make Gmail policy ingest cumulative, not a rolling 30-day snapshot

**Problem to fix:** A rolling `newer_than:30d` / `max_results` manifest causes old but still-critical policy history to disappear from the dashboard.

**Files:**
- `agents/policy_intelligence_ingest.py`
- `scripts/run_policy_intelligence_ingest.py`
- `tests/test_policy_intelligence_ingest.py`

**Required behavior:**

- Build a new ingest batch from Gmail.
- Merge it into a cumulative manifest by `event_id`.
- Preserve existing events unless explicitly deleted by an operator.
- If the same `event_id` appears again, update deterministic metadata and document hashes, but do not drop sidecar linkage.
- Maintain `latest_ingest_status.json` with both latest batch and cumulative manifest names.

**Acceptance test:**

- Create a fixture with one old priority topic and one new Gmail event.
- Run merge.
- Assert both events remain in the cumulative dashboard source.
- Assert deterministic sorting is chronological then stable by `event_id`.

### Task B3 — Add authenticated/private sidecar sync

**Problem to fix:** Private `evidence_quotes` must not be fetched from a public raw URL without auth.

**Files:**
- `agents/ingest/policy_analysis_sync.py`
- `scheduler.py`
- `tests/test_policy_analysis_sync.py`

**Required behavior:**

- Source defaults to GitHub API or authenticated raw fetch using `GITHUB_TOKEN` / deployment secret.
- If auth is missing in production, fail closed with a clear warning.
- Sync only validated JSON sidecars.
- Copy to runtime root under `<POLICY_INTELLIGENCE_ROOT>/analysis/` only after checksum comparison.
- Do not log private quotes.

**Acceptance test:**

- Local source-dir sync remains idempotent.
- Invalid sidecar is not copied.
- Missing auth path returns structured error and does not create partial files.

### Task B4 — Validate sidecars before dashboard display

**Files:**
- `agents/policy_analysis.py`
- `agents/ingest/policy_analysis_sync.py`
- `agents/rule_compliance/checks.py`
- `tests/test_policy_analysis.py`
- `tests/test_rule_compliance_policy_curation.py`

**Required behavior:**

- `validate_analysis()` checks:
  - required fields
  - fingerprint match
  - severity enum
  - `msd_implication.rationale` and `next_action`
  - evidence quote exists as a substring in source text when source text is available
  - banned tokens: `brdBltNo`, `idxno`, `PR-`, `Precision`, `Recall`, `F1`, `조건부 통과`
- Dashboard resolver returns reason flags for non-Hermes curation:
  - `missing_analysis`
  - `stale_fingerprint`
  - `raw_unavailable`
  - `validation_failed`
- Overview exposes counts for pending/stale/invalid without private detail.

---

## 4. Frontend implementation requirements

### Task F1 — Add or finalize navigation tab

**Files:**
- `frontend/src/router/config.tsx` or current router file
- `frontend/src/pages/policy-intelligence/page.tsx`
- Sidebar/nav component if separate

**Required UX:**

- Tab label: `KRPIA Policy Intelligence`
- Korean-first copy.
- White-tone, app-like internal service UI.
- No raw expert defaults; show only decision-useful fields.

### Task F2 — Topic ledger section above the event timeline

**Files:**
- `frontend/src/api/policyIntelligence.ts`
- `frontend/src/pages/policy-intelligence/page.tsx`
- Any extracted policy components

**Required UI blocks:**

1. **Overview KPI row**
   - 전체 이벤트
   - 관리 주제
   - AI 큐레이션 / 미처리 / stale
   - 분리된 일반 미디어 이벤트 count

2. **Topic Ledgers**
   - Topic name
   - Latest summary
   - First/latest date
   - Event count
   - Severity badge
   - Curation badge: `AI 큐레이션` vs `규칙 기본값`
   - MSD implication rationale
   - Next action
   - `Impact assessment ready` badge

3. **Latest changes**
   - `new_topic` vs `updated`
   - What changed
   - Topic filter click-through

4. **Documents / reports**
   - Sanitized filename/title only
   - Download links only through safe backend endpoints
   - No raw local path display

### Task F3 — Event detail modal

**Required fields:**

- Subject and date
- Topic / agency / status / severity
- Curation source badge
- Summary
- MSD implication rationale + next action
- Evidence quotes only if safe to display to authenticated internal users; otherwise show count/source metadata and a “private evidence retained in audit sidecar” note
- Attachments metadata, not raw path

### Task F4 — Empty/loading/error states

Required states:

- No policy events yet
- Sync pending
- Private curation unavailable
- Authentication/session expired
- Backend error

Do not render a blank dashboard or generic “결과 없음” when an API call failed.

---

## 5. Impact assessment report workflow

### 5.1 First priority lane

Promote `기등재 약제 재평가·약가조정` as the first report-ready lane.

Reason: it has direct potential price/reimbursement implications and already appears as a high-priority KRPIA/MOHW discussion theme.

### 5.2 Report artifacts

Add report artifact placeholders under policy intelligence payload:

- `Current Latest`
- `Timeline / History`
- `What Changed`
- `Documents`
- `MSD Impact`
- `Impact Assessment Draft`

### 5.3 Quantitative impact rule

Do not estimate financial/product impact without internal MSD inputs.

Required `data_gaps` for quantitative assessment:

- MSD affected product list
- current reimbursed price / actual confidential contract assumptions if available internally
- volume/sales exposure
- RSA / price-volume / rebate constraints
- patent/LOE and single-product exemption facts
- scenario definitions approved by Joseph/team

Until those are available, dashboard language should stay qualitative and payer-policy focused.

---

## 6. Hermes operating workflow after implementation

When Joseph forwards company email to Gmail for this service:

1. Gmail ingest stores raw mail and original attachments in private runtime storage.
2. HWP/HWPX/PDF attachments are converted/extracted; originals remain preserved.
3. The deterministic manifest classifies topic/agency/deadline.
4. Hermes runs:

```bash
python -m agents.policy_analysis list-pending --since <last_curated_cutoff>
```

5. Hermes reads only pending event source text, writes sidecar JSON, and validates:

```bash
python -m agents.policy_analysis validate --file policy_intelligence/analysis/<event_id>.json
```

6. Hermes commits sidecars only to private `AccessRoutineAnalystic`.
7. Runtime sync pulls validated sidecars.
8. Dashboard reflects updated ledgers, change records, and MSD implications.

If Joseph provides an audio file, Hermes should transcribe it and use it only as supplemental analyst context; the final sidecar must distinguish audio-derived context from email/attachment evidence.

---

## 7. Security and operations gates

Before any production deployment:

```bash
python3 -m py_compile agents/policy_analysis.py agents/policy_intelligence.py agents/policy_intelligence_ingest.py agents/ingest/policy_analysis_sync.py api/server.py scheduler.py
python3 -m pytest tests/test_policy_analysis.py tests/test_policy_intelligence.py tests/test_policy_intelligence_ingest.py tests/test_policy_analysis_sync.py tests/test_rule_compliance_policy_curation.py -q
cd frontend && npm run build
```

Operational checks:

- `AccessRoutineAnalystic` must be private or fetched through authenticated API.
- `GITHUB_TOKEN` / sync credential is present in production secret store.
- No `policy_intelligence/raw`, extracted private text, or sidecar evidence quotes are committed to MA_JABIS.
- Dashboard API payload contains no absolute local paths.
- Mutation/admin endpoints are protected by auth.
- Default admin password fallback is disabled in production.
- JWT secret is fixed in production; no random restart secret.
- Scheduler jobs have reasonable `misfire_grace_time` and log failures as warnings/errors, not silent success.

---

## 8. Definition of done

Claude implementation is complete only when all are true:

- `KRPIA Policy Intelligence` tab is visible and usable.
- Topic-ledger-first UI is implemented.
- Cumulative manifest preserves historical KRPIA policy events.
- Sidecar curation is private, authenticated, validated, and fail-closed.
- Dashboard shows curated/pending/stale counts.
- Prain/KEYTRUDA and Daily Mailing lanes are excluded but counted as separated.
- Event/detail/report payloads expose no raw paths or private source text unless explicitly authenticated and intended.
- Impact assessment is qualitative by default and blocks quantitative claims behind data gaps.
- Backend tests and frontend build pass.
- Implementation docs and curation runbooks stay in GitHub for future Hermes/Claude maintenance.

---

## 9. Suggested Claude execution order

1. Read this file and the six primary references listed at the top.
2. Run current backend/frontend tests to identify actual baseline failures.
3. Fix P1/P3 first: cumulative ingest and runtime manifest availability.
4. Fix P2/B3/B4: private authenticated sidecar sync + validation before display.
5. Finalize frontend Topic Ledger UX.
6. Add/adjust tests for each behavior.
7. Run full verification gates.
8. Open a PR or commit according to current repo workflow; do not include private data.
