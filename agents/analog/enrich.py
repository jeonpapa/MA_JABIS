"""약제 등재 아날로그 — enrich (LLM 구조화 + MFDS API + 갭 분류 + trajectory).

① enrich_disease()   — claude-sonnet-4-6: mfds_effect_text → 질환 분류 JSON
② enrich_efficacy()  — claude-sonnet-4-6: body_text → OS/PFS/비교약제 JSON
③ enrich_policy()    — claude-sonnet-4-6: decision_reason → 정책 의도 JSON
④ enrich_mfds()      — MFDS API: permit_date 보완 (PDF에서 못 얻은 경우)
⑤ enrich_gap()       — claude-sonnet-4-6: 허가↔급여 갭 분류
⑥ enrich_trajectory()— 코퍼스 내장: 약제별 재심의 이력 통합

모든 LLM 결과: analog_llm_cache 테이블 file_hash 기반 캐시 → 재실행 비용 0.

실행:
  python -m agents.analog.enrich disease [limit]
  python -m agents.analog.enrich efficacy [limit]
  python -m agents.analog.enrich policy [limit]
  python -m agents.analog.enrich mfds [limit]
  python -m agents.analog.enrich gap [limit]
  python -m agents.analog.enrich trajectory
  python -m agents.analog.enrich all [limit]
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime
from pathlib import Path

from agents.analog.store import _connect, ensure_schema

logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parents[2]

_PASS_STATES = {"APPROVED", "CONDITIONAL_APPROVED", "APPROVED_WITH_POSTMARKET"}
_GAP_TYPES = {"축소", "확대", "구체화", "동일", "비교불가"}


def _norm_date(s: str | None) -> str | None:
    """'YYYY.MM.DD' / 'YYYY/MM/DD' → 'YYYY-MM-DD' (isoformat 파싱용)."""
    if not s:
        return None
    return s.strip().replace(".", "-").replace("/", "-")


def _clean_brand_for_price(brand: str | None) -> str:
    """약가 매칭용 브랜드 정제: '평가결과' 접두 · '_2024년 제N차' 꼬리 · 함량/회사 제거.

    리포트 brand_name 에 차수 suffix('일라리스주사액_2024년 제2, 4차') 가 남아 prefix range
    매칭이 빗나가는 것을 방지(→ product_name_kr 'X(...)' 가 'X_2024…' 보다 앞서 range 밖).
    """
    import re as _re
    from agents.analog.pdf_parser import _clean_brand, _normalize
    b = _normalize(brand or "")
    b = _re.sub(r"^평가결과[_\s]+", "", b)
    b = _re.sub(r"_?\d{4}년.*$", "", b)        # 차수 꼬리
    b = _clean_brand(b)
    b = _re.sub(r"[,(\[].*$", "", b)            # 잔여 함량/회사/성분 괄호
    # 강건화: 한글 브랜드 코어는 보통 첫 숫자 앞까지 (예 '콰지바주4.5mg-mL'→'콰지바주',
    # '발베사정3,4,5밀리그램한국얀센'→'발베사정'). product_name_kr 도 같은 코어로 시작하므로
    # prefix range 가 더 안정적으로 매칭(하이픈/슬래시 함량표기 차이 흡수).
    core = _re.split(r"\d", b)[0].strip()
    if len(core) >= 2:
        b = core
    return b.strip()


# ── API 키 로딩 ───────────────────────────────────────────────────────────────

def _load_env() -> None:
    env = BASE_DIR / "config" / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


def _anthropic_key() -> str | None:
    _load_env()
    return os.environ.get("ANTHROPIC_API_KEY")


def _openai_key() -> str | None:
    _load_env()
    return os.environ.get("OPENAI_API_KEY")


# ── LLM 호출 — claude-sonnet-4-6 ─────────────────────────────────────────────

def _parse_json_text(text: str) -> dict | None:
    import re
    m = re.search(r'\{.*\}', text, re.DOTALL)
    try:
        return json.loads(m.group(0) if m else text)
    except Exception:
        return None


def _call_openai(system: str, user: str, max_tokens: int = 1024) -> dict | None:
    """gpt-4o 호출 (ANTHROPIC_API_KEY 부재 시 fallback). JSON 모드."""
    key = _openai_key()
    if not key:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key)
        resp = client.chat.completions.create(
            model="gpt-4o",
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return _parse_json_text(resp.choices[0].message.content or "")
    except Exception as e:
        logger.warning("[analog.enrich] OpenAI 호출 실패: %s", e)
        return None


def _call_claude(system: str, user: str, max_tokens: int = 1024) -> dict | None:
    """LLM 호출. ANTHROPIC_API_KEY 있으면 claude-sonnet-4-6, 없으면 gpt-4o.

    enrich 는 1회 배치 — 결과를 DB 에 영구 저장(file_hash 캐시). 배포 런타임은
    DB 만 읽으므로 LLM 키 불필요. 캐시는 모델 무관(file_hash) 이라 추후 claude
    키 추가 시 기존 gpt-4o 결과는 보존되고 신규 파일만 claude 사용.
    """
    key = _anthropic_key()
    if not key:
        return _call_openai(system, user, max_tokens)
    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=key)
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = resp.content[0].text if resp.content else ""
        return _parse_json_text(text)
    except Exception as e:
        logger.warning("[analog.enrich] Claude 호출 실패: %s — OpenAI fallback", e)
        return _call_openai(system, user, max_tokens)


def _get_llm_cache(cache_key: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT result_json FROM analog_llm_cache WHERE cache_key=?", (cache_key,)
        ).fetchone()
    if row:
        try:
            return json.loads(row["result_json"])
        except Exception:
            return None
    return None


def _set_llm_cache(cache_key: str, enrich_type: str, result: dict) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO analog_llm_cache VALUES (?,?,?,?)",
            (cache_key, enrich_type, json.dumps(result, ensure_ascii=False),
             datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()


# ── ① 질환 분류 ───────────────────────────────────────────────────────────────

_DISEASE_SYSTEM = """당신은 한국 HIRA 약제 등재 전문 의학 애널리스트다.
주어진 식약처 허가 적응증(효능효과) 원문을 분석해 아래 JSON 스키마로 정확히 반환하라.
**반드시 JSON만** 출력. 외부 지식 추론 금지, 원문 근거만.

