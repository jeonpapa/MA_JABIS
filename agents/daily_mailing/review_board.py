from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .discovery import NewsDiscoveryItem, canonicalize_url
from .personas import resolve_personas, resolve_reviewer_roles, persona_to_dict, reviewer_role_to_dict
from .writer import assess_article_quality, assess_content_completeness

REVIEW_STATUSES = (
    "candidate",
    "needs_review",
    "ready_for_writer",
    "rejected",
    "draft_created",
    "sent",
    "excluded",
)

SOURCE_STATUSES = (
    "official_verified",
    "publisher_verified",
    "media_report_only",
    "calibration_only",
    "excluded",
)

OFFICIAL_SOURCE_TIERS = {"official", "official_payer", "regulator"}
REGISTERED_MEDIA_PREFIXES = ("media_", "trade_", "publisher_")


def _item_get(item, key: str, default=None):
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _item_to_dict(item) -> dict:
    if isinstance(item, dict):
        return dict(item)
    if isinstance(item, NewsDiscoveryItem):
        return dict(item.__dict__)
    return {
        key: getattr(item, key)
        for key in dir(item)
        if not key.startswith("_") and not callable(getattr(item, key))
    }


def article_id_for_url(url: str) -> str:
    """Return a deterministic article id based on canonicalized URL or title-like input."""
    raw = (url or "").strip()
    canonical = canonicalize_url(raw) if raw else ""
    if not canonical or canonical == "/":
        canonical = raw.lower() or "missing-url"
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"article_{digest}"


def _quality_for(item) -> dict:
    """Return writer quality metadata, preserving explicit item-level annotations."""
    quality = assess_article_quality(item)
    for key in ("ma_relevance", "priority", "review_status", "quality_flags"):
        value = _item_get(item, key)
        if value is not None:
            quality[key] = list(value) if key == "quality_flags" and not isinstance(value, list) else value
    if quality.get("review_status") not in REVIEW_STATUSES:
        quality["review_status"] = "needs_review"
    return quality


def classify_source_status(item, quality: dict | None = None) -> str:
    """Classify review-board source verification status conservatively.

    This is registry-based classification, not a live HTTP/content verification.
    The artifact therefore also emits verification_method='registry_only' and
    reviewer-facing caveats before a draft can become sendable.
    """
    quality = quality or _quality_for(item)
    flags = set(quality.get("quality_flags", []))
    review_status = quality.get("review_status")
    source_tier = str(_item_get(item, "source_tier", "") or "")
    publisher_url = str(_item_get(item, "publisher_url", "") or _item_get(item, "url", "") or "")

    if "calibration_source_not_live_candidate" in flags:
        return "calibration_only"
    if review_status == "excluded":
        return "excluded"
    if source_tier in OFFICIAL_SOURCE_TIERS:
        return "official_verified"
    if publisher_url and (
        source_tier.startswith(REGISTERED_MEDIA_PREFIXES)
        or source_tier in {"media_tier_A", "media_tier_B", "tier_1_trade_media"}
    ):
        return "publisher_verified"
    return "media_report_only"


def verification_caveat_for(item, quality: dict, source_status: str) -> str:
    flags = set(quality.get("quality_flags", []))
    if source_status == "official_verified":
        return "Official-source URL is identified by registry; verify extracted facts before approval."
    if source_status == "publisher_verified" and "official_cross_check_required" in flags:
        return "Publisher page is identified by registry; official HIRA/MOHW/MFDS cross-check required before send."
    if source_status == "publisher_verified":
        return "Publisher page is identified by registry; reviewer should confirm article facts before approval."
    if source_status == "calibration_only":
        return "Calibration/reference source only; excluded from live candidate pool."
    if source_status == "excluded":
        return "Excluded by quality/source gate."
    return "Media/discovery signal only; publisher or official-source verification required before send."



def _next_action_for(quality: dict, source_status: str, content_completeness: dict) -> str:
    flags = set(quality.get("quality_flags", []))
    if "keytruda_direct_source_verification_promoted" in flags:
        return "Keytruda 직접 관련 후보: 공식 출처/원출처 확인 대상으로 승격하고 HIRA/MOHW/MFDS 또는 회사 원문을 대조합니다."
    if "policy_pricing_tracker" in flags:
        return "Policy 파트 누적 tracker에 편입하고 적응증별 약가·RSA·사용량-약가 축으로 후속 변화를 누적합니다."
    if content_completeness.get("missing"):
        return "Complete missing content fields before editorial review."
    if source_status == "media_report_only":
        return "Verify publisher/original source before service use."
    if "official_cross_check_required" in quality.get("quality_flags", []):
        return "Run official-source cross-check for MA claims."
    if quality.get("ma_relevance", 0) < 3:
        return "Keep as Watchlist unless reviewer upgrades MA relevance."
    return "Ready for writer/editor review; live send remains blocked."


