"""정부 키워드 AI 요약 — Home 위젯용.

정부 관련 키워드 (보건복지부/건보공단/심평원 등 · keyword_cloud 에 등록된 키워드)
→ 지난 1개월 Naver 뉴스 수집
→ OpenAI + Gemini 에 독립 요약 요청 (마크다운 500자 이내)
→ 두 리뷰어 응답이 오면 consensus, 1개만 오면 단독 반환.
→ 일자별 cache (`data/cache/gov_summary/YYYY-MM-DD.json`).

사용: `get_government_summary(refresh=False)` → {"markdown", "sources", "reviewers": [...], "updated_at"}
"""
from __future__ import annotations

import json
import logging
import os
import re
import ssl
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from agents.naver_news import get_client

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
CACHE_DIR = BASE_DIR / "data" / "cache" / "gov_summary"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

MAX_MD_CHARS = 500

SYSTEM_PROMPT = """당신은 한국 MSD Marketing & Market Access 팀의 정책 동향 요약 애널리스트입니다.
제공된 최근 1개월 보건당국(보건복지부·건보공단·심평원·식약처 등) **아카이브 뉴스**(번호 부여됨)를
바탕으로, **마케팅·Market Access 관점에서 함의 있는 내용**만 추려 한국어 마크다운으로 요약하세요.

원칙:
- 총 글자수는 한글·공백 포함 500자 이내. 간결·factual·근거 중심.
- 구성: (1) 핵심 흐름 1~2줄 불릿 3개 (2) 간단한 함의 1~2줄. 불필요한 서론 금지.
- 추측·과장 금지. 기사 본문에 없는 숫자/기관명 생성 금지.
- 동일 이슈가 여러 번 보도되면 1개로 합친다.
- 매출·주가·마케팅 캠페인 등 MA 와 무관한 주제는 제외.
- 반드시 마크다운 형식 (`- ` 불릿, **bold** 사용 허용).

**keywords 추출 (가장 중요)**: 제공된 번호 뉴스에 **실제 등장한** 보건당국·약가 정책 주제를
12~18개 키워드로 추출하라. 각 키워드마다 그 주제를 **직접 다루는 기사 번호 목록(articles)**을
반드시 함께 반환하라(최소 1개). 근거 기사가 없는 추상 주제는 만들지 말 것 — 모든 키워드는
제공된 기사에서 출처가 추적되어야 한다. 기관명(보건복지부·심평원·건보공단·식약처·건정심·약평위) +
정책 주제(약가 인하·급여 확대·급여 적정성 재평가·위험분담제(RSA)·실거래가·사용량-약가 연동·
제네릭·혁신형 제약기업·2026 약가제도 개편·선별급여·신약 등재 등) 위주.
각 키워드는 2~12자 한국어 명사구. 일반 단어("환자","병원") 단독 금지.

반드시 아래 JSON 으로만 응답. 다른 텍스트 금지.
{"markdown": "...500자 이내 마크다운...",
 "keywords": [{"text": "급여 적정성 재평가", "articles": [2, 5, 9]},
              {"text": "...", "articles": [1, 4]}, "... 12~18개 ..."],
 "reviewer": "openai"|"gemini"}
"""

# 뉴스 수집용 seed (커버리지 확대). LLM 이 이 중 실제 등장 주제만 keywords 로 추출.
_GOV_SEED_KEYWORDS = [
    "보건복지부 약가", "건강보험심사평가원 급여", "건강보험공단 약가협상",
    "식품의약품안전처 허가", "건강보험정책심의위원회", "약제급여평가위원회",
    "약가 인하", "급여 확대", "급여 적정성 재평가", "위험분담제",
    "실거래가 약가", "사용량 약가 연동", "혁신형 제약기업", "2026 약가제도 개편",
    "제네릭 약가", "신약 급여 등재", "선별급여", "건강보험 보장성 강화",
]


def _load_env() -> None:
    env_path = BASE_DIR / "config" / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def _load_archive_news(days: int = 31, limit: int = 80) -> list[dict]:
    """gov_policy 아카이브에서 최근 뉴스 로드 (키워드 클라우드 입력).

    아카이브가 비어 있으면(최초 구동 등) 1회 크롤 후 재시도, 그래도 없으면
    구형 live Naver 검색으로 graceful fallback.
    """
    from agents import gov_policy_news as _gov
    rows = _gov.list_archive(days=days, limit=limit)
    if not rows:
        try:
            logger.info("[GovSummary] 아카이브 비어 있음 → 1회 크롤 시도")
            _gov.crawl(lookback_days=days)
            rows = _gov.list_archive(days=days, limit=limit)
        except Exception as e:
            logger.warning("[GovSummary] 정책뉴스 크롤 실패: %s", e)
    if rows:
        return [{
            "title": r["title"],
            "url": r["naver_link"] or r["url"],
            "source": r.get("source_name") or r.get("source_domain") or "",
            "date": r["pub_date"],
            "description": (r.get("description") or "")[:200],
            "agency": r.get("brand") or "",
        } for r in rows]
    # ── fallback: 구형 live 검색 (아카이브·크롤 모두 실패 시) ──
    return _collect_news_live(_GOV_SEED_KEYWORDS, days=days)


