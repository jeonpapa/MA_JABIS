"""Home 정부 기관 키워드 클라우드 — 노이즈/불용어/복합 정책어 필터 (순수 함수).

사용자 리포트(2026-10 persona S3/L1):
  - 비의료 노이즈: '공단 위험분담' 같은 검색어가 국가철도공단·산림청(소나무재선충 공동방제사업,
    피해목·위험목 전수조사)·한국도로공사 기사까지 끌어와 '국민건강보험공단' 클라우드를 오염.
  - 불용어: 등을, 민가, 주변, 방안, 기반, 중심 …
  - 복합 정책어 분리: '위험분담' 이 '위험' + '분담' 으로 쪼개짐.

제공:
  normalize_compounds(text)  : '위험 분담제', '약가 유연계약', '100일 신속 등재' → 단일 canonical 토큰
  is_health_relevant(title, description) : 기사 단위 보건의료 관련성 (비의료 기사 drop)
  is_noise_token(token)      : 토큰 단위 불용어/비의료 기관·인프라어
  filter_keywords(items)     : LLM 요약 키워드 리스트용 필터 (dict {"text"} 또는 str)
  keyword_in_text(kw, text)  : 복합어 공백 변형까지 인정하는 표면 매칭 (근거뉴스 연결용)
"""
from __future__ import annotations

import re
from typing import Any, Iterable

# ─────────────────────────────────────────────────────────────────────────────
# 복합 정책어 — (canonical, pattern). 긴 것/구체적인 것 먼저.
# canonical 은 한글만 (토크나이저 [가-힣]{2,} 가 한 토큰으로 읽도록).
# ─────────────────────────────────────────────────────────────────────────────
_SEP = r"[\s·ㆍ\-‧]*"
COMPOUND_TERMS: tuple[tuple[str, str], ...] = (
    ("약가유연계약", rf"약가{_SEP}유연{_SEP}계약(?:제도|제)?"),
    ("유연계약", rf"유연{_SEP}계약(?:제도|제)?"),
    ("위험분담", rf"위험{_SEP}분담(?:{_SEP}계약)?(?:제도|제)?"),
    ("사용량약가연동", rf"사용량{_SEP}[-~]?{_SEP}약가{_SEP}연동(?:제)?"),
    ("신속등재", rf"(?:100{_SEP}일{_SEP})?신속{_SEP}등재(?:제도|제)?"),
    ("경제성평가면제", rf"경제성{_SEP}평가{_SEP}면제(?:제도|제)?"),
    ("선별급여", rf"선별{_SEP}급여"),
    ("실거래가", rf"실거래{_SEP}가(?:격)?(?=\s|$|[^가-힣])"),
)
# 단일 패스 alternation — 이미 치환된 canonical('약가유연계약') 안을 짧은 패턴('유연계약')이 다시 쪼개지 않도록
_COMPOUND_RE = re.compile("|".join(f"(?P<c{i}>{p})" for i, (_, p) in enumerate(COMPOUND_TERMS)))
_COMPOUND_BY_GROUP = {f"c{i}": c for i, (c, _) in enumerate(COMPOUND_TERMS)}
COMPOUND_CANON = frozenset(c for c, _ in COMPOUND_TERMS)

# 복합어 일부로만 의미가 있는 조각 — 단독 토큰이면 제외 ('위험' '분담' 분리 방지)
COMPOUND_FRAGMENTS = frozenset({"위험", "분담", "유연", "계약", "신속", "연동", "면제", "선별"})

# ─────────────────────────────────────────────────────────────────────────────
# 비의료 기관/인프라/일반 행정 노이즈 (토큰 단위 + 기사 단위 마커)
# ─────────────────────────────────────────────────────────────────────────────
NON_HEALTH_TERMS = frozenset({
    # 기관
    "국가철도공단", "철도공단", "한국도로공사", "도로공사", "산림청", "국토부", "국토교통부",
    "국토교통위원회", "한국토지주택공사", "환경부", "산업통상자원부", "국방부", "경찰청",
    "한국전력공사", "한전", "근로복지공단", "국민연금공단", "공무원연금공단", "한국산업인력공단",
    # 인프라/산림 방제 (소나무재선충 등)
    "철도", "도로", "고속도로", "선로", "역사", "교량", "터널",
    "피해목", "위험목", "공동방제사업", "방제", "방제사업", "재선충", "소나무재선충", "소나무",
    "산림", "벌채", "전수조사", "생활권", "민가",
    # 유통/노무 (국감 일반)
    "홈플러스", "퇴직금", "상여금", "희망퇴직", "미지급", "임금체불",
})

# 기사 단위 비의료 마커 — 하나라도 있고 보건의료 강한 표지가 없으면 기사 drop
_NON_HEALTH_ARTICLE_RE = re.compile(
    r"(국가철도공단|철도공단|한국도로공사|도로공사|산림청|국토부|국토교통부|재선충|공동방제|피해목|위험목|"
    r"고속도로|철도|홈플러스|퇴직금|희망퇴직|상여금)"
)
# 보건의료 강한 표지 — 하나만 있어도 관련 기사
_HEALTH_STRONG_RE = re.compile(
    r"(건강보험|건보|국민건강보험공단|건강보험공단|심평원|건강보험심사평가원|식약처|식품의약품안전처|"
    r"보건복지부|복지부|약가|약제|의약품|신약|제약|제네릭|급여기준|요양급여|비급여|위험분담|"
    r"약평위|암질심|품목허가|처방|임상|항암|치료제|백신|병원|의료|의사|약사|환자|보건|"
    r"RSA|HIRA|NHIS|MFDS)"
)
# 보건 기관이 아닌 'OO공단/OO공사' (부산시설공단·국가철도공단 …) — 기사 단위 비의료 마커
_ORG_RE = re.compile(r"[가-힣]{2,}(?:공단|공사)")
_HEALTH_ORGS = frozenset({"국민건강보험공단", "건강보험공단", "건보공단"})

