# 해외약가 수집·환율 크론잡 상세 (2026-10-08 기준)

> 대상: MA 리드 + 운영자. 무엇이 자동으로 돌고, 무엇이 사람의 검색에 의존하며, 실패하면 어디에 드러나는지 정리한다.
> 기준 코드: 로컬 `main`(P1~P1.6 포함, **미배포**). 프로덕션은 2026-09-30 17:40 배포본(그 이전 코드)으로 돌고 있다 — 차이는 §6.

---

## 1. 한 줄 요약

**해외약가 "가격"을 정기적으로 가져오는 크론잡은 없다.** 가격은 사용자가 해외약가 화면에서 검색할 때만 수집된다.
정기 크론으로 도는 것은 ① **환율(KEB 36개월 평균) 월간 갱신**과 ② 가격이 멈췄는지·수집이 깨졌는지를 **감시하는 잡**들이다.

| 구분 | 자동? | 근거 |
|---|---|---|
| 해외 가격 수집 (8개국 스크레이퍼) | ✗ 사용자 검색 시에만 | `POST /api/foreign/search` → `ForeignPriceAgent.search_all` |
| 해외 가격 일괄 백필 | ✗ 수동 전용 (`--price-backfill-now`) | `foreign_price_backfill_job` — 스케줄 미등록 |
| KEB 환율 갱신 + 기존 행 재계산 | ✓ 매월 2일 04:00 | `exchange_rate_refresh_job` |
| 설정 점검 · 신선도 판정 · 화면 스모크 | ✓ 매일 07:40 / 07:45 / 07:50 | P1.5·P1.6 관측 잡 |

---

## 2. 해외약가 관련 잡 전체

시간은 모두 Asia/Seoul. 등록부는 `scheduler.py` `build_job_specs()`.

| 잡 id | 스케줄 | 하는 일 | 실패 시 | misfire 유예 |
|---|---|---|---|---|
| `exchange_rate_refresh` | 매월 2일 04:00 | KEB 36개월 평균환율 엑셀을 Playwright 로 받아 캐시 갱신 → `recompute_foreign_fx()` 로 기존 최신 가격 행을 새 환율로 재계산 | 원장 과제 `scheduler_job / exchange_rate_refresh:{날짜}` (런북 `exchange_rate_refresh`) | 24시간 |
| `foreign_approval_sync` | 매일 04:30 | 가격은 있는데 허가 master 가 없는 제품을 찾아 허가 빌드 | 원장 과제 (`failed` slug 포함) | 6시간 |
| `reimbursement_xnational_sync` | 1·4·7·10월 1일 02:00 | NICE/PBAC/CMS/中医協 급여 결정 수집 (가격 아님, 국가 카드의 급여 축) | 원장 과제 | 24시간 |
| `foreign_config_probe` | 매일 07:40 | **네트워크 없이** 로그인 필요 스크레이퍼의 자격증명 키 존재, `scrapling` import, 프록시 환경변수 확인 | 문제 1건당 `config` 과제 1건(사람이 닫을 때까지) | 6시간 |
| `domain_freshness` | 매일 07:45 | 데이터 도메인 신선도 판정 — 해외약가 4개 포함(§4) | stale 도메인마다 `{도메인}:{YYYY-MM}` 과제 | 6시간 |
| `platform_smoke` | 매일 07:50 | 화면별 API 스모크 — 해외약가 화면 4체크(§4) | 실패 체크 집합당 과제 1건 | 6시간 |
| `foreign_price_backfill` | **미등록** (수동) | `indications_master` 제품마다 INN 으로 8개국 `search_all` | 원장 과제 `foreign_price_backfill:{날짜}` | — |
| `exchange_rate_recompute` | **미등록** (수동) | 캐시는 그대로, 기존 행만 현재 캐시 환율로 재계산 | 원장 과제 | — |

> misfire 유예: 서버 재기동·절전 등으로 정시를 놓쳐도 이 시간 안에 스케줄러가 살아나면 1회 실행한다(coalesce). P1.5 이전에는 대부분 **1초**여서 놓친 실행이 조용히 버려졌다.

### 왜 가격 백필을 스케줄에 올리지 않았나

2026-07-24 비활성화 당시 사유(행 누적·검색 히스토리 고정 노출)는 압축(`compact_foreign_prices`)으로 상당 부분 해소됐지만, 지금 켜면 매주 다음이 반복된다(P1.6 감사 합의).

- JP·UK 는 INN 검색이 구조적으로 0건이었다(P1.6 에서 브랜드 라우팅 추가 — **아직 실측 미검증**).
- US 는 2026-06-08 이후 Micromedex 로그인이 모두 실패했다.
- CA 는 소스(Ontario EAP)에 자사 제품이 없다.
- 실패가 원장에 남지 않았다(P1.6 에서 해결).

