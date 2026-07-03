from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
import html
import json
import os
from typing import Callable, Iterable
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl
from urllib.request import Request, urlopen

from .source_registry import SourceInfo, SourceRegistry
from .personas import DailyMailingPersona

DEFAULT_KEYWORDS = [
    "키트루다 급여",
    "약평위",
    "암질심",
    "약가유연계약",
    "위험분담",
    "사용량 약가",
    "항암제 급여",
    "MSD",
    "MSD 키트루다",
    "MSD 가다실",
    "MSD 린파자",
    "MSD 웰리렉",
]

COMPETITOR_WATCH_KEYWORDS = [
    "Padcev",
    "ADC 항암제",
    "PD-1 VEGF",
    "PD-1 ADC",
    "면역항암제 병용 경쟁",
]


MEDIA_SOURCE_ALIASES = {
    "medi": {"메디칼타임즈"},
    "medicaltimes": {"메디칼타임즈"},
    "doctorsnews": {"청년의사"},
    "docdocdoc": {"청년의사"},
    "medigate": {"메디게이트뉴스"},
    "yakup": {"약업신문"},
    "kpanews": {"약사공론", "한국제약바이오협회"},
    "hitnews": {"히트뉴스", "HIT뉴스"},
    "dailypharm": {"데일리팜"},
    "medipana": {"메디파나뉴스"},
    "newsthevoice": {"뉴스더보이스"},
    "pharmnews": {"팜뉴스"},
    "naver": {"Naver News API"},
}

def normalize_media_scope(media: Iterable[str] | None) -> set[str]:
    """Map dashboard media IDs/labels to registry source names. Empty means all registered sources."""
    scope: set[str] = set()
    for raw in media or []:
        value = str(raw).strip()
        if not value:
            continue
        scope.add(value)
        scope.update(MEDIA_SOURCE_ALIASES.get(value, set()))
    return scope

def source_allowed_by_media(source: SourceInfo | None, media_scope: set[str]) -> bool:
    if not media_scope:
        return True
    if source is None:
        return False
    # Official sources remain eligible even when a narrower media set is selected.
    if source.source_tier in {"official", "official_payer", "regulator"}:
        return True
    names = {source.name, source.name.lower()}
    return bool(names.intersection(media_scope) or {m.lower() for m in media_scope}.intersection(names))

def source_matches_media(source: SourceInfo | None, media_scope: set[str]) -> bool:
    if not source or not media_scope:
        return False
    names = {source.name, source.name.lower()}
    lowered_scope = {m.lower() for m in media_scope}
    return bool(names.intersection(media_scope) or lowered_scope.intersection(names))

MSD_REPRESENTATIVE_TERMS = (
    "msd", "엠에스디", "한국MSD", "한국엠에스디",
    "키트루다", "keytruda", "가다실", "gardasil", "린파자", "lynparza",
    "웰리렉", "welireg", "브리디온", "bridion", "제파티어", "zepatier",
)

TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid"}


@dataclass(frozen=True)
class NewsDiscoveryItem:
    title: str
    publisher_url: str
    naver_url: str
    description: str
    published_at: str
    keyword: str
    discovery_channel: str
    source_name: str
    source_tier: str
    source_weight: float
    ma_depth: int
    novelty: int
    volume: int
    matched_keywords: tuple[str, ...] = ()
    score: float = 0.0


def canonicalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    query = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if not (k.lower().startswith("utm_") or k.lower() in TRACKING_PARAMS)
    ]
    return urlunparse((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        parsed.path.rstrip("/") or "/",
        "",
        urlencode(query, doseq=True),
        "",
    ))


def _clean_html_text(text: str) -> str:
    return html.unescape(text or "").replace("<b>", "").replace("</b>", "").strip()


class NaverNewsClient:
    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        opener: Callable = urlopen,
    ):
        self.client_id = client_id or os.environ.get("NAVER_API_CLIENT_ID")
        self.client_secret = client_secret or os.environ.get("NAVER_API_CLIENT_SECRET")
        self.opener = opener
        if not self.client_id or not self.client_secret:
            raise RuntimeError("NAVER_API_CLIENT_ID and NAVER_API_CLIENT_SECRET are required")

    def search(self, query: str, display: int = 20, sort: str = "date") -> list[dict]:
        params = urlencode({"query": query, "display": display, "sort": sort})
        req = Request(
            f"https://openapi.naver.com/v1/search/news.json?{params}",
            headers={
                "X-Naver-Client-Id": str(self.client_id),
                "X-Naver-Client-Secret": str(self.client_secret),
            },
        )
        with self.opener(req, timeout=15) as response:
            return json.loads(response.read().decode("utf-8")).get("items", [])


