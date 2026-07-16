# 해외약가 수집 기능 — 이관 문서 (Foreign Price Module Handoff)

MA AI Dossier의 **8개국 해외 약가 자동 수집 + 한국 A8 조정가 환산** 서브시스템 전체 정리.
다른 프로젝트로 그대로 포팅하기 위한 아키텍처·규칙·핵심 코드 위치를 담는다.

---

## 1. 개요

- **목적**: 약제명(영문) 하나를 입력하면 8개국의 현지 약가를 스크레이핑하고, 한국 재정영향분석 표준(A8)에 맞춰 **per-unit(정/바이알 1개당) KRW 조정가**로 환산해 DB에 적재.
- **대상 8개국**: `JP, IT, FR, CH, UK, DE, CA, US`
- **데이터 흐름 (단방향)**: `국가별 스크레이퍼 → 환율/조정가 계산 → SQLite DB → API/대쉬보드`. 대쉬보드는 DB만 읽는다.
- **핵심 설계 원칙 2가지**:
  1. **제형(form_type) 단위 저장** — 같은 약도 oral/injection 가격이 다르므로 브랜드 단위로 합치지 않는다.
  2. **최소단위(minimum unit)는 form_type이 결정** — oral=1 tablet, injection=1 vial. 모든 가격 산출(pack_count, per_unit, unit_mg, daily_cost)이 이 단위 위에서 일관.

---

## 2. 파일 맵 (포팅 대상)

| 역할 | 파일 |
|------|------|
| **오케스트레이터** (검색·환율·계산·저장 조율) | `agents/foreign_price_agent.py` |
| 스크레이퍼 추상 베이스 (Playwright, 로그인/검색/파싱 인터페이스) | `agents/scrapers/base.py` |
| 제형 판정 (oral/injection/unknown) | `agents/scrapers/formulation.py` |
| 환율 페처 + A8 계산기 (`ExchangeRateFetcher`, `PriceCalculator`) | `agents/exchange_rate.py` |
| 국가간 용량 정규화 (제형 그룹핑 + LLM 보정) | `agents/foreign_dose_normalize.py` |
| DB 레이어 (`save_foreign_price` 등) | `agents/db/foreign.py`, 스키마 `agents/db/schema.py` |
| 리포트/엑셀 생성 | `agents/foreign_report.py` |
| **국가별 스크레이퍼 8종** | 아래 표 |
| API 엔드포인트 (비동기 job) | `api/server.py` `/api/foreign/search` |
| 스케줄러 (월간 환율 자동갱신) | `scheduler.py` `exchange_rate_refresh_job` |
| **권위 규칙 문서** | `agents/rules/foreign_agent_rules.md` |

### 국가별 스크레이퍼

| 국가 | 파일 | 소스 | 로그인 | SOURCE_TYPE | 통화 |
|------|------|------|--------|-------------|------|
| JP | `agents/scrapers/jp_mhlw.py` | MHLW 薬価基準 Excel (内用/注射/外用 워크북) | ✗ | None | JPY |
| IT | `agents/scrapers/it_aifa.py` | AIFA Class A(retail)+Class H(ex-factory) CSV | ✗ | `aifa_exfactory` | EUR |
| FR | `agents/scrapers/fr_bdpm.py` | BDPM (CIS_bdpm.txt + CIS_CIP_bdpm.txt) | ✗ | None | EUR |
| CH | `agents/scrapers/ch_compendium.py` | Swissmedic Compendium | ✗ | `ch_compendium` | CHF |
| UK | `agents/scrapers/uk_mims.py` | MIMS | ✓ | None | GBP |
| DE | `agents/scrapers/de_rote_liste.py` | Rote Liste | ✓ | None | EUR |
| CA | `agents/scrapers/ca_ontario.py` | Ontario EAP HTML 테이블 (온타리오주 대표) | ✗ | None | CAD |
| US | `agents/scrapers/us_micromedex.py` | Micromedex Red Book (**WAC** 우선) | ✓ | None | USD |

> FR 대체 `fr_vidal.py`, DE 대체 `de_gelbe_liste.py` 도 존재(현재 미사용/fallback).

---

## 2-1. 국가별 약가 확인 소스 & 방법 (상세)

