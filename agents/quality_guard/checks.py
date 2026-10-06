"""무상태 검증 함수 — 스크레이퍼 출력 / DB 레코드 / Keytruda validation / 코드 패턴."""
from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path

from .deviations import _write_deviation

logger = logging.getLogger(__name__)

REQUIRED_SCRAPER_KEYS = {
    "product_name", "ingredient", "dosage_strength",
    "dosage_form", "package_unit", "local_price",
    "source_url", "extra",
}

REQUIRED_DB_KEYS = {
    "searched_at", "query_name", "country", "product_name",
    "local_price", "currency", "source_url", "source_label",
}

VALID_COUNTRIES = {"JP", "IT", "FR", "CH", "UK", "DE", "US", "CA"}
VALID_CURRENCIES = {"JPY", "EUR", "CHF", "GBP", "USD", "CAD"}


def check_scraper_output(
    results: list[dict],
    country: str,
    scraper_file: str = "",
) -> list[dict]:
    """스크레이퍼 반환값 검증. 문제 발견 시 deviation_log 기록 + 자동 수정."""
    issues = []

    for i, item in enumerate(results):
        missing = REQUIRED_SCRAPER_KEYS - set(item.keys())
        if missing:
            issues.append({
                "severity": "ERROR",
                "agent": "Scraper",
                "file": scraper_file,
                "deviation_type": "missing_required_keys",
                "description": f"{country} 스크레이퍼 결과 #{i}: 필수 키 누락 {missing}",
                "expected": str(REQUIRED_SCRAPER_KEYS),
                "actual": str(set(item.keys())),
                "corrective_action": "스크레이퍼 반환 형식 수정 필요",
            })

        price = item.get("local_price")
        if price is not None:
            if not isinstance(price, (int, float)):
                issues.append({
                    "severity": "ERROR",
                    "agent": "Scraper",
                    "file": scraper_file,
                    "deviation_type": "invalid_price_type",
                    "description": f"{country} 가격이 숫자가 아님: {type(price).__name__}='{price}'",
                    "expected": "float 또는 None",
                    "actual": f"{type(price).__name__}: {price}",
                    "corrective_action": "가격 파싱 로직 수정",
                })
            elif price <= 0:
                issues.append({
                    "severity": "WARNING",
                    "agent": "Scraper",
                    "file": scraper_file,
                    "deviation_type": "zero_or_negative_price",
                    "description": f"{country} 가격이 0 이하: {price}",
                    "expected": "양수 float",
                    "actual": str(price),
                    "corrective_action": "None으로 대체 권장",
                })
                results[i]["local_price"] = None

        if not item.get("dosage_strength", "").strip():
            issues.append({
                "severity": "WARNING",
                "agent": "Scraper",
                "file": scraper_file,
                "deviation_type": "missing_dosage_strength",
                "description": f"{country} 결과 #{i}: dosage_strength 빈 값 (product={item.get('product_name','')})",
                "expected": "용량 포함 문자열",
                "actual": "빈 문자열 또는 None",
                "corrective_action": "파싱 로직에서 용량 추출 보완 필요",
            })

        if not item.get("source_url", "").strip():
            issues.append({
                "severity": "WARNING",
                "agent": "Scraper",
                "file": scraper_file,
                "deviation_type": "missing_source_url",
                "description": f"{country} 결과 #{i}: source_url 없음",
                "expected": "실제 접근 URL",
                "actual": "빈 문자열",
                "corrective_action": "스크레이퍼에서 URL 포함 확인",
            })

    for issue in issues:
        _write_deviation(issue)

    return results


