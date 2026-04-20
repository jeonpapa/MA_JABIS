# CompetitorTrendsAgent 규칙

## 목적
MSD 외 경쟁 브랜드의 한국 뉴스를 주 1회 자동 수집 → LLM 중요도 필터 → `/competitor-trends` 카드 자동 갱신.

## 대상 브랜드
`agents/competitor_trends_agent.py` → `COMPETITOR_BRANDS`
- 옵디보 (BMS), 타그리소 / 임핀지 / 린파자 (AstraZeneca), 테쎈트릭 (Roche), 레블리미드 (BMS), 다잘렉스 (Janssen)
- 신규 경쟁사 추가 시 이 리스트에 dict 추가 (`query`, `company`, `logo`, `color`).

## 파이프라인
1. **수집**: `NaverNewsClient.daily_counts(brand, days=7, max_pages=3)` → 최대 30건/브랜드
2. **필터 (LLM)**: GPT-4o-mini — importance ∈ {critical, moderate} 만 유지
   - critical = 허가 · 급여 · 가격 · 적응증 확대 · 주요 임상 결과
   - moderate = 국내 제휴 · 파이프라인 신규 단계 · 가이드라인 반영
   - low = 일반 매출/주가/마케팅 → drop
3. **badge**: `신규 출시 | 가격 변동 | 임상 진행 | 급여 등재 | 파이프라인 | 전략 변화` 중 하나. 해당 없음 → drop
4. **UPSERT**: `competitor_trend` 테이블, `source_type='auto_naver'`, url 기반 dedup
   - `source_type='manual'` 이 있는 url 은 절대 덮어쓰지 않음 (admin 편집 보존)

## 스케줄
```
0 7 * * MON  cd /path/to/MA_AI_Dossier && PYTHONPATH=. python3 agents/competitor_trends_agent.py >> logs/competitor_trends.log 2>&1
```

## 수동 트리거
- CLI: `PYTHONPATH=. python3 agents/competitor_trends_agent.py --days 7 [--dry-run]`
- Admin UI: `/admin/competitor-trends` → "지금 크롤 실행" 버튼
- API: `POST /api/admin/competitor-trends/refresh {"days":7,"dry_run":false}` (admin only)

## 환경변수 (config/.env)
- `NAVER_API_CLIENT_ID`, `NAVER_API_CLIENT_SECRET` — 필수
- `OPENAI_API_KEY` — 필수 (없으면 LLM 필터 skip → accepted=0)

## 데이터 출처 표시 (CLAUDE.md §최소 원칙)
- `source_type` 컬럼으로 'auto_naver' vs 'manual' 구분
- `importance` 컬럼으로 LLM 판정 기록 (audit 용)
- 카드 UI 에서 manual 과 auto 를 동등 노출 (현재는 구분 표시 없음)
