"""약제 등재 아날로그 — enrich (식약처 허가 + 허가↔급여 갭 분류 + 재심의 trajectory).

① trajectory: 코퍼스 자체에서 약제별 위원회 이력 그룹핑 (외부호출 0, 즉시).
② MFDS 허가: agents/scrapers/kr_mfds_permit.lookup_permit → 허가일·허가 적응증(effect_text). 캐시.
③ 갭 분류(LLM): 허가 적응증 vs 급여 적응증 → 축소/확대/구체화/동일/비교불가 + 양측 원문 인용 근거.
   문구 diff 아닌 '본질 범위 변화'. 캐시(generic+permit hash). 충실성: 실제 원문만, 인용 강제.

실행:
  python -m agents.analog.enrich trajectory   # 즉시
  python -m agents.analog.enrich mfds [limit]  # 허가 배치
  python -m agents.analog.enrich gap [limit]   # 갭 분류(LLM)
  python -m agents.analog.enrich all [limit]
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime
from pathlib import Path

from agents.analog.store import DB_PATH, _connect, ensure_schema

logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parents[2]

_PASS_STATES = {"APPROVED", "CONDITIONAL_APPROVED"}
_GAP_TYPES = {"축소", "확대", "구체화", "동일", "비교불가"}

_GAP_SYSTEM = """당신은 한국 약가·급여(HIRA) 전문 애널리스트다.
같은 약제의 **식약처 허가 적응증(효능효과)** 과 **급여 승인 적응증** 을 비교해,
급여가 허가 대비 어떻게 달라졌는지 **본질적 범위 변화**로 분류하라. 문구 단위 diff 가 아니다.

분류(coverage_gap_type) 하나만 선택:
- "축소": 급여 범위가 허가의 일부만 (적응증 수 감소 또는 더 좁은 환자군)
- "확대": 급여가 허가보다 넓음 (드묾)
- "구체화": 적응증은 같으나 급여에서 조건(바이오마커·치료차수·병용)이 더 구체화/제한
- "동일": 실질 범위 동일
- "비교불가": 한쪽 원문 부족으로 판단 불가

