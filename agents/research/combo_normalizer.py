"""
Combination label 정규화.

각 에이전시가 같은 요법을 다르게 표기하므로 canonical key로 변환해
병합 로직이 동일 combo를 인식하도록 한다.

정책: 표시용 원문(label)은 보존. 병합 시 normalize(label) → canonical key 만 사용.
"""

from __future__ import annotations

import re
from typing import Optional

_WS = re.compile(r"\s+")


def _clean(s: str) -> str:
    s = s.lower().strip()
    s = s.replace("–", "-").replace("—", "-")
    s = _WS.sub(" ", s)
    return s


_GROUPS: list[tuple[str, list[str]]] = [
    # 1) 멀티-페이즈 레짐 (neoadj → adj) — 긴 문구 먼저 매칭
    ("platinum-neoadj-adj",        ["platinum", "neoadj", "adjuvant"]),
    ("platinum-neoadj-adj",        ["platinum", "(neoadj"]),
    ("chemo-neoadj-adj",           ["chemotherapy", "neoadj", "adjuvant"]),
    ("chemo-neoadj-adj",           ["chemotherapy", "neoadj"]),
    ("rt-neoadj-adj",              ["radiotherapy", "neoadj"]),

    # 2) HNSCC peri-op w/ RT (single + RT 조합)
    ("rt+/-cisplatin",             ["radiotherapy", "cisplatin"]),
    ("rt+/-cisplatin",             ["radiation therapy", "cisplatin"]),

    # 3) Chemoradiotherapy (CC 1L KEYNOTE-A18)
    ("chemoradiotherapy",          ["chemoradiotherapy"]),

    # 4) Trastuzumab triple (GC HER2+ KEYNOTE-811)
    ("trastuzumab+fp+platinum",    ["trastuzumab", "fluoropyrimidine"]),
    ("trastuzumab+fp+platinum",    ["trastuzumab", "fluorouracil"]),
    ("trastuzumab",                ["trastuzumab"]),

    # 5) Chemo + bev (CC/OC — KEYNOTE-826)
    ("paclitaxel+/-bev",           ["paclitaxel", "bevacizumab"]),
    ("chemo+/-bev",                ["chemotherapy", "bevacizumab"]),

    # 6) 특정 combo agent (mono-agent combos)
    ("enfortumab-vedotin",         ["enfortumab"]),
    ("lenvatinib",                 ["lenvatinib"]),
    ("axitinib",                   ["axitinib"]),
    ("abiraterone",                ["abiraterone"]),
    ("everolimus",                 ["everolimus"]),
    ("durvalumab",                 ["durvalumab"]),
    ("bevacizumab-mono",           ["bevacizumab"]),
    ("pembro-combo",               ["pembrolizumab"]),

    # 7) Chemo family
    ("gem+cis",                    ["gemcitabine", "cisplatin"]),
    ("pemetrexed+platinum",        ["pemetrexed", "platinum"]),
    ("carbo+paclitaxel",           ["carboplatin", "paclitaxel"]),
    ("platinum+fluoropyrimidine",  ["fluoropyrimidine", "platinum"]),
    ("platinum+fluoropyrimidine",  ["platinum", "fluorouracil"]),
    ("platinum+fluoropyrimidine",  ["platinum", "5-fu"]),

    # 8) Mono (마지막 fallback)
    ("mono",                       ["monotherapy"]),
    ("mono",                       ["as a single agent"]),
    ("mono",                       ["single agent"]),

    # 9) Chemo 일반
    ("chemo",                      ["chemotherapy"]),
]


def normalize_combo(text: Optional[str]) -> str:
    """Return canonical combo key or '' when text is empty."""
    if not text:
        return ""
    s = _clean(text)
    for canonical, keys in _GROUPS:
        if all(k in s for k in keys):
            return canonical
    return s  # unknown → keep cleaned text as key (still distinct)


def canonicalize_labels(labels: list[str]) -> str:
    """Aggregate a row's multi-agency combo labels into one sorted canonical key."""
    keys = {normalize_combo(lbl) for lbl in labels if lbl}
    keys.discard("")
    return "|".join(sorted(keys))
