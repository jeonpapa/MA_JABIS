# ForeignPriceAgent 규칙

## 역할
국가별 스크레이퍼를 실행하고 환율 계산 후 DB에 저장.

## 구현된 국가 (AVAILABLE_COUNTRIES)
JP, IT, FR, CH, UK, DE, CA (US는 구독 확인 필요)

## 핵심 원칙: 제형(form_type) 단위 저장 + **최소단위는 form_type 이 결정**

동일 약제라도 국가별로 oral(경구제) 과 injection(주사제) 의 가격이 **다르다**.
브랜드(=query_name) 단위로 합쳐 저장하면 A8 비교가 깨진다.

- **모든 스크레이퍼는 검색 결과 각 row 에 `form_type` 을 붙여야 한다** (oral | injection | unknown)
- `BaseScraper._resolve_form_type()` 가 item 의 `dosage_form` / `product_name` / `extra` 를
  `agents/scrapers/formulation.py::detect_form()` 에 태워 자동 판정
- 스크레이퍼가 item 에 `form_type` 을 직접 넣으면 그 값 우선 (normalize_form_type 적용)
- 제형별 가격 차이가 큰 약제(예: Prevymis — UK oral tab, DE injection vial): 반드시 제형 분리 유지
- 대쉬보드는 `form_type` 필터(전체/oral/injection) 로 A8 비교 제공

### 최소단위(minimum unit) 원칙 — **모든 가격 산출의 기준**

스크레이핑 원문만으로는 "1 단위가 무엇인가"를 알 수 없다. **form_type 이 먼저 정해져야**
`pack_count`, `per_unit_local`, `unit_mg`, `daily_cost_krw` 모두가 같은 단위 위에서 일관된다.

| form_type | 최소단위 | per_unit_local 의미 | unit_mg 의미 |
|-----------|---------|---------------------|--------------|
| oral      | 1 tablet / capsule | pack_price ÷ tablet_count | per-tablet mg (예: 40) |
| injection | 1 vial / ampoule / syringe | pack_price ÷ vial_count | per-vial **total** mg (예: 100) |
| unknown   | 1 (per-unit 가정) | 그대로 | 산출 불가 — daily_cost skip |

**금지사항:**
- injection 에서 `total_mg / unit_mg` ratio 로 pack_count 추론 금지 — 이 비율은 **농도×volume/농도 = volume** 이라 pack count 와 무관 (예: Keytruda 100mg/4ml 25mg/ml → ratio=4 인데 실제는 1 vial).
- injection 에서 `_extract_mg()` (per-mL 농도) 를 unit_mg 로 사용 금지 — 분모가 per-mL 이면 daily_cost 가 4× 과대 또는 반대 방향으로 왜곡.
- **반드시 `_extract_per_unit_mg(form_type, ...)`** 사용: injection 은 "N mg/M mL (M>1)" 또는 "농도 × volume" 으로 per-vial 총량 복원, 복원 불가면 None → daily_cost 계산 skip.

**injection pack_count 파싱 허용 단서** (scraper 가 직접 못 넣을 때만):
- `(N x M)`, `N x M`
- `N vial/flacon/flaconcino/瓶/バイアル/ampoule`
- US Redbook `"4 ml 2s"` → 2 vials
- 마커 없으면 **1 vial 로 보수 fallback**

### 신규 국가 추가 시 form_type 체크리스트
1. 해당국 DB 의 제형 컬럼을 item["dosage_form"] 에 채운다
2. 로컬 약어(예: DE "Filmtabl", "Inf Konz") 를 `formulation.py` 의 ORAL_KEYWORDS / INJECTION_KEYWORDS 에 추가
3. Prevymis 로 검증 — oral/injection 각각 정확히 분류되는지 확인

### 파이프라인 단 form_type 보장 (2026-04-22)
form_type 주입은 **스크레이퍼 + 에이전트 이중 보장**으로 캐시/신규 검색 모두 커버한다.
1. **스크레이퍼 단**: `BaseScraper.run()` 이 item 마다 `self._resolve_form_type(item)` 호출하여 `form_type` 을 붙인다. `run()` 을 오버라이드한 스크레이퍼(us_micromedex / uk_mims / ca_ontario / fr_bdpm / de_rote_liste / de_gelbe_liste / fr_vidal)도 동일 훅을 호출한다.
2. **에이전트 단 (defense-in-depth)**: `ForeignPriceAgent.search_one_country` 가 scraper 실행 직후 `raw_results` 를 순회하며 `form_type` 이 비어있으면 `scraper._resolve_form_type(item)` 로 채운다. **신규 스크레이퍼가 form_type 주입을 빠뜨려도 최소단위 원칙은 깨지지 않는다.**
3. DB 스키마: `foreign_drug_prices.form_type` 컬럼 (TEXT) — `save_foreign_price` 가 매 INSERT 마다 기록, 캐시 재계산(`get_cached_prices`) 에서도 읽힌다.

