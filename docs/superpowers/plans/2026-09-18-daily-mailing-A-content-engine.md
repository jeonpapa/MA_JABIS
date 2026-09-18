# Daily Mailing — Slice A: Content Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn one subscription scope into one briefing run (scope-faithful candidates → article body → LLM relevance → LLM brief with verified quote → quality gate → briefing HTML), plus a pilot CLI that renders real scopes for user review without sending.

**Architecture:** Upgrade the existing `agents/daily_mailing` pipeline in place. Keep `discovery`/`rank_items`/`select_daily_items`/`build_article_card`; add focused new modules (`article_body`, `llm`, `brief`, `archive_candidates`, `engine`). The engine is pure orchestration with injected dependencies (Naver client, LLM, body fetcher) so every step is unit-testable with fakes. No DB writes, no sending — Slice B persists and sends `RunResult`.

**Tech Stack:** Python 3.12, `urllib` (Naver), `requests` + BeautifulSoup/lxml (body), OpenAI SDK (`openai>=1.40`, JSON mode), pytest.

**Spec:** `docs/superpowers/specs/2026-09-18-daily-mailing-service-design.md` §5, §9.

---

## Working rules for this slice

- Work in the git worktree you were given. Python: `/Users/kimjeong-ae/MA_AI_Dossier/.venv/bin/python` (the worktree has no `.venv`). Run tests from the worktree root: `/Users/kimjeong-ae/MA_AI_Dossier/.venv/bin/python -m pytest <path> -q`.
- Do **not** modify: `agents/daily_mailing/storage.py`, `agents/daily_mailing/dashboard_scope.py`, `agents/daily_mailing/subscription_bridge.py`, `api/`, `scheduler.py`, `frontend/`, `requirements.txt` (Slice B owns them).
- Do not call real Naver/OpenAI/HTTP in tests. The pilot CLI (Task A8) is run later by the controller with real keys.
- Commit after each task with the message shown. End every commit message with:
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`

## Interface contract with Slice B (must match exactly)

```python
# agents/daily_mailing/brief.py
@dataclass(frozen=True)
class BriefItem: ...            # fields defined in Task A4
    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, data: dict) -> "BriefItem": ...

# agents/daily_mailing/engine.py
@dataclass(frozen=True)
class EngineConfig: ...          # .from_settings(settings_dict)
@dataclass
class EngineDeps: ...            # .default(db_path=...)
@dataclass
class RunResult:
    run_id: str; subscription_id: str; run_date: str; is_test: bool
    status: str                  # "ready" | "no_news" | "held"
    subject: str; html: str; markdown: str
    items: list[BriefItem]; top_ma_news: list[BriefItem]
    quality_report: dict
    payload: dict                # arg #1 of storage.persist_daily_mailing_run
    articles: list[dict]         # kwarg articles= of persist_daily_mailing_run
    stats: dict

def generate_run(scope: DashboardScope, *, deps: EngineDeps, config: EngineConfig | None = None,
                 name: str | None = None, now: datetime | None = None,
                 top_ma_news: list[BriefItem] | None = None, is_test: bool = False) -> RunResult
def generate_top_ma_news(*, deps: EngineDeps, config: EngineConfig | None = None,
                         now: datetime | None = None) -> list[BriefItem]
```

`payload` keys (Slice B reads these): `run_id, generated_at, window_label, lookback_hours, keywords, media, subscription_id (int|None), owner_email, recipients, discovered_count, recent_count, selected_count, status, quality_report, personas, items (list of BriefItem.to_dict()), dashboard_scope, run_date ("YYYY-MM-DD" KST), is_test (bool), email_subject, email_html, delivery_status ("draft_only"), approval_status ("not_requested")`.

## File structure

| File | Responsibility |
|---|---|
| `agents/daily_mailing/discovery.py` (modify) | Naver throttle + 429 backoff, per-run query cap/stats, opt-out of forced MSD item |
| `agents/daily_mailing/article_body.py` (new) | Fetch + extract article body text (Naver `#dic_area` first, publisher selectors, `<p>` fallback) |
| `agents/daily_mailing/llm.py` (new) | JSON LLM wrapper, relevance judge, brief writer, quote verification |
| `agents/daily_mailing/brief.py` (new) | `BriefItem`, quality gate, subject, HTML/Markdown briefing render |
| `agents/daily_mailing/archive_candidates.py` (new) | Scope-matching candidates from the `competitor_news` archive (read-only) |
| `agents/daily_mailing/engine.py` (new) | Orchestration: `generate_run`, `generate_top_ma_news`, config/deps/result types |
| `config/settings.json` (modify) | `daily_mailing` model/limit settings |
| `scripts/daily_mailing_pilot.py` (new) | Pilot CLI: real scopes → HTML + judgments + index, no send/no DB write |
| `tests/test_dm_*.py` (new) | One test file per module |

---

### Task A1: Naver client throttle, 429 backoff, query cap, no forced MSD item

**Files:**
- Modify: `agents/daily_mailing/discovery.py`
- Test: `tests/test_dm_discovery_robustness.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_dm_discovery_robustness.py
from __future__ import annotations

import json
from urllib.error import HTTPError

import pytest

from agents.daily_mailing.discovery import (
    NaverNewsClient,
    NewsDiscoveryItem,
    discover_naver_news,
    select_daily_items,
)
from agents.daily_mailing.source_registry import SourceInfo, SourceRegistry


class FakeResponse:
    def __init__(self, items):
        self._items = items

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return json.dumps({"items": self._items}).encode("utf-8")


def _raw(title, url):
    return {"title": title, "originallink": url, "link": url, "description": title,
            "pubDate": "Fri, 18 Sep 2026 06:00:00 +0900"}


def _registry():
    return SourceRegistry([SourceInfo(name="약업신문", domains=("yakup.com",), source_tier="media_tier_A")])


def test_throttle_sleeps_between_calls():
    sleeps = []
    client = NaverNewsClient("id", "secret", opener=lambda req, timeout: FakeResponse([]),
                             min_interval=0.15, sleep=sleeps.append, clock=lambda: 100.0)
    client.search("a")
    client.search("b")
    assert sleeps == [pytest.approx(0.15)]


def test_429_retries_with_exponential_backoff_then_succeeds():
    calls = {"n": 0}

    def opener(req, timeout):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise HTTPError(req.full_url, 429, "Too Many Requests", None, None)
        return FakeResponse([_raw("t", "https://www.yakup.com/1")])

    ticks = iter(range(1000))
    sleeps = []
    client = NaverNewsClient("id", "secret", opener=opener, sleep=sleeps.append,
                             clock=lambda: float(next(ticks)))
    items = client.search("q")
    assert len(items) == 1
    assert sleeps == [1.0, 2.0]


def test_429_exhausted_raises():
    def opener(req, timeout):
        raise HTTPError(req.full_url, 429, "Too Many Requests", None, None)

    ticks = iter(range(1000))
    client = NaverNewsClient("id", "secret", opener=opener, sleep=lambda s: None,
                             clock=lambda: float(next(ticks)), max_retries=3)
    with pytest.raises(HTTPError):
        client.search("q")


class ScriptedClient:
    def __init__(self, by_query=None, fail=None):
        self.by_query = by_query or {}
        self.fail = fail or {}
        self.queries = []

    def search(self, query, display=20, sort="date"):
        self.queries.append(query)
        if query in self.fail:
            raise HTTPError("https://openapi.naver.com", self.fail[query], "err", None, None)
        return self.by_query.get(query, [])


def test_discover_respects_max_queries_and_reports_stats():
    client = ScriptedClient({"a": [_raw("a1", "https://www.yakup.com/a1")]})
    stats = {}
    items = discover_naver_news(["a", "b", "c"], client=client, registry=_registry(),
                                max_queries=2, stats=stats)
    assert client.queries == ["a", "b"]
    assert stats["queries"] == 2
    assert stats["query_cap_reached"] is True
    assert [i.title for i in items] == ["a1"]


def test_discover_stops_on_rate_limit_and_keeps_collected_items():
    client = ScriptedClient({"a": [_raw("a1", "https://www.yakup.com/a1")]}, fail={"b": 429})
    stats = {}
    items = discover_naver_news(["a", "b", "c"], client=client, registry=_registry(), stats=stats)
    assert client.queries == ["a", "b"]
    assert stats["rate_limited"] is True
    assert [i.title for i in items] == ["a1"]


def test_discover_skips_non_429_http_error_and_continues():
    client = ScriptedClient({"c": [_raw("c1", "https://www.yakup.com/c1")]}, fail={"b": 500})
    stats = {}
    items = discover_naver_news(["b", "c"], client=client, registry=_registry(), stats=stats)
    assert [i.title for i in items] == ["c1"]
    assert stats["errors"] == ["b: 500"]


def _item(title, url):
    return NewsDiscoveryItem(
        title=title, publisher_url=url, naver_url="", description=title,
        published_at="2026-09-18T06:00:00+09:00", keyword="파드셉", discovery_channel="naver_news_api",
        source_name="약업신문", source_tier="media_tier_A", source_weight=1.0,
        ma_depth=0, novelty=0, volume=0,
    )


def test_select_can_disable_forced_msd_representative():
    padcev = _item("파드셉 요로상피암 급여 확대", "https://www.yakup.com/p1")
    msd = _item("한국MSD 엔플론시아 RSV 예방", "https://www.yakup.com/m1")
    forced = select_daily_items([padcev, msd], ["파드셉"], limit=1)
    assert any("MSD" in i.title for i in forced)  # legacy behaviour unchanged
    plain = select_daily_items([padcev, msd], ["파드셉"], limit=1, force_msd_representative=False)
    assert [i.title for i in plain] == ["파드셉 요로상피암 급여 확대"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/Users/kimjeong-ae/MA_AI_Dossier/.venv/bin/python -m pytest tests/test_dm_discovery_robustness.py -q`
