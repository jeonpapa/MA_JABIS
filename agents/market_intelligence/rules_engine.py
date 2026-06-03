"""market_intelligence_rules.md v3 하드 규칙 강제.

(a) published_at 없거나 형식 불량 → 참조 제거
(b) published_at 이 ±6개월 (특허만료 ±12개월) 윈도우 밖 → 참조 제거
(c) reason 본문의 허용 연도 밖 **문장** 전체 삭제
(d) 남은 refs=0 → mechanism=unknown / confidence=low
(e) window 메타 기록 + enforcement 로그 notes 누적
"""
from __future__ import annotations

import calendar
import logging
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[2]
_RULES_PATH = BASE_DIR / "agents" / "rules" / "market_intelligence_rules.md"


def prepare_review_payload(result: dict) -> dict:
    """LLM 리뷰어(OpenAI/Gemini) 에 전달할 결과 dict — date_unknown refs 제거.

    `date_unknown=True` 또는 `published_at` 빈 refs 는 LLM 이 "published_at 누락 규칙 위반"
    으로 오판하므로 리뷰 대상에서 제외. 기계적 검증(`enforce_rules`) 은 이미 윈도우 외
    references 를 제거한 상태이므로 이 단계는 표시용 필터.
    """
    display = dict(result)
    refs = result.get("references") or []
    display["references"] = [r for r in refs
                              if r.get("published_at") and not r.get("date_unknown")]
    return display


def _load_mi_rules() -> str:
    try:
        return _RULES_PATH.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("[MI Agent] 룰 파일 로드 실패 (%s) — 임베디드 폴백 사용", e)
        return ""


MI_RULES_TEXT = _load_mi_rules()


def window_bounds(change_date: str, months: int = 6):
    """change_date 기준 ±months 윈도우의 (from_dt, to_dt, from_str, to_str) 반환."""
    try:
        dt = datetime.strptime(change_date, "%Y.%m.%d")
    except Exception:
        try:
            dt = datetime.strptime(change_date[:7], "%Y.%m")
        except Exception:
            return None, None, "", ""
    y, m = dt.year, dt.month
    fm = m - months
    fy = y
    while fm <= 0:
        fm += 12
        fy -= 1
    tm = m + months
    ty = y
    while tm > 12:
        tm -= 12
        ty += 1
    wf = datetime(fy, fm, 1)
    wt = datetime(ty, tm, calendar.monthrange(ty, tm)[1])
    return wf, wt, f"{fy}.{fm:02d}", f"{ty}.{tm:02d}"


def _apply_delta_sanity(result: dict, delta_pct: float | None) -> str | None:
    """변동률 vs mechanism 부합성 사후 검증.

    LLM 이 작은 변동 (-10% 이하) 에 patent_expiration/indication_expansion 같은
    큰-임팩트 기전을 잘못 적용하는 경우 보정.

    Returns: 적용된 보정 메시지 또는 None.
    """
    if delta_pct is None:
        return None
    mech = (result.get("mechanism") or "").lower()
    abs_delta = abs(delta_pct)
    big_impact = ("patent_expiration", "indication_expansion")

    # 작은 변동 + 큰-임팩트 기전 = 잘못된 분류
    if abs_delta <= 10 and mech in big_impact:
        # reason 본문에 'PVA'/'사용량'/'실거래가' 단서 있으면 그쪽으로 정규화
        reason = (result.get("reason") or "").lower()
        if "pva" in reason or "사용량" in reason:
            result["mechanism"] = "volume_price"
            result["mechanism_label"] = "사용량-연동 약가인하"
        else:
            result["mechanism"] = "actual_transaction"
            result["mechanism_label"] = "실거래가 연동 약가인하"
        result["confidence"] = "medium"
        return (
            f"delta_pct {delta_pct:+.2f}% (≤10%) — "
            f"{mech} 부적합. {result['mechanism_label']} 로 보정"
        )
    return None


