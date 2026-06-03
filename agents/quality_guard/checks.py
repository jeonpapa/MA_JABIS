"""무상태 검증 함수 — 스크레이퍼 출력 / DB 레코드 / Keytruda validation / 코드 패턴."""
from __future__ import annotations

import logging
import re
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


def check_db_records(records: list[dict], country: str) -> None:
    """DB 저장 전 레코드 검증."""
    for rec in records:
        if rec.get("country", "").upper() not in VALID_COUNTRIES:
            _write_deviation({
                "severity": "ERROR",
                "agent": "ForeignPriceAgent",
                "deviation_type": "invalid_country_code",
                "description": f"유효하지 않은 국가코드: {rec.get('country')}",
                "expected": str(VALID_COUNTRIES),
                "actual": rec.get("country"),
                "corrective_action": "국가코드 수정 필요",
            })

        if rec.get("currency", "").upper() not in VALID_CURRENCIES:
            _write_deviation({
                "severity": "ERROR",
                "agent": "ForeignPriceAgent",
                "deviation_type": "invalid_currency_code",
                "description": f"유효하지 않은 통화코드: {rec.get('currency')} ({country})",
                "expected": str(VALID_CURRENCIES),
                "actual": rec.get("currency"),
                "corrective_action": "통화코드 수정 필요",
            })


def validate_keytruda(results: list[dict], country: str) -> bool:
    """Keytruda 가격 validation — 1건 이상 가격 있으면 True."""
    priced = [r for r in results if r.get("local_price") is not None]
    if not priced:
        _write_deviation({
            "severity": "WARNING",
            "agent": "Scraper",
            "deviation_type": "keytruda_validation_failed",
            "description": f"[{country}] Keytruda validation 실패 — 가격 없음",
            "expected": "1건 이상 local_price 있는 결과",
            "actual": f"{len(results)}건 결과, 모두 local_price=None",
            "corrective_action": "해당 국가 스크레이퍼 점검 또는 비급여 확인",
        })
        return False

    logger.info(
        "[QualityGuard] [%s] Keytruda validation ✅ — %d건 가격 확인",
        country, len(priced),
    )
    return True


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