Expected: FAIL (`TypeError: ... unexpected keyword argument 'min_interval'` / `'max_queries'` / `'force_msd_representative'`).

- [ ] **Step 3: Implement**

In `agents/daily_mailing/discovery.py`:

1. Imports — add below `import os`:

```python
import time
```

and change the urllib import line to:

```python
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
```

2. Replace the whole `class NaverNewsClient` with:

```python
class NaverNewsClient:
    """Naver 뉴스 검색 API 클라이언트.

    호출 간 최소 간격(min_interval)을 두고, 429(Too Many Requests)는 지수 백오프로
    max_retries 회까지 재시도한다 (2026-09-18: 스콥 키워드 대량 호출 시 429 로 실행 중단 재현).
    """

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        opener: Callable = urlopen,
        *,
        min_interval: float = 0.15,
        max_retries: int = 3,
        backoff_base: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.client_id = client_id or os.environ.get("NAVER_API_CLIENT_ID")
        self.client_secret = client_secret or os.environ.get("NAVER_API_CLIENT_SECRET")
        self.opener = opener
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self._sleep = sleep
        self._clock = clock
        self._last_call: float | None = None
        if not self.client_id or not self.client_secret:
            raise RuntimeError("NAVER_API_CLIENT_ID and NAVER_API_CLIENT_SECRET are required")

    def _throttle(self) -> None:
        if self._last_call is not None:
            wait = self.min_interval - (self._clock() - self._last_call)
            if wait > 0:
                self._sleep(wait)
        self._last_call = self._clock()

    def search(self, query: str, display: int = 20, sort: str = "date") -> list[dict]:
        params = urlencode({"query": query, "display": display, "sort": sort})
        attempt = 0
        while True:
            self._throttle()
            req = Request(
                f"https://openapi.naver.com/v1/search/news.json?{params}",
                headers={
                    "X-Naver-Client-Id": str(self.client_id),
                    "X-Naver-Client-Secret": str(self.client_secret),
                },
            )
            try:
                with self.opener(req, timeout=15) as response:
                    return json.loads(response.read().decode("utf-8")).get("items", [])
            except HTTPError as exc:
                if exc.code != 429 or attempt >= self.max_retries:
                    raise
                attempt += 1
                self._sleep(self.backoff_base * (2 ** (attempt - 1)))
```

3. In `discover_naver_news`, add two keyword-only parameters after `media_strategy: str = "boost",`:

```python
    max_queries: int | None = None,
    stats: dict | None = None,
```

and replace the loop header `for keyword in keywords:` / `for raw in client.search(keyword, display=display, sort="date"):` with:

```python
    queries = 0
    for keyword in keywords:
        if max_queries is not None and queries >= max_queries:
            if stats is not None:
                stats["query_cap_reached"] = True
            break
        queries += 1
        try:
            raw_items = client.search(keyword, display=display, sort="date")
        except (HTTPError, URLError) as exc:
            code = getattr(exc, "code", None)
            if stats is not None:
                stats.setdefault("errors", []).append(f"{keyword}: {code or type(exc).__name__}")
            if code == 429:
                if stats is not None:
                    stats["rate_limited"] = True
                break
            continue
        for raw in raw_items:
```

Keep the existing loop body unchanged (it is now indented under `for raw in raw_items:` exactly as before). Replace the final `return items` of the function with:

```python
    if stats is not None:
        stats["queries"] = queries
    return items
```

4. `select_daily_items`: add a keyword-only parameter and short-circuit. Change the signature to:

```python
def select_daily_items(
    items: Iterable[NewsDiscoveryItem],
    keywords: Iterable[str],
    limit: int = 5,
    media: Iterable[str] | None = None,
    personas: Iterable[DailyMailingPersona] | None = None,
    *,
    force_msd_representative: bool = True,
) -> list[NewsDiscoveryItem]:
```

and change the line `if any(is_msd_representative_item(item) for item in selected):` to:

```python
    if not force_msd_representative or any(is_msd_representative_item(item) for item in selected):
```

- [ ] **Step 4: Run the new tests and the existing discovery-related tests**

Run: `/Users/kimjeong-ae/MA_AI_Dossier/.venv/bin/python -m pytest tests/test_dm_discovery_robustness.py tests/test_daily_mailing_final_gates.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add agents/daily_mailing/discovery.py tests/test_dm_discovery_robustness.py
git commit -m "feat(daily-mailing): Naver 429 백오프·호출 간격·쿼리 상한 + MSD 대표기사 강제삽입 옵션화"
```

---

### Task A2: Article body extraction

**Files:**
- Create: `agents/daily_mailing/article_body.py`
- Test: `tests/test_dm_article_body.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_dm_article_body.py
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
```

- [ ] **Step 2: Run to verify failure**

Run: `/Users/kimjeong-ae/MA_AI_Dossier/.venv/bin/python -m pytest tests/test_dm_article_body.py -q`
Expected: FAIL (`ModuleNotFoundError: agents.daily_mailing.article_body`).

- [ ] **Step 3: Implement**

```python
# agents/daily_mailing/article_body.py
"""기사 본문 추출 — Daily Mailing 요약·인용 검증의 근거.

설계 §5 ④. 네이버 뉴스(n.news.naver.com)는 `#dic_area` 구조가 일정하므로 우선 시도하고,
언론사 원문은 알려진 본문 선택자 → `<p>` 밀도 폴백 순으로 추출한다.
결과 status: ok(≥MIN_BODY_CHARS) / too_short / failed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

import requests
from bs4 import BeautifulSoup

MIN_BODY_CHARS = 200
MAX_BODY_CHARS = 8000
BODY_SELECTORS = (
    "#dic_area",                   # 네이버 뉴스
    "#article-view-content-div",   # NDSoft CMS (다수 의약 전문지)
    "#articleBody",
    "#article_body",
    "#articleBodyContents",
    "#news_body_area",
    "div.article_body",
    "div.article-body",
    "div.news_body",
    "div.view_con",
    "#newsEndContents",
    "article",
)
STRIP_TAGS = ("script", "style", "noscript", "iframe", "header", "footer", "nav",
              "aside", "form", "figure", "figcaption", "button")
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