def _collect_news_live(keywords: list[str], days: int = 31, per_kw: int = 8) -> list[dict]:
    """fallback 전용 — 각 seed 별 최신 뉴스 live 수집. URL dedup, 최신순."""
    client = get_client()
    if not client.is_configured:
        return []
    cutoff = datetime.now() - timedelta(days=days)
    seen: set[str] = set()
    items: list[dict] = []
    for kw in keywords:
        try:
            batch = client.search(kw, display=per_kw, sort="date")
        except Exception as e:
            logger.warning("[GovSummary] live search 실패 (%s): %s", kw, e)
            continue
        for n in batch:
            if n.pub_date < cutoff:
                continue
            key = n.original_link or n.link
            if not key or key in seen:
                continue
            seen.add(key)
            items.append({"title": n.title, "url": key, "source": n.source,
                          "date": n.date_str, "description": n.description[:200],
                          "agency": ""})
        if len(items) >= 60:
            break
    items.sort(key=lambda x: x["date"], reverse=True)
    return items[:40]


def _build_user_prompt(news: list[dict]) -> str:
    lines = ["[지난 1개월 보건당국 정책 아카이브 뉴스 — 번호로 참조]"]
    for i, n in enumerate(news, 1):
        agency = f"{n.get('agency')} · " if n.get("agency") else ""
        lines.append(
            f"{i}. ({n['date']}) {agency}[{n['source']}] {n['title']}\n   {n['description']}"
        )
    lines.append("")
    lines.append(
        f"위 번호 뉴스를 바탕으로 마크다운 {MAX_MD_CHARS}자 이내 요약과, 각 키워드별 "
        f"근거 기사번호(articles)를 포함한 keywords 를 JSON 으로 답하세요."
    )
    return "\n".join(lines)


def _strip_md(s: str) -> str:
    s = (s or "").strip()
    if len(s) > MAX_MD_CHARS:
        s = s[:MAX_MD_CHARS].rstrip() + "…"
    return s


def _parse_keyword_objs(data: dict, n_news: int) -> list[dict]:
    """LLM JSON keywords 정규화 → [{"text", "articles":[idx,...]}].

    구형(문자열 list)·신형(객체 list) 모두 수용. articles 는 1-based 기사번호를
    0-based 인덱스로 변환·범위 검증. 중복 text 제거.
    """
    raw = data.get("keywords") or []
    out, seen = [], set()
    for k in raw:
        if isinstance(k, str):
            text, arts = k.strip(), []
        elif isinstance(k, dict):
            text = (k.get("text") or "").strip()
            arts = k.get("articles") or k.get("article_ids") or []
        else:
            continue
        if not (2 <= len(text) <= 20) or text in seen:
            continue
        idxs = []
        for a in arts if isinstance(arts, list) else []:
            try:
                i = int(a) - 1  # 1-based → 0-based
            except (ValueError, TypeError):
                continue
            if 0 <= i < n_news and i not in idxs:
                idxs.append(i)
        seen.add(text)
        out.append({"text": text, "articles": idxs})
    return out[:18]


def _call_openai(prompt: str, n_news: int) -> tuple[str | None, list[dict]]:
    try:
        from openai import OpenAI
    except ImportError:
        logger.info("[GovSummary] openai SDK 미설치")
        return None, []
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None, []
    try:
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": prompt},
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
            max_tokens=1400,
        )
        data = json.loads(resp.choices[0].message.content or "")
        return _strip_md(data.get("markdown", "")), _parse_keyword_objs(data, n_news)
    except Exception as e:
        logger.warning("[GovSummary] OpenAI 호출 실패: %s", e)
        return None, []