def _reviewer_findings_for(item, *, selected_for_draft: bool, quality: dict, source_status: str, reviewer_roles: Iterable) -> list[dict]:
    flags = set(quality.get("quality_flags", []))
    findings: list[dict] = []
    for role in reviewer_roles or []:
        role_id = getattr(role, "role_id", str(role))
        decision = "pass"
        required_fix = ""
        rationale = "No blocking issue identified by deterministic checks."
        if role_id == "source_verifier":
            if "keytruda_direct_source_verification_promoted" in flags:
                decision = "warn"
                required_fix = "Confirm official/original source for direct Keytruda access/reimbursement claim."
                rationale = "Joseph promoted Keytruda-direct candidates to official/original-source verification."
            elif source_status in {"media_report_only", "calibration_only", "excluded"}:
                decision = "block" if selected_for_draft and source_status != "calibration_only" else "warn"
                required_fix = "Confirm publisher/original source URL before using as service content."
                rationale = f"Source status is {source_status}."
            elif "official_cross_check_required" in flags:
                decision = "warn"
                required_fix = "Cross-check official HIRA/MOHW/MFDS or company source for reimbursement claims."
                rationale = "High-MA media item requires official-source confirmation."
        elif role_id == "ma_strategist":
            if quality.get("ma_relevance", 0) < 3 and selected_for_draft:
                decision = "warn"
                required_fix = "Keep as Watchlist unless a direct payer/access implication is verified."
                rationale = "Selected item is monitoring-relevant but not a Top MA signal."
        elif role_id == "competitive_intel":
            if quality.get("monitoring_importance", 0) >= 3 and quality.get("ma_relevance", 0) < 3:
                decision = "warn"
                required_fix = "Classify as MSD/competitor watchpoint and avoid MA overstatement."
                rationale = "Brand/company relevance exceeds payer relevance."
        elif role_id == "clinical_context" and quality.get("ma_relevance", 0) >= 3:
            decision = "warn"
            required_fix = "Verify product, indication, patient group, biomarker/line if present in the final prose."
            rationale = "Clinical context affects MA implication defensibility."
        elif role_id == "executive_editor" and selected_for_draft:
            decision = "warn"
            required_fix = "Check story duplication, section balance, and boilerplate insight before final approval."
            rationale = "Selected draft item needs editorial polishing."
        elif role_id == "compliance_safety" and selected_for_draft:
            decision = "warn"
            required_fix = "Maintain preview-only state until service-level approval."
            rationale = "Draft selection is not approval to send."
        findings.append({
            "reviewer": role_id,
            "label": getattr(role, "label", role_id),
            "decision": decision,
            "rationale": rationale,
            "required_fix": required_fix,
        })
    return findings

def _tracking_lane_for(quality: dict) -> str:
    flags = set(quality.get("quality_flags", []))
    if "keytruda_direct_source_verification_promoted" in flags:
        return "keytruda_source_verification"
    if "policy_pricing_tracker" in flags:
        return "policy_pricing_tracker"
    return "daily_monitoring"


def _tracker_tags_for(item, quality: dict) -> list[str]:
    flags = set(quality.get("quality_flags", []))
    if "policy_pricing_tracker" not in flags:
        return []
    text = f"{_item_get(item, 'title', '')} {_item_get(item, 'description', '')}".lower()
    tags: list[str] = []
    if "적응증별" in text or "1약=1약가" in text:
        tags.append("indication_based_pricing")
    if "rsa" in text or "위험분담" in text:
        tags.append("rsa")
    if "사용량-약가" in text or "사용량 약가" in text:
        tags.append("price_volume")
    return tags or ["pricing_policy"]


def build_article_card(item, *, selected_for_draft: bool, personas: Iterable | None = None, reviewer_roles: Iterable | None = None) -> dict:
    data = _item_to_dict(item)
    quality = _quality_for(item)
    if "keytruda_direct_source_verification_promoted" in quality.get("quality_flags", []):
        quality["priority"] = "High"
        quality["review_status"] = "ready_for_writer"
    source_status = classify_source_status(item, quality)
    publisher_url = str(data.get("publisher_url") or data.get("url") or "")
    naver_url = str(data.get("naver_url") or "")
    official_url = publisher_url if source_status == "official_verified" else None
    content_completeness = assess_content_completeness(item, personas=personas)
    reviewer_findings = _reviewer_findings_for(item, selected_for_draft=selected_for_draft, quality=quality, source_status=source_status, reviewer_roles=reviewer_roles or [])
    return {
        "article_id": article_id_for_url(publisher_url or naver_url or str(data.get("title", ""))),
        "title": data.get("title", ""),
        "publisher_url": publisher_url,
        "naver_url": naver_url,
        "source_name": data.get("source_name", ""),
        "source_tier": data.get("source_tier", ""),
        "source_status": source_status,
        "priority": quality.get("priority"),
        "ma_relevance": quality.get("ma_relevance"),
        "review_status": quality.get("review_status"),
        "quality_flags": quality.get("quality_flags", []),
        "official_url": official_url,
        "publisher_verified_url": publisher_url if source_status == "publisher_verified" else None,
        "verification_method": "registry_only",
        "verification_caveat": verification_caveat_for(item, quality, source_status),
        "selected_for_draft": bool(selected_for_draft),
        "score": data.get("score", 0.0),
        "published_at": data.get("published_at", ""),
        "matched_keywords": list(data.get("matched_keywords", []) or []),
        "keyword": data.get("keyword", ""),
        "reviewer_note": data.get("reviewer_note"),
        "tracking_lane": _tracking_lane_for(quality),
        "tracker_tags": _tracker_tags_for(item, quality),
        "persona_ids": [getattr(p, "persona_id", str(p)) for p in personas or []],
        "reviewer_roles": [getattr(r, "role_id", str(r)) for r in reviewer_roles or []],
        "reviewer_findings": reviewer_findings,
        "content_completeness": content_completeness,
        "next_action": data.get("next_action") or _next_action_for(quality, source_status, content_completeness),
    }