{
  "disease_category": "항암" | "비항암" | "희귀",
  "disease_category_detail": "혈액종양" | "고형암" | "자가면역" | "대사질환" | "희귀" | "기타",
  "disease_name_ko": "비소세포폐암, 악성 흑색종",
  "disease_name_en": "NSCLC, Melanoma",
  "cancer_type": "NSCLC" | "DLBCL" | "ALL" | "BC" | "AML" | "흑색종" | null,
  "line_of_therapy": "1차" | "2차" | "2차이상" | "3차이상" | "제한없음",
  "biomarker": "PD-L1≥50%" | "EGFR-" | null,
  "treatment_setting": "전이성" | "수술불가" | "재발/불응성" | "보조요법" | "모든 병기"
}

cancer_type 예시: NSCLC/DLBCL/ALL/BC(유방암)/AML/CLL/MM(다발골수종)/CRC/GC/HCC/RCC/흑색종
disease_category: 항암(모든 암), 희귀(희귀질환법 지정 또는 orphan drug), 비항암(그 외)"""


def enrich_disease(limit: int = None) -> dict:
    """mfds_effect_text → 질환 분류(disease_category 등) 일괄 업데이트."""
    ensure_schema()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, file_hash, mfds_effect_text FROM analog_reports "
            "WHERE mfds_effect_text IS NOT NULL AND mfds_effect_text != '' "
            "AND disease_category_detail IS NULL "
            "ORDER BY session_date DESC"
        ).fetchall()
    items = list(rows)
    if limit:
        items = items[:limit]

    processed = cached = skipped = 0
    for r in items:
        fh = r["file_hash"]
        cache_key = f"disease:{fh}"
        hit = _get_llm_cache(cache_key)
        if hit:
            cached += 1
        else:
            result = _call_claude(_DISEASE_SYSTEM, r["mfds_effect_text"][:4000], max_tokens=512)
            if not result:
                skipped += 1
                continue
            _set_llm_cache(cache_key, "disease", result)
            processed += 1

        data = hit or result
        with _connect() as conn:
            conn.execute(
                """UPDATE analog_reports SET
                   disease_category=?, disease_category_detail=?,
                   disease_name_ko=?, disease_name_en=?,
                   cancer_type=?, line_of_therapy=?,
                   biomarker=?, treatment_setting=?,
                   enriched_at=?
                   WHERE id=?""",
                (data.get("disease_category"),
                 data.get("disease_category_detail"),
                 data.get("disease_name_ko"),
                 data.get("disease_name_en"),
                 data.get("cancer_type"),
                 data.get("line_of_therapy"),
                 data.get("biomarker"),
                 data.get("treatment_setting"),
                 datetime.now().isoformat(timespec="seconds"),
                 r["id"]),
            )
            conn.commit()

    return {"total": len(items), "processed": processed, "cached": cached, "skipped": skipped}


# ── ② 효과 지표 구조화 ────────────────────────────────────────────────────────

_EFFICACY_SYSTEM = """당신은 HIRA 임상·경제성 평가 전문 의학 애널리스트다.
'나. 평가 내용' 텍스트에서 주요 효과 지표와 비교약제를 추출해 아래 JSON 스키마로 반환하라.
**반드시 JSON만** 출력. 텍스트에 없는 수치 생성 금지.

{
  "efficacy_endpoints": [
    {
      "trial_name": "KEYNOTE-010",
      "endpoint": "OS" | "PFS" | "ORR" | "CR" | "EFS" | "DOR" | "MACE" | "LDL-C" | "HbA1c" | "EASI75" | "기타",
      "endpoint_ko": "전체생존기간",
      "endpoint_detail": null,
      "value": 14.9,
      "value_unit": "개월" | "%" | "%p" | null,
      "comparator_name": "docetaxel",
      "comparator_value": 8.2,
      "hr": 0.54,
      "ci_lower": null,
      "ci_upper": null,
      "p_value": "0.0002",
      "n": 139,
      "note": null
    }
  ],
  "primary_endpoint": "OS",
  "comparator_drugs": ["docetaxel", "pemetrexed"]
}

