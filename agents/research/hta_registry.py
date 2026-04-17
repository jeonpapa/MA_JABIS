"""HTA 기관별 조사 규격 레지스트리

각 HTA 기관은 고유한 ID 체계·결정 등급·경제성평가 단위·PAS/RSA 용어를 가짐.
이 레지스트리는 기관별로 다음을 정의:
  - fields:           조회할 JSON 키 목록
  - narrative_fields: 서술형 (paraphrase 허용) 필드
  - prompt_template:  Python .format() 템플릿 — product/indication 만 치환
  - description:      대시보드 라벨용 설명

사용:
    from agents.research.hta_registry import HTA_REGISTRY, build_prompt
    spec = HTA_REGISTRY["nice"]
    prompt = build_prompt("nice", product="pembrolizumab", indication="1L NSCLC PD-L1 high")
"""

from __future__ import annotations

from typing import TypedDict


class HTASpec(TypedDict):
    code: str
    name: str
    country: str
    fields: list[str]
    narrative_fields: set[str]
    prompt_template: str
    description: str


COMMON_SYSTEM = (
    "당신은 제약 산업 HTA (Health Technology Assessment) 리서치 전문가입니다.\n"
    "답변은 반드시 JSON 블록만 출력하세요. 설명/주석/마크다운 헤더는 포함하지 마세요.\n"
    "확실하지 않은 필드는 null 로 표기하되, 절대 추측하지 마세요."
)


# ─────────────────────────────────────────────────────────────
# NICE (UK) — Technology Appraisal
# ─────────────────────────────────────────────────────────────
NICE_PROMPT = """Find NICE Technology Appraisal guidance for {product} in {indication}.

Return a single JSON object with exactly these keys:
{{
  "ta_number":          "e.g. TA531",
  "decision":           "Recommended | Not recommended | Optimised | Conditional",
  "decision_date":      "YYYY-MM",
  "indication_scope":   "브리핑용 1~2문장 요약 (제한조건·임계값 포함)",
  "icer_value_gbp":     "주요 ICER 수치 in £/QALY (없으면 null)",
  "pas_applied":        true | false | null,
  "rationale":          "NICE가 왜 이 결정을 내렸는지 100단어 이내로 요약",
  "source_url":         "NICE 공식 TA 페이지 URL"
}}

Return ONLY the JSON — no prose before or after."""


# ─────────────────────────────────────────────────────────────
# PBAC (Australia) — Pharmaceutical Benefits Advisory Committee
# ─────────────────────────────────────────────────────────────
PBAC_PROMPT = """Find PBAC (Pharmaceutical Benefits Advisory Committee, Australia) recommendation for {product} in {indication}.

Return a single JSON object with exactly these keys:
{{
  "recommendation":        "Recommended | Not recommended | Deferred | Reject",
  "meeting_date":          "YYYY-MM (decision meeting date)",
  "listing_type":          "New listing | Extension | Amendment | null",
  "indication_scope":      "PBS 등재 적응증 요약 (2문장 이내, 제한조건 포함)",
  "icer_value_aud":        "$/QALY if disclosed (usually redacted, then null)",
  "rsa_applied":           true | false | null,
  "rationale":             "PBAC가 이 결정을 내린 핵심 근거 100단어 이내 요약",
  "source_url":            "PBAC Public Summary Document URL (pbs.gov.au)"
}}

Return ONLY the JSON — no prose before or after."""


# ─────────────────────────────────────────────────────────────
# HAS (France) — Haute Autorité de Santé
# ─────────────────────────────────────────────────────────────
HAS_PROMPT = """Find HAS (Haute Autorité de Santé, France) / CT (Commission de la Transparence) opinion for {product} in {indication}.

Return a single JSON object with exactly these keys:
{{
  "smr_rating":         "Important | Modéré | Faible | Insuffisant | null",
  "asmr_rating":        "I (majeur) | II (important) | III (modéré) | IV (mineur) | V (absence) | null",
  "opinion_date":       "YYYY-MM",
  "indication_scope":   "HAS 평가 적응증 범위 요약 (2문장 이내)",
  "reimbursement_rate": "100% | 65% | 30% | 15% | null (ALD/일반 구분 포함 가능)",
  "rationale":          "SMR/ASMR 평가 핵심 근거 100단어 이내",
  "ceesp_applied":      true | false | null,
  "source_url":         "HAS 공식 평가 페이지 URL (has-sante.fr)"
}}

Return ONLY the JSON — no prose before or after."""


