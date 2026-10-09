# 제외국(해외) 약가 검색 — 인수인계 (2026-10-09)

> 대상: 이 작업을 이어받는 에이전트. 이 문서만 읽고 코드베이스에서 바로 작업을 시작할 수 있도록 썼다.
> 저장소: `/Users/kimjeong-ae/MA_AI_Dossier`, 브랜치 `main` (HEAD `bad46cd0` 이후). 아래 내용은 **로컬 main 기준이며 프로덕션에는 아직 배포되지 않았다** (프로덕션은 2026-09-30 배포본).
> 함께 읽을 것: `docs/agent_team/FOREIGN_PRICE_JOBS.md` (크론잡·환율 상세), `CLAUDE.md`, `agents/rules/foreign_agent_rules.md`, `agents/rules/scraper_rules.md`.

---

## 1. 목적과 현재 판단 기준

해외약가는 한국 약가 협상·재정영향분석의 **A8 제외국 조정가**를 만드는 기능이다. 8개국(JP, IT, FR, CH, UK, DE, CA, US)의 현지 약가를 수집해 KEB 36개월 평균환율과 국가별 factory_ratio 로 원화 조정가와 일일투약비용을 계산한다.

사용자(MA 리드)가 정한 판단 기준:

> **"중요한건 현 시점에 해당 하는 약제의 가격을 가지고 올 수 있느냐야."**

그리고 **"문서는 과거고 코드는 현재 시점을 돈다"** — 규칙 문서와 코드가 다르면 코드의 현재 동작을 진실로 보고 문서를 고친다. 저장된 값을 문서 기준으로 재계산하지 않는다.

---

## 2. 절대 지켜야 할 규칙 (CLAUDE.md 발췌)

- `msd_only=True` 하드코딩 금지
- 가격이 없을 때 임의 값 반환 금지 — 모르면 `None`, 계산하지 않음
- **US 는 Micromedex WAC 만** — AWP 금지 (factory_ratio 0.74 와 이중 계산)
- **injection 에서 `total_mg/unit_mg` 비율로 pack_count 추론 금지** (농도×용량이라 용량을 반환)
- **injection 에서 per-mL 농도를 daily_cost 분모로 쓰기 금지** — per-vial 총량만
- 자격증명은 `config/.env` 에만. 코드·로그·문서에 값 금지 (키 **이름**만 언급 가능)
- 환율은 KEB **'최종' 고시회차**만 (`tmpPbldDvCd_2`). 라디오 체크가 결정적이다
- **신규·변경 로직은 Welireg(경구·팩 수량)와 Keytruda(주사·농도×용량) 양쪽으로 검증** — `tests/test_welireg_excel_baseline.py`, `tests/test_keytruda_injection_baseline.py` 가 항상 통과해야 한다
- 최소단위는 form_type 이 결정한다: oral = 정, injection = vial

---

## 3. 구조

```
해외약가 화면 (frontend/src/pages/international-pricing/page.tsx, api: frontend/src/api/foreign.ts)
  ├ GET  /api/foreign/resolve            브랜드↔INN 확정 (agents/drug_identity.py)
  ├ POST /api/foreign/search             백그라운드 검색 시작 → job_id
  ├ GET  /api/foreign/search/status/<id> 진행·국가별 결과(country_results)
  ├ GET  /api/foreign/cached?q=          저장 행을 읽어 재계산해 표시 (get_cached_results)
  ├ GET  /api/foreign/country-overview?query=   국가 카드 (저장 컬럼 그대로 — 주의 §6)
  ├ POST /api/foreign/report/export      xlsx/hwpx (agents/foreign_report.py)
  └ GET  /api/foreign/drugs, /available_countries

ForeignPriceAgent (agents/foreign_price_agent.py)
  search_all → search_one_country(국가별) → 스크레이퍼 run()
    → pack_count 결정(_resolve_pack_count / _extract_unit_count, pack_count_source 기록)
    → 조정가(PriceCalculator, agents/exchange_rate.py)
    → 일일투약비용(_populate_daily_cost / _extract_per_unit_mg)
    → 저장(agents/db/foreign.py) → compact_foreign_prices (국가별 최신 세션만)
    → 제형 그룹핑(agents/foreign_dose_normalize.py)

스크레이퍼 (agents/scrapers/)
  jp_mhlw · it_aifa(CSV) + it_aifa_tnf · fr_bdpm + fr_vidal · ch_compendium
  uk_mims · de_rote_liste (de_gelbe_liste 미배선) · ca_ontario · us_micromedex
  base.py: BaseScraper, ENV_KEY_MAP, REQUIRES_LOGIN, LOGIN_OPTIONAL, PROXY_ENV_KEYS,
           ScraperConfigError, ScraperLoginError, warnings
  country_coverage.py: 국가별 정책(제도상 공백 is_structural_absence, describe_empty_result)
```

