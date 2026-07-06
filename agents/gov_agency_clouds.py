"""정부기관별 최근 7일 누적 키워드 클라우드 — 순수 빈도 기반 (LLM 아님).

Home 위젯(A1)용. `competitor_news(kind='gov_policy')` 아카이브에서 최근 7일
기사(title+description)를 기관(brand)별로 묶고, 한국어 토큰(2+글자) 빈도를
집계해 기관당 상위 ~30 단어를 반환한다.

- 기관 목록: `editable_factors.get_gov_agencies()` (실패 시 `gov_policy_news.GOV_AGENCIES` 폴백)
- S4 영문 태그는 한글 라벨로 표기: NATIONAL_ASSEMBLY→국회, PATIENT_GROUP→환자단체, MEDICAL_SOCIETY→의료진
- keep 힌트: `editable_factors.get_context_anchors()` — 이 단어는 불용어/조사 strip 을 건너뛴다
- 일자 캐시: `data/cache/gov_summary/gov_agency_clouds_{today}.json`

사용: `get_gov_agency_clouds(refresh=False)` →
{"generated_at", "window_days": 7,
 "agencies": [{"agency", "label", "article_count",
               "keywords": [{"text","count"}...], "newsByKeyword": {...}}]}
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
CACHE_DIR = BASE_DIR / "data" / "cache" / "gov_summary"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

WINDOW_DAYS = 7
TOP_N = 30
MAX_ROWS = 5000
NEWS_PER_KEYWORD = 5

# S4 영문 agency 태그 → 한글 표기 (S1 classifier 키는 영문 유지, 표기만 한글)
AGENCY_LABELS = {
    "NATIONAL_ASSEMBLY": "국회",
    "PATIENT_GROUP": "환자단체",
    "MEDICAL_SOCIETY": "의료진",
}

# 기관 자기 이름 토큰 — 해당 기관 클라우드 안에서는 정보가 없어 제외
_AGENCY_SELF_TOKENS: dict[str, frozenset] = {
    "보건복지부": frozenset({"보건복지부", "복지부"}),
    "건강보험심사평가원": frozenset({"건강보험심사평가원", "심사평가원", "심평원"}),
    "국민건강보험공단": frozenset({"국민건강보험공단", "건강보험공단", "건보공단", "공단"}),
    "식품의약품안전처": frozenset({"식품의약품안전처", "식약처"}),
}

_TOKEN_RE = re.compile(r"[가-힣]{2,}")
_ASCII_RE = re.compile(r"[A-Za-z]{2,}")

# 조사/어미 strip — 긴 것부터 1회. keep 힌트에 있는 단어는 strip 하지 않음.
# 잔여 2글자 미만이면 strip 하지 않음 (예: "약가"의 "가" 는 유지).
_JOSA_SUFFIXES = (
    "에서는", "으로는", "이라는",
    "에서", "으로", "까지", "부터", "에게", "라고", "이라", "라는",
    "마저", "조차", "처럼", "보다", "에는", "에도", "와의", "과의",
    "했다", "한다", "된다", "됐다", "하는", "되는", "하기", "하면", "해야",
    "은", "는", "이", "가", "을", "를", "의", "에", "도", "와", "과", "로", "만",
)

# 일반 동사/보도 상투어/시간어 불용어 (순수 빈도 클라우드의 노이즈 제거)
_STOPWORDS = frozenset({
    # 보도 상투어
    "기자", "뉴스", "기사", "사진", "제공", "무단", "전재", "재배포", "배포",
    "금지", "저작권", "보도", "연합뉴스", "뉴시스", "뉴스보도", "단독",
    # 일반 동사/서술
    "있다", "없다", "했다", "한다", "된다", "됐다", "있는", "없는", "하는",
    "되는", "하며", "하고", "있으며", "있다고", "한다고", "했다고", "됐다고",
    "밝혔다", "말했다", "전했다", "강조했다", "설명했다", "나타났다", "발혔다",
    "이라고", "이라며", "라며", "위한", "위해", "통해", "대한", "대해",
    "따라", "따른", "따르면", "관련", "대해서", "밝혔", "것으로", "것이다",
    "것은", "것을", "것이라고", "하기로", "하겠다고", "가운데",
    # 시간/지시어
    "이번", "지난", "지난해", "지난달", "올해", "내년", "오는", "최근",
    "현재", "오늘", "어제", "이날", "이달", "당시", "이후", "이전",
    # generic 명사 (단독으로 의미 없는)
    "경우", "때문", "대상", "여부", "또한", "함께", "모든", "같은", "다른",
    "이런", "그런", "어떤", "각각", "정도", "한편", "관계자", "국내",
    "예정", "계획", "진행", "추진", "실시", "개최", "발표", "마련",
    "특히", "그러나", "하지만", "이어", "다시", "지금", "모두",
    # 단위어
    "개월", "억원", "조원", "만원", "여원",
})


def _agency_labels() -> dict:
    return dict(AGENCY_LABELS)


def _strip_josa(token: str, keep: frozenset) -> str:
    """말미 조사 1회 strip (긴 것 우선). keep 힌트/2글자 미만 잔여는 원형 유지."""
    if token in keep:
        return token
    for suf in _JOSA_SUFFIXES:
        if token.endswith(suf) and len(token) - len(suf) >= 2:
            base = token[: len(token) - len(suf)]
            return base
    return token


def _tokenize(text: str, keep: frozenset, ascii_keep: frozenset) -> list[str]:
    """한국어 2+글자 토큰화 → 조사 strip → 불용어 제거.

    - 숫자는 정규식상 배제 (한글 음절만 매칭)
    - keep(문맥 anchor) 토큰은 불용어/조사 strip 을 건너뜀
    - ASCII 토큰은 keep 힌트에 등록된 약어(RSA 등)만 인정
    """
    out: list[str] = []
    for tok in _TOKEN_RE.findall(text or ""):
        tok = _strip_josa(tok, keep)
        if len(tok) < 2:
            continue
        if tok in keep:
            out.append(tok)
            continue
        if tok in _STOPWORDS:
            continue
        out.append(tok)
    for tok in _ASCII_RE.findall(text or ""):
        if tok.upper() in ascii_keep:
            out.append(tok.upper())
    return out


def _load_rows(window_days: int = WINDOW_DAYS) -> list[dict]:
    """gov_policy 아카이브 최근 N일 원시 행 (read-only)."""
    from agents import competitor_news_agent as _cn
    _cn.ensure_schema()
    cutoff = (datetime.now() - timedelta(days=window_days)).strftime("%Y-%m-%d")
    with _cn._connect() as conn:
        rows = conn.execute(
            "SELECT brand, title, description, url, naver_link, source_name, "
            "source_domain, pub_date FROM competitor_news "
            "WHERE kind = 'gov_policy' AND pub_date >= ? "
            "ORDER BY pub_date DESC LIMIT ?",
            (cutoff, MAX_ROWS),
        ).fetchall()
    return [dict(r) for r in rows]


def _agency_order() -> list[str]:
    """editable_factors 우선, 실패 시 상수 폴백 — agency 명 순서 리스트."""
    try:
        from agents.editable_factors import get_gov_agencies
        agencies = [a["agency"] for a in get_gov_agencies() if a.get("agency")]
        if agencies:
            return agencies
    except Exception as e:
        logger.warning("[GovClouds] get_gov_agencies 실패, 상수 폴백: %s", e)
    from agents.gov_policy_news import GOV_AGENCIES
    return [a["agency"] for a in GOV_AGENCIES]


def _context_keep() -> tuple[frozenset, frozenset]:
    """(한글 keep 힌트, ASCII keep 약어) — editable_factors anchor, 실패 시 상수."""
    try:
        from agents.editable_factors import get_context_anchors
        anchors = tuple(get_context_anchors())
    except Exception as e:
        logger.warning("[GovClouds] get_context_anchors 실패, 상수 폴백: %s", e)
        from agents.gov_policy_news import _CONTEXT_ANCHORS
        anchors = _CONTEXT_ANCHORS
    keep = frozenset(a for a in anchors if not re.fullmatch(r"[A-Za-z]+", a))
    ascii_keep = frozenset(a.upper() for a in anchors if re.fullmatch(r"[A-Za-z]+", a))
    return keep, ascii_keep


def build_agency_clouds(
    rows: list[dict],
    agency_order: list[str],
    keep: frozenset,
    ascii_keep: frozenset,
    top_n: int = TOP_N,
) -> list[dict]:
    """순수 함수 — 아카이브 행을 기관별 빈도 클라우드로 집계 (테스트 대상).

    반환: [{"agency","label","article_count","keywords":[{"text","count"}...],
            "newsByKeyword": {kw: [{"title","url","source","date"}...]}}]
    기사 0건 기관은 생략. agency_order 에 없는 brand 는 말미에 추가 (방어).
    """
    grouped: dict[str, list[dict]] = {}
    for r in rows:
        agency = (r.get("brand") or "").strip()
        if not agency:
            continue
        grouped.setdefault(agency, []).append(r)

    ordered = [a for a in agency_order if a in grouped]
    ordered += [a for a in grouped if a not in ordered]

    labels = _agency_labels()
    out: list[dict] = []
    for agency in ordered:
        items = grouped[agency]
        self_tokens = _AGENCY_SELF_TOKENS.get(agency, frozenset())
        counts: dict[str, int] = {}
        for it in items:
            text = f"{it.get('title') or ''} {it.get('description') or ''}"
            for tok in _tokenize(text, keep, ascii_keep):
                if tok in self_tokens:
                    continue
                counts[tok] = counts.get(tok, 0) + 1
        top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]
        keywords = [{"text": t, "count": c} for t, c in top]

        # 근거뉴스: 키워드가 표면에 실제 등장하는 최신 기사 최대 5건
        news_by_kw: dict[str, list[dict]] = {}
        for kw in keywords:
            t = kw["text"]
            matched = []
            for it in items:
                surface = f"{it.get('title') or ''} {it.get('description') or ''}"
                if t in surface:
                    matched.append({
                        "title": it.get("title") or "",
                        "url": it.get("naver_link") or it.get("url") or "",
                        "source": it.get("source_name") or it.get("source_domain") or "",
                        "date": it.get("pub_date") or "",
                    })
                if len(matched) >= NEWS_PER_KEYWORD:
                    break
            news_by_kw[t] = matched

        out.append({
            "agency": agency,
            "label": labels.get(agency, agency),
            "article_count": len(items),
            "keywords": keywords,
            "newsByKeyword": news_by_kw,
        })
    return out


def get_gov_agency_clouds(refresh: bool = False, window_days: int = WINDOW_DAYS) -> dict:
    """기관별 최근 7일 빈도 클라우드 — 일자 캐시."""
    today = datetime.now().strftime("%Y-%m-%d")
    cache_file = CACHE_DIR / f"gov_agency_clouds_{today}.json"
    if not refresh and window_days == WINDOW_DAYS and cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    try:
        rows = _load_rows(window_days)
    except Exception as e:
        logger.error("[GovClouds] 아카이브 조회 실패: %s", e, exc_info=True)
        return {"generated_at": datetime.now().isoformat(),
                "window_days": window_days, "agencies": [],
                "error": f"gov_policy 아카이브 조회 실패: {e}"}

    keep, ascii_keep = _context_keep()
    agencies = build_agency_clouds(rows, _agency_order(), keep, ascii_keep)
    result = {
        "generated_at": datetime.now().isoformat(),
        "window_days": window_days,
        "agencies": agencies,
    }
    if not rows:
        result["error"] = "최근 7일 정책뉴스 아카이브 비어 있음 (gov_policy 크롤 확인)"
    if window_days == WINDOW_DAYS:
        try:
            cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        except Exception as e:
            logger.warning("[GovClouds] cache 쓰기 실패: %s", e)
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    data = get_gov_agency_clouds(refresh=True)
    for ag in data["agencies"]:
        tops = ", ".join(f"{k['text']}({k['count']})" for k in ag["keywords"][:10])
        print(f"[{ag['label']}] {ag['article_count']}건: {tops}")
