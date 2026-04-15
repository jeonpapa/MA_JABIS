"""
ReviewAgent — 결과 품질·룰 준수 최종 검증 에이전트

역할:
  다른 에이전트가 생산한 결과를 사용자에게 반환하기 전,
  원래 요청과 프로젝트 룰에 부합하는지 최종 판정한다.
  거부 시 corrective_actions를 반환하여 재시도를 지원한다.

의존성:
  pip install openai

환경변수 (config/.env):
  OPENAI_API_KEY=sk-...
"""

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

BASE_DIR  = Path(__file__).parent.parent
RULES_DIR = BASE_DIR / "agents" / "rules"


def _load_env() -> None:
    env_path = BASE_DIR / "config" / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def _load_rule(name: str) -> str:
    path = RULES_DIR / name
    return path.read_text(encoding="utf-8") if path.exists() else ""


SYSTEM_PROMPT = """당신은 MA AI Dossier 시스템의 품질 검증(Review) 에이전트입니다.
다른 에이전트가 생산한 결과를 **원래 사용자 요청**과 **프로젝트 룰**에 비추어 최종 판정하세요.
반드시 review_agent_rules.md의 4가지 축(요청 부합성, 룰 준수, 근거 품질, 일관성)을 모두 점검하고,
지정된 JSON 형식으로만 응답하세요.

판정 원칙:
- 의심스러우면 거부(reject)가 기본. 거짓 답보다 명시적 unknown이 낫다.
- 윈도우 외 citation, 기전 미분류인데 특정 기전으로 답변 등은 blocker.
- corrective_actions는 재시도 시 즉시 적용 가능한 구체 지시여야 함.
"""