**다시 켜는 조건**: US 로그인 복구 + JP/UK 실측 검증 → 월 1회 스케줄로 등록. 그 전까지는 §4 의 신선도 판정이 "가격이 멈췄다"를 원장에 드러낸다.

---

## 3. 가격 수집 흐름 (사용자 검색 시)

```
해외약가 화면 검색
  → GET /api/foreign/resolve           브랜드↔INN 확정(타입어헤드)
  → POST /api/foreign/search           백그라운드 스레드
      → ForeignPriceAgent.search_all(identity)
          국가별 search_one_country  ─ 실패는 classify_scrape_error 로 분류
             (login / http_403 / config / dependency / timeout / parse / other)
             → 원장 ops_incident  foreign_search:{국가}:{분류}  (재발 시 같은 행 재개)
          A8 조정가 = per-unit 현지가 × KEB 환율 × factory_ratio × (1+10%) × (1+8.69%)
          일일투약비용 = 제형별 단위(oral=정, injection=vial 총 mg)로 계산 — 모르면 계산하지 않음
          compact_foreign_prices → 국가별 최신 세션만 유지
  → 화면 국가 카드: status = priced | no_result | scraper_error
       "수집 실패(로그인 오류) · 다시 시도"  vs  "제도상 비공개/미등재"
```

### 국가별 현황

| 국가 | 소스 | 로그인 | 지금 현재가를 가져올 수 있나 |
|---|---|---|---|
| DE | Rote Liste (DocCheck) | 필요 | 가능. 포장수량 미상 행은 평균 집계에서 제외 |
| IT | AIFA Class A/H CSV + TrovaNorme | 불필요 | 가능. CSV 캐시 30일 TTL, 갱신 실패 시 화면 경고 |
| FR | BDPM 공개파일 + Vidal | Vidal 선택 | 경구 공개약만. 병원 주사제는 Vidal 실측 필요 |
| CH | compendium.ch | 불필요(프록시 권장) | 팩 가격만 — 포장수량 파싱 미완 |
| UK | MIMS (scrapling) | 필요 + 프록시 | 미검증 — 실패는 이제 "설정/로그인 오류"로 드러남 |
| JP | 厚労省 薬価基準 xlsx | 불필요(프록시 권장) | INN→브랜드 라우팅 오프라인 검증. 캐시가 2026-03 공시라 갱신 필요 |
| US | Micromedex Red Book (WAC) | 필요 | **불가** — 로그인 실패 |
| CA | Ontario EAP | 불필요 | 제도상 공백(자사 제품 미등재) |

---

## 4. 감시: 가격이 멈추면 어디에 드러나나

`agents/agent_tasks/freshness.py` (매일 07:45):

| 도메인 | 판정 | 런북 |
|---|---|---|
| `exchange_rate_cache` | 캐시 창 종료일 < 오늘 있어야 할 창(전월 말) | `exchange_rate_refresh` |
| `foreign_price_fx_alignment` | 최신 가격 행의 환율창 ≠ 오늘 있어야 할 창 (캐시 파일이 아니라 기대 창과 비교 — 캐시가 밀려 있으면 예전엔 "ok"였다) | `exchange_rate_refresh` / `exchange_rate_recompute` |
| `foreign_price_age` | 추적 제품마다 가격 있는 최신 행이 **45일** 초과 | 없음(사용자 재검색) |
| `foreign_country_coverage` | 최근 **90일** 가격 행이 없는 국가 — 제도상 공백(CA, FR 병원주사제)은 제외 | 없음 |

`scripts/smoke.py` (매일 07:50) 해외약가 화면: `/api/foreign/cached?q=keytruda` 에 **조정가 있는 국가가 1개 이상**, `/api/foreign/country-overview?query=keytruda`, `/api/foreign/drugs`, `/api/foreign/available_countries`.

`foreign_config_probe` (매일 07:40): 로컬 기준 프록시 3건(JP·CH·UK) 미설정 보고. 자격증명 키는 모두 존재.

---

## 5. 환율 잡 상세와 "왜 멈춰 있나"

### 동작

1. `_calc_date_range(오늘)` → 36개월 창 (예: 10/2 실행 → 2023-10-03 ~ 2026-09-30)
2. Playwright 로 KEB 평균환율 페이지 → 기간평균 · **고시회차 '최종'**(`tmpPbldDvCd_2`) · 기간 입력 → 조회 → 엑셀 다운로드
3. `data/foreign/exchange_rate/keb_avg_rate_{from}_{to}.xlsx` 로 저장 → 파싱(EUC-KR TSV, 매매기준율)
4. `recompute_foreign_fx(rates, meta)` — 조정가는 환율에 선형이라 new/old 비율로 기존 최신 행을 스케일해 새 행 추가
5. 검색 시 `ExchangeRateFetcher._load_latest_cache()` 가 **파일명이 가장 최신인 캐시**를 읽는다

### 프로덕션 실제 이력 (2026-10-08 읽기 전용 확인)

