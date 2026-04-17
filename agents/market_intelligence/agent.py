"""MarketIntelligenceAgent — 약가 변동 사유 분석 메인 클래스.

주요 진입점: `analyze_price_change()` — 2단계 엔진(Perplexity → Naver+GPT-4o),
심층 리서치 에스컬레이션, rule enforcement, 캐시 저장.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from .llm import (
    openai_analyze,
    perplexity_analyze,
    synthesize_reason,
)
from .media import MEDIA_DB, score_source
from .mechanisms import classify_mechanism
from .naver import collect_news
from .rules_engine import BASE_DIR, MI_RULES_TEXT, enforce_rules, window_bounds

logger = logging.getLogger(__name__)


def apply_calibrated_weights() -> None:
    """MediaCalibrator 최신 결과가 있으면 MEDIA_DB weight 를 인메모리 갱신."""
    try:
        from agents.media_calibrator import get_calibrated_weights
        calibrated = get_calibrated_weights()
        if not calibrated:
            return
        updated = []
        for name, new_w in calibrated.items():
            if name in MEDIA_DB:
                old_w = MEDIA_DB[name]["weight"]
                if abs(new_w - old_w) > 0.01:
                    MEDIA_DB[name]["weight"] = new_w
                    updated.append(f"{name}: {old_w:.2f}→{new_w:.2f}")
        if updated:
            logger.info("[MI Agent] 캘리브레이션 가중치 적용: %s", ", ".join(updated))
        else:
            logger.debug("[MI Agent] 캘리브레이션 적용 — 변동 없음")
    except Exception as e:
        logger.debug("[MI Agent] 캘리브레이션 로드 건너뜀: %s", e)


class MarketIntelligenceAgent:
    """한국 의약전문 뉴스 매체 기반 약가 변동 사유 분석.

    초기화 시 최신 MediaCalibrator 결과를 자동으로 MEDIA_DB 에 반영.

    사용 예시:
        agent = MarketIntelligenceAgent()
        result = agent.analyze_price_change(
            drug_ko="키트루다주",
            ingredient_ko="펨브롤리주맙,유전자재조합",
            change_date="2022.03.01",
            delta_pct=-25.61,
        )
    """

    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = cache_dir or BASE_DIR / "data" / "dashboard" / "reason_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        apply_calibrated_weights()

    def _cache_path(self, drug_ko: str, change_date: str) -> Path:
        key = re.sub(r"[^\w]", "_", f"MI_{drug_ko}_{change_date}")
        return self.cache_dir / f"{key}.json"

    def get_cached(self, drug_ko: str, change_date: str) -> Optional[dict]:
        path = self._cache_path(drug_ko, change_date)
        if path.exists():
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            data["cached"] = True
            return data
        return None

    def save_cache(self, drug_ko: str, change_date: str, result: dict) -> None:
        path = self._cache_path(drug_ko, change_date)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

    def analyze_price_change(
        self,
        drug_ko: str,
        ingredient_ko: str,
        change_date: str,
        delta_pct: Optional[float] = None,
        drug_en: str = "",
        ingredient_en: str = "",
        force_refresh: bool = False,
    ) -> dict:
        """2단계 엔진: Perplexity → Naver+GPT-4o 폴백 → 캐시 저장."""
        if not force_refresh:
            cached = self.get_cached(drug_ko, change_date)
            if cached:
                return cached

        logger.info("[MI Agent] 분석 시작: %s %s (δ%s%%)", drug_ko, change_date, delta_pct)

        try:
            dt = datetime.strptime(change_date, "%Y.%m.%d")
        except ValueError:
            try:
                dt = datetime.strptime(change_date[:7], "%Y.%m")
            except ValueError:
                dt = datetime.now()

        # ── 1단계: Perplexity sonar-pro ─────────────────────────────────────
        result = perplexity_analyze(drug_ko, ingredient_ko, change_date, delta_pct)

        if result:
            refs = result.get("references", [])
            tier_counts: dict = {}
            for ref in refs:
                sc = score_source(ref.get("url", ""))
                t  = sc["tier"]
                tier_counts[t] = tier_counts.get(t, 0) + 1

            result["analysis_meta"] = {
                "source":            "perplexity-sonar-pro",
                "total_articles":    len(refs),
                "tier_a_count":      tier_counts.get("A", 0),
                "tier_b_count":      tier_counts.get("B", 0),
                "tier_c_count":      tier_counts.get("C", 0),
                "detected_mechanisms": (
                    [result["mechanism_label"]] if result.get("mechanism_label") else []
                ),
                "top_media": [
                    {"media": r.get("media", "기타"), "weight": r.get("weight", 0.5)}
                    for r in sorted(refs, key=lambda x: -x.get("weight", 0))[:5]
                ],
            }
            result = self._deep_research_if_low(result, drug_ko, ingredient_ko, change_date, delta_pct)
            result = enforce_rules(result, change_date)

            weak = (
                len(result.get("references") or []) <= 1
                or (result.get("mechanism") or "unknown") in ("unknown", "", None)
            )
            if weak:
                logger.info("[MI Agent] Perplexity weak → Naver 보강 시도")
                result = self._augment_with_naver(result, drug_ko, ingredient_ko, change_date, delta_pct)
                result = enforce_rules(result, change_date)

            result["cached"] = False
            self.save_cache(drug_ko, change_date, result)
            return result

        # ── 2단계: Naver + GPT-4o 폴백 ──────────────────────────────────────
        logger.info("[MI Agent] Naver+GPT-4o 폴백 실행")
        articles   = collect_news(drug_ko, ingredient_ko, dt)
        all_text   = " ".join(f"{a['title']} {a.get('snippet','')}" for a in articles)
        mechanisms = classify_mechanism(all_text)
        logger.info("[MI Agent] 탐지 기전: %s", [m["label"] for m in mechanisms])

        result = openai_analyze(drug_ko, change_date, delta_pct, articles, mechanisms)

        tier_counts = {}
        for a in articles:
            t = a.get("tier", "other")
            tier_counts[t] = tier_counts.get(t, 0) + 1

        result["analysis_meta"] = {
            "source":            "naver+gpt-4o",
            "total_articles":    len(articles),
            "tier_a_count":      tier_counts.get("A", 0),
            "tier_b_count":      tier_counts.get("B", 0),
            "tier_c_count":      tier_counts.get("C", 0),
            "detected_mechanisms": [m["label"] for m in mechanisms],
            "top_media": [
                {"media": a["media_name"], "weight": a["weight"]}
                for a in sorted(articles, key=lambda x: -x.get("weight", 0))[:5]
            ],
        }
        result = enforce_rules(result, change_date)
        result = self._deep_research_if_low(result, drug_ko, ingredient_ko, change_date, delta_pct)
        result = enforce_rules(result, change_date)
        result["cached"] = False
        self.save_cache(drug_ko, change_date, result)
        return result

    def _augment_with_naver(
        self,
        result: dict,
        drug_ko: str,
        ingredient_ko: str,
        change_date: str,
        delta_pct: Optional[float],
    ) -> dict:
        """Perplexity weak refs(0~1건) 시 Naver 뉴스로 보강."""
        try:
            dt = datetime.strptime(change_date, "%Y.%m.%d")
        except Exception:
            try:
                dt = datetime.strptime(change_date[:7], "%Y.%m")
            except Exception:
                return result

        wf, wt, _, _ = window_bounds(change_date, months=6)
        articles = collect_news(drug_ko, ingredient_ko, dt)
        if not articles:
            logger.info("[MI Agent] Naver 보강: 기사 0건")
            return result

        in_window = []
        for a in articles:
            pub = (a.get("published_at") or "").strip()
            if not pub:
                continue
            try:
                pd = datetime.strptime(pub, "%Y.%m.%d")
            except Exception:
                continue
            if wf and wt and (wf <= pd <= wt):
                in_window.append(a)

        logger.info("[MI Agent] Naver 보강: 전체 %d / 윈도우 내 %d", len(articles), len(in_window))
        if not in_window:
            return result

        all_text   = " ".join(f"{a['title']} {a.get('snippet','')}" for a in in_window)
        mechanisms = classify_mechanism(all_text)
        naver_result = openai_analyze(drug_ko, change_date, delta_pct, in_window, mechanisms)
        if not naver_result or not naver_result.get("references"):
            return result

        merged_refs = list(naver_result.get("references", []))
        existing = {r.get("url") for r in merged_refs}
        for r in result.get("references", []) or []:
            if r.get("url") and r["url"] not in existing:
                merged_refs.append(r)
                existing.add(r["url"])

        synthesized = synthesize_reason(
            drug_ko=drug_ko,
            change_date=change_date,
            delta_pct=delta_pct,
            primary_reason=result.get("reason", ""),
            deep_answer=naver_result.get("reason", ""),
        )
        result["reason"]           = synthesized or naver_result.get("reason") or result.get("reason")
        result["references"]       = merged_refs
        if (naver_result.get("mechanism") or "unknown") not in ("unknown", "", None):
            result["mechanism"]       = naver_result["mechanism"]
            result["mechanism_label"] = naver_result.get("mechanism_label") or result.get("mechanism_label")
            if (result.get("confidence") or "low") == "low":
                result["confidence"] = "medium"
        result["evidence_summary"] = naver_result.get("evidence_summary") or result.get("evidence_summary", "")
        existing_notes = (result.get("notes") or "").strip()
        result["notes"] = (
            f"{existing_notes} · [Naver 보강] 윈도우 내 기사 {len(in_window)}건 채택"
            if existing_notes else f"[Naver 보강] 윈도우 내 기사 {len(in_window)}건 채택"
        )
        return result

    def _deep_research_if_low(
        self,
        result: dict,
        drug_ko: str,
        ingredient_ko: str,
        change_date: str,
        delta_pct: Optional[float],
    ) -> dict:
        """confidence=low 또는 mechanism=unknown 시 Perplexity 시장조사 에스컬레이션."""
        confidence = (result.get("confidence") or "").lower()
        mechanism  = (result.get("mechanism") or "").lower()
        if confidence != "low" and mechanism != "unknown":
            return result

        try:
            from agents.perplexity_research_agent import research
            logger.info("[MI Agent] 심층 리서치 에스컬레이션 (confidence=%s, mech=%s)",
                        confidence, mechanism)

            short_ing  = (ingredient_ko or drug_ko).split(",")[0].strip()
            brand_base = re.sub(r"(주|정|캡슐|액|주사|시럽)$", "", drug_ko).strip()
            delta_str  = f"{delta_pct:+.2f}%" if delta_pct is not None else "미상"
            try:
                dt = datetime.strptime(change_date, "%Y.%m.%d")
            except Exception:
                dt = datetime.strptime(change_date[:7], "%Y.%m")
            year  = dt.year
            month = dt.month
            window_from = f"{year}.{max(1, month-6):02d}"
            window_to   = f"{year}.{min(12, month+6):02d}" if month+6 <= 12 else f"{year+1}.{(month+6)%12:02d}"

            strict_query = f"""[조사 대상]
