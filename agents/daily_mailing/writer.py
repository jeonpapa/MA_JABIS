from __future__ import annotations

import re
from html import escape
from typing import Iterable


def _item_get(item, key: str, default: object = ""):
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


MA_DIRECT_TERMS = (
    "급여", "약가", "약평위", "암질심", "건정심", "위험분담", "rsa", "약가협상",
    "급여 적정성", "평가금액", "사용량", "재정영향", "고시", "상정", "등재",
)
MA_PROCESS_TERMS = ("신속심사", "허가", "approval", "희귀", "소아", "환자 접근", "급여 사각지대")
LOW_VALUE_MSD_CONTEXT_TERMS = ("사회공헌", "봉사", "캠페인", "질환 교육", "대표적인 약제", "일반 질환")
LOW_VALUE_CORPORATE_REPORT_TERMS = ("지속가능경영보고서", "esg", "성과 공개", "사회책임", "csr")
KEYTRUDA_DIRECT_TERMS = ("키트루다", "keytruda", "펨브롤리주맙", "pembrolizumab")
POLICY_PRICING_TRACKER_TERMS = ("적응증별 약가", "1약=1약가", "위험분담", "rsa", "사용량-약가", "사용량 약가", "약가유연계약")
PHARMA_ACCESS_CONTEXT_TERMS = (
    "약평위", "암질심", "건정심", "약제", "의약품", "신약", "항암제", "치료제", "제약",
    "바이오", "식약처", "심평원", "hira", "평가금액", "위험분담", "급여 적정성", "등재",
    "키트루다", "린파자", "웰리렉", "가다실", "엑스코프리", "버제니오", "카보메틱스",
)
NON_PHARMA_NOISE_TERMS = (
    "도수치료", "실손보험", "실손", "양자컴", "양자컴퓨터", "rsa 공개키", "암호 방식",
    "소인수분해", "황정민", "술톤", "과음", "알코올", "알츠하이머",
)


def _combined_text(item) -> str:
    return f"{_item_get(item, 'title')} {_item_get(item, 'description')}".lower()


def assess_article_quality(item) -> dict:
    """Editorial metadata for monitoring-first Daily Mailing article cards.

    ``ma_relevance`` is not an inclusion gate. Keyword/company/brand-matched
    monitoring items may be included with low MA relevance; the score only
    controls sectioning and whether a Market Access note is written.
    """
    text = _combined_text(item)
    source_tier = str(_item_get(item, "source_tier", ""))
    source_name = str(_item_get(item, "source_name", ""))
    if not source_tier and source_name in {"데일리팜", "약업신문", "메디칼타임즈", "메디파나뉴스", "히트뉴스", "청년의사", "뉴스더보이스", "팜뉴스", "약사공론", "메디게이트뉴스"}:
        source_tier = "media_tier_A"
    flags: list[str] = []

    ma_relevance = 0
    pharma_context = any(term in text for term in PHARMA_ACCESS_CONTEXT_TERMS)
    non_pharma_noise = any(term in text for term in NON_PHARMA_NOISE_TERMS) and not pharma_context
    if non_pharma_noise:
        flags.append("non_pharma_or_non_drug_benefit_noise")
    if non_pharma_noise:
        ma_relevance = 0
    elif any(term in text for term in ("약평위", "암질심", "건정심", "약가협상", "평가금액", "재정영향")):
        ma_relevance = 5
    elif any(term in text for term in MA_DIRECT_TERMS) and pharma_context:
        ma_relevance = 4
    elif any(term in text for term in MA_PROCESS_TERMS):
        ma_relevance = 3
    elif any(term in text for term in ("msd", "엠에스디", "키트루다", "keytruda", "가다실", "린파자", "lynparza", "웰리렉")):
        ma_relevance = 1

    if ma_relevance <= 1 and any(term in text for term in LOW_VALUE_MSD_CONTEXT_TERMS):
        flags.append("low_value_msd_mention")
        ma_relevance = min(ma_relevance, 1)
    if any(term in text for term in LOW_VALUE_CORPORATE_REPORT_TERMS):
        flags.append("corporate_report_not_live_ma_signal")
        ma_relevance = min(ma_relevance, 1)
    if any(term in text for term in KEYTRUDA_DIRECT_TERMS):
        flags.append("keytruda_direct_source_verification_promoted")
        ma_relevance = max(ma_relevance, 4)
    if not non_pharma_noise and any(term in text for term in POLICY_PRICING_TRACKER_TERMS):
        flags.append("policy_pricing_tracker")
        ma_relevance = max(ma_relevance, 3)
    if source_name.lower() in {"biospectator", "바이오스펙테이터"} or "calibration" in source_tier:
        flags.append("calibration_source_not_live_candidate")
    if source_tier in {"", "unregistered", "unknown"}:
        flags.append("unregistered_source_requires_review")
    if source_tier not in {"official", "official_payer", "regulator"}:
        flags.append("publisher_verified_required")
    if ma_relevance >= 4:
        flags.append("official_cross_check_required")

    monitoring_importance = 1
    if _item_get(item, "matched_keywords", ()):  # explicit dashboard scope hit
        monitoring_importance += min(len(_item_get(item, "matched_keywords", ())), 3)
    if any(term in text for term in ("msd", "엠에스디", "한국msd", "한국엠에스디", "키트루다", "keytruda", "가다실", "gardasil", "린파자", "lynparza", "웰리렉", "welireg")):
        monitoring_importance += 2
    if source_tier in {"media_tier_A", "tier_1_trade_media", "official", "official_payer", "regulator"}:
        monitoring_importance += 1
    monitoring_importance = min(monitoring_importance, 5)

    priority = "High" if ma_relevance >= 4 or monitoring_importance >= 4 else "Medium" if ma_relevance == 3 or monitoring_importance >= 3 else "Watch"
    review_status = "excluded" if "calibration_source_not_live_candidate" in flags else "needs_review"
    return {
        "ma_relevance": ma_relevance,
        "monitoring_importance": monitoring_importance,
        "priority": priority,
        "review_status": review_status,
        "quality_flags": flags,
    }


