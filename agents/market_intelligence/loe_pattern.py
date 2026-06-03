"""LOE 단계 인하 패턴 자동 인식 (KR-RULE-009).

가격 history 산수만으로 mechanism 결정 — LLM stochastic 실패에 영향받지 않음.

KR-RULE-009 (한국 약가 제도 micro-rule):
  - 첫 제네릭 등재 시점, 오리지널 가격이 70% 산정률로 인하 → Year 1 stage
  - Year 1 후, 누적 53.55% 산정률로 추가 인하 (Year 1+ stage)
  - 2026 개편 후 누적 45% Floor rule

원리:
  1. 약제의 가격 history 에서 변동일 직전·직후 가격 추출
  2. 가장 오래된 (특허만료 이전) 가격을 anchor 로 사용
  3. 변동일 가격 / anchor 비율을 계산 → KR-RULE-009 임계값 매칭

예 (자누비아 100mg):
  anchor = ₩1,020 (2009 등재가)  # 또는 첫 인하 직전 ₩924
  - 2023.09.05: ₩592 → 비율 0.70 (= 70%) ✓ Year 1 stage
  - 2024.10.01: ₩453 → 비율 0.5355 ✓ Year 1+ stage
  - 2026 개편: ₩459 → 비율 0.45 ✓ Floor rule
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


# KR-RULE-009 임계값 (산정률)
_THRESHOLDS = [
    # (label, target_ratio, kr_rule, stage_desc)
    ("Year 1 patent stage",     0.70,  "KR-RULE-009", "특허 만료 직후 첫 1년 (70% 산정률, -30% 인하)"),
    ("Year 1+ patent stage",    0.5355, "KR-RULE-009", "특허 만료 1년 후 추가 인하 (누적 53.55% 산정률, 추가 -23.5% 인하)"),
    ("2026 reform Floor rule",  0.45,  "KR-RULE-031", "2026 개편 — 누적 45% Floor rule"),
]
_TOLERANCE = 0.025  # ±2.5% (산수 매칭 허용 오차)


@dataclass
class LOEDetection:
    """KR-RULE-009 stage 매칭 결과."""
    matched: bool
    stage_label: Optional[str] = None
    target_ratio: Optional[float] = None
    actual_ratio: Optional[float] = None
    anchor_price: Optional[int] = None
    anchor_date: Optional[str] = None
    new_price: Optional[int] = None
    kr_rule: Optional[str] = None
    stage_desc: Optional[str] = None
    delta_pct_from_anchor: Optional[float] = None
    delta_pct_from_prev: Optional[float] = None

    def to_reason_text(self) -> str:
        """LLM 이 사용할 수 있는 reason 본문 형태."""
        if not self.matched:
            return ""
        return (
            f"가격 시계열 산수 분석: 기준가 ₩{self.anchor_price} ({self.anchor_date}) → "
            f"변동일 ₩{self.new_price} (누적 산정률 {self.actual_ratio:.1%}). "
            f"이는 **{self.kr_rule} {self.stage_label}** 와 일치 — {self.stage_desc}. "
            f"기준가 대비 누적 {self.delta_pct_from_anchor:.2%} 인하."
        )


def detect_loe_stage(
    price_history: list[dict],
    change_date: str,
) -> LOEDetection:
    """가격 history 에서 KR-RULE-009 단계 매칭.

    Anchor 결정 원칙: "특허 만료 직전 가격" — 이미 PVA·실거래가 인하 누적된 상태에서
    LOE 가 시작되므로, history 첫 row 가 아니라 **첫 대규모 인하 (≥25%) 직전 가격** 을 anchor 로 사용.
    이 대규모 인하가 KR-RULE-009 Year 1 stage (-30% / 70%) 의 신호.

    Args:
        price_history: db.get_price_history() 반환 (apply_date 오름차순)
        change_date: "YYYY.MM.DD" 변동일

    Returns: LOEDetection — matched=False 면 LLM 분류로 fallback
    """
    if not price_history or len(price_history) < 2:
        return LOEDetection(matched=False)

    target_date = _parse_date(change_date)
    if not target_date:
        return LOEDetection(matched=False)

    # 변동일과 정확 일치하는 row
    new_price_row = None
    for row in price_history:
        if (row.get("apply_date") or "").replace(".", "") == change_date.replace(".", ""):
            new_price_row = row
            break
    if not new_price_row:
        return LOEDetection(matched=False)
    new_price = new_price_row.get("max_price")
    if not new_price:
        return LOEDetection(matched=False)

    # Anchor 탐색: change_date 이전·이내 첫 대규모 인하 (≥25%) 직전 가격
    # 없으면 history 의 첫 row 를 anchor 로 사용 (LOE 도래 전 약제 케이스)
    anchor_price = price_history[0].get("max_price")
    anchor_date = price_history[0].get("apply_date")
    for i in range(1, len(price_history)):
        d = _parse_date(price_history[i].get("apply_date") or "")
        if not d or d > target_date:
            break
        prev_p = price_history[i - 1].get("max_price")
        curr_p = price_history[i].get("max_price")
        if not prev_p or not curr_p or prev_p <= 0:
            continue
        drop = (prev_p - curr_p) / prev_p
        if drop >= 0.25:  # 25% 이상 단일 인하 = LOE 신호
            anchor_price = prev_p
            anchor_date = price_history[i - 1].get("apply_date")
            break

    if not anchor_price or anchor_price <= 0:
        return LOEDetection(matched=False)

    actual_ratio = new_price / anchor_price

    # 직전 가격 대비 변동률 — change_date 직전 row
    prev_price = anchor_price
    for row in price_history:
        d = _parse_date(row.get("apply_date") or "")
        if not d or d >= target_date:
            break
        if row.get("max_price"):
            prev_price = row["max_price"]

    # KR-RULE-009 임계값 매칭
    for label, target, kr_rule, desc in _THRESHOLDS:
        if abs(actual_ratio - target) <= _TOLERANCE:
            delta_prev = (new_price - prev_price) / prev_price if prev_price else 0
            return LOEDetection(
                matched=True,
                stage_label=label,
                target_ratio=target,
                actual_ratio=actual_ratio,
                anchor_price=anchor_price,
                anchor_date=anchor_date,
                new_price=new_price,
                kr_rule=kr_rule,
                stage_desc=desc,
                delta_pct_from_anchor=actual_ratio - 1.0,
                delta_pct_from_prev=delta_prev,
            )

    return LOEDetection(
        matched=False,
        anchor_price=anchor_price,
        new_price=new_price,
        actual_ratio=actual_ratio,
    )


def _parse_date(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    raw = raw[:10].replace("-", ".")
    for fmt in ("%Y.%m.%d", "%Y.%m"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None
