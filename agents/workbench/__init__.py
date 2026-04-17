"""Negotiation Workbench — 협상 시나리오 빌더 + xlsx export 모듈

- DefaultAssumptions: HIRA 고시값 기반 공장도비율·VAT·유통마진 기본값
- compute_scenario: 단일 시나리오의 조정가 산출 (min×N% 또는 avg×N%)
- export_workbook:  전체 세션 → 9 sheet xlsx 파일로 export
"""

from .assumptions import (
    DEFAULT_ASSUMPTIONS,
    load_assumptions,
    save_assumptions,
)
from .compute import compute_scenario, compute_all_scenarios
from .dose_normalizer import (
    REFERENCE_SKU,
    normalize_prices,
    parse as parse_dose,
    parse_reference_mg,
)
from .exporter import export_workbook
from .hta_loader import (
    list_available_products,
    load_hta_for_product,
    summarize_hta,
)

__all__ = [
    "DEFAULT_ASSUMPTIONS",
    "load_assumptions",
    "save_assumptions",
    "compute_scenario",
    "compute_all_scenarios",
    "export_workbook",
    "list_available_products",
    "load_hta_for_product",
    "summarize_hta",
    "REFERENCE_SKU",
    "normalize_prices",
    "parse_dose",
    "parse_reference_mg",
]
