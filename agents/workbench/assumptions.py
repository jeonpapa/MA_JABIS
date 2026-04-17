"""Workbench Assumptions — HIRA 고시값 기반 기본 가정치

저장 위치: data/workbench/assumptions.json
- 환율: 이전월 기준 36개월 rolling 평균 (KEB하나은행)
- 공장도비율: 국가별 HIRA 고시값
- VAT: 국가별 부가세율
- 유통마진: 국가별 유통마진율
"""

from __future__ import annotations

import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
STORE = BASE_DIR / "data" / "workbench" / "assumptions.json"


# HIRA 고시값 (2026 기준). 변경 시 Audit Log 기록 필요.
DEFAULT_ASSUMPTIONS: dict = {
    "fx_window_months": 36,          # 이전월 기준 rolling window
    "fx_source": "KEB_HANA_BANK",
    "countries": {
        "JP": {
            "currency":        "JPY",
            "factory_ratio":   0.70,
            "vat_rate":        0.10,
            "margin_rate":     0.04,
            "fx_rate_default": 9.12,     # 3-year rolling avg KRW/JPY (예시)
        },
        "IT": {
            "currency":        "EUR",
            "factory_ratio":   0.665,
            "vat_rate":        0.10,
            "margin_rate":     0.30,
            "fx_rate_default": 1476.0,
        },
        "FR": {
            "currency":        "EUR",
            "factory_ratio":   0.77,
            "vat_rate":        0.021,
            "margin_rate":     0.09,
            "fx_rate_default": 1476.0,
        },
        "CH": {
            "currency":        "CHF",
            "factory_ratio":   0.70,
            "vat_rate":        0.025,
            "margin_rate":     0.12,
            "fx_rate_default": 1510.0,
        },
        "UK": {
            "currency":        "GBP",
            "factory_ratio":   0.95,
            "vat_rate":        0.00,
            "margin_rate":     0.125,
            "fx_rate_default": 1780.0,
        },
        "DE": {
            "currency":        "EUR",
            "factory_ratio":   0.85,
            "vat_rate":        0.19,
            "margin_rate":     0.034,
            "fx_rate_default": 1476.0,
        },
        "US": {
            "currency":        "USD",
            "factory_ratio":   0.80,
            "vat_rate":        0.00,
            "margin_rate":     0.15,
            "fx_rate_default": 1380.0,
            "phase":           2,           # Phase 2 placeholder
        },
    },
    "last_updated":    "2026-04-16",
    "updated_by":      "HIRA_DEFAULT",
}


def load_assumptions() -> dict:
    """저장된 가정치 로드. 없으면 DEFAULT_ASSUMPTIONS 반환."""
    if STORE.exists():
        try:
            return json.loads(STORE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return json.loads(json.dumps(DEFAULT_ASSUMPTIONS))


def save_assumptions(data: dict, user: str = "dashboard") -> None:
    """가정치 저장. Audit Log 에도 기록."""
    data = dict(data)
    data["last_updated"] = data.get("last_updated") or ""
    data["updated_by"] = user
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
