"""헤르메스 run-bundle 스키마 마이그레이션 (KANBAN_MIGRATION_SPEC_20260706) 회귀 테스트.

- daily_mailing_run/article 신규 컬럼 (fresh CREATE + 구스키마 ALTER 양쪽)
- 6-lane 고정 배정 규칙
- 샘플 run 번들 ingest → load_admin_kanban 계약
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.daily_mailing.storage import (
    ensure_daily_mailing_tables,
    load_admin_kanban,
    persist_daily_mailing_run,
)
from agents.ingest.daily_mailing_sync import sync_daily_mailing_runs

LANE_NAMES = ["Dashboard Scope", "Source Intake", "Triage/Verify", "Writer Agent", "Review Board", "Delivery/History"]

NEW_RUN_COLS = {
    "quality_report_json", "personas_json", "reviewer_roles_json", "operating_policy_json",
    "counts_json", "draft_items_json", "dashboard_scope_json",
}
NEW_ARTICLE_COLS = {
    "tracking_lane", "reviewer_findings_json", "next_action", "tracker_tags_json",
    "verification_method", "official_url", "content_completeness_json", "persona_ids_json", "reviewer_note",
}

SAMPLE_BUNDLE = (
    Path(__file__).resolve().parent.parent
    / "docs" / "daily_mailing" / "ma_daily_mailing_final_refine_20260706_015250"
    / "ma_daily_mailing_run_bundle_20260706_015250.json"
)

OLD_SCHEMA_SQL = """
CREATE TABLE daily_mailing_run (
    run_id TEXT PRIMARY KEY, generated_at TEXT NOT NULL, window_label TEXT,
    lookback_hours INTEGER NOT NULL DEFAULT 24, keywords_json TEXT NOT NULL,
    media_json TEXT NOT NULL DEFAULT '[]', subscription_id INTEGER, owner_email TEXT,
    recipients_json TEXT NOT NULL DEFAULT '[]', delivery_status TEXT NOT NULL DEFAULT 'draft_only',
    approval_status TEXT NOT NULL DEFAULT 'not_requested', gmail_draft_id TEXT,
    gmail_message_id TEXT, sent_at TEXT, discovered_count INTEGER NOT NULL DEFAULT 0,
    recent_count INTEGER NOT NULL DEFAULT 0, selected_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL, markdown_path TEXT, html_path TEXT, json_path TEXT,
    review_board_path TEXT, expires_at TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE daily_mailing_article (
    article_id TEXT NOT NULL, run_id TEXT NOT NULL, title TEXT NOT NULL,
    publisher_url TEXT, naver_url TEXT, source_name TEXT, source_tier TEXT,
    source_status TEXT, priority TEXT, ma_relevance INTEGER, review_status TEXT,
    quality_flags_json TEXT NOT NULL, selected_for_draft INTEGER NOT NULL DEFAULT 0,
    score REAL NOT NULL DEFAULT 0, published_at TEXT, matched_keywords_json TEXT NOT NULL,
    keyword TEXT, verification_caveat TEXT, expires_at TEXT NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, article_id)
);
"""


def _cols(db, table):
    with sqlite3.connect(str(db)) as conn:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_fresh_db_has_new_columns(tmp_path):
    db = tmp_path / "fresh.db"
    ensure_daily_mailing_tables(db)
    assert NEW_RUN_COLS <= _cols(db, "daily_mailing_run")
    assert NEW_ARTICLE_COLS <= _cols(db, "daily_mailing_article")


def test_legacy_db_migrated_via_alter(tmp_path):
    db = tmp_path / "legacy.db"
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(OLD_SCHEMA_SQL)
    ensure_daily_mailing_tables(db)
    assert NEW_RUN_COLS <= _cols(db, "daily_mailing_run")
    assert NEW_ARTICLE_COLS <= _cols(db, "daily_mailing_article")
    # 구 스키마 DB 도 load 가 새 계약으로 동작해야 함
    k = load_admin_kanban(db_path=db)
    assert [lane["name"] for lane in k["lanes"]] == LANE_NAMES
    assert k["operating_policy"]["article_approval_required"] is False
    assert len(k["personas"]) == 3 and len(k["reviewer_roles"]) == 6


def _article(article_id, **over):
    base = {
        "article_id": article_id, "title": f"t-{article_id}", "source_status": "media_report_only",
        "review_status": "needs_review", "selected_for_draft": False, "score": 1.0,
        "quality_flags": [], "matched_keywords": [], "tracking_lane": "daily_monitoring",
        "reviewer_findings": [], "tracker_tags": [], "persona_ids": ["ma_lead"],
        "content_completeness": {"score": 50, "missing": []}, "verification_method": "registry_only",
        "next_action": "verify", "reviewer_note": None, "official_url": None,
    }
    base.update(over)
    return base


def test_six_lane_assignment_rule(tmp_path):
    db = tmp_path / "lanes.db"
    payload = {"run_id": "R-LANES", "generated_at": "2026-07-06T02:00:00+09:00",
               "keywords": ["키트루다"], "status": "quality_gated_draft",
               "discovered_count": 4, "recent_count": 4, "selected_count": 1}
    articles = [
        _article("a-writer", selected_for_draft=True, review_status="ready_for_writer"),
        _article("a-review", review_status="ready_for_writer"),
        _article("a-triage", source_status="publisher_verified"),
        _article("a-triage2", source_status="official_verified"),
        _article("a-intake"),
    ]
    persist_daily_mailing_run(payload, articles=articles, db_path=db)
    k = load_admin_kanban(db_path=db)
    lanes = {lane["name"]: [i.get("article_id") for i in lane["items"]] for lane in k["lanes"]}
    assert lanes["Writer Agent"] == ["a-writer"]
    assert lanes["Review Board"] == ["a-review"]
    assert set(lanes["Triage/Verify"]) == {"a-triage", "a-triage2"}
    assert lanes["Source Intake"] == ["a-intake"]
    # run-level 정보 레인: synthetic 카드
    assert k["lanes"][0]["name"] == "Dashboard Scope" and k["lanes"][0]["items"][0]["type"] == "run_scope"
    assert k["lanes"][5]["items"][0]["type"] == "run_delivery"
    # counts 파생 (needs_review/ready_for_writer 는 카드에서)
    assert k["runs"][0]["counts"] == {"discovered": 4, "recent": 4, "selected": 1,
                                      "needs_review": 3, "ready_for_writer": 2}


def test_reviewer_findings_dict_normalized_to_list(tmp_path):
    db = tmp_path / "findings.db"
    payload = {"run_id": "R-F", "generated_at": "2026-07-06T02:00:00+09:00", "keywords": [], "status": "quality_gated_draft"}
    finding = {"reviewer": "source_verifier", "label": "Source Verifier", "decision": "warn",
               "rationale": "check", "required_fix": "fix"}
    persist_daily_mailing_run(payload, articles=[_article("a1", reviewer_findings=finding)], db_path=db)
    k = load_admin_kanban(db_path=db)
    item = next(i for lane in k["lanes"] for i in lane["items"] if i.get("article_id") == "a1")
    assert item["reviewer_findings"] == [finding]


@pytest.mark.skipif(not SAMPLE_BUNDLE.exists(), reason="sample run bundle not present")
def test_sample_bundle_ingest_contract(tmp_path):
    db = tmp_path / "sample.db"
    src = tmp_path / "runs"; src.mkdir()
    (src / "20260706_015250.json").write_bytes(SAMPLE_BUNDLE.read_bytes())
    res = sync_daily_mailing_runs(source_dir=src, db_path=db)
    assert res["imported"] == 1 and not res["errors"]
    k = load_admin_kanban(db_path=db)
    lanes = {lane["name"]: lane["items"] for lane in k["lanes"]}
    assert [lane["name"] for lane in k["lanes"]] == LANE_NAMES
    writer = lanes["Writer Agent"]
    assert len(writer) == 6 and all(a["selected_for_draft"] for a in writer)
    # 4개 기사 레인 합 = 193 리치 카드 전량
    assert sum(len(lanes[n]) for n in ("Source Intake", "Triage/Verify", "Writer Agent", "Review Board")) == 193
    # 리치 필드
    card = writer[0]
    assert card["reviewer_findings"] and card["reviewer_findings"][0]["reviewer"]
    assert card["tracking_lane"] and card["next_action"] and card["verification_method"] == "registry_only"
    assert isinstance(card["tracker_tags"], list) and isinstance(card["persona_ids"], list)
    assert isinstance(card["content_completeness"], dict)
    # top-level 계약
    assert [p["persona_id"] for p in k["personas"]] == ["ma_lead", "brand_strategy", "policy_watch"]
    assert len(k["reviewer_roles"]) == 6 and all(r["required_checks"] for r in k["reviewer_roles"])
    assert "board_purpose" in k["operating_policy"]
    # runs[0] 계약
    run = k["runs"][0]
    assert run["quality_report"]["status"] == "quality_gated_draft"
    assert run["counts"]["selected"] == 6 and run["counts"]["discovered"] == 487
    assert len(run["draft_items"]) == 6 and run["draft_items"][0]["title"]
    # draft_items 는 writer 로 monitoring_point/work_note 가 보강됨 (이메일 브리프 인사이트)
    assert all(di.get("monitoring_point") and di.get("work_note") for di in run["draft_items"])
    assert run["dashboard_scope"]["keywords"]
    # 멱등 재적재
    assert sync_daily_mailing_runs(source_dir=src, db_path=db)["imported"] == 1
    k2 = load_admin_kanban(db_path=db)
    assert len(k2["runs"]) == 1 and len(k2["lanes"][3]["items"]) == 6