@dataclass(frozen=True)
class ArticleBody:
    status: str        # ok | too_short | failed
    text: str
    source_url: str
    error: str = ""


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def extract_body_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "lxml")
    for tag in soup(list(STRIP_TAGS)):
        tag.decompose()
    for selector in BODY_SELECTORS:
        node = soup.select_one(selector)
        if node is not None:
            text = _normalize_ws(node.get_text(" "))
            if len(text) >= MIN_BODY_CHARS:
                return text[:MAX_BODY_CHARS]
    # 폴백: 직계 <p> 텍스트가 가장 긴 부모 블록
    best = ""
    seen: set[int] = set()
    for p in soup.find_all("p"):
        parent = p.parent
        if parent is None or id(parent) in seen:
            continue
        seen.add(id(parent))
        text = _normalize_ws(" ".join(c.get_text(" ") for c in parent.find_all("p", recursive=False)))
        if len(text) > len(best):
            best = text
    if best:
        return best[:MAX_BODY_CHARS]
    # 마지막 폴백: 선택자에 걸렸지만 짧은 본문
    for selector in BODY_SELECTORS:
        node = soup.select_one(selector)
        if node is not None:
            return _normalize_ws(node.get_text(" "))[:MAX_BODY_CHARS]
    return ""


def _default_fetch(url: str, timeout: float) -> str:
    resp = requests.get(url, headers={"User-Agent": _UA}, timeout=timeout)
    resp.raise_for_status()
    if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding
    return resp.text


def candidate_urls(publisher_url: str, naver_url: str = "") -> list[str]:
    ordered: list[str] = []
    if naver_url and "n.news.naver.com" in naver_url:
        ordered.append(naver_url)
    for url in (publisher_url, naver_url):
        if url and url not in ordered:
            ordered.append(url)
    return ordered


def fetch_article_body(
    publisher_url: str,
    naver_url: str = "",
    *,
    fetch: Callable[[str, float], str] | None = None,
    timeout: float = 10.0,
) -> ArticleBody:
    fetch = fetch or _default_fetch
    last_error = ""
    best_text, best_url = "", ""
    for url in candidate_urls(publisher_url, naver_url):
        try:
            text = extract_body_text(fetch(url, timeout))
        except Exception as exc:  # 네트워크/파싱 실패는 다음 URL 로
            last_error = f"{type(exc).__name__}: {exc}"[:200]
            continue
        if len(text) >= MIN_BODY_CHARS:
            return ArticleBody(status="ok", text=text, source_url=url)
        if len(text) > len(best_text):
            best_text, best_url = text, url
    if best_text:
        return ArticleBody(status="too_short", text=best_text, source_url=best_url)
    return ArticleBody(status="failed", text="", source_url="", error=last_error)
```

- [ ] **Step 4: Run tests**

Run: `/Users/kimjeong-ae/MA_AI_Dossier/.venv/bin/python -m pytest tests/test_dm_article_body.py -q`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add agents/daily_mailing/article_body.py tests/test_dm_article_body.py
git commit -m "feat(daily-mailing): 기사 본문 추출 (네이버 dic_area 우선 + 언론사 선택자 + p 밀도 폴백)"
```

---

### Task A3: LLM wrapper, relevance judge, brief writer, quote verification

**Files:**
- Create: `agents/daily_mailing/llm.py`
- Test: `tests/test_dm_llm.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_dm_llm.py
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agents.daily_mailing.llm import (
    RELEVANCE_SYSTEM,
    WRITER_SYSTEM,
    LLMError,
    OpenAIJsonLLM,
    judge_relevance,
    verify_quote,
    write_brief,
)


class RecordingLLM:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def complete_json(self, *, system, user, model):
        self.calls.append({"system": system, "user": json.loads(user), "model": model})
        return self.response


def test_relevance_keeps_only_known_scope_terms():
    llm = RecordingLLM({"relevant": True, "matched_scope_terms": ["파드셉", "존재하지않는용어"], "reason": "주제"})
    v = judge_relevance(title="파드셉 급여", text="본문", scope_terms=["파드셉", "약가 인하"], llm=llm, model="m")
    assert v.relevant is True
    assert v.matched_scope_terms == ("파드셉",)
    assert llm.calls[0]["system"] == RELEVANCE_SYSTEM
    assert llm.calls[0]["model"] == "m"
    assert llm.calls[0]["user"]["scope_terms"] == ["파드셉", "약가 인하"]


def test_relevance_requires_a_matched_term():
    llm = RecordingLLM({"relevant": True, "matched_scope_terms": [], "reason": "?"})
    v = judge_relevance(title="t", text="b", scope_terms=["파드셉"], llm=llm, model="m")
    assert v.relevant is False


def test_relevance_truncates_article_text():
    llm = RecordingLLM({"relevant": False, "matched_scope_terms": [], "reason": "무관"})
    judge_relevance(title="t", text="가" * 5000, scope_terms=["x"], llm=llm, model="m")
    assert len(llm.calls[0]["user"]["article_text"]) == 2000


def test_write_brief_parses_and_limits_bullets():
    llm = RecordingLLM({"summary_bullets": ["a", " ", "b", "c", "d"], "ma_implication": " 시사점 ",
                        "evidence_quote": " 인용 "})
    d = write_brief(title="t", body="본문", scope_terms=["x"], matched_scope_terms=["x"], llm=llm, model="w")
    assert d.summary_bullets == ("a", "b", "c")
    assert d.ma_implication == "시사점"
    assert d.evidence_quote == "인용"
    assert llm.calls[0]["system"] == WRITER_SYSTEM


@pytest.mark.parametrize("quote,expected", [
    ("요로상피암 1차 치료로 급여 확대", True),
    ("요로상피암   1차 치료로\n급여 확대", True),     # 공백 정규화
    ("“요로상피암 1차 치료로 급여 확대”", True),      # 따옴표 제거
    ("본문에 없는 문장입니다 정말로", False),
    ("짧음", False),                                  # 최소 길이 미달
    ("", False),
])
def test_verify_quote(quote, expected):
    body = "파드셉 병용요법이 요로상피암 1차 치료로 급여 확대 논의에 들어갔다."
    assert verify_quote(quote, body) is expected


def _fake_openai(content=None, exc=None):
    calls = {"n": 0}

    def create(**kwargs):
        calls["n"] += 1
        if exc is not None and calls["n"] == 1:
            raise exc
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return client, calls


def test_openai_wrapper_returns_dict_and_retries_once():
    client, calls = _fake_openai(content='{"ok": 1}', exc=RuntimeError("timeout"))
    llm = OpenAIJsonLLM(client=client, max_retries=1)
    assert llm.complete_json(system="s", user="u", model="m") == {"ok": 1}
    assert calls["n"] == 2


def test_openai_wrapper_raises_llm_error_after_retries():
    client, _ = _fake_openai(content="not json")
    llm = OpenAIJsonLLM(client=client, max_retries=1)
    with pytest.raises(LLMError):
        llm.complete_json(system="s", user="u", model="m")
```

- [ ] **Step 2: Run to verify failure**

Run: `/Users/kimjeong-ae/MA_AI_Dossier/.venv/bin/python -m pytest tests/test_dm_llm.py -q`
Expected: FAIL (`ModuleNotFoundError: agents.daily_mailing.llm`).

- [ ] **Step 3: Implement**