def _iso_pub_date(pub_date: str) -> str:
    if not pub_date:
        return ""
    try:
        return parsedate_to_datetime(pub_date).isoformat()
    except Exception:
        return pub_date


def discover_naver_news(
    keywords: Iterable[str] = DEFAULT_KEYWORDS,
    *,
    client: NaverNewsClient | None = None,
    registry: SourceRegistry | None = None,
    registry_path: str = "/opt/data/ma_ingest/config/source_registry.yaml",
    display: int = 20,
    media: Iterable[str] | None = None,
    media_strategy: str = "boost",
) -> list[NewsDiscoveryItem]:
    client = client or NaverNewsClient()
    registry = registry or SourceRegistry.from_file(registry_path)
    items: list[NewsDiscoveryItem] = []
    seen: set[str] = set()
    media_scope = normalize_media_scope(media)
    for keyword in keywords:
        for raw in client.search(keyword, display=display, sort="date"):
            publisher_url = canonicalize_url(raw.get("originallink") or raw.get("link") or "")
            if not publisher_url or publisher_url in seen:
                continue
            seen.add(publisher_url)
            source = registry.match_url(publisher_url) or registry.match_url(raw.get("link", ""))
            if source and "calibration_reference" in source.use_for:
                continue
            if media_strategy == "strict" and not source_allowed_by_media(source, media_scope):
                continue
            items.append(NewsDiscoveryItem(
                title=_clean_html_text(raw.get("title", "")),
                publisher_url=publisher_url,
                naver_url=raw.get("link", ""),
                description=_clean_html_text(raw.get("description", "")),
                published_at=_iso_pub_date(raw.get("pubDate", "")),
                keyword=keyword,
                discovery_channel="naver_news_api",
                source_name=source.name if source else "Unknown publisher",
                source_tier=source.source_tier if source else "unregistered",
                source_weight=source.weight if source else 1.0,
                ma_depth=source.ma_depth or 0 if source else 0,
                novelty=source.novelty or 0 if source else 0,
                volume=source.volume or 0 if source else 0,
            ))
    return items


def filter_recent_items(
    items: Iterable[NewsDiscoveryItem],
    *,
    now: str | datetime | None = None,
    hours: int = 24,
) -> list[NewsDiscoveryItem]:
    """Keep items published within the previous N hours."""
    if now is None:
        now_dt = datetime.now().astimezone()
    elif isinstance(now, str):
        now_dt = datetime.fromisoformat(now)
    else:
        now_dt = now
    if now_dt.tzinfo is None:
        now_dt = now_dt.astimezone()
    cutoff = now_dt - timedelta(hours=hours)
    recent: list[NewsDiscoveryItem] = []
    for item in items:
        try:
            published = datetime.fromisoformat(item.published_at)
        except Exception:
            continue
        if published.tzinfo is None:
            published = published.astimezone()
        if cutoff <= published <= now_dt:
            recent.append(item)
    return recent


def expand_keywords_for_personas(
    keywords: Iterable[str],
    personas: Iterable[DailyMailingPersona] | None = None,
) -> list[str]:
    """Append persona default keywords while preserving dashboard/user keywords."""
    expanded: list[str] = []
    for keyword in keywords:
        value = str(keyword).strip()
        if value and value not in expanded:
            expanded.append(value)
    for persona in personas or []:
        for keyword in getattr(persona, "default_keywords", ()):  # defensive for tests/callers
            value = str(keyword).strip()
            if value and value not in expanded:
                expanded.append(value)
    return expanded