endpoint 가이드:
- 항암: OS(전체생존기간)/PFS(무진행생존기간)/ORR(객관적반응률)/CR/EFS/DOR
- 심혈관: MACE/LDL-C변화율
- 당뇨: HbA1c변화량
- 피부/자가면역: EASI75/IGA/ACR20/DAS28
- 텍스트에 없으면 endpoint_detail 에 원문 표현 그대로
comparator_drugs: 비용효과 분석에서 비교약제로 선정된 약제명 목록"""


def enrich_efficacy(limit: int = None) -> dict:
    """body_text → efficacy_data(JSON), os_months, pfs_months, orr_pct, key_hr."""
    ensure_schema()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, file_hash, body_text FROM analog_reports "
            "WHERE body_text IS NOT NULL AND body_text != '' "
            "AND efficacy_data IS NULL AND pdf_extractable = 1 "
            "ORDER BY session_date DESC"
        ).fetchall()
    items = list(rows)
    if limit:
        items = items[:limit]

    processed = cached = skipped = 0
    for r in items:
        fh = r["file_hash"]
        cache_key = f"efficacy:{fh}"
        hit = _get_llm_cache(cache_key)
        if hit:
            cached += 1
        else:
            # 나. 평가내용 상위 6000자만
            body = (r["body_text"] or "")[:6000]
            result = _call_claude(_EFFICACY_SYSTEM, body, max_tokens=1500)
            if not result:
                skipped += 1
                continue
            _set_llm_cache(cache_key, "efficacy", result)
            processed += 1

        data = hit or result
        endpoints = data.get("efficacy_endpoints") or []
        os_m = pfs_m = orr = hr = None
        for ep in endpoints:
            ept = ep.get("endpoint", "")
            val = ep.get("value")
            if ept == "OS" and val and os_m is None:
                try:
                    os_m = float(val)
                except (TypeError, ValueError):
                    pass
            elif ept == "PFS" and val and pfs_m is None:
                try:
                    pfs_m = float(val)
                except (TypeError, ValueError):
                    pass
            elif ept == "ORR" and val and orr is None:
                try:
                    orr = float(val)
                except (TypeError, ValueError):
                    pass
            if ep.get("hr") and hr is None:
                try:
                    hr = float(ep["hr"])
                except (TypeError, ValueError):
                    pass

        comparators = data.get("comparator_drugs") or []
        with _connect() as conn:
            conn.execute(
                """UPDATE analog_reports SET
                   efficacy_data=?, primary_endpoint=?,
                   os_months=?, pfs_months=?, orr_pct=?, key_hr=?,
                   comparator_drugs=?, enriched_at=?
                   WHERE id=?""",
                (json.dumps(endpoints, ensure_ascii=False),
                 data.get("primary_endpoint"),
                 os_m, pfs_m, orr, hr,
                 json.dumps(comparators, ensure_ascii=False),
                 datetime.now().isoformat(timespec="seconds"),
                 r["id"]),
            )
            conn.commit()

    return {"total": len(items), "processed": processed, "cached": cached, "skipped": skipped}


# ── ③ 정책 의도 ───────────────────────────────────────────────────────────────

_POLICY_SYSTEM = """당신은 한국 HIRA 약가 정책 전문 애널리스트다.
'가. 평가 결과' 텍스트를 분석해 HIRA 의 핵심 정책 의도를 아래 JSON 으로 반환하라.
**반드시 JSON만** 출력.

{
  "policy_intent_summary": "1-2문장: 왜 이 결정을 내렸는지 핵심 이유",
  "policy_tags": ["중증질환 보장성", "혁신성 인정", "단일군 임상 수락", "재정위험 관리", "국산신약 지원"],
  "approval_driver": "RSA" | "PE_WAIVER" | "COST_EFFECTIVE" | "POLICY_PRIORITY" | "REJECTED_COST",
  "rejection_reason": null,
  "future_conditions": null
}

policy_tags 후보: 중증질환 보장성/혁신성 인정/단일군 임상 수락/재정위험 관리/국산신약 지원/
  희귀질환 지원/소아·청소년/보장성 강화 정책/RSA 조건부/비용효과 불인정
approval_driver: RSA(위험분담 없이 불가), PE_WAIVER(경제성평가 생략 수락), COST_EFFECTIVE(비용효과 입증),
  POLICY_PRIORITY(정책적 우선 지원), REJECTED_COST(비용효과 불충족 거절)
rejection_reason: 비급여 결정 시 사유 1문장, 급여 시 null
future_conditions: 사후관리·향후 재평가 조건 있으면 1문장"""


def enrich_policy(limit: int = None) -> dict:
    """decision_reason → policy_intent_summary, policy_tags, approval_driver."""
    ensure_schema()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, file_hash, decision_reason, body_text FROM analog_reports "
            "WHERE (decision_reason IS NOT NULL OR body_text IS NOT NULL) "
            "AND policy_intent_summary IS NULL AND pdf_extractable = 1 "
            "ORDER BY session_date DESC"
        ).fetchall()
    items = list(rows)
    if limit:
        items = items[:limit]

    processed = cached = skipped = 0
    for r in items:
        fh = r["file_hash"]
        cache_key = f"policy:{fh}"
        hit = _get_llm_cache(cache_key)
        if hit:
            cached += 1
        else:
            text = ((r["decision_reason"] or "")[:4000] + "\n\n" +
                    (r["body_text"] or "")[:2000])
            result = _call_claude(_POLICY_SYSTEM, text, max_tokens=600)
            if not result:
                skipped += 1
                continue
            _set_llm_cache(cache_key, "policy", result)
            processed += 1

        data = hit or result
        tags = data.get("policy_tags") or []
        with _connect() as conn:
            conn.execute(
                """UPDATE analog_reports SET
                   policy_intent_summary=?, policy_tags=?,
                   approval_driver=?, future_conditions=?,
                   enriched_at=?
                   WHERE id=?""",
                (data.get("policy_intent_summary"),
                 json.dumps(tags, ensure_ascii=False),
                 data.get("approval_driver"),
                 data.get("future_conditions"),
                 datetime.now().isoformat(timespec="seconds"),
                 r["id"]),
            )
            conn.commit()

    return {"total": len(items), "processed": processed, "cached": cached, "skipped": skipped}


# ── ④ MFDS API (허가일 보완) ──────────────────────────────────────────────────

def enrich_mfds(limit: int = None) -> dict:
    """brand_name 별 식약처 허가일 조회 → mfds_permit_date 보완."""
    ensure_schema()
    from agents.scrapers.kr_mfds_permit import lookup_permit

    with _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT brand_name, generic_name FROM analog_reports "
            "WHERE brand_name IS NOT NULL AND brand_name != '' "
            "AND mfds_permit_date IS NULL"
        ).fetchall()
    drugs = [(r["brand_name"], r["generic_name"]) for r in rows]
    if limit:
        drugs = drugs[:limit]

    found = miss = 0
    for brand, generic in drugs:
        try:
            res = lookup_permit(brand, ingredient=generic)
        except Exception as e:
            logger.warning("[analog.enrich] MFDS %s 실패: %s", brand, e)
            res = None
        permit_date = (res or {}).get("permit_date")
        effect = (res or {}).get("effect_text")
        if permit_date:
            found += 1
        else:
            miss += 1
        with _connect() as conn:
            # effect_text 는 PDF 추출분 우선 (더 정확), 없는 경우만 보완
            conn.execute(
                "UPDATE analog_reports SET mfds_permit_date=?, "
                "mfds_effect_text=COALESCE(NULLIF(mfds_effect_text,''), ?) "
                "WHERE brand_name=? AND mfds_permit_date IS NULL",
                (permit_date, effect, brand),
            )
            conn.commit()
    return {"drugs": len(drugs), "found": found, "miss": miss}


# ── ⑤ 허가↔급여 갭 분류 ──────────────────────────────────────────────────────

_GAP_SYSTEM = """당신은 한국 약가·급여(HIRA) 전문 애널리스트다.
식약처 허가 적응증(효능효과)과 급여 승인 적응증을 비교해 **본질적 범위 변화**로 분류하라.