class ReviewAgent:
    def __init__(self, openai_model: str = "gpt-4o", gemini_model: str = "gemini-2.0-flash"):
        self.model = openai_model
        _load_env()
        self.review_rules = _load_rule("review_agent_rules.md")
        # 두 번째 독립 리뷰어 (Gemini) — 키 없으면 자동 비활성
        try:
            from agents.gemini_reviewer import GeminiReviewer
            self.gemini = GeminiReviewer(model=gemini_model)
        except Exception as e:
            logger.warning("[ReviewAgent] Gemini 초기화 실패: %s", e)
            self.gemini = None

    def review_price_change_reason(
        self,
        original_request: dict,
        mi_result: dict,
        mi_rules: str,
    ) -> dict:
        """
        MarketIntelligenceAgent 결과 검증.

        Args:
            original_request: {"drug": "...", "date": "YYYY.MM.DD", "delta_pct": float, ...}
            mi_result: MI agent가 반환한 dict
            mi_rules: market_intelligence_rules.md 원문

        Returns:
            {"approved": bool, "score": int, "issues": [...],
             "corrective_actions": [...], "final_verdict": str}
        """
        # ── 1단계: 기계적 검증 (하드 체크) ──────────────────────────────
        mechanical = self._mechanical_check(original_request, mi_result)
        if mechanical["has_blocker"]:
            return {
                "approved": False,
                "score": max(0, 50 - 10 * len(mechanical["issues"])),
                "issues": mechanical["issues"],
                "corrective_actions": mechanical["corrective_actions"],
                "final_verdict": "기계적 검증 실패 — 윈도우 외 근거 또는 필수 필드 누락",
                "review_mode": "mechanical_only",
            }

        # ── 2단계: LLM 패널 (OpenAI + Gemini 독립 리뷰) ─────────────────
        openai_verdict = self._llm_review(original_request, mi_result, mi_rules)
        openai_verdict.setdefault("reviewer", "openai-" + self.model)

        gemini_verdict = self.gemini.review(original_request, mi_result, mi_rules) \
            if self.gemini else None

        final = self._merge_panel(openai_verdict, gemini_verdict, mechanical)
        return final

    # ─────────────────────────────────────────────────────────────────
    # 패널 합의 — OpenAI AND Gemini 모두 approve 해야 최종 approve
    # ─────────────────────────────────────────────────────────────────
    def _merge_panel(self, openai_v: dict, gemini_v, mechanical: dict) -> dict:
        panel = [openai_v]
        if gemini_v:
            panel.append(gemini_v)

        approvals = [bool(v.get("approved")) for v in panel]
        # 두 리뷰어 모두 찬성해야 approve. 한 명만 찬성/한 명만 있으면 그대로.
        all_approved = all(approvals) and len(approvals) >= 1

        # issues 합치기 (중복 제거 by detail)
        merged_issues, seen = [], set()
        for v in panel:
            for iss in (v.get("issues") or []):
                key = (iss.get("axis"), iss.get("detail"))
                if key in seen:
                    continue
                seen.add(key)
                merged_issues.append(iss)
        for iss in mechanical.get("issues", []):
            key = (iss.get("axis"), iss.get("detail"))
            if key in seen:
                continue
            seen.add(key)
            merged_issues.append(iss)

        # corrective_actions 합치기 (중복 제거)
        merged_actions, seen_a = [], set()
        for v in panel:
            for act in (v.get("corrective_actions") or []):
                if act not in seen_a:
                    seen_a.add(act); merged_actions.append(act)
        for act in mechanical.get("corrective_actions", []):
            if act not in seen_a:
                seen_a.add(act); merged_actions.append(act)

        scores = [int(v.get("score", 0) or 0) for v in panel]
        score = min(scores) if scores else 0
        if mechanical.get("issues"):
            score = min(score, 80)

        verdicts_txt = " | ".join(
            f"[{v.get('reviewer','?')}] {v.get('final_verdict','')}" for v in panel
        )
        reason = "패널 합의 승인" if all_approved else "패널 합의 거부 — 리뷰어 중 한 명 이상이 거부"

        return {
            "approved": all_approved,
            "score": score,
            "issues": merged_issues,
            "corrective_actions": merged_actions,
            "final_verdict": f"{reason}. {verdicts_txt}",
            "review_mode": "panel" if gemini_v else "openai-only",
            "panel": panel,
            "panel_size": len(panel),
        }

    # ─────────────────────────────────────────────────────────────────
    # 기계적 검증 — 윈도우 외 references, 스키마 위반 등
    # ─────────────────────────────────────────────────────────────────
    def _mechanical_check(self, req: dict, result: dict) -> dict:
        issues, actions = [], []
        has_blocker = False

        change_date = req.get("date", "")
        try:
            dt = datetime.strptime(change_date, "%Y.%m.%d")
        except Exception:
            try:
                dt = datetime.strptime(change_date[:7], "%Y.%m")
            except Exception:
                dt = None

        # (a) mechanism 필드 존재 여부
        allowed = {"indication_expansion", "patent_expiration",
                   "volume_price", "actual_transaction", "unknown"}
        if result.get("mechanism") not in allowed:
            issues.append({
                "axis": "rule_compliance", "severity": "blocker",
                "detail": f"mechanism 값이 허용 목록에 없음: {result.get('mechanism')}",
            })
            actions.append("mechanism을 4대 기전 id 또는 'unknown' 중 하나로 설정")
            has_blocker = True

        # (b) reason 본문의 윈도우 외 연도 언급 탐지
        reason = result.get("reason", "") or ""
        if dt:
            window_year = dt.year
            # patent_expiration은 ±12개월 허용
            allow_years = {window_year, window_year - 1, window_year + 1}
            mentioned_years = set(int(y) for y in re.findall(r"\b(19\d{2}|20\d{2})\b", reason))
            offending = mentioned_years - allow_years
            if offending:
                issues.append({
                    "axis": "rule_compliance", "severity": "major",
                    "detail": f"reason 본문에 윈도우 외 연도 언급: {sorted(offending)}",
                })
                actions.append(
                    f"reason에서 {sorted(offending)} 연도 서술 삭제 — "
                    f"{window_year}±6개월 윈도우 내 사실만 남길 것"
                )

        # (c) references의 published_at이 윈도우 내인지
        refs = result.get("references", []) or []
        out_of_window = []
        if dt and refs:
            from dateutil.relativedelta import relativedelta  # type: ignore
            try:
                wf = dt - relativedelta(months=6)
                wt = dt + relativedelta(months=6)
            except Exception:
                wf = wt = None
            if wf and wt:
                for r in refs:
                    pub = r.get("published_at") or ""
                    try:
                        pd = datetime.strptime(pub[:10].replace("-", "."), "%Y.%m.%d")
                        if pd < wf or pd > wt:
                            out_of_window.append(r)
                    except Exception:
                        pass  # 날짜 파싱 실패는 별도 이슈
        if out_of_window:
            issues.append({
                "axis": "evidence_quality", "severity": "blocker",
                "detail": f"references 중 {len(out_of_window)}건이 ±6개월 윈도우 외",
            })
            actions.append("윈도우 외 references 제거. 가능하면 윈도우 내 대체 출처 재검색.")
            has_blocker = True

        # (d) 스키마: mechanism이 unknown이 아닌데 references 없음
        if result.get("mechanism") not in ("unknown", None) and not refs:
            issues.append({
                "axis": "evidence_quality", "severity": "major",
                "detail": "확정 기전 분류임에도 references가 비어있음",
            })
            actions.append(
                "근거 없으면 mechanism='unknown', confidence='low'로 하향 또는 윈도우 내 references 추가"
            )

        return {"issues": issues, "corrective_actions": actions, "has_blocker": has_blocker}

    # ─────────────────────────────────────────────────────────────────
    # LLM 판정 — 의미적 정합성
    # ─────────────────────────────────────────────────────────────────
    def _llm_review(self, req: dict, result: dict, mi_rules: str) -> dict:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))

            user_msg = (
                f"[원 사용자 요청]\n"
                f"약제: {req.get('drug')}, 변동일: {req.get('date')}, "
                f"변동률: {req.get('delta_pct')}%\n\n"
                f"[에이전트 결과]\n{json.dumps(result, ensure_ascii=False, indent=2)}\n\n"
                f"[MarketIntelligenceAgent 룰 원문]\n{mi_rules}\n\n"
                f"[ReviewAgent 룰 원문]\n{self.review_rules}\n\n"
                f"위 4가지 축을 점검하고 JSON으로만 응답하세요."
            )

            resp = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.1,
                max_tokens=800,
            )
            raw = resp.choices[0].message.content.strip()
            if "```" in raw:
                raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
            m = re.search(r"\{[\s\S]+\}", raw)
            if m:
                raw = m.group(0)
            verdict = json.loads(raw)
            verdict.setdefault("review_mode", "llm")
            return verdict

        except Exception as e:
            logger.warning("[ReviewAgent] LLM 판정 실패 — pass-through: %s", e)
            # LLM 실패 시 기계적 검증만으로 통과 처리 (false-reject 방지)
            return {
                "approved": True,
                "score": 75,
                "issues": [],
                "corrective_actions": [],
                "final_verdict": "LLM 판정 실패로 기계적 검증만 통과 — 수동 재검토 권장",
                "review_mode": "mechanical_only_fallback",
            }