```python
# agents/daily_mailing/llm.py
"""Daily Mailing LLM 단계 — 관련성 판정 · 브리핑 작성 · 인용 검증.

설계 §5 ⑤⑦. 모델은 EngineConfig(settings.json daily_mailing) 에서 주입.
- 관련성: 스콥 항목을 '주제로 다루는' 기사만 통과, matched_scope_terms 는 주어진 항목 중에서만.
- 작성: 본문 근거 요약 2~3개 + MA 시사점 1문장 + 본문 원문 인용. 인용은 verify_quote 로 코드 검증.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Iterable, Protocol

logger = logging.getLogger(__name__)

MIN_QUOTE_CHARS = 10
RELEVANCE_TEXT_CHARS = 2000
WRITER_BODY_CHARS = 6000

RELEVANCE_SYSTEM = """너는 한국 제약 Market Access 뉴스 모니터링 편집자다.
구독자의 모니터링 스콥(키워드·브랜드·회사·질환·정책토픽)과 기사 1건이 주어진다.
기사가 스콥 항목 중 하나 이상을 '주제로 다루는지' 판정한다.
- 스콥 항목이 기사의 핵심 사건·주체·정책이면 relevant=true.
- 스콥 항목이 나열·배경·광고·인사 동정으로 스쳐 언급만 되면 relevant=false.
- 스콥과 무관한 다른 약제·질환·정책 기사는 relevant=false.
반드시 JSON 객체 하나만 출력한다:
{"relevant": true 또는 false, "matched_scope_terms": ["주어진 스콥 항목 원문"], "reason": "한국어 한 문장 근거"}
matched_scope_terms 는 입력 scope_terms 의 문자열 중에서만 고른다."""

WRITER_SYSTEM = """너는 한국 제약 Market Access(MA) 리드를 위한 아침 브리핑 작성자다.
기사 본문(article_body)만 근거로 쓴다. 본문에 없는 사실·수치·전망을 만들지 않는다.
반드시 JSON 객체 하나만 출력한다:
{"summary_bullets": ["...", "..."], "ma_implication": "...", "evidence_quote": "..."}
- summary_bullets: 2~3개. 각 60자 안팎의 사실 요약(누가·무엇을·언제). 본문 사실만.
- ma_implication: 한 문장. 급여·약가·등재·위험분담·경쟁 관점에서 이 기사가 구독 스콥(matched_scope_terms)에 갖는 의미. 본문으로 뒷받침되지 않으면 빈 문자열.
- evidence_quote: ma_implication 의 근거가 되는 본문 문장을 글자 그대로 복사(20~200자). ma_implication 이 빈 문자열이면 빈 문자열."""


class LLMError(RuntimeError):
    """LLM 호출/파싱 실패 (재시도 소진)."""


class JsonLLM(Protocol):
    def complete_json(self, *, system: str, user: str, model: str) -> dict: ...


class OpenAIJsonLLM:
    """OpenAI chat.completions JSON 모드 래퍼. 실패 시 max_retries 회 재시도 후 LLMError."""

    def __init__(self, api_key: str | None = None, *, timeout: float = 60.0,
                 max_retries: int = 1, client: Any | None = None):
        if client is None:
            from openai import OpenAI

            key = api_key or os.environ.get("OPENAI_API_KEY")
            if not key:
                raise LLMError("OPENAI_API_KEY is required")
            client = OpenAI(api_key=key, timeout=timeout)
        self._client = client
        self._max_retries = max_retries

    def complete_json(self, *, system: str, user: str, model: str) -> dict:
        last: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                resp = self._client.chat.completions.create(
                    model=model,
                    temperature=0.1,
                    response_format={"type": "json_object"},
                    messages=[{"role": "system", "content": system},
                              {"role": "user", "content": user}],
                )
                data = json.loads(resp.choices[0].message.content or "{}")
                if not isinstance(data, dict):
                    raise ValueError("LLM JSON 이 객체가 아님")
                return data
            except Exception as exc:
                last = exc
                logger.warning("[daily_mailing.llm] 호출 실패 (%d/%d): %s",
                               attempt + 1, self._max_retries + 1, exc)
        raise LLMError(f"LLM 호출 실패: {last}")


@dataclass(frozen=True)
class RelevanceVerdict:
    relevant: bool
    matched_scope_terms: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class BriefDraft:
    summary_bullets: tuple[str, ...]
    ma_implication: str
    evidence_quote: str


def judge_relevance(*, title: str, text: str, scope_terms: Iterable[str],
                    llm: JsonLLM, model: str) -> RelevanceVerdict:
    terms = [str(t) for t in scope_terms]
    user = json.dumps({"scope_terms": terms, "title": title,
                       "article_text": (text or "")[:RELEVANCE_TEXT_CHARS]}, ensure_ascii=False)
    data = llm.complete_json(system=RELEVANCE_SYSTEM, user=user, model=model)
    allowed = set(terms)
    matched = tuple(t for t in (data.get("matched_scope_terms") or [])
                    if isinstance(t, str) and t in allowed)
    return RelevanceVerdict(
        relevant=bool(data.get("relevant")) and bool(matched),
        matched_scope_terms=matched,
        reason=str(data.get("reason") or "")[:300],
    )


def write_brief(*, title: str, body: str, scope_terms: Iterable[str],
                matched_scope_terms: Iterable[str], llm: JsonLLM, model: str) -> BriefDraft:
    user = json.dumps({"scope_terms": list(scope_terms),
                       "matched_scope_terms": list(matched_scope_terms),
                       "title": title,
                       "article_body": (body or "")[:WRITER_BODY_CHARS]}, ensure_ascii=False)
    data = llm.complete_json(system=WRITER_SYSTEM, user=user, model=model)
    bullets = tuple(str(b).strip() for b in (data.get("summary_bullets") or []) if str(b).strip())[:3]
    return BriefDraft(
        summary_bullets=bullets,
        ma_implication=str(data.get("ma_implication") or "").strip(),
        evidence_quote=str(data.get("evidence_quote") or "").strip(),
    )


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def verify_quote(quote: str, body: str) -> bool:
    """인용문이 본문에 (공백 정규화 후) 그대로 존재하는지. 따옴표는 양끝에서 제거."""
    q = _norm((quote or "").strip().strip("\"'“”‘’"))
    return len(q) >= MIN_QUOTE_CHARS and q in _norm(body)
```

- [ ] **Step 4: Run tests**

Run: `/Users/kimjeong-ae/MA_AI_Dossier/.venv/bin/python -m pytest tests/test_dm_llm.py -q`
Expected: 12 passed.

- [ ] **Step 5: Commit**

```bash
git add agents/daily_mailing/llm.py tests/test_dm_llm.py
git commit -m "feat(daily-mailing): LLM 관련성 판정·브리핑 작성·인용 검증"
```

---

### Task A4: BriefItem, quality gate, subject and briefing render

**Files:**
- Create: `agents/daily_mailing/brief.py`
- Test: `tests/test_dm_brief.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_dm_brief.py
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
```

- [ ] **Step 2: Run to verify failure**

Run: `/Users/kimjeong-ae/MA_AI_Dossier/.venv/bin/python -m pytest tests/test_dm_brief.py -q`
Expected: FAIL (`ModuleNotFoundError: agents.daily_mailing.brief`).

- [ ] **Step 3: Implement**