def should_include_in_top_signals(item) -> bool:
    quality = assess_article_quality(item)
    blocked_flags = {"corporate_report_not_live_ma_signal", "low_value_msd_mention", "calibration_source_not_live_candidate", "non_pharma_or_non_drug_benefit_noise"}
    return (
        quality["review_status"] != "excluded"
        and quality["ma_relevance"] >= 3
        and not blocked_flags.intersection(quality["quality_flags"])
    )



def split_top_signals_and_watchlist(items: Iterable) -> tuple[list, list]:
    top: list = []
    watch: list = []
    for item in items:
        if should_include_in_top_signals(item):
            top.append(item)
        else:
            watch.append(item)
    return top, watch

def _section_heading(label: str, count: int) -> str:
    return f"## {label} ({count})"

def summarize_key_points(item, max_points: int = 3) -> list[str]:
    """Summarize the news content into up to three deterministic bullets."""
    title = str(_item_get(item, "title", "")).strip()
    desc = str(_item_get(item, "description", "")).strip()
    text = re.sub(r"\s+", " ", desc).strip(" .")
    pieces = [p.strip(" .") for p in re.split(r"(?<=[.!?。])\s+|\.\.\.|…|;", text) if p.strip(" .")]
    bullets: list[str] = []
    if title and len(pieces) < 2:
        bullets.append(title)
    for p in pieces:
        if p and p not in bullets:
            bullets.append(p)
        if len(bullets) >= max_points:
            break
    return bullets[:max_points] or ["원문 확인 필요"]


