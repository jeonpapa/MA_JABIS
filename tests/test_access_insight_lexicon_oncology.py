"""Access Insight B7/B6/B5 — lexicon 분류·is_oncology 캐스케이드·committee-aware 세션.

- B7: DB lexicon 우선 분류 + 오분류 콜리전 교정 + fallback 완화 + reclassify UPDATE.
- B6: is_oncology 우선순위 캐스케이드(ATC/efficacy/analog/indication).
- B5: nearest_session_id committee-aware(비항암=암질심 강제 배정 금지) + expected_committee.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from agents.access_insight import classify as C
from agents.access_insight import backfill as B
from agents.access_insight.aggregate import leaderboard, journey


_SCHEMA = """
CREATE TABLE amjilsim_drugs (
    drug_id INTEGER PRIMARY KEY,
    product_slug TEXT,
    brand_kr TEXT NOT NULL,
    brand_en TEXT,
    ingredient_inn TEXT,
    atc TEXT,
    efficacy_group TEXT,
    indication TEXT,
    msd_flag INTEGER NOT NULL DEFAULT 0,
    expected_session_id INTEGER,
    amjilsim_pass_date DATE,
    yakpyungwi_pass_date DATE,
    is_oncology INTEGER
);
CREATE TABLE amjilsim_sessions (
    session_id INTEGER PRIMARY KEY,
    year INTEGER NOT NULL,
    ordinal_assumed INTEGER NOT NULL DEFAULT 0,
    ordinal_official INTEGER,
    session_date DATE NOT NULL,
    status TEXT NOT NULL DEFAULT 'SCHEDULED',
    committee_type TEXT NOT NULL DEFAULT 'AMJILSIM'
);
CREATE TABLE amjilsim_media_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    drug_id INTEGER,
    session_id INTEGER,
    tier TEXT NOT NULL,
    outlet TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT,
    published_at TEXT,
    snippet TEXT,
    signal_type TEXT,
    signal_phrases TEXT,
    crossref_count INTEGER NOT NULL DEFAULT 1,
    weight REAL NOT NULL DEFAULT 1.0,
    crawled_at TEXT,
    committee_target TEXT NOT NULL DEFAULT 'UNKNOWN',
    source_verified TEXT NOT NULL DEFAULT 'headline_only'
);
CREATE TABLE analog_reports (
    id INTEGER PRIMARY KEY,
    brand_name TEXT,
    disease_category TEXT,
    mfds_permit_date TEXT,
    first_reimbursement_date TEXT
);
"""


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "t.db"
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()
    C.invalidate_lexicon_cache()
    yield str(path)
    C.invalidate_lexicon_cache()


# ── B7: lexicon 분류 ──────────────────────────────────────────────────────────
def test_seed_and_load_lexicon(db):
    conn = sqlite3.connect(db)
    n = C.seed_lexicon(conn)
    conn.close()
    assert n > 0
    lex = C.load_lexicon(db)
    # priority 오름차순 정렬 확인
    priorities = [e["priority"] for e in lex]
    assert priorities == sorted(priorities)


def test_collision_uiwon_not_matching_clinic(db):
    """'의원' 콜리전 교정: 병'의원'(clinic) 은 GOV 로 오분류되면 안 된다."""
    conn = sqlite3.connect(db)
    C.seed_lexicon(conn)
    conn.close()
    lex = C.load_lexicon(db)
    # 병의원 개설 기사 — GOV_STATEMENT 아니어야 함 (fallback IR)
    st, _ = C.classify_signal_type("동네 병의원 개설 증가", "1차 의료기관 현황", "competitor", lexicon=lex)
    assert st != C.GOV_STATEMENT
    # 진짜 국회의원 발의 기사 — GOV_STATEMENT
    st2, ph2 = C.classify_signal_type("국회의원 국정감사 법안 발의", "", "competitor", lexicon=lex)
    assert st2 == C.GOV_STATEMENT


def test_collision_tonggwa_result_context(db):
    """'통과' 콜리전 교정: 약평위 통과만 RESULT_REPORT."""
    conn = sqlite3.connect(db)
    C.seed_lexicon(conn)
    conn.close()
    lex = C.load_lexicon(db)
    st, _ = C.classify_signal_type("키트루다 약평위 통과", "", "competitor", lexicon=lex)
    assert st == C.RESULT_REPORT
    # 무관한 '통과'(터널 통과 등) 는 RESULT 로 안 잡힘
    st2, _ = C.classify_signal_type("환자 검문소 통과", "", "competitor", lexicon=lex)
    assert st2 != C.RESULT_REPORT


def test_fallback_not_blanket_ir(db):
    """미매칭 gov_policy → GOV_STATEMENT (blanket IR 아님)."""
    conn = sqlite3.connect(db)
    C.seed_lexicon(conn)
    conn.close()
    lex = C.load_lexicon(db)
    st, _ = C.classify_signal_type("건강보험 정책 동향", "복지부 발표", "gov_policy", lexicon=lex)
    assert st == C.GOV_STATEMENT
    # 매칭 없는 일반 competitor → IR_RELEASE (kind 기반)
    st2, _ = C.classify_signal_type("신제품 출시 소식", "브랜드 리뉴얼", "competitor", lexicon=lex)
    assert st2 == C.IR_RELEASE


def test_new_lexicon_terms_classify_accurately(db):
    """B7 확장 lexicon: 프로드 UNCLASSIFIED 클러스터가 올바른 유형으로 분류."""
    conn = sqlite3.connect(db)
    C.seed_lexicon(conn)
    conn.close()
    lex = C.load_lexicon(db)

    def cls(title):
        return C.classify_signal_type(title, "", "competitor", lexicon=lex, unclassified_ok=True)[0]

    # 급여 collocation → RESULT_REPORT
    assert cls("옵디보 위암 급여확대 '적정'") == C.RESULT_REPORT
    assert cls("옵디보+여보이, 간암 1차 급여 문턱 넘어") == C.RESULT_REPORT
    assert cls("카나브젯·소그로야, 내달 신규 급여") == C.RESULT_REPORT
    assert cls("난소암 신약 급여 심사 착수") == C.RESULT_REPORT
    # 이해관계자(복지부) 명시 시 그 stakeholder 유형 우선 (급여 mention 이 있어도 GOV)
    assert cls("복지부, 난소암 신약 급여 심사") == C.GOV_STATEMENT
    assert cls("소그로야 급여 등재, 펠루비 상한액 인하") == C.RESULT_REPORT
    # 임박/청신호 → PRE_AGENDA_LEAK (다가오는 이벤트)
    assert cls("옵디보 위암 급여확대 임박") == C.PRE_AGENDA_LEAK
    assert cls("키트루다·옵디보 위암 급여 확대 '청신호'") == C.PRE_AGENDA_LEAK
    # 학술대회 → KOL_OPINION
    assert cls("[ASCO 2026] 지헤라 병용, 위암 혜택") == C.KOL_OPINION
    assert cls("[AACR 2026] 유전자치료 간암 반응률 개선") == C.KOL_OPINION
    # 규제 마일스톤 → IR_RELEASE
    assert cls("BMS '옵디보+AVD' 병용요법 유럽 승인") == C.IR_RELEASE
    assert cls("림카토 품목허가, 국내 CAR-T 상용화") == C.IR_RELEASE
    assert cls("옵디보, 이필리무맙 병용 적응증 확대 승인") == C.IR_RELEASE
    # 재무/IPO → IR_RELEASE
    assert cls("넥스아이, 기술성평가 통과…상장예비심사") == C.IR_RELEASE
    assert cls("알테오젠 특허 완화에 증권가 '실적 모멘텀 주목'") == C.IR_RELEASE
    # 종양 산업 catch-all → IR_RELEASE (더 강한 신호 없을 때만)
    assert cls("글로벌 항암제 시장 화두는 '피하주사 전환'") == C.IR_RELEASE


def test_new_terms_no_collision_regression(db):
    """확장 lexicon 이 기존 콜리전 교정을 되돌리지 않는지 (의원/통과/실적/급여 salary)."""
    conn = sqlite3.connect(db)
    C.seed_lexicon(conn)
    conn.close()
    lex = C.load_lexicon(db)

    def cls(title, snip=""):
        return C.classify_signal_type(title, snip, "competitor", lexicon=lex, unclassified_ok=True)[0]

    # 병'의원'(clinic) 은 여전히 GOV 아님
    assert cls("동네 병의원 개설 증가") != C.GOV_STATEMENT
    # 무관한 '통과'(검문소) 는 RESULT 아님
    assert cls("환자 검문소 통과") != C.RESULT_REPORT
    # bare 급여 미도입 확인: '근로자 급여'(salary) 는 RESULT 로 오분류되지 않음
    assert cls("건설 현장 근로자 급여 인상 논의") != C.RESULT_REPORT
    # 진짜 국회의원 발의는 여전히 GOV
    assert cls("국회의원 급여 확대 법안 발의") in (C.GOV_STATEMENT, C.RESULT_REPORT)


def test_seed_lexicon_upsert_adds_new_tokens_preserves_edits(tmp_path):
    """seed_lexicon 은 빈 테이블 게이팅이 아니라 per-token INSERT OR IGNORE —
    이미 시딩된 테이블에 재실행 시 신규 토큰만 추가, admin 편집 보존."""
    path = tmp_path / "lex.db"
    conn = sqlite3.connect(path)
    # 1st seed
    n1 = C.seed_lexicon(conn)
    assert n1 == len(C._SEED_LEXICON)
    # admin 이 기존 토큰 편집 (is_active=0, weight 변경)
    conn.execute(
        "UPDATE amjilsim_signature_lexicon SET is_active=0, weight=9.9 WHERE token='급여 확대'"
    )
    # 신규 토큰이 나중에 추가된 상황 시뮬레이션: 임의 토큰 삭제 후 재시드가 되살리는지
    conn.execute("DELETE FROM amjilsim_signature_lexicon WHERE token='암질심'")
    conn.commit()
    # 2nd seed (재실행) — 삭제된 토큰만 재삽입, 편집된 토큰은 보존
    n2 = C.seed_lexicon(conn)
    assert n2 == 1  # '암질심' 1건만 새로 삽입
    row = conn.execute(
        "SELECT is_active, weight FROM amjilsim_signature_lexicon WHERE token='급여 확대'"
    ).fetchone()
    assert row == (0, 9.9)  # admin 편집 보존 (덮어쓰지 않음)
    assert conn.execute(
        "SELECT COUNT(*) FROM amjilsim_signature_lexicon WHERE token='암질심'"
    ).fetchone()[0] == 1
    conn.close()


def test_unclassified_fallback_opt_in(db):
    """unclassified_ok=True 면 미매칭 competitor → UNCLASSIFIED, 아니면 IR_RELEASE."""
    conn = sqlite3.connect(db)
    C.seed_lexicon(conn)
    conn.close()
    lex = C.load_lexicon(db)
    # 기본(하위호환): IR_RELEASE
    st, _ = C.classify_signal_type("무관한 제품 소식", "", "competitor", lexicon=lex)
    assert st == C.IR_RELEASE
    # opt-in: UNCLASSIFIED
    st2, _ = C.classify_signal_type(
        "무관한 제품 소식", "", "competitor", lexicon=lex, unclassified_ok=True
    )
    assert st2 == C.UNCLASSIFIED
    # gov_policy 는 opt-in 여부와 무관하게 GOV_STATEMENT
    st3, _ = C.classify_signal_type("정책 동향", "", "gov_policy", lexicon=lex, unclassified_ok=True)
    assert st3 == C.GOV_STATEMENT


def test_reclassify_signals_updates_not_deletes(db):
    conn = sqlite3.connect(db)
    conn.executemany(
        "INSERT INTO amjilsim_media_signals (drug_id, tier, outlet, url, title, snippet, "
        "signal_type, weight) VALUES (?,?,?,?,?,?,?,?)",
        [
            (1, "A", "o1", "u1", "국회의원 국정감사 법안 발의", "", "IR_RELEASE", 0.8),
            (1, "A", "o2", "u2", "환자단체 청원 접수", "", "IR_RELEASE", 0.8),
            (1, "A", "o3", "u3", "약평위 통과", "", "IR_RELEASE", 0.8),
        ],
    )
    conn.commit()
    conn.close()

    res = C.reclassify_signals(db)
    assert res["total"] == 3
    assert res["changed"] == 3  # 3건 모두 IR → 실제 유형으로 이동

    conn = sqlite3.connect(db)
    types = sorted(r[0] for r in conn.execute("SELECT signal_type FROM amjilsim_media_signals"))
    n = conn.execute("SELECT COUNT(*) FROM amjilsim_media_signals").fetchone()[0]
    conn.close()
    assert n == 3  # 삭제 없음
    assert types == ["GOV_STATEMENT", "PATIENT_PETITION", "RESULT_REPORT"]


# ── B6: is_oncology 캐스케이드 ────────────────────────────────────────────────
def test_oncology_cascade(db):
    conn = sqlite3.connect(db)
    conn.executemany(
        "INSERT INTO amjilsim_drugs (drug_id, brand_kr, atc, efficacy_group, indication) "
        "VALUES (?,?,?,?,?)",
        [
            (1, "키트루다", "L01FF02", None, None),           # ① ATC L01
            (2, "약X", "L02BX", None, None),                  # ① ATC L02
            (3, "약Y", "V08AA", "항악성종양제", None),         # ② efficacy_group
            (4, "바벤시오", None, None, None),                 # ③ analog join
            (5, "약Z", None, None, "전이성 유방암 2차 치료"),   # ④ indication 키워드
            (6, "위장약", "A02BC", "소화성궤양용제", "위궤양"),  # ⑤ none → 0
        ],
    )
    conn.execute("INSERT INTO analog_reports (id, brand_name, disease_category) VALUES (1,'바벤시오','항암')")
    conn.commit()
    conn.close()

    res = B.backfill_oncology(db)
    assert res["oncology"] == 5
    assert res["general"] == 1
    assert res["by_rule"]["atc"] == 2
    assert res["by_rule"]["efficacy_group"] == 1
    assert res["by_rule"]["analog"] == 1
    assert res["by_rule"]["indication"] == 1
    assert [m["drug_id"] for m in res["manual_review"]] == [6]

    conn = sqlite3.connect(db)
    flags = dict(conn.execute("SELECT drug_id, is_oncology FROM amjilsim_drugs"))
    conn.close()
    assert flags == {1: 1, 2: 1, 3: 1, 4: 1, 5: 1, 6: 0}


def test_oncology_idempotent(db):
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO amjilsim_drugs (drug_id, brand_kr, atc) VALUES (1,'키트루다','L01FF02')")
    conn.commit()
    conn.close()
    r1 = B.backfill_oncology(db)
    r2 = B.backfill_oncology(db)
    assert r1["oncology"] == r2["oncology"] == 1


def test_oncology_english_abbrev_and_inn_override(db):
    """프로드 오분류 교정: 영문 적응증(NSCLC/DLBCL) + 골수섬유증 + INN 오버라이드."""
    conn = sqlite3.connect(db)
    conn.executemany(
        "INSERT INTO amjilsim_drugs (drug_id, brand_kr, ingredient_inn, atc, efficacy_group, "
        "indication) VALUES (?,?,?,?,?,?)",
        [
            # 프로드에서 general 로 오분류됐던 4종 (영문/희소 표기 → 이제 oncology)
            (45, "리브리반트주", "amivantamab", None, None,
             "EGFR 엑손20 삽입 변이 NSCLC — 백금기반 화학요법 중/이후 진행"),
            (56, "타그리소 확대", "osimertinib", None, None, "EGFR+ NSCLC 급여 확대"),
            (48, "림카토주", "anbalcabtagene autoleucel", None, None,
             "r/r DLBCL·PMBCL CAR-T (국산 1호)"),
            (19, "옴짜라", None, None, None, "골수섬유증"),
            # INN 오버라이드 전용 케이스: indication 비어있음
            (99, "테스트항암", "osimertinib", None, None, None),
            # 진짜 일반약 (오버플립 금지) — 당뇨/천식/희귀
            (23, "마운자로", "tirzepatide", None, "당뇨병용제", "제2형 당뇨병"),
            (36, "듀피젠트", "dupilumab", None, None, "천식 확대"),
            (21, "스핀라자", "nusinersen", None, None, "SMA 확대"),
        ],
    )
    conn.commit()
    conn.close()

    res = B.backfill_oncology(db)
    conn = sqlite3.connect(db)
    flags = dict(conn.execute("SELECT drug_id, is_oncology FROM amjilsim_drugs"))
    conn.close()
    # 4 misses + INN-only → oncology
    assert flags[45] == 1 and flags[56] == 1 and flags[48] == 1 and flags[19] == 1
    assert flags[99] == 1  # inn_override (indication 없음)
    assert res["by_rule"]["inn_override"] == 1
    # 일반약은 그대로 general (보수적 — 오버플립 없음)
    assert flags[23] == 0 and flags[36] == 0 and flags[21] == 0


# ── B5/A2: committee-aware nearest_session + expected_committee ───────────────
def test_expected_committee():
    assert B.expected_committee(1) == "AMJILSIM"
    # A2 — 일반약의 진입 위원회는 약평위 (급여기준소위는 내부 소위 — 폐기).
    assert B.expected_committee(0) == "YAKPYUNGWI"
    # 미상(백필 전) → None (특정 위원회로 단정하면 백필 전 항암제가 오표기) — committee-agnostic.
    assert B.expected_committee(None) is None


def test_journey_unknown_oncology_has_null_committee(db):
    """is_oncology 미상 약제는 expected_committee=None → 프론트가 라벨 숨김."""
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO amjilsim_drugs (drug_id, brand_kr, is_oncology) VALUES (1,'미상약',NULL)")
    conn.commit()
    conn.close()
    j = journey(1, db_path=db)
    assert j["is_oncology"] is None
    assert j["expected_committee"] is None


def test_nearest_session_committee_aware():
    sessions = [
        ("2026-05-27", 10, "AMJILSIM"),
        ("2026-06-04", 20, "YAKPYUNGWI"),
        ("2026-07-08", 11, "AMJILSIM"),
    ]
    # 항암 → 암질심 세션만
    assert B.nearest_session_id(sessions, "2026-05-01", committee_type="AMJILSIM") == 10
    # 일반 → 약평위 세션만 (암질심 강제 배정 금지)
    assert B.nearest_session_id(sessions, "2026-05-01", committee_type="YAKPYUNGWI") == 20
    # 해당 위원회 세션 일정이 없으면 None (다른 위원회로 강제 배정하지 않음)
    only_amjilsim = [("2026-05-27", 10, "AMJILSIM")]
    assert B.nearest_session_id(only_amjilsim, "2026-05-01", committee_type="YAKPYUNGWI") is None
    # committee 미지정 → 위원회 무관 최근접
    assert B.nearest_session_id(sessions, "2026-06-01") == 20


def test_leaderboard_class_filter_and_fields(db):
    conn = sqlite3.connect(db)
    conn.executemany(
        "INSERT INTO amjilsim_drugs (drug_id, brand_kr, is_oncology) VALUES (?,?,?)",
        [(1, "항암약", 1), (2, "일반약", 0)],
    )
    conn.executemany(
        "INSERT INTO amjilsim_media_signals (drug_id, tier, outlet, url, title, published_at, "
        "signal_type, weight) VALUES (?,?,?,?,?,?,?,?)",
        [
            (1, "A", "o1", "u1", "t1", "2026-06-01", "GOV_STATEMENT", 1.5),
            (2, "A", "o2", "u2", "t2", "2026-06-01", "IR_RELEASE", 0.8),
        ],
    )
    conn.commit()
    conn.close()

    onco = leaderboard(db_path=db, drug_class="oncology")
    assert [d["drug_id"] for d in onco] == [1]
    assert onco[0]["is_oncology"] == 1
    assert onco[0]["expected_committee"] == "AMJILSIM"

    gen = leaderboard(db_path=db, drug_class="general")
    assert [d["drug_id"] for d in gen] == [2]
    # A2 — 일반약 진입 위원회 = 약평위 (구 BENEFIT_SUBCOMMITTEE 폐기)
    assert gen[0]["expected_committee"] == "YAKPYUNGWI"
    assert gen[0]["track"] == "general"
    assert onco[0]["track"] == "oncology"

    allrows = leaderboard(db_path=db)
    assert len(allrows) == 2


def test_ensure_signal_type_unclassified_migration(tmp_path):
    """CHECK 확장 migration 이 UNCLASSIFIED 를 허용하게 만들고, 기존 행을 보존한다."""
    from scripts.migrate_amjilsim_v1 import ensure_signal_type_unclassified

    path = tmp_path / "sig.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE amjilsim_media_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drug_id INTEGER, session_id INTEGER,
            tier TEXT NOT NULL, outlet TEXT NOT NULL, url TEXT NOT NULL,
            title TEXT, published_at TEXT, snippet TEXT,
            signal_type TEXT CHECK (signal_type IN
                ('PRE_AGENDA_LEAK','QUEUE_INVENTORY','IR_RELEASE','GOV_STATEMENT',
                 'PATIENT_PETITION','KOL_OPINION','RESULT_REPORT')),
            signal_phrases TEXT, crossref_count INTEGER NOT NULL DEFAULT 1,
            weight REAL NOT NULL DEFAULT 1.0, crawled_at TEXT,
            UNIQUE(outlet, url)
        )
        """
    )
    conn.execute("CREATE INDEX idx_as_drug ON amjilsim_media_signals(drug_id)")
    conn.execute(
        "INSERT INTO amjilsim_media_signals (tier, outlet, url, signal_type) "
        "VALUES ('A','o','u','IR_RELEASE')"
    )
    conn.commit()

    assert C.unclassified_allowed(conn) is False
    did = ensure_signal_type_unclassified(conn)
    assert did is True
    assert C.unclassified_allowed(conn) is True
    # 기존 행 보존 + 이제 UNCLASSIFIED 쓰기 가능
    assert conn.execute("SELECT COUNT(*) FROM amjilsim_media_signals").fetchone()[0] == 1
    conn.execute(
        "UPDATE amjilsim_media_signals SET signal_type='UNCLASSIFIED' WHERE url='u'"
    )
    conn.commit()
    # 인덱스도 재생성됨
    idx = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='amjilsim_media_signals'"
    )]
    assert "idx_as_drug" in idx
    # 멱등 — 두 번째 호출은 no-op
    assert ensure_signal_type_unclassified(conn) is False
    conn.close()