```python
# agents/daily_mailing/brief.py
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

WEEKDAYS_KR = "월화수목금토일"


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


def evaluate_brief_quality(items: Iterable[BriefItem], *, hold_reasons: Iterable[str] = ()) -> BriefQualityReport:
    all_items = list(items)
    scope_items = [i for i in all_items if i.section == "scope"]
    top_items = [i for i in all_items if i.section == "top_ma_news"]
    blocking = [str(r) for r in hold_reasons if str(r)]
    warnings: list[str] = []
    for item in all_items:
        if item.ma_implication and not item.quote_verified:
            blocking.append("unverified_implication_rendered")
        if not item.summary_bullets:
            blocking.append("empty_summary")
        if item.body_status != "ok":
            warnings.append("body_not_extracted")
    for item in scope_items:
        if not item.matched_scope_terms:
            blocking.append("scope_item_without_match")
    implications = [i.ma_implication for i in all_items if i.ma_implication]
    if len(implications) != len(set(implications)):
        blocking.append("duplicate_implication")
    blocking_t = tuple(sorted(set(blocking)))
    warnings_t = tuple(sorted(set(warnings)))
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
    return f"{dt.month}/{dt.day} {dt:%H:%M}"


def _split(items: Iterable[BriefItem]) -> tuple[list[BriefItem], list[BriefItem]]:
    items = list(items)
    return ([i for i in items if i.section == "scope"], [i for i in items if i.section == "top_ma_news"])


def _item_html(index: int, item: BriefItem) -> str:
    url = escape(item.publisher_url or item.naver_url, quote=True)
    meta = " · ".join(x for x in (escape(item.source_name), escape(_published_label(item.published_at))) if x)
    bullets = "".join(f'<li style="margin:0 0 4px;">{escape(b)}</li>' for b in item.summary_bullets)
    parts = [
        '<div style="margin:0 0 20px;padding:0 0 16px;border-bottom:1px solid #E5E7EB;">',
        f'<div style="font-size:16px;font-weight:700;line-height:1.45;margin:0 0 4px;">{index}. '
        f'<a href="{url}" style="color:#0F172A;text-decoration:none;">{escape(item.title)}</a></div>',
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
    head = f"{index}. [{item.title}]({item.publisher_url or item.naver_url}) — {item.source_name} {_published_label(item.published_at)}"
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
```

- [ ] **Step 4: Run tests**

Run: `/Users/kimjeong-ae/MA_AI_Dossier/.venv/bin/python -m pytest tests/test_dm_brief.py -q`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add agents/daily_mailing/brief.py tests/test_dm_brief.py
git commit -m "feat(daily-mailing): BriefItem·품질 게이트(no_news 발송 허용)·브리핑형 HTML/MD 렌더"
```

---

### Task A5: Archive candidates (read-only `competitor_news`)

**Files:**
- Create: `agents/daily_mailing/archive_candidates.py`
- Test: `tests/test_dm_archive_candidates.py`

`competitor_news` (see `agents/competitor_news_agent.py`) has `title, url, naver_link, description, pub_date ('YYYY-MM-DD'), source_name, tier`. It is filled daily 06:30 by the scheduler. `pub_date` has no time, so archive items are filtered by date (today/yesterday KST) in SQL — **not** by `filter_recent_items`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_dm_archive_candidates.py
from __future__ import annotations

import sqlite3
from datetime import date

from agents.daily_mailing.archive_candidates import load_archive_candidates
from agents.daily_mailing.source_registry import SourceInfo, SourceRegistry


def _db(tmp_path):
    path = tmp_path / "a.db"
    with sqlite3.connect(path) as c:
        c.execute("""CREATE TABLE competitor_news (id INTEGER PRIMARY KEY, url_hash TEXT, brand TEXT,
            title TEXT, url TEXT, naver_link TEXT, description TEXT, pub_date TEXT, source_name TEXT, tier INTEGER)""")
        rows = [
            ("파드셉 급여 확대 논의", "https://www.yakup.com/1?utm_source=x", "https://n.news.naver.com/1", "요로상피암", "2026-09-18", "약업신문", 1),
            ("파드셉 중복 URL", "https://www.yakup.com/1", "", "", "2026-09-17", "약업신문", 1),
            ("키트루다 뉴스", "https://www.hitnews.co.kr/2", "", "파드셉 병용", "2026-09-17", "히트뉴스", 1),
            ("오래된 파드셉", "https://www.yakup.com/3", "", "", "2026-09-10", "약업신문", 1),
            ("무관 기사", "https://www.yakup.com/4", "", "", "2026-09-18", "약업신문", 1),
        ]
        c.executemany("INSERT INTO competitor_news (url_hash, brand, title, url, naver_link, description, pub_date, source_name, tier) VALUES ('h','b',?,?,?,?,?,?,?)", rows)
    return path


def test_loads_matching_recent_unique(tmp_path):
    registry = SourceRegistry([SourceInfo(name="약업신문", domains=("yakup.com",), source_tier="media_tier_A", weight=1.3)])
    items = load_archive_candidates(["파드셉"], db_path=_db(tmp_path), today=date(2026, 9, 18), registry=registry)
    titles = [i.title for i in items]
    assert titles == ["파드셉 급여 확대 논의", "키트루다 뉴스"]
    first = items[0]
    assert first.publisher_url == "https://www.yakup.com/1"
    assert first.discovery_channel == "news_archive"
    assert first.source_tier == "media_tier_A" and first.source_weight == 1.3
    assert first.matched_keywords == ("파드셉",)
    assert first.published_at == "2026-09-18T00:00:00+09:00"


def test_missing_db_or_table_or_terms_returns_empty(tmp_path):
    assert load_archive_candidates(["x"], db_path=tmp_path / "none.db", today=date(2026, 9, 18)) == []
    empty = tmp_path / "e.db"
    sqlite3.connect(empty).close()
    assert load_archive_candidates(["x"], db_path=empty, today=date(2026, 9, 18)) == []
    assert load_archive_candidates([], db_path=_db(tmp_path), today=date(2026, 9, 18)) == []
```

- [ ] **Step 2: Run to verify failure**

