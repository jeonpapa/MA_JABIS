from __future__ import annotations

import re
from datetime import date

import pytest

from agents.daily_mailing.brief import (
    BriefItem,
    build_subject,
    clean_snippet,
    evaluate_brief_quality,
    format_date_label,
    publisher_display_name,
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
    assert data["impact"] == "" and data["affected_asset"] == ""
    assert BriefItem.from_dict(data) == item
    tagged = _item(impact="threat", affected_asset="키트루다")
    assert BriefItem.from_dict(tagged.to_dict()) == tagged
    assert BriefItem.from_dict({"title": "t", "publisher_url": "https://x/1"}).impact == ""


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
    assert "MA 시사점" in html and ">참고</span>" in html               # impact 없는 구 항목 → 참고 배지
    assert "근거: “1차 치료로 급여 확대”" in html
    assert "오늘의 주요 MA 뉴스" in html
    assert "특이사항 없음" not in html


def test_render_no_news_and_snippet_note():
    html = render_brief_html(name="파드셉", run_date=date(2026, 9, 18), scope_terms=["파드셉"], items=[])
    assert "특이사항 없음" in html
    snippet = _item(body_status="snippet", ma_implication="", evidence_quote="", quote_verified=False)
    html2 = render_brief_html(name="n", run_date=date(2026, 9, 18), scope_terms=["x"], items=[snippet])
    assert "본문 미확보" in html2
    assert "MA 시사점" not in html2


def test_render_markdown():
    md = render_brief_markdown(name="파드셉", run_date=date(2026, 9, 18), scope_terms=["파드셉"], items=[_item()])
    assert md.startswith("# 파드셉 · 9/18(금)")
    assert "▶ MA 시사점 [참고]: 요로상피암 1차 급여 경쟁 구도 변화" in md
    assert "> 오늘의 한 줄: 참고 — 파드셉 급여 확대" in md


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


def test_also_reported_by_round_trip_and_render():
    item = _item(also_reported_by=({"source_name": "데일리팜", "url": "https://www.dailypharm.com/2"},
                                   {"source_name": "약업신문", "url": "https://www.yakup.com/3"}))
    data = item.to_dict()
    assert data["also_reported_by"] == [{"source_name": "데일리팜", "url": "https://www.dailypharm.com/2"},
                                        {"source_name": "약업신문", "url": "https://www.yakup.com/3"}]
    assert BriefItem.from_dict(data) == item
    assert BriefItem.from_dict({"title": "t", "publisher_url": "https://x/1"}).also_reported_by == ()
    html = render_brief_html(name="n", run_date=date(2026, 9, 18), scope_terms=["x"], items=[item])
    assert "외 2개 매체: " in html
    assert 'href="https://www.dailypharm.com/2"' in html and ">데일리팜</a>" in html
    md = render_brief_markdown(name="n", run_date=date(2026, 9, 18), scope_terms=["x"], items=[item])
    assert "외 2개 매체: [데일리팜](https://www.dailypharm.com/2), [약업신문](https://www.yakup.com/3)" in md
    plain = render_brief_html(name="n", run_date=date(2026, 9, 18), scope_terms=["x"], items=[_item()])
    assert "외 " not in plain


def test_also_reported_by_escapes_and_skips_unsafe_links():
    item = _item(also_reported_by=({"source_name": "<b>x</b>", "url": "javascript:alert(1)"},))
    html = render_brief_html(name="n", run_date=date(2026, 9, 18), scope_terms=["x"], items=[item])
    assert "외 1개 매체: &lt;b&gt;x&lt;/b&gt;" in html
    assert "javascript:" not in html


# ---- 표시 다듬기 (item 3): 매체명 폴백 · 검색 요약 한 줄 ----
def test_publisher_display_name_falls_back_to_registrable_domain():
    assert publisher_display_name("Unknown publisher", "https://www.newsmp.com/news/articleView.html?idxno=1") == "newsmp.com"
    assert publisher_display_name("Unknown publisher", "http://www.hankooki.com/news/1") == "hankooki.com"
    assert publisher_display_name("", "https://m.docdocdoc.co.kr/news/1") == "docdocdoc.co.kr"
    assert publisher_display_name("Naver News API", "https://n.news.naver.com/article/1", source_tier="search_channel") == "naver.com"
    assert publisher_display_name("Unknown publisher", "https://news.example.go.kr/x") == "example.go.kr"


def test_publisher_display_name_keeps_registry_and_archive_names():
    assert publisher_display_name("약업신문", "https://www.yakup.com/1") == "약업신문"
    assert publisher_display_name("뉴스더보이스", "https://www.newsthevoice.com/1", source_tier="unregistered") == "뉴스더보이스"
    assert publisher_display_name("Unknown publisher", "") == ""
    assert publisher_display_name("Unknown publisher", "not a url") == ""
    assert publisher_display_name("Unknown publisher", "http://[bad/1") == ""


def test_clean_snippet_trims_truncated_tail_to_sentence_boundary():
    truncated = ("건보공단은 사용량-약가 연동 협상으로 80개 품목의 약가를 인하한다고 밝혔다. "
                 "이번 인하로 연간 339억원의 건강보험 재정이 절감될 것으로 공단은 기대...")
    assert clean_snippet(truncated) == "건보공단은 사용량-약가 연동 협상으로 80개 품목의 약가를 인하한다고 밝혔다."
    assert clean_snippet("문장 하나뿐인데 잘렸다 어디서 끝날지 몰라…") == "문장 하나뿐인데 잘렸다 어디서 끝날지 몰라"
    assert clean_snippet("완전한 문장이다.") == "완전한 문장이다."
    assert clean_snippet("  공백   정리  ") == "공백 정리"
    assert clean_snippet("") == ""
    # 경계가 너무 앞이면(조각만 남으면) 자르지 않고 말줄임만 뗀다
    assert clean_snippet("짧다. 아주 긴 두 번째 문장이 계속 이어지다가 검색 API 가 자르는 바람에 끝이 없…") \
        == "짧다. 아주 긴 두 번째 문장이 계속 이어지다가 검색 API 가 자르는 바람에 끝이 없"


def test_render_snippet_item_as_single_search_summary_line():
    snippet = _item(body_status="snippet", ma_implication="", evidence_quote="", quote_verified=False,
                    summary_bullets=("건보공단이 80개 품목 약가를 인하했다.",))
    html = render_brief_html(name="n", run_date=date(2026, 9, 18), scope_terms=["x"], items=[snippet])
    assert "검색 요약: 건보공단이 80개 품목 약가를 인하했다." in html
    assert "<ul" not in html
    assert "본문 미확보" in html
    md = render_brief_markdown(name="n", run_date=date(2026, 9, 18), scope_terms=["x"], items=[snippet])
    assert "   - 검색 요약: 건보공단이 80개 품목 약가를 인하했다.\n" in md
    assert "본문 미확보" in md
    full = render_brief_html(name="n", run_date=date(2026, 9, 18), scope_terms=["x"], items=[_item()])
    assert "<ul" in full and "검색 요약" not in full


# ---- 검색 요약 스니펫 (2026-09-30 파일럿: '검색 요약: 위한 급여기준 개선을 요청했다고 밝혔다.') ----
PILOT_FRAGMENT = "위한 급여기준 개선을 요청했다고 밝혔다."
NAVER_JOINED = "환우회는 국회 토론회에서 ... 위한 급여기준 개선을 요청했다고 밝혔다..."
NAVER_PERIOD_JOINED = ("현재 국내외 임상진료지침에서는 GnRH 유사체를 표준 치료로 권고하고 있다.... "
                       "또한 급여 기준과 별도로 치료 여부를 결정할 때는 사춘기 진행 속도를 가장 중요하게... ")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


@pytest.mark.parametrize("raw", [
    PILOT_FRAGMENT,
    PILOT_FRAGMENT + " 환우회는 이날 성명에서 PNH 치료제의 급여 확대와 본인부담 완화를... 촉구했다...",
    "한국발작성야간혈색소뇨증환우회는 29일 국회 토론회에서 PNH 환자 치료 접근성을 위한 급여기준 개선을 요청했다고 밝혔다...",
    NAVER_JOINED,
    NAVER_PERIOD_JOINED,
    "짧다. 아주 긴 두 번째 문장이 계속 이어지다가 검색 API 가 자르는 바람에 끝이 없…",
])
def test_clean_snippet_never_loses_the_beginning(raw):
    out = clean_snippet(raw)
    head = re.split(r"\.{2,}|…", _norm(raw))[0].strip()[:12]
    assert out.startswith(head), (out, head)                          # 머리글은 항상 원문 시작
    assert len(out) >= len(_norm(raw).rstrip(". …")) * 0.3            # 남는 길이 ≥ 30%
    assert not re.search(r"\.{2,}\s*$|…\s*$", out)                     # 꼬리 말줄임은 뗀다
    assert clean_snippet(out) == out                                   # 엔진이 bullets 를 다시 정리해도 같다


def test_clean_snippet_pilot_fragment_is_kept_intact():
    # 머리글이 없는 것은 네이버 발췌(키워드 중심 시작) 탓 — clean_snippet 은 남은 문장을 손대지 않는다
    assert clean_snippet(PILOT_FRAGMENT) == PILOT_FRAGMENT
    assert clean_snippet(PILOT_FRAGMENT + " 환우회는 이날 성명에서 급여 확대를... 촉구했다...") == PILOT_FRAGMENT


def test_clean_snippet_normal_snippet_keeps_complete_sentences_and_drops_truncated_tail():
    raw = ("한국발작성야간혈색소뇨증환우회는 29일 국회 토론회에서 PNH 환자 치료 접근성을 위한 급여기준 개선을 요청했다고 밝혔다. "
           "환우회는 이날 성명에서 급여 확대와 본인부담 완화를 함께...")
    assert clean_snippet(raw) == ("한국발작성야간혈색소뇨증환우회는 29일 국회 토론회에서 PNH 환자 치료 접근성을 위한 "
                                  "급여기준 개선을 요청했다고 밝혔다.")


def test_clean_snippet_inline_ellipsis_is_an_excerpt_joiner_not_a_sentence_end():
    # 네이버 description 은 비연속 발췌를 '...' 로 잇는다 (아카이브 400건 중 257건). 이음표를 문장 끝으로 보면 머리 조각만 남는다.
    assert clean_snippet(NAVER_JOINED) == "환우회는 국회 토론회에서 … 위한 급여기준 개선을 요청했다고 밝혔다"
    # '있다....' = 문장 끝 + 이음표: 문장 끝은 살리고, 뒤 조각이 잘린 문장이면 거기까지만
    assert clean_snippet(NAVER_PERIOD_JOINED) == "현재 국내외 임상진료지침에서는 GnRH 유사체를 표준 치료로 권고하고 있다."
    # 뒤 조각에 완결 문장이 있으면 그 문장까지 (이음표 포함) 유지
    assert clean_snippet("A 회사는 신약을 출시했다.... 이어 급여 등재를 신청했다. 회사는 내년...") \
        == "A 회사는 신약을 출시했다. … 이어 급여 등재를 신청했다."
    # 꼬리가 잘리지 않은(말줄임 없는) 본문은 이음표만 정리하고 자르지 않는다
    assert clean_snippet("A 회사는 ... 출시했다. 이어 신청했다.") == "A 회사는 … 출시했다. 이어 신청했다."



# ---- 카드 레이아웃 (2026-09-30 파일럿 리뷰: 요약기 → MA 분석가, 한 줄 다이제스트 + 영향 배지 카드) ----
import os
import shutil
from dataclasses import replace

from agents.daily_mailing.brief import IMPACT_LABELS, build_digest


def _threat():
    return _item(title="사용범위 넓어진 키트루다·테빔브라, RSA도 다시 계약", publisher_url="https://www.yakup.com/tv1",
                 summary_bullets=("건보공단이 테빔브라 급여 범위를 비소세포폐암 1차 등으로 확대했다.",
                                  "키트루다 위험분담계약(RSA)은 재계약됐다."),
                 ma_implication=("같은 PD-1 계열 테빔브라(약가 1,206,000원/100mg)가 키트루다(2,100,556원/100mg)와 같은 급여 적응증을 "
                                 "가져가면 키트루다 RSA 재계약 조건에 약가 인하 압박이 된다"),
                 evidence_quote="테빔브라의 급여 범위를 비소세포폐암 1차 등 키트루다와 같은 적응증으로 확대", impact="threat",
                 affected_asset="키트루다",
                 also_reported_by=({"source_name": "데일리팜", "url": "https://www.dailypharm.com/1"},
                                   {"source_name": "메디파나뉴스", "url": "https://www.medipana.com/1"},
                                   {"source_name": "약사공론", "url": "https://www.kpanews.co.kr/1"},
                                   {"source_name": "청년의사", "url": "https://www.docdocdoc.co.kr/1"},
                                   {"source_name": "뉴스더보이스", "url": "javascript:alert(1)"}))


def _neutral():
    return _item(title="암질심 10월 안건 일정 공개", publisher_url="https://www.dailypharm.com/n1", source_name="데일리팜",
                 summary_bullets=("심평원이 10월 암질심 안건 일정을 공개했다.",),
                 ma_implication="10월 암질심 일정은 키트루다 병용 급여 확대 안건의 심의 시점에 닿는다",
                 evidence_quote="10월 암질심 안건 일정을 공개", impact="neutral", affected_asset="키트루다")


def _opportunity():
    return _item(title="웰리렉 VHL 급여 적정성 인정", publisher_url="https://www.medipana.com/o1", source_name="메디파나뉴스",
                 ma_implication="웰리렉의 VHL 급여 적정성 인정은 등재 협상 단계 진입을 뜻한다", evidence_quote="급여 적정성 인정",
                 impact="opportunity", affected_asset="웰리렉")


def _snippet():
    return _item(title="PNH 환우회, 급여기준 개선 요청", publisher_url="https://www.newsmp.com/s1", source_name="newsmp.com",
                 summary_bullets=("환우회는 국회 토론회에서 … 위한 급여기준 개선을 요청했다고 밝혔다",),
                 ma_implication="", evidence_quote="", quote_verified=False, body_status="snippet")


def test_build_digest_prefers_threat_then_opportunity_then_neutral():
    assert build_digest([_neutral(), _opportunity(), _threat()]) == "위협 · 키트루다 — 사용범위 넓어진 키트루다·테빔브라, RSA도 다시 계약"
    assert build_digest([_neutral(), _opportunity()]) == "기회 · 웰리렉 — 웰리렉 VHL 급여 적정성 인정"
    assert build_digest([_neutral()]) == "참고 · 키트루다 — 암질심 10월 안건 일정 공개"
    legacy = _item(impact="")                                            # 구 run 의 시사점(impact 없음) → 참고
    assert build_digest([legacy]) == "참고 — 파드셉 급여 확대"
    assert build_digest([_snippet(), _snippet()]) == "특이사항 없음 — 스콥 기사 2건, 시사점 없음"
    assert build_digest([]) == "특이사항 없음"
    assert build_digest([replace(_neutral(), section="top_ma_news")]) == "특이사항 없음"   # 다이제스트는 스콥 기사만
    unverified = _item(quote_verified=False, impact="threat")           # 렌더되지 않는 시사점은 다이제스트에도 없다
    assert build_digest([unverified]) == "특이사항 없음 — 스콥 기사 1건, 시사점 없음"


def test_render_html_digest_and_impact_badges():
    html = render_brief_html(name="키트루다", run_date=date(2026, 9, 30), scope_terms=["키트루다"],
                             items=[_threat(), _neutral(), _opportunity()])
    assert "오늘의 한 줄" in html
    assert "위협 · 키트루다 — 사용범위 넓어진 키트루다·테빔브라, RSA도 다시 계약" in html
    assert ">위협 · 키트루다</span>" in html and "#B91C1C" in html.split(">위협 · 키트루다</span>")[0][-400:]
    assert ">기회 · 웰리렉</span>" in html and "#15803D" in html.split(">기회 · 웰리렉</span>")[0][-400:]
    assert ">참고 · 키트루다</span>" in html and "#374151" in html.split(">참고 · 키트루다</span>")[0][-400:]
    assert "MA 시사점" in html
    assert "근거: “테빔브라의 급여 범위를 비소세포폐암 1차 등 키트루다와 같은 적응증으로 확대”" in html
    assert html.count("border-radius:10px") >= 3                        # 카드 3장


def test_render_html_escapes_badge_asset_and_implication():
    evil = _item(ma_implication="<img src=x> 시사점", affected_asset="<b>키트루다</b>", impact="threat")
    html = render_brief_html(name="n", run_date=date(2026, 9, 30), scope_terms=["x"], items=[evil])
    assert "<img" not in html and "&lt;img src=x&gt; 시사점" in html
    assert "위협 · &lt;b&gt;키트루다&lt;/b&gt;" in html


def test_render_also_reported_lists_at_most_three_names_plus_count():
    html = render_brief_html(name="n", run_date=date(2026, 9, 30), scope_terms=["x"], items=[_threat()])
    assert "외 5개 매체: " in html
    assert "데일리팜" in html and "메디파나뉴스" in html and "약사공론" in html
    assert "청년의사" not in html and "뉴스더보이스" not in html
    assert "외 2곳" in html
    assert 'href="https://www.dailypharm.com/1"' in html and 'href="https://www.docdocdoc.co.kr/1"' not in html
    assert "javascript:" not in html
    md = render_brief_markdown(name="n", run_date=date(2026, 9, 30), scope_terms=["x"], items=[_threat()])
    assert "외 5개 매체: [데일리팜](https://www.dailypharm.com/1), [메디파나뉴스](https://www.medipana.com/1), [약사공론](https://www.kpanews.co.kr/1) 외 2곳" in md
    three = _item(also_reported_by=tuple({"source_name": f"매체{i}", "url": f"https://x/{i}"} for i in range(3)))
    html3 = render_brief_html(name="n", run_date=date(2026, 9, 30), scope_terms=["x"], items=[three])
    assert "외 3개 매체: 매체0, 매체1, 매체2" in html3.replace('<a href="https://x/0" style="color:#64748B;">', "").replace("</a>", "") \
        .replace('<a href="https://x/1" style="color:#64748B;">', "").replace('<a href="https://x/2" style="color:#64748B;">', "")
    assert "외 0곳" not in html3 and " 외 " not in html3.split("외 3개 매체: ")[1].split("</div>")[0]


def test_render_html_is_email_safe_dark_mode_and_phone_ready():
    html = render_brief_html(name="n", run_date=date(2026, 9, 30), scope_terms=["x"], items=[_threat()])
    assert '<meta name="viewport" content="width=device-width,initial-scale=1">' in html
    assert '<meta name="color-scheme" content="light">' in html
    assert "<body style=" in html and "background:#F3F4F6" in html.split("<body")[1].split(">")[0]
    assert "color:#0F172A" in html.split("<body")[1].split(">")[0]
    assert "max-width:640px" in html
    assert "<style" not in html                                          # 인라인 스타일만
    assert "원문 보기" in html


def test_render_snippet_card_keeps_search_summary_and_note():
    html = render_brief_html(name="n", run_date=date(2026, 9, 30), scope_terms=["x"], items=[_snippet()])
    assert "검색 요약: 환우회는 국회 토론회에서 … 위한 급여기준 개선을 요청했다고 밝혔다" in html
    assert "본문 미확보 — 검색 요약 기반" in html
    assert "MA 시사점" not in html
    assert "특이사항 없음 — 스콥 기사 1건, 시사점 없음" in html


def test_render_markdown_in_step_with_digest_and_badge():
    md = render_brief_markdown(name="키트루다", run_date=date(2026, 9, 30), scope_terms=["키트루다"], items=[_threat(), _snippet()])
    assert md.splitlines()[0] == "# 키트루다 · 9/30(수)"
    assert "오늘의 한 줄: 위협 · 키트루다 — 사용범위 넓어진 키트루다·테빔브라, RSA도 다시 계약" in md
    assert "   - ▶ MA 시사점 [위협 · 키트루다]: 같은 PD-1 계열 테빔브라" in md
    assert "   - 근거: “테빔브라의 급여 범위를" in md
    assert "   - 검색 요약: 환우회는 국회 토론회에서" in md and "본문 미확보" in md


def test_layout_preview_fixture_threat_neutral_snippet(tmp_path):
    """오프라인 렌더 픽스처: 위협 카드 1 + 참고 카드 1 + 스니펫 카드 1 + 주요 MA 뉴스 1. DM_LAYOUT_PREVIEW_OUT 이 있으면 그 경로에도 저장."""
    items = [_threat(), _neutral(), _snippet(), replace(_opportunity(), section="top_ma_news")]
    html = render_brief_html(name="키트루다 (파일럿)", run_date=date(2026, 9, 30), scope_terms=["키트루다", "급여 확대"],
                             items=items, window_label="이전 24시간")
    out = tmp_path / "layout_preview.html"
    out.write_text(html, encoding="utf-8")
    for label in IMPACT_LABELS.values():
        assert label in html
    assert "오늘의 주요 MA 뉴스" in html
    assert html.count("border-radius:10px") >= 4
    target = os.environ.get("DM_LAYOUT_PREVIEW_OUT")
    if target:
        shutil.copyfile(out, target)
