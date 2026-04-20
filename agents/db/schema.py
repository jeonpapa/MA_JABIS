"""DB 스키마 정의 + HIRA 엑셀 컬럼 매핑 후보."""
from __future__ import annotations


DB_SCHEMA = """
-- 핵심 테이블: 적용일별 전체 약가 스냅샷
CREATE TABLE IF NOT EXISTS drug_prices (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    apply_date      TEXT    NOT NULL,   -- 적용 기준일 (예: 2026.04.01)
    insurance_code  TEXT    NOT NULL,   -- 보험코드
    product_name_kr TEXT,               -- 한글제품명
    product_name_en TEXT,               -- 영문제품명
    company         TEXT,               -- 업체명
    ingredient      TEXT,               -- 성분명(일반명)
    dosage_strength TEXT,               -- 함량
    dosage_form     TEXT,               -- 제형
    package_unit    TEXT,               -- 포장단위
    max_price       INTEGER,            -- 상한금액 (원)
    coverage_start  TEXT,               -- 급여개시일
    remark          TEXT                -- 비고
);

-- 적용일 + 보험코드 조합 중복 방지 인덱스
CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_date_code
    ON drug_prices(apply_date, insurance_code);

-- 검색용 인덱스
CREATE INDEX IF NOT EXISTS idx_code
    ON drug_prices(insurance_code);
CREATE INDEX IF NOT EXISTS idx_name
    ON drug_prices(product_name_kr);
CREATE INDEX IF NOT EXISTS idx_ingredient
    ON drug_prices(ingredient);
CREATE INDEX IF NOT EXISTS idx_date
    ON drug_prices(apply_date);

-- 검색 최적화: 보험코드별 최신 약가 레코드만 유지 (~22K 행)
-- drug_prices(3.78M 행) 풀스캔 대신 이 테이블에서 검색
CREATE TABLE IF NOT EXISTS drug_latest (
    insurance_code  TEXT PRIMARY KEY,
    apply_date      TEXT,
    product_name_kr TEXT,
    product_name_en TEXT,
    company         TEXT,
    ingredient      TEXT,
    dosage_strength TEXT,
    dosage_form     TEXT,
    package_unit    TEXT,
    max_price       INTEGER,
    coverage_start  TEXT,
    remark          TEXT
);

-- 다운로드 / 처리 이력 로그
CREATE TABLE IF NOT EXISTS download_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    post_number      INTEGER,            -- 게시물 번호 (226, 225, ...)
    brd_blt_no       INTEGER,            -- HIRA 내부 brdBltNo 파라미터
    apply_date       TEXT,               -- Excel 파일 기준일
    filename         TEXT,               -- 저장된 파일명
    file_path        TEXT,               -- 로컬 파일 경로
    download_status  TEXT DEFAULT 'pending',   -- pending / success / failed / skipped
    process_status   TEXT DEFAULT 'pending',   -- pending / success / failed
    record_count     INTEGER DEFAULT 0,
    downloaded_at    TEXT,
    processed_at     TEXT,
    error_msg        TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_log_brd
    ON download_log(brd_blt_no);

-- 해외 약가 검색 결과 저장 테이블
CREATE TABLE IF NOT EXISTS foreign_drug_prices (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    searched_at         TEXT    NOT NULL,   -- 검색 실행 일시
    query_name          TEXT    NOT NULL,   -- 검색어 (영문 제품명 또는 성분명)
    country             TEXT    NOT NULL,   -- 국가코드 (US/UK/DE/FR/IT/CH/JP/CA)
    product_name        TEXT,               -- 해당국 제품명
    ingredient          TEXT,               -- 성분명
    dosage_strength     TEXT,               -- 함량
    dosage_form         TEXT,               -- 제형
    package_unit        TEXT,               -- 포장단위
    local_price         REAL,               -- 현지 약가 (해당국 화폐)
    currency            TEXT,               -- 통화 (USD/GBP/EUR/CHF/JPY/CAD)
    exchange_rate       REAL,               -- 적용 환율 (36개월 평균)
    exchange_rate_from  TEXT,               -- 환율 적용 시작월 (YYYY-MM)
    exchange_rate_to    TEXT,               -- 환율 적용 종료월 (YYYY-MM)
    factory_price_krw   INTEGER,            -- 공장도출하가격 (원)
    vat_rate            REAL,               -- 부가가치세율 (소수점)
    distribution_margin REAL,               -- 유통거래폭 (소수점)
    adjusted_price_krw  INTEGER,            -- 조정가 (원)
    source_url          TEXT,               -- 자료 출처 URL
    source_label        TEXT,               -- 자료원 명칭 (예: Redbook, MIMS)
    raw_data            TEXT                -- 원본 데이터 JSON
);

CREATE INDEX IF NOT EXISTS idx_foreign_query
    ON foreign_drug_prices(query_name, country);
CREATE INDEX IF NOT EXISTS idx_foreign_date
    ON foreign_drug_prices(searched_at);

-- 검색 이력 로그 — 모든 검색을 기록하여 캐시 관리 + 사용자 히스토리
CREATE TABLE IF NOT EXISTS search_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    query        TEXT NOT NULL,
    resolved_to  TEXT,
    search_type  TEXT NOT NULL,   -- 'foreign_price' | 'hta' | 'domestic'
    searched_at  TEXT NOT NULL,
    result_count INTEGER DEFAULT 0,
    status       TEXT DEFAULT 'complete'
);
CREATE INDEX IF NOT EXISTS idx_search_query ON search_log(query, search_type);

-- 데이터 신선도 추적 — 각 data_type+scope별 마지막 수집 시점
CREATE TABLE IF NOT EXISTS data_freshness (
    data_type    TEXT NOT NULL,
    scope_key    TEXT NOT NULL,
    last_fetched TEXT NOT NULL,
    next_check   TEXT,
    etag         TEXT,
    PRIMARY KEY (data_type, scope_key)
);

-- 약제 부가정보 캐시 (RSA / 용법용량 / 허가일)
CREATE TABLE IF NOT EXISTS drug_enrichment (
    normalized_name         TEXT PRIMARY KEY,
    representative_code     TEXT,                -- 대표 보험코드
    insurance_codes_json    TEXT,                -- 병합된 모든 보험코드 JSON array
    is_rsa                  INTEGER,             -- 1=RSA 대상, 0=아님, NULL=미확인
    rsa_type                TEXT,                -- 총액제한 / 환급 / 사용량연동 / 조건부 등
    rsa_note                TEXT,                -- RSA 관련 특이사항
    approval_date           TEXT,                -- YYYY.MM.DD 최초 품목허가일
    usage_text              TEXT,                -- 용법용량 자연어 원문
    daily_dose_units        REAL,                -- 1일 투여 단위 수 (정/바이알/mL)
    dose_schedule           TEXT,                -- 'continuous' / 'cycle' / 'as_needed'
    cycle_days              INTEGER,             -- 항암제 등 주기 (일)
    doses_per_cycle         REAL,                -- 1 주기당 투여 단위 수
    sources_json            TEXT,                -- [{url,title,media}]
    confidence              TEXT,                -- high|medium|low
    notes                   TEXT,
    fetched_at              TEXT,                -- ISO8601
    ttl_days                INTEGER DEFAULT 30
);
CREATE INDEX IF NOT EXISTS idx_enrichment_code
    ON drug_enrichment(representative_code);

-- ─────────────────────────────────────────────────────────────────────────
-- 적응증(Indication) 마스터 + 국가별 variant
-- discussion.md (2026-04-16) 결정 기반: 5-anchor 매칭 / FDA·EMA·NICE·... variant
-- ─────────────────────────────────────────────────────────────────────────

-- Anchor 테이블: 적응증의 본질 (국가 무관)
CREATE TABLE IF NOT EXISTS indications_master (
    indication_id    TEXT PRIMARY KEY,    -- slug 예: keytruda_nsclc_1l_mono_pdl1_50
    product          TEXT NOT NULL,       -- brand slug 예: keytruda
    pivotal_trial    TEXT,                -- 예: KEYNOTE-024
    disease          TEXT,                -- 예: NSCLC
    stage            TEXT,                -- 예: metastatic / advanced / adjuvant
    line_of_therapy  TEXT,                -- 예: 1L / 2L / 3L+ / neoadjuvant
    population       TEXT,                -- 예: adult / pediatric >=6mo
    biomarker_class  TEXT,                -- 6번째 anchor (msi_h / tmb_h / pdl1_50 / all_comers / ...)
    title            TEXT,                -- 사람이 읽을 1-line 요약
    fda_indication_code TEXT,             -- 라벨 1.x 코드 (FDA에서 분리할 때 보존)
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ind_master_product
    ON indications_master(product);
CREATE INDEX IF NOT EXISTS idx_ind_master_trial
    ON indications_master(pivotal_trial);

-- Variant 테이블: 국가/기관별 좁힘·넓힘 기록
CREATE TABLE IF NOT EXISTS indications_by_agency (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    indication_id       TEXT NOT NULL,    -- FK -> indications_master.indication_id
    agency              TEXT NOT NULL,    -- FDA / EMA / NICE / HAS / G-BA / PMDA / TGA / Swissmedic / MHRA
    biomarker_label     TEXT,             -- 라벨 원문 그대로 (예: "PD-L1 TPS >=1%")
    combination_label   TEXT,             -- 라벨 원문 그대로
    approval_date       TEXT,             -- YYYY-MM-DD
    label_excerpt       TEXT,             -- 해당 적응증 본문 발췌
    label_url           TEXT,
    restriction_note    TEXT,             -- 좁혀진 사유 (CHMP opinion 등)
    raw_source          TEXT,             -- 원본 payload JSON
    fetched_at          TEXT NOT NULL,
    FOREIGN KEY (indication_id) REFERENCES indications_master(indication_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_ind_agency_unique
    ON indications_by_agency(indication_id, agency);
CREATE INDEX IF NOT EXISTS idx_ind_agency_indid
    ON indications_by_agency(indication_id);

-- ──────────────────────────────────────────────────────────────
-- 적응증별 한국 급여 상태 (HIRA 항암화학요법 공고 / 고시 기반 수동 편집)
-- LLM 파싱 대신 admin UI 에서 체크리스트 편집
-- ──────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS indication_reimbursement (
    indication_id    TEXT PRIMARY KEY,         -- FK -> indications_master.indication_id
    is_reimbursed    INTEGER NOT NULL DEFAULT 0, -- 0/1 boolean
    effective_date   TEXT,                     -- 급여 개시일 YYYY-MM-DD
    criteria_text    TEXT,                     -- 급여 조건/제한 (예: "본인부담 30%, MSI-H 확인")
    notice_date      TEXT,                     -- 근거 공고일 YYYY-MM-DD
    notice_url       TEXT,                     -- HIRA 공고 원문 링크
    updated_by       TEXT,                     -- 편집자 email
    updated_at       TEXT NOT NULL,
    FOREIGN KEY (indication_id) REFERENCES indications_master(indication_id)
);

-- ──────────────────────────────────────────────────────────────
-- Korean Market Share (IQVIA NSA-E Master quarterly Excel drop)
-- product_id = hash(PRODUCT NAME + MOLECULE DESC + PACK)
-- ──────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS market_share_products (
    product_id       TEXT PRIMARY KEY,
    otc_ethical      TEXT,
    atc2_code        TEXT,
    atc3_code        TEXT,
    atc4_code        TEXT,
    atc4_desc        TEXT,
    mfr_name         TEXT,
    corp             TEXT,
    mnc13            TEXT,
    product_name     TEXT NOT NULL,
    product_group    TEXT,
    molecule_desc    TEXT NOT NULL,
    em_ethical       TEXT,
    kr_market        TEXT,
    nfc3             TEXT,
    strength         TEXT,
    pack_desc        TEXT,
    pack             TEXT,
    pack_launch_date TEXT,
    updated_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_msp_atc4         ON market_share_products(atc4_code);
CREATE INDEX IF NOT EXISTS idx_msp_product_name ON market_share_products(product_name);
CREATE INDEX IF NOT EXISTS idx_msp_molecule     ON market_share_products(molecule_desc);
CREATE INDEX IF NOT EXISTS idx_msp_mnc13        ON market_share_products(mnc13);

CREATE TABLE IF NOT EXISTS market_share_quarterly (
    product_id   TEXT NOT NULL,
    quarter      TEXT NOT NULL,    -- '2021Q1', '2025Q4'
    values_lc    REAL,             -- 매출 (원)
    dosage_units REAL,             -- 처방 단위
    PRIMARY KEY (product_id, quarter),
    FOREIGN KEY (product_id) REFERENCES market_share_products(product_id)
);

CREATE INDEX IF NOT EXISTS idx_msq_quarter ON market_share_quarterly(quarter);

CREATE TABLE IF NOT EXISTS market_share_upload_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    uploaded_at    TEXT NOT NULL,
    uploaded_by    TEXT,
    filename       TEXT,
    rows_ingested  INTEGER,
    quarters_json  TEXT
);

-- MSD 한국 파이프라인 (관리자 직접 관리)
CREATE TABLE IF NOT EXISTS msd_pipeline (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    phase         TEXT,
    indication    TEXT,
    expected_year INTEGER,
    status        TEXT NOT NULL DEFAULT 'upcoming',  -- 'current' | 'upcoming'
    note          TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msd_pipeline_year   ON msd_pipeline(expected_year);
CREATE INDEX IF NOT EXISTS idx_msd_pipeline_status ON msd_pipeline(status);

-- 브랜드 미디어 트래픽 (관리자 직접 관리)
CREATE TABLE IF NOT EXISTS brand_traffic (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    rank           INTEGER NOT NULL,
    brand          TEXT NOT NULL,
    company        TEXT,
    category       TEXT,
    color          TEXT,
    traffic_index  INTEGER NOT NULL DEFAULT 0,
    change_pct     REAL DEFAULT 0,
    sparkline_json TEXT,            -- JSON array of 7 numbers
    news_json      TEXT,            -- JSON array of {title,source,date,tag,url}
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_brand_traffic_rank ON brand_traffic(rank);

-- Daily Mailing 구독 설정 (사용자별)
CREATE TABLE IF NOT EXISTS mail_subscription (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_email    TEXT NOT NULL,           -- users.email
    name           TEXT NOT NULL,
    keywords_json  TEXT NOT NULL,           -- JSON string[]
    media_json     TEXT NOT NULL,           -- JSON string[]  (media id)
    schedule       TEXT NOT NULL,           -- 'Daily' | 'Weekly'
    time           TEXT NOT NULL,           -- 'HH:MM'
    week_day       TEXT,                    -- 'Monday'..'Friday' when Weekly
    emails_json    TEXT NOT NULL,           -- JSON string[]  (수신 이메일)
    active         INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    last_sent_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_mail_sub_owner  ON mail_subscription(owner_email);
CREATE INDEX IF NOT EXISTS idx_mail_sub_active ON mail_subscription(active);

-- Competitor Trends — 경쟁사 동향 카드 (Competitor Trends 페이지 & 대시보드)
CREATE TABLE IF NOT EXISTS competitor_trend (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    company      TEXT NOT NULL,
    logo         TEXT,                    -- 2~3자 약어
    color        TEXT,                    -- '#RRGGBB'
    badge        TEXT NOT NULL,           -- '신규 출시'|'가격 변동'|'임상 진행'|'급여 등재'|'파이프라인'|'전략 변화'
    badge_color  TEXT,                    -- Tailwind class string
    headline     TEXT NOT NULL,
    detail       TEXT NOT NULL,
    date         TEXT NOT NULL,           -- YYYY-MM-DD
    source       TEXT,                    -- '보건복지부 고시' / 매체명
    url          TEXT,
    source_type  TEXT NOT NULL DEFAULT 'manual', -- 'manual' | 'auto_naver'
    importance   TEXT,                    -- 'critical'|'moderate'|'low' (auto 만 사용)
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_competitor_trend_date    ON competitor_trend(date DESC);
CREATE INDEX IF NOT EXISTS idx_competitor_trend_badge   ON competitor_trend(badge);
CREATE INDEX IF NOT EXISTS idx_competitor_trend_company ON competitor_trend(company);
-- URL 기반 lookup 용 (dedup 은 app-level SELECT-then-upsert)
CREATE INDEX IF NOT EXISTS idx_competitor_trend_url ON competitor_trend(url);

-- Keyword Cloud — Home 워드클라우드 (Phase 3e-2)
CREATE TABLE IF NOT EXISTS keyword_cloud (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    text       TEXT NOT NULL UNIQUE,
    weight     INTEGER NOT NULL DEFAULT 50,
    color      TEXT,                      -- '#RRGGBB'
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_keyword_cloud_weight ON keyword_cloud(weight DESC);
"""


# 엑셀 컬럼명과 DB 컬럼명 매핑 후보 (다양한 HIRA 파일 포맷 대응)
COL_CANDIDATES = {
    # 현행(2010년대~) 포맷
    "insurance_code":  ["보험코드", "보험\n코드", "급여코드", "코드",
                        # 구형(2008~2009) 포맷
                        "제품코드"],
    "product_name_kr": ["한글제품명", "제품명", "한글\n제품명", "품목명(한글)", "한글 제품명"],
    "product_name_en": ["영문제품명", "영문\n제품명", "품목명(영문)", "영문 제품명"],
    "company":         ["업체명", "제조(수입)업체", "회사명", "제약사",
                        "업소명"],           # 구형 포맷
    "ingredient":      ["성분명(일반명)", "성분명", "일반명", "주성분"],
    "dosage_strength": ["함량", "규격", "함량/규격"],
    "dosage_form":     ["제형", "剂型"],
    "package_unit":    ["포장단위", "포장", "단위"],
    "max_price":       ["상한금액", "상한\n금액", "최고가격", "보험약가", "약가(원)"],
    "coverage_start":  ["급여개시일", "급여\n개시일", "급여적용일"],
    "remark":          ["비고"],
}
