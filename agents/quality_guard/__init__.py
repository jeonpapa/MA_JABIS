"""QualityGuard 패키지 — 감시·기록·보완·리뷰·제안.

공용 API:
    from agents.quality_guard import (
        QualityGuardAgent, _write_deviation,
        check_scraper_output, check_db_records, validate_keytruda, check_code_pattern,
    )

CLI:
    python -m agents.quality_guard [scan|report|summary|review]

규칙: agents/rules/quality_guard_rules.md
"""
from .agent import QualityGuardAgent
from .checks import (
    check_code_pattern,
    check_db_records,
    check_scraper_output,
    validate_keytruda,
)
from .deviations import _write_deviation

__all__ = [
    "QualityGuardAgent",
    "_write_deviation",
    "check_scraper_output",
    "check_db_records",
    "validate_keytruda",
    "check_code_pattern",
]
