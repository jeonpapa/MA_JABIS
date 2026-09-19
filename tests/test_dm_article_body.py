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


# ---- 원문 fetch 안전장치 (item 4) ----
import pytest
import requests
from requests.structures import CaseInsensitiveDict

from agents.daily_mailing import article_body
from agents.daily_mailing.article_body import MAX_BODY_BYTES, _default_fetch

HTML_OK = f"<html><body><div id='dic_area'>{LONG}</div></body></html>"


class FakeResponse:
    def __init__(self, *, status=200, headers=None, chunks=(), encoding="utf-8"):
        self.status_code = status
        self.headers = CaseInsensitiveDict(headers if headers is not None else {"Content-Type": "text/html; charset=utf-8"})
        self.encoding = encoding
        self.closed = False
        self.chunks_served = 0
        self._chunks = chunks

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def iter_content(self, chunk_size=1):
        for chunk in self._chunks:
            self.chunks_served += 1
            yield chunk

    def close(self):
        self.closed = True


def _install_get(monkeypatch, response):
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return response

    monkeypatch.setattr(article_body, "_http_get", fake_get)
    return calls


def test_default_fetch_streams_bounded_and_closes(monkeypatch):
    resp = FakeResponse(chunks=(HTML_OK.encode("utf-8"),))
    calls = _install_get(monkeypatch, resp)
    text = _default_fetch("https://www.yakup.com/1", 5.0)
    assert "요로상피암" in text
    assert calls[0][1]["stream"] is True and calls[0][1]["timeout"] == 5.0
    assert resp.closed is True


def test_default_fetch_stops_reading_at_max_bytes(monkeypatch):
    one_mb = b"a" * 1_000_000
    resp = FakeResponse(chunks=(one_mb,) * 6)
    _install_get(monkeypatch, resp)
    text = _default_fetch("https://www.yakup.com/1", 5.0)
    assert len(text) == MAX_BODY_BYTES
    assert resp.chunks_served == 3
    assert resp.closed is True


def test_default_fetch_rejects_oversize_content_length(monkeypatch):
    resp = FakeResponse(headers={"Content-Type": "text/html", "Content-Length": str(MAX_BODY_BYTES + 1)},
                        chunks=(HTML_OK.encode(),))
    _install_get(monkeypatch, resp)
    with pytest.raises(ValueError, match="large"):
        _default_fetch("https://www.yakup.com/1", 5.0)
    assert resp.chunks_served == 0 and resp.closed is True
    body = fetch_article_body("https://www.yakup.com/1", "")
    assert body.status == "failed" and "large" in body.error


def test_default_fetch_rejects_non_html_content_type(monkeypatch):
    resp = FakeResponse(headers={"Content-Type": "application/pdf"}, chunks=(HTML_OK.encode(),))
    _install_get(monkeypatch, resp)
    body = fetch_article_body("https://www.yakup.com/1", "")
    assert body.status == "failed" and "content-type" in body.error.lower()
    assert resp.chunks_served == 0 and resp.closed is True


def test_default_fetch_falls_back_to_detected_encoding(monkeypatch):
    resp = FakeResponse(chunks=(HTML_OK.encode("utf-8"),), encoding=None,
                        headers={"Content-Type": "text/html"})
    _install_get(monkeypatch, resp)
    assert "파드셉" in _default_fetch("https://www.yakup.com/1", 5.0)
    resp2 = FakeResponse(chunks=(HTML_OK.encode("utf-8"),), encoding="ISO-8859-1",
                         headers={"Content-Type": "text/html"})
    _install_get(monkeypatch, resp2)
    assert "파드셉" in _default_fetch("https://www.yakup.com/1", 5.0)


@pytest.mark.parametrize("url", [
    "javascript:alert(1)",
    "file:///etc/passwd",
    "ftp://example.com/x",
    "http://127.0.0.1/x",
    "http://localhost/x",
    "http://10.0.0.8/x",
    "http://192.168.1.1/x",
    "http://[::1]/x",
    "http://169.254.169.254/latest/meta-data",
    "http://0.0.0.0/x",
    "http://[bad/1",
    "",
])
def test_unsafe_urls_fail_without_any_fetch(monkeypatch, url):
    def never(*args, **kwargs):
        raise AssertionError("network fetch must not happen")

    monkeypatch.setattr(article_body, "_http_get", never)
    body = fetch_article_body(url, "")
    assert body.status == "failed"
    assert body.text == ""
    custom_calls = []

    def custom_fetch(u, timeout):
        custom_calls.append(u)
        return HTML_OK

    assert fetch_article_body(url, "", fetch=custom_fetch).status == "failed"
    assert custom_calls == []


def test_unsafe_first_url_then_safe_second_succeeds(monkeypatch):
    resp = FakeResponse(chunks=(HTML_OK.encode(),))
    calls = _install_get(monkeypatch, resp)
    body = fetch_article_body("http://127.0.0.1/x", "https://n.news.naver.com/a")
    assert body.status == "ok" and body.source_url == "https://n.news.naver.com/a"
    assert [c[0] for c in calls] == ["https://n.news.naver.com/a"]


# ---- 선택자 우선 (item 7): 선택자가 맞으면 짧아도 그 본문을 쓴다 ----
SHORT_ARTICLE = ("파드셉 병용요법의 요로상피암 1차 급여 확대 논의가 시작됐다. 심평원은 다음 달 암질심 안건 상정을 "
                 "검토 중이며 관계자는 신중한 입장을 밝혔다.")


def test_matched_selector_short_text_beats_paragraph_density():
    assert len(SHORT_ARTICLE) < MIN_BODY_CHARS
    teaser = "".join(f"<p>{LONG[:80]} {i}</p>" for i in range(5))
    html = f"<html><body><div id='dic_area'>{SHORT_ARTICLE}</div><div class='teaser'>{teaser}</div></body></html>"
    assert extract_body_text(html) == SHORT_ARTICLE
    body = fetch_article_body("https://www.yakup.com/1", "", fetch=lambda u, t: html)
    assert body.status == "too_short" and body.text == SHORT_ARTICLE


def test_among_matched_selectors_prefer_first_with_enough_text():
    html = f"<html><body><div id='dic_area'>짧은 요약</div><article>{LONG}</article></body></html>"
    assert extract_body_text(html).startswith("파드셉과 키트루다")
