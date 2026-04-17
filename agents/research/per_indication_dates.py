"""
적응증별 실제 승인일 매핑.

각 에이전시의 이니셜 권한 승인일(initial authorization)이 아닌,
특정 적응증이 라벨에 추가된 실제 승인 또는 variation 일자.

dict key = indications_master.indication_id
dict value = {agency: YYYY-MM-DD}

출처: FDA Oncology Approvals, EMA EPAR variations, PMDA 審議結果報告書,
     MFDS 의약품통합정보시스템, MHRA product info, TGA ARTG history.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Dict

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "db" / "drug_prices.db"


# FDA 적응증별 승인일 (공개된 FDA Oncology Approvals 기반).
KEYTRUDA_FDA: Dict[str, str] = {
    "keytruda_btc_1l_metastatic_gem_cis": "2023-10-31",
    "keytruda_cc_metastatic_pdl1_1_bevacizumab": "2021-10-13",  # KEYNOTE-826
    "keytruda_cc_1l_locally_advanced_crt": "2024-01-12",        # KEYNOTE-A18
    "keytruda_cc_2l_recurrent_pdl1_1_mono": "2018-06-12",       # KEYNOTE-158
    "keytruda_crc_1l_metastatic_msi_h_mono": "2020-06-29",      # KEYNOTE-177
    "keytruda_ec_1l_advanced_mono": "2024-06-17",               # KEYNOTE-868/NRG-GY018
    "keytruda_ec_2l_advanced_lenvatinib": "2021-07-21",         # KEYNOTE-775
    "keytruda_ec_3l_advanced_msi_h_mono": "2017-05-23",         # MSI-H tissue agnostic
    "keytruda_esc_2l_locally_advanced_pdl1_10_mono": "2019-07-30",  # KEYNOTE-181
    "keytruda_gc_1l_metastatic_pdl1_1_trastuzumab": "2021-05-05",   # KEYNOTE-811
    "keytruda_gc_1l_locally_advanced_unresectable_or_metastatic_pdl1_1_platinum": "2023-11-16",  # KEYNOTE-859
    "keytruda_hcc_2l_mono": "2018-11-09",                        # KEYNOTE-224
    "keytruda_hnscc_1l_metastatic_platinum_fu": "2019-06-10",    # KEYNOTE-048
    "keytruda_hnscc_1l_unresectable_recurrent_pdl1_1_mono": "2019-06-10",  # KEYNOTE-048
    "keytruda_hnscc_2l_recurrent_or_metastatic_mono": "2016-08-05",  # KEYNOTE-012
    "keytruda_hnscc_perioperative_resectable_pdl1_1_mono": "2025-06-12",  # KEYNOTE-689
    "keytruda_mcc_metastatic_mono": "2018-12-19",                # KEYNOTE-017
    "keytruda_mel_advanced_mono": "2014-09-04",                  # 최초 승인 (KEYNOTE-001/006)
    "keytruda_mel_adjuvant_adjuvant_mono": "2019-02-15",         # KEYNOTE-054
    "keytruda_nsclc_1l_metastatic_pemetrexed": "2018-08-20",     # KEYNOTE-189
    "keytruda_nsclc_1l_metastatic_carbo_pacl": "2018-10-30",     # KEYNOTE-407
    "keytruda_nsclc_1l_metastatic_pdl1_1_mono": "2019-04-11",    # KEYNOTE-042
    "keytruda_nsclc_2l_metastatic_pdl1_1_mono": "2015-10-02",    # KEYNOTE-010
    "keytruda_nsclc_adjuvant_resectable_mono": "2023-01-26",     # KEYNOTE-091
    "keytruda_nsclc_perioperative_resectable_mono": "2023-10-16",  # KEYNOTE-671
    "keytruda_oc_2l_pdl1_1_bevacizumab": "2021-10-13",           # KEYNOTE-826
    "keytruda_pmbcl_3l_mono": "2018-06-13",                      # KEYNOTE-170
    "keytruda_rcc_1l_advanced_axitinib": "2019-04-19",           # KEYNOTE-426
    "keytruda_rcc_1l_advanced_lenvatinib": "2021-08-10",         # KEYNOTE-581/CLEAR
    "keytruda_rcc_adjuvant_adjuvant_mono": "2021-11-17",         # KEYNOTE-564
    "keytruda_solid_2l_unresectable_msi_h_mono": "2017-05-23",   # MSI-H tissue agnostic
    "keytruda_solid_2l_unresectable_or_metastatic_tmb_h_mono": "2020-06-16",  # TMB-H tissue agnostic
    "keytruda_tnbc_1l_metastatic_pdl1_10_chemo": "2020-11-13",   # KEYNOTE-355
    "keytruda_tnbc_perioperative_mono": "2021-07-26",            # KEYNOTE-522
    "keytruda_uc_locally_advanced_ev": "2023-12-15",             # KEYNOTE-A39/EV-302
    "keytruda_uc_locally_advanced_or_metastatic_mono": "2017-05-18",  # KEYNOTE-052
    "keytruda_uc_2l_mono": "2017-05-18",                         # KEYNOTE-045
    "keytruda_uc_perioperative_locally_advanced_ev": "2025-03-28",   # MIBC peri-op EV+pembro
    "keytruda_chl_3l_mono": "2017-03-14",                        # KEYNOTE-087
    "keytruda_chl_3l_recurrent_mono": "2017-03-14",              # KEYNOTE-087
    "keytruda_cscc_recurrent_mono": "2020-06-24",                # KEYNOTE-629
}


# EMA variations (CHMP opinion → EC decision).
KEYTRUDA_EMA: Dict[str, str] = {
    "keytruda_btc_1l_metastatic_gem_cis": "2024-04-26",
    "keytruda_cc_metastatic_pdl1_1_bevacizumab": "2022-03-21",
    "keytruda_cc_1l_locally_advanced_crt": "2024-09-17",
    "keytruda_crc_1l_metastatic_msi_h_mono": "2021-01-25",
    "keytruda_ec_1l_advanced_mono": "2024-10-02",
    "keytruda_ec_2l_advanced_lenvatinib": "2021-11-17",
    "keytruda_esc_2l_locally_advanced_pdl1_10_mono": "2021-05-20",
    "keytruda_gc_1l_metastatic_pdl1_1_trastuzumab": "2023-12-15",
    "keytruda_gc_1l_locally_advanced_unresectable_or_metastatic_pdl1_1_platinum": "2024-02-19",
    "keytruda_hnscc_1l_metastatic_platinum_fu": "2019-11-28",
    "keytruda_hnscc_1l_unresectable_recurrent_pdl1_1_mono": "2019-11-28",
    "keytruda_mcc_metastatic_mono": "2024-05-14",
    "keytruda_mel_advanced_mono": "2015-07-17",                  # 초기 허가
    "keytruda_mel_adjuvant_adjuvant_mono": "2019-01-31",
    "keytruda_nsclc_1l_metastatic_pemetrexed": "2018-09-03",
    "keytruda_nsclc_1l_metastatic_carbo_pacl": "2019-03-18",
    "keytruda_nsclc_1l_metastatic_pdl1_1_mono": "2019-03-29",
    "keytruda_nsclc_2l_metastatic_pdl1_1_mono": "2016-07-29",
    "keytruda_nsclc_adjuvant_resectable_mono": "2023-05-03",
    "keytruda_nsclc_perioperative_resectable_mono": "2024-04-22",
    "keytruda_pmbcl_3l_mono": "2019-03-04",
    "keytruda_rcc_1l_advanced_axitinib": "2019-09-04",
    "keytruda_rcc_1l_advanced_lenvatinib": "2021-11-17",
    "keytruda_rcc_adjuvant_adjuvant_mono": "2022-04-27",
    "keytruda_tnbc_1l_metastatic_pdl1_10_chemo": "2021-11-11",
    "keytruda_tnbc_perioperative_mono": "2022-04-11",
    "keytruda_uc_locally_advanced_or_metastatic_mono": "2017-08-24",
    "keytruda_uc_2l_mono": "2017-08-24",
    "keytruda_uc_locally_advanced_ev": "2024-08-29",
    "keytruda_chl_3l_mono": "2017-05-02",
    "keytruda_chl_3l_recurrent_mono": "2017-05-02",
    "keytruda_cscc_recurrent_mono": "2022-09-01",
}


# PMDA 適応追加 (一部変更承認) 날짜.
KEYTRUDA_PMDA: Dict[str, str] = {
    "keytruda_mel_advanced_mono": "2016-09-28",                  # 초기 승인
    "keytruda_nsclc_2l_metastatic_pdl1_1_mono": "2016-12-19",
    "keytruda_nsclc_1l_metastatic_pdl1_1_mono": "2017-02-14",
    "keytruda_nsclc_1l_metastatic_pemetrexed": "2018-12-21",
    "keytruda_nsclc_1l_metastatic_carbo_pacl": "2019-02-21",
    "keytruda_hnscc_1l_metastatic_platinum_fu": "2019-12-20",
    "keytruda_hnscc_1l_unresectable_recurrent_pdl1_1_mono": "2019-12-20",
    "keytruda_crc_1l_metastatic_msi_h_mono": "2020-08-21",
    "keytruda_rcc_1l_advanced_axitinib": "2019-12-20",
    "keytruda_rcc_1l_advanced_lenvatinib": "2021-08-25",
    "keytruda_mel_adjuvant_adjuvant_mono": "2019-12-20",
    "keytruda_uc_2l_mono": "2017-12-01",
    "keytruda_esc_2l_locally_advanced_pdl1_10_mono": "2020-03-25",
    "keytruda_tnbc_perioperative_mono": "2022-09-26",
    "keytruda_tnbc_1l_metastatic_pdl1_10_chemo": "2022-09-26",
    "keytruda_rcc_adjuvant_adjuvant_mono": "2022-06-20",
    "keytruda_ec_2l_advanced_lenvatinib": "2022-03-28",
    "keytruda_cc_metastatic_pdl1_1_bevacizumab": "2022-09-26",
    "keytruda_gc_1l_metastatic_pdl1_1_trastuzumab": "2024-02-09",
    "keytruda_nsclc_adjuvant_resectable_mono": "2023-06-26",
    "keytruda_nsclc_perioperative_resectable_mono": "2024-09-24",
    "keytruda_btc_1l_metastatic_gem_cis": "2024-02-09",
    "keytruda_chl_3l_mono": "2017-12-01",
    "keytruda_chl_3l_recurrent_mono": "2017-12-01",
}


# MFDS 는 `scripts.apply_mfds_official_dates` 가 변경이력 API 로 공식일을 자동 적용.
# 과거 하드코딩 dict 는 date_source 회귀 위험으로 제거됨 (2026-04-17).


# MHRA (영국 자체 승인 또는 EC decision 승계 날짜).
KEYTRUDA_MHRA: Dict[str, str] = {
    "keytruda_mel_advanced_mono": "2015-07-17",
    "keytruda_nsclc_2l_metastatic_pdl1_1_mono": "2016-07-29",
    "keytruda_nsclc_1l_metastatic_pdl1_1_mono": "2019-03-29",
    "keytruda_nsclc_1l_metastatic_pemetrexed": "2018-09-03",
    "keytruda_nsclc_1l_metastatic_carbo_pacl": "2019-03-18",
    "keytruda_hnscc_1l_metastatic_platinum_fu": "2019-11-28",
    "keytruda_hnscc_1l_unresectable_recurrent_pdl1_1_mono": "2019-11-28",
    "keytruda_crc_1l_metastatic_msi_h_mono": "2021-01-25",
    "keytruda_rcc_1l_advanced_axitinib": "2019-09-04",
    "keytruda_rcc_1l_advanced_lenvatinib": "2021-11-17",
    "keytruda_mel_adjuvant_adjuvant_mono": "2019-01-31",
    "keytruda_uc_2l_mono": "2017-08-24",
    "keytruda_esc_2l_locally_advanced_pdl1_10_mono": "2021-05-20",
    "keytruda_tnbc_1l_metastatic_pdl1_10_chemo": "2021-11-11",
    "keytruda_tnbc_perioperative_mono": "2022-04-11",
    "keytruda_rcc_adjuvant_adjuvant_mono": "2022-04-27",
    "keytruda_ec_2l_advanced_lenvatinib": "2021-11-17",
    "keytruda_cc_metastatic_pdl1_1_bevacizumab": "2022-03-21",
    "keytruda_nsclc_adjuvant_resectable_mono": "2023-05-03",
    "keytruda_nsclc_perioperative_resectable_mono": "2024-04-22",
    "keytruda_btc_1l_metastatic_gem_cis": "2024-04-26",
    "keytruda_chl_3l_mono": "2017-05-02",
}


# TGA ARTG 등재·변경 일자.
KEYTRUDA_TGA: Dict[str, str] = {
    "keytruda_mel_advanced_mono": "2015-04-16",                  # ARTG 최초 등재
    "keytruda_nsclc_2l_metastatic_pdl1_1_mono": "2016-09-01",
    "keytruda_nsclc_1l_metastatic_pdl1_1_mono": "2017-06-13",
    "keytruda_nsclc_1l_metastatic_pemetrexed": "2018-11-13",
    "keytruda_nsclc_1l_metastatic_carbo_pacl": "2019-01-08",
    "keytruda_hnscc_1l_metastatic_platinum_fu": "2020-02-25",
    "keytruda_hnscc_1l_unresectable_recurrent_pdl1_1_mono": "2020-02-25",
    "keytruda_crc_1l_metastatic_msi_h_mono": "2021-04-07",
    "keytruda_rcc_1l_advanced_axitinib": "2019-11-14",
    "keytruda_rcc_1l_advanced_lenvatinib": "2021-12-02",
    "keytruda_mel_adjuvant_adjuvant_mono": "2019-05-07",
    "keytruda_uc_2l_mono": "2018-05-01",
    "keytruda_esc_2l_locally_advanced_pdl1_10_mono": "2021-07-29",
    "keytruda_tnbc_1l_metastatic_pdl1_10_chemo": "2021-08-12",
    "keytruda_tnbc_perioperative_mono": "2022-06-23",
    "keytruda_rcc_adjuvant_adjuvant_mono": "2022-03-17",
    "keytruda_ec_2l_advanced_lenvatinib": "2021-11-04",
    "keytruda_cc_metastatic_pdl1_1_bevacizumab": "2022-07-07",
    "keytruda_gc_1l_locally_advanced_unresectable_or_metastatic_pdl1_1_platinum": "2024-03-14",
    "keytruda_nsclc_adjuvant_resectable_mono": "2023-06-29",
    "keytruda_nsclc_perioperative_resectable_mono": "2024-08-22",
    "keytruda_btc_1l_metastatic_gem_cis": "2024-05-16",
    "keytruda_chl_3l_mono": "2017-11-30",
    "keytruda_chl_3l_recurrent_mono": "2017-11-30",
}



# Welireg (belzutifan) 적응증별 승인일.
WELIREG_FDA: Dict[str, str] = {
    "welireg_vhl_mono": "2021-08-13",                            # VHL 관련 종양 (LITESPARK-004)
    "welireg_rcc_3l_advanced_mono": "2023-12-14",                # advanced RCC post-TKI/IO
    "welireg_ppgl_locally_advanced_mono": "2025-05-14",          # 2025 새 적응증 (pheo/para)
}
WELIREG_EMA: Dict[str, str] = {
    "welireg_vhl_mono": "2025-03-20",                            # EU VHL 승인
    "welireg_rcc_3l_advanced_mono": "2025-03-20",                # EU RCC 동시
}
WELIREG_PMDA: Dict[str, str] = {
    "welireg_vhl_mono": "2024-06-24",
    "welireg_rcc_3l_advanced_mono": "2024-06-24",
}
WELIREG_MHRA: Dict[str, str] = {
    "welireg_vhl_mono": "2025-04-10",
}
WELIREG_TGA: Dict[str, str] = {
    "welireg_vhl_mono": "2024-11-27",
    "welireg_rcc_3l_advanced_mono": "2024-11-27",
}


# Lynparza (olaparib) 적응증별 승인일.
LYNPARZA_FDA: Dict[str, str] = {
    "lynparza_oc_recurrent_brca_mut_mono": "2014-12-19",         # 초기 승인 (4L)
    "lynparza_oc_2l_recurrent_brca_mut_mono": "2017-08-17",      # maintenance platinum-sensitive
    "lynparza_oc_1l_advanced_brca_mut_mono": "2018-12-19",       # SOLO-1 1L maintenance BRCAmut
    "lynparza_oc_1l_advanced_hrd_pos_bevacizumab": "2020-05-08", # PAOLA-1 HRD+ bev combo
    "lynparza_bc_3l_metastatic_brca_mut_mono": "2018-01-12",     # OlympiAD gBRCA HER2- mBC
    "lynparza_bc_adjuvant_adjuvant_brca_mut_mono": "2022-03-11", # OlympiA adj BRCA HER2- HR high risk
    "lynparza_paad_1l_maintenance_metastatic_brca_mut_mono": "2019-12-27",  # POLO
    "lynparza_paad_1l_metastatic_brca_mut_mono": "2019-12-27",
    "lynparza_mcrpc_2l_metastatic_brca_mut_mono": "2020-05-19",  # PROfound HRR-mutated
    "lynparza_mcrpc_2l_metastatic_hrr_mut_mono": "2020-05-19",
}
LYNPARZA_EMA: Dict[str, str] = {
    "lynparza_oc_1l_advanced_brca_mut_mono": "2019-06-17",
    "lynparza_oc_1l_advanced_hrd_pos_bevacizumab": "2020-11-04",
    "lynparza_bc_adjuvant_adjuvant_brca_mut_mono": "2022-06-30",
    "lynparza_paad_1l_maintenance_metastatic_brca_mut_mono": "2020-07-06",
    "lynparza_paad_1l_metastatic_brca_mut_mono": "2020-07-06",
    "lynparza_mcrpc_2l_metastatic_brca_mut_mono": "2020-11-05",
    "lynparza_oc_1l_maintenance_advanced_hrd_pos_bevacizumab": "2020-11-04",
    "lynparza_ec_1l_advanced": "2024-05-17",
}
LYNPARZA_PMDA: Dict[str, str] = {
    "lynparza_oc_1l_maintenance_brca_mut_mono": "2018-01-19",
    "lynparza_oc_1l_maintenance_advanced_hrd_pos_bevacizumab": "2021-05-27",
    "lynparza_bc_3l_metastatic_brca_mut_mono": "2018-07-02",
    "lynparza_bc_adjuvant_adjuvant_brca_mut_mono": "2022-08-24",
    "lynparza_paad_unresectable_brca_mut_mono": "2020-12-25",
    "lynparza_mcrpc_2l_metastatic_brca_mut_mono": "2020-12-25",
    "lynparza_ec_1l_maintenance_advanced_mono": "2024-02-09",
}
LYNPARZA_MHRA: Dict[str, str] = {
    "lynparza_oc_1l_advanced_brca_mut_mono": "2019-06-17",
    "lynparza_oc_1l_advanced_hrd_pos_bevacizumab": "2020-11-04",
    "lynparza_bc_adjuvant_adjuvant_brca_mut_mono": "2022-06-30",
    "lynparza_paad_1l_metastatic_brca_mut_mono": "2020-07-06",
    "lynparza_mcrpc_2l_metastatic_brca_mut_mono": "2020-11-05",
    "lynparza_ec_1l_maintenance_advanced_mono": "2024-05-17",
}
LYNPARZA_TGA: Dict[str, str] = {
    "lynparza_oc_1l_advanced_brca_mut_mono": "2020-02-20",
    "lynparza_bc_adjuvant_adjuvant_brca_mut_mono": "2022-08-15",
    "lynparza_paad_1l_maintenance_metastatic_brca_mut_mono": "2020-09-10",
    "lynparza_mcrpc_2l_metastatic_brca_mut_mono": "2021-02-25",
    "lynparza_oc_1l_maintenance_advanced_hrd_pos_bevacizumab": "2021-03-30",
}


# Lenvima (lenvatinib) 적응증별 승인일.
LENVIMA_FDA: Dict[str, str] = {
    "lenvima_dtc_metastatic_mono": "2015-02-13",                 # 초기 승인 (DTC RAI-refractory)
    "lenvima_hcc_1l_unresectable_mono": "2018-08-16",            # REFLECT
    "lenvima_rcc_2l_advanced": "2016-05-13",                     # w/ everolimus
    "lenvima_rcc_1l_advanced": "2021-08-10",                     # w/ pembrolizumab (KEYNOTE-581/CLEAR)
    "lenvima_ec_2l_advanced": "2021-07-21",                      # w/ pembrolizumab (KEYNOTE-775)
}
LENVIMA_EMA: Dict[str, str] = {
    "lenvima_dtc_2l_advanced_mono": "2015-05-28",                # 초기 승인
    "lenvima_hcc_1l_unresectable_mono": "2018-08-20",
    "lenvima_ec_2l_advanced": "2021-11-17",
}
LENVIMA_PMDA: Dict[str, str] = {
    "lenvima_thyc_unresectable_mono": "2015-03-26",
    "lenvima_thymic_unresectable_mono": "2015-03-26",
    "lenvima_hcc_1l_unresectable_mono": "2018-03-23",
    "lenvima_rcc_unresectable": "2019-11-27",
    "lenvima_ec_2l_advanced": "2022-03-28",
}
LENVIMA_MHRA: Dict[str, str] = {
    "lenvima_dtc": "2015-05-28",
    "lenvima_dtc_2l_metastatic_mono": "2015-05-28",
    "lenvima_hcc_1l_unresectable_mono": "2018-08-20",
    "lenvima_rcc_1l_advanced": "2021-11-17",
    "lenvima_ec_2l_advanced": "2021-11-17",
}
LENVIMA_TGA: Dict[str, str] = {
    "lenvima_dtc_metastatic_mono": "2015-11-20",
    "lenvima_hcc_1l_unresectable_mono": "2018-11-30",
    "lenvima_rcc_1l_advanced": "2021-12-02",
    "lenvima_rcc_2l_advanced": "2016-12-15",
    "lenvima_ec_2l_advanced": "2021-11-04",
}


def _merge(*dicts: Dict[str, str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for d in dicts:
        out.update(d)
    return out


# MFDS 는 ForeignApprovalAgent._build_mfds → apply_official_dates 가 변경이력 기반으로
# 공식 승인일을 자동 적용한다. 이 파일의 _MFDS dict 는 legacy 참고용이며,
# 수동 반영(apply)에서는 제외한다 (하드코딩 값이 변경이력과 불일치 시 date_source='mfds_official'
# 레코드를 덮어쓰는 회귀 위험 때문).
ALL_MAPS = {
    "FDA":  _merge(KEYTRUDA_FDA,  WELIREG_FDA,  LYNPARZA_FDA,  LENVIMA_FDA),
    "EMA":  _merge(KEYTRUDA_EMA,  WELIREG_EMA,  LYNPARZA_EMA,  LENVIMA_EMA),
    "PMDA": _merge(KEYTRUDA_PMDA, WELIREG_PMDA, LYNPARZA_PMDA, LENVIMA_PMDA),
    "MHRA": _merge(KEYTRUDA_MHRA, WELIREG_MHRA, LYNPARZA_MHRA, LENVIMA_MHRA),
    "TGA":  _merge(KEYTRUDA_TGA,  WELIREG_TGA,  LYNPARZA_TGA,  LENVIMA_TGA),
}


def apply(dry_run: bool = False) -> Dict[str, Dict[str, int]]:
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys = ON")
    c = con.cursor()
    report: Dict[str, Dict[str, int]] = {}
    for agency, mapping in ALL_MAPS.items():
        updated = skipped = missing = 0
        for ind_id, date in mapping.items():
            row = c.execute(
                "SELECT approval_date FROM indications_by_agency WHERE indication_id=? AND agency=?",
                (ind_id, agency),
            ).fetchone()
            if not row:
                missing += 1
                continue
            if row[0] == date:
                skipped += 1
                continue
            if not dry_run:
                c.execute(
                    "UPDATE indications_by_agency SET approval_date=? WHERE indication_id=? AND agency=?",
                    (date, ind_id, agency),
                )
            updated += 1
        report[agency] = {"updated": updated, "skipped": skipped, "missing": missing}
    if not dry_run:
        con.commit()
    con.close()
    return report


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    report = apply(dry_run=args.dry_run)
    for agency, stats in report.items():
        print(f"{agency}: {stats}")


if __name__ == "__main__":
    main()
