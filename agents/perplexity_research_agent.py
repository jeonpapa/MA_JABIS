"""
PerplexityResearchAgent — 시장 리서치 보고서 생성 에이전트

역할:
  Perplexity Sonar API를 이용해 약제/시장/급여 관련 심층 웹 리서치 보고서를
  자동으로 생성한다. Perplexity는 OpenAI 호환 API를 제공하므로 openai 클라이언트를
  base_url만 바꿔서 사용한다.

의존성:
  pip install openai

환경변수 (config/.env):
  PERPLEXITY_API_KEY=pplx-...

사용 모델:
  - sonar                : 빠른 웹 검색 (fallback)
  - sonar-pro            : 심층 검색 (기본값)
  - sonar-deep-research  : 멀티스텝 리서치 보고서 (report 모드)
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
CACHE_DIR = BASE_DIR / "data" / "research"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

PERPLEXITY_BASE_URL = "https://api.perplexity.ai"

MODEL_MAP = {
    "fast":   "sonar",
    "pro":    "sonar-pro",
    "report": "sonar-deep-research",
}


# ─────────────────────────────────────────────────────────────────────────────
# 1) .env 로더 (python-dotenv 없을 때도 동작)
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# 2) Perplexity 클라이언트
# ─────────────────────────────────────────────────────────────────────────────

def _get_client():
    _load_env()
    api_key = os.environ.get("PERPLEXITY_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "PERPLEXITY_API_KEY가 config/.env에 설정되어 있지 않습니다."
        )
    from openai import OpenAI
    return OpenAI(api_key=api_key, base_url=PERPLEXITY_BASE_URL)


# ─────────────────────────────────────────────────────────────────────────────
# 3) 보고서 생성
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT_REPORT = """당신은 글로벌 제약회사 Market Access 팀을 지원하는 시장 리서치 애널리스트입니다.
모든 답변은 한국어로 작성하되, 고유명사(약제명, 규제기관, 저널)는 원문을 병기합니다.

보고서 구조:
1. Executive Summary (3~5문장)
2. 주요 발견사항 (bullet points)
3. 약가·급여 관련 핵심 사실 (수치 중심, 출처 명시)
4. 규제·HTA 관점의 함의
5. 근거·출처 (citations)