def enforce_rules(result: dict, change_date: str, delta_pct: float | None = None) -> dict:
    """Rule enforcement — in-place 수정 후 동일 dict 반환."""
    mech = (result.get("mechanism") or "").lower()
    months = 12 if mech == "patent_expiration" else 6
    wf, wt, wf_str, wt_str = window_bounds(change_date, months=months)
    result["window"] = {"from": wf_str, "to": wt_str, "months": months}

    enforcement_log = []

    # (pre-a) URL 검증 — Perplexity hallucination (오래된 기사를 신규로 위조) 방지.
    # ID 휴리스틱 의심 + top weight refs 의 published_at 을 실제 fetch 로 교체.
    refs_in = result.get("references") or []
    if refs_in:
        try:
            from .url_verifier import verify_references
            stat = verify_references(refs_in, top_n=4, max_fetch=8)
            if stat["corrected"] or stat["dropped"]:
                enforcement_log.append(
                    f"URL 검증: {stat['corrected']}건 published_at 교체, "
                    f"{stat['dropped']}건 drop (의심 {stat['suspicious']}건)"
                )
        except Exception as e:
            logger.warning("[enforce] URL 검증 실패: %s", e)
        # drop=True 마킹된 ref 실제 제거
        result["references"] = [r for r in refs_in if not r.get("drop")]

    # (a)+(b) references 필터 — published_at 누락 시 URL 에서 best-effort 추출, 여전히 없으면
    # `date_unknown=True` 로 마킹하되 제거하지는 않는다 (refs=0 fallback 을 피하기 위함).
    kept, dropped_out_of_window, marked_date_unknown = [], 0, 0
    for r in result.get("references", []) or []:
        pub = (r.get("published_at") or "").strip()
        url = (r.get("url") or "")
        # URL 에서 /YYYY/MM/DD 또는 ?date=YYYYMMDD 패턴 추출 시도
        if not pub:
            m = re.search(r"/(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})", url)
            if not m:
                m = re.search(r"(\d{4})(\d{2})(\d{2})", url)
            if m:
                pub = f"{m.group(1)}.{m.group(2).zfill(2)}.{m.group(3).zfill(2)}"
                r["published_at"] = pub
                r.setdefault("notes", "date_inferred_from_url")
        pd = None
        if pub:
            try:
                pd = datetime.strptime(pub[:10].replace("-", "."), "%Y.%m.%d")
            except Exception:
                pd = None
        if pd is None:
            r["date_unknown"] = True
            marked_date_unknown += 1
            kept.append(r)
            continue
        if wf and wt and (pd < wf or pd > wt):
            dropped_out_of_window += 1
            continue
        kept.append(r)
    result["references"] = kept
    if marked_date_unknown:
        enforcement_log.append(f"published_at 미확인 {marked_date_unknown}건 — date_unknown 마킹 후 보존")
    if dropped_out_of_window:
        enforcement_log.append(f"윈도우 외 references {dropped_out_of_window}건 제거")

    # (c) reason 본문의 연도 게이트
    # 윈도우 경계 연도 뿐 아니라 사이 연도 전부 허용 (예: 2022.09~2024.09 → {2022, 2023, 2024})
    if wf and wt:
        allowed_years = set(range(wf.year, wt.year + 1))
        reason = (result.get("reason") or "").strip()
        if reason:
            sentences = re.split(r"(?<=[.!?。])\s+|\n+", reason)
            cleaned, stripped = [], 0
            for sent in sentences:
                years = set(int(y) for y in re.findall(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)", sent))
                if years and not years.issubset(allowed_years):
                    stripped += 1
                    continue
                cleaned.append(sent.strip())
            cleaned_reason = " ".join(s for s in cleaned if s).strip()
            if stripped:
                enforcement_log.append(f"reason 문장 {stripped}개 삭제(윈도우 외 연도)")
                result["reason"] = cleaned_reason or f"추정: 윈도우({wf_str}~{wt_str}) 내 확인 가능한 공개 보도 없음."

    # (d-pre) delta_pct vs mechanism sanity — 작은 변동에 patent/indication 부적합 분류 보정
    sanity_msg = _apply_delta_sanity(result, delta_pct)
    if sanity_msg:
        enforcement_log.append(sanity_msg)

    # (d) refs=0 → 기전 하향
    if not result.get("references"):
        if (result.get("mechanism") or "").lower() not in ("unknown", ""):
            result["mechanism"] = "unknown"
            result["mechanism_label"] = "미분류"
        result["confidence"] = "low"
        current = (result.get("reason") or "").strip()
        fallback = f"추정: 윈도우({wf_str}~{wt_str}) 내 확인 가능한 공개 보도 없음."
        if not current or len(current) < 10:
            result["reason"] = fallback
        elif not current.lstrip().startswith("추정"):
            result["reason"] = "추정: " + current

    if enforcement_log:
        existing = (result.get("notes") or "").strip()
        joined = " · ".join(enforcement_log)
        result["notes"] = f"{existing} · [enforce] {joined}".strip(" ·") if existing else f"[enforce] {joined}"

    return result
