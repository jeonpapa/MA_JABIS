"""작업 원장 과제의 담당(owner) — 'server' | 'aside' | 'hermes' (운영 모델: docs/ops/OWNERSHIP.md).

DB 컬럼이 아니라 (source, source_ref, detail 의 '런북: <이름>' 힌트) 에서 **결정적으로** 계산한다. 이유:
  - 마이그레이션·백필이 없다 — 기존 행도 같은 규칙으로 즉시 담당이 붙는다.
  - 규칙이 바뀌면 원장 전체가 같이 바뀐다(행마다 저장된 담당이 규칙과 어긋나는 drift 가 없다).

담당의 뜻 — "누가 다음 행동을 해야 하는가":
  - server : 프로덕션 안의 결정적 런북(runbooks.RUNBOOKS)으로 풀린다. 런북 실행 트리거는 디스패처(`--owner server`)다.
  - aside  : 해외약가(A8/OECD 가격 조사·증빙·로그인 소스 Micromedex/Rote Liste/MIMS, JP/CH 프록시, 가격 요청 워커).
             사용자 노트북의 Aside 브라우저 에이전트가 /api/price-agent/* 와 /api/agent/tasks?owner=aside 로 처리한다.
  - hermes : 판단이 필요하거나 크론이 아닌 일(위원회·정책·Stakeholder·메일링 큐레이션·QG 제안·설정 문제 에스컬레이션 등).
             외부 VPS 의 Hermes 에이전트 팀. 사람(admin) 몫 적체도 Hermes 가 정리·요청하는 담당으로 둔다.

판정 순서(첫 일치):
  1) 해외약가 → aside: source foreign_search / price_request* / foreign_price*,
     scheduler_job 의 foreign_config_probe·foreign_price_backfill, domain_freshness 의 foreign_price_age·foreign_country_coverage
  2) 설정 문제(source_ref 에 ':config') → hermes — 재실행으로 안 풀린다(토큰·권한·secret). Hermes 가 원인을 정리해 사람에게 올린다
  3) detail 의 '런북: <이름>' 이 등록 런북 → server
  4) 그 외 → hermes
"""
from __future__ import annotations

import re
from typing import Iterable, Mapping

OWNERS = ("server", "aside", "hermes")
DEFAULT_OWNER = "hermes"
# scripts/agent_dispatcher.py:RUNBOOK_HINT 와 같은 형식(signals.runbook_line 이 쓴다)
RUNBOOK_HINT = re.compile(r"런북:\s*([a-z_]+)")

ASIDE_SOURCES = frozenset({"foreign_search"})
ASIDE_SOURCE_PREFIXES = ("price_request", "foreign_price")
ASIDE_JOBS = frozenset({"foreign_config_probe", "foreign_price_backfill"})
ASIDE_FRESHNESS_DOMAINS = frozenset({"foreign_price_age", "foreign_country_coverage"})
# 역할별로 선점할 수 있는 담당 — 서버가 claim/resolve 에서 강제한다(api/server.py). admin 은 전부.
ROLE_OWNERS: dict[str, tuple[str, ...]] = {
    "agent": ("server", "hermes"),      # Hermes VPS 디스패처(서버 런북 트리거 포함)
    "price_agent": ("aside",),          # Aside 해외약가 워커 — 최소 권한: aside 과제만
}


def _registered_runbooks() -> frozenset[str]:
    from .runbooks import list_runbooks      # 표준 라이브러리만 import — 웹·VPS 어디서든 안전
    return frozenset(list_runbooks())


def runbook_hint(detail: str | None) -> str | None:
    m = RUNBOOK_HINT.search(str(detail or ""))
    return m.group(1) if m else None


def _head(source_ref: str | None) -> str:
    return str(source_ref or "").split(":", 1)[0]


def is_aside(source: str, source_ref: str | None) -> bool:
    if source in ASIDE_SOURCES or source.startswith(ASIDE_SOURCE_PREFIXES):
        return True
    head = _head(source_ref)
    if source == "scheduler_job" and head in ASIDE_JOBS:
        return True
    return source == "domain_freshness" and head in ASIDE_FRESHNESS_DOMAINS


def owner_for(source: str | None, source_ref: str | None, detail: str | None = None, *,
              runbook: str | None = None) -> str:
    """담당 계산. runbook 을 주면 detail 힌트 대신 그 값을 쓴다(신선도 판정처럼 과제 생성 전에 담당을 알아야 할 때)."""
    source = str(source or "")
    if is_aside(source, source_ref):
        return "aside"
    if ":config" in str(source_ref or ""):
        return "hermes"
    name = runbook if runbook is not None else runbook_hint(detail)
    if name and name in _registered_runbooks():
        return "server"
    return DEFAULT_OWNER


def task_owner(task: Mapping) -> str:
    return owner_for(task.get("source"), task.get("source_ref"), task.get("detail"))


def annotate(tasks: Iterable[dict]) -> list[dict]:
    """과제 dict 에 owner 키를 붙인다(제자리 수정 + 같은 목록 반환)."""
    out = []
    for t in tasks:
        t["owner"] = task_owner(t)
        out.append(t)
    return out


def role_may_handle(role: str | None, owner: str) -> bool:
    if role == "admin":
        return True
    return owner in ROLE_OWNERS.get(str(role or ""), ())
