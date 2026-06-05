# HIRA Access Analyst Agent — Operating Manual

## Purpose
Single specialist agent for HIRA Market Access intelligence routines: Yakpyeongwi and Amjilsim D-2 prediction, D+1 result review, monthly trend diagnosis, evidence verification, and prediction-rule audit.

## Initial pilot jobs registered
- 2026-06-30 16:00 KST: Yakpyeongwi 7th D-2 prediction
- 2026-07-03 08:00 KST: Yakpyeongwi 7th D+1 result review
- 2026-07-09 08:00 KST: Amjilsim 6th D+1 result review

## Official source extraction
- Press release list: https://www.hira.or.kr/bbsDummy.do?pgmid=HIRAA020041000100
- Detail URL pattern: https://www.hira.or.kr/bbsDummy.do?pgmid=HIRAA020041000100&brdScnBltNo=4&brdBltNo=<BNO>&pageIndex=1&pageIndex2=1
- Reusable extractor: `/opt/data/hira_pipeline/scripts/hira_press_extractor.py`
- Confirmed on 2026-06-05: HIRA HTML list directly exposes brdBltNo and titles. Detail page for brdBltNo=11814 directly included HWP-converted body text containing drug names and official result terms.

### Extractor commands
```bash
/opt/data/hira_pipeline/scripts/hira_press_extractor.py list --limit 10
/opt/data/hira_pipeline/scripts/hira_press_extractor.py find --query '약제급여평가위원회 심의결과' --limit 5
/opt/data/hira_pipeline/scripts/hira_press_extractor.py detail --brdBltNo 11814 --keywords 리브리반트 빌로이 핀테플라 지텍 테빔브라
```

### Extraction priority
1. HIRA direct HTML extraction using the extractor.
2. HIRA detail page clean text validation against expected drug/result keywords.
3. If direct detail body is missing or incomplete, inspect attachment download JS path.
4. If still unavailable, use Tier A/B media body-verified cross-reference and record fallback only in audit.

## Reporting rules
- Use HIRA terms: 급여 적정성 있음, 평가금액 이하 수용 시 적정, 위험분담 확대 적정, 재심의, 급여기준 설정/미설정.
- Do not expose media idx, brdBltNo, PR rule IDs, precision/recall, or system metadata in leadership reports. Keep those in audit files only.
- Audience: MSD Korea Market Access leadership.

## Delivery rules
- Slack: allowed for summaries in originating thread.
- Email: draft/send requires Google Workspace or SMTP setup and explicit user approval before any external send.
- Until email auth is configured, do not claim automatic emailing is active.

## Local storage
- Root: /opt/data/hira_pipeline/
- Evidence: /opt/data/hira_pipeline/evidence/
- Reports: /opt/data/hira_pipeline/reports/
- Audit: /opt/data/hira_pipeline/audit/