각 국가 스크레이퍼가 **어디서 · 어떻게 · 어떤 가격 필드**를 가져오는지 정리. 포팅 시 이 부분이 가장 국가 고유성이 크다.

### 🇯🇵 JP — MHLW 薬価基準収載品目リスト (`jp_mhlw.py`)
- **소스**: 후생노동성 공식 약가기준 게시 페이지 → 엑셀 3종 다운로드
  - 페이지: `https://www.mhlw.go.jp/topics/2025/04/tp20250401-01.html`
  - `_01.xlsx`=内用薬(경구), `_02.xlsx`=注射薬(주사), `_03.xlsx`=外用薬(외용). 파일명 날짜(`tp20260318`)는 갱신마다 바뀌므로 페이지에서 `_01~_03` 링크를 동적 탐색(`_find_excel_urls`).
- **검색 방법**: 영문 약제명 → `EN_JP_NAME_MAP`으로 카타카나 변환("keytruda"→"キイトルーダ") 후 엑셀 品名 컬럼 부분일치. 매핑 없으면 영문 그대로 부분일치. `add_name_mapping()`으로 런타임 확장.
- **가격 필드**: 엑셀 薬価 컬럼(정/바이알당 円, 이미 per-unit). `"1,234.56"` → `float`.
- **제형**: 워크북 카테고리(内用/注射/外用)가 그대로 `dosage_form` → oral/injection 분리.
- **과금**: per-unit (pack_count=1). **로그인 불필요**.

### 🇮🇹 IT — AIFA Liste Classe A/H (`it_aifa.py`)
- **소스**: `https://www.aifa.gov.it/en/liste-farmaci-a-h` 페이지에서 CSV 링크 정규식 추출 → 다운로드
  - `Classe_A_per_nome_commerciale...csv`, `Classe_H_...csv` (requests, `Referer` 헤더 필요).
- **검색 방법**: CSV 로드 → `Denominazione`(상품명) 부분일치 (`_search_rows`).
- **가격 필드 (중요)**:
  - **Class H** → `Prezzo Ex-factory` 컬럼 사용, `SOURCE_TYPE="aifa_exfactory"` → factory_ratio **1.0** (이미 공장도가).
  - **Class A** → `Prezzo al pubblico`(소비자가) 컬럼, `SOURCE_TYPE=None` → 기본 ratio 0.93.
  - IT 숫자: **comma=소수점** (`"1.234,56"` → `1234.56`).
- **컬럼 자동탐지**: `Denominazione/Principio/Ex-factory/Prezzo al pubblico/Titolare/AIC/Gruppo` 키워드 매칭. **로그인 불필요**.

### 🇫🇷 FR — BDPM/ANSM 공개 DB (`fr_bdpm.py`)
- **소스**: 프랑스 공개 약품 DB 텍스트 덤프 2종 (24h 캐시)
  - `CIS_bdpm.txt` (제품 마스터: CIS·제품명·제형·경로·회사), `CIS_CIP_bdpm.txt` (포장·가격).
  - `https://base-donnees-publique.medicaments.gouv.fr/download/file/...`
- **검색 방법**: `CIS_bdpm.txt`에서 제품명 부분일치로 CIS 코드 추출(`_find_matching_cis`) → `CIS_CIP_bdpm.txt`에서 해당 CIS의 포장·가격 조인(`_find_packages`).
- **가격 필드**: `prix_ttc`(부가세 포함, 컬럼11) 우선, 없으면 `prix_sans`(HT, 컬럼10). FR 숫자 파싱(`_parse_fr_price`). `SOURCE_TYPE="vidal"` → ratio **0.65**.
- **주의**: 주사제는 CIP 공시가가 없는 경우 많음 → `local_price=None` 허용. 성분명은 브랜드명만 기록(enrichment는 별도 에이전트). **로그인 불필요**.

### 🇨🇭 CH — compendium.ch (`ch_compendium.py`)
- **소스**: `https://www.compendium.ch`
  - autocomplete API: `/search/autocomplete` (mnr+slug 획득) → 제품 상세 `/product/{mnr}/{slug}` HTML.