# ─────────────────────────────────────────────────────────────
# G-BA (Germany) — Gemeinsamer Bundesausschuss / IQWiG benefit rating
# ─────────────────────────────────────────────────────────────
GBA_PROMPT = """Find G-BA (Gemeinsamer Bundesausschuss, Germany) added benefit assessment for {product} in {indication}.

Return a single JSON object with exactly these keys:
{{
  "benefit_rating":      "major | considerable | minor | non-quantifiable | no added benefit | less benefit | null",
  "decision_date":       "YYYY-MM (G-BA Beschluss date)",
  "subpopulation_count": "평가된 환자 하위집단 수 (integer, 없으면 null)",
  "indication_scope":    "G-BA 평가 적응증 · 하위집단 요약 (2문장 이내)",
  "comparator":          "appropriate comparator therapy (zVT) 명시",
  "rationale":           "benefit 판정 핵심 근거 100단어 이내",
  "price_negotiation":   "GKV-SV 가격협상 상태 | 결과 (예: 가격 합의, 중재판정, 진행중, null)",
  "source_url":          "G-BA Beschluss PDF 또는 IQWiG 보고서 URL"
}}

Return ONLY the JSON — no prose before or after."""


# ─────────────────────────────────────────────────────────────
# 레지스트리
# ─────────────────────────────────────────────────────────────
HTA_REGISTRY: dict[str, HTASpec] = {
    "nice": {
        "code": "nice",
        "name": "NICE",
        "country": "UK",
        "fields": [
            "ta_number", "decision", "decision_date", "indication_scope",
            "icer_value_gbp", "pas_applied", "rationale", "source_url",
        ],
        "narrative_fields": {"indication_scope", "rationale"},
        "prompt_template": NICE_PROMPT,
        "description": "NICE Technology Appraisal (UK)",
    },
    "pbac": {
        "code": "pbac",
        "name": "PBAC",
        "country": "AU",
        "fields": [
            "recommendation", "meeting_date", "listing_type", "indication_scope",
            "icer_value_aud", "rsa_applied", "rationale", "source_url",
        ],
        "narrative_fields": {"indication_scope", "rationale"},
        "prompt_template": PBAC_PROMPT,
        "description": "Pharmaceutical Benefits Advisory Committee (Australia)",
    },
    "has": {
        "code": "has",
        "name": "HAS",
        "country": "FR",
        "fields": [
            "smr_rating", "asmr_rating", "opinion_date", "indication_scope",
            "reimbursement_rate", "rationale", "ceesp_applied", "source_url",
        ],
        "narrative_fields": {"indication_scope", "rationale"},
        "prompt_template": HAS_PROMPT,
        "description": "Haute Autorité de Santé / Commission de la Transparence (France)",
    },
    "gba": {
        "code": "gba",
        "name": "G-BA",
        "country": "DE",
        "fields": [
            "benefit_rating", "decision_date", "subpopulation_count", "indication_scope",
            "comparator", "rationale", "price_negotiation", "source_url",
        ],
        "narrative_fields": {"indication_scope", "rationale", "comparator"},
        "prompt_template": GBA_PROMPT,
        "description": "Gemeinsamer Bundesausschuss (Germany)",
    },
}


def build_prompt(agency_code: str, product: str, indication: str) -> str:
    """HTA 기관 코드·제품·적응증 으로 prompt 생성."""
    spec = HTA_REGISTRY[agency_code]
    return spec["prompt_template"].format(product=product, indication=indication)


def get_spec(agency_code: str) -> HTASpec:
    """HTA 기관 스펙 조회."""
    return HTA_REGISTRY[agency_code]


def list_agencies() -> list[str]:
    """지원 HTA 기관 코드 목록."""
    return list(HTA_REGISTRY.keys())
