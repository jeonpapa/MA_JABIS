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
import sqlite3
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from agents.naver_news import get_client

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
CACHE_DIR = BASE_DIR / "data" / "cache" / "gov_summary"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = BASE_DIR / "agents" / "db" / "drug_prices.db"

MAX_MD_CHARS = 500

SYSTEM_PROMPT = """당신은 한국 MSD Marketing & Market Access 팀의 정책 동향 요약 애널리스트입니다.
제공된 최근 1개월 보건당국(보건복지부·건보공단·심평원·식약처 등) 관련 뉴스를 바탕으로,
**마케팅·Market Access 관점에서 함의 있는 내용**만 추려 한국어 마크다운으로 요약하세요.

원칙:
- 총 글자수는 한글·공백 포함 500자 이내. 간결·factual·근거 중심.
- 구성: (1) 핵심 흐름 1~2줄 불릿 3개 (2) 간단한 함의 1~2줄. 불필요한 서론 금지.
- 추측·과장 금지. 기사 본문에 없는 숫자/기관명 생성 금지.
- 동일 이슈가 여러 번 보도되면 1개로 합친다.
- 매출·주가·마케팅 캠페인 등 MA 와 무관한 주제는 제외.
- 반드시 마크다운 형식 (`- ` 불릿, **bold** 사용 허용).

반드시 아래 JSON 으로만 응답. 다른 텍스트 금지.
{"markdown": "...500자 이내 마크다운...", "reviewer": "openai"|"gemini"}
"""


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


def _fetch_gov_keywords() -> list[str]:
    """keyword_cloud 에서 정부/기관 관련 키워드 추출.
    현재 스키마에는 카테고리 컬럼이 없으므로, 휴리스틱으로 '보건복지부/건보공단/심평원/식약처/HIRA/NHIS' 를 포함하는 키워드 + weight 상위 전체 반환.
    """
    keywords: list[str] = []
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT text FROM keyword_cloud ORDER BY weight DESC"
        ).fetchall()
        conn.close()
        keywords = [r[0] for r in rows if r and r[0]]
    except Exception as e:
        logger.warning("[GovSummary] keyword_cloud 조회 실패: %s", e)
    # 기본 정부 키워드 fallback
    if not keywords:
        keywords = ["보건복지부", "건강보험심사평가원", "건강보험공단", "식품의약품안전처"]
    return keywords[:20]


def _collect_news(keywords: list[str], days: int = 31, per_kw: int = 8) -> list[dict]:
    """각 키워드별 최신 뉴스 per_kw 건. URL dedup."""
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
            logger.warning("[GovSummary] search 실패 (%s): %s", kw, e)
            continue
        for n in batch:
            if n.pub_date < cutoff:
                continue
            key = n.original_link or n.link
            if not key or key in seen:
                continue
            seen.add(key)
            items.append({
                "title": n.title,
                "url": key,
                "source": n.source,
                "date": n.date_str,
                "description": n.description[:200],
                "keyword": kw,
            })
        if len(items) >= 60:
            break
    # 최신순 정렬
    items.sort(key=lambda x: x["date"], reverse=True)
    return items[:40]


def _build_user_prompt(keywords: list[str], news: list[dict]) -> str:
    lines = ["[정부 관련 키워드]"]
    lines.append(", ".join(keywords[:12]))
    lines.append("")
    lines.append("[지난 1개월 뉴스]")
    for i, n in enumerate(news, 1):
        lines.append(
            f"{i}. ({n['date']}) [{n['source']}] {n['title']}\n   {n['description']}"
        )
    lines.append("")
    lines.append(f"위 뉴스를 바탕으로 마크다운 {MAX_MD_CHARS}자 이내 요약을 JSON 으로 답하세요.")
    return "\n".join(lines)


def _strip_md(s: str) -> str:
    s = (s or "").strip()
    if len(s) > MAX_MD_CHARS:
        s = s[:MAX_MD_CHARS].rstrip() + "…"
    return s


def _call_openai(prompt: str) -> str | None:
    try:
        from openai import OpenAI
    except ImportError:
        logger.info("[GovSummary] openai SDK 미설치")
        return None
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
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
            max_tokens=900,
        )
        text = resp.choices[0].message.content or ""
        data = json.loads(text)
        return _strip_md(data.get("markdown", ""))
    except Exception as e:
        logger.warning("[GovSummary] OpenAI 호출 실패: %s", e)
        return None


def _call_gemini(prompt: str) -> str | None:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None
    endpoint = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.5-flash:generateContent?key={api_key}"
    )
    body = {
        "systemInstruction": {"role": "system", "parts": [{"text": SYSTEM_PROMPT}]},
        "contents":          [{"role": "user",  "parts": [{"text": prompt}]}],
        "generationConfig":  {
            "temperature": 0.2,
            "maxOutputTokens": 900,
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
            return None
        if "```" in text:
            text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
        m = re.search(r"\{[\s\S]+\}", text)
        if m:
            text = m.group(0)
        data = json.loads(text)
        return _strip_md(data.get("markdown", ""))
    except Exception as e:
        logger.warning("[GovSummary] Gemini 호출 실패: %s", e)
        return None


def _consensus(openai_md: str | None, gemini_md: str | None) -> tuple[str, list[str]]:
    """두 리뷰어 결과 병합. 둘 다 있으면 OpenAI 결과를 primary 로 쓰되 두 리뷰어 이름 기록."""
    reviewers: list[str] = []
    if openai_md:
        reviewers.append("openai:gpt-4o-mini")
    if gemini_md:
        reviewers.append("gemini:gemini-2.5-flash")
    primary = openai_md or gemini_md or ""
    return primary, reviewers


def get_government_summary(refresh: bool = False) -> dict:
    _load_env()
    today = datetime.now().strftime("%Y-%m-%d")
    cache_file = CACHE_DIR / f"gov_summary_{today}.json"
    if not refresh and cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    keywords = _fetch_gov_keywords()
    news = _collect_news(keywords, days=31, per_kw=8)
    if not news:
        result = {
            "updated_at": datetime.now().isoformat(),
            "markdown": "",
            "reviewers": [],
            "sources": [],
            "error": "수집된 정부 관련 뉴스 없음 (Naver API 키 또는 키워드 확인)",
        }
        cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result

    prompt = _build_user_prompt(keywords, news)
    openai_md = _call_openai(prompt)
    gemini_md = _call_gemini(prompt)
    markdown, reviewers = _consensus(openai_md, gemini_md)

    result = {
        "updated_at": datetime.now().isoformat(),
        "markdown": markdown,
        "reviewers": reviewers,
        "sources": [{"title": n["title"], "url": n["url"], "source": n["source"], "date": n["date"]} for n in news[:10]],
        "keywords": keywords[:12],
    }
    if not markdown:
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