- **검색 방법**: 쿼리로 autocomplete 호출(`_autocomplete`) → 매칭 제품의 상세 HTML fetch → 정규식 파싱.
- **가격 필드**: HTML에서 `CHF X'XXX.XX`(스위스식 `'` 천단위) 또는 `Publikumspreis/VK-Preis` 라벨 뒤 숫자. `SOURCE_TYPE="compendium"` → ratio **0.65**.
- **부가 추출**: Wirkstoff(성분), Zulassungsinhaberin(제조사), SL 상태(급여여부). **로그인 불필요**.

### 🇬🇧 UK — MIMS online (`uk_mims.py`)
- **소스**: `https://www.mims.co.uk` (Google Referer로 공개 접근 — 명시적 로그인 없음)
- **검색 방법**: **DuckDuckGo HTML**(`https://html.duckduckgo.com/html/`)로 `mims.co.uk` 약제 URL 탐색(`_find_mims_urls`) → Playwright로 페이지 방문(`extra_http_headers={"Referer": "https://www.google.com/"}`).
- **가격 필드**: 페이지 텍스트의 `Price:` 섹션에서 `용량=£가격` 패턴 정규식 추출. 예: `100mg/4ml conc for soln for inf in vial, 2=£5260.00` → 용량별 여러 행.
- **주의**: `"log in or register"` + £ 없음 → 비급여/공개가 없음으로 빈 결과. 성분(How Supplied 아래 INN)·제조사(Manufacturer:) 함께 추출. `REQUIRES_LOGIN=False`(Referer 우회).

### 🇩🇪 DE — Rote Liste (`de_rote_liste.py`)
- **소스**: `https://www.rote-liste.de` — **DocCheck OAuth 로그인 필요**.
  - 로그인: `login.doccheck.com` → `auth.doccheck.com/de/authorize` → 302 redirect to `/login`(토큰 포함). requests 우선(`_login_requests`), 실패 시 Playwright fallback(`_login_playwright`).
- **검색 방법**: `GET /search?query={query}` → HTML에서 `/rle/detail/{prod_id}/{slug}` 링크 추출 → 상세 페이지 방문(`_extract_detail`).
- **가격 필드**: 상세 페이지 텍스트에서 `용량 + N123(pack) + PZN(8자리) + AVP` 패턴 정규식. AVP(EB)/FB 우선, 없으면 AVP/UVP. DE 숫자(`1.234,56`). 없으면 `NNN €` fallback. `SOURCE_TYPE=None` → ratio **0.6955**.
- **주의**: `ROTE_LISTE_DE_USERNAME/PASSWORD` 필요. 로그인 벽 감지 시 `login_wall` 마킹.

### 🇨🇦 CA — Ontario EAP Product Prices (`ca_ontario.py`)
- **소스**: `https://www.ontario.ca/page/exceptional-access-program-product-prices` (온타리오주 대표, 연방 단일 DB 없음. 24h 캐시)
- **검색 방법**: 페이지 HTML의 **연도별 테이블** 전체 파싱(BeautifulSoup). `h2`=연도, 테이블 헤더에 `trade name/din` 포함된 것만. DIN 기준 중복 제거(최신 연도 우선).
- **가격 필드**: 테이블 컬럼 `[DIN, Trade Name, Strength, Dosage Form, Price]`. `$238.7160` → float. `SOURCE_TYPE=None` → ratio **0.81**.
- **과금**: per-unit (pack_count=1). **로그인 불필요**.

### 🇺🇸 US — Micromedex Red Book (`us_micromedex.py`)
- **소스**: `https://www.micromedexsolutions.com` — **로그인 필요 + 좌석(seat) 동시성 제한**.
  - 로그인: `HOME_URL` → dispatch → Red Book 검색 폼. `MICROMEDEX_US_USERNAME/PASSWORD`. 라이선스 한도(`LICENSE_LIMIT_PATTERNS`) 감지 시 최대 3회 재시도(25s 간격), `asyncio.Lock`으로 seat 직렬화. 종료 시 반드시 로그아웃(응급 로그아웃 핸들러 포함).