def infer_news_insight(item) -> str:
    text = _combined_text(item)
    source = _item_get(item, "source_name", "출처")
    if "린파자" in text and any(t in text for t in ("암질심", "약평위", "급여")):
        return "린파자 병용 유지요법이 암질심 이후 약평위 단계로 이동하는 신호로, HRD 양성 난소암 환자군의 급여 공백 해소와 병용요법 재정영향이 동시에 쟁점화될 수 있습니다."
    if "평가금액" in text or "약가협상" in text:
        return "평가금액 이하 수용/약가협상 국면은 임상 가치보다 가격 수용성과 예상청구액 관리가 실제 등재 시점의 병목이 되는 신호입니다."
    if not should_include_in_top_signals(item):
        return "사용자 지정 모니터링 범위에 포함되는 기사입니다. 현재 기사만으로는 한국 급여·약가·payer 의사결정으로 이어지는 직접 근거가 제한적이므로, 회사/브랜드/시장 노출 관점에서 추적합니다."
    if any(t in text for t in ("급여", "약가", "약평위", "암질심", "위험분담", "rsa", "협상")):
        return f"{source} 보도는 치료 접근성·재정영향·등재 프로세스가 실제 의사결정 이슈로 전환되고 있음을 시사합니다."
    if any(t in text for t in ("msd", "엠에스디", "키트루다", "keytruda", "가다실", "lynparza", "린파자", "welireg", "웰리렉")):
        return "MSD 또는 MSD 제품 관련 미디어 노출입니다. 한국 MA 영향은 급여·약가·환자 접근성 근거가 추가 확인될 때 별도 Market Access Note로 확장합니다."
    if any(t in text for t in ("허가", "임상", "approval", "clinical", "phase", "신속심사")):
        return "허가·임상 신호가 향후 국내 도입/등재 타임라인과 치료 옵션 논의로 확장될 가능성이 있습니다."
    return "모니터링 범위에는 포함되지만 현재 기사만으로는 뚜렷한 MA 의사결정 신호가 제한적입니다."


def infer_ma_implication(item) -> str:
    """Return Korea MA implication only when there is a defensible MA angle."""
    text = _combined_text(item)
    if not should_include_in_top_signals(item):
        return ""
    if "린파자" in text and any(t in text for t in ("암질심", "약평위", "급여")):
        return "HRD 양성 난소암에서 병용 유지요법의 급여 확대가 논의되는 만큼, 환자군 정의(HRD/BRCA), bevacizumab 병용 비용, 기존 단독 유지요법 대비 추가 benefit, 재정영향 관리 조건을 분리해 약평위 쟁점을 추적해야 합니다."
    if any(t in text for t in ("구강붕해정", "odt", "제형", "로수바", "에제")) and any(t in text for t in ("허가", "약가 신청", "고시", "출시")):
        return "로수바스타틴+에제티미브 구강붕해정 경쟁은 신약 가치평가보다 제형 차별화·복합제/제네릭 약가 산정·허가-약가 신청 타이밍이 시장 접근 속도를 좌우하는 사안입니다. 동일 성분 내 제형 프리미엄 인정 가능성, 대체 조제/처방 편의성, 출시월 약가 고시 일정을 구분해 추적해야 합니다."
    if "평가금액" in text or "약가협상" in text:
        return "평가금액 이하 수용 조건이 붙은 건은 협상 가격·예상청구액·대체약제 대비 비용효과성이 최종 등재 시점과 접근성의 핵심 변수이므로, 협상 결과와 급여기준 문구를 후속 확인해야 합니다."
    if any(t in text for t in ("약제관리실", "약제급여평가위원회", "약평위", "재정영향평가위원회")):
        return "약평위·재정영향평가 운영 축의 변화/강조점이 신약 등재 심사 속도와 쟁점 설정에 영향을 줄 수 있으므로, 향후 회의 안건·평가 기준 변화와 RSA/허가-평가 연계 운용 방향을 추적해야 합니다."
    if any(t in text for t in ("키트루다", "keytruda")) and any(t in text for t in ("급여", "신청", "탈락", "환자")):
        return "Keytruda 접근성 논의가 환자부담·우선순위 논쟁의 대표 사례로 재소환되고 있어, 적응증별 unmet need, 대체요법, budget impact framing을 분리해 대응 논리를 점검해야 합니다."
    if any(t in text for t in ("신속심사", "허가", "approval")) and any(t in text for t in ("소아", "희귀", "뇌종양", "신약")):
        return "허가/신속심사 이후 급여 관문이 핵심 병목으로 부각될 가능성이 높아, 소아·희귀질환의 unmet need와 대체치료 부재를 등재 가치 논리로 어떻게 연결할지 선제 검토가 필요합니다."
    if any(t in text for t in ("다발골수종", "cll", "btk", "치료 선택권", "고령")) and "급여" in text:
        return "혈액암 치료 옵션 접근성 이슈로, 환자 세부군·치료순서·기존 급여권 내 대안의 한계를 근거로 급여 확대 필요성이 제기될 수 있습니다. 경쟁 약제 포지셔닝과 실제 사용 가능 환자군을 함께 봐야 합니다."
    if any(t in text for t in ("위험분담", "rsa", "협상", "약가", "급여적정성")):
        return "위험분담·약가협상·급여적정성 재평가와 연결되는 사안이므로, 재정영향 관리 조건과 사후관리 지표가 등재 전략의 핵심 변수가 될 수 있습니다."
    if any(t in text for t in ("급여", "약가")):
        return "급여/약가 관련 신호이므로 대상 환자군, 비교약제, 재정영향, 환자 접근성 개선 폭을 원문 기준으로 분해해 후속 모니터링해야 합니다."
    return ""


