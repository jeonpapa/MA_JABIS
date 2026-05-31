# macOS launchd로 amjilsim·약평위 스케줄러 routine 작동 설정

> **목적**: scheduler.py가 백그라운드 daemon으로 항상 돌아가서 매일 02:00 크롤 / 08:00 D+1 / 09:00 월간 / 16:00 D-2 자동 발사 보장.

## plist 파일 위치

`~/Library/LaunchAgents/com.msd.hira-pipeline-tracker.plist`

이미 작성됨. 내용:
- Python: `/Users/kimjeong-ae/MA_AI_Dossier/.venv/bin/python`
- Script: `/Users/kimjeong-ae/MA_AI_Dossier/scheduler.py`
- WorkingDirectory: `MA_AI_Dossier/`
- PYTHONPATH: 자동 설정
- Timezone: Asia/Seoul
- RunAtLoad: true (load 시 즉시 시작)
- KeepAlive: 비정상 종료 시 자동 재시작
- 로그: `MA_AI_Dossier/logs/scheduler.stdout.log` + `.stderr.log`

## 등록·실행 명령 (사용자가 직접 실행)

```bash
# 1. plist 등록 (load)
launchctl load ~/Library/LaunchAgents/com.msd.hira-pipeline-tracker.plist

# 2. 상태 확인
launchctl list | grep hira-pipeline-tracker
# → 정상이면: PID 표시 + Status=0

# 3. 로그 확인
tail -f ~/MA_AI_Dossier/logs/scheduler.stdout.log

# 4. 즉시 실행 테스트 (calendar gating 작동 확인)
cd ~/MA_AI_Dossier && PYTHONPATH=. .venv/bin/python scheduler.py --amjilsim-d-minus-2-now
# → "오늘은 어떤 차수의 D-2도 아님 — idle pass" (오늘 5/30, D-2 회의 없음)
# → 6/2 화요일에 실행 시: "D-2 발사 대상: YAKPYUNGWI 6차 (2026-06-04)"

# 5. 일시 중단 (필요 시)
launchctl unload ~/Library/LaunchAgents/com.msd.hira-pipeline-tracker.plist
```

## cron 작동 검증

```bash
# scheduler.py 실행 후 다음 로그가 보이면 정상
tail -n 50 ~/MA_AI_Dossier/logs/scheduler.stdout.log
```

**기대 로그**:
```
스케줄러 시작 — 파이프라인(매월 1일 09:00) / Git 백업(매일 00:00) / QualityGuard 리뷰(매일 06:00)
```

추가 cron 5개도 같은 시작 시점에 등록됨.

## 등록된 cron 목록 (HIRA Pipeline Tracker 부분)

| Job ID | 시각 | 동작 |
|---|---|---|
| amjilsim_daily_crawl | 매일 02:00 KST | HIRA 공식 + 27개 의약 전문지 크롤 |
| amjilsim_d_minus_2_reporter | 매일 16:00 KST | calendar gating → D-2 보고서 발사 (17:00 마감) |
| amjilsim_d_plus_1_reporter | 매일 08:00 KST | calendar gating → D+1 보고서 + audit (09:00 마감) |
| amjilsim_monthly_trend | 매일 09:00 KST | 매월 마지막 약평위 D+7 gating → 월간 트렌드 |
| hira_schedule_fetcher_annual | 매년 1/1 09:00 KST | HIRA 공식 일정 자동 갱신 |

## 첫 라이브 자동 발사 예정

| 차수 | 자동 발사 시점 | 자동 발사 내용 |
|---|---|---|
| 6/4 약평위 6차 | 2026-06-02 16:00 (D-2) | D-2 사전 예측 보고서 자동 발사 |
| 6/4 약평위 6차 | 2026-06-05 08:00 (D+1) | D+1 결과 리뷰 + 룰 자가 학습 audit |
| 7/8 암질심 6차 | 2026-07-06 16:00 (D-2) | D-2 사전 예측 |
| 7/8 암질심 6차 | 2026-07-09 08:00 (D+1) | D+1 결과 리뷰 |
| 7/2 약평위 7차 | 2026-06-30 16:00 (D-2) | D-2 사전 예측 |
| 7/2 약평위 7차 + 7/9 트렌드 발사 | 2026-07-09 09:00 | 매월 마지막 약평위(7/2) + D+7 = 7/9 월간 트렌드 |

## 사전 조건

backend Python module 실제 logic은 아직 placeholder. cron은 등록되지만 실제 발사 시 다음이 호출되어야 함:
- `AmjilsimTrackerAgent.daily_crawl()`
- `AmjilsimTrackerAgent.run_d_minus_2(committee, session_id)`
- `AmjilsimTrackerAgent.run_d_plus_1(committee, session_id)`
- `AmjilsimTrackerAgent.run_monthly_trend()`
- `schedule_fetcher.fetch_and_sync()`

이들은 W2~W6 일정에 구현 예정. 현재는 calendar gating + 로그만 작동.

## 첫 자동 발사 전 사용자 확인 사항

1. `launchctl load` 실행해 daemon 시작
2. 6/2 16:00 시점에 로그에 "D-2 발사 대상: YAKPYUNGWI 6차" 표시 확인
3. backend module 구현 완료 후 실제 보고서 자동 생성 검증