## 국가별 소스 매핑 (2026-04 기준)

| 국가 | 소스 | 로그인 | 제형 커버리지 |
|------|------|--------|---------------|
| JP | MHLW 薬価基準 Excel | 불필요 | 内用薬/注射薬/外用薬 별 워크북 분리 → oral/injection 각각 |
| IT | AIFA Class A (retail) + Class H (hospital) CSV | 불필요 | Class A=prezzo al pubblico(oral), Class H=ex-factory(injection) |
| FR | base-donnees-publique.medicaments.gouv.fr (BDPM) | 불필요 | CIS_bdpm.txt + CIS_CIP_bdpm.txt. 주사제는 CIP 공시가 자주 없음 (None 허용) |
| CH | Swissmedic Compendium | 불필요 | Filmtabl/Gran(oral) + Inf Konz(injection) 혼재 |
| UK | MIMS | 필요 | 경구제 중심, injection 없으면 결과 1건 |
| DE | Rote Liste | 필요 | Pharmazie 리스트, 제형 기본 injection |
| CA | Ontario EAP HTML 테이블 | 불필요 | Tab(oral) + Inj Sol(injection) 혼재. 단일 연방 DB 없음 — 온타리오주 대표 |
| US | Micromedex (Red Book) | 필요 | **WAC package price 우선** (ex-manufacturer). AWP 는 유통 마크업 포함이라 factory_ratio 0.74 와 중복 → fallback 만. `price_basis` 컬럼으로 식별 |

## 새 국가 추가 시 필수 작업
1. `agents/scrapers/{cc}_{source}.py` 생성 (BaseScraper 상속)
2. `foreign_price_agent.py` 에 import + `_build_scraper()` 케이스 추가
3. `AVAILABLE_COUNTRIES` 리스트에 추가
4. `base.py` `ENV_KEY_MAP` 에 자격증명 키 추가 (로그인 필요 시)
5. `config/.env` 템플릿에 키 추가
6. 제형 약어 확인 → `formulation.py` 업데이트
7. Prevymis + Keytruda 로 validation 테스트

## A8 조정가 공식 (2025.3 기준, Korean 재정영향분석 표준)

**adjusted_price_krw 의미: per-UNIT (tablet/vial) KRW.** pack 단위 아님. 회귀 방지 필수.

```
A8_adj_per_unit_KRW =
    per_unit_local                          ← listed_price / pack_count
    × exchange_rate                         ← KEB 36mo 평균 (JPY per-1 정규화)
    × factory_ratio(country, source_type)
    × (1 + KR_VAT = 0.10)                   ← 한국 부가가치세 (공통)
    × (1 + KR_DIST_MARGIN = 0.0869)         ← 한국 유통거래폭 (공통)
```

**KR_VAT / KR_DIST_MARGIN 은 Korean A8 기준 상수.** foreign ex-factory → 한국 retail 등가 환산용.
국가별 VAT(DE 19%, JP 10% 등) 와 혼동 금지 — 국가별 VAT 는 factory_ratio 에 이미 녹아있음.

### 국가별 factory_ratio (local retail → 해당국 ex-factory)
| 국가 | ratio | 출처 |
|------|-------|------|
| US | 0.74 | 재정영향분석 표준 (WAC 기준 — AWP 사용 시 double-count) |
| UK | 0.73 | 재정영향분석 표준 |
| DE | 0.6955 | 재정영향분석 표준 (Germany AMNOG rebate 반영) |
| FR | 0.77 | 재정영향분석 표준 |
| IT | 0.93 | 재정영향분석 표준 (Class A retail) |
| CH | 0.73 | 재정영향분석 표준 |
| JP | 0.79 | 재정영향분석 표준 |
| CA | 0.81 | 재정영향분석 표준 |

### source-specific 오버라이드 (PriceCalculator.SOURCE_OVERRIDES)
- `aifa_exfactory` (IT Class H) → 1.0 (이미 ex-factory)
- `ch_compendium` → 0.65
- `fr_vidal` → 0.65 (현재 BDPM 사용 시 SOURCE_TYPE=None → 기본 0.77)

## source_type 처리
```python
source_type = getattr(scraper, "SOURCE_TYPE", None)
calc = self.calculator.calculate_adjusted_price(
    country=country, listed_price=item["local_price"],
    exchange_rate=rate, pack_count=pack_count, source_type=source_type,
)
```

## pack_count 결정 (반드시 필요) — **form_type 우선**

scraper 가 pack 가격을 반환하면 pack_count 를 명시해야 per-unit 환산이 맞는다.

**우선순위** (ForeignPriceAgent._resolve_pack_count):
1. scraper 가 item 에 직접 `pack_count` 필드 제공 — 가장 신뢰
2. **oral 전용**: `_extract_total_pkg_mg / _extract_mg` ratio (예: 3600/40=90)
   - injection 에서는 이 ratio 가 농도×volume 의 volume 을 반환 → **금지**