def assess_content_completeness(item, *, personas: Iterable | None = None) -> dict:
    """Return deterministic writer/reviewer completeness metadata.

    This is a content-quality checklist, not approval to send. It helps the
    reviewer agents see which fields/facts still require verification.
    """
    missing: list[str] = []
    warnings: list[str] = []
    title = str(_item_get(item, "title", "") or "").strip()
    desc = str(_item_get(item, "description", "") or "").strip()
    url = str(_item_get(item, "publisher_url", "") or _item_get(item, "url", "") or "").strip()
    published_at = str(_item_get(item, "published_at", "") or "").strip()
    source_name = str(_item_get(item, "source_name", "") or "").strip()
    quality = assess_article_quality(item)
    implication = infer_ma_implication(item)
    text = _combined_text(item)

    if not title:
        missing.append("title")
    if not url:
        missing.append("source_url")
    if not published_at:
        missing.append("published_at")
    if not source_name or source_name == "Unknown publisher":
        missing.append("source_name")
    if len(desc) < 30:
        warnings.append("short_description_requires_source_check")
    if quality.get("ma_relevance", 0) >= 4 and not implication:
        missing.append("ma_implication_when_top_signal")
    if "official_cross_check_required" in quality.get("quality_flags", []):
        warnings.append("official_cross_check_required")
    if "unregistered_source_requires_review" in quality.get("quality_flags", []):
        warnings.append("publisher_or_source_registration_required")

    persona_ids: list[str] = []
    for persona in personas or []:
        persona_id = getattr(persona, "persona_id", str(persona))
        persona_ids.append(persona_id)
        if persona_id == "brand_strategy" and not any(t in text for t in ("msd", "엠에스디", "키트루다", "keytruda", "가다실", "gardasil", "린파자", "lynparza", "웰리렉", "welireg", "경쟁", "바이오시밀러")):
            warnings.append("brand_or_competitor_context_weak")
        if persona_id == "policy_watch" and not any(t in text for t in ("심평원", "보건복지부", "건보공단", "약평위", "암질심", "건정심", "고시", "위원회", "재정영향")):
            warnings.append("policy_or_payer_context_weak")

    score = max(0, 100 - (25 * len(set(missing))) - (10 * len(set(warnings))))
    return {
        "score": score,
        "missing": sorted(set(missing)),
        "warnings": sorted(set(warnings)),
        "persona_ids": persona_ids,
    }


def _verification_label(item) -> str:
    quality = assess_article_quality(item)
    if _item_get(item, "source_tier") in {"official", "official_payer", "regulator"}:
        return "Official source identified · facts still checked before final approval"
    if "unregistered_source_requires_review" in quality.get("quality_flags", []):
        return "Media/discovery signal only · publisher verification required"
    if "official_cross_check_required" in quality.get("quality_flags", []):
        return "Publisher signal · official HIRA/MOHW/MFDS cross-check required"
    return "Publisher signal · reviewer confirmation required"


def _executive_snapshot(items: list) -> list[str]:
    top, watch = split_top_signals_and_watchlist(items)
    if not items:
        return ["지난 24시간 기준 dashboard scope에 해당하는 주요 후보가 없습니다."]
    return [
        f"핵심 MA 신호: {len(top)}건 — 상세 제목은 Top Signal 카드에서 확인합니다.",
        f"구성: Top Signal {len(top)}건 / Watchlist {len(watch)}건 — Top은 MA relevance 3+ 기준으로 제한했습니다.",
        "발송 상태: quality-gated draft이며, 공식/원문 확인 전 live send는 차단됩니다.",
    ]