### 핵심 계산

- **A8 조정가 (per-UNIT KRW)** = 현지 단위가 × KEB 환율 × factory_ratio × (1 + 0.10) × (1 + 0.0869)
- **factory_ratio (현재 실효값 — 코드가 진실)**: UK 0.73 · DE 0.6955 · CH 0.73 · FR 0.77 · US 0.74 · JP 0.79 · IT Class H/TNF ex-factory 1.0 · IT Class A 공중가 0.93. 행별 가격 유형이 정한다(`resolve_item_source_type`). `tests/test_foreign_factory_ratio_effective.py`, `tests/test_foreign_factory_ratio_item_source.py` 가 고정한다. (규칙 문서의 옛 0.65 는 발효된 적 없음 — 2026-10-06 정정)
- **JPY**: KEB 는 100엔당 고시. `per_unit_rate()` 한 곳에서 정규화. DB 에는 옛 행이 923.21(100엔당) 형식으로 남아 있지만 모든 소비처가 정규화한다
- **일일투약비용 sanity cap** ₩10M/day

---

## 4. 국가별 현황 — "지금 현재가를 가져올 수 있나"

| 국가 | 소스 · 인증 | 상태 | 남은 일 |
|---|---|---|---|
| **DE** | Rote Liste, DocCheck 로그인 (`ROTE_LISTE_DE_*`) | 가격 수집 가능. 포장수량(Packungsangaben "N St") 미파싱 → 일부 행 집계 제외 | 실제 상세 페이지 HTML 캡처 → 파서 |
| **IT** | AIFA Class A/H CSV + TrovaNorme JSON, 인증 없음 | 가능. CSV 캐시 30일 TTL, 갱신 실패 시 "캐시 만료" 경고. 두 채널이 같은 `searched_at` 공유 | 실제 CSV 갱신 1회 실측 |
| **FR** | BDPM 공개파일(경구) + Vidal(병원 주사제, 로그인 선택 `VIDAL_FR_*`) | 경구 공개약만 가능 | Vidal 로그인 + 키트루다 UCD 페이지 캡처(용량 "4 ml" 포함) |
| **CH** | compendium.ch, 프록시 권장 | 팩 가격만, 포장수량 없음 → 집계 제외 | 상품 페이지 Packungen 표("N Stk") 캡처 → 파서 |
| **UK** | MIMS(scrapling), 로그인 `MIMS_UK_*` + 프록시 `MIMS_UK_PROXY` | 미검증. 실패는 "설정/로그인 오류"로 드러남. INN→브랜드 라우팅 | 로그인 실측 |
| **JP** | 厚労省 薬価基準 xlsx, 인증 없음, 프록시 권장 | INN→일본어 브랜드 라우팅 오프라인 검증 완료. 캐시가 2026-03-18 공시 → "캐시 만료" 경고 | 薬価基準 목록의 고정 인덱스 페이지 URL 확인 + HTML 픽스처 |
| **US** | Micromedex Red Book WAC, 로그인 `MICROMEDEX_US_*` | **불가** — 2026-06-08 이후 로그인 33/34회 실패 | 로그인 진단(§7) |
| **CA** | Ontario EAP | 제도상 공백 — 자사 제품 미등재 (`is_structural_absence`) | 없음 |

