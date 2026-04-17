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


def enforce_rules(result: dict, change_date: str) -> dict:
    """Rule enforcement — in-place 수정 후 동일 dict 반환."""
    mech = (result.get("mechanism") or "").lower()
    months = 12 if mech == "patent_expiration" else 6
    wf, wt, wf_str, wt_str = window_bounds(change_date, months=months)
    result["window"] = {"from": wf_str, "to": wt_str, "months": months}

    enforcement_log = []

    # (a)+(b) references 필터
    kept, dropped_missing_date, dropped_out_of_window = [], 0, 0
    for r in result.get("references", []) or []:
        pub = (r.get("published_at") or "").strip()
        if not pub:
            dropped_missing_date += 1
            continue
        try:
            pd = datetime.strptime(pub[:10].replace("-", "."), "%Y.%m.%d")
        except Exception:
            dropped_missing_date += 1
            continue
        if wf and wt and (pd < wf or pd > wt):
            dropped_out_of_window += 1
            continue
        kept.append(r)
    result["references"] = kept
    if dropped_missing_date:
        enforcement_log.append(f"published_at 누락/불량 {dropped_missing_date}건 제거")
    if dropped_out_of_window:
        enforcement_log.append(f"윈도우 외 references {dropped_out_of_window}건 제거")

    # (c) reason 본문의 연도 게이트
    if wf and wt:
        allowed_years = {wf.year, wt.year}
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