```
keb_avg_rate_20230603_20260531.xlsx  15971  Jun 14  (수동)
keb_avg_rate_20230703_20260630.xlsx  15971  Jul  2 04:00  ✓
keb_avg_rate_20230803_20260731.xlsx  15971  Aug  2 04:00  ✓
keb_avg_rate_20230903_20260831.xlsx  15971  Sep  2 04:00  ✓
keb_avg_rate_20231003_20260930.xlsx      0  Oct  2 04:00  ✗ 빈 파일
```
```
2026-10-02 04:00:14 [ERROR] KEB 환율 갱신 실패: 헤더 행을 찾을 수 없음: keb_avg_rate_20231003_20260930.xlsx
2026-10-02 04:00:14 [INFO]  Job "KEB 36개월 평균환율 월간 갱신" executed successfully
```

**프로덕션 잡은 7·8·9월 정상으로 돌았고, 10월 2일에 실패했다.** KEB 가 빈 응답(0바이트)을 내려줬는데 코드가 그것을 검증 없이 최종 파일명으로 저장했고, 구버전 코드가 예외를 삼켜 "executed successfully" 로 기록됐다. 빈 응답의 원인(KEB 페이지 변경·일시 장애·조회 타이밍)은 재실행 실측이 필요하다.

**더 큰 문제 — 잠복한 장애**: `_load_latest_cache()` 는 파일명이 가장 최신인 파일 **하나만** 읽는다. 지금 그 파일이 0바이트라, **다음 해외약가 검색은 환율 로드에서 예외가 나 조정가를 계산하지 못한다.** 10/2 이후 프로덕션에서 해외 검색이 한 번도 없어서 아직 아무도 겪지 않았을 뿐이다.

### 로컬 캐시가 5월에 멈춘 이유 (프로덕션과 별개)

로컬 맥의 launchd 스케줄러는 8/2 에 절전으로 2시간 늦게 깨어났고(당시 유예 1초 → 폐기), 9/2 는 4일 넘게 절전 상태였으며, 9/18 이후 꺼져 있다. 로컬 캐시는 사용자 화면과 무관하다.

### 조치

| 단계 | 내용 | 상태 |
|---|---|---|
| 코드 | 다운로드를 임시 파일로 받아 검증(비어 있지 않음·파싱 성공·주요 통화 존재) 후에만 캐시명으로 교체. 캐시 로드는 최신→과거 순으로 **유효한 파일**을 고르고, 건너뛴 파일을 드러낸다 | 완료 (main, 미배포) — 재시도 1회(60초) 포함 |
| 즉시 완화(선택) | 프로덕션 볼륨의 0바이트 파일 삭제 → 9/2 캐시(2023-09-03~2026-08-31)로 복귀 | **사용자 확인 필요** (프로덕션 쓰기) |
| 재실행 | 배포 후 `fly ssh console -C "python scheduler.py --fx-refresh-now"` → 성공 시 기존 행 재계산까지 자동 | 배포 후 |
| 감시 | P1.5 이후 실패는 원장 과제로 남고, `exchange_rate_cache` 신선도가 매일 07:45 에 다시 잡는다 | 배포 시 활성화 |

---

## 6. 프로덕션(현재) vs main(미배포)

| 항목 | 프로덕션 (9/30 배포본) | main |
|---|---|---|
| 잡 실패 | 예외를 삼키고 "executed successfully" | `guarded_job` → 원장 과제, `/api/health.jobs_failed_24h` |
| 놓친 실행 | 유예 1초(대부분) | 일간 6시간 / 월간·분기 24시간 + `EVENT_JOB_MISSED` 신호 |
| 해외 검색 실패 | 로그만, 화면엔 "구독 필요" 등 정책 문구 | 분류 → 원장 + 화면 "수집 실패(○○ 오류)" |
| 가격 정체·국가 공백 | 감지 없음 | 매일 07:45 신선도 판정 |
| 설정 점검·스모크 | 없음 | 07:40 프로브, 07:50 스모크 |
| 빈 환율 파일 | 캐시를 오염시킴 | 차단 — 빈·깨진 파일은 캐시로 교체되지 않고, 로드 시 건너뛰고 기록 |

---

## 7. 수동 명령

```bash
python scheduler.py --fx-refresh-now        # 환율 갱신 + 기존 행 재계산
python scheduler.py --fx-recompute-now      # 재계산만 (캐시 그대로)
python scheduler.py --price-backfill-now    # 8개국 일괄 백필 (US 로그인 복구 전에는 비권장)
python scheduler.py --config-probe-now      # 자격증명·의존성·프록시 점검 (네트워크 없음)
python scheduler.py --freshness-now         # 신선도 판정 즉시 실행
python scheduler.py --smoke-now             # 화면 스모크 (웹 서버 필요)
```

프로덕션에서는 `fly ssh console -a ma-ai-dossier -C "python scheduler.py <플래그>"`.