원칙:
- 출처 없는 수치·단정 금지
- 불확실한 정보는 '추정:' 접두 사용
- MSD/Merck 편향 금지, 중립적 시각 유지
"""

SYSTEM_PROMPT_SEARCH = """You are a precise research assistant for a Market Access team at a global pharmaceutical company.
Answer in Korean. Keep proper nouns (drug names, agencies, journals) in original form.
Always cite sources with URLs. If a fact is uncertain, prefix with '추정:'.
Prefer official regulator and peer-reviewed sources over general media.
"""


def research(
    query: str,
    mode: str = "pro",
    *,
    drug_name: Optional[str] = None,
    country: Optional[str] = None,
    save: bool = True,
    temperature: float = 0.2,
) -> dict:
    """
    Perplexity로 리서치 실행.

    Args:
        query: 리서치 질의 (한/영 모두 가능)
        mode: "fast" | "pro" | "report"
        drug_name, country: 캐시 파일명에 사용
        save: True면 data/research/ 에 JSON으로 저장
        temperature: 0.0~1.0

    Returns:
        {
          "query": str,
          "mode": str,
          "model": str,
          "answer": str,
          "citations": [str, ...],
          "usage": {...},
          "created_at": ISO8601,
        }
    """
    if mode not in MODEL_MAP:
        raise ValueError(f"mode는 {list(MODEL_MAP)} 중 하나여야 합니다.")

    model = MODEL_MAP[mode]
    system = SYSTEM_PROMPT_REPORT if mode == "report" else SYSTEM_PROMPT_SEARCH

    client = _get_client()
    logger.info("Perplexity 리서치 시작 | model=%s | query=%s", model, query[:80])

    resp = client.chat.completions.create(
        model=model,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": query},
        ],
    )

    choice = resp.choices[0]
    answer = choice.message.content or ""

    citations = []
    raw = getattr(resp, "citations", None)
    if raw:
        citations = list(raw)
    else:
        citations = _extract_citations_fallback(resp)

    usage = {}
    if getattr(resp, "usage", None):
        try:
            usage = resp.usage.model_dump()
        except Exception:
            usage = {"prompt_tokens": getattr(resp.usage, "prompt_tokens", None),
                     "completion_tokens": getattr(resp.usage, "completion_tokens", None)}

    result = {
        "query": query,
        "mode": mode,
        "model": model,
        "answer": answer,
        "citations": citations,
        "usage": usage,
        "drug_name": drug_name,
        "country": country,
        "created_at": datetime.utcnow().isoformat() + "Z",
    }

    if save:
        _save_report(result)

    return result


def _extract_citations_fallback(resp) -> list:
    """Perplexity 응답 버전에 따라 citations 위치가 다를 수 있어 방어적으로 추출."""
    try:
        data = resp.model_dump()
    except Exception:
        return []
    for key in ("citations", "search_results"):
        if key in data and isinstance(data[key], list):
            out = []
            for item in data[key]:
                if isinstance(item, str):
                    out.append(item)
                elif isinstance(item, dict):
                    url = item.get("url") or item.get("link")
                    if url:
                        out.append(url)
            if out:
                return out
    return []


def _save_report(result: dict) -> Path:
    drug = (result.get("drug_name") or "general").replace("/", "_")
    country = (result.get("country") or "global").replace("/", "_")
    mode = result.get("mode", "pro")
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    fname = f"{drug}_{country}_{mode}_{ts}.json"
    path = CACHE_DIR / fname
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("리서치 결과 저장 → %s", path)
    return path


# ─────────────────────────────────────────────────────────────────────────────
# 4) 고수준 헬퍼 — 자주 쓰는 리서치 템플릿
# ─────────────────────────────────────────────────────────────────────────────

def research_drug_market(drug_name: str, country: str = "대한민국", mode: str = "report") -> dict:
    """특정 약제의 특정 국가 시장·급여 현황 보고서."""
    query = (
        f"{drug_name}의 {country} 시장·급여 현황을 분석해 주세요. "
        f"다음을 포함합니다:\n"
        f"- 공식 약가(또는 list price), 통화와 함께\n"
        f"- 급여 여부, 적응증, 급여 기준 (해당 시 HTA 결정 인용)\n"
        f"- 최근 2년 내 약가·급여 변동 이력\n"
        f"- 경쟁 약제 및 점유 현황 (가능 범위 내)\n"
        f"- 출처 URL을 각 수치마다 명시"
    )
    return research(query, mode=mode, drug_name=drug_name, country=country)


def research_price_change_reason(
    drug_name: str,
    country: str,
    change_date: str,
    mode: str = "pro",
) -> dict:
    """
    약가 변동 사유 리서치 (MarketIntelligenceAgent 보완용).

    엄격한 제약:
      - change_date ±6개월 윈도우 내 사실만 근거
      - 한국 약가 사후관리 4대 기전 중 하나로 분류
      - 윈도우 밖 연도의 일반 시장동향 서술 금지
    """
    from datetime import datetime
    try:
        dt = datetime.strptime(change_date, "%Y.%m.%d")
    except Exception:
        try:
            dt = datetime.strptime(change_date[:7], "%Y.%m")
        except Exception:
            dt = datetime.now()
    y, m = dt.year, dt.month
    window_from = f"{y}.{max(1, m-6):02d}"
    window_to   = f"{y}.{min(12, m+6):02d}" if m+6 <= 12 else f"{y+1}.{(m+6)%12:02d}"

    query = f"""{country}에서 {drug_name}의 약가가 {change_date} 전후로 변동된 사유를 조사해 주세요.

[필수 준수 규칙]
1) **시점 제약**: 근거는 반드시 {window_from} ~ {window_to} 윈도우 내 보도·고시·협상결과만 사용.
   윈도우 밖 연도의 일반 시장동향·매출 추이 서술 금지 (퀄리티 미달로 간주).
2) **4대 기전 분류**: 다음 중 하나로 판정 (해당 없으면 '미분류' + 사유 명시).
   - indication_expansion (적응증 확대)
   - patent_expiration (특허 만료, 제네릭/바이오시밀러 등재)
   - volume_price (사용량-연동)
   - actual_transaction (실거래가 연동)
3) **우선 매체**: dailypharm.com, yakup.com, medipana.com, hitnews.co.kr, mohw.go.kr, hira.or.kr, law.go.kr.
4) 근거 없으면 "추정:" 접두, 꾸며내지 말 것. 각 수치마다 URL 출처.

[출력 구조 (500자 이내)]
- [기전 판정]
- [핵심 근거] (윈도우 내 보도·고시 요약, 매체·일자 포함)
- [보완 설명]
- [출처 URL]
"""
    return research(
        query, mode=mode, drug_name=drug_name, country=country, temperature=0.1
    )


# ─────────────────────────────────────────────────────────────────────────────
# 5) CLI 진입점
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Perplexity 시장 리서치")
    parser.add_argument("query", help="리서치 질의")
    parser.add_argument("--mode", default="pro", choices=list(MODEL_MAP))
    parser.add_argument("--drug", default=None)
    parser.add_argument("--country", default=None)
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

    result = research(
        args.query,
        mode=args.mode,
        drug_name=args.drug,
        country=args.country,
        save=not args.no_save,
    )

    print("\n=== ANSWER ===\n")
    print(result["answer"])
    print("\n=== CITATIONS ===")
    for i, c in enumerate(result["citations"], 1):
        print(f"[{i}] {c}")
    print(f"\n(model={result['model']}, usage={result['usage']})")
