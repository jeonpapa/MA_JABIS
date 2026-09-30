"""스토리 단위 클러스터링 — 2026-09-19 파일럿에서 같은 사건이 매체별로 반복 노출된 회귀.

구독 19: '사용량-약가 연동 80품목 4.6% 인하' 가 5건 중 4건. 구독 14/16: '키트루다SC 일본 승인' 2건.
클러스터링은 LLM 없이 제목 정규화 + 유사도 + 공유 숫자/개체 휴리스틱만 쓴다 (결정적·저비용).
"""
from __future__ import annotations

from dataclasses import dataclass

from agents.daily_mailing.cluster import cluster_stories, normalize_title, story_similarity


@dataclass(frozen=True)
class T:
    title: str
    description: str = ""


# 파일럿 구독 19 실제 제목 (같은 사건, 매체 3곳)
USAGE_LINK_80 = [
    "듀카브·램시마 등 80개 품목 약가 평균 4.6% 인하…339억원 절감",
    "건보공단, '사용량-약가 연동 협상'으로 80개 품목 약가 인하",
    "사용량 늘어난 의약품 80개 약가 평균 4.6% 인하",
]
# 파일럿 구독 14/16: 키트루다SC 일본 승인 (매체 2곳) — 표기 변형(피하주사/SC, 일본/일본서)
KEYTRUDA_SC_JP = [
    "MSD 키트루다 피하주사(SC) 제형, 일본 후생노동성 승인",
    "키트루다SC 일본서 승인…투여 시간 1분으로 단축",
]
# 같은 날 다른 사건 — 개체가 겹쳐도 묶이면 안 된다
DISTINCT = [
    "키트루다, 국내 자궁내막암 1차 급여 확대",
    "약평위, 엔허투 급여 적정성 인정",
    "약평위, 렉라자 급여 재평가 착수",
    "복지부, 2027년 약가제도 개편안 행정예고",
    "약사회 '성분명 처방' 촉구 집회…복지부 신속 추진 요구",
]


def _titles(cluster):
    return [t.title for t in cluster]


def test_pilot_usage_link_titles_cluster_together():
    items = [T(t) for t in USAGE_LINK_80]
    clusters = cluster_stories(items)
    assert len(clusters) == 1
    assert _titles(clusters[0]) == USAGE_LINK_80          # 입력 순서 보존


def test_pilot_keytruda_sc_titles_cluster_together():
    clusters = cluster_stories([T(t) for t in KEYTRUDA_SC_JP])
    assert len(clusters) == 1


def test_distinct_stories_stay_separate():
    clusters = cluster_stories([T(t) for t in DISTINCT])
    assert len(clusters) == len(DISTINCT)


def test_mixed_input_keeps_first_appearance_order_and_groups_transitively():
    items = [T(USAGE_LINK_80[0]), T(DISTINCT[0]), T(KEYTRUDA_SC_JP[0]), T(USAGE_LINK_80[1]),
             T(KEYTRUDA_SC_JP[1]), T(USAGE_LINK_80[2]), T(DISTINCT[1])]
    clusters = cluster_stories(items)
    assert [_titles(c) for c in clusters] == [
        USAGE_LINK_80,                 # 1↔3 강한 유사, 2 는 3 을 통해 전이적으로 결합
        [DISTINCT[0]],
        KEYTRUDA_SC_JP,
        [DISTINCT[1]],
    ]


def test_same_number_alone_does_not_cluster():
    # 숫자 '80' 만 공유하고 사건이 다르면 별개
    a = T("약평위, 이달 80개 안건 심의 예정")
    b = T(USAGE_LINK_80[2])
    assert len(cluster_stories([a, b])) == 2


def test_generic_term_matched_by_containment_is_not_entity_evidence():
    # '약가협상' ⊃ '약가', '건보공단' 공유 — 둘 다 범용 어휘라 다른 사건으로 남아야 한다
    a = T("건보공단, 약가협상 결렬 통보")
    b = T("건보공단, 사용량-약가 연동 80개 품목 인하")
    assert len(cluster_stories([a, b])) == 2
    c = T("복지부, 2027년 약가제도 개편안 행정예고")
    assert len(cluster_stories([c, T(USAGE_LINK_80[0])])) == 2


def test_empty_and_single_inputs():
    assert cluster_stories([]) == []
    assert cluster_stories([T("단일 기사")]) == [[T("단일 기사")]]


def test_normalize_title_strips_brackets_outlet_suffix_and_punctuation():
    assert normalize_title("[단독] 키트루다SC 일본서 승인…투여 시간 단축 - 데일리팜") == "키트루다sc 일본 승인 투여 시간 단축"
    assert normalize_title("건보공단, '사용량-약가 연동 협상'으로 80개 품목 약가 인하 | 약업신문") \
        == "건보공단 사용량 약가 연동 협상 80개 품목 약가 인하"


def test_story_similarity_is_symmetric_and_bounded():
    a, b = USAGE_LINK_80[0], USAGE_LINK_80[1]
    assert story_similarity(a, b) == story_similarity(b, a)
    assert 0.0 <= story_similarity(a, b) <= 1.0
    assert story_similarity(a, a) == 1.0
    assert story_similarity(USAGE_LINK_80[0], DISTINCT[3]) < story_similarity(USAGE_LINK_80[0], USAGE_LINK_80[2])
