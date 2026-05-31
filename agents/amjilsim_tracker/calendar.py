"""
2026년 심평원 양 위원회 차수 캘린더.

대상 위원회 (순차 pipeline)
----------------------------
1. **중증(암)질환심의위원회(암질심)** — 항암제·중증희귀질환 요양급여 사전심의 (연 9회)
   - 항암제 reimbursement pipeline 1단계
2. **약제급여평가위원회(약평위)** — 모든 신약 본 심의 (매월 첫 목요일, 연 12회)
   - 비-항암 신약 직접 진입 또는 암질심 통과 후 진입 (transition 3~6개월)
   - 회사·심평원/NHIS 협상이 두 위원회 사이 + 약평위 후 진행

원칙
----
- official_session_date(공식 회의일)가 1차 키. ordinal은 ORDER BY date로 자동 산출.
- 매체별 차수 표기(4차 vs 5차)는 session_resolver.py가 매핑.
- ordinal_assumed는 §6.1 핸드오프 문서 추정. 보건복지부 공식 의결결과 PDF 확정 시 갱신.

D-N 게이트
----------
calendar.py는 단순 상수만 제공. APScheduler cron이 매일 호출해
"오늘이 D-N에 해당하는 차수"가 있는지만 판별 → 실제 작업은 agent.py에서.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional


@dataclass(frozen=True)
class CommitteeSession:
    committee: str                 # 'AMJILSIM' | 'YAKPYUNGWI'
    ordinal: int
    session_date: date
    note: str = ""

    # 하위 호환 — 기존 코드가 .ordinal_assumed 참조
    @property
    def ordinal_assumed(self) -> int:
        return self.ordinal


# 암질심 — 2026 일자 (HIRA 공식 보도자료 기준, 2026-05-30 정정)
# HIRA verified: 3/4=2차 (brdBltNo 미확인), 4/15=4차 (brdBltNo 11770), 5/27=5차 (brdBltNo 11808)
# 3차 암질심은 별도 일자에 존재할 가능성 — 추가 fetch 필요. 1/21=1차는 가정.
AMJILSIM_SESSIONS_2026: list[CommitteeSession] = [
    CommitteeSession('AMJILSIM', 1, date(2026, 1, 21),
                     note="1차 가정. HIRA brdBltNo 미확인."),
    CommitteeSession('AMJILSIM', 2, date(2026, 3, 4),
                     note="HIRA 공식 2차 (게시번호 4959)."),
    # 3차 암질심 일자 미확정 — 3/4(2차)와 4/15(4차) 사이 어딘가 (3월 말 또는 4월 초 추정)
    CommitteeSession('AMJILSIM', 4, date(2026, 4, 15),
                     note="HIRA 공식 4차 (brdBltNo 11770). 옵디보+여보이 1차 통과·투키사·티루캡·킴리아 미설정."),
    CommitteeSession('AMJILSIM', 5, date(2026, 5, 27),
                     note="HIRA 공식 5차 (brdBltNo 11808). 엘라히어·버제니오 통과 / 림카토·알레센자·키스칼리 미설정. v2~v5 baseline 보고서."),
    CommitteeSession('AMJILSIM', 6, date(2026, 7, 8),
                     note="6차 (5+1). Welireg(2026-03-20 신청) 추적 우선 차수."),
    CommitteeSession('AMJILSIM', 7, date(2026, 8, 19)),
    CommitteeSession('AMJILSIM', 8, date(2026, 9, 30)),
    CommitteeSession('AMJILSIM', 9, date(2026, 11, 11)),
    CommitteeSession('AMJILSIM', 10, date(2026, 12, 23)),
]

# 약평위 12차 — 매월 첫 목요일 (사용자 확정 일정)
YAKPYUNGWI_SESSIONS_2026: list[CommitteeSession] = [
    CommitteeSession('YAKPYUNGWI', 1,  date(2026, 1, 15)),
    CommitteeSession('YAKPYUNGWI', 2,  date(2026, 2, 5)),
    CommitteeSession('YAKPYUNGWI', 3,  date(2026, 3, 5)),
    CommitteeSession('YAKPYUNGWI', 4,  date(2026, 4, 2)),
    CommitteeSession('YAKPYUNGWI', 5,  date(2026, 5, 7)),
    CommitteeSession('YAKPYUNGWI', 6,  date(2026, 6, 4),
                     note="옵션 A 첫 매뉴얼 D-2 라이브 후보 (6/2)."),
    CommitteeSession('YAKPYUNGWI', 7,  date(2026, 7, 2),
                     note="옵션 B 첫 자동 D-2 라이브 후보 (6/30)."),
    CommitteeSession('YAKPYUNGWI', 8,  date(2026, 8, 6)),
    CommitteeSession('YAKPYUNGWI', 9,  date(2026, 9, 3)),
    CommitteeSession('YAKPYUNGWI', 10, date(2026, 10, 1)),
    CommitteeSession('YAKPYUNGWI', 11, date(2026, 11, 5)),
    CommitteeSession('YAKPYUNGWI', 12, date(2026, 12, 3)),
]

# 통합 list (호출 편의)
ALL_SESSIONS_2026: list[CommitteeSession] = (
    AMJILSIM_SESSIONS_2026 + YAKPYUNGWI_SESSIONS_2026
)

# 하위 호환 — 기존 코드가 SESSIONS_2026 + AmjilsimSession 참조
SESSIONS_2026 = AMJILSIM_SESSIONS_2026
AmjilsimSession = CommitteeSession


# D-N 게이트 정의 (음수 = D-Day 이전, 양수 = D-Day 이후)
# v3: D-2/D+1이 자동 보고서 발사 시점. D-30/D-14/D-7은 백그라운드 크롤 누적 trigger.
# D+7은 월간 트렌드 리포트 D+0 판단(매월 마지막 약평위 + 7일)에 사용.
TRIGGER_OFFSETS = {
    "d_minus_30": -30,
    "d_minus_14": -14,
    "d_minus_7": -7,
    "d_minus_2": -2,          # v3 D-2 사전 예측 리포트 trigger ⭐
    "d_minus_1": -1,          # v1 legacy (보고서 시점 D-1 → D-2로 이동)
    "d_plus_0": 0,
    "d_plus_1": 1,
    "d_plus_3": 3,            # v1 legacy (D+3 리포트 제거)
    "d_plus_7": 7,            # v3 월간 트렌드 리포트 D+0 판단용
}


def session_for_offset(
    today: date,
    offset_key: str,
    committee: Optional[str] = None,
) -> Optional[CommitteeSession]:
    """오늘이 어느 차수의 D-N에 해당하면 그 차수 반환. committee로 필터링 가능."""
    offset = TRIGGER_OFFSETS[offset_key]
    target = today - timedelta(days=offset)
    pool = ALL_SESSIONS_2026 if committee is None else [
        s for s in ALL_SESSIONS_2026 if s.committee == committee
    ]
    for s in pool:
        if s.session_date == target:
            return s
    return None


def next_session(
    after: date,
    committee: Optional[str] = None,
) -> Optional[CommitteeSession]:
    """`after` 이후 첫 차수. committee로 필터링 가능."""
    pool = ALL_SESSIONS_2026 if committee is None else [
        s for s in ALL_SESSIONS_2026 if s.committee == committee
    ]
    upcoming = [s for s in pool if s.session_date > after]
    return upcoming[0] if upcoming else None


def session_by_date(d: date, committee: Optional[str] = None) -> Optional[CommitteeSession]:
    pool = ALL_SESSIONS_2026 if committee is None else [
        s for s in ALL_SESSIONS_2026 if s.committee == committee
    ]
    for s in pool:
        if s.session_date == d:
            return s
    return None


def is_last_yakpyungwi_of_month(d: date) -> bool:
    """`d`가 자신이 속한 달의 마지막 약평위 차수 일자인지 (월간 트렌드 리포트 D+0 판단용)."""
    same_month = [s for s in YAKPYUNGWI_SESSIONS_2026
                  if s.session_date.year == d.year and s.session_date.month == d.month]
    if not same_month:
        return False
    return d == max(s.session_date for s in same_month)
