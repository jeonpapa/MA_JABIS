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
