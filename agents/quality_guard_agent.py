"""
QualityGuardAgent — 모니터링·기록·보완 에이전트

역할:
  - 개발 진행 중 에이전트들의 결과물을 지속 모니터링
  - 의도한 방향에서 벗어난 경우를 기록(deviation_log.jsonl)
  - 자동 보완 가능한 경우 즉시 수정, 아니면 사용자에게 보고
  - 일일 요약 보고서 생성

기록 위치:
  quality_guard/deviation_log.jsonl
  quality_guard/daily_report_{date}.md
"""

import json
import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
GUARD_DIR = BASE_DIR / "quality_guard"
DEVIATION_LOG = GUARD_DIR / "deviation_log.jsonl"

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


# ─────────────────────────────────────────────────────────────────────────────
# 1) 편차 기록
# ─────────────────────────────────────────────────────────────────────────────

def _write_deviation(entry: dict) -> None:
    """편차를 JSONL 파일에 추가 기록."""
    GUARD_DIR.mkdir(parents=True, exist_ok=True)
    entry.setdefault("timestamp", datetime.now().isoformat())
    entry.setdefault("resolved", False)

    with open(DEVIATION_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    level = entry.get("severity", "INFO")
    if level == "ERROR":
        logger.error("[QualityGuard] %s", entry.get("description", ""))
    elif level == "WARNING":
        logger.warning("[QualityGuard] %s", entry.get("description", ""))
    else:
        logger.info("[QualityGuard] %s", entry.get("description", ""))


# ─────────────────────────────────────────────────────────────────────────────
# 2) 체크 함수들
# ─────────────────────────────────────────────────────────────────────────────

def check_scraper_output(
    results: list[dict],
    country: str,
    scraper_file: str = "",
) -> list[dict]:
    """
    스크레이퍼 반환값 검증.
    문제 발견 시 deviation_log에 기록하고, 자동 수정 가능한 항목은 수정.
    """
    issues = []

    for i, item in enumerate(results):
        # 필수 키 누락
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

        # local_price 타입 검증
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
                # 자동 보완: 0 이하 → None
                results[i]["local_price"] = None

        # dosage_strength 비어있는 경우 경고
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

        # source_url 비어있는 경우
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

    return results   # 자동 수정된 버전 반환


def check_db_records(
    records: list[dict],
    country: str,
) -> None:
    """DB 저장 전 레코드 검증."""
    for i, rec in enumerate(records):
        # 국가코드 검증
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

        # 통화코드 검증
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
    """
    Keytruda 가격 validation.
    가격이 있는 결과가 하나라도 있으면 True (시스템 정상 동작).
    """
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
        country, len(priced)
    )
    return True


def check_code_pattern(file_path: Path) -> list[str]:
    """
    파이썬 파일에서 금지 패턴 탐지.
    반환: 발견된 문제 목록
    """
    if not file_path.exists():
        return []

    code = file_path.read_text(encoding="utf-8")
    issues = []

    # 1) msd_only=True 탐지 (주석·독스트링 제외)
    # - 타입 어노테이션 기본값(msd_only: bool = True) → WARNING
    # - 실제 호출 하드코딩 → ERROR
    in_docstring = False
    docstring_char = None
    found_default = False
    found_hardcoded = False
    for line in code.splitlines():
        stripped = line.strip()
        # 독스트링 진입/탈출 추적
        for marker in ('"""', "'''"):
            count = stripped.count(marker)
            if count:
                if not in_docstring:
                    in_docstring = True
                    docstring_char = marker
                    if count >= 2:  # 한 줄 독스트링
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

    # 2) 자격증명 하드코딩 (이메일/패스워드 패턴)
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

    # 3) BaseScraper 미상속 (scrapers/ 디렉터리 파일만)
    if "scrapers" in str(file_path) and "base.py" not in str(file_path):
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


# ─────────────────────────────────────────────────────────────────────────────
# 3) QualityGuardAgent 메인 클래스
# ─────────────────────────────────────────────────────────────────────────────