분류: 축소/확대/구체화/동일/비교불가
반드시 JSON만: {"coverage_gap_type":"...","evidence":"허가:'...' / 급여:'...' → 1문장"}

- 축소: 급여 범위가 허가의 일부 (적응증 수 감소 또는 환자군 축소)
- 확대: 급여가 허가보다 넓음 (드묾)
- 구체화: 동일 적응증이나 급여에서 조건(바이오마커·치료차수·병용)이 더 구체화
- 동일: 실질 범위 동일
- 비교불가: 한쪽 원문 부족으로 판단 불가"""


def _classify_gap(permit_text: str, reimb_text: str) -> dict | None:
    return _call_claude(
        _GAP_SYSTEM,
        f"[허가 적응증]\n{permit_text[:4000]}\n\n[급여 승인 적응증]\n{reimb_text[:2000]}",
        max_tokens=400,
    )


def enrich_gap(limit: int = None) -> dict:
    """허가 적응증·급여 적응증 → 갭 분류. analog_gap_cache 활용."""
    ensure_schema()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT id, brand_name, generic_name, file_hash, "
            "mfds_effect_text, disease_name_ko, disease_name, body_text, decision_reason "
            "FROM analog_reports WHERE mfds_effect_text IS NOT NULL "
            "AND mfds_effect_text != '' AND coverage_gap_type IS NULL"
        ).fetchall()
    items = list(rows)
    if limit:
        items = items[:limit]

    classified = cached = skipped = 0
    for r in items:
        permit_text = r["mfds_effect_text"]
        reimb_text = " / ".join(filter(None, [
            r["disease_name_ko"] or r["disease_name"],
            (r["decision_reason"] or "")[:1500],
        ]))
        if not reimb_text.strip():
            skipped += 1
            continue

        ckey = hashlib.sha1(
            f"{r['brand_name']}|{hashlib.sha1((permit_text or '').encode()).hexdigest()[:12]}"
            f"|{hashlib.sha1(reimb_text.encode()).hexdigest()[:12]}".encode()
        ).hexdigest()

        with _connect() as conn:
            hit = conn.execute(
                "SELECT gap_type, evidence FROM analog_gap_cache WHERE cache_key=?", (ckey,)
            ).fetchone()

        if hit:
            gap = {"coverage_gap_type": hit["gap_type"], "evidence": hit["evidence"]}
            cached += 1
        else:
            gap = _classify_gap(permit_text, reimb_text)
            if not gap or gap.get("coverage_gap_type") not in _GAP_TYPES:
                skipped += 1
                continue
            with _connect() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO analog_gap_cache VALUES (?,?,?,?)",
                    (ckey, gap["coverage_gap_type"], gap.get("evidence", ""),
                     datetime.now().isoformat(timespec="seconds")),
                )
                conn.commit()
            classified += 1

        with _connect() as conn:
            conn.execute(
                "UPDATE analog_reports SET coverage_gap_type=?, coverage_gap_evidence=?, "
                "enriched_at=? WHERE id=?",
                (gap["coverage_gap_type"], (gap.get("evidence") or "")[:1000],
                 datetime.now().isoformat(timespec="seconds"), r["id"]),
            )
            conn.commit()

    return {"total": len(items), "classified": classified, "cached": cached, "skipped": skipped}


# ── ⑥ 재심의 trajectory ───────────────────────────────────────────────────────

def enrich_trajectory() -> dict:
    """약제(generic_name_en 우선)별 위원회 이력 → 재심의 횟수·통과 차수."""
    ensure_schema()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, generic_name_en, generic_name, brand_name, "
            "session_date, review_result, ordinal, mfds_permit_date "
            "FROM analog_reports WHERE file_name LIKE '%.pdf'"
        ).fetchall()

    groups: dict[str, list] = {}
    for r in rows:
        key = (
            (r["generic_name_en"] or r["generic_name"] or r["brand_name"] or "")
            .strip().lower()
        )
        if not key:
            continue
        groups.setdefault(key, []).append(r)

    updated = 0
    with _connect() as conn:
        for key, items in groups.items():
            items.sort(key=lambda x: (x["session_date"] or ""))
            first_date = items[0]["session_date"]
            requeue = 0
            pass_date = None
            for it in items:
                rr = (it["review_result"] or "").upper()
                if rr in _PASS_STATES and pass_date is None:
                    pass_date = it["session_date"]
                    break
                if rr == "REJECTED":
                    requeue += 1
            n_sessions = next(
                (i + 1 for i, it in enumerate(items)
                 if (it["review_result"] or "").upper() in _PASS_STATES),
                None,
            )
            span_days = None
            if first_date and pass_date:
                try:
                    span_days = (
                        datetime.fromisoformat(pass_date)
                        - datetime.fromisoformat(first_date)
                    ).days
                except ValueError:
                    span_days = None
            # 그룹 대표 허가일 (가장 이른 mfds_permit_date) — 점 표기 정규화
            permit_dates = sorted(
                _norm_date(it["mfds_permit_date"]) for it in items
                if it["mfds_permit_date"]
            )
            group_permit = permit_dates[0] if permit_dates else None
            reimb_date = _norm_date(
                pass_date or (items[-1]["session_date"] if items else None)
            )
            lag_days = None
            if group_permit and reimb_date:
                try:
                    lag_days = (
                        datetime.fromisoformat(reimb_date)
                        - datetime.fromisoformat(group_permit)
                    ).days
                    if lag_days < 0:
                        lag_days = None
                except ValueError:
                    lag_days = None
            for it in items:
                conn.execute(
                    "UPDATE analog_reports SET requeue_count=?, first_session_date=?, "
                    "pass_session_date=?, sessions_to_pass=?, "
                    "lag_days_approval_to_reimb=? WHERE id=?",
                    (requeue, first_date, pass_date,
                     span_days if span_days is not None else n_sessions,
                     lag_days, it["id"]),
                )
                updated += 1
        conn.commit()

    return {"groups": len(groups), "rows_updated": updated}


# ── ⑦ 위원회 부가정보 (암질심 일정·의견조회 학회) ────────────────────────────────

import re as _re

_RE_SOCIETY = _re.compile(r'[가-힣A-Za-z·]{2,28}학회')
# 의견조회/검토 맥락에서만 학회 추출 (단순 본문 언급 과다수집 방지용 컨텍스트 키워드)
_SOCIETY_CONTEXT = ("의견", "검토", "자문", "조회", "학회")


def enrich_committee(limit: int = None) -> dict:
    """body_text/decision_reason 에서 위원회·등재 부가정보 backfill (네트워크 불필요).

    - amjilsim_history: 본문 명시 암질환심의위/급여기준소위 날짜 (regex)
    - consulted_societies: 의견조회·검토 학회 목록 (regex)
    - foreign_listing_count/basis: 제외국(A7/A8) 등재국가수 (숫자형+국가명 나열형)
    - rsa_types: 위험분담 세부 조건 유형 (복수)
    이미 적재된 본문에서 재추출 — PDF 재파싱 없이 정규식 개선분 반영."""
    ensure_schema()
    from agents.analog.pdf_parser import (
        _extract_amjilsim_history, _extract_foreign_listing,
        _extract_rsa_conditions, determine_track_ko,
    )

    _RE_CE_ACCEPT = _re.compile(r'비용\s*효과(?:성|적)')

    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, body_text, decision_reason, amjilsim_history, "
            "foreign_listing_count, has_rsa, pe_waiver, has_postmarket_condition, "
            "session_year, approval_driver, reimbursement_track_ko, "
            "policy_tags, review_result "
            "FROM analog_reports WHERE file_name LIKE '%.pdf' AND pdf_extractable=1 "
            "AND (body_text IS NOT NULL OR decision_reason IS NOT NULL)"
        ).fetchall()
    items = list(rows)
    if limit:
        items = items[:limit]

    amjilsim_set = soc_set = foreign_set = rsa_set = track_set = tag_fix = 0
    with _connect() as conn:
        for r in items:
            text = (r["body_text"] or "") + "\n" + (r["decision_reason"] or "")
            sets, vals = [], []

            # 사전심의 위원회(암질심/급여기준소위) — 비어있거나 committee 태그
            # 미부여(구버전 {date}만) 인 경우 본문에서 재추출. 단, join 으로
            # 채워진 단일 항목({date,committee})은 보존 (본문 추출이 더 적으면 덮지 않음).
            cur_amj = r["amjilsim_history"]
            needs_amj = (not cur_amj or cur_amj in ("", "[]")
                         or '"committee"' not in (cur_amj or ""))
            if needs_amj:
                hist = _extract_amjilsim_history(text)
                if hist:
                    sets.append("amjilsim_history=?")
                    vals.append(json.dumps(hist, ensure_ascii=False))
                    amjilsim_set += 1

            # 의견조회 학회
            socs = list(dict.fromkeys(_RE_SOCIETY.findall(text)))
            if socs:
                sets.append("consulted_societies=?")
                vals.append(json.dumps(socs, ensure_ascii=False))
                soc_set += 1

            # 제외국 등재국가수 (기존 미수집 시 보완) — 국가 나열형 섹션 포함
            if r["foreign_listing_count"] is None:
                fc, fb = _extract_foreign_listing(text, r["session_year"])
                if fc is not None:
                    sets.append("foreign_listing_count=?")
                    vals.append(fc)
                    sets.append("foreign_listing_basis=?")
                    vals.append(fb)
                    foreign_set += 1

            # 위험분담 세부 조건 (has_rsa 인 경우)
            if r["has_rsa"]:
                conds = _extract_rsa_conditions(text)
                if conds:
                    sets.append("rsa_types=?")
                    vals.append(json.dumps(conds, ensure_ascii=False))
                    rsa_set += 1

            # 등재트랙 보정 (issue 1): '기타' 인데 비용효과 입증 케이스면 재산정
            cur_track = r["reimbursement_track_ko"]
            ce_evidence = (r["approval_driver"] == "COST_EFFECTIVE"
                           or _RE_CE_ACCEPT.search(text))
            if (not cur_track or cur_track == "기타") and ce_evidence:
                new_track = determine_track_ko(
                    text, bool(r["has_rsa"]), bool(r["pe_waiver"]),
                    bool(r["has_postmarket_condition"]),
                )
                if new_track and new_track != "기타" and new_track != cur_track:
                    sets.append("reimbursement_track_ko=?")
                    vals.append(new_track)
                    track_set += 1

            # 정책태그 모순 교정 (issue 2): APPROVED + 비용효과 수용인데
            # '비용효과 불인정' 태그가 붙은 경우 제거 (LLM 오라벨)
            raw_tags = r["policy_tags"]
            if raw_tags:
                try:
                    tags = json.loads(raw_tags)
                except (TypeError, ValueError):
                    tags = []
                if isinstance(tags, list) and "비용효과 불인정" in tags:
                    approved = (r["review_result"] or "").startswith("APPROVED") \
                        or r["review_result"] in ("CONDITIONAL_APPROVED",
                                                  "APPROVED_WITH_POSTMARKET")
                    if approved and ce_evidence:
                        tags = [t for t in tags if t != "비용효과 불인정"]
                        if "비용효과 입증" not in tags:
                            tags.append("비용효과 입증")
                        sets.append("policy_tags=?")
                        vals.append(json.dumps(tags, ensure_ascii=False))
                        tag_fix += 1

            if sets:
                vals.append(r["id"])
                conn.execute(
                    f"UPDATE analog_reports SET {', '.join(sets)} WHERE id=?", vals
                )
        conn.commit()

    return {"total": len(items), "amjilsim_set": amjilsim_set,
            "societies_set": soc_set, "foreign_set": foreign_set,
            "rsa_set": rsa_set, "track_set": track_set, "tag_fix": tag_fix}


# ── ⑧ 암질심 일정 보강 (HIRA 파이프라인 테이블 join) ──────────────────────────────

def enrich_amjilsim_join() -> dict:
    """amjilsim_drugs(HIRA 보도자료·파이프라인) 의 amjilsim_pass_date 를
    analog_reports 에 join — PDF 에서 암질심일을 못 찾은 최근 약제 보강.

    매칭: analog.brand_name 이 amjilsim_drugs.brand_kr 로 시작 (함량/제형 suffix 허용).
    analog.amjilsim_history 가 비어 있을 때만 채움 (PDF 추출 우선).

    날짜 정합성 가드: 암질심은 같은 등재 사이클의 약평위(session_date)보다 **앞서야**
    하므로 `amjilsim_pass_date <= session_date` 일 때만 join 한다. amjilsim_drugs 는
    약제(성분)당 1행(최신 암질심)이라, 신규/확대 적응증의 미래 암질심이 과거
    적응증의 약평위 평가에 잘못 붙는 것을 방지한다 (예: 바벤시오 2020 약평위 ↔
    2026 요로상피암 확대 암질심)."""
    ensure_schema()
    filled = skipped_future = 0
    with _connect() as conn:
        drugs = conn.execute(
            "SELECT brand_kr, amjilsim_pass_date FROM amjilsim_drugs "
            "WHERE amjilsim_pass_date IS NOT NULL AND brand_kr IS NOT NULL"
        ).fetchall()
        for d in drugs:
            bk = (d["brand_kr"] or "").strip()
            dt = _norm_date(d["amjilsim_pass_date"])
            if not bk or not dt:
                continue
            rows = conn.execute(
                "SELECT id, session_date, amjilsim_history FROM analog_reports "
                "WHERE file_name LIKE '%.pdf' AND brand_name LIKE ? "
                "AND (amjilsim_history IS NULL OR amjilsim_history IN ('', '[]'))",
                (bk + "%",),
            ).fetchall()
            for r in rows:
                sd = r["session_date"]
                # 약평위 날짜를 모르거나, 암질심이 약평위보다 뒤면 다른 사이클 → 제외
                if not sd or dt > sd:
                    skipped_future += 1
                    continue
                conn.execute(
                    "UPDATE analog_reports SET amjilsim_history=? WHERE id=?",
                    (json.dumps([{"date": dt, "committee": "암질환심의위원회"}],
                                ensure_ascii=False), r["id"]),
                )
                filled += 1
        conn.commit()
    return {"drugs_with_amjilsim": len(drugs), "analog_rows_filled": filled,
            "skipped_future_cycle": skipped_future}


# ── ⑨ 용량 분리 (brand_name → 제품명 + 용량) ──────────────────────────────────

def enrich_dosage() -> dict:
    """brand_name_raw 에서 용량(강도)을 분리해 dosage 컬럼 채우고 brand_name 정제.

    기존 brand_name 에 '가드렛정100밀리그램' 처럼 용량이 붙어 있는 행을
    제품명('가드렛정') + dosage('100밀리그램') 으로 분리. 네트워크 불필요."""
    ensure_schema()
    from agents.analog.pdf_parser import _split_brand_dosage

    name_fix = dose_set = 0
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, brand_name, brand_name_raw, dosage FROM analog_reports "
            "WHERE file_name LIKE '%.pdf'"
        ).fetchall()
        for r in rows:
            src = r["brand_name_raw"] or r["brand_name"] or ""
            clean, dose = _split_brand_dosage(src)
            sets, vals = [], []
            if clean and clean != (r["brand_name"] or ""):
                sets.append("brand_name=?")
                vals.append(clean)
                name_fix += 1
            if dose and dose != (r["dosage"] or ""):
                sets.append("dosage=?")
                vals.append(dose)
                dose_set += 1
            if sets:
                vals.append(r["id"])
                conn.execute(
                    f"UPDATE analog_reports SET {', '.join(sets)} WHERE id=?", vals
                )
        conn.commit()
        # FTS 재빌드 (brand_name 변경 반영)
        try:
            conn.execute("INSERT INTO analog_fts(analog_fts) VALUES('rebuild')")
            conn.commit()
        except Exception as e:
            logger.warning("[enrich_dosage] FTS rebuild 실패: %s", e)
    return {"total": len(rows), "brand_name_fixed": name_fix, "dosage_set": dose_set}


def _split_multi(val: str | None) -> list[str]:
    """'비소세포폐암 | 흑색종' / 'NSCLC, Melanoma' → ['비소세포폐암','흑색종',...]."""
    if not val:
        return []
    return [p.strip() for p in _re.split(r"[|,/·;]", val) if p.strip()]


def enrich_tags(limit: int = None) -> dict:
    """구조화 필드 → concept 태그 매핑 (grounded tagging) + tags_text 빌드.

    동의어 검색의 핵심: 한 report 가 보유한 모든 동의어 concept 를 태깅하면
    '고지혈증 주사제'·'이상지질혈증 주사제'·'PCSK9 주사제' 가 같은 약제를 retrieval.
    네트워크 불필요 — 온톨로지(tag_seeds) in-memory 매칭.
    """
    ensure_schema()
    from agents.analog.store import seed_concepts
    from agents.analog import tag_seeds as ts

    seeded = seed_concepts()
    idx = ts.alias_index()
    cby = ts.concept_by_id()
    aliases_of = ts.aliases_of()
    scan_terms = ts.disease_scan_terms()

    # (필드후보 → 매칭) 가중치 + source 정의
    # 구조화 필드: (컬럼, source, weight, multi여부)
    field_specs = [
        ("disease_name_ko", "disease_col", 1.0, True),
        ("disease_name_en", "disease_col", 1.0, True),
        ("cancer_type", "cancer_col", 1.0, True),
        ("generic_name_en", "inn_col", 1.0, False),
        ("generic_name", "inn_col", 1.0, False),
        ("biomarker", "biomarker_col", 1.0, True),
        ("line_of_therapy", "lot_col", 0.8, False),
        ("treatment_setting", "setting_col", 0.8, True),
    ]

    n_reports = n_tags = n_tagstext = 0
    with _connect() as conn:
        sql = ("SELECT id, brand_name, brand_name_raw, generic_name, generic_name_en, "
               "disease_name_ko, disease_name_en, cancer_type, biomarker, line_of_therapy, "
               "treatment_setting, comparator_drugs, mfds_effect_text "
               "FROM analog_reports WHERE file_name LIKE '%.pdf' "
               "AND (pdf_extractable IS NULL OR pdf_extractable = 1)")
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = conn.execute(sql).fetchall()

        for r in rows:
            # concept_id → (weight, source) — 최고 weight 유지
            tags: dict[str, tuple[float, str]] = {}

            def _add(cid: str, w: float, src: str):
                if cid not in tags or w > tags[cid][0]:
                    tags[cid] = (w, src)

            for col, src, w, multi in field_specs:
                raw = r[col]
                vals = _split_multi(raw) if multi else ([raw] if raw else [])
                for v in vals:
                    for cid in idx.get(ts.normalize(v), []):
                        _add(cid, w, src)

            # 비교약제 (JSON list)
            try:
                comps = json.loads(r["comparator_drugs"] or "[]")
            except (ValueError, TypeError):
                comps = []
            for c in comps:
                if isinstance(c, str):
                    for cid in idx.get(ts.normalize(c), []):
                        _add(cid, 0.6, "comparator_col")

            # 제형 (브랜드명 접미사)
            form_cid = ts.detect_form(r["brand_name"] or r["brand_name_raw"])
            if form_cid:
                _add(form_cid, 0.8, "form_suffix")

            # 효능효과 자유텍스트 질환 스캔 (보조, 낮은 weight)
            eff = r["mfds_effect_text"] or ""
            if eff:
                eff_n = ts.normalize(eff)
                for _raw, term_n, cid in scan_terms:
                    if cid not in tags and term_n in eff_n:
                        _add(cid, 0.7, "effect_scan")

            # 쓰기: report_tags 갱신
            conn.execute("DELETE FROM analog_report_tags WHERE report_id=?", (r["id"],))
            for cid, (w, src) in tags.items():
                conn.execute(
                    "INSERT OR REPLACE INTO analog_report_tags"
                    "(report_id, concept_id, tag_type, weight, source) VALUES (?,?,?,?,?)",
                    (r["id"], cid, cby.get(cid, {}).get("type"), w, src),
                )
            n_tags += len(tags)

            # tags_text 그림자 컬럼 (concept canonical + alias 전량 → FTS 동의어 색인)
            surfaces: list[str] = []
            for cid in tags:
                c = cby.get(cid, {})
                if c.get("canonical_ko"):
                    surfaces.append(c["canonical_ko"])
                if c.get("canonical_en"):
                    surfaces.append(c["canonical_en"])
                surfaces.extend(aliases_of.get(cid, []))
            tags_text = " ".join(dict.fromkeys(surfaces)) or None
            conn.execute("UPDATE analog_reports SET tags_text=? WHERE id=?",
                         (tags_text, r["id"]))
            if tags_text:
                n_tagstext += 1
            n_reports += 1

        conn.commit()
        # FTS 재빌드 (tags_text 반영)
        try:
            conn.execute("INSERT INTO analog_fts(analog_fts) VALUES('rebuild')")
            conn.commit()
        except Exception as e:
            logger.warning("[enrich_tags] FTS rebuild 실패: %s", e)

    return {"seeded": seeded, "reports": n_reports,
            "tags_written": n_tags, "tags_text_set": n_tagstext}


# ── 전체 실행 ─────────────────────────────────────────────────────────────────

def enrich_reimbursement_date() -> dict:
    """국내약가(drug_prices)에서 최초 약가 등재일(MIN apply_date) → first_reimbursement_date.

    매칭(인덱스 prefix only — 3.8M행 풀스캔 금지):
      ① 브랜드: product_name_kr LIKE '{brand_name}%' (idx_name)  ← 우선
      ② 성분:  LOWER(ingredient) LIKE '{generic_name_en 첫토큰}%' (idx_ingredient)  ← fallback
    정합성: first_reimbursement_date ≥ mfds_permit_date(허가 후). 위반/미매칭 skip.
    apply_date 는 'YYYY.MM.DD' 문자열 — zero-padded 라 MIN() 문자열정렬=날짜정렬.
    """
    ensure_schema()
    filled = skipped = reverted = 0
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, brand_name, generic_name_en, mfds_permit_date FROM analog_reports"
        ).fetchall()
        for r in rows:
            brand = _clean_brand_for_price(r["brand_name"])
            first = key = None
            # range 쿼리(>= prefix AND < prefix+U+FFFF)로 인덱스 사용 — LIKE 는 case-insensitive 라 풀스캔
            if brand:
                x = conn.execute(
                    "SELECT MIN(apply_date) FROM drug_prices WHERE product_name_kr >= ? AND product_name_kr < ? AND apply_date IS NOT NULL",
                    (brand, brand + "￿"),
                ).fetchone()
                if x and x[0]:
                    first, key = x[0], "brand"
            if not first and (r["generic_name_en"] or "").strip():
                inn = r["generic_name_en"].strip().split()[0]
                if len(inn) >= 4:
                    x = conn.execute(
                        "SELECT MIN(apply_date) FROM drug_prices WHERE ingredient >= ? AND ingredient < ? AND apply_date IS NOT NULL",
                        (inn, inn + "￿"),
                    ).fetchone()
                    if x and x[0]:
                        first, key = x[0], "ingredient"
            if not first:
                skipped += 1
                continue
            nd = _norm_date(first)
            permit = _norm_date(r["mfds_permit_date"])
            if permit and nd and nd < permit:   # 약가가 허가보다 앞 → 매칭 오류
                reverted += 1
                continue
            conn.execute(
                "UPDATE analog_reports SET first_reimbursement_date=?, reimbursement_match_key=? WHERE id=?",
                (nd, key, r["id"]),
            )
            filled += 1
        conn.commit()
    res = {"total": len(rows), "filled": filled, "skipped_no_match": skipped, "skipped_before_permit": reverted}
    logger.info("[enrich.reimbursement_date] %s", res)
    return res


def enrich_all(limit: int = None) -> dict:
    dis = enrich_disease(limit)
    eff = enrich_efficacy(limit)
    pol = enrich_policy(limit)
    mfds = enrich_mfds(limit)
    gap = enrich_gap(limit)
    com = enrich_committee(limit)
    amj = enrich_amjilsim_join()
    dose = enrich_dosage()
    # trajectory 는 mfds(허가일) backfill 이후 실행해야 lag 계산이 최신
    traj = enrich_trajectory()
    # tags 는 disease/efficacy 결과(질환·비교약제) 이후 실행
    tags = enrich_tags(limit)
    return {
        "disease": dis,
        "efficacy": eff,
        "policy": pol,
        "mfds": mfds,
        "gap": gap,
        "committee": com,
        "amjilsim_join": amj,
        "dosage": dose,
        "trajectory": traj,
        "tags": tags,
    }


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    lim = int(sys.argv[2]) if len(sys.argv) > 2 else None
    fns = {
        "disease": lambda: enrich_disease(lim),
        "efficacy": lambda: enrich_efficacy(lim),
        "policy": lambda: enrich_policy(lim),
        "mfds": lambda: enrich_mfds(lim),
        "gap": lambda: enrich_gap(lim),
        "committee": lambda: enrich_committee(lim),
        "amjilsim_join": enrich_amjilsim_join,
        "reimbursement_date": enrich_reimbursement_date,
        "dosage": enrich_dosage,
        "trajectory": enrich_trajectory,
        "tags": lambda: enrich_tags(lim),
        "all": lambda: enrich_all(lim),
    }
    fn = fns.get(cmd)
    if fn is None:
        print(f"알 수 없는 명령: {cmd}. 사용: {list(fns)}")
        sys.exit(1)
    print(json.dumps(fn(), ensure_ascii=False, indent=2))
