"""Research LLM 클라이언트 — 3개 소스 통합

각 클라이언트는 동일한 시그니처:
    ask_XXX(prompt: str, system: str | None = None) -> dict

반환 dict:
    {
        "source":    "gemini (2.5-pro) / perplexity (sonar-pro) / openai (gpt-5)",
        "text":      "<LLM 응답 본문>",
        "citations": [{"title": str, "url": str}, ...],   # 소스에서 제공 시
        "raw":       <원본 payload>,                        # 디버그용
        "error":     "...",                                 # 실패 시만
    }
"""

from __future__ import annotations

import json
import os
import ssl
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]


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


_load_env()


# ─────────────────────────────────────────────────────────────
# Gemini 2.5-pro + Google Search grounding
# ─────────────────────────────────────────────────────────────
def ask_gemini_grounded(
    prompt: str,
    system: str | None = None,
    model: str = "gemini-2.5-pro",
    timeout: int = 180,
) -> dict:
    """Gemini 2.5-pro 에 Google Search grounding 활성화 후 질의."""
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return {"source": f"gemini ({model})", "error": "GEMINI_API_KEY 없음"}

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={key}"
    )
    body: dict = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 4096,
        },
    }
    if system:
        body["systemInstruction"] = {
            "role": "system",
            "parts": [{"text": system}],
        }

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read().decode("utf-8")
        payload = json.loads(raw)

        cand = (payload.get("candidates") or [{}])[0]
        parts = (cand.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts).strip()

        # grounding 메타데이터에서 citation 추출
        citations = []
        grounding = cand.get("groundingMetadata") or {}
        chunks = grounding.get("groundingChunks") or []
        for ch in chunks:
            web = ch.get("web") or {}
            if web.get("uri"):
                citations.append({"title": web.get("title", ""), "url": web["uri"]})

        return {
            "source": f"gemini ({model}, grounded)",
            "text": text,
            "citations": citations,
            "raw": payload,
        }
    except Exception as e:
        return {"source": f"gemini ({model})", "error": str(e)}


# ─────────────────────────────────────────────────────────────
# Perplexity sonar-pro (native citations)
# ─────────────────────────────────────────────────────────────
def ask_perplexity(
    prompt: str,
    system: str | None = None,
    model: str = "sonar-pro",
    timeout: int = 180,
) -> dict:
    """Perplexity sonar-pro 질의. OpenAI 호환 API."""
    key = os.environ.get("PERPLEXITY_API_KEY", "")
    if not key:
        return {"source": f"perplexity ({model})", "error": "PERPLEXITY_API_KEY 없음"}

    try:
        from openai import OpenAI
    except ImportError:
        return {"source": f"perplexity ({model})", "error": "openai 패키지 미설치"}

    try:
        client = OpenAI(api_key=key, base_url="https://api.perplexity.ai")
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.2,
            timeout=timeout,
        )
        text = resp.choices[0].message.content or ""

        # Perplexity 는 citations 를 최상위 필드로 노출
        citations = []
        raw = resp.model_dump() if hasattr(resp, "model_dump") else {}
        for url in raw.get("citations", []) or []:
            citations.append({"title": "", "url": url})

        return {
            "source": f"perplexity ({model})",
            "text": text.strip(),
            "citations": citations,
            "raw": raw,
        }
    except Exception as e:
        return {"source": f"perplexity ({model})", "error": str(e)}


# ─────────────────────────────────────────────────────────────
# OpenAI GPT-5 (training knowledge baseline — no web search)
# ─────────────────────────────────────────────────────────────
def ask_openai(
    prompt: str,
    system: str | None = None,
    model: str = "gpt-5",
    timeout: int = 180,
) -> dict:
    """OpenAI GPT-5 질의. 학습 지식만 사용 (web search 없음 — 독립 baseline 용도)."""
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        return {"source": f"openai ({model})", "error": "OPENAI_API_KEY 없음"}

    try:
        from openai import OpenAI
    except ImportError:
        return {"source": f"openai ({model})", "error": "openai 패키지 미설치"}

    try:
        client = OpenAI(api_key=key, timeout=timeout)
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            max_completion_tokens=4000,
        )
        text = resp.choices[0].message.content or ""
        return {
            "source": f"openai ({model})",
            "text": text.strip(),
            "citations": [],
            "raw": resp.model_dump() if hasattr(resp, "model_dump") else {},
        }
    except Exception as e:
        return {"source": f"openai ({model})", "error": str(e)}
