"""기존 competitor_news 아카이브 → amjilsim_media_signals 백필 — Access Insight S1.

READ-ONLY: competitor_news / amjilsim_drugs / product_alias_map / amjilsim_sessions.
WRITE: amjilsim_media_signals 만 (INSERT, 기존 행 변경 없음).

멱등: (url, drug_id) 조합이 이미 있으면 skip (check-before-insert).

S5 (신선 신호 크롤러) 공유 계약: `insert_signal` / `nearest_session_id` /
`load_sessions_sorted` / `TIER_LETTER` 는 `agents/amjilsim_tracker/signal_extractor.py`
가 재사용하는 공용 헬퍼 — 백필과 신선 경로가 동일한 (url, drug_id) 멱등 INSERT
계약을 유지하도록 여기 한 곳에만 둔다 (복붙 금지).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

from .classify import (
    classify_signal_type,
    load_lexicon,
    seed_lexicon,
    signal_weight,
    unclassified_allowed,
)
from .link import (
    DEFAULT_DB_PATH,
    PROMINENCE_PASSING,
    build_alias_index,
    drug_in_text,
    drug_prominence,
    resolve_drug_with_prominence,
)

PathLike = Union[str, Path]

# competitor_news.tier(INTEGER, 1/2/3) → amjilsim_media_signals.tier(TEXT, A/B/D).
# 1=Tier1 전문지, 2=Tier2 종합·경제·통신, 3=미분류 도메인(사실상 미등록) → 'D'.
TIER_LETTER = {1: "A", 2: "B", 3: "D"}
_TIER_LETTER = TIER_LETTER  # 하위호환 별칭

DEFAULT_KINDS: tuple[str, ...] = ("competitor", "gov_policy", "msd_asset")

# A2 — 약제 track 별 예상(진입) 위원회.
#   도메인 사실 (korea-drug-pricing-system): 약평위(약제급여평가위원회)는 항암/일반
#   **공통**의 결정 위원회이고, 항암제만 그 앞에 암질심을 추가로 거친다.
#   급여기준소위(BENEFIT_SUBCOMMITTEE)는 내부 평가 소위원회로 track 진입 위원회가
#   아니다 — enum 상수는 하위호환용으로만 유지하고 더 이상 배정에 쓰지 않는다.
COMMITTEE_AMJILSIM = "AMJILSIM"
COMMITTEE_YAKPYUNGWI = "YAKPYUNGWI"
COMMITTEE_BENEFIT_SUB = "BENEFIT_SUBCOMMITTEE"  # deprecated — 진입 위원회로 사용 금지


def expected_committee(is_oncology: Optional[int]) -> Optional[str]:
    """약제 is_oncology 플래그 → 예상 진입 위원회 라벨.

    - 1(항암)          → AMJILSIM (암질심)
    - 0(일반)          → YAKPYUNGWI (약평위 — 일반약도 약평위에 도달한다.
      구 BENEFIT_SUBCOMMITTEE 배정은 폐기: 급여기준소위는 내부 소위이지 진입 위원회가
      아니며, 소위 세션 일정이 없어 일반약 신호가 세션 미배정으로 남았었다)
    - None(미상, 백필 전) → None — 위원회 배정을 committee-agnostic 으로 두어 백필 전
      항암제(예: Keytruda)를 오단정하지 않는다.
    """
    if is_oncology == 1:
        return COMMITTEE_AMJILSIM
    if is_oncology == 0:
        return COMMITTEE_YAKPYUNGWI
    return None


def _connect(db_path: PathLike) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def nearest_session_id(
    sessions_sorted: list[tuple],
    pub_date: str,
    committee_type: Optional[str] = None,
) -> Optional[int]:
    """pub_date 이후(>=) 가장 이른 session_id (committee-aware).

    sessions_sorted 항목은 (session_date, session_id) 또는 committee 포함
    (session_date, session_id, committee_type) 튜플 모두 허용 (하위호환).

    committee_type 이 주어지면 해당 위원회 세션만 후보로 삼는다. 예: 비항암 약제는
    committee_type='BENEFIT_SUBCOMMITTEE' 로 호출되며, 급여기준소위 세션 일정이 없으면
    None 을 반환한다 — **절대 암질심(AMJILSIM) 세션으로 강제 배정하지 않는다.**
    """
    for item in sessions_sorted:
        session_date, session_id = item[0], item[1]
        sess_committee = item[2] if len(item) > 2 else None
        if committee_type and sess_committee is not None and sess_committee != committee_type:
            continue
        if session_date and session_date >= pub_date:
            return session_id
    return None


_nearest_session_id = nearest_session_id  # 하위호환 별칭


def load_sessions_sorted(conn: sqlite3.Connection) -> list[tuple]:
    """amjilsim_sessions 를 (session_date, session_id, committee_type) 오름차순 로드.

    3-tuple 로 committee_type 을 포함해 nearest_session_id 의 committee-aware 필터를
    지원한다 (구 2-tuple 소비자와도 unpack 호환 — 앞 두 요소 동일).
    """
    rows = conn.execute(
        "SELECT session_id, session_date, committee_type "
        "FROM amjilsim_sessions ORDER BY session_date ASC"
    ).fetchall()
    return [(r["session_date"], r["session_id"], r["committee_type"]) for r in rows]


def _has_prominence_column(conn: sqlite3.Connection) -> bool:
    return any(
        r[1] == "prominence"
        for r in conn.execute("PRAGMA table_info(amjilsim_media_signals)")
    )


def ensure_prominence_column(conn: sqlite3.Connection) -> None:
    """amjilsim_media_signals.prominence TEXT 멱등 추가 (A1)."""
    if not _has_prominence_column(conn):
        conn.execute("ALTER TABLE amjilsim_media_signals ADD COLUMN prominence TEXT")


def insert_signal(
    conn: sqlite3.Connection,
    *,
    drug_id: int,
    session_id: Optional[int],
    tier: str,
    outlet: str,
    url: str,
    title: str,
    published_at: Optional[str],
    snippet: str,
    signal_type: str,
    signal_phrases: list[str],
    crossref_count: int = 0,
    weight: float = 1.0,
    source_verified: str = "snippet_match",
    committee_target: str = "UNKNOWN",
    prominence: Optional[str] = None,
) -> bool:
    """amjilsim_media_signals 에 1행 INSERT. (url, drug_id) 멱등 — 이미 있으면 False.

    커밋은 호출자 책임. 기존 행은 절대 UPDATE/DELETE 하지 않는다 (INSERT-only).
    스키마의 UNIQUE(outlet, url) 충돌(예: alias 인덱스 변경으로 같은 기사가 다른
    drug 으로 재해석된 경우)도 duplicate 로 흡수해 기존 행을 보호한다.

    prominence 는 A1 컬럼 — 미마이그레이션 DB(컬럼 없음)에서는 자동으로 생략한다.
    """
    existing = conn.execute(
        "SELECT 1 FROM amjilsim_media_signals WHERE url = ? AND drug_id = ? LIMIT 1",
        (url, drug_id),
    ).fetchone()
    if existing:
        return False
    columns = [
        "drug_id", "session_id", "tier", "outlet", "url", "title", "published_at",
        "snippet", "signal_type", "signal_phrases", "crossref_count", "weight",
        "crawled_at", "source_verified", "committee_target",
    ]
    values: list = [
        drug_id,
        session_id,
        tier,
        outlet,
        url,
        title,
        published_at,
        snippet,
        signal_type,
        json.dumps(signal_phrases, ensure_ascii=False),
        crossref_count,
        weight,
        _now_iso(),
        source_verified,
        committee_target,
    ]
    if prominence is not None and _has_prominence_column(conn):
        columns.append("prominence")
        values.append(prominence)
    placeholders = ",".join("?" for _ in columns)
    try:
        conn.execute(
            f"INSERT INTO amjilsim_media_signals ({', '.join(columns)}) "
            f"VALUES ({placeholders})",
            values,
        )
    except sqlite3.IntegrityError:
        return False
    return True


def backfill_signals(
    db_path: Optional[PathLike] = None,
    kinds: tuple[str, ...] = DEFAULT_KINDS,
    limit: Optional[int] = None,
) -> dict:
    """competitor_news 를 스캔해 amjilsim_media_signals 로 백필. 통계 dict 반환."""
    path = str(db_path or DEFAULT_DB_PATH)
    index = build_alias_index(path)

    conn = _connect(path)
    try:
        # B7 — lexicon seed 보장 후 1회 로드 (행마다 재로드 방지).
        seed_lexicon(conn)
        lexicon = load_lexicon(path)
        unc_ok = unclassified_allowed(conn)
        # A1 — prominence 컬럼 멱등 보장.
        ensure_prominence_column(conn)

        drug_rows = conn.execute(
            "SELECT drug_id, brand_kr, expected_session_id, "
            + ("is_oncology" if _has_oncology_column(conn) else "NULL AS is_oncology")
            + " FROM amjilsim_drugs"
        ).fetchall()
        expected_session = {r["drug_id"]: r["expected_session_id"] for r in drug_rows}
        drug_names = {r["drug_id"]: r["brand_kr"] for r in drug_rows}
        drug_oncology = {r["drug_id"]: r["is_oncology"] for r in drug_rows}

        sessions_sorted = load_sessions_sorted(conn)

        placeholders = ",".join("?" for _ in kinds)
        query = (
            f"SELECT id, brand, kind, title, url, source_name, source_domain, tier, "
            f"description, pub_date FROM competitor_news WHERE kind IN ({placeholders}) ORDER BY id"
        )
        params: tuple = tuple(kinds)
        if limit:
            query += " LIMIT ?"
            params = params + (limit,)
        rows = conn.execute(query, params).fetchall()

        stats: dict = {
            "scanned": 0,
            "matched": 0,
            "inserted": 0,
            "unmatched": 0,
            "duplicate_skipped": 0,
            "by_signal_type": {},
            "by_drug": {},
            "by_prominence": {},
        }

        for row in rows:
            stats["scanned"] += 1
            title = row["title"] or ""
            snippet = row["description"] or ""
            kind = row["kind"] or ""

            # A1 — 매칭 텍스트는 title+snippet 만. competitor_news.brand (크롤 쿼리
            # 태그) 는 기사 표면 텍스트가 아니므로 제외 — 표면에 약 이름이 없는
            # 기사가 신호로 잡히던 오탐의 근원이었다.
            drug_id, prominence = resolve_drug_with_prominence(title, snippet, index)
            if drug_id is None:
                stats["unmatched"] += 1
                continue
            stats["matched"] += 1

            signal_type, phrases = classify_signal_type(
                title, snippet, kind, lexicon=lexicon, unclassified_ok=unc_ok
            )
            tier_letter = TIER_LETTER.get(row["tier"], "D")
            weight = signal_weight(tier_letter, signal_type)
            outlet = row["source_name"] or row["source_domain"] or "unknown"
            pub_date = row["pub_date"]

            session_id = expected_session.get(drug_id)
            if not session_id and pub_date:
                # A2 — track 에 맞는 위원회 세션만 최근접 배정.
                #   oncology → AMJILSIM, general → YAKPYUNGWI,
                #   is_oncology 미상(NULL) → committee-agnostic (구 동작 보존).
                committee = expected_committee(drug_oncology.get(drug_id))
                session_id = nearest_session_id(sessions_sorted, pub_date, committee_type=committee)

            # A1 — source_verified: 발췌에 약물 거명이 있으면 snippet_match,
            # 제목에만 있으면 headline_only.
            source_verified = (
                "snippet_match"
                if drug_in_text(drug_id, snippet, index)
                else "headline_only"
            )

            inserted = insert_signal(
                conn,
                drug_id=drug_id,
                session_id=session_id,
                tier=tier_letter,
                outlet=outlet,
                url=row["url"],
                title=title,
                published_at=pub_date,
                snippet=snippet,
                signal_type=signal_type,
                signal_phrases=phrases,
                crossref_count=0,
                weight=weight,
                source_verified=source_verified,
                prominence=prominence,
            )
            if not inserted:
                stats["duplicate_skipped"] += 1
                continue
            stats["inserted"] += 1
            stats["by_signal_type"][signal_type] = stats["by_signal_type"].get(signal_type, 0) + 1
            stats["by_drug"][drug_id] = stats["by_drug"].get(drug_id, 0) + 1
            stats["by_prominence"][prominence] = stats["by_prominence"].get(prominence, 0) + 1

        conn.commit()
    finally:
        conn.close()

    top_drugs = sorted(stats["by_drug"].items(), key=lambda kv: -kv[1])[:15]
    stats["by_drug_top"] = [
        {"drug_id": drug_id, "brand_kr": drug_names.get(drug_id), "count": count}
        for drug_id, count in top_drugs
    ]
    return stats


# ─────────────────────────────────────────────────────────────────────────────
# B6 — 항암/일반 플래그 (is_oncology) 스키마 + 백필 캐스케이드
# ─────────────────────────────────────────────────────────────────────────────
def _has_oncology_column(conn: sqlite3.Connection) -> bool:
    return any(
        r[1] == "is_oncology" for r in conn.execute("PRAGMA table_info(amjilsim_drugs)")
    )


def ensure_oncology_column(conn: sqlite3.Connection) -> None:
    """amjilsim_drugs.is_oncology INTEGER 멱등 추가."""
    if not _has_oncology_column(conn):
        conn.execute("ALTER TABLE amjilsim_drugs ADD COLUMN is_oncology INTEGER")


# indication 텍스트 항암 휴리스틱 (캐스케이드 ④) — 한글 키워드.
#   '골수섬유증'(myelofibrosis) 은 골수증식성 종양(WHO 혈액암) → 항암.
_CANCER_KEYWORDS = (
    "암", "종양", "림프종", "백혈병", "흑색종", "골수종", "육종", "모세포종",
    "선암", "암종", "암세포", "전이", "항암", "종양학", "골수섬유증",
)
# indication 에 등장하지만 항암이 아닌 오탐 방지 (예: '고암모니아', '암모니아').
_CANCER_FALSE_POSITIVE = ("암모니아",)

# 영문 약어 — 한글 '암' 이 없는 항암 적응증(예: 'EGFR+ NSCLC', 'r/r DLBCL·PMBCL')을
# 잡기 위한 대문자 매칭 세트. 보수적으로 종양학에서만 쓰이는 약어로 한정.
#   (65개 amjilsim_drugs 중 이 약어를 가진 일반약제 0건 — 프로드 sample 검증.)
_CANCER_KEYWORDS_EN = ("NSCLC", "SCLC", "DLBCL", "PMBCL", "EGFR")

# indication 텍스트가 비거나 sparse 해 키워드로 못 잡는 항암제 INN 오버라이드 (보수적).
#   프로드 sample 검증: amivantamab(리브리반트)·osimertinib(타그리소) 는 indication 이
#   'NSCLC'(영문)라 기존 한글 '암' 키워드에 안 걸렸다. anbalcabtagene(림카토, DLBCL CAR-T)도.
KNOWN_ONCOLOGY_INN = frozenset({
    "amivantamab",
    "osimertinib",
    "anbalcabtagene autoleucel",
})
KNOWN_ONCOLOGY_BRAND_PREFIX = ()  # 필요 시 브랜드 prefix 오버라이드 (현재 불필요)


def _indication_is_cancer(text: Optional[str]) -> bool:
    if not text:
        return False
    t = text
    for fp in _CANCER_FALSE_POSITIVE:
        t = t.replace(fp, "")
    if any(kw in t for kw in _CANCER_KEYWORDS):
        return True
    upper = t.upper()
    return any(kw in upper for kw in _CANCER_KEYWORDS_EN)


def _inn_is_oncology(inn: Optional[str]) -> bool:
    return bool(inn) and inn.strip().lower() in KNOWN_ONCOLOGY_INN


def backfill_oncology(db_path: Optional[PathLike] = None) -> dict:
    """amjilsim_drugs.is_oncology 를 우선순위 캐스케이드로 백필 (멱등, 삭제 없음).

    ① ATC L01/L02 → 1
    ② efficacy_group='항악성종양제' → 1
    ③ analog_reports.disease_category='항암' (brand_kr join) → 1
    ④ indication 암 키워드 휴리스틱(한글+영문 약어 NSCLC/DLBCL 등) → 1
    ⑤ KNOWN_ONCOLOGY_INN 오버라이드 (indication sparse/영문) → 1
    ⑥ 잔여 → 0 (수동 검토용 목록 반환)

    반환: {oncology, general, by_rule, manual_review:[{drug_id, brand_kr, indication}]}
    """
    path = str(db_path or DEFAULT_DB_PATH)
    conn = _connect(path)
    try:
        ensure_oncology_column(conn)

        onco_brands = {
            (r["brand_name"] or "").strip()
            for r in conn.execute(
                "SELECT DISTINCT brand_name FROM analog_reports WHERE disease_category='항암'"
            )
            if (r["brand_name"] or "").strip()
        }

        rows = conn.execute(
            "SELECT drug_id, brand_kr, ingredient_inn, atc, efficacy_group, indication "
            "FROM amjilsim_drugs"
        ).fetchall()

        by_rule = {"atc": 0, "efficacy_group": 0, "analog": 0,
                   "indication": 0, "inn_override": 0, "none": 0}
        manual_review: list[dict] = []
        onco = general = 0

        for r in rows:
            drug_id = r["drug_id"]
            atc = (r["atc"] or "").upper()
            eff = r["efficacy_group"] or ""
            brand = (r["brand_kr"] or "").strip()
            inn = r["ingredient_inn"]
            indication = r["indication"]

            flag = 0
            rule = "none"
            if atc.startswith("L01") or atc.startswith("L02"):
                flag, rule = 1, "atc"
            elif eff == "항악성종양제":
                flag, rule = 1, "efficacy_group"
            elif brand and any(brand == b or brand.startswith(b) or b.startswith(brand)
                               for b in onco_brands):
                flag, rule = 1, "analog"
            elif _indication_is_cancer(indication):
                flag, rule = 1, "indication"
            elif _inn_is_oncology(inn):
                # ⑤ indication 이 sparse/영문 → 알려진 항암 INN 오버라이드.
                flag, rule = 1, "inn_override"

            by_rule[rule] += 1
            if flag:
                onco += 1
            else:
                general += 1
                manual_review.append(
                    {"drug_id": drug_id, "brand_kr": brand,
                     "indication": (indication or "")[:60]}
                )

            conn.execute(
                "UPDATE amjilsim_drugs SET is_oncology=? WHERE drug_id=?", (flag, drug_id)
            )
        conn.commit()
    finally:
        conn.close()

    return {
        "total": len(rows),
        "oncology": onco,
        "general": general,
        "by_rule": by_rule,
        "manual_review": manual_review,
    }


# ─────────────────────────────────────────────────────────────────────────────
# A1 — prominence 재판정 (기존 행 UPDATE-only, 멱등)
# ─────────────────────────────────────────────────────────────────────────────
def backfill_prominence(db_path: Optional[PathLike] = None) -> dict:
    """기존 amjilsim_media_signals 전 행의 prominence 를 저장된 title/snippet 로
    재판정해 UPDATE (행 삭제/INSERT 없음 — 아카이브 보존, 재실행 멱등).

    행의 **자체 drug_id** 기준으로 판정한다 (alias 인덱스 재해석 금지 — 과거 brand
    태그 매칭으로 들어와 기사 표면에 약 이름이 없는 행은 'passing' 이 되어 momentum
    에서 제외된다). source_verified 도 snippet_match↔headline_only 를 실제 발췌
    거명 여부로 교정한다 (body_verified 는 건드리지 않음).

    CLI: python scheduler.py --backfill-prominence-now
    """
    path = str(db_path or DEFAULT_DB_PATH)
    index = build_alias_index(path)

    conn = _connect(path)
    try:
        ensure_prominence_column(conn)

        rows = conn.execute(
            "SELECT id, drug_id, title, snippet, prominence, source_verified "
            "FROM amjilsim_media_signals WHERE drug_id IS NOT NULL"
        ).fetchall()

        stats: dict = {
            "total": len(rows),
            "updated": 0,
            "source_verified_fixed": 0,
            "by_prominence": {},
        }
        for r in rows:
            prom = drug_prominence(r["drug_id"], r["title"] or "", r["snippet"] or "", index)
            stats["by_prominence"][prom] = stats["by_prominence"].get(prom, 0) + 1

            sv = r["source_verified"]
            if sv in ("snippet_match", "headline_only"):
                new_sv = (
                    "snippet_match"
                    if drug_in_text(r["drug_id"], r["snippet"] or "", index)
                    else "headline_only"
                )
            else:  # body_verified 등 상위 검증 상태는 보존
                new_sv = sv

            if prom != r["prominence"] or new_sv != sv:
                conn.execute(
                    "UPDATE amjilsim_media_signals SET prominence = ?, source_verified = ? "
                    "WHERE id = ?",
                    (prom, new_sv, r["id"]),
                )
                stats["updated"] += 1
                if new_sv != sv:
                    stats["source_verified_fixed"] += 1
        conn.commit()
    finally:
        conn.close()
    return stats


# ─────────────────────────────────────────────────────────────────────────────
# A2 — track 별 위원회 기준 세션 재배정 (기존 행 UPDATE-only, 멱등)
# ─────────────────────────────────────────────────────────────────────────────
def relink_sessions(db_path: Optional[PathLike] = None) -> dict:
    """기존 신호의 session_id 를 약제 track 에 맞는 위원회 세션으로 재배정.

    배정 규칙 (INSERT 경로와 동일 우선순위):
      ① 약제의 expected_session_id (큐레이션 값) — 있으면 그대로.
      ② published_at 이후(>=) 최근접 세션 — oncology→AMJILSIM, general→YAKPYUNGWI,
        미상(NULL)→committee-agnostic.
      ③ 해당 위원회 세션이 없으면 NULL (잘못된 위원회 세션에 남겨두지 않는다 —
        예: 마운자로(일반)가 암질심 세션에 잘못 붙어 있던 건을 해소).

    UPDATE-only, 재실행 멱등. CLI: python scheduler.py --relink-sessions-now
    """
    path = str(db_path or DEFAULT_DB_PATH)
    conn = _connect(path)
    try:
        has_onco = _has_oncology_column(conn)
        onco_col = "is_oncology" if has_onco else "NULL AS is_oncology"
        drug_rows = conn.execute(
            f"SELECT drug_id, expected_session_id, {onco_col} FROM amjilsim_drugs"
        ).fetchall()
        expected_session = {r["drug_id"]: r["expected_session_id"] for r in drug_rows}
        drug_oncology = {r["drug_id"]: r["is_oncology"] for r in drug_rows}
        sessions_sorted = load_sessions_sorted(conn)

        rows = conn.execute(
            "SELECT id, drug_id, session_id, published_at FROM amjilsim_media_signals "
            "WHERE drug_id IS NOT NULL"
        ).fetchall()

        stats: dict = {
            "total": len(rows),
            "changed": 0,
            "cleared": 0,
            "by_committee": {},
        }
        for r in rows:
            drug_id = r["drug_id"]
            committee = expected_committee(drug_oncology.get(drug_id))
            desired = expected_session.get(drug_id)
            if not desired and r["published_at"]:
                desired = nearest_session_id(
                    sessions_sorted, r["published_at"], committee_type=committee
                )
            desired = desired or None

            key = committee or "AGNOSTIC"
            stats["by_committee"][key] = stats["by_committee"].get(key, 0) + 1

            if desired != r["session_id"]:
                conn.execute(
                    "UPDATE amjilsim_media_signals SET session_id = ? WHERE id = ?",
                    (desired, r["id"]),
                )
                stats["changed"] += 1
                if desired is None:
                    stats["cleared"] += 1
        conn.commit()
    finally:
        conn.close()
    return stats


if __name__ == "__main__":
    result = backfill_signals()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
