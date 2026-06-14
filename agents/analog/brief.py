"""약제 등재 아날로그 — LLM 전략 브리프 (유사사례 종합).

선택된 아날로그 사례 N건 → gpt-4o-mini 가 공통 등재 패턴·RSA 구조·소요일 분포·
허가↔급여 갭 경향·전략 시사점을 종합. **충실성 가드**: 실제 사례만 입력, "[사례 N] 인용" 강제,
외부 사실 금지. 캐시(사례 id 집합 + query). change-reason 캐시 선례.
"""
from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime
from pathlib import Path

from agents.analog.store import _connect, ensure_schema, get_detail

logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parents[2]

_SYSTEM = """당신은 한국 MSD Market Access 팀의 등재 전략 애널리스트다.
아래 제공된 **과거 약제 등재 사례 N건(번호 부여)** 만 근거로, 신약 등재 전략에 쓸
'아날로그 브리프'를 한국어 마크다운으로 작성하라.

구성(간결):
- **공통 등재 패턴**: 결과(통과/조건부/미설정)·등재트랙(CUA/WAP/PE_WAIVER)·RSA 유형 경향
- **허가 ↔ 급여 갭 경향**: 축소/구체화 등 어떤 변화가 흔했는지
- **재심의·소요**: 몇 차 만에 통과가 흔한지, 재심의 패턴
- **전략 시사점**: 위 패턴이 신약에 주는 함의 2~3개

원칙(엄수):
- **제공된 사례에 있는 사실만** 사용. 외부 지식·일반론·추측 금지.
- 각 주장 끝에 근거 사례 번호를 **[사례 3]** 형식으로 인용. 인용 없는 주장 금지.
- 사례에 없는 수치/약제/날짜 생성 금지. 데이터 부족하면 "사례 부족"이라 명시.
- 총 600자 이내. 마크다운 불릿."""


def _openai_key() -> str | None:
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        return key
    env = BASE_DIR / "config" / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("OPENAI_API_KEY="):
                return line.split("=", 1)[1].strip()
    return None


def _case_block(i: int, d: dict) -> str:
    f = lambda k: d.get(k) or "—"

    # 효과지표 요약
    eff_parts = []
    if d.get("os_months"):
        eff_parts.append(f"OS {d['os_months']}개월")
    if d.get("pfs_months"):
        eff_parts.append(f"PFS {d['pfs_months']}개월")
    if d.get("orr_pct"):
        eff_parts.append(f"ORR {d['orr_pct']}%")
    if d.get("key_hr"):
        eff_parts.append(f"HR {d['key_hr']}")
    eff_str = " / ".join(eff_parts) if eff_parts else "—"

    # 비교약제
    comps = d.get("comparator_drugs") or []
    comp_str = ", ".join(comps[:3]) if comps else "—"

    # 임상시험
    trials = d.get("clinical_trials") or []
    trials_str = ", ".join(trials[:3]) if trials else "—"

    # 정책 태그
    tags = d.get("policy_tags") or []
    tags_str = " | ".join(tags[:3]) if tags else "—"

    return (
        f"[사례 {i}] {f('brand_name')} ({f('generic_name_en') or f('generic_name')}) "
        f"· {f('session_date')} 약평위 {f('ordinal')}차\n"
        f"  질환: {f('disease_name_ko') or f('disease_name')} / {f('cancer_type')} / "
        f"{f('line_of_therapy')} / {f('treatment_setting')}\n"
        f"  결과: {f('review_result')} · 트랙: {f('reimbursement_track_ko')}\n"
        f"  효과지표: {eff_str} · 비교약제: {comp_str} · 임상: {trials_str}\n"
        f"  허가↔급여 갭: {f('coverage_gap_type')} ({(d.get('coverage_gap_evidence') or '')[:100]})\n"
        f"  정책: {f('approval_driver')} | {tags_str}\n"
        f"  재심의: {f('requeue_count')}회 · 소요: {f('sessions_to_pass')}일"
    )


def generate_brief(report_ids: list[int], query: str = "") -> dict:
    ensure_schema()
    cases = [get_detail(rid) for rid in report_ids[:12]]
    cases = [c for c in cases if c]
    if not cases:
        return {"brief": "", "error": "유효한 사례 없음", "cited_ids": []}

    ckey = hashlib.sha256(
        (query + "|" + ",".join(str(c["id"]) for c in cases)).encode()).hexdigest()
    with _connect() as conn:
        hit = conn.execute("SELECT brief FROM analog_brief_cache WHERE cache_key=?",
                           (ckey,)).fetchone()
    if hit:
        return {"brief": hit["brief"], "cached": True,
                "cited_ids": [c["id"] for c in cases]}

    key = _openai_key()
    if not key:
        return {"brief": "", "error": "OPENAI_API_KEY 없음 — 브리프 비활성", "cited_ids": []}

    blocks = "\n\n".join(_case_block(i + 1, c) for i, c in enumerate(cases))
    user = (f"[신약/검색 맥락]\n{query or '(미지정)'}\n\n[과거 등재 사례 {len(cases)}건]\n{blocks}")
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": _SYSTEM},
                      {"role": "user", "content": user}],
            temperature=0.1, max_tokens=900)
        brief = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.warning("[analog.brief] LLM 실패: %s", e)
        return {"brief": "", "error": f"LLM 실패: {e}", "cited_ids": []}

    with _connect() as conn:
        conn.execute("INSERT OR REPLACE INTO analog_brief_cache VALUES (?,?,?)",
                     (ckey, brief, datetime.now().isoformat(timespec="seconds")))
        conn.commit()
    return {"brief": brief, "cached": False,
            "cases": [{"id": c["id"], "brand_name": c["brand_name"],
                       "session_date": c["session_date"]} for c in cases],
            "cited_ids": [c["id"] for c in cases]}
