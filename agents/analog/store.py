"""약제 등재 아날로그 검색 — DB store (스키마·ingest·임베딩·검색).

analog_reports 테이블 + FTS5 + 임베딩 BLOB.
v2: DREC Raw PDF 소스 기반 — 질환분류·효과지표·정책의도·타임라인 컬럼 대폭 확장.

함수:
  ensure_schema()        — 테이블 생성 + 마이그레이션(신규 컬럼 ALTER TABLE)
  ingest_corpus(payload) — file_hash dedup UPSERT + 신규/변경만 임베딩
  search(...)            — 패싯/FTS/시맨틱 하이브리드
  facet_values()         — 드롭다운 옵션
  get_detail(id)         — 상세 조회
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import struct
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BASE_DIR / "data" / "db" / "drug_prices.db"

EMBED_MODEL = "text-embedding-3-small"
EMBED_DIM = 1536

_FACET_COLS = [
    "disease_category", "disease_category_detail", "cancer_type", "line_of_therapy",
    "committee", "review_result", "reimbursement_track_ko", "coverage_gap_type",
    "medical_necessity", "approval_driver",
]

# 검색 필터에서 허용하는 컬럼 (패싯 + 일부 식별자)
_FILTER_COLS = _FACET_COLS + [
    "generic_name", "brand_name", "generic_name_en",
    "has_rsa", "pe_waiver", "has_postmarket_condition",
]

# ── 기본 스키마 (신규 설치) ────────────────────────────────────────────────────

# 테이블 + 인덱스만 (FTS 제외 — ensure_schema 에서 마이그레이션 후 FTS 생성)
_SCHEMA_TABLE = """
CREATE TABLE IF NOT EXISTS analog_reports (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    -- 파일 식별
    file_name                   TEXT UNIQUE NOT NULL,
    file_hash                   TEXT NOT NULL,
    brand_name_raw              TEXT,
    brand_name                  TEXT,
    dosage                      TEXT,    -- 용량/강도 (예: 100밀리그램, 0.4%) — brand_name 에서 분리
    generic_name                TEXT,    -- 기존 호환용 (INN 한글 또는 영문)
    generic_name_en             TEXT,    -- INN 영문
    manufacturer                TEXT,
    session_year                INTEGER,
    ordinal                     INTEGER,
    reimbursable_requested      INTEGER, -- 1=급여 신청, 0=비급여
    pdf_extractable             INTEGER DEFAULT 1,
    -- 질환 분류 (LLM enrich)
    disease_category            TEXT,    -- 항암/비항암/희귀
    disease_category_detail     TEXT,    -- 혈액종양/고형암/자가면역/대사질환/희귀/기타
    disease_name                TEXT,    -- 기존 호환
    disease_name_ko             TEXT,    -- 비소세포폐암, 악성 흑색종
    disease_name_en             TEXT,    -- NSCLC, Melanoma
    cancer_type                 TEXT,
    line_of_therapy             TEXT,
    biomarker                   TEXT,
    treatment_setting           TEXT,
    -- 결정
    committee                   TEXT,
    session_date                TEXT,
    review_result               TEXT,    -- APPROVED/REJECTED/CONDITIONAL_APPROVED/APPROVED_WITH_POSTMARKET/UNKNOWN
    review_result_ko            TEXT,    -- 한국어 레이블
    reimbursement_track         TEXT,    -- 기존 호환
    reimbursement_track_ko      TEXT,    -- 경제성평가 생략 (PE Waiver) + 위험분담제
    has_rsa                     INTEGER DEFAULT 0,
    pe_waiver                   INTEGER DEFAULT 0,
    has_postmarket_condition    INTEGER DEFAULT 0,
    postmarket_condition_detail TEXT,
    rsa_type_hint               TEXT,    -- 환급형/총액제한/기타
    rsa_types                   TEXT,    -- JSON (기존 호환)
    -- 날짜 히스토리 (JSON)
    committee_history           TEXT,    -- [{type,date,ordinal,result}]
    amjilsim_history            TEXT,    -- [{date}]
    days_mfds_to_first_committee INTEGER,
    days_amjilsim_to_committee  INTEGER,
    -- 임상 효과 지표 (LLM enrich)
    efficacy_data               TEXT,    -- JSON [{trial,endpoint,value,unit,hr,p,...}]
    primary_endpoint            TEXT,
    os_months                   REAL,
    pfs_months                  REAL,
    orr_pct                     REAL,
    key_hr                      REAL,
    comparator_drugs            TEXT,    -- JSON [약제명, ...]
    clinical_trials             TEXT,    -- JSON [임상시험명, ...]
    -- 해외 등재 현황
    foreign_listing_count       INTEGER,
    foreign_listing_basis       INTEGER, -- 7 or 8 (A7/A8)
    medical_necessity           TEXT,    -- 필수/불필수
    -- 정책 (LLM enrich)
    policy_signals              TEXT,    -- JSON [키워드, ...]
    policy_intent_summary       TEXT,
    policy_tags                 TEXT,    -- JSON [태그, ...]
    approval_driver             TEXT,    -- RSA/PE_WAIVER/COST_EFFECTIVE/POLICY_PRIORITY/REJECTED_COST
    future_conditions           TEXT,
    policy_drivers              TEXT,    -- JSON (기존 호환)
    -- 본문
    decision_reason             TEXT,    -- 가. 평가 결과 전문
    body_text                   TEXT,    -- 나. 평가 내용 전문
    mfds_effect_text            TEXT,    -- 식약처 허가 적응증 (PDF page1 또는 MFDS API)
    -- MFDS API
    mfds_permit_date            TEXT,
    mfds_approval_date          TEXT,    -- 기존 호환
    application_date            TEXT,
    amjilsim_date               TEXT,
    -- 허가↔급여 갭 (LLM enrich)
    coverage_gap_type           TEXT,    -- 축소/확대/구체화/동일/비교불가
    coverage_gap_evidence       TEXT,
    -- 재심의 trajectory
    requeue_count               INTEGER,
    first_session_date          TEXT,
    pass_session_date           TEXT,
    sessions_to_pass            INTEGER,
    lag_days_approval_to_reimb  INTEGER,
    -- 기타
    wikilinks                   TEXT,    -- JSON (기존 호환)
    title                       TEXT,    -- 기존 호환
    enriched_at                 TEXT,
    -- 임베딩
    embedding                   BLOB     -- float32 x1536
);
CREATE INDEX IF NOT EXISTS idx_analog_generic ON analog_reports(generic_name);
CREATE INDEX IF NOT EXISTS idx_analog_date ON analog_reports(session_date);
CREATE TABLE IF NOT EXISTS analog_brief_cache (
    cache_key  TEXT PRIMARY KEY,
    brief      TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS analog_gap_cache (
    cache_key  TEXT PRIMARY KEY,
    gap_type   TEXT,
    evidence   TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS analog_llm_cache (
    cache_key   TEXT PRIMARY KEY,
    enrich_type TEXT,  -- disease/efficacy/policy
    result_json TEXT,
    created_at  TEXT
);
-- ── 태그/동의어 concept 그래프 (tag_seeds.py 온톨로지 기반) ──────────────────
CREATE TABLE IF NOT EXISTS analog_concepts (
    concept_id   TEXT PRIMARY KEY,   -- 예: disease-nsclc, target-pcsk9, form-injection
    concept_type TEXT,               -- disease|drug_class|target|biomarker|form|setting|line_of_therapy
    canonical_ko TEXT,
    canonical_en TEXT
);
CREATE TABLE IF NOT EXISTS analog_concept_aliases (
    alias_norm TEXT NOT NULL,        -- 정규화 표기 (소문자+공백제거)
    concept_id TEXT NOT NULL,
    alias_raw  TEXT,
    PRIMARY KEY (alias_norm, concept_id)
);
CREATE INDEX IF NOT EXISTS idx_aca_alias ON analog_concept_aliases(alias_norm);
CREATE INDEX IF NOT EXISTS idx_aca_concept ON analog_concept_aliases(concept_id);
CREATE TABLE IF NOT EXISTS analog_report_tags (
    report_id  INTEGER NOT NULL,
    concept_id TEXT NOT NULL,
    tag_type   TEXT,
    weight     REAL DEFAULT 1.0,
    source     TEXT,                 -- disease_col|cancer_col|inn_col|comparator_col|effect_scan|form_suffix|biomarker_col
    PRIMARY KEY (report_id, concept_id)
);
CREATE INDEX IF NOT EXISTS idx_art_report ON analog_report_tags(report_id);
CREATE INDEX IF NOT EXISTS idx_art_concept ON analog_report_tags(concept_id);
-- ── 검색어 피드백 (사용자가 입력한 시멘틱 → 실제 의도 약제 매핑) ──────────────────
-- 검색 결과가 의도와 다를 때 사용자가 남긴 피드백. 검색 로직 개선의 1차 근거.
CREATE TABLE IF NOT EXISTS analog_search_feedback (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    query         TEXT,        -- 사용자가 통합검색에 입력한 자연어
    filters_json  TEXT,        -- 검색 당시 드롭다운 필터 (JSON)
    returned_ids  TEXT,        -- 실제 노출된 상위 결과 id (JSON 배열)
    returned_top  TEXT,        -- 상위 결과 브랜드명 요약 (사람이 읽기 위함)
    intended_text TEXT,        -- 사용자가 실제 찾고자 했던 약제/내용 (자유 입력)
    note          TEXT,        -- 추가 코멘트 (선택)
    resolved      INTEGER DEFAULT 0,  -- 개발자 처리 완료 플래그
    created_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_asf_created ON analog_search_feedback(created_at);
CREATE INDEX IF NOT EXISTS idx_asf_resolved ON analog_search_feedback(resolved);
"""

# 마이그레이션 완료 후 생성 (generic_name_en 컬럼 존재 필요)
_SCHEMA_POST_MIGRATE = """
CREATE INDEX IF NOT EXISTS idx_analog_generic_en ON analog_reports(generic_name_en);
CREATE INDEX IF NOT EXISTS idx_analog_disease ON analog_reports(disease_category, cancer_type);
CREATE VIRTUAL TABLE IF NOT EXISTS analog_fts USING fts5(
    brand_name, brand_name_raw, generic_name, generic_name_en, disease_name, disease_name_ko,
    body_text, mfds_effect_text, decision_reason,
    content='analog_reports', content_rowid='id'
);
"""

# 신규 컬럼 목록 (기존 설치 마이그레이션용)
_NEW_COLS: list[tuple[str, str]] = [
    ("brand_name_raw", "TEXT"),
    ("dosage", "TEXT"),
    ("generic_name_en", "TEXT"),
    ("session_year", "INTEGER"),
    ("reimbursable_requested", "INTEGER"),
    ("pdf_extractable", "INTEGER DEFAULT 1"),
    ("disease_category_detail", "TEXT"),
    ("disease_name_ko", "TEXT"),
    ("disease_name_en", "TEXT"),
    ("biomarker", "TEXT"),
    ("treatment_setting", "TEXT"),
    ("review_result_ko", "TEXT"),
    ("reimbursement_track_ko", "TEXT"),
    ("has_rsa", "INTEGER DEFAULT 0"),
    ("pe_waiver", "INTEGER DEFAULT 0"),
    ("has_postmarket_condition", "INTEGER DEFAULT 0"),
    ("postmarket_condition_detail", "TEXT"),
    ("rsa_type_hint", "TEXT"),
    ("committee_history", "TEXT"),
    ("amjilsim_history", "TEXT"),
    ("days_mfds_to_first_committee", "INTEGER"),
    ("days_amjilsim_to_committee", "INTEGER"),
    ("efficacy_data", "TEXT"),
    ("primary_endpoint", "TEXT"),
    ("os_months", "REAL"),
    ("pfs_months", "REAL"),
    ("orr_pct", "REAL"),
    ("key_hr", "REAL"),
    ("comparator_drugs", "TEXT"),
    ("clinical_trials", "TEXT"),
    ("foreign_listing_count", "INTEGER"),
    ("foreign_listing_basis", "INTEGER"),
    ("consulted_societies", "TEXT"),
    ("medical_necessity", "TEXT"),
    ("policy_signals", "TEXT"),
    ("policy_intent_summary", "TEXT"),
    ("policy_tags", "TEXT"),
    ("approval_driver", "TEXT"),
    ("future_conditions", "TEXT"),
    ("decision_reason", "TEXT"),
    ("tags_text", "TEXT"),    # concept canonical+alias 그림자 컬럼 (FTS 동의어 색인용)
    # 약평위 메타(엑셀) 매칭 — 게시물 링크 + 결과 검증/보완
    ("post_url", "TEXT"),                 # HIRA 약평위 게시물 URL
    ("post_blt_no", "INTEGER"),           # 게시물 번호(bltNo)
    ("result_meta", "TEXT"),              # 메타 원문 결과(급여/비급여/조건부…)
    ("result_source", "TEXT"),            # review_result 출처: pdf | meta
    ("review_result_pdf", "TEXT"),        # 메타 교정 전 PDF 원본 결과(추적)
    # 급여 등재일(국내약가 최초 등재) — 허가→급여 타임라인
    ("first_reimbursement_date", "TEXT"),  # MIN(drug_prices.apply_date) 매칭
    ("reimbursement_match_key", "TEXT"),   # 매칭 근거: brand | ingredient
    # RSA/사후조건 미디어 보완 (PDF 원본과 분리, 근거 보존)
    ("rsa_media_types", "TEXT"),          # JSON [refund, expenditure_cap, …]
    ("rsa_media_conditions", "TEXT"),     # JSON 구체 조건
    ("rsa_media_monitoring", "TEXT"),     # JSON 사후 모니터링(기간·지표·재평가)
    ("rsa_media_sources", "TEXT"),        # JSON [{title,url,media,date}]
    ("rsa_media_confidence", "TEXT"),     # high|medium|low
    ("rsa_media_fetched_at", "TEXT"),
]

# corpus 에서 ingest 할 컬럼 목록 (embedding 제외)
_INSERT_COLS = [
    "file_name", "file_hash", "brand_name_raw", "brand_name", "dosage", "generic_name",
    "generic_name_en", "manufacturer", "session_year", "ordinal",
    "reimbursable_requested", "pdf_extractable",
    "disease_category", "disease_name", "cancer_type", "line_of_therapy",
    "committee", "session_date", "review_result", "reimbursement_track_ko",
    "has_rsa", "pe_waiver", "has_postmarket_condition", "postmarket_condition_detail",
    "rsa_type_hint", "rsa_types", "committee_history", "amjilsim_history",
    "foreign_listing_count", "foreign_listing_basis", "medical_necessity",
    "clinical_trials", "policy_signals", "policy_drivers",
    "mfds_effect_text", "mfds_approval_date", "application_date", "amjilsim_date",
    "lag_days_approval_to_reimb", "wikilinks", "decision_reason", "body_text",
]

# JSON 컬럼 (직렬화/역직렬화 대상)
_JSON_COLS = {
    "rsa_types", "policy_drivers", "wikilinks", "committee_history",
    "amjilsim_history", "clinical_trials", "policy_signals", "policy_tags",
    "comparator_drugs", "efficacy_data", "consulted_societies",
}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema() -> None:
    with _connect() as conn:
        # 1단계: 테이블 + 보조 캐시 테이블 (FTS 제외)
        conn.executescript(_SCHEMA_TABLE)
        # 2단계: 신규 컬럼 마이그레이션 (기존 설치 — FTS 생성 전에 먼저 실행)
        existing_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(analog_reports)").fetchall()
        }
        for col_name, col_def in _NEW_COLS:
            if col_name not in existing_cols:
                try:
                    conn.execute(
                        f"ALTER TABLE analog_reports ADD COLUMN {col_name} {col_def}"
                    )
                    logger.debug("[analog.store] 컬럼 추가: %s %s", col_name, col_def)
                except Exception as e:
                    logger.warning("[analog.store] ALTER 실패 %s: %s", col_name, e)
        conn.commit()
        # 3단계: FTS5 + 파생 인덱스 (모든 컬럼 존재 보장 후)
        # 구버전 FTS 테이블에 generic_name_en 없을 수 있으므로 재빌드
        # r[1] = column name (r[2] = type, empty for FTS5)
        fts_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(analog_fts)").fetchall()
        }
        if (not fts_cols or "generic_name_en" not in fts_cols
                or "brand_name_raw" not in fts_cols or "tags_text" not in fts_cols):
            # FTS5 shadow tables 전체 명시 삭제 (executescript 미사용 — 트랜잭션 안전)
            for tbl in ("analog_fts", "analog_fts_config", "analog_fts_data",
                        "analog_fts_docsize", "analog_fts_idx"):
                conn.execute(f"DROP TABLE IF EXISTS {tbl}")
            conn.execute(
                "CREATE VIRTUAL TABLE analog_fts USING fts5("
                "    brand_name, brand_name_raw, generic_name, generic_name_en, disease_name, disease_name_ko,"
                "    body_text, mfds_effect_text, decision_reason, tags_text,"
                "    content='analog_reports', content_rowid='id'"
                ")"
            )
            conn.execute("INSERT INTO analog_fts(analog_fts) VALUES('rebuild')")
            # 파생 인덱스 (이미 존재하면 무시)
            for sql in (
                "CREATE INDEX IF NOT EXISTS idx_analog_generic_en ON analog_reports(generic_name_en)",
                "CREATE INDEX IF NOT EXISTS idx_analog_disease ON analog_reports(disease_category, cancer_type)",
            ):
                conn.execute(sql)
            conn.commit()


# ── concept 그래프 시드 ─────────────────────────────────────────────────────────

def seed_concepts() -> dict:
    """tag_seeds 온톨로지를 analog_concepts/analog_concept_aliases 에 idempotent 적재."""
    ensure_schema()
    from agents.analog import tag_seeds as ts
    with _connect() as conn:
        for c in ts.concepts():
            conn.execute(
                "INSERT OR REPLACE INTO analog_concepts"
                "(concept_id, concept_type, canonical_ko, canonical_en) VALUES (?,?,?,?)",
                (c["concept_id"], c.get("type"), c.get("canonical_ko"), c.get("canonical_en")),
            )
        for n, cid, raw in ts.alias_rows():
            conn.execute(
                "INSERT OR IGNORE INTO analog_concept_aliases(alias_norm, concept_id, alias_raw) "
                "VALUES (?,?,?)",
                (n, cid, raw),
            )
        conn.commit()
        nc = conn.execute("SELECT COUNT(*) FROM analog_concepts").fetchone()[0]
        na = conn.execute("SELECT COUNT(*) FROM analog_concept_aliases").fetchone()[0]
    return {"concepts": nc, "aliases": na}


# concept_type 별 태그 오버랩 가중치 (검색 랭킹용)
_TAG_TYPE_WEIGHT = {
    "disease": 1.5, "target": 1.3, "drug_class": 1.2, "biomarker": 1.1,
    "form": 0.8, "setting": 0.7, "line_of_therapy": 0.6,
}


def _tag_scores(conn, concept_ids: list[str], report_ids: list[int]) -> dict[int, float]:
    """후보 report 별 태그 오버랩 점수 = Σ(row.weight × concept_type_weight)."""
    if not concept_ids or not report_ids:
        return {}
    cph = ",".join("?" for _ in concept_ids)
    rph = ",".join("?" for _ in report_ids)
    rows = conn.execute(
        f"SELECT report_id, concept_id, tag_type, weight FROM analog_report_tags "
        f"WHERE concept_id IN ({cph}) AND report_id IN ({rph})",
        list(concept_ids) + list(report_ids),
    ).fetchall()
    out: dict[int, float] = {}
    for r in rows:
        tw = _TAG_TYPE_WEIGHT.get(r["tag_type"], 1.0)
        out[r["report_id"]] = out.get(r["report_id"], 0.0) + (r["weight"] or 1.0) * tw
    return out


def _base_filter_where(filters: dict) -> tuple[list[str], list]:
    """search() 와 동일한 패싯/PDF 기본 WHERE 절 (재사용)."""
    clauses: list[str] = []
    params: list = []
    for col in _FILTER_COLS:
        val = filters.get(col)
        if val is None:
            continue
        if col in ("has_rsa", "pe_waiver", "has_postmarket_condition"):
            try:
                clauses.append(f"a.{col} = ?")
                params.append(int(val))
            except (ValueError, TypeError):
                pass
        else:
            clauses.append(f"a.{col} = ?")
            params.append(val)
    clauses.append("a.file_name LIKE '%.pdf'")
    if "pdf_extractable" not in filters:
        clauses.append("(a.pdf_extractable IS NULL OR a.pdf_extractable = 1)")
    return clauses, params


def _and_candidate_ids(conn, groups, filters: dict, cap: int) -> list[int]:
    """모든 hard 그룹(concept/field)을 동시에 충족하는 report id (AND 후보 보장)."""
    from agents.analog import domain_terms as dt
    clauses, params = _base_filter_where(filters)
    for g in groups:
        if g.kind == "concept" and g.concept_ids:
            ph = ",".join("?" for _ in g.concept_ids)
            clauses.append(
                f"EXISTS (SELECT 1 FROM analog_report_tags t "
                f"WHERE t.report_id = a.id AND t.concept_id IN ({ph}))"
            )
            params += list(g.concept_ids)
        elif g.kind == "field" and g.domain_key:
            term = dt.by_key(g.domain_key)
            if term:
                clauses.append(term.where_sql)
    sql = ("SELECT a.id FROM analog_reports a WHERE " + " AND ".join(clauses)
           + " ORDER BY a.session_date DESC LIMIT ?")
    params.append(cap)
    return [r[0] for r in conn.execute(sql, params).fetchall()]


def _report_concept_ids(conn, report_ids: list[int]) -> dict[int, set]:
    """report_id → {concept_id, ...} (그룹 충족 판정용)."""
    if not report_ids:
        return {}
    ph = ",".join("?" for _ in report_ids)
    rows = conn.execute(
        f"SELECT report_id, concept_id FROM analog_report_tags WHERE report_id IN ({ph})",
        list(report_ids),
    ).fetchall()
    out: dict[int, set] = {}
    for r in rows:
        out.setdefault(r["report_id"], set()).add(r["concept_id"])
    return out


def _count_groups_satisfied(row, groups, concept_set: set) -> int:
    """후보 row 가 충족하는 hard 그룹 수 (AND 우선순위 정렬용)."""
    from agents.analog import domain_terms as dt
    n = 0
    for g in groups:
        if g.kind == "concept":
            if concept_set & set(g.concept_ids):
                n += 1
        elif g.kind == "field" and g.domain_key:
            term = dt.by_key(g.domain_key)
            if term and term.row_pred(row):
                n += 1
    return n


def _compute_namerank(row, query: str) -> int:
    """FTS 외 후보(AND 직접수집 row)의 약제명 우선순위 — search() CASE 와 동치."""
    if not query:
        return 4
    bn = row["brand_name"] or ""
    bnr = row["brand_name_raw"] or ""
    if bn == query:
        return 0
    if bn.startswith(query) or query in bnr:
        return 1
    if query in (row["generic_name_en"] or "") or query in (row["generic_name"] or ""):
        return 2
    if (query in (row["disease_name_ko"] or "") or query in (row["disease_name"] or "")
            or query in (row["cancer_type"] or "")):
        return 3
    return 4


def _fetch_rows_by_ids(conn, ids: list[int]) -> list:
    if not ids:
        return []
    ph = ",".join("?" for _ in ids)
    return conn.execute(
        f"SELECT a.* FROM analog_reports a WHERE a.id IN ({ph})", list(ids)
    ).fetchall()


def _pack(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _unpack(blob: bytes):
    import numpy as np
    return np.frombuffer(blob, dtype="<f4")


# ── 임베딩 ────────────────────────────────────────────────────────────────────

def _embed_texts(texts: list[str]) -> Optional[list[list[float]]]:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        _load_env_key()
        key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key)
        out: list[list[float]] = []
        # 요청당 토큰 한도(300k) 보호: 건수(≤256) + 누적 글자수(≤250k) 양쪽 캡.
        # 한국어는 토큰당 글자수가 적어 256건 × 8000자가 한도를 넘는다.
        _MAX_BATCH, _MAX_CHARS = 128, 250_000
        batch: list[str] = []
        chars = 0
        for t in texts:
            t = t[:8000]
            if batch and (len(batch) >= _MAX_BATCH or chars + len(t) > _MAX_CHARS):
                resp = client.embeddings.create(model=EMBED_MODEL, input=batch)
                out.extend(d.embedding for d in resp.data)
                batch, chars = [], 0
            batch.append(t)
            chars += len(t)
        if batch:
            resp = client.embeddings.create(model=EMBED_MODEL, input=batch)
            out.extend(d.embedding for d in resp.data)
        return out
    except Exception as e:
        logger.warning("[analog.store] 임베딩 실패: %s", e)
        return None


def _load_env_key() -> None:
    env = BASE_DIR / "config" / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("OPENAI_API_KEY="):
            os.environ.setdefault("OPENAI_API_KEY", line.split("=", 1)[1].strip())


def _embed_doc_text(r: dict) -> str:
    parts = [
        r.get("brand_name") or "",
        r.get("generic_name_en") or r.get("generic_name") or "",
        r.get("disease_name_ko") or r.get("disease_name") or "",
        r.get("cancer_type") or "",
        r.get("line_of_therapy") or "",
        r.get("biomarker") or "",
        r.get("review_result") or "",
        r.get("reimbursement_track_ko") or "",
        r.get("policy_intent_summary") or "",
        (r.get("body_text") or "")[:2000],
    ]
    return " ".join(p for p in parts if p)


# ── ingest ────────────────────────────────────────────────────────────────────

def ingest_corpus(payload: dict, embed: bool = True) -> dict:
    """corpus payload → analog_reports UPSERT. 신규/변경만 임베딩."""
    ensure_schema()
    reports = payload.get("reports", [])
    inserted = updated = skipped = 0
    to_embed: list[tuple[int, dict]] = []

    with _connect() as conn:
        existing = {
            r["file_name"]: (r["id"], r["file_hash"], r["embedding"] is not None)
            for r in conn.execute(
                "SELECT id, file_name, file_hash, embedding FROM analog_reports"
            )
        }
        for r in reports:
            fn, fh = r["file_name"], r["file_hash"]
            vals = {}
            for k in _INSERT_COLS:
                v = r.get(k)
                if k in _JSON_COLS:
                    if isinstance(v, (list, dict)):
                        v = json.dumps(v, ensure_ascii=False)
                    elif v is None:
                        v = "[]"
                elif isinstance(v, bool):
                    v = int(v)
                vals[k] = v

            if fn in existing:
                rid, old_hash, has_emb = existing[fn]
                if old_hash == fh and has_emb:
                    skipped += 1
                    continue
                upd_cols = [c for c in _INSERT_COLS if c != "file_name"]
                conn.execute(
                    f"UPDATE analog_reports SET {', '.join(f'{c}=?' for c in upd_cols)} WHERE id=?",
                    [vals[c] for c in upd_cols] + [rid],
                )
                if old_hash != fh or not has_emb:
                    to_embed.append((rid, r))
                updated += 1
            else:
                cur = conn.execute(
                    f"INSERT INTO analog_reports ({', '.join(_INSERT_COLS)}) "
                    f"VALUES ({', '.join('?' for _ in _INSERT_COLS)})",
                    [vals[c] for c in _INSERT_COLS],
                )
                rid = cur.lastrowid
                to_embed.append((rid, r))
                inserted += 1
        # content-based FTS5 는 analog_reports 를 직접 읽으므로 rebuild 한번만
        if inserted or updated:
            conn.execute("INSERT INTO analog_fts(analog_fts) VALUES('rebuild')")
        conn.commit()

    embedded = 0
    if embed and to_embed:
        vecs = _embed_texts([_embed_doc_text(r) for _, r in to_embed])
        if vecs:
            with _connect() as conn:
                for (rid, _), v in zip(to_embed, vecs):
                    conn.execute("UPDATE analog_reports SET embedding=? WHERE id=?",
                                 (_pack(v), rid))
                conn.commit()
            embedded = len(vecs)

    return {"reports": len(reports), "inserted": inserted, "updated": updated,
            "skipped": skipped, "embedded": embedded}


# ── 검색 ──────────────────────────────────────────────────────────────────────

# 드롭다운 종속(cascade) 순서: 앞단 선택이 뒷단 옵션을 좁힌다.
_CASCADE_ORDER = [
    "disease_category", "disease_category_detail", "cancer_type", "line_of_therapy",
    "review_result", "reimbursement_track_ko", "coverage_gap_type", "approval_driver",
]


def facet_values(filters: dict = None) -> dict:
    """패싯 드롭다운 옵션 + 카운트 (cascade).

    각 facet 의 옵션/카운트는 `_CASCADE_ORDER` 상 **앞단에서 선택된 필터**를 적용한
    부분집합에서 계산한다. 예: 질환군=항암 선택 시 세부질환군·암종·치료차수 등은
    항암 사례 안에서만 집계. filters 없으면 전역 집계(기존 동작).
    """
    ensure_schema()
    filters = filters or {}
    out: dict[str, list] = {}
    with _connect() as conn:
        for col in _FACET_COLS:
            clauses = [f"{col} IS NOT NULL", f"{col} != ''", "file_name LIKE '%.pdf'"]
            params: list = []
            # 앞단(cascade 상 col 보다 먼저 오는) 선택 필터만 적용
            preceding = (_CASCADE_ORDER[:_CASCADE_ORDER.index(col)]
                         if col in _CASCADE_ORDER else _CASCADE_ORDER)
            for pcol in preceding:
                v = filters.get(pcol)
                if v not in (None, ""):
                    clauses.append(f"{pcol} = ?")
                    params.append(v)
            rows = conn.execute(
                f"SELECT {col} v, COUNT(*) n FROM analog_reports "
                f"WHERE {' AND '.join(clauses)} GROUP BY {col} ORDER BY n DESC",
                params,
            ).fetchall()
            out[col] = [{"value": r["v"], "count": r["n"]} for r in rows]
    return out


# ── 검색어 피드백 ───────────────────────────────────────────────────────────────

def add_search_feedback(query: str = None, filters: dict = None,
                        returned_ids: list = None, returned_top: str = None,
                        intended_text: str = None, note: str = None) -> int:
    """사용자 검색 피드백 1건 저장. 반환: 생성된 row id.

    검색 결과가 의도와 다를 때 사용자가 '실제 찾던 약제'를 남긴다.
    검색어 시멘틱 → 의도 매핑을 축적해 검색 로직 개선에 활용.
    """
    ensure_schema()
    from datetime import datetime
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO analog_search_feedback "
            "(query, filters_json, returned_ids, returned_top, intended_text, note, resolved, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
            (
                (query or "").strip() or None,
                json.dumps(filters or {}, ensure_ascii=False),
                json.dumps(returned_ids or [], ensure_ascii=False),
                (returned_top or "").strip() or None,
                (intended_text or "").strip() or None,
                (note or "").strip() or None,
                now,
            ),
        )
        conn.commit()
        return cur.lastrowid


def list_search_feedback(limit: int = 200, only_unresolved: bool = False) -> list[dict]:
    """저장된 검색 피드백 조회 (개발/분석용, 최신순)."""
    ensure_schema()
    where = "WHERE resolved = 0 " if only_unresolved else ""
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM analog_search_feedback {where}ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        for k in ("filters_json", "returned_ids"):
            try:
                d[k] = json.loads(d.get(k) or ("[]" if k == "returned_ids" else "{}"))
            except (ValueError, TypeError):
                d[k] = [] if k == "returned_ids" else {}
        out.append(d)
    return out


def _row_to_dict(r: sqlite3.Row, include_body: bool = False) -> dict:
    d = dict(r)
    for k in _JSON_COLS:
        try:
            d[k] = json.loads(d.get(k) or "[]")
        except (ValueError, TypeError):
            d[k] = []
    d.pop("embedding", None)
    d.pop("_namerank", None)
    d.pop("_bm25", None)
    if not include_body:
        d.pop("body_text", None)
        d.pop("decision_reason", None)
    return d


def search(filters: dict = None, q: str = None, fts: str = None,
           semantic: str = None, limit: int = 50, debug: bool = False) -> dict:
    """패싯 필터 + 동의어 인지 통합 검색 (concept 확장 + 태그 오버랩 + bm25).

    q/fts/semantic 중 아무거나 들어오면 단일 통합 쿼리로 처리한다.
      ① concept_resolver 로 쿼리를 온톨로지 concept 로 해석 → 동의어 전량으로 FTS 확장
         ("고지혈증 주사제" = "이상지질혈증 주사제" = "PCSK9 주사제" 동일 결과)
      ② FTS5 후보군을 _namerank(약제명 직접일치)·bm25 로 1차 정렬
      ③ analog_report_tags 태그 오버랩 점수로 재정렬 (동의어 의미 일치 우선)
    온톨로지/태그 테이블이 비어 있으면 기존 lexical 동작으로 graceful fallback.
    """
    ensure_schema()
    filters = filters or {}
    query = (q or fts or semantic or "").strip() or None

    # ── concept 해석 (동의어 확장) ──────────────────────────────────────────
    resolution = None
    fts_match = None
    if query:
        try:
            from agents.analog.concept_resolver import resolve_query
            resolution = resolve_query(query)
            if resolution.has_concepts:
                fts_match = resolution.fts_query
        except Exception as e:
            logger.warning("[analog.store] concept 해석 실패: %s", e)
        if not fts_match:
            fts_match = _fts_query(query)

    select = "SELECT a.*"
    join = ""
    order = "a.session_date DESC"
    where_clauses: list[str] = []
    params: list = []

    if query:
        like = f"%{query}%"
        prefix = f"{query}%"
        # ① 약제명 우선순위 부스트 (SELECT 절 — 텍스트상 가장 먼저 바인딩)
        select += (
            ", (CASE "
            "WHEN a.brand_name = ? THEN 0 "
            "WHEN a.brand_name LIKE ? OR a.brand_name_raw LIKE ? THEN 1 "
            "WHEN a.generic_name_en LIKE ? OR a.generic_name LIKE ? THEN 2 "
            "WHEN a.disease_name_ko LIKE ? OR a.disease_name LIKE ? OR a.cancer_type LIKE ? THEN 3 "
            "ELSE 4 END) AS _namerank, "
            # 컬럼 가중치: brand>brand_raw>generic(한/영)>disease>effect>body=decision>tags
            "bm25(analog_fts, 12.0, 11.0, 9.0, 9.0, 5.0, 5.0, 1.0, 2.0, 1.5, 4.0) AS _bm25"
        )
        params += [query, prefix, like, like, like, like, like, like]
        join = " JOIN analog_fts f ON f.rowid = a.id"
        where_clauses.append("analog_fts MATCH ?")
        params.append(fts_match)
        order = "_namerank ASC, _bm25 ASC, a.session_date DESC"

    # ② 패싯 필터
    for col in _FILTER_COLS:
        val = filters.get(col)
        if val is None:
            continue
        if col in ("has_rsa", "pe_waiver", "has_postmarket_condition"):
            try:
                where_clauses.append(f"a.{col} = ?")
                params.append(int(val))
            except (ValueError, TypeError):
                pass
        else:
            where_clauses.append(f"a.{col} = ?")
            params.append(val)

    # v1 레거시 .md 제외 — v2 PDF 소스만
    where_clauses.append("a.file_name LIKE '%.pdf'")
    # 스캔 PDF 는 기본 제외 (명시 필터 없으면)
    if "pdf_extractable" not in filters:
        where_clauses.append("(a.pdf_extractable IS NULL OR a.pdf_extractable = 1)")

    hard_groups = resolution.hard_groups if (query and resolution) else []
    # 태그/AND 재정렬을 위해 후보 풀을 넉넉히 확보 (limit 의 4배, 최대 300)
    want_rerank = bool(query and resolution and (resolution.has_concepts or hard_groups))
    pool = min(limit * 4, 300) if want_rerank else limit

    sql = select + " FROM analog_reports a" + join
    if where_clauses:
        sql += " WHERE " + " AND ".join(where_clauses)
    sql += f" ORDER BY {order} LIMIT ?"
    params.append(pool)

    bm25_map: dict[int, float] = {}
    namerank_map: dict[int, int] = {}
    with _connect() as conn:
        text_rows = conn.execute(sql, params).fetchall()
        for r in text_rows:
            bm25_map[r["id"]] = r["_bm25"] if query else 0.0
            namerank_map[r["id"]] = r["_namerank"] if query else 4
        cand_ids = list(bm25_map.keys())

        # ③ AND 후보 보강: 모든 hard 그룹 동시 충족 row 를 풀에 합류 (FTS 누락 방지)
        if hard_groups:
            for i in _and_candidate_ids(conn, hard_groups, filters, pool):
                if i not in bm25_map:
                    cand_ids.append(i)

        rows = _fetch_rows_by_ids(conn, cand_ids) if cand_ids else []

        # ④ 태그 오버랩 점수 (concept 매칭된 경우)
        tag_score: dict[int, float] = {}
        if query and resolution and resolution.concept_ids and cand_ids:
            tag_score = _tag_scores(conn, resolution.concept_ids, cand_ids)

        # ⑤ 그룹 충족 수 (AND 우선순위)
        group_match: dict[int, int] = {}
        if hard_groups and rows:
            tagmap = _report_concept_ids(conn, [r["id"] for r in rows])
            for r in rows:
                group_match[r["id"]] = _count_groups_satisfied(
                    r, hard_groups, tagmap.get(r["id"], set())
                )

    num_hard = len(hard_groups)
    and_rerank = num_hard >= 2

    def _nr(r):
        return namerank_map.get(r["id"], _compute_namerank(r, query) if query else 4)

    # 최종 순위: 약제명 직접일치 → 그룹 AND 충족 desc → 태그오버랩 desc → bm25 asc
    rows = sorted(
        rows,
        key=lambda r: (
            _nr(r),
            -group_match.get(r["id"], 0),
            -tag_score.get(r["id"], 0.0),
            bm25_map.get(r["id"], 1e9),
        ),
    )[:limit]

    results = []
    for r in rows:
        d = _row_to_dict(r)
        if debug and query and resolution:
            d["_tag_score"] = round(tag_score.get(r["id"], 0.0), 3)
            d["_groups_matched"] = group_match.get(r["id"], 0)
        results.append(d)

    out = {"mode": "search" if query else "facet",
           "count": len(results), "results": results}
    if query and resolution:
        out["query_debug"] = {
            "matched_concepts": resolution.matched_concepts,
            "concept_count": len(resolution.concept_ids),
            "tag_rerank": bool(tag_score),
            "and_rerank": and_rerank,
            "groups": [
                {"label": g.label, "kind": g.kind,
                 "domain_key": g.domain_key, "concept_ids": g.concept_ids}
                for g in hard_groups
            ],
        }
    return out


def _semantic_rerank(query: str, rows: list[sqlite3.Row], limit: int):
    qv = _embed_texts([query])
    if not qv:
        return None
    import numpy as np
    q = np.asarray(qv[0], dtype="<f4")
    q = q / (np.linalg.norm(q) + 1e-9)
    scored = []
    for r in rows:
        blob = r["embedding"]
        if not blob:
            continue
        v = _unpack(blob)
        sim = float(np.dot(q, v) / (np.linalg.norm(v) + 1e-9))
        d = _row_to_dict(r)
        d["similarity"] = round(sim, 4)
        scored.append((sim, d))
    if not scored:
        return None
    scored.sort(key=lambda x: -x[0])
    return [d for _, d in scored[:limit]]


def _fts_query(text: str) -> str:
    import re
    toks = [t for t in re.findall(r"[\w가-힣]+", text) if len(t) >= 2]
    return " OR ".join(f'"{t}"*' for t in toks) if toks else '""'


def get_detail(report_id: int) -> Optional[dict]:
    ensure_schema()
    with _connect() as conn:
        r = conn.execute("SELECT * FROM analog_reports WHERE id=?", (report_id,)).fetchone()
    if not r:
        return None
    return _row_to_dict(r, include_body=True)


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if len(sys.argv) > 1 and sys.argv[1] == "ingest":
        payload = json.loads(
            (BASE_DIR / "agents" / "ingest" / "analog_corpus.json")
            .read_text(encoding="utf-8")
        )
        print(json.dumps(
            ingest_corpus(payload, embed="--no-embed" not in sys.argv),
            ensure_ascii=False, indent=2,
        ))
    else:
        print(json.dumps(facet_values(), ensure_ascii=False, indent=2)[:800])