- 약제명: {drug_ko} (브랜드: {brand_base})
- 성분명: {short_ing}
- 약가 변동 시점: {change_date}
- 변동률: {delta_str}
- 국가: 대한민국
- 윈도우: {window_from} ~ {window_to} (이 범위 밖 사실 인용 금지)

[반드시 아래 룰 원문을 그대로 준수할 것]
{MI_RULES_TEXT}

[답변 구조 한국어 500자 이내]
[기전 판정] / [핵심 근거 (윈도우 내, 매체·일자)] / [보완 설명] / [출처 URL]
"""
            deep = research(
                strict_query,
                mode="pro",
                drug_name=drug_ko,
                country="대한민국",
                save=True,
                temperature=0.1,
            )
            deep_answer = (deep.get("answer") or "").strip()
            if not deep_answer:
                return result

            original_reason = result.get("reason", "")
            synthesized = synthesize_reason(
                drug_ko=drug_ko,
                change_date=change_date,
                delta_pct=delta_pct,
                primary_reason=original_reason,
                deep_answer=deep_answer,
            )
            if synthesized:
                result["reason"] = synthesized
            else:
                result["reason"] = original_reason or deep_answer
            result["deep_research"] = {
                "source":    "perplexity-sonar-pro-research",
                "model":     deep.get("model"),
                "citations": deep.get("citations", []),
                "created_at": deep.get("created_at"),
            }
            existing = {r.get("url", "") for r in result.get("references", [])}
            for url in deep.get("citations", []):
                if url and url not in existing:
                    sc = score_source(url)
                    result.setdefault("references", []).append({
                        "title":  url.split("/")[-1][:60] or url,
                        "url":    url,
                        "media":  sc["media_name"],
                        "weight": sc["weight"],
                    })
            if confidence == "low":
                result["confidence"] = "medium"
            logger.info("[MI Agent] 심층 리서치 완료 — citations: %d건",
                        len(deep.get("citations", [])))
        except Exception as e:
            logger.warning("[MI Agent] 심층 리서치 실패: %s", e)
        return result

    def get_media_leaderboard(self) -> list:
        """매체 신뢰도 리더보드 (가중치 정렬, 캘리브레이션 날짜 포함)."""
        try:
            from agents.media_calibrator import load_latest_calibration
            cal = load_latest_calibration()
            last_calibrated = cal["calibrated_at"][:10] if cal else "미보정"
        except Exception:
            last_calibrated = "미보정"

        rows = sorted(
            [{"media": k, **{f: v for f, v in info.items() if f != "domain"}}
             for k, info in MEDIA_DB.items()],
            key=lambda x: -x["weight"]
        )
        return {"last_calibrated": last_calibrated, "leaderboard": rows}