def rank_items(
    items: Iterable[NewsDiscoveryItem],
    keywords: Iterable[str],
    media: Iterable[str] | None = None,
    personas: Iterable[DailyMailingPersona] | None = None,
) -> list[NewsDiscoveryItem]:
    keyword_list = [k.strip() for k in keywords if k.strip()]
    ranked: list[NewsDiscoveryItem] = []
    media_scope = normalize_media_scope(media)
    for item in items:
        haystack = f"{item.title} {item.description}".lower()
        matched = tuple(k for k in keyword_list if k.lower() in haystack)
        score = item.source_weight + (item.ma_depth * 0.25) + (item.novelty * 0.15) + (len(matched) * 0.7)
        if any(token in haystack for token in ["급여", "약가", "약평위", "암질심", "위험분담", "rsa"]):
            score += 1.0
        if is_msd_representative_item(item):
            score += 1.2
        source_info = SourceInfo(
            name=item.source_name, domains=(), source_tier=item.source_tier, weight=item.source_weight
        )
        if source_matches_media(source_info, media_scope):
            score += 0.8
        for persona in personas or []:
            priority_terms = [str(term).lower() for term in getattr(persona, "priority_terms", ())]
            watch_terms = [str(term).lower() for term in getattr(persona, "watch_terms", ())]
            if any(term in haystack for term in priority_terms):
                score += 0.8
            elif any(term in haystack for term in watch_terms):
                score += 0.3
        ranked.append(replace(item, matched_keywords=matched, score=round(score, 3)))
    return sorted(ranked, key=lambda x: (x.score, x.published_at), reverse=True)


def is_msd_representative_item(item: NewsDiscoveryItem) -> bool:
    haystack = f"{item.title} {item.description}".lower()
    return any(term.lower() in haystack for term in MSD_REPRESENTATIVE_TERMS)


def _editorial_theme_key(item: NewsDiscoveryItem) -> str:
    """Coarse daily briefing theme key to avoid spending multiple slots on same story wave."""
    text = f"{item.title} {item.description}".lower()
    if ("100일" in text or "100 day" in text) and ("희귀" in text or "rare" in text):
        return "rare-disease-100-day-listing"
    if any(t in text for t in ("엑스코프리", "팁소보", "스프라바토", "옥스루모", "넴루비오")) and any(t in text for t in ("약평위", "급여", "적정성")):
        return "hira-drec-2026-7th-new-drug-reimbursement-results"
    if "린파자" in text and ("난소암" in text or "hrd" in text):
        return "lynparza-hrd-ovarian-reimbursement"
    if "베오바" in text or "vibegron" in text:
        return "beova-price-negotiation"
    if any(t in text for t in ("구강붕해정", "odt")) and any(t in text for t in ("로수바", "에제", "ezetimibe")):
        return "rosuva-eze-odt-formulation"
    return canonicalize_url(item.publisher_url) or item.title[:80]


def select_daily_items(
    items: Iterable[NewsDiscoveryItem],
    keywords: Iterable[str],
    limit: int = 5,
    media: Iterable[str] | None = None,
    personas: Iterable[DailyMailingPersona] | None = None,
) -> list[NewsDiscoveryItem]:
    """Rank items for a monitoring-first daily newsletter.

    User-selected keyword/company/brand matches are the primary inclusion signal.
    MA relevance still boosts ordering and controls whether a Market Access note is
    written, but low-MA MSD/product monitoring items must not disappear merely
    because they are not reimbursement signals.
    """
    ranked = rank_items(items, keywords, media=media, personas=personas)
    try:
        from .writer import should_include_in_top_signals
    except Exception:  # pragma: no cover - keep discovery usable if writer import fails
        should_include_in_top_signals = lambda item: True

    top_signal_candidates = [item for item in ranked if should_include_in_top_signals(item)]
    watch_candidates = [item for item in ranked if item not in top_signal_candidates]

    selected: list[NewsDiscoveryItem] = []
    seen_themes: set[str] = set()
    for item in top_signal_candidates + watch_candidates:
        theme = _editorial_theme_key(item)
        if theme in seen_themes:
            continue
        selected.append(item)
        seen_themes.add(theme)
        if len(selected) >= limit:
            break

    if any(is_msd_representative_item(item) for item in selected):
        return selected
    representative = next((item for item in ranked if is_msd_representative_item(item)), None)
    if representative is None:
        return selected
    if len(selected) < limit:
        return selected + [representative]
    return selected[:-1] + [representative]
