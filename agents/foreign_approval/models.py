"""빌드 결과 dataclass — agency 단위 누적 카운터 + 전체 summary."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AgencyResult:
    agency:  str
    ok:      int = 0
    failed:  int = 0
    matched: int = 0
    new:     int = 0
    elapsed: float = 0.0
    errors:  list[str] = field(default_factory=list)


@dataclass
class BuildSummary:
    product:   str
    agencies:  list[AgencyResult]
    wiped:     bool

    def to_dict(self) -> dict:
        return {
            "product":  self.product,
            "wiped":    self.wiped,
            "agencies": [a.__dict__ for a in self.agencies],
        }
