"""Daily Mailing 콘텐츠 엔진 — 구독 스콥 1개 → 브리핑 run 1개.

설계: docs/superpowers/specs/2026-09-18-daily-mailing-service-design.md §5.
기존 파이프라인(discovery · rank_items · select_daily_items · build_article_card)을 재사용하고:
- 검색어 = 사용자 스콥만 (페르소나 기본 키워드는 '주요 MA 뉴스' 전용)
- 본문 추출 → LLM 관련성 판정 → LLM 작성 + 인용 검증
- MSD 대표 기사 강제 삽입 금지
DB 저장·발송은 하지 않는다 — 슬라이스 B(dispatch)가 RunResult 를 저장·발송한다.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, fields, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable

from .archive_candidates import load_archive_candidates
from .article_body import ArticleBody, fetch_article_body
from .brief import BriefItem, build_subject, evaluate_brief_quality, render_brief_html, render_brief_markdown
from .dashboard_scope import DashboardScope
from .discovery import (
    NaverNewsClient,
    NewsDiscoveryItem,
    canonicalize_url,
    discover_naver_news,
    filter_recent_items,
    rank_items,
    select_daily_items,
)
from .llm import JsonLLM, LLMError, OpenAIJsonLLM, RelevanceVerdict, judge_relevance, verify_quote, write_brief
from .personas import resolve_personas
from .review_board import build_article_card
from .source_registry import SourceRegistry

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY_PATH = REPO_ROOT / "config" / "source_registry.yaml"
TOP_MA_NEWS_PERSONAS = ("ma_lead", "policy_watch")


@dataclass(frozen=True)
class EngineConfig:
    relevance_model: str = "gpt-4o-mini"
    writer_model: str = "gpt-4o"
    max_candidates: int = 15
    max_naver_queries: int = 40
    brief_limit: int = 5
    top_ma_news_limit: int = 2
    naver_display: int = 20

    @classmethod
    def from_settings(cls, settings: dict | None) -> "EngineConfig":
        section = (settings or {}).get("daily_mailing") or {}
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in section.items() if k in names})


@dataclass
class EngineDeps:
    naver: NaverNewsClient
    llm: JsonLLM
    registry: SourceRegistry
    fetch_body: Callable[[str, str], ArticleBody] = fetch_article_body
    archive_db_path: str | None = None

    @classmethod
    def default(cls, *, db_path: str | None = None,
                registry_path: str | Path = DEFAULT_REGISTRY_PATH) -> "EngineDeps":
        return cls(naver=NaverNewsClient(), llm=OpenAIJsonLLM(),
                   registry=SourceRegistry.from_file(registry_path), archive_db_path=db_path)


@dataclass
class RunResult:
    run_id: str
    subscription_id: str
    run_date: str
    is_test: bool
    status: str
    subject: str
    html: str
    markdown: str
    items: list[BriefItem]
    top_ma_news: list[BriefItem]
    quality_report: dict
    payload: dict
    articles: list[dict]
    stats: dict = field(default_factory=dict)


@dataclass(frozen=True)
class _Judged:
    item: NewsDiscoveryItem
    body: ArticleBody
    verdict: RelevanceVerdict


def _now_kst(now: datetime | None) -> datetime:
    now = now or datetime.now(KST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=KST)
    return now.astimezone(KST)


def _key(item) -> str:
    return canonicalize_url(item.publisher_url or item.naver_url or item.title)


def _safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_") or "sub"


def _merge_unique(*groups: Iterable[NewsDiscoveryItem]) -> list[NewsDiscoveryItem]:
    out: list[NewsDiscoveryItem] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            key = _key(item)
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
    return out


def _collect_candidates(terms: list[str], *, media, lookback_hours: int, deps: EngineDeps,
                        config: EngineConfig, now: datetime, stats: dict) -> tuple[list[NewsDiscoveryItem], int]:
    naver_stats: dict = {}
    discovered = discover_naver_news(terms, client=deps.naver, registry=deps.registry,
                                     display=config.naver_display, media=media,
                                     max_queries=config.max_naver_queries, stats=naver_stats)
    recent = filter_recent_items(discovered, now=now, hours=lookback_hours)
    archive: list[NewsDiscoveryItem] = []
    if deps.archive_db_path:
        archive = load_archive_candidates(terms, db_path=deps.archive_db_path, today=now.date(),
                                          registry=deps.registry)
    merged = _merge_unique(recent, archive)
    stats["naver"] = naver_stats
    stats["discovered"] = len(discovered)
    stats["archive_candidates"] = len(archive)
    stats["recent"] = len(merged)
    return merged, len(discovered) + len(archive)


def _judge(candidates: list[NewsDiscoveryItem], terms: list[str], *, deps: EngineDeps,
           config: EngineConfig, stats: dict) -> list[_Judged]:
    judged: list[_Judged] = []
    errors = 0
    for item in candidates:
        body = deps.fetch_body(item.publisher_url, item.naver_url)
        text = body.text or item.description
        try:
            verdict = judge_relevance(title=item.title, text=text, scope_terms=terms,
                                      llm=deps.llm, model=config.relevance_model)
        except LLMError as exc:
            errors += 1
            logger.warning("[daily_mailing] 관련성 판정 실패 (%s): %s", item.title[:40], exc)
            continue
        judged.append(_Judged(item=item, body=body, verdict=verdict))
    stats["relevance_llm_errors"] = errors
    return judged


def _select(judged: list[_Judged], terms: list[str], *, media, personas, limit: int) -> list[_Judged]:
    relevant = [j for j in judged if j.verdict.relevant]
    by_key = {_key(j.item): j for j in relevant}
    items = [replace(j.item, matched_keywords=j.verdict.matched_scope_terms) for j in relevant]
    chosen = select_daily_items(items, terms, limit=limit, media=media, personas=personas,
                                force_msd_representative=False)
    return [by_key[_key(i)] for i in chosen if _key(i) in by_key]


def _write(j: _Judged, terms: list[str], *, deps: EngineDeps, config: EngineConfig,
           section: str, stats: dict) -> BriefItem | None:
    item, body, verdict = j.item, j.body, j.verdict
    common = dict(title=item.title, publisher_url=item.publisher_url, naver_url=item.naver_url,
                  source_name=item.source_name, published_at=item.published_at,
                  matched_scope_terms=verdict.matched_scope_terms, relevance_reason=verdict.reason,
                  section=section)
    snippet = (item.description,) if item.description else ()
    if body.status != "ok":
        return BriefItem(summary_bullets=snippet, body_status="snippet", **common)
    try:
        draft = write_brief(title=item.title, body=body.text, scope_terms=terms,
                            matched_scope_terms=verdict.matched_scope_terms,
                            llm=deps.llm, model=config.writer_model)
    except LLMError as exc:
        stats["writer_llm_errors"] = stats.get("writer_llm_errors", 0) + 1
        logger.warning("[daily_mailing] 브리핑 작성 실패 (%s): %s", item.title[:40], exc)
        return None
    verified = bool(draft.ma_implication) and verify_quote(draft.evidence_quote, body.text)
    if draft.ma_implication and not verified:
        stats["quote_rejected"] = stats.get("quote_rejected", 0) + 1
    return BriefItem(
        summary_bullets=draft.summary_bullets or snippet,
        ma_implication=draft.ma_implication if verified else "",
        evidence_quote=draft.evidence_quote if verified else "",
        quote_verified=verified,
        body_status="ok",
        **common,
    )


def _card(j: _Judged, *, brief: BriefItem | None, personas) -> dict:
    card = build_article_card(replace(j.item, matched_keywords=j.verdict.matched_scope_terms),
                              selected_for_draft=brief is not None, personas=personas)
    note = f"관련성 {'통과' if j.verdict.relevant else '제외'}: {j.verdict.reason}"
    if brief is not None and brief.quote_verified:
        note += f" | 인용 확인: “{brief.evidence_quote}”"
    card["reviewer_note"] = note
    card["matched_keywords"] = list(j.verdict.matched_scope_terms)
    card["verification_method"] = ("llm_relevance+quote_check" if brief is not None and brief.quote_verified
                                   else "llm_relevance")
    card["verification_caveat"] = f"본문 {j.body.status}" + (f" ({j.body.error})" if j.body.error else "")
    card["review_status"] = ("ready_for_writer" if brief is not None
                             else "needs_review" if j.verdict.relevant else "excluded")
    return card


def top_ma_news_terms() -> list[str]:
    terms: list[str] = []
    for persona in resolve_personas(list(TOP_MA_NEWS_PERSONAS)):
        for keyword in persona.default_keywords:
            if keyword not in terms:
                terms.append(keyword)
    return terms


def generate_top_ma_news(*, deps: EngineDeps, config: EngineConfig | None = None,
                         now: datetime | None = None) -> list[BriefItem]:
    """구독 공통 '오늘의 주요 MA 뉴스' 1~2건 (설계 §5.1). 페르소나(ma_lead·policy_watch) 기본 키워드 사용."""
    config = config or EngineConfig()
    now = _now_kst(now)
    terms = top_ma_news_terms()
    personas = resolve_personas(list(TOP_MA_NEWS_PERSONAS))
    stats: dict = {}
    candidates, _ = _collect_candidates(terms, media=(), lookback_hours=24, deps=deps,
                                        config=config, now=now, stats=stats)
    ranked = rank_items(candidates, terms, personas=personas)[: config.max_candidates]
    judged = _judge(ranked, terms, deps=deps, config=config, stats=stats)
    out: list[BriefItem] = []
    for j in _select(judged, terms, media=(), personas=personas, limit=config.top_ma_news_limit):
        written = _write(j, terms, deps=deps, config=config, section="top_ma_news", stats=stats)
        if written is not None:
            out.append(written)
    return out


def generate_run(scope: DashboardScope, *, deps: EngineDeps, config: EngineConfig | None = None,
                 name: str | None = None, now: datetime | None = None,
                 top_ma_news: list[BriefItem] | None = None, is_test: bool = False) -> RunResult:
    config = config or EngineConfig()
    now = _now_kst(now)
    run_date = now.date()
    sid = str(scope.subscription_id)
    display_name = name or sid
    terms = scope.expanded_keywords()
    personas = resolve_personas(list(scope.personas))
    stats: dict = {"scope_terms": list(terms)}
    hold: list[str] = []
    judged: list[_Judged] = []
    scope_items: list[BriefItem] = []
    brief_by_key: dict[str, BriefItem] = {}
    discovered_count = 0

    if not terms:
        hold.append("empty_scope")
    else:
        candidates, discovered_count = _collect_candidates(
            terms, media=scope.media, lookback_hours=scope.lookback_hours, deps=deps,
            config=config, now=now, stats=stats)
        ranked = rank_items(candidates, terms, media=scope.media, personas=personas)[: config.max_candidates]
        judged = _judge(ranked, terms, deps=deps, config=config, stats=stats)
        if ranked and not judged:
            hold.append("llm_unavailable")
        for j in _select(judged, terms, media=scope.media, personas=personas, limit=config.brief_limit):
            written = _write(j, terms, deps=deps, config=config, section="scope", stats=stats)
            if written is not None:
                scope_items.append(written)
                brief_by_key[_key(j.item)] = written

    top: list[BriefItem] = []
    if scope.include_top_ma_signals and top_ma_news:
        taken = {canonicalize_url(i.publisher_url) for i in scope_items}
        top = [replace(i, section="top_ma_news") for i in top_ma_news
               if canonicalize_url(i.publisher_url) not in taken][: config.top_ma_news_limit]

    all_items = scope_items + top
    quality = evaluate_brief_quality(all_items, hold_reasons=hold)
    window_label = f"이전 {scope.lookback_hours}시간"
    subject = build_subject(display_name, run_date, is_test=is_test)
    html = render_brief_html(name=display_name, run_date=run_date, scope_terms=terms,
                             items=all_items, window_label=window_label)
    markdown = render_brief_markdown(name=display_name, run_date=run_date, scope_terms=terms,
                                     items=all_items, window_label=window_label)
    run_id = f"dm_{_safe_id(sid)}_{now:%Y%m%d_%H%M%S}" + ("_test" if is_test else "")
    articles = [_card(j, brief=brief_by_key.get(_key(j.item)), personas=personas) for j in judged]
    payload = {
        "run_id": run_id,
        "generated_at": now.isoformat(),
        "window_label": window_label,
        "lookback_hours": scope.lookback_hours,
        "keywords": list(terms),
        "media": list(scope.media),
        "subscription_id": int(sid) if sid.isdigit() else None,
        "owner_email": scope.owner_email,
        "recipients": list(scope.recipients),
        "discovered_count": discovered_count,
        "recent_count": stats.get("recent", 0),
        "selected_count": len(scope_items),
        "status": quality.status,
        "quality_report": quality.to_dict(),
        "personas": list(scope.personas),
        "items": [i.to_dict() for i in all_items],
        "dashboard_scope": scope.to_dict(),
        "run_date": run_date.isoformat(),
        "is_test": is_test,
        "email_subject": subject,
        "email_html": html,
        "delivery_status": "draft_only",
        "approval_status": "not_requested",
    }
    return RunResult(run_id=run_id, subscription_id=sid, run_date=run_date.isoformat(), is_test=is_test,
                     status=quality.status, subject=subject, html=html, markdown=markdown,
                     items=scope_items, top_ma_news=top, quality_report=quality.to_dict(),
                     payload=payload, articles=articles, stats=stats)