3. pack-pricing 국가({UK,US,DE,FR,IT,CH})에서 `foreign_drug_dosing.default_pack_count` fallback
4. 기본 1 (per-unit / 단일 vial)

**국가별 과금방식:**
- Per-unit (pack_count=1): JP MHLW, CA Ontario
- Pack (pack_count ≥ 1): UK MIMS, US Redbook, DE Rote Liste, FR BDPM, IT AIFA, CH Compendium

## 환율 적용 원칙
- KEB하나은행 36개월 평균 환율
- 환율 조회 실패 시 → adjusted_price_krw = None (DB 저장은 진행)
- EUR 은 FR/IT/DE 공용
- **JPY: KEB 가 "100엔당 KRW" 로 고시.** parse 시점에 /100 정규화 + calculator safeguard 이중 방어.

## DB 저장 시 필수 필드
`searched_at`, `query_name`, `country`, `product_name`, `ingredient`,
`dosage_strength`, `dosage_form`, `package_unit`,
`local_price`, `currency`,
`exchange_rate`, `exchange_rate_from`, `exchange_rate_to`,
`factory_price_krw`, `vat_rate` (=0.10), `distribution_margin` (=0.0869),
`adjusted_price_krw` (**per-unit KRW**), `pack_count`, `per_unit_local`,
`total_pkg_mg`, `daily_dose_mg`, `daily_cost_krw`, `daily_cost_note`,
`source_url`, `source_label`, `raw_data`, `form_type`

## 일일 투약비용(daily_cost_krw) 계산

**공식 — adj 와 unit_mg 모두 per-unit(최소단위) 기준:**
```
daily_cost_krw = daily_dose_mg × (adjusted_price_krw / unit_mg)
```

- `unit_mg` = `_extract_per_unit_mg(form_type, dosage_strength, package_unit)` — **form-aware**
  - oral: per-tablet mg (`_extract_mg(dosage_strength)` 와 동일, 예: 40)
  - injection: per-vial **총** mg (예: "100 mg/4 mL" → 100, "25 mg/mL" + "4 mL" → 100)
  - 농도만 있고 volume 없으면 per-vial 복원 불가 → `unit_mg=None` → daily_cost skip
- `adjusted_price_krw` 는 **per-unit KRW** (정 1개 또는 바이알 1개 기준)
- 분모가 per-mL 농도로 들어가면 daily_cost 가 4×~100× 왜곡됨 — **절대 금지**
- **회귀 방지**: 과거에는 adj 가 per-pack 이어서 분모도 total_pkg_mg 였음. 이제 둘 다 per-unit 로
  통일됨 → 일관성 유지됨.

### 파싱 우선순위 (_extract_total_pkg_mg — pack 총 mg 표시용)
1. `"X mg/Y ml"` (Y>1) → 그 바이알 총량 X mg
2. `"X mg/1 ml"` 또는 `"X mg/ml"` (농도) + volume → conc × vol
3. `"X mg"` 단독 → per-unit, count multiplier 별도 탐색
4. Count multiplier: `(N x M)`, `de N`, `N tablet/comprim/caps/vial/flacon/瓶/錠/정`, `vial, N`, US `"ml Ns"`, `"Ns ea"`, trailing `, N`

### Sanity cap
- daily_cost > ₩10M/day → 이상치로 `daily_cost_krw=None`, `daily_cost_note="suspicious_outlier"` + WARN log
- 상수: `foreign_price_agent.DAILY_COST_SANITY_CAP_KRW`

### 신규 스크레이퍼 검증 필수 2-케이스
1. **Welireg (경구·pack)**: dosing.default_pack_count=90, per-tablet A8 ≈ ₩200K 수준
2. **Keytruda (주사·농도×volume)**: 100mg/4ml, per-vial A8 ≈ ₩1-2M (국가별)

## Excel Baseline Regression (Welireg per-tablet A8, KEB 2023-02 ~ 2025-02 FX 기준)
재정영향분석 엑셀 값과 ±1% 이내 수렴해야 함. FX 기간이 다르면 FX 차이만 허용.

| country | local_pack | pack_count | factory_ratio | FX(Excel) | Excel per-tablet KRW |
|---------|-----------|------------|---------------|-----------|---------------------|
| UK | 11,936.70 GBP | 90 | 0.73 | 1707.75 | 197,684 |
| US | 31,162.50 USD | 90 | 0.74 | 1351.85 | 414,126 |
| CA | 213.33 CAD | 1 (per-tablet) | 0.81 | 988.63 | 204,246 |
| JP | 21,916.80 JPY | 1 (per-tablet) | 0.79 | 9.1559 | 189,534 |
| DE | 17,830.31 EUR | 90 | 0.6955 | 1457.84 | 240,163 |

→ `tests/test_welireg_excel_baseline.py` 에서 자동 검증. PriceCalculator 변경 시 반드시 통과 확인.
- QualityGuard 는 이 공식으로 국가별 편차를 검사 (1개국만 10× 차이 나면 플래그).
