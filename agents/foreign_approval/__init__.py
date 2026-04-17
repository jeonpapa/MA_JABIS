"""ForeignApprovalAgent 패키지 — 적응증 단위 해외 허가사항 수집.

규칙: `agents/rules/foreign_approval_agent_rules.md`

구성:
    models.py   — AgencyResult, BuildSummary dataclass
    builders.py — 6기관 build 루프 (_BuildersMixin + _process_indications 공통)
    merger.py   — fragmented master 병합 (_MergerMixin)
    matrix.py   — 커버리지 매트릭스 (_MatrixMixin)
    agent.py    — ForeignApprovalAgent 통합 클래스

공용 API:
    from agents.foreign_approval import ForeignApprovalAgent
"""
from .agent import ForeignApprovalAgent
from .models import AgencyResult, BuildSummary

__all__ = ["ForeignApprovalAgent", "AgencyResult", "BuildSummary"]