데이터 현황(로컬 DB, 읽기 전용 기준): `foreign_drug_prices` 203행, 9개 query, 마지막 수집 **2026-07-28**. US·CA 0행.

---

## 5. 최근 수정 이력 (P1.6, 모두 main 병합·미배포)

**값 정확성**
- 키트루다 UK: "100mg/4ml … in vial, 2" 를 pack 1 로 읽어 2배 과대 → 공용 수량 추출기 `_extract_unit_count` 로 pack 2. per-vial ₩4,140,351, 일일 ₩394,319
- 키트루다 FR(Vidal #1504): per-mL 농도 "25 mg" 를 vial 총량으로 써 4배 과대 → 용량을 복원할 수 없으면 일일투약비용을 **계산하지 않음**(`daily_cost_note=unit_mg_unresolved`)
- 경구 팩: IT `cpr`, DE/CH `Stk/St`, FR `comprimé` 등 패턴 추가. 수량을 끝내 모르는 행은 `pack_count_source='default'` 로 표시하고 A8 평균·최소·최고에서 제외(`a8_aggregate_eligibility`). 화면에 "포장수량 미확인"
- JPY 보고서 적용환율 기재 정규화(`per_unit_rate`)
- QualityGuard 의 죽은 검증 함수 삭제 → 데이터 검사 `check_foreign_price_data` 를 매일 06:00 리뷰에 배선

**수집**
- IT CSV 30일 TTL, 다채널 국가 세션당 `searched_at` 하나, Class A 0.93
- JP INN 검색 → `product_alias_map.agency_brand_overrides["PMDA"]` / 별칭으로 일본어 브랜드 라우팅, 최신 공시 파일 탐색, 90일 TTL
- UK 자격증명 없음·로그인 벽 → `ScraperConfigError`/`ScraperLoginError`
- identity 없이 들어온 검색(백필·CLI)도 `derive_identity_offline` 으로 브랜드 유도

**관측**
- 국가별 검색 실패 분류 → 원장 `foreign_search:{국가}:{분류}`, 화면 "수집 실패(○○ 오류)" vs "제도상 비공개"
- 신선도: `foreign_price_age`(45일), `foreign_country_coverage`(90일), `foreign_price_fx_alignment`(기대 환율창 기준), `exchange_rate_cache`
- 설정 프로브 `foreign_config_probe` 매일 07:40(네트워크 없음), 스모크가 조정가 있는 국가 1개 이상 요구
- 환율: KEB 빈 다운로드(2026-10-02 프로덕션 0바이트 파일)가 캐시를 오염시키던 문제 → 임시 파일 검증 후 원자적 교체, 로더는 최신 **유효** 파일 선택, 재시도 1회

---

## 6. 알려진 문제 (미해결)

1. **저장 컬럼이 아직 틀린 행** — 캐시 화면(`get_cached_results`)은 읽을 때 재계산해서 교정되지만 `country-overview` 카드는 저장 컬럼을 그대로 쓴다.
   - 재계산으로 고칠 수 있음: UK #1300, IT CSV 85행(#1292–1293, #1304–1380, #1471–1474, #1478–1479), FR BDPM #1407·#1408·#1417, IT Class A 79행(ratio 1.0→0.93)
   - 재수집 필요: FR Vidal #1504, DE #1470(농도를 단위강도로 저장), CH #1430–#1465(36행), DE #1466·#1477·#1480
   - **실제 DB 를 고치기 전에 반드시 사용자 확인.** 프로덕션 DB 는 Fly 볼륨에 따로 있다
2. **가격 백필 잡은 꺼져 있다** (`foreign_price_backfill_job`, 수동 전용). US 로그인 복구와 JP/UK 실측 전에는 켜지 않는다
3. 프로덕션 환율 캐시에 0바이트 파일 `keb_avg_rate_20231003_20260930.xlsx` 가 있다. 새 로더는 건너뛰지만 배포 전 구버전은 이 파일 때문에 조정가 계산이 실패한다
4. `de_gelbe_liste.py` 는 존재하나 배선되지 않음
5. 워크벤치(`agents/workbench/assumptions.py`)는 KEB 캐시를 쓰지 않는 별도 하드코딩 환율·공식을 갖고 있다 — 파이프라인과 값이 다를 수 있음

---

## 7. 다음 작업 (실측 — 네트워크·자격증명 필요)

우선순위 순. 실측 결과 HTML/CSV 는 `tests/fixtures/<국가>/` 에 저장해 오프라인 테스트로 고정한다.

1. **US Micromedex 로그인 진단**
   - `scripts/dev_micromedex_session.py` — 로그인 1회 유지 REPL. `search Keytruda`, `dump <label>`(→ `data/foreign/us/dev_<label>.html`), `shot`, **`quit` 으로 반드시 로그아웃**(좌석 제한)
   - 로그인 직후 페이지를 dump 해서 "Please Login" / 라이선스 좌석 초과 / 폼 변경 중 무엇인지 판별. 좌석 초과면 코드가 아니라 벤더·관리자 문제
   - 로그인되면 `search Welireg` → dump → `scripts/test_micromedex_parse.py` 로 파서 검증(좌석 미사용)
   - 테스트는 **한 세션 로그인 유지**가 원칙(프로젝트 메모)
2. **키트루다·웰리렉 재수집** — 화면 검색 또는 `ForeignPriceAgent.search_all`. 결과로 Keytruda baseline 수치와 대조
3. **환율 재갱신** — `python scheduler.py --fx-refresh-now` (프로덕션: `fly ssh console -a ma-ai-dossier -C "..."`). 성공 시 기존 행 재계산까지 자동
4. CH Packungen 표, DE Packungsangaben, FR Vidal 포장문 캡처 → 수량·용량 파서
5. JP 薬価基準 인덱스 페이지 URL 확정 → `tests/fixtures/jp_mhlw/index.html`

---

## 8. 작업 방식 (이 프로젝트의 관례)

- **TDD**: 실패 테스트 → 구현 → 통과 → 커밋. 커밋 메시지는 한국어
- **실제 DB 보호**: `data/db/drug_prices.db`(1.4GB)는 운영 데이터. `tests/conftest.py` 가 테스트 중 실제 DB 연결을 읽기 전용으로 바꾸고 세션 끝에 지문을 비교한다 — 우회하지 말 것. 조사는 `sqlite3.connect("file:...?mode=ro", uri=True)`
- **테스트에서 네트워크·Playwright·LLM 금지** — 스크레이퍼는 저장된 픽스처로 검증
- **배포·`git push`·`flyctl deploy` 는 사용자만**. 프로덕션은 읽기 전용 진단(`fly ssh console` 로 ls/grep)만 허용, 쓰기는 사용자 확인 후
- **오탐 억제**: 원장 과제는 정상 상태에서 하루 3건 이하가 목표. 반복 신호는 `signals.record_recurring` 으로 같은 행을 재개
- 회귀 확인:
  ```bash
  .venv/bin/python -m pytest tests/ -q          # 기존 환경 의존 실패 1건(test_access_insight_link)만 허용
  .venv/bin/python -m pytest tests/test_keytruda_injection_baseline.py tests/test_welireg_excel_baseline.py -q
  cd frontend && npm run type-check && npm run build
  ```

### 관련 테스트 파일

`test_keytruda_injection_baseline` · `test_welireg_excel_baseline` · `test_foreign_pack_count_parsing` · `test_foreign_pack_count_source` · `test_foreign_injection_unit_mg` · `test_foreign_factory_ratio_effective` · `test_foreign_factory_ratio_item_source` · `test_foreign_channel_session` · `test_foreign_channel_failures` · `test_foreign_search_signals` · `test_foreign_search_job` · `test_foreign_config_probe` · `test_foreign_price_freshness` · `test_foreign_backfill_guard` · `test_it_aifa_csv_ttl` · `test_jp_mhlw_inn_routing` · `test_uk_mims_errors_and_routing` · `test_exchange_rate_cache_guard` · `test_exchange_rate_jpy_per_unit` · `test_exchange_rate_recompute_job`