원칙:
- 제공된 두 원문에 **실제로 있는 내용만** 근거로. 외부 지식·추측 금지(없으면 비교불가).
- evidence 에 허가측·급여측 핵심 문구를 **그대로 인용**(짧게).
반드시 JSON 만: {"coverage_gap_type": "...", "evidence": "허가: '...' / 급여: '...' → 판단 근거 1문장"}"""


# ── ① trajectory (코퍼스 내장) ───────────────────────────────────────────────

def enrich_trajectory() -> dict:
    """약제(generic 우선, 없으면 brand)별 위원회 이력 → 재심의 횟수·통과 차수/소요."""
    ensure_schema()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, generic_name, brand_name, session_date, review_result, ordinal "
            "FROM analog_reports").fetchall()
        groups: dict[str, list] = {}
        for r in rows:
            key = (r["generic_name"] or r["brand_name"] or "").strip().lower()
            if not key:
                continue
            groups.setdefault(key, []).append(r)

        updated = 0
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
            n_sessions = next((i + 1 for i, it in enumerate(items)
                               if (it["review_result"] or "").upper() in _PASS_STATES), None)
            span_days = None
            if first_date and pass_date:
                try:
                    span_days = (datetime.fromisoformat(pass_date)
                                 - datetime.fromisoformat(first_date)).days
                except ValueError:
                    span_days = None
            for it in items:
                conn.execute(
                    "UPDATE analog_reports SET requeue_count=?, first_session_date=?, "
                    "pass_session_date=?, sessions_to_pass=? WHERE id=?",
                    (requeue, first_date, pass_date,
                     span_days if span_days is not None else n_sessions, it["id"]))
                updated += 1
        conn.commit()
    return {"groups": len(groups), "rows_updated": updated}


# ── ② MFDS 허가 (허가일 + 허가 적응증) ───────────────────────────────────────

def enrich_mfds(limit: int = None) -> dict:
    """branded 약제별 식약처 허가 조회 → permit_date·effect_text 저장. 캐시 활용."""
    ensure_schema()
    from agents.scrapers.kr_mfds_permit import lookup_permit

    with _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT brand_name, generic_name FROM analog_reports "
            "WHERE brand_name IS NOT NULL AND brand_name != '' "
            "AND (mfds_permit_date IS NULL AND mfds_effect_text IS NULL)").fetchall()
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
        if permit_date or effect:
            found += 1
        else:
            miss += 1
        with _connect() as conn:
            conn.execute(
                "UPDATE analog_reports SET mfds_permit_date=?, mfds_effect_text=? "
                "WHERE brand_name=?",
                (permit_date, effect, brand))
            conn.commit()
    return {"drugs": len(drugs), "found": found, "miss": miss}


# ── ③ 허가↔급여 갭 분류 (LLM) ────────────────────────────────────────────────

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


def _classify_gap(permit_text: str, reimb_text: str) -> dict | None:
    key = _openai_key()
    if not key:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key)
        user = (f"[식약처 허가 적응증]\n{permit_text[:4000]}\n\n"
                f"[급여 승인 적응증]\n{reimb_text[:2000]}")
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": _GAP_SYSTEM},
                      {"role": "user", "content": user}],
            temperature=0.0, response_format={"type": "json_object"}, max_tokens=400)
        data = json.loads(resp.choices[0].message.content or "{}")
        gt = data.get("coverage_gap_type")
        if gt not in _GAP_TYPES:
            return None
        return {"coverage_gap_type": gt, "evidence": (data.get("evidence") or "")[:1000]}
    except Exception as e:
        logger.warning("[analog.enrich] 갭 분류 실패: %s", e)
        return None


def enrich_gap(limit: int = None) -> dict:
    """허가 적응증·급여 적응증 둘 다 있는 약제 → 갭 분류(LLM). 캐시(약제+허가hash)."""
    ensure_schema()
    with _connect() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS analog_gap_cache (
            cache_key TEXT PRIMARY KEY, gap_type TEXT, evidence TEXT, created_at TEXT)""")
        conn.commit()
        rows = conn.execute(
            "SELECT DISTINCT brand_name, generic_name, mfds_effect_text, disease_name, body_text "
            "FROM analog_reports WHERE mfds_effect_text IS NOT NULL AND mfds_effect_text != '' "
            "AND coverage_gap_type IS NULL").fetchall()
    drugs = list(rows)
    if limit:
        drugs = drugs[:limit]

    classified = cached = skipped = 0
    for r in drugs:
        permit_text = r["mfds_effect_text"]
        reimb_text = " / ".join(filter(None, [r["disease_name"], (r["body_text"] or "")[:1500]]))
        if not reimb_text.strip():
            skipped += 1
            continue
        ckey = hashlib.sha1(
            f"{r['brand_name']}|{hashlib.sha1((permit_text or '').encode()).hexdigest()[:12]}"
            f"|{hashlib.sha1(reimb_text.encode()).hexdigest()[:12]}".encode()).hexdigest()
        with _connect() as conn:
            hit = conn.execute("SELECT gap_type, evidence FROM analog_gap_cache WHERE cache_key=?",
                               (ckey,)).fetchone()
        if hit:
            gap = {"coverage_gap_type": hit["gap_type"], "evidence": hit["evidence"]}
            cached += 1
        else:
            gap = _classify_gap(permit_text, reimb_text)
            if not gap:
                skipped += 1
                continue
            with _connect() as conn:
                conn.execute("INSERT OR REPLACE INTO analog_gap_cache VALUES (?,?,?,?)",
                             (ckey, gap["coverage_gap_type"], gap["evidence"],
                              datetime.now().isoformat(timespec="seconds")))
                conn.commit()
            classified += 1
        with _connect() as conn:
            conn.execute(
                "UPDATE analog_reports SET coverage_gap_type=?, coverage_gap_evidence=?, enriched_at=? "
                "WHERE brand_name=?",
                (gap["coverage_gap_type"], gap["evidence"],
                 datetime.now().isoformat(timespec="seconds"), r["brand_name"]))
            conn.commit()
    return {"drugs": len(drugs), "classified": classified, "cached": cached, "skipped": skipped}


def enrich_all(limit: int = None) -> dict:
    t = enrich_trajectory()
    m = enrich_mfds(limit)
    g = enrich_gap(limit)
    return {"trajectory": t, "mfds": m, "gap": g}


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    lim = int(sys.argv[2]) if len(sys.argv) > 2 else None
    fn = {"trajectory": enrich_trajectory, "mfds": lambda: enrich_mfds(lim),
          "gap": lambda: enrich_gap(lim), "all": lambda: enrich_all(lim)}[cmd]
    print(json.dumps(fn(), ensure_ascii=False, indent=2))
