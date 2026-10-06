"""QualityGuard 패키지 — 감시·기록·보완·리뷰·제안.

공용 API:
    from agents.quality_guard import (
        QualityGuardAgent, _write_deviation,
        check_scraper_output, check_foreign_price_data, check_code_pattern,
    )

CLI:
    python -m agents.quality_guard [scan|report|summary|review]

규칙: agents/rules/quality_guard_rules.md
"""
from .agent import QualityGuardAgent
from .checks import (
    check_code_pattern,
    check_foreign_price_data,
    check_scraper_output,
    summarize_unverified_pack_rows,
)
from .deviations import _write_deviation

__all__ = [
    "QualityGuardAgent",
    "_write_deviation",
    "check_scraper_output",
    "check_foreign_price_data",
    "summarize_unverified_pack_rows",
    "check_code_pattern",
]