def check_foreign_price_data(db_path: Path | str) -> list[dict]:
    """저장된 해외약가(foreign_drug_prices) **데이터** 검사 — 매일 06:00 `review_codebase` 가 호출한다.

    코드 패턴 grep(`_check_foreign_minimum_unit`) 은 간접 fallback 을 못 잡았다 — 2026-10 감사에서 FR Vidal
    "25 mg"(per-mL 농도) 가 분모로 들어가 daily_cost 4× 였는데 QG 는 통과시켰다. 그래서 값 자체를 본다.

    항목:
      - injection_concentration_as_unit: priced injection 행의 unit_strength_mg 가 농도(N mg/ml 의 N)와 같거나
        form-aware 재계산과 다르거나, per-vial mg 가 불명인데 daily_cost_krw 가 있다 → ERROR
      - invalid_country_code / invalid_currency_code: 저장 행의 코드가 ISO 집합 밖 → ERROR (구 check_db_records 승계)

    반환 항목은 record_quality_review 가 이해하는 drift 형태 {rule, file, severity} 에 check/country/row_id/detail 을
    더한 것 (file 의 숫자는 내용 키에서 지워지므로 같은 (국가, 약제) 는 원장에 1건). DB 는 read-only 로 연다.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as e:
        logger.warning("[QualityGuard] 해외약가 데이터 검사 — DB 열기 실패: %s", e)
        return []
    try:
        conn.row_factory = sqlite3.Row
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='foreign_drug_prices'"
        ).fetchone():
            return []
        rows = conn.execute(
            "SELECT id, query_name, country, currency, product_name, dosage_strength, dosage_form, "
            "package_unit, form_type, unit_strength_mg, total_pkg_mg, daily_cost_krw, dose_norm_factor "
            "FROM foreign_drug_prices WHERE local_price IS NOT NULL ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return []

    from agents.foreign_price_agent import ForeignPriceAgent   # lazy — 스크레이퍼 import 비용

    findings: list[dict] = []

    def _add(check: str, rule: str, r, detail: str, severity: str = "ERROR") -> None:
        f = {
            "kind": "data", "check": check, "severity": severity, "rule": rule,
            "file": f"foreign_drug_prices id={r['id']} — {r['country']} {r['query_name']}",
            "country": r["country"], "row_id": r["id"], "detail": detail,
        }
        findings.append(f)
        _write_deviation({
            "severity": severity, "agent": "QualityGuard", "file": f["file"],
            "deviation_type": check, "description": f"{rule} → {f['file']}: {detail}",
            "corrective_action": "해당 행 재수집(스크레이퍼가 volume/포장문 포함) 또는 값 정정 — "
                                 "foreign_agent_rules.md §최소단위",
        })

    for r in rows:
        country = (r["country"] or "").upper()
        currency = (r["currency"] or "").upper()
        if country not in VALID_COUNTRIES:
            _add("invalid_country_code", "scraper_rules.md — country 는 ISO 2자리 코드", r,
                 f"country={r['country']!r}")
        if currency not in VALID_CURRENCIES:
            _add("invalid_currency_code", "scraper_rules.md — currency 는 ISO 3자리 코드", r,
                 f"currency={r['currency']!r}")
        if (r["form_type"] or "").lower() != "injection":
            continue
        ds, pu = r["dosage_strength"] or "", r["package_unit"] or ""
        pn, df = r["product_name"] or "", r["dosage_form"] or ""
        derived = ForeignPriceAgent._extract_per_unit_mg("injection", ds, pu, product_name=pn, dosage_form=df)
        conc = None
        for text in (ds, df, pn):
            m = ForeignPriceAgent._MG_PER_ML.search(text.translate(ForeignPriceAgent._ZENKAKU))
            if m:
                conc = float(m.group(1))
                break
        stored = r["unit_strength_mg"]
        reasons = []
        if stored is not None and conc is not None and abs(float(stored) - conc) < 1e-6:
            reasons.append(f"unit_strength_mg={stored} 가 농도 {conc} mg/mL 와 같다")
        if stored is not None and derived is not None and abs(float(stored) - derived) > 0.01:
            reasons.append(f"unit_strength_mg={stored} ≠ form-aware per-vial {derived}")
        if derived is None and r["daily_cost_krw"] is not None:
            reasons.append(f"per-vial mg 불명인데 daily_cost_krw={r['daily_cost_krw']} (total_pkg_mg={r['total_pkg_mg']})")
        if reasons:
            _add("injection_concentration_as_unit",
                 "foreign_agent_rules.md §최소단위 — injection unit_mg 는 per-vial 총량 (per-mL 농도 분모 금지)", r,
                 "; ".join(reasons) + f" [dosage_strength={ds!r} package_unit={pu!r} product_name={pn[:60]!r}"
                 f" dose_norm_factor={r['dose_norm_factor']}]")
    return findings


def summarize_unverified_pack_rows(db_path: Path | str) -> list[str]:
    """pack_count 미확인(default) 으로 A8 집계에서 빠져 있는 pack 국가 행 요약 — review_codebase 제안(🧮) 1줄.

    foreign_agent_rules.md §pack_count: pack-pricing 국가 oral/unknown 행이 수량 단서도 dosing 도 없으면 pack_count=1
    (default) 이고, 그 숫자는 팩가가 단위가로 보이는 것이라 UI 요약·리포트가 평균에서 뺀다. 그 행들이 어느 국가·약제에
    몇 건인지 매일 보이게 한다 (재수집/스크레이퍼 보강 대상). 저장 행이 아니라 현재 resolver 로 다시 판정한다
    (저장 pack_count_source 는 레거시 NULL 일 수 있다). DB 는 read-only.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    try:
        conn.row_factory = sqlite3.Row
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='foreign_drug_prices'"
        ).fetchone():
            return []
        cols = {r[1] for r in conn.execute("PRAGMA table_info(foreign_drug_prices)")}
        # 운영 DB 가 아직 pack_count_source 마이그레이션 전이면 컬럼 없이 읽는다(레거시 = NULL 과 같은 의미)
        src_col = "pack_count_source" if "pack_count_source" in cols else "NULL AS pack_count_source"
        rows = [dict(r) for r in conn.execute(
            "SELECT id, query_name, country, product_name, dosage_strength, dosage_form, package_unit, form_type, "
            f"pack_count, {src_col} FROM foreign_drug_prices WHERE local_price IS NOT NULL"
        ).fetchall()]
        dosing = {r["ingredient_key"]: dict(r) for r in conn.execute(
            "SELECT ingredient_key, default_pack_count FROM foreign_drug_dosing"
        ).fetchall()} if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='foreign_drug_dosing'"
        ).fetchone() else {}
    finally:
        conn.close()
    if not rows:
        return []

    from collections import Counter

    from agents.foreign_price_agent import (
        PACK_PRICING_COUNTRIES, ForeignPriceAgent, a8_aggregate_eligibility,
    )
    agent = ForeignPriceAgent.__new__(ForeignPriceAgent)
    counts: Counter = Counter()
    for r in rows:
        if r["country"] not in PACK_PRICING_COUNTRIES:
            continue
        d = dosing.get((r.get("query_name") or "").lower())
        agent._resolve_pack_count(r["country"], r, d)
        ok, reason = a8_aggregate_eligibility(r["country"], {**r, "adjusted_price_krw": 1})
        if not ok and reason == "pack_count_unverified":
            counts[(r["country"], r["query_name"])] += 1
    if not counts:
        return []
    total = sum(counts.values())
    parts = " · ".join(f"{c} {q} {n}" for (c, q), n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
    return [
        f"🧮 해외약가 pack_count 미확인(default) 행 {total}건 — {parts}: 팩가가 단위가로 저장된 행이라 A8 평균에서 "
        f"제외 중. 스크레이퍼가 포장수량(CH Packungen 'N Stk', DE Packungsangaben 'N St')을 가져오도록 보강 후 재수집 "
        f"(foreign_agent_rules.md §pack_count)"
    ]


# ──────────────────────────────────────────────────────────────────────────
# 해외 약가 최소단위 원칙 회귀 체커 (2026-04-22 추가)
# foreign_agent_rules.md §최소단위(minimum unit) 원칙, CLAUDE.md 절대 금지
# ──────────────────────────────────────────────────────────────────────────

_AWP_DIRECT_ASSIGN = re.compile(r"local_price\s*=\s*awp_\w+", re.IGNORECASE)
_RATIO_PACK_COUNT  = re.compile(r"total_mg\s*/\s*unit_mg|total_pkg_mg\s*/\s*unit_mg")
_UNIT_MG_EXTRACT   = re.compile(r"unit_mg\s*=\s*[\w\.]*_extract_mg\s*\(")


def _check_foreign_minimum_unit(file_path: Path, code: str, issues: list[str]) -> None:
    """form_type 기반 최소단위 원칙 회귀 탐지 — agents/ 하위 전체에서 스캔."""
    rel_name = file_path.name

    # (a) US Micromedex: local_price = awp_* 직접 대입 — WAC 우선 없으면 double-count.
    if rel_name == "us_micromedex.py":
        for m in _AWP_DIRECT_ASSIGN.finditer(code):
            snippet = code[max(0, m.start() - 60): m.end()]
            # WAC fallback 패턴(`wac_pkg_val if ... else awp_pkg_val`)은 허용.
            if "wac_" in snippet.lower() and "if" in snippet.lower():
                continue
            issues.append(f"US AWP 를 local_price 로 직접 사용: {rel_name}")
            _write_deviation({
                "severity": "ERROR",
                "agent": "Developer",
                "file": str(file_path),
                "deviation_type": "us_awp_as_local_price",
                "description": f"{rel_name}: local_price 에 AWP 직접 대입 (WAC 우선 누락)",
                "expected": "local_price = wac_pkg_val if wac_pkg_val is not None else awp_pkg_val",
                "actual": m.group(0),
                "corrective_action": "WAC 우선 + AWP fallback 패턴 복원 (foreign_agent_rules.md §국가별 소스)",
            })
            break

    # (b) injection pack_count ratio 추론 — form_type=='oral' guard 없이 사용 금지.
    for m in _RATIO_PACK_COUNT.finditer(code):
        pre = code[max(0, m.start() - 400): m.start()]
        if re.search(r'form_type\s*==\s*["\']oral["\']', pre):
            continue
        issues.append(f"injection ratio pack_count 추론 가능 경로: {rel_name}")
        _write_deviation({
            "severity": "ERROR",
            "agent": "Developer",
            "file": str(file_path),
            "deviation_type": "injection_ratio_pack_count",
            "description": f"{rel_name}: total_mg/unit_mg ratio 를 pack_count 로 사용 "
                           f"(form_type=='oral' guard 없음)",
            "expected": "if form_type == 'oral': ... ratio = total_mg / unit_mg",
            "actual": m.group(0),
            "corrective_action": "oral 분기 안으로 이동 — injection 은 농도×volume/농도=volume 이라 pack count 아님",
        })

    # (c) _populate_daily_cost 내부에서 unit_mg = _extract_mg(...) 사용 금지
    #     (per-mL 농도가 분모로 들어가 injection 에서 daily_cost 왜곡).
    if "def _populate_daily_cost" in code:
        func_match = re.search(
            r"def\s+_populate_daily_cost\b[\s\S]*?(?=\n\s{0,4}def\s|\Z)", code
        )
        if func_match and _UNIT_MG_EXTRACT.search(func_match.group(0)):
            issues.append(f"daily_cost 분모로 _extract_mg 사용: {rel_name}")
            _write_deviation({
                "severity": "ERROR",
                "agent": "Developer",
                "file": str(file_path),
                "deviation_type": "daily_cost_uses_extract_mg",
                "description": f"{rel_name}: _populate_daily_cost 가 unit_mg 에 "
                               f"_extract_mg 사용 (per-mL 농도 → injection 에서 왜곡)",
                "expected": "unit_mg = self._extract_per_unit_mg(form_type, ...)",
                "actual": "unit_mg = self._extract_mg(...)",
                "corrective_action": "_extract_per_unit_mg(form_type, dosage_strength, package_unit) 로 교체",
            })


def check_code_pattern(file_path: Path) -> list[str]:
    """파이썬 파일에서 금지 패턴 탐지. 발견된 문제 목록 반환."""
    if not file_path.exists():
        return []

    code = file_path.read_text(encoding="utf-8")
    issues: list[str] = []

    # 1) msd_only=True — 타입 기본값은 WARNING, 호출 하드코딩은 ERROR
    in_docstring = False
    docstring_char = None
    found_default = False
    found_hardcoded = False
    for line in code.splitlines():
        stripped = line.strip()
        for marker in ('"""', "'''"):
            count = stripped.count(marker)
            if count:
                if not in_docstring:
                    in_docstring = True
                    docstring_char = marker
                    if count >= 2:
                        in_docstring = False
                        docstring_char = None
                elif docstring_char == marker:
                    in_docstring = False
                    docstring_char = None
                break
        if in_docstring or stripped.startswith("#"):
            continue
        if re.search(r"msd_only\s*:\s*bool\s*=\s*True", stripped):
            found_default = True
        elif re.search(r"msd_only\s*=\s*True", stripped):
            found_hardcoded = True

    if found_hardcoded:
        issues.append(f"msd_only=True (호출 하드코딩): {file_path.name}")
        _write_deviation({
            "severity": "ERROR",
            "agent": "Developer",
            "file": str(file_path),
            "deviation_type": "msd_only_hardcoded",
            "description": f"{file_path.name}: msd_only=True 호출 하드코딩",
            "expected": "msd_only=False 또는 파라미터로 전달",
            "actual": "msd_only=True",
            "corrective_action": "msd_only=False로 변경",
        })
    elif found_default:
        issues.append(f"msd_only=True (생성자 기본값): {file_path.name}")
        _write_deviation({
            "severity": "WARNING",
            "agent": "Developer",
            "file": str(file_path),
            "deviation_type": "msd_only_default_true",
            "description": f"{file_path.name}: msd_only 기본값=True (ForeignPriceAgent에서 False로 호출 중)",
            "expected": "msd_only=False 권장",
            "actual": "msd_only: bool = True",
            "corrective_action": "ForeignPriceAgent에서 msd_only=False로 호출하면 무방",
        })

    # 2) 자격증명 하드코딩
    cred_pattern = re.findall(
        r'(?:password|passwd|secret)\s*=\s*["\'][^"\']{4,}["\']', code, re.IGNORECASE
    )
    if cred_pattern:
        issues.append(f"자격증명 하드코딩 의심: {file_path.name}")
        _write_deviation({
            "severity": "ERROR",
            "agent": "Developer",
            "file": str(file_path),
            "deviation_type": "hardcoded_credentials",
            "description": f"{file_path.name}에 자격증명 하드코딩 의심",
            "expected": "config/.env에서 로드",
            "actual": str(cred_pattern[:2]),
            "corrective_action": "환경변수로 이동",
        })

    # 3-pre) 해외 약가 최소단위 원칙 회귀 (2026-04-22 추가)
    #   (a) US Micromedex: AWP 를 local_price 로 직접 사용 금지 — factory_ratio 0.74 와 중복
    #   (b) injection 에서 total_mg/unit_mg ratio 로 pack_count 추론 금지
    #   (c) _populate_daily_cost 에서 _extract_per_unit_mg 대신 _extract_mg 사용 금지
    _check_foreign_minimum_unit(file_path, code, issues)

    # 3) BaseScraper 미상속 — 해외 가격 스크레이퍼(agents/scrapers/) 한정.
    #    HTA 승인 스크레이퍼(agents/hta_scrapers/) 는 BaseScraper 상속 대상 아님.
    if file_path.parent.name == "scrapers" and file_path.name != "base.py":
        if "class " in code and "BaseScraper" not in code:
            issues.append(f"BaseScraper 미상속: {file_path.name}")
            _write_deviation({
                "severity": "WARNING",
                "agent": "Developer",
                "file": str(file_path),
                "deviation_type": "missing_base_scraper",
                "description": f"{file_path.name}이 BaseScraper를 상속하지 않음",
                "expected": "class XxxScraper(BaseScraper):",
                "actual": "독립 클래스 구현",
                "corrective_action": "BaseScraper 상속 구조로 변경",
            })

    return issues
