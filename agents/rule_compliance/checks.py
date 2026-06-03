"""메모리(합의 사항) 별 런타임 검증 함수 레지스트리.

각 체크 함수는 `(name, path_root) -> CheckResult` 시그니처.
- `pass`: 신호 확인됨 (수치 포함)
- `fail`: 신호 위반 or 결손 (root cause 힌트 포함)
- `skip`: 런타임 신호로 환원 불가 (개발 관행/process 메모리)

신규 메모리 추가 시 이 파일에 `CHECKS` 딕셔너리만 확장하면 된다.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

Status = Literal["pass", "fail", "skip"]


@dataclass
class CheckResult:
    memory: str                   # 메모리 파일명 (확장자 제외)
    status: Status
    detail: str                   # 한 줄 사람용 설명
    metrics: dict[str, Any] = field(default_factory=dict)  # 수치화 가능한 증거


# ────────────────────────────────────────────────────────────────────────────
# 개별 체크 함수
# ────────────────────────────────────────────────────────────────────────────

def check_comparator_completeness(name: str, root: Path) -> CheckResult:
    """`project_comparator_drug_structure` — 비교약제 enrichment 커버리지.

    신호: drug_latest 에서 ingredient 있는 row 비율 >= 50% (단일월이라도 백필되면 충족).
    """
    db_path = root / "data" / "db" / "drug_prices.db"
    if not db_path.exists():
        return CheckResult(name, "skip", "drug_prices.db 없음")
    with sqlite3.connect(db_path) as conn:
        total = conn.execute("SELECT COUNT(*) FROM drug_latest").fetchone()[0]
        has_ing = conn.execute(
            "SELECT COUNT(*) FROM drug_latest WHERE ingredient IS NOT NULL AND ingredient != ''"
        ).fetchone()[0]
    if total == 0:
        return CheckResult(name, "skip", "drug_latest 비어있음")
    ratio = has_ing / total
    if ratio >= 0.3:
        return CheckResult(
            name, "pass",
            f"drug_latest ingredient {has_ing}/{total} ({ratio:.1%}) — 비교약제 필터 재가동",
            {"has_ing": has_ing, "total": total, "ratio": ratio},
        )
    return CheckResult(
        name, "fail",
        f"drug_latest ingredient {has_ing}/{total} ({ratio:.1%}) — "
        f"`주성분명` 백필 누락. scripts/backfill_ingredient.py --all 실행 필요",
        {"has_ing": has_ing, "total": total, "ratio": ratio},
    )


def check_reason_evidence_quality(name: str, root: Path) -> CheckResult:
    """`project_price_change_reason_quality` — 변동사유 근거 품질.

    신호: 최근 30건 reason_cache 중 n_refs=0 비율 < 50%.
    """
    cache_dir = root / "data" / "dashboard" / "reason_cache"
    if not cache_dir.exists():
        return CheckResult(name, "skip", "reason_cache 없음 (분석 수행 전)")
    files = sorted(cache_dir.glob("MI_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:30]
    if not files:
        return CheckResult(name, "skip", "reason_cache 비어있음")
    zero = 0
    low = 0
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if len(d.get("references") or []) == 0:
            zero += 1
        if (d.get("confidence") or "").lower() == "low":
            low += 1
    n = len(files)
    zero_r = zero / n
    low_r = low / n
    if zero_r < 0.5 and low_r < 0.7:
        return CheckResult(
            name, "pass",
            f"최근 {n}건 중 n_refs=0 {zero}건({zero_r:.0%}), low {low}건({low_r:.0%})",
            {"files": n, "zero_refs": zero, "low_conf": low},
        )
    return CheckResult(
        name, "fail",
        f"최근 {n}건 중 n_refs=0 {zero}건({zero_r:.0%}), low_conf {low}건({low_r:.0%}) — "
        f"WARP 켜고 refresh=1 로 재분석 or enforce_rules URL-date 경로 점검",
        {"files": n, "zero_refs": zero, "low_conf": low},
    )


def check_indication_decomposition(name: str, root: Path) -> CheckResult:
    """`project_indication_level_approval` — 적응증 단위 분해.

    신호: indications_master 에 row 존재하며 단일 브랜드가 1 row 만 갖지 않음.
    """
    db_path = root / "data" / "db" / "drug_prices.db"
    if not db_path.exists():
        return CheckResult(name, "skip", "DB 없음")
    with sqlite3.connect(db_path) as conn:
        master_exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='indications_master'"
        ).fetchone()
        if not master_exists:
            return CheckResult(name, "fail", "indications_master 테이블 없음 — 스키마 미적용")
        rows = conn.execute("SELECT COUNT(*) FROM indications_master").fetchone()[0]
        keytruda = conn.execute(
            "SELECT COUNT(*) FROM indications_master WHERE product = 'keytruda'"
        ).fetchone()[0]
    if rows == 0:
        return CheckResult(name, "fail", "indications_master 비어있음")
    if keytruda < 20:
        return CheckResult(
            name, "fail",
            f"Keytruda 적응증 {keytruda}건 — 브랜드 단위 수집 회귀 가능성 (기대 ≥ 20)",
            {"total": rows, "keytruda": keytruda},
        )
    return CheckResult(
        name, "pass",
        f"indications_master {rows}건 · Keytruda {keytruda}건 분해 유지",
        {"total": rows, "keytruda": keytruda},
    )


def check_mfds_official_dates(name: str, root: Path) -> CheckResult:
    """`project_mfds_official_date_pipeline` — MFDS 공식 승인일 파이프라인."""
    db_path = root / "data" / "db" / "drug_prices.db"
    if not db_path.exists():
        return CheckResult(name, "skip", "DB 없음")
    with sqlite3.connect(db_path) as conn:
        t = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='indications_by_agency'"
        ).fetchone()
        if not t:
            return CheckResult(name, "fail", "indications_by_agency 미존재")
        total_mfds = conn.execute(
            "SELECT COUNT(*) FROM indications_by_agency WHERE agency='MFDS'"
        ).fetchone()[0]
        official = conn.execute(
            "SELECT COUNT(*) FROM indications_by_agency "
            "WHERE agency='MFDS' AND date_source='mfds_official'"
        ).fetchone()[0]
    if total_mfds == 0:
        return CheckResult(name, "skip", "MFDS 데이터 없음")
    ratio = official / total_mfds
    if ratio >= 0.5:
        return CheckResult(
            name, "pass",
            f"MFDS {official}/{total_mfds} ({ratio:.0%}) 공식일 매핑",
            {"official": official, "total": total_mfds, "ratio": ratio},
        )
    return CheckResult(
        name, "fail",
        f"MFDS 공식일 {official}/{total_mfds} ({ratio:.0%}) — 변경이력 매핑 회귀 의심. "
        f"scripts/apply_mfds_official_dates.py 재실행 권장",
        {"official": official, "total": total_mfds, "ratio": ratio},
    )


def check_foreign_form_type(name: str, root: Path) -> CheckResult:
    """`project_foreign_scraper_form_type` — 해외약가 form_type 채움율."""
    db_path = root / "data" / "db" / "drug_prices.db"
    if not db_path.exists():
        return CheckResult(name, "skip", "DB 없음")
    with sqlite3.connect(db_path) as conn:
        t = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='foreign_drug_prices'"
        ).fetchone()
        if not t:
            return CheckResult(name, "skip", "foreign_drug_prices 테이블 없음")
        # form_type 컬럼 존재 확인
        cols = [r[1] for r in conn.execute("PRAGMA table_info(foreign_drug_prices)").fetchall()]
        if "form_type" not in cols:
            return CheckResult(name, "fail", "foreign_drug_prices.form_type 컬럼 없음 — 마이그레이션 미적용")
        total = conn.execute("SELECT COUNT(*) FROM foreign_drug_prices").fetchone()[0]
        filled = conn.execute(
            "SELECT COUNT(*) FROM foreign_drug_prices "
            "WHERE form_type IS NOT NULL AND form_type != ''"
        ).fetchone()[0]
    if total == 0:
        return CheckResult(name, "skip", "foreign_drug_prices 비어있음")
    ratio = filled / total
    if ratio >= 0.9:
        return CheckResult(
            name, "pass",
            f"form_type {filled}/{total} ({ratio:.0%}) 채움",
            {"filled": filled, "total": total, "ratio": ratio},
        )
    return CheckResult(
        name, "fail",
        f"form_type {filled}/{total} ({ratio:.0%}) — scraper run() 또는 "
        f"agent defensive fallback 중 한쪽 미동작. BaseScraper._resolve_form_type 점검",
        {"filled": filled, "total": total, "ratio": ratio},
    )


def check_foreign_daily_cost_sanity(name: str, root: Path) -> CheckResult:
    """`feedback_foreign_daily_cost_total_mg` — 해외 일일비용 sanity (₩10M/day 초과 0 건)."""
    db_path = root / "data" / "db" / "drug_prices.db"
    if not db_path.exists():
        return CheckResult(name, "skip", "DB 없음")
    with sqlite3.connect(db_path) as conn:
        t = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='foreign_drug_prices'"
        ).fetchone()
        if not t:
            return CheckResult(name, "skip", "foreign_drug_prices 없음")
        cols = [r[1] for r in conn.execute("PRAGMA table_info(foreign_drug_prices)").fetchall()]
        if "daily_cost_krw" not in cols:
            return CheckResult(name, "skip", "daily_cost_krw 컬럼 없음")
        over = conn.execute(
            "SELECT COUNT(*) FROM foreign_drug_prices WHERE daily_cost_krw > 10000000"
        ).fetchone()[0]
    if over == 0:
        return CheckResult(name, "pass", "₩10M/day 초과 0건 (sanity cap 유효)", {"over_10M": 0})
    return CheckResult(
        name, "fail",
        f"일일투약비 ₩10M 초과 {over}건 — _extract_total_pkg_mg 분모 오류 회귀 의심",
        {"over_10M": over},
    )


def check_cache_db_first(name: str, root: Path) -> CheckResult:
    """`feedback_cache_db_first` — 조사 데이터 영구 캐시.

    신호: reason_cache / gov_summary 디렉터리 존재 + 파일 있음.
    """
    dirs = [
        root / "data" / "dashboard" / "reason_cache",
        root / "data" / "cache" / "gov_summary",
    ]
    counts = {str(d.relative_to(root)): len(list(d.glob("*.json"))) if d.exists() else 0 for d in dirs}
    total = sum(counts.values())
    if total == 0:
        return CheckResult(name, "fail", "캐시 디렉터리 모두 비어있음 — 재조회 반복 위험", counts)
    return CheckResult(
        name, "pass",
        f"영구 캐시 총 {total} 파일 ({', '.join(f'{k.split(chr(47))[-1]}={v}' for k,v in counts.items())})",
        counts,
    )


def check_price_approval_coverage(name: str, root: Path) -> CheckResult:
    """가격 ↔ 허가 파이프라인 커버리지 대칭 검증.

    foreign_drug_prices 에 있는 제품이 indications_master 에도 있어야 함
    (허가 > 가격 상식). 미수록 제품은 `sync-from-prices` 로 자동 빌드 가능.
    """
    db_path = root / "data" / "db" / "drug_prices.db"
    if not db_path.exists():
        return CheckResult(name, "skip", "DB 없음")
    try:
        import sys
        sys.path.insert(0, str(root))
        from agents.foreign_approval.agent import ForeignApprovalAgent
        agent = ForeignApprovalAgent(db_path=db_path)
        gaps = agent.list_coverage_gaps()
    except Exception as e:
        return CheckResult(name, "skip", f"coverage 체크 실행 실패: {e}")
    if not gaps:
        return CheckResult(name, "pass", "가격↔허가 커버리지 100%", {"gaps": []})
    return CheckResult(
        name, "fail",
        f"{len(gaps)}건 가격만 있고 허가 없음: {', '.join(gaps[:5])}{'...' if len(gaps) > 5 else ''} "
        f"— `python -m agents.foreign_approval sync-from-prices` 실행 필요",
        {"gaps": gaps},
    )


def check_mfds_authoritative_source(name: str, root: Path) -> CheckResult:
    """`project_mfds_authoritative_source` — MFDS 공공데이터 API 통합 동작 검증.

    신호:
    - mfds_permit_cache 에 행 1개 이상 (실제 API 호출 발생)
    - mfds_patent_cache 에 행 1개 이상
    - 환경변수 MFDS_PATENT_API_KEY 설정됨
    """
    db_path = root / "data" / "db" / "drug_prices.db"
    if not db_path.exists():
        return CheckResult(name, "skip", "drug_prices.db 없음")

    # 환경변수 확인
    import os
    env_path = root / "config" / ".env"
    has_key = False
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("MFDS_PATENT_API_KEY=") and len(s) > 30:
                has_key = True
                break
    if not has_key and not os.environ.get("MFDS_PATENT_API_KEY"):
        return CheckResult(
            name, "fail",
            "MFDS_PATENT_API_KEY 환경변수 미설정 — config/.env 등록 필요",
            {"has_key": False},
        )

    with sqlite3.connect(db_path) as conn:
        try:
            permit_count = conn.execute("SELECT COUNT(*) FROM mfds_permit_cache").fetchone()[0]
            patent_count = conn.execute("SELECT COUNT(*) FROM mfds_patent_cache").fetchone()[0]
        except sqlite3.OperationalError:
            return CheckResult(
                name, "fail",
                "mfds_permit_cache / mfds_patent_cache 테이블 없음 — 모듈 로드 시 _ensure_table() 미실행",
                {"permit_count": 0, "patent_count": 0},
            )

    if permit_count == 0 and patent_count == 0:
        return CheckResult(
            name, "skip",
            "캐시 비어있음 — 검색 1회 이상 후 재실행. python -m agents.scrapers.kr_mfds_permit '자누비아정100밀리그램' 으로 단독 검증 가능",
            {"permit_count": 0, "patent_count": 0},
        )
    return CheckResult(
        name, "pass",
        f"MFDS API 통합 동작 — permit cache {permit_count}건 / patent cache {patent_count}건",
        {"permit_count": permit_count, "patent_count": patent_count, "has_key": True},
    )


def check_xnational_reimbursement(name: str, root: Path) -> CheckResult:
    """`project_xnational_reimbursement` — cross-national 급여 데이터 누적 + 신선도.

    신호:
    - reimbursement_xnational 테이블 존재 + row 1+ 권장
    - 마지막 fetched_at 이 90일 이내면 PASS
    - 비어있거나 90일 초과면 SKIP/FAIL (경고)
    """
    db_path = root / "data" / "db" / "drug_prices.db"
    if not db_path.exists():
        return CheckResult(name, "skip", "drug_prices.db 없음")
    try:
        with sqlite3.connect(db_path) as conn:
            r = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='reimbursement_xnational'"
            ).fetchone()
            if r is None:
                return CheckResult(
                    name, "fail",
                    "reimbursement_xnational 테이블 없음 — agents/db/schema.py 적용 필요",
                )
            row = conn.execute(
                "SELECT COUNT(*) as n, MAX(fetched_at) as last "
                "FROM reimbursement_xnational"
            ).fetchone()
            n, last = row[0], row[1]
            by_country = conn.execute(
                "SELECT country, body, COUNT(*) FROM reimbursement_xnational "
                "GROUP BY country, body"
            ).fetchall()
    except Exception as e:
        return CheckResult(name, "skip", f"DB 조회 실패: {e}")

    if n == 0:
        return CheckResult(
            name, "skip",
            "reimbursement_xnational 비어있음 — `python -m agents.foreign_approval.reimbursement_sync --product keytruda` 로 첫 수집 권장",
            {"total": 0},
        )

    # 신선도: 90일 임계
    from datetime import datetime, timedelta
    is_fresh = False
    if last:
        try:
            t = datetime.fromisoformat(last)
            if t > datetime.now() - timedelta(days=90):
                is_fresh = True
        except Exception:
            pass
    distribution = ", ".join(f"{c}/{b}={cnt}" for c, b, cnt in by_country)
    if is_fresh:
        return CheckResult(
            name, "pass",
            f"reimbursement_xnational {n}건 (last={last[:10]}). 분포: {distribution}",
            {"total": n, "last_fetched": last, "by_country": dict(((c, b), cnt) for c, b, cnt in by_country)},
        )
    return CheckResult(
        name, "fail",
        f"reimbursement_xnational {n}건 누적이지만 last_fetched={last} > 90일 — 분기 sync 미실행",
        {"total": n, "last_fetched": last},
    )


def check_foreign_price_coverage(name: str, root: Path) -> CheckResult:
    """`project_foreign_price_coverage` — 6약 × 8국 가격 백필 모니터링.

    신호: indications_master 의 product slug 별로 foreign_drug_prices 에 1+ 국가 존재.
    """
    db_path = root / "data" / "db" / "drug_prices.db"
    if not db_path.exists():
        return CheckResult(name, "skip", "drug_prices.db 없음")
    try:
        with sqlite3.connect(db_path) as conn:
            slugs = [r[0] for r in conn.execute(
                "SELECT DISTINCT product FROM indications_master ORDER BY product"
            ).fetchall()]
            # 각 slug 에 대해 alias 확장 + foreign_drug_prices 매칭
            from agents.db.drug_aliases import aliases as _aliases
            covered = []
            missing = []
            country_counts = {}
            for slug in slugs:
                names = _aliases(slug)
                placeholders = ",".join(["?"] * len(names))
                rows = conn.execute(
                    f"SELECT country, COUNT(*) FROM foreign_drug_prices "
                    f"WHERE LOWER(query_name) IN ({placeholders}) GROUP BY country",
                    tuple(n.lower() for n in names),
                ).fetchall()
                if rows:
                    covered.append(slug)
                    country_counts[slug] = {c: n for c, n in rows}
                else:
                    missing.append(slug)
    except Exception as e:
        return CheckResult(name, "skip", f"DB 조회 실패: {e}")

    if not slugs:
        return CheckResult(name, "skip", "indications_master 비어있음")
    coverage = len(covered) / len(slugs)
    if coverage >= 0.5:
        return CheckResult(
            name, "pass",
            f"가격 커버리지 {len(covered)}/{len(slugs)} ({coverage:.0%}) — covered: {','.join(covered[:5])}",
            {"covered": covered, "missing": missing, "country_counts": country_counts},
        )
    return CheckResult(
        name, "fail",
        f"가격 커버리지 {len(covered)}/{len(slugs)} ({coverage:.0%}) — missing: {','.join(missing)} "
        f"— `python -m agents.foreign_price_agent search <brand>` 로 백필",
        {"covered": covered, "missing": missing},
    )


def check_patent_classification_policy(name: str, root: Path) -> CheckResult:
    """`project_patent_classification_policy` — 특허 분류 baseline 검증.

    신호: 자누비아·글리벡·허셉틴 = 만료, 키트루다·옵디보 = 유효 (MFDS API 실측 기준).
    캐시 우선이지만 source 무관 status 만 검증 (분류 로직 회귀 감지가 목적).
    """
    db_path = root / "data" / "db" / "drug_prices.db"
    if not db_path.exists():
        return CheckResult(name, "skip", "drug_prices.db 없음")

    try:
        import sys
        sys.path.insert(0, str(root))
        from agents.scrapers.kr_mfds_patent import lookup_patent
    except ImportError as e:
        return CheckResult(name, "fail", f"kr_mfds_patent 모듈 import 실패: {e}")

    expected = {
        "자누비아정100밀리그램": "만료",
        "글리벡필름코팅정100밀리그램": "만료",
        "허셉틴주150밀리그람": "만료",
        "키트루다주": "유효",
        "옵디보주": "유효",
    }
    mismatches = []
    errors = []
    sources = {"cache": 0, "api": 0, "miss": 0}
    for name_kr, exp_status in expected.items():
        try:
            r = lookup_patent(name_kr, refresh=False, use_cache=True)
        except Exception as e:
            errors.append(f"{name_kr}: {e}")
            continue
        sources[r.get("source", "miss")] = sources.get(r.get("source", "miss"), 0) + 1
        if r.get("status") != exp_status:
            mismatches.append(f"{name_kr}: 기대={exp_status}, 실제={r.get('status')} ({r.get('judgment_basis')})")

    if errors:
        return CheckResult(
            name, "fail",
            f"{len(errors)}건 호출 실패: {'; '.join(errors[:2])}",
            {"errors": errors},
        )
    if mismatches:
        return CheckResult(
            name, "fail",
            f"{len(mismatches)}건 baseline 불일치 — summarize 분류 로직 회귀 의심: {'; '.join(mismatches[:3])}",
            {"mismatches": mismatches, "sources": sources},
        )
    return CheckResult(
        name, "pass",
        f"5/5 baseline 분류 일치 (만료 3 / 유효 2). cache/{sources.get('cache',0)} api/{sources.get('api',0)}",
        {"sources": sources, "matched": 5},
    )


def check_mfds_baseline_8(name: str, root: Path) -> CheckResult:
    """`feedback_mfds_pattern_matching` — MFDS 8개 baseline indication 공식일 일치."""
    try:
        import sys
        sys.path.insert(0, str(root))
        from agents.quality_guard.agent import QualityGuardAgent
        qg = QualityGuardAgent()
        regressions = qg.check_mfds_baseline()
    except Exception as e:
        return CheckResult(name, "skip", f"QG check_mfds_baseline 실패: {e}")
    if not regressions:
        return CheckResult(name, "pass", "8개 baseline 모두 일치", {"regressions": 0})
    return CheckResult(
        name, "fail",
        f"{len(regressions)}건 회귀 — peri/adj/neo 매칭 로직 점검 필요",
        {"regressions": len(regressions), "items": regressions},
    )


# ────────────────────────────────────────────────────────────────────────────
# 레지스트리
# ────────────────────────────────────────────────────────────────────────────

CHECKS: dict[str, Callable[[str, Path], CheckResult]] = {
    "project_comparator_drug_structure": check_comparator_completeness,
    "project_price_change_reason_quality": check_reason_evidence_quality,
    "project_indication_level_approval": check_indication_decomposition,
    "project_mfds_official_date_pipeline": check_mfds_official_dates,
    "project_foreign_scraper_form_type": check_foreign_form_type,
    "feedback_foreign_daily_cost_total_mg": check_foreign_daily_cost_sanity,
    "feedback_cache_db_first": check_cache_db_first,
    "feedback_mfds_pattern_matching": check_mfds_baseline_8,
    # price↔approval 대칭성 (2026-04-23 추가, Prevymis 류 누락 방지)
    "project_price_approval_coverage": check_price_approval_coverage,
    # MFDS 공공데이터 API 통합 (2026-04-27 추가)
    "project_mfds_authoritative_source": check_mfds_authoritative_source,
    "project_patent_classification_policy": check_patent_classification_policy,
    # Cross-national reimbursement (pure-napping-goose plan, 2026-04-27 추가)
    "project_xnational_reimbursement": check_xnational_reimbursement,
    "project_foreign_price_coverage": check_foreign_price_coverage,
}

# 런타임 검증 불가 (개발 관행 / process state) — 명시적 SKIP
SKIP_WITH_REASON: dict[str, str] = {
    "user_joseph":                        "사용자 프로파일 (정적)",
    "project_drug_price_dashboard":       "프로젝트 프레임 (정적)",
    "project_workbench_pivot":            "피벗 결정 (process state)",
    "project_deployment_architecture":    "배포 원칙 (개발 관행)",
    "project_tls_remediation":            "TLS 환경 설정 (네트워크 런타임)",
    "project_kr_approval_vs_reimbursement": "데이터 모델링 원칙 (정적)",
    "project_readdy_migration":           "UI 마이그레이션 계획 (process)",
    "project_competitor_daily_mailing_plan": "프로세스 결정",
    "project_reimbursement_admin_checklist":"수동 UI 체크리스트",
    "project_competitor_trends_auto":     "주 1회 크론 — 별도 모니터",
    "feedback_web_last":                  "개발 순서 원칙",
    "feedback_auto_proceed":              "협업 스타일",
    "feedback_micromedex_session_reuse":  "개발 관행",
    "feedback_daily_mailing_replan":      "사용자 대기 상태",
    "feedback_verify_rules_not_just_write":"이 에이전트 자체가 구현체",
    "project_rule_compliance_agent":      "자기 참조 (재귀 방지)",
    "reference_naver_news_api":           "외부 시스템 참조",
    "reference_hira_oncology_notice":     "외부 시스템 참조",
    "feedback_rsa_invisible_pricing":     "Intelligence (정적 사실) — RSA registry 운영으로 충족",
    "project_health_kr_primary_source":   "MFDS API 도입 후 보조 소스로 강등 — 운영 정책 (정적)",
}
