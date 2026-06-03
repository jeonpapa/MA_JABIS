"""KR-RULE 사실 base 를 MI agent prompt 에 동적 주입.

`~/.claude/skills/korea-drug-pricing-system/references/` 의 markdown 파일을 로드해
LLM 이 한국 약가 micro-rule (KR-RULE-001~037) 을 인용할 수 있게 한다.

문제 (2026-04-25 발견):
  자누비아 100mg 2024.10.01 -23.48% 인하는 KR-RULE-009 의 "Year 1 이후 단계 인하"
  (누적 53.55% 산정률) 인데 LLM 이 사실 모르고 PVA/실거래가 같은 일반론으로 추정.

해결:
  - quick_reference.md (37 rule 한 줄 요약) — 모든 호출에 inject (cheap, broad)
  - pricing_rules.md / reform_2026.md — 약가 인하 분석 시 추가 inject
  - drug-context-aware: MSD 자산 (키트루다·웰리렉 등) 명시 시 msd_assets.md 추가
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_SKILL_DIR = Path.home() / ".claude" / "skills" / "korea-drug-pricing-system" / "references"

# 캐시 — 매 prompt 마다 file IO 회피
_cache: dict[str, str] = {}


def _read(name: str) -> str:
    if name in _cache:
        return _cache[name]
    path = _SKILL_DIR / name
    if not path.exists():
        logger.warning("[skill_kb] %s 미존재 — KR-RULE prompt 주입 skip", path)
        _cache[name] = ""
        return ""
    try:
        text = path.read_text(encoding="utf-8")
        _cache[name] = text
        return text
    except Exception as e:
        logger.warning("[skill_kb] %s 읽기 실패: %s", name, e)
        _cache[name] = ""
        return ""


# 트리거 키워드별 inject 매핑 — drug name / context 기반 동적 선택
_MSD_ASSETS = ("키트루다", "웰리렉", "브리디온", "저박사", "에멘드",
               "keytruda", "welireg", "bridion", "zerbaxa", "emend",
               "pembrolizumab", "belzutifan", "sugammadex")

_LOE_KEYWORDS = ("특허", "제네릭", "loe", "patent", "특허만료")
_REFORM_KEYWORDS = ("2026 개편", "floor rule", "그룹 1", "그룹 2", "11년", "45%", "53.55%")


def build_kr_rule_context(drug_name: str = "", reason_hint: str = "") -> str:
    """drug + context 기반 KR-RULE 인용 base 구성.

    LLM 이 system prompt 외에 **사용자 요청과 무관하게** 한국 약가 제도
    micro-rule 을 인용할 수 있도록 reference text 를 직조립.

    Args:
        drug_name: 약제명 (한글 또는 영문 brand). MSD 자산이면 msd_assets 추가.
        reason_hint: 분석 hint (delta_pct·약가 history 등). LOE 패턴 의심 시 pricing_rules 추가.

    Returns: 0~12K characters of KR-RULE context (system prompt 에 그대로 inject 가능).
    """
    sections: list[str] = []

    # 1) quick_reference — 항상 주입 (37 rule 한 줄 요약, ~5K chars)
    qr = _read("quick_reference.md")
    if qr:
        sections.append("## 한국 약가 제도 KR-RULE Quick Reference\n\n" + qr)

    # 2) pricing_rules — LOE/특허/제네릭 키워드 등장 시 추가 (~10K chars)
    blob = (drug_name + " " + reason_hint).lower()
    if any(k in blob for k in _LOE_KEYWORDS):
        pr = _read("pricing_rules.md")
        if pr:
            sections.append("## Pricing Rules 상세 (KR-RULE-001~009)\n\n" + pr)

    # 3) reform_2026 — 2026 개편안 키워드 시 추가
    if any(k in blob for k in _REFORM_KEYWORDS):
        rf = _read("reform_2026.md")
        if rf:
            sections.append("## 2026 약가제도 개편안 (KR-RULE-031~037)\n\n" + rf)

    # 4) MSD 자산 — 명시적 자산명 등장 시 deep dive 추가
    if any(k in blob for k in _MSD_ASSETS):
        ma = _read("msd_assets.md")
        if ma:
            sections.append("## MSD Korea 자산별 사실 (Tier 1)\n\n" + ma)

    # 5) severe_violations — 항상 inject (자가 검증 체크리스트, ~9K chars)
    sv = _read("severe_violations.md")
    if sv:
        sections.append("## 자가 검증 체크리스트 (출력 직전 8개 self-check)\n\n" + sv)

    if not sections:
        return ""

    return (
        "=== 한국 약가 제도 사실 base (KR-RULE 인용 시 번호 명시 필수) ===\n\n"
        + "\n\n".join(sections)
        + "\n\n=== KR-RULE 사실 base 끝 ==="
    )


def get_quick_reference() -> str:
    """37 rule 한 줄 요약만 반환 (lightweight 호출)."""
    return _read("quick_reference.md")
