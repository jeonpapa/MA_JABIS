"""심평원 급여기준 DB 스키마 (추가 전용 — 기존 테이블은 건드리지 않는다).

'최신만' 원칙: 파생 테이블(onco_criteria_*, drug_criteria_item, onco_offlabel_*)은 실행마다 통째로
다시 만든다(트랜잭션). 단, criteria_disease_kcd 의 method='manual' 행(사람 검토)은 보존한다.
"""
from __future__ import annotations

import sqlite3

DDL = (
    # 원문 단위 (어떤 파일로부터 만들었는지 — 최신 근거 추적)
    """CREATE TABLE IF NOT EXISTS hira_criteria_source (
        source_id      INTEGER PRIMARY KEY AUTOINCREMENT,
        kind           TEXT NOT NULL,   -- onco_consolidated|onco_notice|onco_offlabel|drug_booklet|drug_gosi_item|kcd_master
        title          TEXT,
        notice_no      TEXT,            -- '공고 제2026-232호' / '고시 제2026-192호'
        effective_date TEXT,            -- 시행일(원문 기준) YYYY-MM-DD
        posted_date    TEXT,            -- 게시일
        url            TEXT,
        file_path      TEXT,            -- data/hira_criteria/raw/...
        md_path        TEXT,            -- kordoc 변환본
        sha256         TEXT,
        fetched_at     TEXT DEFAULT (datetime('now')),
        UNIQUE(kind, sha256)
    )""",
    # KCD (심평원 상병마스터 기준 — 현재 KCD-9)
    """CREATE TABLE IF NOT EXISTS kcd_code (
        code           TEXT PRIMARY KEY,   -- 점 없는 상병기호 (예: C349, C3490)
        name_kr        TEXT,
        name_en        TEXT,
        is_complete    INTEGER,            -- 1=완전코드(청구 가능 최하위), 0=불완전
        main_dx_ok     INTEGER,            -- 1=주상병 사용 가능
        version        TEXT                -- '상병마스터_260329 (KCD-9)'
    )""",
    "CREATE INDEX IF NOT EXISTS idx_kcd_name ON kcd_code(name_kr)",
    # 같은 상병기호의 동의어 행(상병마스터는 표제어 다음에 동의어를 같은 코드로 반복 수록)
    """CREATE TABLE IF NOT EXISTS kcd_alias (
        code     TEXT NOT NULL,
        name_kr  TEXT NOT NULL,
        name_en  TEXT,
        PRIMARY KEY (code, name_kr)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_kcd_alias_name ON kcd_alias(name_kr)",
    # 항암: 요법 × 투여대상 변형 1행
    """CREATE TABLE IF NOT EXISTS onco_criteria_regimen (
        regimen_key     TEXT PRIMARY KEY,  -- '{cancer_code}|{setting_code}|{line_code}|t{table}|{no}|v{variant}'
        source_sha      TEXT,              -- hira_criteria_source.sha256 (원문 식별)
        cancer_code     TEXT,              -- '2', '7-1', '40'
        cancer_name_kr  TEXT,
        cancer_name_en  TEXT,
        setting_code    TEXT,              -- neoadjuvant|adjuvant|perioperative|ccrt|primary|palliative|maintenance|consolidation|induction|salvage|intravesical|conditioning|other
        setting_raw     TEXT,
        subgroup_raw    TEXT,              -- 소제목(예: 'HER2 양성 유방암의 선행화학요법', '1군/2군')
        line_code       TEXT,              -- 1L|1L+|2L|2L+|3L|3L+|1L_or_1L+|2L_or_3L|NA
        line_raw        TEXT,
        line_source     TEXT,              -- heading|column|target_text|none
        regimen_no      TEXT,              -- 표의 연번
        regimen_text    TEXT,              -- 'pembrolizumab + pemetrexed + platinum'
        drugs_json      TEXT,              -- ["pembrolizumab","pemetrexed","platinum"]
        target_text     TEXT,              -- 투여대상 원문
        prior_therapy   TEXT,              -- 이전 치료 조건 문장(원문 발췌)
        biomarker_text  TEXT,              -- PD-L1/EGFR/ALK/HER2/BRCA 등 발췌
        stage_text      TEXT,              -- stage/전이성/국소진행 등 발췌
        self_pay        TEXT,              -- '100/100'(약값 전액 본인부담) 등
        footnotes       TEXT,              -- 요법에 붙은 주N 표식
        notice_history  TEXT,              -- '(제2022-38호: 2022.3.1., 개정 제2026-88호: 2026.4.2.)'
        last_notice_no  TEXT,              -- 마지막 공고번호 (제2026-88호)
        last_effective  TEXT,              -- 마지막 시행일
        extra_json      TEXT,              -- 표 형식별 추가 칼럼(허가사항·급여요법 등)
        md_path         TEXT,
        parse_flags     TEXT               -- 파싱 경고(쉼표 구분)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_onco_reg_cancer ON onco_criteria_regimen(cancer_code, setting_code, line_code)",
    """CREATE TABLE IF NOT EXISTS onco_criteria_regimen_drug (
        regimen_key  TEXT NOT NULL,
        seq          INTEGER NOT NULL,
        ingredient   TEXT NOT NULL,        -- 소문자 INN (platinum 등 계열명 포함)
        PRIMARY KEY (regimen_key, seq)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_onco_reg_drug_ing ON onco_criteria_regimen_drug(ingredient)",
    # 허가초과 항암요법(심평원 xlsx — 신청기관 한정 인정 요법 / 불승인 요법)
    """CREATE TABLE IF NOT EXISTS onco_offlabel_regimen (
        regimen_code  TEXT PRIMARY KEY,
        source_sha    TEXT,
        cancer        TEXT, subcancer TEXT, regimen_text TEXT, target_text TEXT,
        line_raw      TEXT, line_code TEXT, setting_raw TEXT, cost_note TEXT, dosing_note TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS onco_offlabel_rejected (
        seq INTEGER PRIMARY KEY, source_sha TEXT, cancer TEXT, regimen_text TEXT, reason TEXT, note TEXT
    )""",
    # 일반약제: 세부사항(약제) 1항목 = 1행 (최신본)
    """CREATE TABLE IF NOT EXISTS drug_criteria_item (
        item_key        TEXT PRIMARY KEY,  -- '{class_no}|{정규화 제목}'
        source_sha      TEXT,              -- hira_criteria_source.sha256 (원문 식별)
        class_no        TEXT,              -- 분류번호 '421' / '일반원칙'
        title           TEXT,              -- 'Ruxolitinib phosphate 경구제(품명: 자카비정5밀리그램 등)'
        ingredient      TEXT,
        dosage_form     TEXT,
        brand_text      TEXT,
        criteria_text   TEXT,              -- 세부인정기준 원문(최신)
        gosi_no         TEXT,              -- 최신 고시번호
        effective_date  TEXT,              -- 최신 시행일
        latest_basis    TEXT,              -- booklet|gosi_newer (책자 이후 개정 고시로 대체)
        latest_url      TEXT,
        md_path         TEXT,
        parse_flags     TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_drug_item_ing ON drug_criteria_item(ingredient)",
    # 질환(KCD) ↔ 항암요법/일반약제 항목
    """CREATE TABLE IF NOT EXISTS criteria_disease_kcd (
        subject_type   TEXT NOT NULL,      -- onco_regimen|drug_item|onco_offlabel
        subject_key    TEXT NOT NULL,
        kcd_code       TEXT NOT NULL,      -- 범위의 대표 3단 코드(예: C34) 또는 세부 코드
        disease_label  TEXT,               -- 원문 질환 표현
        method         TEXT NOT NULL,      -- curated|name_match|llm|manual
        confidence     REAL,
        review_status  TEXT DEFAULT 'auto',-- auto|needs_review|reviewed
        note           TEXT,
        PRIMARY KEY (subject_type, subject_key, kcd_code)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_cdk_kcd ON criteria_disease_kcd(kcd_code)",
    # 일반약제 문단(1. / 가.) ↔ 질환 — 검색 발췌·비교에 사용 (drug_sections.refine 이 생성)
    """CREATE TABLE IF NOT EXISTS criteria_section_kcd (
        subject_key  TEXT NOT NULL,        -- drug_criteria_item.item_key
        heading      TEXT NOT NULL,        -- 문단 제목 줄(원문)
        level        INTEGER,              -- 1='1.' 2='가.'
        kcd_code     TEXT NOT NULL,
        PRIMARY KEY (subject_key, heading, kcd_code)
    )""",
)

DERIVED_TABLES = ("onco_criteria_regimen", "onco_criteria_regimen_drug",
                  "onco_offlabel_regimen", "onco_offlabel_rejected", "drug_criteria_item")


def ensure_schema(conn: sqlite3.Connection) -> None:
    for stmt in DDL:
        conn.execute(stmt)
