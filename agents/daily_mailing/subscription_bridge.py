"""mail_subscription 행 → dashboard_scope JSON 브릿지.

대쉬보드 UI 에서 정한 모니터링 스콥을 daily-monitoring 레포의 dashboard_scope 계약으로
변환한다. 외부 헤르메스(GPT-5.5) 에이전트가 이 JSON 을 읽어 검토·작성·매일 발송한다.
(대쉬보드는 발송하지 않는다. 정책 인텔리전스 헤르메스 모델과 동일한 '입력 스냅샷' 역할.)
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_PERSONAS = ["ma_lead", "brand_strategy", "policy_watch"]


def _default_scope_root() -> Path:
    configured = os.environ.get("DAILY_MAILING_SCOPE_ROOT")
    if configured:
        return Path(configured)
    if Path("/app/data").exists():
        return Path("/app/data/daily_mailing/scopes")
    return Path(__file__).resolve().parents[2] / "data" / "daily_mailing" / "scopes"


def ensure_test_request_column(conn) -> None:
    """mail_subscription.test_request_json 멱등 ALTER (기존 스콥 확장 마이그레이션과 동일 사상).

    테스트 메일 요청은 스냅샷 파일이 아니라 **행에 지속**되어야 스콥 export 가 항상 반영한다.
    (헤르메스가 [TEST] run 을 커밋하면 inbound run-sync 가 이 컬럼을 소비/해제한다.)
    """
    try:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(mail_subscription)").fetchall()}
    except Exception:
        return
    if existing and "test_request_json" not in existing:
        conn.execute("ALTER TABLE mail_subscription ADD COLUMN test_request_json TEXT")
        conn.commit()


def subscription_to_scope(sub: dict) -> dict:
    """mail_subscription dict(_mail_sub_row_to_dict 산출) → dashboard_scope dict.

    현재 subscription 스키마엔 keywords/media/emails 만 있으므로 그것을 매핑하고,
    brands/companies/aliases/policy_topics 등 확장 필드는 있으면 반영, 없으면 빈 값.
    test_request(테스트 메일 요청 플래그) 는 행에 지속된 값이 있을 때만 포함.
    """
    scope = {
        "subscription_id": str(sub.get("id") or sub.get("name") or "default"),
        "name": sub.get("name"),
        "owner_email": sub.get("owner_email"),
        "recipients": list(sub.get("emails") or []),
        "keywords": list(sub.get("keywords") or []),
        "companies": list(sub.get("companies") or []),
        "brands": list(sub.get("brands") or []),
        "aliases": sub.get("aliases") or {},
        "disease_areas": list(sub.get("disease_areas") or []),
        "policy_topics": list(sub.get("policy_topics") or []),
        "media": list(sub.get("media") or []),
        "custom_sources": list(sub.get("custom_sources") or []),
        "personas": list(sub.get("personas") or DEFAULT_PERSONAS),
        "lookback_hours": int(sub.get("lookback_hours") or 24),
        "delivery_mode": sub.get("delivery_mode") or "gmail_draft",
        "schedule": sub.get("schedule"),
        "time": sub.get("time"),
        "week_day": sub.get("week_day"),
        "active": bool(sub.get("active", True)),
        "include_top_ma_signals": True,
        "include_user_keyword_watchlist": True,
    }
    test_request = sub.get("test_request")
    if test_request:
        scope["test_request"] = test_request
    return scope


def safe_scope_filename(subscription_id) -> str:
    """subscription_id → 채널 파일명 `<safe_id>.json` (로컬/원격 동일 규칙)."""
    sid = str(subscription_id or "default")
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in sid)[:80] or "default"
    return f"{safe}.json"


def write_scope_snapshot(scope: dict, root: str | Path | None = None) -> Path:
    """scope JSON 을 헤르메스가 읽는 위치(<root>/<subscription_id>.json)에 저장.

    이 디렉토리가 헤르메스 채널(비공개 git / prod 볼륨)로 동기화되어 헤르메스가 소비한다.
    """
    root_path = Path(root) if root is not None else _default_scope_root()
    root_path.mkdir(parents=True, exist_ok=True)
    path = root_path / safe_scope_filename(scope.get("subscription_id"))
    path.write_text(json.dumps(scope, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
