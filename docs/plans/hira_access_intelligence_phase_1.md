# HIRA Access Intelligence — Phase 1 Implementation Plan

> **For Hermes:** Use the HIRA Access Analyst Agent as the single specialist routine operator for now. Split into Evidence/Prediction/Report/Audit agents only after Phase 1 is stable.

**Goal:** Convert the HIRA Access Intelligence concept into a working single-agent routine service with direct HIRA official-source extraction, evidence storage, D-2/D+1 report generation, and audit-ready outputs.

**Architecture:** Start with local Hermes storage under `/opt/data/hira_pipeline/` and scheduled Hermes cron jobs delivering summaries back to the originating Slack thread. Use HIRA official HTML extraction as the first source path, then media cross-reference only as fallback.

**Tech Stack:** Python stdlib extractor, Hermes cron jobs, Markdown evidence/reports/audit files, optional future Google Workspace/Gmail integration.

---

## Phase 1A — Direct HIRA extraction foundation

### Task 1: Create reusable HIRA press extractor

**Status:** Completed.

**Files:**
- Created: `/opt/data/hira_pipeline/scripts/hira_press_extractor.py`
- Evidence output: `/opt/data/hira_pipeline/evidence/raw/hira/`

**Verification command:**

```bash
/opt/data/hira_pipeline/scripts/hira_press_extractor.py list --limit 5
/opt/data/hira_pipeline/scripts/hira_press_extractor.py detail --brdBltNo 11814 --keywords 리브리반트 빌로이 핀테플라 지텍 테빔브라
```

**Verified result:**
- HIRA press list fetch succeeded.
- Detail page for `brdBltNo=11814` fetched.
- Clean text extracted.
- Drug/result keywords found in official text.

### Task 2: Store operating manual

**Status:** Completed.

**File:**
- `/opt/data/hira_pipeline/HIRA_ACCESS_ANALYST_AGENT_OPERATING_MANUAL.md`

---

## Phase 1B — Pilot routine execution

### Task 3: Run 7th Yakpyeongwi D-2 pilot

**Scheduled:** 2026-06-30 16:00 KST.

**Cron job:** `4971fce83555`

**Expected outputs:**
- `/opt/data/hira_pipeline/reports/D-2_사전_예측/2026-06-30_yakpyungwi-7_d_minus_2.md`
- audit file under `/opt/data/hira_pipeline/audit/`
- Slack executive summary.

### Task 4: Run 7th Yakpyeongwi D+1 pilot

**Scheduled:** 2026-07-03 08:00 KST.

**Cron job:** `1bbdb12e3fcd`

**Expected outputs:**
- `/opt/data/hira_pipeline/reports/D+1_결과_리뷰/2026-07-03_yakpyungwi-7_d_plus_1.md`
- audit comparing D-2 prediction vs actual outcome.
- Slack executive summary.

### Task 5: Run 6th Amjilsim D+1 pilot

**Scheduled:** 2026-07-09 08:00 KST.

**Cron job:** `011e4c965e92`

**Expected outputs:**
- `/opt/data/hira_pipeline/reports/D+1_결과_리뷰/2026-07-09_amjilsim-6_d_plus_1.md`
- transition analysis to Yakpyeongwi.
- Slack executive summary.

---

## Phase 1C — Before full recurring conversion

### Required decisions

1. **GitHub source of truth**
   - Confirm whether Hermes should clone/push `https://github.com/jeonpapa/AccessRoutineAnalystic`.
   - Confirm auth method: GitHub token or SSH.

2. **Email delivery mode**
   - Recommended: Gmail OAuth draft creation first, no auto-send.
   - Requires Google client secret and OAuth token setup.

3. **Storage sync**
   - Current Hermes root: `/opt/data/hira_pipeline/`.
   - Decide whether to sync into Obsidian vault via GitHub or file sync.

4. **Full recurring jobs**
   - Daily media crawl.
   - Daily D-2 detector.
   - Daily D+1 detector.
   - Monthly trend detector.
   - Annual/D-30 schedule fetcher.

---

## Current recommendation

Keep the three July jobs as controlled pilots. After the first D+1 audit confirms the HIRA extractor works against a new release, convert to recurring daily detector jobs and add Gmail draft delivery.