def _followup_actions(items: list) -> list[str]:
    actions: list[str] = []
    for item in items:
        quality = assess_article_quality(item)
        if quality["ma_relevance"] >= 4:
            actions.append(f"{_item_get(item, 'title')}: HIRA/공식자료 또는 원문으로 급여·약가 claim 확인")
        elif any(term in _combined_text(item) for term in ("msd", "엠에스디", "키트루다", "가다실", "린파자", "웰리렉")):
            actions.append(f"{_item_get(item, 'title')}: MSD/경쟁 제품 직접 영향 여부를 brand watch로 재분류")
    return actions[:5] or ["공식/원문 source 확인 후 후속 보도 모니터링"]


def render_news_item_markdown(
    *,
    title: str,
    news_insight: str,
    why_it_matters: str,
    ma_implication: str,
    source: str,
    url: str,
    msd_or_competitor_impact: str = "추가 검토 필요",
    next_watch: str = "공식/원문 source 확인 및 후속 보도 모니터링",
    caveat: str = "Discovery/candidate signal; official facts require source verification.",
) -> str:
    """Legacy renderer kept for tests/API compatibility."""
    implication_block = f"**Korea MA implication:** {ma_implication}\n\n" if ma_implication else ""
    return (
        f"### {title}\n\n"
        f"**News insight:** {news_insight}\n\n"
        f"**Why it matters:** {why_it_matters}\n\n"
        f"{implication_block}"
        f"**MSD / competitor impact:** {msd_or_competitor_impact}\n\n"
        f"**Next watch:** {next_watch}\n\n"
        f"**Sources / caveats:** {source} — {url}  \n{caveat}\n"
    )


def _render_item_markdown(item) -> str:
    title = _item_get(item, "title")
    source = _item_get(item, "source_name")
    url = _item_get(item, "publisher_url") or _item_get(item, "url")
    bullets = "\n".join(f"- {point}" for point in summarize_key_points(item))
    quality = assess_article_quality(item)
    completeness = assess_content_completeness(item)
    insight = infer_news_insight(item)
    implication = infer_ma_implication(item)
    parts = [
        f"### [{quality['priority']}] {title}",
        "",
        f"Review status: {quality['review_status']} · Monitoring importance: {quality.get('monitoring_importance', 0)}/5 · MA relevance: {quality['ma_relevance']}/5 · Flags: {', '.join(quality['quality_flags']) or 'none'}",
        f"Verification: {_verification_label(item)}",
        f"Content completeness: {completeness['score']}/100 · Missing: {', '.join(completeness['missing']) or 'none'} · Warnings: {', '.join(completeness['warnings']) or 'none'}",
        "",
        "**주요 내용**",
        bullets,
        "",
        f"**Why it matters:** {insight}",
        "",
    ]
    if implication:
        parts.extend([f"**Market Access Note:** {implication}", ""])
    parts.extend([
        f"**Sources / caveats:** {source} — {url}  ",
        "Discovery/candidate signal; official facts require source verification.",
        "",
    ])
    return "\n".join(parts)


def render_daily_draft_markdown(
    *,
    items: Iterable,
    keywords: list[str],
    window_label: str = "이전 24시간",
    max_items: int = 5,
) -> str:
    items_list = list(items)[:max_items]
    lines = [
        "# Daily Monitoring Newsletter Draft",
        "",
        f"Monitoring window: {window_label}",
        f"Keywords: {', '.join(keywords) if keywords else 'default MA keywords'}",
        f"Candidate count: {len(items_list)}",
        "",
    ]
    if not items_list:
        lines.append("지난 24시간 기준 주요 후보 뉴스가 없습니다. 키워드 범위를 넓히거나 source를 추가 확인하세요.")
        return "\n".join(lines)
    top, watch = split_top_signals_and_watchlist(items_list)
    snapshot = _executive_snapshot(items_list)
    lines.extend([_section_heading("Executive Snapshot", len(snapshot)), ""])
    lines.extend(f"- {line}" for line in snapshot)
    lines.append("")
    if top:
        lines.extend([_section_heading("Top Monitoring Highlights", len(top)), ""])
        for item in top:
            lines.append(_render_item_markdown(item))
    if watch:
        lines.extend([_section_heading("Watchlist", len(watch)), ""])
        for item in watch:
            lines.append(_render_item_markdown(item))
    followups = _followup_actions(items_list)
    lines.extend([_section_heading("Official Follow-up Checklist", len(followups)), ""])
    lines.extend(f"- {action}" for action in followups)
    return "\n".join(lines)