def _call_gemini(prompt: str, n_news: int) -> tuple[str | None, list[dict]]:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None, []
    endpoint = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.5-flash:generateContent?key={api_key}"
    )
    body = {
        "systemInstruction": {"role": "system", "parts": [{"text": SYSTEM_PROMPT}]},
        "contents":          [{"role": "user",  "parts": [{"text": prompt}]}],
        "generationConfig":  {
            "temperature": 0.2,
            "maxOutputTokens": 1500,  # 키워드별 articles 중첩으로 JSON 커짐 — 절단 방지
            "responseMimeType": "application/json",
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    try:
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=45, context=ssl.create_default_context()) as resp:
            raw = resp.read().decode("utf-8")
        payload = json.loads(raw)
        text = (
            payload.get("candidates", [{}])[0]
            .get("content", {}).get("parts", [{}])[0]
            .get("text", "")
        ).strip()
        if not text:
            return None, []
        if "```" in text:
            text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
        m = re.search(r"\{[\s\S]+\}", text)
        if m:
            text = m.group(0)
        data = json.loads(text)
        return _strip_md(data.get("markdown", "")), _parse_keyword_objs(data, n_news)
    except Exception as e:
        logger.warning("[GovSummary] Gemini 호출 실패: %s", e)
        return None, []


def _consensus(openai_md: str | None, gemini_md: str | None,
               openai_kw: list[dict], gemini_kw: list[dict]) -> tuple[str, list[str], list[dict]]:
    """두 리뷰어 결과 병합. markdown=OpenAI primary, keywords=union(text 기준, articles 합집합)."""
    reviewers: list[str] = []
    if openai_md:
        reviewers.append("openai:gpt-4o-mini")
    if gemini_md:
        reviewers.append("gemini:gemini-2.5-flash")
    primary = openai_md or gemini_md or ""
    merged: dict[str, set[int]] = {}
    order: list[str] = []
    for k in (openai_kw or []) + (gemini_kw or []):
        t = k["text"]
        if t not in merged:
            merged[t] = set()
            order.append(t)
        merged[t].update(k.get("articles") or [])
    kw_objs = [{"text": t, "articles": sorted(merged[t])} for t in order[:18]]
    return primary, reviewers, kw_objs


def _date_recency(date_str: str, window: int = 31) -> float:
    """최신성 가중 — 오늘=1.0, window 일 경과=0.3 으로 선형 감쇠."""
    try:
        d = datetime.strptime((date_str or "")[:10], "%Y-%m-%d")
    except Exception:
        return 0.5
    age = (datetime.now() - d).days
    return max(0.3, min(1.0, 1.0 - age / max(1, window)))


def get_government_summary(refresh: bool = False) -> dict:
    _load_env()
    today = datetime.now().strftime("%Y-%m-%d")
    cache_file = CACHE_DIR / f"gov_summary_{today}.json"
    if not refresh and cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    news = _load_archive_news(days=31, limit=80)
    if not news:
        result = {
            "updated_at": datetime.now().isoformat(),
            "markdown": "", "reviewers": [], "sources": [],
            "keywords": [], "newsByKeyword": {},
            "error": "수집된 정부 정책 뉴스 없음 (Naver API 키 또는 크롤 확인)",
        }
        cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result

    prompt = _build_user_prompt(news)
    openai_md, openai_kw = _call_openai(prompt, len(news))
    gemini_md, gemini_kw = _call_gemini(prompt, len(news))
    markdown, reviewers, kw_objs = _consensus(openai_md, gemini_md, openai_kw, gemini_kw)

    def _article(i: int) -> dict:
        n = news[i]
        return {"title": n["title"], "url": n["url"], "source": n["source"], "date": n["date"]}

    # weight = Σ(근거기사 최신성), 50~100 정규화. newsByKeyword = 실제 근거 기사.
    keywords: list[dict] = []
    news_by_kw: dict[str, list[dict]] = {}
    scored = [(k["text"], k["articles"],
               sum(_date_recency(news[i]["date"]) for i in k["articles"]))
              for k in kw_objs if k["articles"]]  # 근거 기사 없는 키워드 배제 (#2 근본수정)
    keyword_source = "archive_llm_linked"
    if scored:
        max_s = max(s for _, _, s in scored) or 1.0
        for text, arts, score in scored:
            keywords.append({"text": text, "weight": round(50 + 50 * (score / max_s))})
            arts_sorted = sorted(arts, key=lambda i: news[i]["date"], reverse=True)
            news_by_kw[text] = [_article(i) for i in arts_sorted[:6]]
    else:
        # fallback: LLM 링크 실패 → seed 키워드를 아카이브 제목 토큰으로 연결 (근거 있는 것만)
        keyword_source = "archive_fallback"
        for kw in _GOV_SEED_KEYWORDS:
            toks = [t for t in kw.split() if len(t) >= 2]
            matched = [n for n in news if any(t in n["title"] for t in toks)][:6]
            if not matched:
                continue
            keywords.append({"text": kw, "weight": 70})
            news_by_kw[kw] = [{"title": m["title"], "url": m["url"],
                               "source": m["source"], "date": m["date"]} for m in matched]
        keywords = keywords[:14]

    result = {
        "updated_at": datetime.now().isoformat(),
        "markdown": markdown,
        "reviewers": reviewers,
        "sources": [_article(i) for i in range(min(20, len(news)))],
        "keywords": keywords,
        "newsByKeyword": news_by_kw,
        "keyword_source": keyword_source,
        "archive_count": len(news),
    }
    if not markdown and not keywords:
        result["error"] = "OpenAI / Gemini 모두 응답 실패"
    try:
        cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("[GovSummary] cache 쓰기 실패: %s", e)
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    out = get_government_summary(refresh=True)
    print(json.dumps(out, ensure_ascii=False, indent=2))