def test_ensure_benefit_subcommittee_check_migration(tmp_path):
    """committee_type CHECK 확장이 BENEFIT_SUBCOMMITTEE 를 허용하고 행/FK 를 보존한다."""
    from scripts.migrate_amjilsim_v1 import ensure_benefit_subcommittee_check

    path = tmp_path / "sess.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE amjilsim_sessions (
            session_id INTEGER PRIMARY KEY AUTOINCREMENT,
            year INTEGER NOT NULL,
            ordinal_assumed INTEGER NOT NULL,
            session_date DATE NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'SCHEDULED',
            committee_type TEXT NOT NULL DEFAULT 'AMJILSIM'
                CHECK (committee_type IN ('AMJILSIM','YAKPYUNGWI'))
        )
        """
    )
    conn.execute("CREATE INDEX idx_as_committee ON amjilsim_sessions(committee_type)")
    conn.execute(
        "INSERT INTO amjilsim_sessions (session_id, year, ordinal_assumed, session_date, "
        "committee_type) VALUES (10, 2026, 1, '2026-05-27', 'AMJILSIM')"
    )
    conn.commit()

    # 확장 전엔 BSC INSERT 가 CHECK 위반
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO amjilsim_sessions (year, ordinal_assumed, session_date, committee_type) "
            "VALUES (2026, 2, '2026-06-01', 'BENEFIT_SUBCOMMITTEE')"
        )
    conn.rollback()

    did = ensure_benefit_subcommittee_check(conn)
    assert did is True
    # session_id 값 보존
    assert conn.execute("SELECT committee_type FROM amjilsim_sessions WHERE session_id=10").fetchone()[0] == "AMJILSIM"
    # 이제 BSC INSERT 가능
    conn.execute(
        "INSERT INTO amjilsim_sessions (year, ordinal_assumed, session_date, committee_type) "
        "VALUES (2026, 2, '2026-06-01', 'BENEFIT_SUBCOMMITTEE')"
    )
    conn.commit()
    idx = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='amjilsim_sessions'"
    )]
    assert "idx_as_committee" in idx
    # 멱등
    assert ensure_benefit_subcommittee_check(conn) is False
    conn.close()


def test_reclassify_seeds_lexicon_when_empty(tmp_path):
    """lexicon 미시딩 DB 에서도 reclassify 가 seed 후 신규 lexicon 으로 분류 (구 상수 폴백 X)."""
    path = tmp_path / "r.db"
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    # lexicon 테이블만 비운 채로 신호 삽입 (seed 안 함)
    conn.execute(
        "INSERT INTO amjilsim_media_signals (drug_id, tier, outlet, url, title, signal_type, weight) "
        "VALUES (1,'A','o1','u1','국회의원 급여 확대 법안 발의','IR_RELEASE',0.8)"
    )
    conn.commit()
    conn.close()
    C.invalidate_lexicon_cache(str(path))

    # lexicon 은 비어있음 (seed 전)
    assert C.load_lexicon(str(path)) == []
    C.invalidate_lexicon_cache(str(path))

    res = C.reclassify_signals(str(path))
    assert res["changed"] == 1  # IR → GOV_STATEMENT (신규 lexicon)
    # reclassify 가 lexicon 을 시딩했는지
    assert len(C.load_lexicon(str(path))) > 0
    conn = sqlite3.connect(path)
    st = conn.execute("SELECT signal_type FROM amjilsim_media_signals WHERE url='u1'").fetchone()[0]
    conn.close()
    assert st == "GOV_STATEMENT"


def test_journey_exposes_committee(db):
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO amjilsim_drugs (drug_id, brand_kr, is_oncology) VALUES (1,'항암약',1)")
    conn.commit()
    conn.close()
    j = journey(1, db_path=db)
    assert j["is_oncology"] == 1
    assert j["expected_committee"] == "AMJILSIM"