def _item_html(item) -> str:
    raw_title = str(_item_get(item, "title"))
    title = escape(raw_title)
    source = escape(str(_item_get(item, "source_name")))
    url = escape(str(_item_get(item, "publisher_url") or _item_get(item, "url")))
    score = escape(str(_item_get(item, "score", "")))
    published_at = escape(str(_item_get(item, "published_at", "")) or "발행일 확인 필요")
    matched_keywords = _item_get(item, "matched_keywords", ()) or ()
    keyword_text = escape(", ".join(str(k) for k in matched_keywords) or str(_item_get(item, "keyword", "scope match")))
    quality = assess_article_quality(item)
    completeness = assess_content_completeness(item)
    priority = escape(str(quality["priority"]))
    quality_meta = escape(f"Review {quality['review_status']} · Monitoring {quality.get('monitoring_importance', 0)}/5 · MA relevance {quality['ma_relevance']}/5")
    completeness_meta = escape(f"Completeness {completeness['score']}/100 · Missing: {', '.join(completeness['missing']) or 'none'} · Warnings: {', '.join(completeness['warnings']) or 'none'}")
    flags = escape(", ".join(quality["quality_flags"]) or "none")
    bullets = "".join(f"<li>{escape(point)}</li>" for point in summarize_key_points(item))
    insight = escape(infer_news_insight(item))
    implication = infer_ma_implication(item)
    implication_text = escape(implication or "현재 기사만으로는 별도 MA implication을 쓰지 않습니다. Dashboard monitoring/watch 관점에서만 추적합니다.")
    implication_label = "MA/Business Implication" if implication else "Monitoring Note"
    next_watch = escape(_followup_actions([item])[0])
    verification = escape(_verification_label(item))
    aria_title = escape(raw_title, quote=True)
    return f"""
    <section data-component="article-card" aria-label="기사 카드: {aria_title}" style="margin:0 0 18px;border:1px solid #D7DEE8;border-radius:18px;background:#FFFFFF;box-shadow:0 3px 12px rgba(15,23,42,.07);overflow:hidden;">
      <div style="padding:18px 20px 14px;border-bottom:1px solid #E2E8F0;background:#FBFDFF;">
        <div style="margin-bottom:10px;">
          <span style="display:inline-block;margin-right:6px;padding:4px 9px;border-radius:999px;background:#DBEAFE;color:#1D4ED8;font-size:11px;font-weight:800;">{priority}</span>
          <span style="color:#64748B;font-size:12px;">{source} · {published_at} · score {score}</span>
        </div>
        <a href="{url}" style="display:block;color:#0F172A;font-size:19px;font-weight:850;line-height:1.38;text-decoration:none;">{title}</a>
        <div style="margin-top:12px;color:#64748B;font-size:12px;line-height:1.5;">Scope: {keyword_text} · {quality_meta}</div>
      </div>
      <div style="padding:18px 20px;">
        <div style="margin:0 0 14px;padding:14px;border:1px solid #E2E8F0;border-radius:14px;background:#F8FAFC;">
          <div style="color:#2563EB;font-size:12px;font-weight:800;text-transform:uppercase;letter-spacing:.02em;">핵심 기사 요약</div>
          <ul style="margin:8px 0 0 18px;padding:0;color:#334155;font-size:14px;line-height:1.65;">{bullets}</ul>
        </div>
        <div style="margin:0 0 14px;padding:14px;border:1px solid #BBF7D0;border-radius:14px;background:#F0FDF4;">
          <div style="color:#047857;font-size:12px;font-weight:800;text-transform:uppercase;letter-spacing:.02em;">Why it matters</div>
          <p style="margin:6px 0 0;color:#334155;font-size:14px;line-height:1.65;">{insight}</p>
        </div>
        <div style="margin:0 0 14px;padding:14px;border:1px solid #FED7AA;border-radius:14px;background:#FFF7ED;">
          <div style="color:#B45309;font-size:12px;font-weight:800;text-transform:uppercase;letter-spacing:.02em;">{implication_label}</div>
          <p style="margin:6px 0 0;color:#334155;font-size:14px;line-height:1.65;">{implication_text}</p>
        </div>
        <div style="margin:0 0 14px;padding:14px;border:1px solid #E9D5FF;border-radius:14px;background:#FAF5FF;">
          <div style="color:#7E22CE;font-size:12px;font-weight:800;text-transform:uppercase;letter-spacing:.02em;">다음 확인 포인트</div>
          <p style="margin:6px 0 0;color:#334155;font-size:14px;line-height:1.65;">{next_watch}</p>
        </div>
        <div style="padding-top:2px;color:#64748B;font-size:11px;line-height:1.55;">
          <div>Verification: {verification}</div>
          <div>Quality flags: {flags}</div>
          <div>{completeness_meta}</div>
          <div style="margin-top:10px;"><a href="{url}" style="display:inline-block;padding:8px 12px;border-radius:10px;background:#0F172A;color:#FFFFFF;text-decoration:none;font-size:12px;font-weight:800;">원문 보기</a></div>
          <div style="margin-top:10px;color:#94A3B8;">Discovery/candidate signal. Facts require publisher/official-source verification.</div>
        </div>
      </div>
    </section>
    """