# 불용어 (gov_agency_clouds._STOPWORDS 에 추가) — 사용자 지적 + 집계 상위 일반어
EXTRA_STOPWORDS = frozenset({
    "등을", "등이", "등의", "등은", "등도", "민가", "주변", "방안", "기반", "중심", "중심으로",
    "상황", "역할", "제거", "신속한", "필요한", "높은", "직접", "문제", "열린", "별도", "수준",
    "필요", "가능", "통한", "위한", "이상", "이하", "부분", "측면", "내용", "결과", "사항",
    "강화", "개선", "검토", "적용", "관리", "지원", "대책", "글로벌", "혁신",
    "대표", "사장", "직원", "공급", "인력", "지급", "의원", "장관", "업계", "시장", "단계",
    "운영", "조건", "격차", "경험", "공유", "현지", "부담", "사업", "실적", "직접", "경쟁",
    # 토크나이저 잔여 조각 (조사 strip 부산물 / Naver 발췌 '...' 절단)
    "거쳐", "나온", "만에", "그는", "해당", "있도록", "기존", "과정", "주요", "최대", "일부",
    "실제", "개사", "국민", "한국", "제품", "기업", "기관",
    "에페", "것으", "개로", "치료제인", "식품의약품안전처로", "피해자",
})

_ALL_STOP = EXTRA_STOPWORDS | NON_HEALTH_TERMS


def normalize_compounds(text: str) -> str:
    """복합 정책어 변형(공백/가운뎃점/접미 '제') → canonical 단일 토큰 (양옆 공백)."""
    return _COMPOUND_RE.sub(lambda m: f" {_COMPOUND_BY_GROUP[m.lastgroup]} ", text or "")


def _non_health_marker(text: str) -> bool:
    if _NON_HEALTH_ARTICLE_RE.search(text):
        return True
    return any(m not in _HEALTH_ORGS and not m.endswith("건강보험공단")
               for m in _ORG_RE.findall(text))


def is_health_relevant(title: str, description: str = "") -> bool:
    """기사 단위 — '명백한 비의료 기사' 만 False (보수적: 표지가 없으면 유지).
    - 비의료 마커(국가철도공단·산림청·재선충·홈플러스 퇴직금·OO시설공단 …) 없음 → True
    - 마커 있음 → 보건의료 강한 표지(건강보험·약가·의약품·심평원 …)가 있을 때만 True.
      단 마커가 제목에 있고 강한 표지가 제목엔 없고 본문에 1개뿐이면 False.
    """
    title = title or ""
    text = f"{title} {description or ''}"
    if not _non_health_marker(text):
        return True
    strong = _HEALTH_STRONG_RE.findall(text)
    if not strong:
        return False
    if _non_health_marker(title) and not _HEALTH_STRONG_RE.search(title) and len(strong) < 2:
        return False
    return True


_NON_HEALTH_SUFFIX_RE = re.compile(r"(공단|공사|청)$")
_HEALTH_ORG_OK = frozenset({"국민건강보험공단", "건강보험공단", "건보공단", "식약청", "질병관리청", "질병청"})


def brand_keep_terms() -> frozenset:
    """브랜드명 — 조사 strip 으로 '빌로이'→'빌로', '마운자로'→'마운자' 되는 것 방지 (keep 힌트)."""
    try:
        from agents.competitor_trend_quality import build_brand_map
        return frozenset(build_brand_map().keys())
    except Exception:
        return frozenset()


def is_noise_token(token: str) -> bool:
    """토큰 단위 노이즈 — 불용어 · 비의료 기관/인프라 · 복합어 조각 · 비보건 공단/공사/청."""
    t = (token or "").strip()
    if len(t) < 2:
        return True
    if t in COMPOUND_CANON:
        return False
    if t in _ALL_STOP or t in COMPOUND_FRAGMENTS:
        return True
    if _NON_HEALTH_SUFFIX_RE.search(t) and t not in _HEALTH_ORG_OK and len(t) >= 4:
        return True
    return False


def _kw_text(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("text") or "")
    return str(item or "")


def filter_keywords(items: Iterable[Any]) -> list[Any]:
    """LLM/빈도 키워드 리스트 필터 — 다어절 키워드는 모든 어절이 노이즈일 때만 제외.
    복합어 canonical 로 표기 통일 (예: '위험 분담' → '위험분담'). 중복 제거."""
    out: list[Any] = []
    seen: set[str] = set()
    for it in items or []:
        raw = _kw_text(it).strip()
        if not raw:
            continue
        norm = re.sub(r"\s+", " ", normalize_compounds(raw)).strip()
        words = norm.split(" ")
        if all(is_noise_token(w) for w in words):
            continue
        if any(w in NON_HEALTH_TERMS for w in words):
            continue
        if norm in seen:
            continue
        seen.add(norm)
        if isinstance(it, dict):
            new = dict(it)
            if norm != raw and len(words) == 1:
                new["text"] = norm
            out.append(new)
        else:
            out.append(norm if len(words) == 1 else raw)
    return out


def keyword_in_text(keyword: str, text: str) -> bool:
    """근거뉴스 매칭 — 원문 그대로 또는 복합어 정규화 후 포함."""
    if not keyword:
        return False
    text = text or ""
    if keyword in text:
        return True
    return keyword in normalize_compounds(text).replace(" ", "") if keyword in COMPOUND_CANON else False