def _key_for(item) -> str:
    data = _item_to_dict(item)
    raw = str(data.get("publisher_url") or data.get("url") or data.get("naver_url") or data.get("title") or "")
    canonical = canonicalize_url(raw) if raw else ""
    if canonical and canonical != "/":
        return canonical
    title = str(data.get("title") or "").strip()
    return f"title:{title}" if title else f"missing:{id(item)}"


def _safe_run_id(run_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", run_id).strip("._-")
    return safe or datetime.now().strftime("%Y%m%d_%H%M%S")


def build_review_board_payload(
    *,
    discovered: Iterable,
    recent: Iterable,
    selected: Iterable,
    keywords: list[str],
    lookback_hours: int,
    generated_at: str | None = None,
    run_id: str | None = None,
    persona_ids: list[str] | None = None,
    reviewer_role_ids: list[str] | None = None,
) -> dict:
    generated_at = generated_at or datetime.now().astimezone().isoformat()
    run_id = _safe_run_id(run_id or generated_at.replace(":", "").replace("-", "").split(".", 1)[0])
    discovered_list = list(discovered)
    recent_list = list(recent)
    selected_list = list(selected)
    personas = resolve_personas(persona_ids)
    reviewer_roles = resolve_reviewer_roles(reviewer_role_ids)
    selected_by_key = {_key_for(item): item for item in selected_list}
    selected_keys = set(selected_by_key)

    # Review board focuses on the candidate window. It also appends any selected
    # item missing from that window as an explicit anomaly, so the selected count
    # can never hide a draft article from review.
    articles: list[dict] = []
    seen_keys: set[str] = set()
    for item in recent_list:
        key = _key_for(item)
        seen_keys.add(key)
        articles.append(build_article_card(selected_by_key.get(key, item), selected_for_draft=key in selected_keys, personas=personas, reviewer_roles=reviewer_roles))
    for key, item in selected_by_key.items():
        if key in seen_keys:
            continue
        card = build_article_card(item, selected_for_draft=True, personas=personas, reviewer_roles=reviewer_roles)
        flags = list(card.get("quality_flags", []))
        if "selected_item_not_in_recent_window" not in flags:
            flags.append("selected_item_not_in_recent_window")
        card["quality_flags"] = flags
        card["verification_caveat"] = f"Selected item was not present in the recent candidate window. {card['verification_caveat']}"
        articles.append(card)

    lane_counts: dict[str, int] = {}
    for article in articles:
        lane = str(article.get("review_status") or "candidate")
        lane_counts[lane] = lane_counts.get(lane, 0) + 1

    return {
        "run_id": run_id,
        "generated_at": generated_at,
        "status": "quality_gated_draft",
        "lookback_hours": lookback_hours,
        "keywords": list(keywords),
        "personas": [persona_to_dict(p) for p in personas],
        "reviewer_roles": [reviewer_role_to_dict(r) for r in reviewer_roles],
        "counts": {
            "discovered": len(discovered_list),
            "recent": len(recent_list),
            "selected": len(selected_list),
            "articles": len(articles),
            "lanes": lane_counts,
        },
        "lanes": [
            "Dashboard Scope",
            "Source Intake",
            "Triage/Verify",
            "Writer Agent",
            "Review Board",
            "Delivery/History",
        ],
        "operating_policy": {
            "article_approval_required": False,
            "live_send_allowed": False,
            "reviewer_roles_are_advisory": True,
            "personas_are_audience_targeting_metadata": True,
            "board_purpose": "Admin operational Kanban: scope/intake/triage/writer/delivery 상태와 품질 플래그를 보는 화면이며, 기사별 approve workflow는 두지 않습니다.",
            "sendable_requires": [
                "draft artifact generated from selected dashboard scope",
                "publisher/official-source caveats visible to operator",
                "final mailing send/draft step approved at service level, not per article",
            ],
        },
        "articles": articles,
    }


def save_review_board(payload: dict, out_dir: str | Path) -> Path:
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    run_id = _safe_run_id(str(payload.get("run_id") or datetime.now().strftime("%Y%m%d_%H%M%S")))
    path = out_path / f"ma_daily_mailing_review_board_{run_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