- **검색 방법**: Red Book 검색어 입력(`WordWheel_SearchTerm`) → 제출 → 결과 테이블(`tr.rowBeige/rowWhite`) 파싱. 각 행 19개 `td.rbProductCell`.
- **가격 필드 (중요)**: `[16] WAC package price` **우선**(ex-manufacturer). `[17] AWP package`는 유통 마크업 포함이라 factory_ratio 0.74와 **double-count → WAC 없을 때만 fallback**. `price_basis`에 `WAC_package`/`AWP_package` 기록. `SOURCE_TYPE="redbook_wac"`, ratio **0.74**.
- **부가**: NDC 코드, Form(SOL/TAB/INJ), Strength, Package size(`4 ml 2s`=2 vials), Route. `"--"` 행은 alternate NDC로 skip.

### 소스 유형 요약

| 방식 | 국가 | 접근 |
|------|------|------|
| 파일 다운로드 (Excel/CSV/TXT) | JP, IT, FR | requests/Playwright, 공개 |
| HTML 파싱 (직접 URL) | CH, CA | requests + BeautifulSoup/정규식, 공개 |
| 검색엔진 우회 → HTML | UK | DuckDuckGo → Playwright, Referer 우회 |
| 로그인 + HTML/테이블 | DE, US | OAuth(DE) / 세션+seat(US) |

---

## 3. 아키텍처 & 실행 흐름

```
사용자/API: query="keytruda"
     │
     ▼
ForeignPriceAgent.search_all(query, countries)
     │  for each country:
     ▼
ForeignPriceAgent.search_one_country(query, country)
     │
     ├─ 1) _build_scraper(country) → BaseScraper.run(query)
     │       로그인(필요시) → search() → 결과 파싱 → form_type 부착
     │
     ├─ 2) 환율: ExchangeRateFetcher.get_36m_average(currency)   (캐시만 소비)
     │
     ├─ 3) pack_count 결정: _resolve_pack_count()  (form_type 우선)
     │
     ├─ 4) A8 조정가: PriceCalculator.calculate_adjusted_price()
     │
     ├─ 5) daily_cost 계산: _populate_daily_cost()
     │
     └─ 6) DB 저장: db.save_foreign_price()   (append-only)
     │
     ▼ (search_all 종료 후 후처리)
_normalize_doses_across_countries()  → process_formulations()
     제형 그룹핑 + 국가간 표시단위 보정(불일치 시 LLM) → DB UPDATE
```

### BaseScraper 계약 (신규 국가 추가 시 구현할 것)

서브클래스는 클래스 속성 `COUNTRY / CURRENCY / SOURCE_LABEL / REQUIRES_LOGIN`(+ 선택 `SOURCE_TYPE`)을 정의하고 `search(query, page)`를 구현. `run()`이 로그인→검색→로그아웃→form_type 부착→DB dict 변환까지 공통 처리.

`search()` 반환 각 item 형식:
```python
{
    "product_name": str,     # 해당국 제품명
    "ingredient": str,       # 성분명
    "dosage_strength": str,  # 함량 (예: "100 mg/4 mL")
    "dosage_form": str,      # 제형 (로컬 약어 가능)
    "package_unit": str,     # 포장단위
    "local_price": float,    # 현지 통화 가격 (없으면 None 허용)
    "source_url": str,
    "extra": dict,           # 원본 raw (form_type 자동추론에 사용)
    # 선택: "form_type", "pack_count" 를 직접 넣으면 우선 채택
}
```

---

## 4. A8 조정가 공식 (핵심 IP)

`PriceCalculator` (`agents/exchange_rate.py`). 반환 `adjusted_price_krw`는 **per-unit KRW** (pack 아님).

```
A8_adj_per_unit_KRW =
    per_unit_local                       ← listed_price / pack_count
    × exchange_rate                      ← KEB 36개월 평균 (JPY는 /100 정규화)
    × factory_ratio(country, source_type)← 국가별 공장도 출하 비율
    × (1 + KR_VAT       = 0.10)          ← 한국 부가세 (전국가 공통 상수)
    × (1 + KR_DIST_MARGIN = 0.0869)      ← 한국 유통거래폭 (전국가 공통 상수)
```

**주의**: `KR_VAT`/`KR_DIST_MARGIN`은 한국 A8 기준 상수(외국 ex-factory→한국 retail 등가 uplift). 국가별 VAT(DE 19%, JP 10% 등)와 혼동 금지 — 국가별 VAT는 이미 factory_ratio에 반영됨.

### 국가별 factory_ratio (local retail → 해당국 ex-factory)

