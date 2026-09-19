"""Daily Mailing 브리핑 — 기사 항목(BriefItem) · 품질 게이트 · 메일 렌더.

설계 §5 ⑦⑧⑩.
- 스콥 기사 3~5건 + 하단 '오늘의 주요 MA 뉴스'(section='top_ma_news').
- MA 시사점은 본문 인용이 검증된 경우(quote_verified)에만 렌더한다.
- 스콥 기사 0건은 실패가 아니라 status='no_news' → '특이사항 없음' 메일.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from html import escape
from typing import Iterable
from urllib.parse import urlparse

WEEKDAYS_KR = "월화수목금토일"
LINK_SCHEMES = ("http", "https")


def _safe_link(*urls: str) -> str:
    """http(s) URL 만 링크로 렌더한다 (javascript:/data: 등 차단). 첫 허용 URL 을 돌려주고 없으면 ''."""
    for url in urls:
        value = (url or "").strip()
        if not value:
            continue
        try:
            scheme = urlparse(value).scheme.lower()
        except ValueError:
            continue
        if scheme in LINK_SCHEMES:
            return value
    return ""


@dataclass(frozen=True)
class BriefItem:
    title: str
    publisher_url: str
    naver_url: str = ""
    source_name: str = ""
    published_at: str = ""
    summary_bullets: tuple[str, ...] = ()
    ma_implication: str = ""
    evidence_quote: str = ""
    quote_verified: bool = False
    body_status: str = "snippet"                 # ok | snippet
    matched_scope_terms: tuple[str, ...] = ()
    relevance_reason: str = ""
    section: str = "scope"                        # scope | top_ma_news

    def to_dict(self) -> dict:
        data = asdict(self)
        data["summary_bullets"] = list(self.summary_bullets)
        data["matched_scope_terms"] = list(self.matched_scope_terms)
        # 칸반 DraftItem 호환 키 (frontend/src/api/dailyMailingKanban.ts)
        data["description"] = " ".join(self.summary_bullets)
        data["evidence_quotes"] = [self.evidence_quote] if self.evidence_quote else []
        data["monitoring_point"] = self.relevance_reason
        data["work_note"] = self.ma_implication
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "BriefItem":
        return cls(
            title=str(data.get("title") or ""),
            publisher_url=str(data.get("publisher_url") or ""),
            naver_url=str(data.get("naver_url") or ""),
            source_name=str(data.get("source_name") or ""),
            published_at=str(data.get("published_at") or ""),
            summary_bullets=tuple(str(x) for x in data.get("summary_bullets") or ()),
            ma_implication=str(data.get("ma_implication") or ""),
            evidence_quote=str(data.get("evidence_quote") or ""),
            quote_verified=bool(data.get("quote_verified")),
            body_status=str(data.get("body_status") or "snippet"),
            matched_scope_terms=tuple(str(x) for x in data.get("matched_scope_terms") or ()),
            relevance_reason=str(data.get("relevance_reason") or ""),
            section=str(data.get("section") or "scope"),
        )


@dataclass(frozen=True)
class BriefQualityReport:
    status: str                       # ready | no_news | held
    sendable: bool
    blocking_reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    total_articles: int
    top_ma_news_count: int

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "sendable": self.sendable,
            "live_send_allowed": self.sendable,
            "blocking_reasons": list(self.blocking_reasons),
            "warnings": list(self.warnings),
            "total_articles": self.total_articles,
            "top_ma_news_count": self.top_ma_news_count,
        }


def evaluate_brief_quality(items: Iterable[BriefItem], *, hold_reasons: Iterable[str] = (),
                           warnings: Iterable[str] = ()) -> BriefQualityReport:
    """hold_reasons 는 차단(held), warnings 는 비차단 경고(예: 엔진의 partial_relevance·naver_rate_limited)로 병합."""
    all_items = list(items)
    scope_items = [i for i in all_items if i.section == "scope"]
    top_items = [i for i in all_items if i.section == "top_ma_news"]
    blocking = [str(r) for r in hold_reasons if str(r)]
    warning_list: list[str] = [str(w) for w in warnings if str(w)]
    for item in all_items:
        if item.ma_implication and not item.quote_verified:
            blocking.append("unverified_implication_rendered")
        if not item.summary_bullets:
            blocking.append("empty_summary")
        if item.body_status != "ok":
            warning_list.append("body_not_extracted")
    for item in scope_items:
        if not item.matched_scope_terms:
            blocking.append("scope_item_without_match")
    implications = [i.ma_implication for i in all_items if i.ma_implication]
    if len(implications) != len(set(implications)):
        blocking.append("duplicate_implication")
    blocking_t = tuple(sorted(set(blocking)))
    warnings_t = tuple(sorted(set(warning_list)))
    if blocking_t:
        status = "held"
    elif not scope_items:
        status = "no_news"
    else:
        status = "ready"
    return BriefQualityReport(status, not blocking_t, blocking_t, warnings_t, len(scope_items), len(top_items))


def format_date_label(d: date) -> str:
    return f"{d.month}/{d.day}({WEEKDAYS_KR[d.weekday()]})"


def build_subject(name: str, run_date: date, *, is_test: bool = False) -> str:
    prefix = "[TEST] " if is_test else ""
    return f"{prefix}[MA Daily] {name} · {format_date_label(run_date)}"


def _published_label(value: str) -> str:
    try:
        dt = datetime.fromisoformat(value)
    except Exception:
        return (value or "")[:16]
    if (dt.hour, dt.minute, dt.second) == (0, 0, 0):
        return f"{dt.month}/{dt.day}"   # 아카이브 합성 시각(날짜만 있는 pub_date) → 시각 표시 생략
    return f"{dt.month}/{dt.day} {dt:%H:%M}"


def _split(items: Iterable[BriefItem]) -> tuple[list[BriefItem], list[BriefItem]]:
    items = list(items)
    return ([i for i in items if i.section == "scope"], [i for i in items if i.section == "top_ma_news"])


def _item_html(index: int, item: BriefItem) -> str:
    link = _safe_link(item.publisher_url, item.naver_url)
    url = escape(link, quote=True)
    title = escape(item.title)
    title_html = f'<a href="{url}" style="color:#0F172A;text-decoration:none;">{title}</a>' if link else title
    meta = " · ".join(x for x in (escape(item.source_name), escape(_published_label(item.published_at))) if x)
    bullets = "".join(f'<li style="margin:0 0 4px;">{escape(b)}</li>' for b in item.summary_bullets)
    parts = [
        '<div style="margin:0 0 20px;padding:0 0 16px;border-bottom:1px solid #E5E7EB;">',
        f'<div style="font-size:16px;font-weight:700;line-height:1.45;margin:0 0 4px;">{index}. {title_html}</div>',
        f'<div style="font-size:12px;color:#64748B;margin:0 0 8px;">{meta}</div>',
        f'<ul style="margin:0 0 8px 18px;padding:0;font-size:14px;line-height:1.6;color:#1F2937;">{bullets}</ul>',
    ]
    if item.ma_implication and item.quote_verified:
        parts.append(
            '<div style="font-size:14px;line-height:1.6;color:#0F172A;background:#F0FDF4;'
            'border-left:3px solid #16A34A;padding:8px 10px;margin:0 0 6px;">'
            f'<b>▶ MA 시사점</b> {escape(item.ma_implication)}</div>'
        )
        parts.append(f'<div style="font-size:12px;color:#6B7280;margin:0 0 6px;">근거: “{escape(item.evidence_quote)}”</div>')
    if item.body_status != "ok":
        parts.append('<div style="font-size:12px;color:#B45309;margin:0 0 6px;">본문 미확보 — 검색 요약 기반</div>')
    if link:
        parts.append(f'<div style="font-size:12px;"><a href="{url}" style="color:#2563EB;">원문 보기</a></div>')
    parts.append("</div>")
    return "".join(parts)


def render_brief_html(*, name: str, run_date: date, scope_terms: Iterable[str],
                      items: Iterable[BriefItem], window_label: str = "이전 24시간") -> str:
    scope_items, top_items = _split(items)
    if scope_items:
        body = "".join(_item_html(n, item) for n, item in enumerate(scope_items, 1))
    else:
        body = ('<p style="font-size:14px;color:#374151;padding:14px;background:#F9FAFB;border-radius:8px;">'
                f'{escape(window_label)} 동안 구독 스콥에 해당하는 기사가 없습니다 — <b>특이사항 없음</b></p>')
    top_html = ""
    if top_items:
        cards = "".join(_item_html(n, item) for n, item in enumerate(top_items, 1))
        top_html = ('<h2 style="font-size:15px;color:#0F172A;margin:28px 0 12px;padding-top:12px;'
                    f'border-top:2px solid #0F172A;">오늘의 주요 MA 뉴스</h2>{cards}')
    terms = escape(", ".join(scope_terms))
    return (
        '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        f'<title>{escape(build_subject(name, run_date))}</title></head>'
        '<body style="margin:0;padding:24px 12px;background:#FFFFFF;color:#0F172A;'
        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Malgun Gothic',sans-serif;\">"
        '<div style="max-width:640px;margin:0 auto;">'
        '<div style="font-size:12px;color:#2563EB;font-weight:700;letter-spacing:.04em;">MA DAILY BRIEFING</div>'
        f'<h1 style="font-size:20px;margin:4px 0 2px;">{escape(name)} · {escape(format_date_label(run_date))}</h1>'
        f'<div style="font-size:12px;color:#64748B;margin:0 0 20px;">{escape(window_label)} · 스콥: {terms}</div>'
        f'{body}{top_html}'
        '<div style="font-size:11px;color:#9CA3AF;margin-top:28px;line-height:1.6;">'
        'MA AI Dossier · 구독 설정에 따라 자동 생성된 메일입니다. '
        '본문 인용으로 확인되지 않은 MA 시사점은 표시하지 않습니다.</div>'
        '</div></body></html>'
    )


def _item_md(index: int, item: BriefItem) -> list[str]:
    link = _safe_link(item.publisher_url, item.naver_url)
    title = f"[{item.title}]({link})" if link else item.title
    head = f"{index}. {title} — {item.source_name} {_published_label(item.published_at)}"
    lines = [head.rstrip()]
    lines += [f"   - {b}" for b in item.summary_bullets]
    if item.ma_implication and item.quote_verified:
        lines.append(f"   - ▶ MA 시사점: {item.ma_implication}")
        lines.append(f"   - 근거: “{item.evidence_quote}”")
    if item.body_status != "ok":
        lines.append("   - (본문 미확보 — 검색 요약 기반)")
    lines.append("")
    return lines


def render_brief_markdown(*, name: str, run_date: date, scope_terms: Iterable[str],
                          items: Iterable[BriefItem], window_label: str = "이전 24시간") -> str:
    scope_items, top_items = _split(items)
    lines = [f"# {name} · {format_date_label(run_date)}", "", f"{window_label} · 스콥: {', '.join(scope_terms)}", ""]
    if not scope_items:
        lines += [f"{window_label} 동안 구독 스콥에 해당하는 기사가 없습니다 — 특이사항 없음", ""]
    for n, item in enumerate(scope_items, 1):
        lines += _item_md(n, item)
    if top_items:
        lines += ["## 오늘의 주요 MA 뉴스", ""]
        for n, item in enumerate(top_items, 1):
            lines += _item_md(n, item)
    return "\n".join(lines).rstrip() + "\n"