class QualityGuardAgent:
    """
    전체 개발·운영 과정을 모니터링하고 편차를 기록·보완하는 에이전트.

    사용 예시:
        guard = QualityGuardAgent()

        # 스크레이퍼 결과 검증
        results = guard.validate_scraper_results(raw_results, "UK", "uk_mims.py")

        # Keytruda validation
        guard.run_keytruda_validation("UK", results)

        # 코드 패턴 검사
        guard.scan_codebase()

        # 일일 보고서
        guard.generate_daily_report()
    """

    def __init__(self):
        GUARD_DIR.mkdir(parents=True, exist_ok=True)

    # ── 스크레이퍼 결과 검증 ───────────────────────────────────────────────

    def validate_scraper_results(
        self,
        results: list[dict],
        country: str,
        scraper_file: str = "",
    ) -> list[dict]:
        """스크레이퍼 결과 검증 + 자동 보완 후 반환."""
        logger.info("[QualityGuard] [%s] 스크레이퍼 결과 검증: %d건", country, len(results))
        return check_scraper_output(results, country, scraper_file)

    def validate_db_records(self, records: list[dict], country: str) -> None:
        """DB 저장 전 레코드 검증."""
        check_db_records(records, country)

    # ── Keytruda Validation ────────────────────────────────────────────────

    def run_keytruda_validation(self, country: str, results: list[dict]) -> bool:
        """Keytruda 기준으로 스크레이퍼 정상 동작 검증."""
        return validate_keytruda(results, country)

    # ── 코드 스캔 ─────────────────────────────────────────────────────────

    def scan_codebase(self, target_dir: Optional[Path] = None) -> list[str]:
        """전체 코드베이스에서 금지 패턴 스캔."""
        scan_dir = target_dir or (BASE_DIR / "agents")
        all_issues = []

        for py_file in scan_dir.rglob("*.py"):
            if "__pycache__" in str(py_file):
                continue
            if py_file.name == "quality_guard_agent.py":
                continue  # 자기 자신은 스캔 제외
            issues = check_code_pattern(py_file)
            all_issues.extend(issues)

        if all_issues:
            logger.warning("[QualityGuard] 코드 스캔 결과: %d건 문제 발견", len(all_issues))
        else:
            logger.info("[QualityGuard] 코드 스캔 완료: 문제 없음 ✅")

        return all_issues

    # ── 편차 로그 조회 ─────────────────────────────────────────────────────

    def get_unresolved_deviations(self) -> list[dict]:
        """미해결 편차 목록 반환."""
        if not DEVIATION_LOG.exists():
            return []

        unresolved = []
        with open(DEVIATION_LOG, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    if not entry.get("resolved", False):
                        unresolved.append(entry)
                except json.JSONDecodeError:
                    continue
        return unresolved

    def mark_resolved(self, deviation_type: str, file: str = "") -> int:
        """특정 편차 유형을 해결로 표시. 처리 건수 반환."""
        if not DEVIATION_LOG.exists():
            return 0

        lines = DEVIATION_LOG.read_text(encoding="utf-8").splitlines()
        updated = 0
        new_lines = []

        for line in lines:
            try:
                entry = json.loads(line)
                if (entry.get("deviation_type") == deviation_type
                        and (not file or entry.get("file", "") == file)
                        and not entry.get("resolved", False)):
                    entry["resolved"] = True
                    entry["resolved_at"] = datetime.now().isoformat()
                    updated += 1
                new_lines.append(json.dumps(entry, ensure_ascii=False))
            except json.JSONDecodeError:
                new_lines.append(line)

        DEVIATION_LOG.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        return updated

    # ── 일일 보고서 생성 ───────────────────────────────────────────────────

    def generate_daily_report(self) -> Path:
        """미해결 편차를 요약한 일일 보고서 생성."""
        unresolved = self.get_unresolved_deviations()
        today = date.today().isoformat()

        errors   = [d for d in unresolved if d.get("severity") == "ERROR"]
        warnings = [d for d in unresolved if d.get("severity") == "WARNING"]

        lines = [
            f"# QualityGuard 일일 보고 — {today}",
            "",
            f"## 요약",
            f"- 미해결 편차 총계: **{len(unresolved)}건**",
            f"- ERROR: {len(errors)}건 / WARNING: {len(warnings)}건",
            "",
        ]

        if errors:
            lines.append("## ❌ ERROR")
            for e in errors:
                lines.append(
                    f"- [{e.get('file', e.get('agent', ''))}] "
                    f"{e.get('description', '')} "
                    f"→ *{e.get('corrective_action', '')}*"
                )
            lines.append("")

        if warnings:
            lines.append("## ⚠️ WARNING")
            for w in warnings:
                lines.append(
                    f"- [{w.get('file', w.get('agent', ''))}] "
                    f"{w.get('description', '')} "
                    f"→ *{w.get('corrective_action', '')}*"
                )
            lines.append("")

        if not unresolved:
            lines.append("## ✅ 모든 항목 정상 — 편차 없음")

        report_path = GUARD_DIR / f"daily_report_{today}.md"
        report_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("[QualityGuard] 일일 보고서 생성: %s", report_path)
        return report_path

    def print_summary(self) -> None:
        """현재 미해결 편차 요약을 콘솔 출력."""
        unresolved = self.get_unresolved_deviations()
        errors   = [d for d in unresolved if d.get("severity") == "ERROR"]
        warnings = [d for d in unresolved if d.get("severity") == "WARNING"]

        print(f"\n[QualityGuard 현황] 미해결: {len(unresolved)}건 "
              f"(ERROR={len(errors)}, WARNING={len(warnings)})")
        for d in unresolved[:10]:
            icon = "❌" if d.get("severity") == "ERROR" else "⚠️"
            print(f"  {icon} {d.get('description', '')}")
        if len(unresolved) > 10:
            print(f"  ... 외 {len(unresolved)-10}건")


# ─────────────────────────────────────────────────────────────────────────────
# CLI 실행
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    guard = QualityGuardAgent()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"

    if cmd == "scan":
        issues = guard.scan_codebase()
        print(f"\n발견된 문제: {len(issues)}건")
        for issue in issues:
            print(f"  - {issue}")

    elif cmd == "report":
        path = guard.generate_daily_report()
        print(f"보고서 생성: {path}")
        guard.print_summary()

    elif cmd == "summary":
        guard.print_summary()

    else:
        print(f"사용법: python quality_guard_agent.py [scan|report|summary]")