Run: `/Users/kimjeong-ae/MA_AI_Dossier/.venv/bin/python -m pytest tests/test_dm_archive_candidates.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

```python
# agents/daily_mailing/archive_candidates.py
"""뉴스 아카이브(competitor_news) 에서 스콥 매칭 후보를 읽는다 — 읽기 전용.

설계 §5 ②. 스케줄러가 매일 06:30 수집한 기사를 Naver 검색 후보와 병합해,
Naver 호출 상한/429 상황에서도 후보 풀을 유지한다. pub_date 는 날짜만 있으므로
시간 창이 아니라 날짜(오늘·어제 KST)로 거른다.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

from .discovery import NewsDiscoveryItem, canonicalize_url
from .source_registry import SourceRegistry

_SCAN_LIMIT = 2000


def load_archive_candidates(
    scope_terms: Iterable[str],
    *,
    db_path: str | Path,
    today: date,
    days: int = 1,
    registry: SourceRegistry | None = None,
    limit: int = 200,
) -> list[NewsDiscoveryItem]:
    terms = [t.strip() for t in scope_terms if t and t.strip()]
    path = Path(db_path)
    if not terms or not path.exists():
        return []
    since = (today - timedelta(days=days)).isoformat()
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
            rows = conn.execute(
                "SELECT title, url, naver_link, description, pub_date, source_name "
                "FROM competitor_news WHERE pub_date >= ? ORDER BY pub_date DESC, id DESC LIMIT ?",
                (since, _SCAN_LIMIT),
            ).fetchall()
    except sqlite3.Error:
        return []
    out: list[NewsDiscoveryItem] = []
    seen: set[str] = set()
    for title, url, naver_link, description, pub_date, source_name in rows:
        haystack = f"{title or ''} {description or ''}".lower()
        matched = tuple(t for t in terms if t.lower() in haystack)
        if not matched or not url:
            continue
        canonical = canonicalize_url(url)
        if canonical in seen:
            continue
        seen.add(canonical)
        source = registry.match_url(canonical) if registry else None
        out.append(NewsDiscoveryItem(
            title=str(title or ""),
            publisher_url=canonical,
            naver_url=str(naver_link or ""),
            description=str(description or ""),
            published_at=f"{pub_date}T00:00:00+09:00" if len(str(pub_date or "")) == 10 else str(pub_date or ""),
            keyword=matched[0],
            discovery_channel="news_archive",
            source_name=source.name if source else str(source_name or "Unknown publisher"),
            source_tier=source.source_tier if source else "unregistered",
            source_weight=source.weight if source else 1.0,
            ma_depth=(source.ma_depth or 0) if source else 0,
            novelty=(source.novelty or 0) if source else 0,
            volume=(source.volume or 0) if source else 0,
            matched_keywords=matched,
        ))
        if len(out) >= limit:
            break
    return out
```

- [ ] **Step 4: Run tests**

Run: `/Users/kimjeong-ae/MA_AI_Dossier/.venv/bin/python -m pytest tests/test_dm_archive_candidates.py -q`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add agents/daily_mailing/archive_candidates.py tests/test_dm_archive_candidates.py
git commit -m "feat(daily-mailing): 뉴스 아카이브 스콥 매칭 후보 (읽기 전용)"
```

---

### Task A6: Engine orchestration + settings

**Files:**
- Create: `agents/daily_mailing/engine.py`
- Modify: `config/settings.json`
- Test: `tests/test_dm_engine.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_dm_engine.py
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agents.daily_mailing.article_body import ArticleBody
from agents.daily_mailing.brief import BriefItem
from agents.daily_mailing.dashboard_scope import DashboardScope
from agents.daily_mailing.engine import (
    EngineConfig,
    EngineDeps,
    generate_run,
    generate_top_ma_news,
    top_ma_news_terms,
)
from agents.daily_mailing.llm import RELEVANCE_SYSTEM, LLMError
from agents.daily_mailing.source_registry import SourceInfo, SourceRegistry

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 18, 8, 0, tzinfo=KST)
BODY = ("파드셉과 키트루다 병용요법이 요로상피암 1차 치료로 급여 확대 논의에 들어갔다. "
        "심평원 암질심은 다음 달 안건으로 상정할 예정이다. ") * 4


def _raw(title, url):
    return {"title": title, "originallink": url, "link": url, "description": f"{title} 설명",
            "pubDate": "Fri, 18 Sep 2026 06:00:00 +0900"}


class FakeNaver:
    def __init__(self, by_query):
        self.by_query = by_query
        self.queries = []

    def search(self, query, display=20, sort="date"):
        self.queries.append(query)
        return self.by_query.get(query, [])


class FakeLLM:
    def __init__(self, quote_mode="verbatim", fail_relevance=False, fail_writer=False):
        self.quote_mode = quote_mode
        self.fail_relevance = fail_relevance
        self.fail_writer = fail_writer

    def complete_json(self, *, system, user, model):
        data = json.loads(user)
        if system == RELEVANCE_SYSTEM:
            if self.fail_relevance:
                raise LLMError("down")
            hits = [t for t in data["scope_terms"] if t in data["title"]]
            return {"relevant": bool(hits), "matched_scope_terms": hits, "reason": "제목 주제" if hits else "무관"}
        if self.fail_writer:
            raise LLMError("down")
        quote = data["article_body"][:40] if self.quote_mode == "verbatim" else "본문에 없는 인용 문장입니다 정말로"
        return {"summary_bullets": [f"{data['title']} 요약"], "ma_implication": f"{data['title']} 시사점",
                "evidence_quote": quote}


def _deps(naver, llm=None, body_status="ok"):
    registry = SourceRegistry([SourceInfo(name="약업신문", domains=("yakup.com",), source_tier="media_tier_A")])
    text = BODY if body_status == "ok" else ""
    return EngineDeps(naver=naver, llm=llm or FakeLLM(), registry=registry,
                      fetch_body=lambda p, n: ArticleBody(body_status, text, p))


def _scope(**kw):
    base = dict(subscription_id="19", owner_email="o@msd.com", recipients=("r@msd.com",),
                keywords=("약가 인하",), brands=("파드셉",))
    base.update(kw)
    return DashboardScope(**base)


PADCEV = _raw("파드셉 요로상피암 급여 확대", "https://www.yakup.com/p1")
ENFLON = _raw("한국MSD 엔플론시아 RSV 예방", "https://www.yakup.com/m1")


def test_scope_only_queries_and_relevant_items_only():
    naver = FakeNaver({"파드셉": [PADCEV, ENFLON], "약가 인하": [ENFLON]})
    result = generate_run(_scope(), deps=_deps(naver), name="파드셉", now=NOW)
    assert naver.queries == ["약가 인하", "파드셉"]               # 페르소나 기본 키워드 합산 없음
    assert [i.title for i in result.items] == ["파드셉 요로상피암 급여 확대"]  # MSD 대표 기사 강제 삽입 없음
    item = result.items[0]
    assert item.quote_verified is True and item.ma_implication
    assert result.status == "ready"
    assert result.subject == "[MA Daily] 파드셉 · 9/18(금)"
    assert result.run_date == "2026-09-18"
    assert result.run_id.startswith("dm_19_20260918_080000")


def test_payload_contract_for_slice_b():
    naver = FakeNaver({"파드셉": [PADCEV]})
    result = generate_run(_scope(), deps=_deps(naver), name="파드셉", now=NOW)
    p = result.payload
    for key in ("run_id", "generated_at", "window_label", "lookback_hours", "keywords", "media",
                "subscription_id", "owner_email", "recipients", "discovered_count", "recent_count",
                "selected_count", "status", "quality_report", "personas", "items", "dashboard_scope",
                "run_date", "is_test", "email_subject", "email_html", "delivery_status", "approval_status"):
        assert key in p, key
    assert p["subscription_id"] == 19
    assert p["recipients"] == ["r@msd.com"]
    assert p["email_html"] == result.html
    assert p["items"][0]["evidence_quotes"]
    card_by_title = {a["title"]: a for a in result.articles}
    assert card_by_title["파드셉 요로상피암 급여 확대"]["selected_for_draft"] is True
    assert "관련성 통과" in card_by_title["파드셉 요로상피암 급여 확대"]["reviewer_note"]


def test_no_relevant_articles_is_no_news():
    naver = FakeNaver({"파드셉": [ENFLON]})
    result = generate_run(_scope(), deps=_deps(naver), name="파드셉", now=NOW)
    assert result.status == "no_news"
    assert "특이사항 없음" in result.html
    assert result.articles[0]["review_status"] == "excluded"


def test_unverified_quote_drops_implication_but_keeps_item():
    naver = FakeNaver({"파드셉": [PADCEV]})
    result = generate_run(_scope(), deps=_deps(naver, FakeLLM(quote_mode="fake")), name="파드셉", now=NOW)
    item = result.items[0]
    assert item.ma_implication == "" and item.quote_verified is False
    assert result.stats["quote_rejected"] == 1
    assert result.status == "ready"


def test_body_missing_uses_snippet_without_implication():
    naver = FakeNaver({"파드셉": [PADCEV]})
    result = generate_run(_scope(), deps=_deps(naver, body_status="failed"), name="파드셉", now=NOW)
    item = result.items[0]
    assert item.body_status == "snippet"
    assert item.summary_bullets == ("파드셉 요로상피암 급여 확대 설명",)
    assert item.ma_implication == ""


def test_llm_down_holds_run():
    naver = FakeNaver({"파드셉": [PADCEV]})
    result = generate_run(_scope(), deps=_deps(naver, FakeLLM(fail_relevance=True)), name="파드셉", now=NOW)
    assert result.status == "held"
    assert "llm_unavailable" in result.quality_report["blocking_reasons"]


def test_empty_scope_holds_without_calls():
    naver = FakeNaver({})
    result = generate_run(_scope(keywords=(), brands=()), deps=_deps(naver), name="빈 스콥", now=NOW)
    assert result.status == "held"
    assert "empty_scope" in result.quality_report["blocking_reasons"]
    assert naver.queries == []


def test_top_ma_news_appended_deduped_and_optional():
    naver = FakeNaver({"파드셉": [PADCEV]})
    top = [BriefItem(title="약평위 결과", publisher_url="https://x/1", summary_bullets=("s",),
                     matched_scope_terms=("약평위",), section="top_ma_news"),
           BriefItem(title="dup", publisher_url="https://www.yakup.com/p1", summary_bullets=("s",),
                     matched_scope_terms=("급여",), section="top_ma_news")]
    result = generate_run(_scope(), deps=_deps(naver), name="파드셉", now=NOW, top_ma_news=top)
    assert [i.title for i in result.top_ma_news] == ["약평위 결과"]
    assert "오늘의 주요 MA 뉴스" in result.html
    off = generate_run(_scope(include_top_ma_signals=False), deps=_deps(FakeNaver({"파드셉": [PADCEV]})),
                       name="파드셉", now=NOW, top_ma_news=top)
    assert off.top_ma_news == []


def test_test_run_subject_and_id():
    result = generate_run(_scope(), deps=_deps(FakeNaver({})), name="파드셉", now=NOW, is_test=True)
    assert result.subject.startswith("[TEST] ")
    assert result.run_id.endswith("_test")
    assert result.payload["is_test"] is True


def test_query_cap_from_config():
    naver = FakeNaver({})
    generate_run(_scope(keywords=("a", "b", "c"), brands=()), deps=_deps(naver),
                 config=EngineConfig(max_naver_queries=2), name="n", now=NOW)
    assert naver.queries == ["a", "b"]


def test_generate_top_ma_news_uses_persona_terms():
    terms = top_ma_news_terms()
    assert "약평위" in terms and "심평원" in terms and "키트루다" not in terms
    naver = FakeNaver({"약평위": [_raw("약평위 9월 결과 발표", "https://www.yakup.com/t1")]})
    items = generate_top_ma_news(deps=_deps(naver), now=NOW)
    assert [i.title for i in items] == ["약평위 9월 결과 발표"]
    assert items[0].section == "top_ma_news"


def test_engine_config_from_settings_file():
    settings = json.loads((Path(__file__).resolve().parents[1] / "config" / "settings.json").read_text(encoding="utf-8"))
    config = EngineConfig.from_settings(settings)
    assert config.relevance_model == "gpt-4o-mini"
    assert config.writer_model == "gpt-4o"
    assert EngineConfig.from_settings({"daily_mailing": {"brief_limit": 3, "unknown": 1}}).brief_limit == 3
```

- [ ] **Step 2: Run to verify failure**

Run: `/Users/kimjeong-ae/MA_AI_Dossier/.venv/bin/python -m pytest tests/test_dm_engine.py -q`
Expected: FAIL (`ModuleNotFoundError: agents.daily_mailing.engine`).

- [ ] **Step 3: Add settings**

In `config/settings.json`, add a top-level key (keep the file's existing formatting; add a comma after the preceding top-level entry):

```json
  "daily_mailing": {
    "relevance_model": "gpt-4o-mini",
    "writer_model": "gpt-4o",
    "max_candidates": 15,
    "max_naver_queries": 40,
    "brief_limit": 5,
    "top_ma_news_limit": 2,
    "naver_display": 20
  }
```

Verify: `/Users/kimjeong-ae/MA_AI_Dossier/.venv/bin/python -c "import json;print(json.load(open('config/settings.json'))['daily_mailing'])"`

- [ ] **Step 4: Implement the engine**

```python
# agents/daily_mailing/engine.py
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
```

- [ ] **Step 5: Run tests**

Run: `/Users/kimjeong-ae/MA_AI_Dossier/.venv/bin/python -m pytest tests/test_dm_engine.py -q`
Expected: 12 passed. If `test_scope_only_queries...` fails on query order, check `DashboardScope.expanded_keywords()` order (keywords, companies, brands, …) — the expected order `["약가 인하", "파드셉"]` follows it.

- [ ] **Step 6: Commit**

```bash
git add agents/daily_mailing/engine.py config/settings.json tests/test_dm_engine.py
git commit -m "feat(daily-mailing): 콘텐츠 엔진 generate_run/generate_top_ma_news + settings daily_mailing"
```

---

### Task A7: Pilot CLI (no send, no DB write)

**Files:**
- Create: `scripts/daily_mailing_pilot.py`
- Test: `tests/test_dm_pilot_cli.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dm_pilot_cli.py
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from agents.daily_mailing.article_body import ArticleBody
from agents.daily_mailing.engine import EngineConfig, EngineDeps
from agents.daily_mailing.llm import RELEVANCE_SYSTEM
from agents.daily_mailing.source_registry import SourceRegistry
from scripts.daily_mailing_pilot import run_pilot

NOW = datetime(2026, 9, 18, 8, 0, tzinfo=timezone(timedelta(hours=9)))
BODY = "파드셉 병용요법이 요로상피암 1차 치료로 급여 확대 논의에 들어갔다. " * 6


class Naver:
    def search(self, query, display=20, sort="date"):
        if query == "파드셉":
            return [{"title": "파드셉 급여 확대", "originallink": "https://www.yakup.com/p1",
                     "link": "https://www.yakup.com/p1", "description": "설명",
                     "pubDate": "Fri, 18 Sep 2026 06:00:00 +0900"}]
        return []


class LLM:
    def complete_json(self, *, system, user, model):
        data = json.loads(user)
        if system == RELEVANCE_SYSTEM:
            hits = [t for t in data["scope_terms"] if t in data["title"]]
            return {"relevant": bool(hits), "matched_scope_terms": hits, "reason": "주제"}
        return {"summary_bullets": ["요약"], "ma_implication": "시사점", "evidence_quote": data["article_body"][:30]}


def test_run_pilot_writes_outputs(tmp_path):
    scope_dir = tmp_path / "scopes"
    scope_dir.mkdir()
    (scope_dir / "19.json").write_text(json.dumps({"subscription_id": "19", "name": "파드셉 <테스트>",
                                                   "brands": ["파드셉"], "recipients": ["r@msd.com"]},
                                                  ensure_ascii=False), encoding="utf-8")
    deps = EngineDeps(naver=Naver(), llm=LLM(), registry=SourceRegistry([]),
                      fetch_body=lambda p, n: ArticleBody("ok", BODY, p))
    out = tmp_path / "out"
    summaries = run_pilot(sorted(scope_dir.glob("*.json")), out, deps=deps, config=EngineConfig(), now=NOW)
    assert summaries[0]["status"] == "ready"
    assert (out / "sub_19.html").read_text(encoding="utf-8").count("파드셉 급여 확대") >= 1
    judgments = json.loads((out / "sub_19.judgments.json").read_text(encoding="utf-8"))
    assert judgments["judgments"][0]["selected"] is True
    index = (out / "index.html").read_text(encoding="utf-8")
    assert "파드셉 &lt;테스트&gt;" in index
    assert "sub_19.html" in index
```

- [ ] **Step 2: Run to verify failure**

Run: `/Users/kimjeong-ae/MA_AI_Dossier/.venv/bin/python -m pytest tests/test_dm_pilot_cli.py -q`
Expected: FAIL (`ModuleNotFoundError: scripts.daily_mailing_pilot`). If `scripts` is not importable as a package in tests, check how other tests import from `scripts/` (e.g. `grep -rn "from scripts" tests | head`) and follow the same approach (an empty `scripts/__init__.py` is acceptable if none exists).

- [ ] **Step 3: Implement**

```python
# scripts/daily_mailing_pilot.py
"""Daily Mailing 파일럿 — 실제 구독 스콥으로 브리핑 메일을 생성만 한다 (발송·DB 쓰기 없음).