def render_daily_draft_html(
    *,
    items: Iterable,
    keywords: list[str],
    window_label: str = "이전 24시간",
    max_items: int = 5,
) -> str:
    items_list = list(items)[:max_items]
    top, watch = split_top_signals_and_watchlist(items_list)
    snapshot_html = "".join(f"<li>{escape(line)}</li>" for line in _executive_snapshot(items_list))
    followup_html = "".join(f"<li>{escape(action)}</li>" for action in _followup_actions(items_list))
    def render_section(label: str, section_items: list) -> str:
        if not section_items:
            return ""
        cards = "".join(_item_html(item) for item in section_items)
        return f'<h2 style="margin:22px 0 10px;color:#0F172A;font-size:18px;">{escape(label)} ({len(section_items)})</h2>{cards}'
    body = (render_section("Top Monitoring Highlights", top) + render_section("Watchlist", watch)) or '<p style="color:#64748B;">지난 24시간 기준 주요 후보 뉴스가 없습니다.</p>'
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>Daily Monitoring Newsletter Draft</title></head>
<body style="margin:0;padding:24px;background:#FFFFFF;color:#0F172A;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Malgun Gothic',sans-serif;">
  <main style="max-width:760px;margin:0 auto;">
    <header style="margin-bottom:20px;padding:24px;border:1px solid #E2E8F0;border-radius:16px;background:#F8FAFC;">
      <div style="color:#2563EB;font-size:12px;font-weight:800;letter-spacing:.08em;">MA AI DOSSIER · DAILY MONITORING NEWSLETTER</div>
      <h1 style="margin:8px 0 4px;color:#0F172A;font-size:25px;">Daily Monitoring Newsletter</h1>
      <div style="color:#64748B;font-size:13px;">{escape(window_label)} · {escape(', '.join(keywords) if keywords else 'default MA keywords')}</div>
    </header>
    <section style="margin:0 0 18px;padding:18px;border:1px solid #BFDBFE;border-radius:14px;background:#EFF6FF;">
      <h2 style="margin:0 0 8px;color:#1E3A8A;font-size:18px;">Executive Snapshot</h2>
      <ul style="margin:8px 0 0 18px;padding:0;color:#1E293B;font-size:14px;line-height:1.65;">{snapshot_html}</ul>
    </section>
    {body}
    <section style="margin:0 0 18px;padding:18px;border:1px solid #E2E8F0;border-radius:14px;background:#F8FAFC;">
      <h2 style="margin:0 0 8px;color:#0F172A;font-size:18px;">Official Follow-up Checklist</h2>
      <ul style="margin:8px 0 0 18px;padding:0;color:#334155;font-size:14px;line-height:1.65;">{followup_html}</ul>
    </section>
  </main>
</body></html>"""