| US | UK | DE | FR | IT | CH | JP | CA |
|----|----|----|----|----|----|----|----|
| 0.74 | 0.73 | 0.6955 | 0.77 | 0.93 | 0.73 | 0.79 | 0.81 |

source_type 오버라이드: `aifa_exfactory`=1.0 (IT Class H 이미 ex-factory), `ch_compendium`=0.65, `fr_vidal`=0.65.

### pack_count (per-unit 환산 기준) — `_resolve_pack_count`, form_type 우선
1. scraper가 item에 직접 제공 → 최우선
2. **oral 전용**: `total_pkg_mg / unit_mg` ratio (예: 3600/40=90). **injection 금지** (농도×volume/농도=volume → pack 아님)
3. pack-pricing 국가(`{UK,US,DE,FR,IT,CH}`)에서 `dosing.default_pack_count` fallback (oral만)
4. 기본 1 (JP/CA는 per-unit 과금)

### daily_cost_krw
```
daily_cost_krw = daily_dose_mg × (adjusted_price_krw / unit_mg)
```
- `unit_mg` = `_extract_per_unit_mg(form_type, ...)` — oral=per-tablet mg, injection=per-vial **총** mg (농도×volume 복원)
- Sanity cap: > ₩10M/day → `daily_cost_krw=None`, `note="suspicious_outlier"`

---

## 5. 환율 (KEB하나은행)

- **36개월 평균환율**, 고시회차는 **반드시 '최종'** (`tmpPbldDvCd_2` value=0=최종). KEB 폼은 `inputCheck()`가 제출 시 체크된 라디오값으로 hidden `pbldDvCd`를 재생성하므로 **라디오 체크가 결정적**.
- **JPY 주의**: KEB는 "100엔당 KRW"로 고시 → parse 시 `/100` + calculator에서 `>100`이면 `/100` 이중 방어.
- EUR은 FR/IT/DE 공용.
- **캐시 = 단일 소스 (수집 1 / 소비 다수)**:
  - 수집(크롤)은 월간 잡 하나뿐: `scheduler.exchange_rate_refresh_job` (매월 2일 04:00 KST) → `data/foreign/exchange_rate/keb_avg_rate_{from}_{to}.xlsx` 덮어씀.
  - 검색은 크롤 안 함 — `get_36m_average` → `_load_latest_cache`로 캐시만 읽음. 신규 검색은 현재 캐시값으로 즉시 계산(환율 동결).
  - 기존 행 갱신: 월간 잡이 `DrugPriceDB.recompute_foreign_fx()`로 최신행을 **선형 스케일**(new/old rate) 재계산해 새 행 append (adj는 환율에 선형 → 재스크레이프 불필요).
  - 수동: `scheduler.py --fx-refresh-now`(갱신+재계산) / `--fx-recompute-now`(재계산만).

---

## 6. DB 스키마 (`foreign_drug_prices`)

`agents/db/schema.py`. append-only. 최신행이 노출 기준.

주요 컬럼: `searched_at, query_name, country, product_name, ingredient, dosage_strength, dosage_form, package_unit, local_price, currency, exchange_rate, exchange_rate_from/to, factory_price_krw, vat_rate(=0.10), distribution_margin(=0.0869), adjusted_price_krw(per-unit KRW), pack_count, per_unit_local, total_pkg_mg, daily_dose_mg, daily_cost_krw, daily_cost_note, source_url, source_label, raw_data, form_type`

용량 정규화 컬럼: `unit_strength_mg, reference_strength_mg, dose_norm_factor, adjusted_price_krw_normalized, dose_norm_note`

인덱스: `(query_name, country)`, `(searched_at)`.

---

## 7. 국가간 용량 정규화 (per-unit 비교 공정성)

`agents/foreign_dose_normalize.py` — `search_all` 후처리.

- 문제: adj가 per-unit이라 국가별 최소단위 강도(mg)가 다르면 비교 불가 (예: Prevymis 일본 20mg정 vs 타국 240mg정 → 12배 싸 보임).
- 해법: 제형(강도×경로)별 그룹핑(US canonical 기준) + 제형 내 표시단위 보정. regex로 strength 추출 → **2종 이상 불일치 시에만 GPT-4o 호출**로 `reference_strength_mg`·국가별 `factor` 판단.
- `adjusted_price_krw_normalized = adjusted_price_krw × factor`. 비교(min/avg/max·그래프)는 normalized 기본, 없으면 raw fallback.
- 동일 제형·단일 활성성분만 보정. 복합제/강도불명은 factor=null + `dose_norm_note` 사유.