설계 §9 파일럿 검수 게이트: 사용자에게 HTML 미리보기 + 기사별 판정 근거를 제출한다.

사용:
  .venv/bin/python scripts/daily_mailing_pilot.py --scope-dir <dir> --out-dir <dir>
  (<dir>/<subscription_id>.json = dashboard_scope 계약. 선택 키 'name' 은 메일 제목에 사용)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from html import escape
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agents.daily_mailing.brief import BriefItem  # noqa: E402
from agents.daily_mailing.dashboard_scope import load_dashboard_scope  # noqa: E402
from agents.daily_mailing.engine import (  # noqa: E402
    EngineConfig,
    EngineDeps,
    generate_run,
    generate_top_ma_news,
)


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"\''))


def _judgments(articles: list[dict]) -> list[dict]:
    return [{
        "title": a.get("title"),
        "url": a.get("publisher_url"),
        "source": a.get("source_name"),
        "selected": bool(a.get("selected_for_draft")),
        "review_status": a.get("review_status"),
        "matched": a.get("matched_keywords") or [],
        "note": a.get("reviewer_note"),
        "body": a.get("verification_caveat"),
    } for a in articles]


def render_index(summaries: list[dict], top: list[BriefItem], generated_at: str) -> str:
    blocks = []
    for s in summaries:
        rows = "".join(
            "<tr>"
            f"<td>{'✅' if j['selected'] else ('—' if j['review_status'] != 'excluded' else '✗')}</td>"
            f"<td><a href=\"{escape(j['url'] or '', quote=True)}\">{escape(j['title'] or '')}</a></td>"
            f"<td>{escape(j['source'] or '')}</td>"
            f"<td>{escape(', '.join(j['matched']))}</td>"
            f"<td>{escape(j['note'] or '')}</td>"
            f"<td>{escape(j['body'] or '')}</td>"
            "</tr>"
            for j in s["judgments"]
        )
        blocks.append(
            f"<h2>{escape(s['name'])} <small>(구독 {escape(str(s['subscription_id']))})</small></h2>"
            f"<p>상태 <b>{escape(s['status'])}</b> · 메일 기사 {s['items']}건 · 주요 MA 뉴스 {s['top_ma_news']}건 · "
            f"판정 {s['judged']}건(제외 {s['excluded']}) · "
            f"<a href=\"{escape(s['html'], quote=True)}\">메일 미리보기 열기</a></p>"
            "<table><thead><tr><th>선정</th><th>기사</th><th>매체</th><th>매칭 스콥</th><th>판정 근거</th><th>본문</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
        )
    top_list = "".join(f"<li>{escape(t.title)}</li>" for t in top) or "<li>(없음)</li>"
    return (
        "<!doctype html><html lang=\"ko\"><head><meta charset=\"utf-8\"><title>Daily Mailing 파일럿</title>"
        "<style>body{font-family:-apple-system,'Malgun Gothic',sans-serif;max-width:1100px;margin:24px auto;padding:0 16px;color:#0F172A}"
        "table{border-collapse:collapse;width:100%;font-size:13px}td,th{border:1px solid #E5E7EB;padding:6px;vertical-align:top;text-align:left}"
        "h2{margin-top:36px}small{color:#64748B;font-weight:400}</style></head><body>"
        f"<h1>Daily Mailing 파일럿 검수</h1><p>생성 {escape(generated_at)} · 발송 없음 · DB 쓰기 없음</p>"
        f"<h3>오늘의 주요 MA 뉴스 (구독 공통)</h3><ul>{top_list}</ul>"
        + "".join(blocks) + "</body></html>"
    )


