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
from .link import DEFAULT_DB_PATH, build_alias_index, resolve_drug

PathLike = Union[str, Path]

# competitor_news.tier(INTEGER, 1/2/3) → amjilsim_media_signals.tier(TEXT, A/B/D).
# 1=Tier1 전문지, 2=Tier2 종합·경제·통신, 3=미분류 도메인(사실상 미등록) → 'D'.
TIER_LETTER = {1: "A", 2: "B", 3: "D"}
_TIER_LETTER = TIER_LETTER  # 하위호환 별칭

DEFAULT_KINDS: tuple[str, ...] = ("competitor", "gov_policy", "msd_asset")

# B5 — 약제 유형별 예상(진입) 위원회.
#   항암제 → 암질심(AMJILSIM), 비항암제 → 급여기준소위(BENEFIT_SUBCOMMITTEE).
#   약평위(YAKPYUNGWI)는 두 경로 공통의 후속 단계이므로 pre-committee 로는 쓰지 않는다.
COMMITTEE_AMJILSIM = "AMJILSIM"
COMMITTEE_YAKPYUNGWI = "YAKPYUNGWI"
COMMITTEE_BENEFIT_SUB = "BENEFIT_SUBCOMMITTEE"


def expected_committee(is_oncology: Optional[int]) -> Optional[str]:
    """약제 is_oncology 플래그 → 예상 진입 위원회 라벨.

    - 1(항암)          → AMJILSIM (암질심/DREC)
    - 0(비항암)         → BENEFIT_SUBCOMMITTEE (급여기준소위/BSC)
    - None(미상, 백필 전) → None — 위원회 배정을 committee-agnostic 으로 두어 백필 전
      항암제(예: Keytruda)를 BSC 로 오단정하지 않는다.
    """
    if is_oncology == 1:
        return COMMITTEE_AMJILSIM
    if is_oncology == 0:
        return COMMITTEE_BENEFIT_SUB
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
) -> bool:
    """amjilsim_media_signals 에 1행 INSERT. (url, drug_id) 멱등 — 이미 있으면 False.

    커밋은 호출자 책임. 기존 행은 절대 UPDATE/DELETE 하지 않는다 (INSERT-only).
    스키마의 UNIQUE(outlet, url) 충돌(예: alias 인덱스 변경으로 같은 기사가 다른
    drug 으로 재해석된 경우)도 duplicate 로 흡수해 기존 행을 보호한다.
    """
    existing = conn.execute(
        "SELECT 1 FROM amjilsim_media_signals WHERE url = ? AND drug_id = ? LIMIT 1",
        (url, drug_id),
    ).fetchone()
    if existing:
        return False
    try:
        conn.execute(
            """
            INSERT INTO amjilsim_media_signals (
                drug_id, session_id, tier, outlet, url, title, published_at,
                snippet, signal_type, signal_phrases, crossref_count, weight,
                crawled_at, source_verified, committee_target
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
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
            ),
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
        }

        for row in rows:
            stats["scanned"] += 1
            title = row["title"] or ""
            snippet = row["description"] or ""
            brand = row["brand"] or ""
            kind = row["kind"] or ""
            text = f"{title} {snippet} {brand}"

            drug_id = resolve_drug(text, index)
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
                # B5 — 항암/비항암에 맞는 위원회 세션만 최근접 배정.
                #   is_oncology 미상(NULL) → expected_committee=None → committee-agnostic
                #   (구 동작 보존); 명시적 비항암(0) → BSC → 암질심 강제 배정 금지.
                committee = expected_committee(drug_oncology.get(drug_id))
                session_id = nearest_session_id(sessions_sorted, pub_date, committee_type=committee)

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
                source_verified="snippet_match",
            )
            if not inserted:
                stats["duplicate_skipped"] += 1
                continue
            stats["inserted"] += 1
            stats["by_signal_type"][signal_type] = stats["by_signal_type"].get(signal_type, 0) + 1
            stats["by_drug"][drug_id] = stats["by_drug"].get(drug_id, 0) + 1

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


# indication 텍스트 항암 휴리스틱 (캐스케이드 ④).
_CANCER_KEYWORDS = (
    "암", "종양", "림프종", "백혈병", "흑색종", "골수종", "육종", "모세포종",
    "선암", "암종", "암세포", "전이", "항암", "종양학",
)
# indication 에 등장하지만 항암이 아닌 오탐 방지 (예: '고암모니아', '암모니아').
_CANCER_FALSE_POSITIVE = ("암모니아",)


def _indication_is_cancer(text: Optional[str]) -> bool:
    if not text:
        return False
    t = text
    for fp in _CANCER_FALSE_POSITIVE:
        t = t.replace(fp, "")
    return any(kw in t for kw in _CANCER_KEYWORDS)


def backfill_oncology(db_path: Optional[PathLike] = None) -> dict:
    """amjilsim_drugs.is_oncology 를 우선순위 캐스케이드로 백필 (멱등, 삭제 없음).

    ① ATC L01/L02 → 1
    ② efficacy_group='항악성종양제' → 1
    ③ analog_reports.disease_category='항암' (brand_kr join) → 1
    ④ indication 암 키워드 휴리스틱 → 1
    ⑤ 잔여 → 0 (수동 검토용 목록 반환)

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
            "SELECT drug_id, brand_kr, atc, efficacy_group, indication FROM amjilsim_drugs"
        ).fetchall()

        by_rule = {"atc": 0, "efficacy_group": 0, "analog": 0, "indication": 0, "none": 0}
        manual_review: list[dict] = []
        onco = general = 0

        for r in rows:
            drug_id = r["drug_id"]
            atc = (r["atc"] or "").upper()
            eff = r["efficacy_group"] or ""
            brand = (r["brand_kr"] or "").strip()
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


if __name__ == "__main__":
    result = backfill_signals()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
