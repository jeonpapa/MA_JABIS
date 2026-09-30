"""Daily Mailing 브리핑 — 기사 항목(BriefItem) · 품질 게이트 · 메일 렌더.

설계 §5 ⑦⑧⑩.
- 스콥 기사 3~5건 + 하단 '오늘의 주요 MA 뉴스'(section='top_ma_news').
- MA 시사점은 본문 인용이 검증된 경우(quote_verified)에만 렌더한다.
- 스콥 기사 0건은 실패가 아니라 status='no_news' → '특이사항 없음' 메일.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date, datetime
from html import escape
from typing import Iterable
from urllib.parse import urlparse

WEEKDAYS_KR = "월화수목금토일"
LINK_SCHEMES = ("http", "https")
# 매체명이 아닌 자리표시자 — 메일에는 원문 URL 의 등록 도메인으로 대체한다 (2026-09-19 파일럿 리뷰)
PLACEHOLDER_SOURCE_NAMES = frozenset({"", "unknown publisher", "naver news api"})
NON_PUBLISHER_TIERS = frozenset({"search_channel"})
# 2단계 공용 접미사 — 등록 도메인은 접미사 앞 라벨까지 (docdocdoc.co.kr)
MULTI_LABEL_SUFFIXES = frozenset({"co.kr", "or.kr", "go.kr", "ne.kr", "re.kr", "ac.kr", "pe.kr",
                                  "co.jp", "ne.jp", "or.jp", "co.uk", "org.uk", "ac.uk",
                                  "com.au", "com.cn", "com.tw", "com.sg"})
# 네이버 description 은 키워드 중심 발췌라 비연속 조각을 '...' 로 잇고(아카이브 400건 중 257건) 끝을 '...' 로 자른다.
# 점 2개 이상 또는 '…' 의 연속 = 발췌 이음표. '있다....' 처럼 점 4개 이상이면 문장 끝 마침표 + 이음표로 본다.
_ELLIPSIS_RUN = re.compile(r"\s*(?:\.{2,}|…)+\s*")
_JOINER = " … "
_JOINER_TAIL = re.compile(r"\s*…\s*$")
_SENTENCE_END = re.compile(r"[.!?。](?=\s|$)")
MIN_SENTENCE_KEEP_RATIO = 0.3   # 잘린 스니펫을 문장 경계에서 자를 때 남는 길이의 최소 비율


def registrable_domain(url: str) -> str:
    """URL 의 등록 도메인 (www.newsmp.com → newsmp.com, m.docdocdoc.co.kr → docdocdoc.co.kr). 파싱 불가·호스트 없음 → ''."""
    try:
        host = (urlparse((url or "").strip()).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""
    labels = [x for x in host.split(".") if x]
    if len(labels) < 2:
        return ""
    if len(labels) >= 3 and ".".join(labels[-2:]) in MULTI_LABEL_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def publisher_display_name(source_name: str, publisher_url: str, *, source_tier: str = "") -> str:
    """메일 표시용 매체명. 레지스트리·아카이브 이름은 유지하고, 자리표시자('Unknown publisher'·'Naver News API')
    나 검색 채널 tier 면 원문 URL 의 등록 도메인으로 대체한다. 도메인도 없으면 ''."""
    name = (source_name or "").strip()
    if name.lower() not in PLACEHOLDER_SOURCE_NAMES and source_tier not in NON_PUBLISHER_TIERS:
        return name
    return registrable_domain(publisher_url)


def _normalize_joiner(match: re.Match) -> str:
    run = match.group(0).strip()
    return "." + _JOINER if "…" not in run and run.count(".") >= 4 else _JOINER


def clean_snippet(text: str) -> str:
    """검색 API 스니펫을 한 줄로 정리. 결과는 항상 원문의 시작에서 시작한다 (머리글을 잃지 않는다).
    ① 공백 정규화 ② 인라인 말줄임(...)은 문장 끝이 아니라 발췌 이음표 ' … ' 로 통일 ③ 꼬리 말줄임은 뗀다
    ④ 꼬리가 잘린 경우에만, 마지막 '진짜' 문장 끝이 충분히 뒤(MIN_SENTENCE_KEEP_RATIO 이상)에 있으면 뒤 조각을 자른다.
    이음표에서 자르면 머리 조각만 남거나 '...' 로 끝나는 줄이 되므로(2026-09-30 파일럿) 이음표는 절단 후보가 아니다."""
    value = re.sub(r"\s+", " ", text or "").strip()
    if not value:
        return ""
    joined = _ELLIPSIS_RUN.sub(_normalize_joiner, value).strip()
    stripped = _JOINER_TAIL.sub("", joined).strip()
    if not stripped or stripped == joined:
        return stripped                     # 꼬리가 잘리지 않은 스니펫은 자르지 않는다
    ends = [m.end() for m in _SENTENCE_END.finditer(stripped)]
    if ends and ends[-1] >= len(stripped) * MIN_SENTENCE_KEEP_RATIO and ends[-1] < len(stripped):
        return stripped[: ends[-1]].rstrip()
    return stripped


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
    # 같은 사건을 보도한 다른 매체 (클러스터링으로 흡수된 기사): ({"source_name": ..., "url": ...}, ...)
    also_reported_by: tuple[dict, ...] = ()

    def to_dict(self) -> dict:
        data = asdict(self)
        data["summary_bullets"] = list(self.summary_bullets)
        data["matched_scope_terms"] = list(self.matched_scope_terms)
        data["also_reported_by"] = [dict(e) for e in self.also_reported_by]
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
            also_reported_by=tuple(
                {"source_name": str(e.get("source_name") or ""), "url": str(e.get("url") or "")}
                for e in (data.get("also_reported_by") or ()) if isinstance(e, dict)),
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


def _also_reported_label(item: BriefItem) -> str:
    return f"외 {len(item.also_reported_by)}개 매체: "


def _also_reported_html(item: BriefItem) -> str:
    """'외 2개 매체: 데일리팜, 약업신문' — 매체명은 http(s) URL 이 있을 때만 링크."""
    names = []
    for entry in item.also_reported_by:
        name = escape(str(entry.get("source_name") or entry.get("url") or ""))
        href = _safe_link(str(entry.get("url") or ""))
        names.append(f'<a href="{escape(href, quote=True)}" style="color:#64748B;">{name}</a>' if href else name)
    return escape(_also_reported_label(item)) + ", ".join(names)


def _also_reported_md(item: BriefItem) -> str:
    names = []
    for entry in item.also_reported_by:
        name = str(entry.get("source_name") or entry.get("url") or "")
        href = _safe_link(str(entry.get("url") or ""))
        names.append(f"[{name}]({href})" if href else name)
    return _also_reported_label(item) + ", ".join(names)


def _snippet_line(item: BriefItem) -> str:
    return clean_snippet(" ".join(item.summary_bullets))


def _item_html(index: int, item: BriefItem) -> str:
    link = _safe_link(item.publisher_url, item.naver_url)
    url = escape(link, quote=True)
    title = escape(item.title)
    title_html = f'<a href="{url}" style="color:#0F172A;text-decoration:none;">{title}</a>' if link else title
    meta = " · ".join(x for x in (escape(item.source_name), escape(_published_label(item.published_at))) if x)
    if item.body_status == "ok":
        bullets = "".join(f'<li style="margin:0 0 4px;">{escape(b)}</li>' for b in item.summary_bullets)
        summary_html = f'<ul style="margin:0 0 8px 18px;padding:0;font-size:14px;line-height:1.6;color:#1F2937;">{bullets}</ul>'
    else:
        # 본문 미확보: 검색 스니펫 한 줄 (불릿 아님)
        summary_html = ('<div style="font-size:14px;line-height:1.6;color:#1F2937;margin:0 0 8px;">'
                        f'검색 요약: {escape(_snippet_line(item))}</div>')
    parts = [
        '<div style="margin:0 0 20px;padding:0 0 16px;border-bottom:1px solid #E5E7EB;">',
        f'<div style="font-size:16px;font-weight:700;line-height:1.45;margin:0 0 4px;">{index}. {title_html}</div>',
        f'<div style="font-size:12px;color:#64748B;margin:0 0 8px;">{meta}</div>',
        summary_html,
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
    if item.also_reported_by:
        parts.append(f'<div style="font-size:12px;color:#64748B;margin:0 0 6px;">{_also_reported_html(item)}</div>')
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
    meta = " ".join(x for x in (item.source_name, _published_label(item.published_at)) if x)
    head = f"{index}. {title} — {meta}" if meta else f"{index}. {title}"
    lines = [head.rstrip()]
    if item.body_status == "ok":
        lines += [f"   - {b}" for b in item.summary_bullets]
    else:
        lines.append(f"   - 검색 요약: {_snippet_line(item)}")
    if item.ma_implication and item.quote_verified:
        lines.append(f"   - ▶ MA 시사점: {item.ma_implication}")
        lines.append(f"   - 근거: “{item.evidence_quote}”")
    if item.body_status != "ok":
        lines.append("   - (본문 미확보 — 검색 요약 기반)")
    if item.also_reported_by:
        lines.append(f"   - {_also_reported_md(item)}")
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
