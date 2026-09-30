"""스토리 단위 클러스터링 — 같은 사건을 보도한 여러 매체 기사를 한 묶음으로.

2026-09-19 파일럿: 구독 19 는 5건 중 4건이 '사용량-약가 연동 80품목 4.6% 인하' 였고(매체만 다름),
구독 14/16 은 '키트루다SC 일본 승인' 이 2번 실렸다. `_editorial_theme_key` 는 하드코딩 테마 + URL 만 보므로
매체별 재보도를 거르지 못한다.

방법 (LLM 호출 없음 · 결정적 · 저비용):
1. 제목 정규화 — 선두 태그([단독]·[속보]), 매체 접미사(' - 데일리팜'), 구두점·따옴표 제거, 소문자, 조사 분리.
2. 두 제목이 같은 사건인지 판정 — 공유 토큰을 '특정 개체'(약제명·기관명·숫자·국가 등) 와
   '범용 MA 어휘'(급여·약가·승인·확대·인하 …) 로 나눠 센다. 범용 어휘는 이 도메인 거의 모든 제목에 나오므로
   증거가 되지 않는다. 특정 개체 ≥2 / 특정 1 + 범용 ≥2 / 정규화 제목 유사도 ≥ NEAR_DUPLICATE 이면 같은 사건.
   토큰 비교는 포함 관계도 허용한다 ('키트루다' ⊂ '키트루다sc', '약가' ⊂ '약가협상').
3. union-find 로 전이적 결합 (A~C, B~C 이면 A·B·C 한 묶음). 묶음 순서·묶음 내 순서는 입력 순서 보존.

`cluster_stories(items)` 는 `.title` 속성만 요구하는 순수 함수다.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Iterable, Sequence, TypeVar

T = TypeVar("T")

NEAR_DUPLICATE = 0.75          # 정규화 제목 SequenceMatcher ratio 이 값 이상이면 증거 없이도 같은 사건
MIN_SPECIFIC_ALONE = 2         # 특정 개체만으로 묶는 최소 공유 수
MIN_GENERIC_WITH_ONE = 2       # 특정 개체 1개 + 범용 어휘 최소 공유 수
MIN_NUMBER_CHARS = 2           # 공유 숫자 증거는 두 자리 이상 또는 소수 ('1차'·'2건' 은 제외)

_LEADING_TAG = re.compile(r"^(?:\s*[\[【（(][^\]】）)]{1,12}[\]】）)])+")
_OUTLET_SUFFIX = re.compile(r"\s*[-|–—]\s*[\w가-힣.]{2,12}$")
_ELLIPSIS = re.compile(r"\.{2,}|…")
_KEEP = re.compile(r"[^\w%]+", re.UNICODE)          # 단어문자·% 외 전부 공백 (소수점은 아래서 보호)
_DECIMAL = re.compile(r"(?<=\d)\.(?=\d)")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_PARTICLES = ("에서는", "으로는", "에서", "으로", "로는", "은", "는", "이", "가", "을", "를",
              "의", "에", "로", "와", "과", "도", "서")
# 이 도메인 제목 대부분에 나오는 어휘 — 공유돼도 '같은 사건' 의 증거가 되지 않는다
GENERIC_TERMS = frozenset("""
급여 약가 약평위 암질심 건정심 심평원 건보공단 공단 복지부 식약처 정부 국회 승인 허가 확대 인하 인상 등재 협상
품목 의약품 제약 신약 치료제 항암제 국내 발표 결정 통과 적용 시행 추진 검토 논의 예정 전망 기대 우려 지적
강조 요구 촉구 필요 논란 계획 방안 대책 개편 개정 제도 정책 환자 병용 치료 요법 회사 기업 업계 시장 매출
출시 도입 계약 체결 공개 발간 보고서 조사 결과 대상 기준 최초 평균 인정 적정성 재평가 심의 안건 제형 획득
통보 착수 예고 행정예고 신속 개선 강화 완화 지원 확보 확인 관련 관계 관계자 대한 위한 통해 오늘 내달 지난
올해 이달 내년 최근 한다 했다 된다 됐다 밝혔다 나섰다 눈앞 본격 가능 여부 문제 방침 이어 첫 새 등 및
""".split())


def _strip_particle(token: str) -> str:
    for particle in sorted(_PARTICLES, key=len, reverse=True):
        if token.endswith(particle) and len(token) - len(particle) >= 2:
            return token[: -len(particle)]
    return token


def normalize_title(title: str) -> str:
    """선두 태그·매체 접미사·구두점 제거, 소문자, 조사 분리, 공백 정규화. 괄호 안 내용은 보존한다
    ('피하주사(SC)' → '피하주사 sc')."""
    text = _LEADING_TAG.sub(" ", title or "")
    text = _ELLIPSIS.sub(" ", text)
    text = _OUTLET_SUFFIX.sub("", text.strip())
    text = _DECIMAL.sub("\x00", text.lower())     # 4.6 의 소수점 보호
    text = _KEEP.sub(" ", text).replace("\x00", ".")
    tokens = [_strip_particle(tok) for tok in text.split()]
    # 따옴표 뒤에 떨어져 나온 단독 조사("'협상'으로" → "협상 으로") 는 버린다
    return " ".join(tok for tok in tokens if tok and tok not in _PARTICLES)


def _tokens(norm: str) -> list[str]:
    return [tok for tok in norm.split() if len(tok) >= 2]


def _numbers(norm: str) -> set[str]:
    return {n for n in _NUMBER.findall(norm) if len(n) >= MIN_NUMBER_CHARS or "." in n}


def _match(token: str, other_tokens: Sequence[str]) -> str | None:
    """token 과 같거나 포함 관계인 상대 토큰 (없으면 None). 완전 일치를 우선한다."""
    if token in other_tokens:
        return token
    return next((o for o in other_tokens if token in o or o in token), None)


def _contained(token: str, other_tokens: Sequence[str]) -> bool:
    return _match(token, other_tokens) is not None


def _shared_evidence(norm_a: str, norm_b: str) -> tuple[int, int]:
    """(특정 개체 공유 수, 범용 어휘 공유 수). 양방향 포함 매칭, 방향별 결과의 최대값."""
    ta, tb = _tokens(norm_a), _tokens(norm_b)
    if not ta or not tb:
        return 0, 0
    best_specific = best_generic = 0
    for src, dst in ((ta, tb), (tb, ta)):
        specific: set[str] = set()
        generic: set[str] = set()
        for tok in src:
            counterpart = _match(tok, dst)
            if counterpart is None:
                continue
            # 포함 매칭('약가협상' ⊃ '약가') 은 어느 한쪽이 범용이면 범용 증거로만 센다
            (generic if tok in GENERIC_TERMS or counterpart in GENERIC_TERMS else specific).add(tok)
        # 두 자리 이상 숫자('80'·'4.6'·'339')는 단위 표기가 달라도('80개'·'80품목') 특정 증거.
        # 숫자로 시작하는 토큰('80개'·'4.6%'·'2027년')은 숫자 증거로만 세어 이중 계산을 막는다 ('her2' 는 개체).
        numbers = _numbers(norm_a) & _numbers(norm_b)
        specific_count = len({s for s in specific if not _NUMBER.match(s)}) + len(numbers)
        best_specific = max(best_specific, specific_count)
        best_generic = max(best_generic, len(generic))
    return best_specific, best_generic


def story_similarity(title_a: str, title_b: str) -> float:
    """정규화 제목 유사도 [0,1] — SequenceMatcher ratio 와 토큰 포함 겹침(min 기준) 의 평균. 대칭."""
    na, nb = normalize_title(title_a), normalize_title(title_b)
    if not na or not nb:
        return 0.0
    seq = SequenceMatcher(None, na, nb).ratio()
    ta, tb = _tokens(na), _tokens(nb)
    if not ta or not tb:
        return round(seq, 4)
    overlap_ab = sum(1 for t in ta if _contained(t, tb)) / len(ta)
    overlap_ba = sum(1 for t in tb if _contained(t, ta)) / len(tb)
    return round((seq + max(overlap_ab, overlap_ba)) / 2, 4)


def same_story(title_a: str, title_b: str) -> bool:
    na, nb = normalize_title(title_a), normalize_title(title_b)
    if not na or not nb:
        return False
    if na == nb or SequenceMatcher(None, na, nb).ratio() >= NEAR_DUPLICATE:
        return True
    specific, generic = _shared_evidence(na, nb)
    if specific >= MIN_SPECIFIC_ALONE:
        return True
    return specific >= 1 and generic >= MIN_GENERIC_WITH_ONE


def cluster_stories(items: Iterable[T]) -> list[list[T]]:
    """같은 사건 기사를 묶는다. 묶음 순서·묶음 내 순서는 입력 순서. 전이적 결합(union-find)."""
    seq = list(items)
    n = len(seq)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    titles = [str(getattr(it, "title", "") or "") for it in seq]
    for i in range(n):
        for j in range(i + 1, n):
            if find(i) != find(j) and same_story(titles[i], titles[j]):
                parent[find(j)] = find(i)
    groups: dict[int, list[T]] = {}
    for i, item in enumerate(seq):
        groups.setdefault(find(i), []).append(item)
    return list(groups.values())