---

## 8. 자격증명 (`config/.env`, 하드코딩 금지)

로그인 필요 국가만. `base.py::load_credentials`의 `ENV_KEY_MAP`:
```
UK → MIMS_UK_USERNAME / MIMS_UK_PASSWORD
US → MICROMEDEX_US_USERNAME / MICROMEDEX_US_PASSWORD
DE → ROTE_LISTE_DE_USERNAME / ROTE_LISTE_DE_PASSWORD
```
JP/IT/FR/CH/CA는 공개 접근(로그인 불필요).

---

## 9. 신규 국가 추가 절차

1. `agents/scrapers/{cc}_{source}.py` 생성 (`BaseScraper` 상속, `search()` 구현)
2. `foreign_price_agent.py`의 `_build_scraper()`에 케이스 추가 + `AVAILABLE_COUNTRIES`에 등록
3. 로그인 필요 시 `base.py` `ENV_KEY_MAP` + `config/.env` 키 추가
4. `PriceCalculator.FACTORY_RATIO`에 국가 ratio 추가, `COUNTRY_CURRENCY`에 통화 추가
5. 로컬 제형 약어를 `formulation.py`의 ORAL/INJECTION_KEYWORDS에 추가
6. **검증 필수 2케이스**: Welireg(경구·pack count) + Keytruda(주사·농도×volume)

---

## 10. 회귀 방지 — 과거 실수 (반드시 숙지)

- **injection pack_count 오인식**: `total_mg/unit_mg` ratio는 injection에서 volume 반환 → oral에서만 허용.
- **injection daily_cost 분모**: per-mL 농도가 아니라 per-vial 총 mg 사용 (`_extract_per_unit_mg`).
- **US는 WAC만**: AWP는 유통 마크업 포함 → factory_ratio 0.74와 double-count. WAC package 우선.
- **해외 daily_cost 분모**: 단위강도 X, 포장당 총 mg O (tablet count 누락 시 10~100× 과대).
- **KEB 환율**: '최초' 아닌 '최종' 회차. 월간 자동갱신 필수(stale 방지).

### 회귀 테스트
- `tests/test_welireg_excel_baseline.py` — 재정영향분석 엑셀 5개국 per-tablet 값과 ±1% 수렴 검증. PriceCalculator 변경 시 필수 통과.

| country | local_pack | pack_count | factory_ratio | Excel per-tablet KRW |
|---------|-----------|------------|---------------|---------------------|
| UK | 11,936.70 GBP | 90 | 0.73 | 197,684 |
| US | 31,162.50 USD | 90 | 0.74 | 414,126 |
| CA | 213.33 CAD | 1 | 0.81 | 204,246 |
| JP | 21,916.80 JPY | 1 | 0.79 | 189,534 |
| DE | 17,830.31 EUR | 90 | 0.6955 | 240,163 |

---

## 11. 이관 시 의존성 체크리스트

- **Playwright** (chromium) — 스크레이퍼 전부. Docker/서버에 chromium 설치 필요.
- **pandas** — JP MHLW / IT AIFA 엑셀·CSV 파싱.
- **openai (GPT-4o)** — 용량 정규화 LLM (선택. 불일치 감지 시에만 호출).
- **SQLite** — `agents/db/*`. 다른 DB로 옮기려면 `save_foreign_price`/`get_foreign_prices`/`recompute_foreign_fx`만 재구현.
- **환율 캐시 디렉토리** — `data/foreign/exchange_rate/`.
- 스케줄러(APScheduler) — 월간 환율 갱신 잡. 없으면 `--fx-refresh-now` 수동 실행으로 대체.

포팅 최소 세트: `foreign_price_agent.py` + `scrapers/{base,formulation,국가별}.py` + `exchange_rate.py` + `foreign_dose_normalize.py` + `db/{foreign,schema}.py` + `rules/foreign_agent_rules.md`.
