from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DashboardScope:
    """User-selected MA Dashboard monitoring scope snapshot.

    This is intentionally JSON-first so AI Dashboard can write a simple file and
    the morning mailing job can consume the exact user choices used for the run.
    """

    subscription_id: str = "default"
    owner_email: str | None = None
    recipients: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    companies: tuple[str, ...] = ()
    brands: tuple[str, ...] = ()
    aliases: dict[str, tuple[str, ...]] = field(default_factory=dict)
    disease_areas: tuple[str, ...] = ()
    policy_topics: tuple[str, ...] = ()
    media: tuple[str, ...] = ()
    personas: tuple[str, ...] = ("ma_lead", "brand_strategy", "policy_watch")
    lookback_hours: int = 24
    delivery_mode: str = "gmail_draft"
    include_top_ma_signals: bool = True
    include_user_keyword_watchlist: bool = True

    def expanded_keywords(self) -> list[str]:
        ordered: list[str] = []
        for bucket in (self.keywords, self.companies, self.brands, self.disease_areas, self.policy_topics):
            for item in bucket:
                _append_unique(ordered, item)
        for canonical, vals in self.aliases.items():
            _append_unique(ordered, canonical)
            for alias in vals:
                _append_unique(ordered, alias)
        return ordered

    def to_dict(self) -> dict[str, Any]:
        return {
            "subscription_id": self.subscription_id,
            "owner_email": self.owner_email,
            "recipients": list(self.recipients),
            "keywords": list(self.keywords),
            "companies": list(self.companies),
            "brands": list(self.brands),
            "aliases": {k: list(v) for k, v in self.aliases.items()},
            "disease_areas": list(self.disease_areas),
            "policy_topics": list(self.policy_topics),
            "media": list(self.media),
            "personas": list(self.personas),
            "lookback_hours": self.lookback_hours,
            "delivery_mode": self.delivery_mode,
            "include_top_ma_signals": self.include_top_ma_signals,
            "include_user_keyword_watchlist": self.include_user_keyword_watchlist,
        }


def _append_unique(out: list[str], value: str | None) -> None:
    value = (value or "").strip()
    if value and value not in out:
        out.append(value)


def _tuple(data: dict[str, Any], key: str) -> tuple[str, ...]:
    raw = data.get(key) or []
    if isinstance(raw, str):
        raw = [raw]
    return tuple(str(x).strip() for x in raw if str(x).strip())


def load_dashboard_scope(path: str | Path) -> DashboardScope:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    aliases_raw = data.get("aliases") or {}
    aliases: dict[str, tuple[str, ...]] = {}
    for key, val in aliases_raw.items():
        aliases[str(key)] = tuple(str(x).strip() for x in (val if isinstance(val, list) else [val]) if str(x).strip())
    return DashboardScope(
        subscription_id=str(data.get("subscription_id") or "default"),
        owner_email=data.get("owner_email"),
        recipients=_tuple(data, "recipients"),
        keywords=_tuple(data, "keywords"),
        companies=_tuple(data, "companies"),
        brands=_tuple(data, "brands"),
        aliases=aliases,
        disease_areas=_tuple(data, "disease_areas"),
        policy_topics=_tuple(data, "policy_topics"),
        media=_tuple(data, "media"),
        personas=_tuple(data, "personas") or ("ma_lead", "brand_strategy", "policy_watch"),
        lookback_hours=int(data.get("lookback_hours") or 24),
        delivery_mode=str(data.get("delivery_mode") or "gmail_draft"),
        include_top_ma_signals=bool(data.get("include_top_ma_signals", True)),
        include_user_keyword_watchlist=bool(data.get("include_user_keyword_watchlist", True)),
    )
