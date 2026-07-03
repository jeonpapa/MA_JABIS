from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, asdict
from typing import Iterable

from .writer import infer_ma_implication, should_include_in_top_signals, assess_article_quality, assess_content_completeness

GENERIC_IMPLICATION_MARKERS = (
    "대상 환자군, 비교약제, 재정영향",
    "신약 등재 심사 속도와 쟁점 설정",
    "재정영향 관리 조건과 사후관리 지표",
)

@dataclass(frozen=True)
class DraftQualityReport:
    status: str
    sendable: bool
    live_send_allowed: bool
    total_articles: int
    top_signal_count: int
    watchlist_count: int
    min_total_articles: int
    min_top_signals: int
    blocking_reasons: list[str]
    warnings: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def _duplicate_implication_flags(items: list) -> list[str]:
    implications = [infer_ma_implication(item).strip() for item in items if infer_ma_implication(item).strip()]
    counts = Counter(implications)
    flags = ["duplicate_implication_boilerplate" for text, n in counts.items() if n > 1 and len(text) > 20]
    for implication in implications:
        if any(marker in implication for marker in GENERIC_IMPLICATION_MARKERS):
            # Allow only when article-specific terms also appear; otherwise it is too generic.
            if not any(term in implication for term in ("Keytruda", "키트루다", "린파자", "HRD", "평가금액", "환자군", "비교약제")):
                flags.append("generic_implication_requires_editorial_review")
    return sorted(set(flags))


def evaluate_draft_quality(
    items: Iterable,
    *,
    min_total_articles: int = 3,
    min_top_signals: int = 2,
) -> DraftQualityReport:
    selected = list(items)
    top = [item for item in selected if should_include_in_top_signals(item)]
    watch = [item for item in selected if item not in top]
    blocking: list[str] = []
    warnings: list[str] = []

    if len(selected) < min_total_articles:
        blocking.append("insufficient_coverage")
    if len(top) < min_top_signals:
        blocking.append("insufficient_top_signals")
    if any("calibration_source_not_live_candidate" in assess_article_quality(item).get("quality_flags", []) for item in selected):
        blocking.append("calibration_source_selected")
    boilerplate_flags = _duplicate_implication_flags(selected)
    warnings.extend(boilerplate_flags)
    completeness_reports = [assess_content_completeness(item) for item in selected]
    if any(report.get("missing") for report in completeness_reports):
        warnings.append("content_completeness_missing_fields")
    if any("official_cross_check_required" in report.get("warnings", []) for report in completeness_reports):
        warnings.append("official_cross_check_required")
    if any("publisher_or_source_registration_required" in report.get("warnings", []) for report in completeness_reports):
        warnings.append("publisher_or_source_registration_required")
    if any("unregistered_source_requires_review" in assess_article_quality(item).get("quality_flags", []) for item in selected):
        blocking.append("selected_unregistered_source_requires_verification")
    # Monitoring newsletters may legitimately include multiple articles from the
    # same story wave. Duplicate implication text is an editorial warning for the
    # writer/reviewer, not a reason to suppress the monitoring draft itself.

    warnings = sorted(set(warnings))
    status = "quality_gated_draft" if not blocking else "draft_only_insufficient_quality"
    return DraftQualityReport(
        status=status,
        sendable=False,
        live_send_allowed=False,
        total_articles=len(selected),
        top_signal_count=len(top),
        watchlist_count=len(watch),
        min_total_articles=min_total_articles,
        min_top_signals=min_top_signals,
        blocking_reasons=blocking,
        warnings=warnings,
    )
