from __future__ import annotations

from datetime import date

from agents.daily_mailing.brief import (
    BriefItem,
    build_subject,
    evaluate_brief_quality,
    format_date_label,
    render_brief_html,
    render_brief_markdown,
)


def _item(**kw):
    base = dict(title="파드셉 급여 확대", publisher_url="https://www.yakup.com/1", source_name="약업신문",
                published_at="2026-09-18T06:10:00+09:00", summary_bullets=("요약1", "요약2"),
                ma_implication="요로상피암 1차 급여 경쟁 구도 변화", evidence_quote="1차 치료로 급여 확대",
                quote_verified=True, body_status="ok", matched_scope_terms=("파드셉",), relevance_reason="주제")
    base.update(kw)
    return BriefItem(**base)


def test_round_trip_dict():
    item = _item()
    data = item.to_dict()
    assert data["summary_bullets"] == ["요약1", "요약2"]
    assert data["evidence_quotes"] == ["1차 치료로 급여 확대"]
    assert data["description"] == "요약1 요약2"
    assert data["work_note"] == "요로상피암 1차 급여 경쟁 구도 변화"
    assert BriefItem.from_dict(data) == item


def test_subject_and_date_label():
    d = date(2026, 9, 18)  # 금요일
    assert format_date_label(d) == "9/18(금)"
    assert build_subject("파드셉", d) == "[MA Daily] 파드셉 · 9/18(금)"
    assert build_subject("파드셉", d, is_test=True) == "[TEST] [MA Daily] 파드셉 · 9/18(금)"


def test_quality_ready():
    r = evaluate_brief_quality([_item()])
    assert (r.status, r.sendable, r.blocking_reasons) == ("ready", True, ())
    assert r.to_dict()["live_send_allowed"] is True


def test_quality_no_news_is_sendable():
    r = evaluate_brief_quality([_item(section="top_ma_news")])
    assert r.status == "no_news"
    assert r.sendable is True
    assert r.top_ma_news_count == 1


def test_quality_blocks_unverified_implication_duplicate_and_hold():
    bad = _item(quote_verified=False)
    assert "unverified_implication_rendered" in evaluate_brief_quality([bad]).blocking_reasons
    dup = evaluate_brief_quality([_item(), _item(publisher_url="https://www.yakup.com/2")])
    assert "duplicate_implication" in dup.blocking_reasons
    held = evaluate_brief_quality([_item()], hold_reasons=["llm_unavailable"])
    assert held.status == "held" and held.sendable is False
    assert "scope_item_without_match" in evaluate_brief_quality([_item(matched_scope_terms=())]).blocking_reasons


def test_quality_warns_on_snippet_items():
    r = evaluate_brief_quality([_item(body_status="snippet", ma_implication="", evidence_quote="", quote_verified=False)])
    assert r.status == "ready"
    assert "body_not_extracted" in r.warnings


def test_render_html_escapes_and_shows_sections():
    items = [_item(title="<b>파드셉</b>"),
             _item(title="약평위 결과", publisher_url="https://x/2", ma_implication="", evidence_quote="",
                   quote_verified=False, section="top_ma_news")]
    html = render_brief_html(name="파드셉", run_date=date(2026, 9, 18), scope_terms=["파드셉"], items=items)
    assert "&lt;b&gt;파드셉&lt;/b&gt;" in html
    assert "▶ MA 시사점" in html
    assert "근거: “1차 치료로 급여 확대”" in html
    assert "오늘의 주요 MA 뉴스" in html
    assert "특이사항 없음" not in html


def test_render_no_news_and_snippet_note():
    html = render_brief_html(name="파드셉", run_date=date(2026, 9, 18), scope_terms=["파드셉"], items=[])
    assert "특이사항 없음" in html
    snippet = _item(body_status="snippet", ma_implication="", evidence_quote="", quote_verified=False)
    html2 = render_brief_html(name="n", run_date=date(2026, 9, 18), scope_terms=["x"], items=[snippet])
    assert "본문 미확보" in html2
    assert "▶ MA 시사점" not in html2


def test_render_markdown():
    md = render_brief_markdown(name="파드셉", run_date=date(2026, 9, 18), scope_terms=["파드셉"], items=[_item()])
    assert md.startswith("# 파드셉 · 9/18(금)")
    assert "▶ MA 시사점: 요로상피암 1차 급여 경쟁 구도 변화" in md


def test_render_omits_links_for_non_http_urls():
    bad = _item(publisher_url="javascript:alert(1)", naver_url="")
    html = render_brief_html(name="n", run_date=date(2026, 9, 18), scope_terms=["x"], items=[bad])
    assert "javascript:" not in html
    assert "원문 보기" not in html
    assert "파드셉 급여 확대" in html
    md = render_brief_markdown(name="n", run_date=date(2026, 9, 18), scope_terms=["x"], items=[bad])
    assert "javascript:" not in md
    assert "파드셉 급여 확대" in md
    # publisher 가 비허용이어도 naver_url 이 http(s) 면 그쪽으로 링크
    fallback = _item(publisher_url="javascript:alert(1)", naver_url="https://n.news.naver.com/1")
    html2 = render_brief_html(name="n", run_date=date(2026, 9, 18), scope_terms=["x"], items=[fallback])
    assert 'href="https://n.news.naver.com/1"' in html2 and "javascript:" not in html2 and "원문 보기" in html2
    ok = render_brief_html(name="n", run_date=date(2026, 9, 18), scope_terms=["x"], items=[_item()])
    assert 'href="https://www.yakup.com/1"' in ok and "원문 보기" in ok


def test_published_label_date_only_for_midnight_archive_timestamp():
    from agents.daily_mailing.brief import _published_label

    assert _published_label("2026-09-18T00:00:00+09:00") == "9/18"      # 아카이브 합성 시각 → 날짜만
    assert _published_label("2026-09-18T06:10:00+09:00") == "9/18 06:10"
    assert _published_label("") == ""
    assert _published_label("garbage") == "garbage"
    md = render_brief_markdown(name="n", run_date=date(2026, 9, 18), scope_terms=["x"],
                               items=[_item(published_at="2026-09-18T00:00:00+09:00")])
    assert "약업신문 9/18\n" in md and "00:00" not in md


def test_quality_accepts_external_warnings_without_blocking():
    snippet = _item(body_status="snippet", ma_implication="", evidence_quote="", quote_verified=False)
    r = evaluate_brief_quality([snippet], warnings=["partial_relevance", "", "partial_relevance"])
    assert r.status == "ready" and r.sendable is True
    assert r.warnings == ("body_not_extracted", "partial_relevance")
    assert r.to_dict()["warnings"] == ["body_not_extracted", "partial_relevance"]
    assert r.blocking_reasons == ()
