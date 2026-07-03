from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class DailyMailingPersona:
    """Audience persona used to shape MA Daily Mailing scope and content completeness."""

    persona_id: str
    label: str
    description: str
    default_keywords: tuple[str, ...]
    priority_terms: tuple[str, ...]
    watch_terms: tuple[str, ...]
    content_requirements: tuple[str, ...]


@dataclass(frozen=True)
class ReviewerRole:
    """Deterministic reviewer role exposed on the review board.

    These roles are advisory quality-control lenses. They do not approve live send;
    service-level approval remains separate and disabled by default.
    """

    role_id: str
    label: str
    description: str
    required_checks: tuple[str, ...]


PERSONAS: dict[str, DailyMailingPersona] = {
    "ma_lead": DailyMailingPersona(
        persona_id="ma_lead",
        label="Market Access Lead",
        description="Korea reimbursement, pricing, payer decision, and patient-access monitoring.",
        default_keywords=("약평위", "암질심", "건정심", "약가협상", "위험분담", "RSA", "급여"),
        priority_terms=("약평위", "암질심", "건정심", "평가금액", "약가협상", "위험분담", "RSA", "급여 적정성"),
        watch_terms=("허가", "신속심사", "환자 접근", "희귀", "소아", "급여 사각지대"),
        content_requirements=(
            "source_url",
            "published_at",
            "source_status",
            "key_points",
            "why_it_matters",
            "ma_implication_when_top_signal",
            "official_cross_check_when_high_ma",
        ),
    ),
    "brand_strategy": DailyMailingPersona(
        persona_id="brand_strategy",
        label="Brand / Franchise Strategy",
        description="MSD product, competitor, launch, indication, and franchise watchpoints.",
        default_keywords=("MSD", "한국MSD", "키트루다", "가다실", "린파자", "웰리렉", "Keytruda", "Gardasil", "Lynparza", "Welireg"),
        priority_terms=("msd", "엠에스디", "키트루다", "keytruda", "가다실", "gardasil", "린파자", "lynparza", "웰리렉", "welireg"),
        watch_terms=("캠페인", "임상", "허가", "경쟁", "출시", "적응증", "바이오시밀러", "특허", "LOE"),
        content_requirements=(
            "source_url",
            "published_at",
            "source_status",
            "key_points",
            "msd_or_competitor_context",
            "why_it_matters",
        ),
    ),
    "policy_watch": DailyMailingPersona(
        persona_id="policy_watch",
        label="Policy / Payer Watch",
        description="HIRA/MOHW/NHIS policy, reimbursement-process, and payer-operation signals.",
        default_keywords=("보건복지부", "심평원", "건보공단", "약제급여평가위원회", "재정영향", "고시"),
        priority_terms=("보건복지부", "심평원", "건보공단", "약제급여평가위원회", "재정영향", "고시", "사후관리"),
        watch_terms=("제도", "위원회", "평가", "수가", "보험", "정책"),
        content_requirements=(
            "source_url",
            "published_at",
            "source_status",
            "key_points",
            "policy_or_payer_context",
            "official_cross_check_when_high_ma",
        ),
    ),
}


REVIEWER_ROLES: dict[str, ReviewerRole] = {
    "source_verifier": ReviewerRole(
        role_id="source_verifier",
        label="Source Verifier",
        description="Checks publisher/official URL, source tier, cross-check needs, and source caveats.",
        required_checks=("publisher_url", "source_status", "verification_caveat", "official_cross_check_required"),
    ),
    "ma_strategist": ReviewerRole(
        role_id="ma_strategist",
        label="MA Strategist",
        description="Checks reimbursement/pricing/payer implication quality and defensibility.",
        required_checks=("ma_relevance", "market_access_note", "patient_population", "comparator_or_budget_impact"),
    ),
    "competitive_intel": ReviewerRole(
        role_id="competitive_intel",
        label="Competitive Intelligence",
        description="Checks MSD/competitor/product relevance and franchise monitoring value.",
        required_checks=("msd_or_competitor_context", "matched_keywords", "monitoring_importance"),
    ),
    "clinical_context": ReviewerRole(
        role_id="clinical_context",
        label="Clinical Context Reviewer",
        description="Checks indication, biomarker, patient group, and overstatement risk from incomplete snippets.",
        required_checks=("product", "indication", "patient_population", "clinical_claims_to_verify"),
    ),
    "executive_editor": ReviewerRole(
        role_id="executive_editor",
        label="Executive Editor",
        description="Checks section balance, duplication, readability, and actionability for leadership readers.",
        required_checks=("section_balance", "duplicate_story_wave", "non_boilerplate_insight", "next_watch"),
    ),
    "compliance_safety": ReviewerRole(
        role_id="compliance_safety",
        label="Compliance / Safety Reviewer",
        description="Checks preview-only status and prevents overstatement of unverified facts.",
        required_checks=("live_send_allowed_false", "candidate_signal_caveat", "no_unverified_official_claim"),
    ),
}


def resolve_personas(persona_ids: list[str] | tuple[str, ...] | None = None) -> list[DailyMailingPersona]:
    ids = list(persona_ids or ["ma_lead", "brand_strategy", "policy_watch"])
    return [PERSONAS[p] for p in ids if p in PERSONAS]


def resolve_reviewer_roles(role_ids: list[str] | tuple[str, ...] | None = None) -> list[ReviewerRole]:
    ids = list(role_ids or ["source_verifier", "ma_strategist", "competitive_intel", "clinical_context", "executive_editor", "compliance_safety"])
    return [REVIEWER_ROLES[r] for r in ids if r in REVIEWER_ROLES]


def persona_to_dict(persona: DailyMailingPersona) -> dict:
    return asdict(persona)


def reviewer_role_to_dict(role: ReviewerRole) -> dict:
    return asdict(role)
