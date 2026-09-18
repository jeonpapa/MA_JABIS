from __future__ import annotations

from agents.daily_mailing.article_body import (
    MIN_BODY_CHARS,
    candidate_urls,
    extract_body_text,
    fetch_article_body,
)

LONG = "파드셉과 키트루다 병용요법이 요로상피암 1차 치료로 급여 확대 논의에 들어갔다. " * 8


def test_extract_prefers_naver_dic_area():
    html = f"<html><body><nav>메뉴</nav><div id='dic_area'>{LONG}</div><p>광고</p></body></html>"
    text = extract_body_text(html)
    assert text.startswith("파드셉과 키트루다")
    assert "메뉴" not in text


def test_extract_publisher_selector():
    html = f"<html><body><div id='article-view-content-div'>{LONG}</div></body></html>"
    assert "요로상피암" in extract_body_text(html)


def test_extract_paragraph_density_fallback():
    paras = "".join(f"<p>{LONG[:80]} {i}</p>" for i in range(5))
    html = f"<html><body><div class='x'><p>짧음</p></div><div class='y'>{paras}</div></body></html>"
    text = extract_body_text(html)
    assert len(text) >= MIN_BODY_CHARS
    assert "짧음" not in text


def test_candidate_urls_naver_first_and_deduped():
    urls = candidate_urls("https://www.yakup.com/1", "https://n.news.naver.com/mnews/article/001/1")
    assert urls == ["https://n.news.naver.com/mnews/article/001/1", "https://www.yakup.com/1"]
    assert candidate_urls("https://a.com/1", "https://a.com/1") == ["https://a.com/1"]


def test_fetch_ok_from_first_url():
    pages = {"https://n.news.naver.com/a": f"<div id='dic_area'>{LONG}</div>"}
    body = fetch_article_body("https://www.yakup.com/1", "https://n.news.naver.com/a",
                              fetch=lambda url, timeout: pages[url])
    assert body.status == "ok"
    assert body.source_url == "https://n.news.naver.com/a"


def test_fetch_falls_back_when_first_url_fails():
    def fetch(url, timeout):
        if "naver" in url:
            raise OSError("blocked")
        return f"<div id='articleBody'>{LONG}</div>"

    body = fetch_article_body("https://www.yakup.com/1", "https://n.news.naver.com/a", fetch=fetch)
    assert body.status == "ok"
    assert body.source_url == "https://www.yakup.com/1"


def test_fetch_too_short_and_failed():
    short = fetch_article_body("https://a.com/1", "", fetch=lambda u, t: "<article>짧은 본문</article>")
    assert short.status == "too_short"
    assert short.text == "짧은 본문"

    def boom(url, timeout):
        raise OSError("down")

    failed = fetch_article_body("https://a.com/1", "", fetch=boom)
    assert failed.status == "failed"
    assert failed.text == ""
    assert "OSError" in failed.error