def run_pilot(scope_paths: list[Path], out_dir: Path, *, deps: EngineDeps, config: EngineConfig,
              now: datetime | None = None) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    top = generate_top_ma_news(deps=deps, config=config, now=now)
    summaries: list[dict] = []
    for path in scope_paths:
        raw = json.loads(path.read_text(encoding="utf-8"))
        scope = load_dashboard_scope(path)
        name = str(raw.get("name") or scope.subscription_id)
        result = generate_run(scope, deps=deps, config=config, name=name, now=now, top_ma_news=top)
        stem = f"sub_{scope.subscription_id}"
        (out_dir / f"{stem}.html").write_text(result.html, encoding="utf-8")
        (out_dir / f"{stem}.md").write_text(result.markdown, encoding="utf-8")
        judgments = _judgments(result.articles)
        (out_dir / f"{stem}.judgments.json").write_text(json.dumps({
            "subject": result.subject, "status": result.status, "quality_report": result.quality_report,
            "stats": result.stats, "judgments": judgments,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        summaries.append({
            "subscription_id": scope.subscription_id, "name": name, "status": result.status,
            "subject": result.subject, "items": len(result.items), "top_ma_news": len(result.top_ma_news),
            "judged": len(result.articles),
            "excluded": sum(1 for a in result.articles if a.get("review_status") == "excluded"),
            "html": f"{stem}.html", "judgments": judgments, "stats": result.stats,
        })
    generated_at = (now or datetime.now()).isoformat(timespec="minutes")
    (out_dir / "index.html").write_text(render_index(summaries, top, generated_at), encoding="utf-8")
    return summaries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Daily Mailing 파일럿 (발송·DB 쓰기 없음)")
    parser.add_argument("--scope-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--env", default=str(REPO_ROOT / "config" / ".env"))
    parser.add_argument("--db", default=str(REPO_ROOT / "data" / "db" / "drug_prices.db"),
                        help="뉴스 아카이브(competitor_news) 읽기 전용 소스. 없으면 Naver 만 사용")
    parser.add_argument("--settings", default=str(REPO_ROOT / "config" / "settings.json"))
    args = parser.parse_args(argv)

    _load_env(Path(args.env))
    settings = json.loads(Path(args.settings).read_text(encoding="utf-8"))
    config = EngineConfig.from_settings(settings)
    db_path = args.db if Path(args.db).exists() else None
    deps = EngineDeps.default(db_path=db_path)
    paths = [p for p in sorted(Path(args.scope_dir).glob("*.json")) if p.name != "scopes_index.json"]
    summaries = run_pilot(paths, Path(args.out_dir), deps=deps, config=config)
    print(json.dumps([{k: s[k] for k in ("subscription_id", "name", "status", "items", "judged", "excluded")}
                      for s in summaries], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests**

Run: `/Users/kimjeong-ae/MA_AI_Dossier/.venv/bin/python -m pytest tests/test_dm_pilot_cli.py -q`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/daily_mailing_pilot.py tests/test_dm_pilot_cli.py
git commit -m "feat(daily-mailing): 파일럿 CLI — 실제 스콥 → 메일 HTML·판정 근거·index (발송/DB 쓰기 없음)"
```

(If you added `scripts/__init__.py`, include it in this commit.)

---

### Task A8: Regression run

- [ ] **Step 1: Run the whole daily-mailing test surface**

Run:
```bash
/Users/kimjeong-ae/MA_AI_Dossier/.venv/bin/python -m pytest tests/test_dm_discovery_robustness.py tests/test_dm_article_body.py tests/test_dm_llm.py tests/test_dm_brief.py tests/test_dm_archive_candidates.py tests/test_dm_engine.py tests/test_dm_pilot_cli.py tests/test_daily_mailing_final_gates.py tests/test_daily_mailing_bridge.py tests/test_daily_mailing_kanban_migration.py tests/test_daily_mailing_sync.py tests/test_dashboard_scope.py -q
```
Expected: all pass. Fix any regression in files you own (A-owned files only). Report any failure in a B-owned file instead of editing it.

- [ ] **Step 2: Import smoke**

Run: `/Users/kimjeong-ae/MA_AI_Dossier/.venv/bin/python -c "import agents.daily_mailing.engine, scripts.daily_mailing_pilot; print('ok')"`
Expected: `ok`.

---

## Self-review checklist (done by plan author)

- Spec §5 ①–⑩ and §5.1: ①⑥ Task A6 (`_select`, scope-only terms), ② A1+A5+A6, ③ A6 (`filter_recent_items` for Naver, date filter for archive), ④ A2, ⑤ A3+A6, ⑦ A3+A6, ⑧ A4, ⑨ A6 `_card`, ⑩ A4, §5.1 A6 `generate_top_ma_news`. §9 pilot: A7.
- Contract names used by Slice B: `BriefItem.to_dict/from_dict`, `EngineConfig.from_settings`, `EngineDeps.default(db_path=)`, `generate_run(...)`, `generate_top_ma_news(...)`, `RunResult.payload/articles/status/run_id` — all defined in A4/A6.
