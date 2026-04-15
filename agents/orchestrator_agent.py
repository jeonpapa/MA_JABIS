"""
OrchestratorAgent — 요청 분석·분배 에이전트

역할:
  사용자 요청을 코딩에 반영하기 전에 기존 룰(CLAUDE.md, rules/*.md)과 비교하고,
  OpenAI API로 코드 영향도를 분석한 후 작업 계획(WorkPlan)을 수립·분배한다.

의존성:
  pip install openai

환경변수 (config/.env):
  OPENAI_API_KEY=sk-...
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
RULES_DIR = BASE_DIR / "agents" / "rules"
CLAUDE_MD = BASE_DIR / "CLAUDE.md"


# ─────────────────────────────────────────────────────────────────────────────
# 1) 룰 로더
# ─────────────────────────────────────────────────────────────────────────────

def _load_rules() -> str:
    """CLAUDE.md + 모든 rules/*.md를 하나의 문자열로 합쳐 반환."""
    parts = []

    if CLAUDE_MD.exists():
        parts.append(f"=== CLAUDE.md ===\n{CLAUDE_MD.read_text(encoding='utf-8')}")

    for rule_file in sorted(RULES_DIR.glob("*.md")):
        parts.append(f"=== {rule_file.name} ===\n{rule_file.read_text(encoding='utf-8')}")

    return "\n\n".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# 2) OpenAI 분석 (Codex / GPT-4o)
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """
당신은 MA AI Dossier의 **룰 게이트키퍼 겸 작업 분배자**(Orchestrator)입니다.

책임:
1) 사용자 요청을 CLAUDE.md + agents/rules/*.md 원문과 대조.
2) 룰 충돌이 있으면 **진행 전 차단**하고 conflict 내역 명시.
3) 작업 계획을 수립하고, 작업 대상 에이전트를 지정.
4) 반드시 마지막 단계에 **ReviewAgent 검증**을 포함시킬 것.

출력은 반드시 아래 JSON 형식만:
{
  "request_summary": "요청 요약 (1~2문장)",
  "rule_conflicts": ["충돌 룰 목록 (없으면 빈 배열)"],
  "blocked": true | false,
  "affected_files": ["영향 파일 목록"],
  "risk_level": "low | medium | high",
  "work_order": [
    {"step": 1, "target": "파일 또는 에이전트명", "task": "작업 내용"}
  ],
  "review_plan": "ReviewAgent가 결과를 어떤 기준으로 검증할지 (구체적으로)",
  "validation_check": "완료 검증 방법",
  "notes": "추가 주의사항 (없으면 빈 문자열)"
}

원칙:
- rule_conflicts 가 존재하면 blocked=true. 사용자 추가 확인 없이는 진행 불가.
- work_order 마지막 step은 항상 "ReviewAgent가 결과 검증" 이어야 함.
"""


def analyze_with_openai(user_request: str, rules_context: str, model: str = "gpt-4o") -> dict:
    """
    OpenAI API로 요청 영향도 분석.
    API 키 없거나 실패 시 기본 WorkPlan 반환.
    """
    try:
        from openai import OpenAI
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))

        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": (
                    f"[프로젝트 규칙]\n{rules_context}\n\n"
                    f"[사용자 요청]\n{user_request}"
                )},
            ],
            temperature=0.2,
            max_tokens=1500,
        )

        raw = response.choices[0].message.content.strip()
        # JSON 블록 추출
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw)

    except ImportError:
        logger.warning("[Orchestrator] openai 패키지 미설치 — 기본 WorkPlan 반환")
    except Exception as e:
        logger.warning("[Orchestrator] OpenAI 분석 실패: %s", e)

    # 폴백: 기본 WorkPlan
    return {
        "request_summary": user_request[:100],
        "rule_conflicts": [],
        "affected_files": ["미확인 (OpenAI 분석 실패)"],
        "risk_level": "medium",
        "work_order": [{"step": 1, "target": "개발자", "task": "수동 검토 필요"}],
        "validation_check": "Keytruda 가격 validation 수행",
        "notes": "OpenAI API 키를 config/.env에 OPENAI_API_KEY로 설정하세요.",
    }


# ─────────────────────────────────────────────────────────────────────────────
# 3) OrchestratorAgent 메인 클래스
# ─────────────────────────────────────────────────────────────────────────────

class OrchestratorAgent:
    """
    사용자 요청을 분석하고 작업을 분배하는 에이전트.

    사용 예시:
        agent = OrchestratorAgent()
        plan = agent.analyze("UK MIMS 스크레이퍼의 가격 파싱 로직을 개선해줘")
        agent.print_plan(plan)
    """

    def __init__(self, openai_model: str = "gpt-4o"):
        self.model = openai_model
        self._load_env()

    def _load_env(self):
        env_path = BASE_DIR / "config" / ".env"
        if env_path.exists():
            try:
                from dotenv import load_dotenv
                load_dotenv(env_path, override=False)
            except ImportError:
                pass

    def analyze(self, user_request: str) -> dict:
        """
        요청을 분석하고 WorkPlan을 반환.

        Args:
            user_request: 사용자의 자연어 요청

        Returns:
            WorkPlan dict
        """
        logger.info("[Orchestrator] 요청 분석 시작: %s", user_request[:80])

        # 1) 룰 로드
        rules = _load_rules()

        # 2) OpenAI로 영향도 분석
        plan = analyze_with_openai(user_request, rules, self.model)

        # 3) 타임스탬프·요청 원문 추가
        plan["timestamp"] = datetime.now().isoformat()
        plan["original_request"] = user_request

        # 4) 로그
        logger.info(
            "[Orchestrator] 분석 완료 — 위험도: %s, 영향 파일: %d개",
            plan.get("risk_level", "?"),
            len(plan.get("affected_files", [])),
        )

        return plan

    def print_plan(self, plan: dict) -> None:
        """WorkPlan을 사람이 읽기 쉬운 형식으로 출력."""
        print("\n" + "=" * 60)
        print("[ OrchestratorAgent WorkPlan ]")
        print("=" * 60)
        print(f"요청 요약  : {plan.get('request_summary', '')}")
        print(f"위험 수준  : {plan.get('risk_level', '').upper()}")
        print(f"분석 시각  : {plan.get('timestamp', '')}")

        conflicts = plan.get("rule_conflicts", [])
        if conflicts:
            print(f"\n⚠️  규칙 충돌:")
            for c in conflicts:
                print(f"   - {c}")
        else:
            print("\n✅ 규칙 충돌 없음")

        print(f"\n영향 파일:")
        for f in plan.get("affected_files", []):
            print(f"   - {f}")

        print(f"\n작업 순서:")
        for step in plan.get("work_order", []):
            print(f"   {step['step']}. [{step['target']}] {step['task']}")

        print(f"\n검증 방법 : {plan.get('validation_check', '')}")
        notes = plan.get("notes", "")
        if notes:
            print(f"주의사항  : {notes}")
        print("=" * 60 + "\n")

    def approve_and_dispatch(self, plan: dict) -> bool:
        """
        WorkPlan을 검토하고 실행 승인.
        blocked=true 또는 high risk 시 사용자 확인 요청.

        Returns:
            True = 진행, False = 취소
        """
        self.print_plan(plan)

        if plan.get("blocked"):
            print("🚫 룰 충돌로 blocked=true. 사용자 확인 없이는 진행 불가. 진행? (y/n): ", end="")
            return input().strip().lower() == "y"

        if plan.get("risk_level") == "high":
            print("⚠️  위험 수준이 'high'입니다. 진행하시겠습니까? (y/n): ", end="")
            return input().strip().lower() == "y"

        return True

    def handoff_to_review(self, original_request: dict, agent_result: dict,
                          rules_text: str) -> dict:
        """
        작업 완료 후 ReviewAgent로 최종 검증 핸드오프.
        """
        from agents.review_agent import ReviewAgent
        reviewer = ReviewAgent(openai_model=self.model)
        return reviewer.review_price_change_reason(
            original_request=original_request,
            mi_result=agent_result,
            mi_rules=rules_text,
        )

    def save_plan(self, plan: dict, output_dir: Optional[Path] = None) -> Path:
        """WorkPlan을 JSON 파일로 저장."""
        save_dir = output_dir or BASE_DIR / "quality_guard"
        save_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = save_dir / f"workplan_{ts}.json"
        path.write_text(json.dumps(plan, ensure_ascii=False, indent=2))
        logger.info("[Orchestrator] WorkPlan 저장: %s", path)
        return path


# ─────────────────────────────────────────────────────────────────────────────
# CLI 실행
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    request = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else (
        "US Micromedex 스크레이퍼를 추가하고 싶어. "
        "Micromedex RED BOOK에서 약가를 가져오는 방식으로 구현해줘."
    )

    agent = OrchestratorAgent()
    plan = agent.analyze(request)
    agent.print_plan(plan)
    agent.save_plan(plan)
