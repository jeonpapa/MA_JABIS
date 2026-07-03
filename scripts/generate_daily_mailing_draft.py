#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agents.daily_mailing.discovery import DEFAULT_KEYWORDS, discover_naver_news, filter_recent_items, select_daily_items, expand_keywords_for_personas
from agents.daily_mailing.dashboard_scope import load_dashboard_scope
from agents.daily_mailing.personas import resolve_personas, resolve_reviewer_roles
from agents.daily_mailing.review_board import build_review_board_payload, save_review_board
from agents.daily_mailing.quality import evaluate_draft_quality
from agents.daily_mailing.calibration import write_calibration_template
from agents.daily_mailing.source_registry import SourceRegistry
from agents.daily_mailing.storage import persist_daily_mailing_run
from agents.daily_mailing.writer import render_daily_draft_html, render_daily_draft_markdown


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"\''))


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate MA Daily Mailing draft artifacts without sending email.")
    parser.add_argument("--keywords", nargs="*", default=DEFAULT_KEYWORDS)
    parser.add_argument("--dashboard-scope", default="", help="Path to AI Dashboard user-selection JSON. When provided, keywords/media/personas/lookback are read from the scope snapshot.")
    parser.add_argument("--display", type=int, default=20)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--media", nargs="*", default=[], help="Dashboard media/source IDs to boost/include; empty means all registered sources.")
    parser.add_argument("--personas", nargs="*", default=["ma_lead", "brand_strategy", "policy_watch"], help="Daily Mailing audience personas to shape scope/content.")
    parser.add_argument("--reviewer-roles", nargs="*", default=["source_verifier", "ma_strategist", "competitive_intel", "clinical_context", "executive_editor", "compliance_safety"], help="Reviewer roles to expose in the review-board artifact.")
    parser.add_argument("--media-strategy", choices=["boost", "strict"], default="boost", help="boost selected media while preserving coverage, or strict-filter to selected media only")
    parser.add_argument("--min-total-articles", type=int, default=3)
    parser.add_argument("--min-top-signals", type=int, default=2)
    parser.add_argument("--lookback-hours", type=int, default=24)
    parser.add_argument("--registry", default=str(REPO_ROOT / "config" / "source_registry.yaml"))
    parser.add_argument("--env", default=str(REPO_ROOT / "config" / ".env"))
    parser.add_argument("--out-dir", default=str(REPO_ROOT / "runs" / "drafts"))
    parser.add_argument("--db", default=str(REPO_ROOT / "data" / "db" / "drug_prices.db"))
    parser.add_argument("--retention-days", type=int, default=183, help="DB run/article retention; 183≈6개월")
    parser.add_argument("--review-board", action="store_true", default=True, help="Write review-board artifact JSON (default).")
    parser.add_argument("--no-review-board", dest="review_board", action="store_false", help="Disable review-board artifact JSON.")
    args = parser.parse_args()

    _load_env(Path(args.env))
    dashboard_scope_snapshot = None
    if args.dashboard_scope:
        scope = load_dashboard_scope(args.dashboard_scope)
        dashboard_scope_snapshot = scope.to_dict()
        args.keywords = scope.expanded_keywords() or args.keywords
        args.media = list(scope.media) or args.media
        args.personas = list(scope.personas) or args.personas
        args.lookback_hours = scope.lookback_hours or args.lookback_hours
    registry = SourceRegistry.from_file(args.registry)
    personas = resolve_personas(args.personas)
    reviewer_roles = resolve_reviewer_roles(args.reviewer_roles)
    expanded_keywords = expand_keywords_for_personas(args.keywords, personas)
    discovered = discover_naver_news(expanded_keywords, registry=registry, display=args.display, media=args.media, media_strategy=args.media_strategy)
    recent = filter_recent_items(discovered, hours=args.lookback_hours)
    ranked = select_daily_items(recent, expanded_keywords, limit=args.limit, media=args.media, personas=personas)
    quality_report = evaluate_draft_quality(ranked, min_total_articles=args.min_total_articles, min_top_signals=args.min_top_signals)
    window = f"이전 {args.lookback_hours}시간"

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    generated_at = datetime.now().astimezone().isoformat()
    run_id = stamp
    base = out_dir / f"ma_daily_mailing_draft_{stamp}"
    json_path = base.with_suffix(".json")
    md_path = base.with_suffix(".md")
    html_path = base.with_suffix(".html")
    review_board_path = None

    if args.review_board:
        review_payload = build_review_board_payload(
            discovered=discovered,
            recent=recent,
            selected=ranked,
            keywords=list(expanded_keywords),
            lookback_hours=args.lookback_hours,
            generated_at=generated_at,
            run_id=run_id,
            persona_ids=args.personas,
            reviewer_role_ids=args.reviewer_roles,
        )
        review_payload["status"] = quality_report.status
        review_payload["quality_report"] = asdict(quality_report)
        review_board_path = save_review_board(review_payload, out_dir)

    payload = {
        "run_id": run_id,
        "generated_at": generated_at,
        "window_label": window,
        "lookback_hours": args.lookback_hours,
        "keywords": expanded_keywords,
        "persona_ids": args.personas,
        "reviewer_role_ids": args.reviewer_roles,
        "personas": [p.persona_id for p in personas],
        "reviewer_roles": [r.role_id for r in reviewer_roles],
        "media": args.media,
        "dashboard_scope_snapshot": dashboard_scope_snapshot,
        "discovered_count": len(discovered),
        "recent_count": len(recent),
        "selected_count": len(ranked),
        "quality_report": quality_report.to_dict(),
        "status": quality_report.status,
        "delivery_status": "preview_only",
        "approval_status": "not_requested",
        "review_board_path": str(review_board_path) if review_board_path else None,
        "items": [item.__dict__ for item in ranked],
    }
    calibration_path = write_calibration_template(out_dir / "writer_agent_calibration_template.md")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_daily_draft_markdown(items=ranked, keywords=expanded_keywords, window_label=window, max_items=args.limit), encoding="utf-8")
    html_path.write_text(render_daily_draft_html(items=ranked, keywords=expanded_keywords, window_label=window, max_items=args.limit), encoding="utf-8")
    persisted = persist_daily_mailing_run(
        payload,
        articles=review_payload.get("articles", []) if args.review_board else ranked,
        markdown_path=str(md_path),
        html_path=str(html_path),
        json_path=str(json_path),
        review_board_path=str(review_board_path) if review_board_path else None,
        db_path=args.db,
        retention_days=args.retention_days,
    )

    print(json.dumps({
        "json": str(json_path),
        "markdown": str(md_path),
        "html": str(html_path),
        "review_board": str(review_board_path) if review_board_path else None,
        "db_persisted": persisted,
        "discovered_count": len(discovered),
        "recent_count": len(recent),
        "selected_count": len(ranked),
        "quality_report": quality_report.to_dict(),
        "calibration_template": str(calibration_path),
        "preview_only": True,
        "send_blocked": True,
        "top_titles": [item.title for item in ranked[:3]],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
