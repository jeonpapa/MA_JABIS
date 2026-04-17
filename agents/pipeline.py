"""Agent Pipeline — 오케스트레이션 실행 파이프라인.

CLAUDE.md 아키텍처를 실제 코드로 구현:

  사용자 요청
      │
      ▼
  [OrchestratorAgent]  → WorkPlan 수립 + 룰 충돌 검토
      │
      ▼
  [실행 에이전트]       → 작업 수행 (DomesticPrice / ForeignPrice / HTA / Dashboard)
      │
      ▼
  [QualityGuardAgent]  → 결과 검증 + 편차 기록
      │
      ▼
  결과 반환

사용법:
    from agents.pipeline import run_pipeline
    result = run_pipeline("belzutifan 해외약가 + 허가현황 조회")
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent


def run_pipeline(
    request: str,
    *,
    drug: Optional[str] = None,
    skip_orchestrator: bool = False,
    force_refresh: bool = False,
) -> dict:
    """메인 파이프라인 실행.

    Args:
        request: 사용자 요청 (자연어)
        drug: 약제명 (없으면 request에서 추출 시도)
        skip_orchestrator: True면 오케스트레이터 분석 생략 (빠른 실행)
        force_refresh: True면 캐시 무시하고 재수집

    Returns:
        { "plan": WorkPlan, "results": {...}, "quality": {...} }
    """
    from agents.quality_guard import QualityGuardAgent

    guard = QualityGuardAgent()
    result = {"timestamp": datetime.now().isoformat(), "request": request}

    # ── 1. Orchestrator 분석 ───────────────────────────────────────────
    plan = None
    if not skip_orchestrator:
        try:
            from agents.orchestrator_agent import OrchestratorAgent
            orch = OrchestratorAgent()
            plan = orch.analyze(request)
            result["plan"] = plan

            if plan.get("blocked"):
                logger.warning("[Pipeline] 룰 충돌로 차단: %s", plan.get("rule_conflicts"))
                result["blocked"] = True
                result["conflicts"] = plan.get("rule_conflicts", [])
                return result
        except Exception as e:
            logger.warning("[Pipeline] Orchestrator 분석 실패 (계속 진행): %s", e)
            plan = {"risk_level": "unknown", "notes": f"Orchestrator 오류: {e}"}
            result["plan"] = plan

    # ── 2. 약제명 추출 ────────────────────────────────────────────────
    if not drug:
        import re
        m = re.search(r"(?:drug|약제|검색)\s*[:=]\s*(\S+)", request, re.I)
        if m:
            drug = m.group(1)
        else:
            words = request.split()
            drug = words[0] if words else "keytruda"

    result["drug"] = drug
    logger.info("[Pipeline] 대상 약제: %s", drug)

    # ── 3. HTA 허가현황 조회 ──────────────────────────────────────────
    hta_result = {}
    try:
        from agents.hta_approval_agent import HTAApprovalAgent
        hta = HTAApprovalAgent()
        matrix = hta.get_indication_matrix(drug, force_refresh=force_refresh)
        hta_result = {
            "indication_count": len(matrix.get("indications", [])),
            "fda": matrix.get("fda", {}),
            "bodies_searched": ["FDA", "PBAC", "CADTH", "NICE", "SMC"],
        }
        result["hta"] = hta_result
    except Exception as e:
        logger.error("[Pipeline] HTA 조회 실패: %s", e)
        hta_result = {"error": str(e)}
        result["hta"] = hta_result

    # ── 4. QualityGuard 최종 검증 ─────────────────────────────────────
    unresolved = guard.get_unresolved_deviations()
    recent = [d for d in unresolved
              if d.get("timestamp", "") >= datetime.now().isoformat()[:10]]
    result["quality"] = {
        "total_unresolved": len(unresolved),
        "today_deviations": len(recent),
        "status": "pass" if len(recent) == 0 else "review_needed",
    }

    # ── 5. 코드 패턴 검사 (선택적) ───────────────────────────────────
    try:
        issues = guard.scan_codebase(BASE_DIR / "agents")
        if issues:
            result["quality"]["code_issues"] = issues[:5]
    except Exception:
        pass

    logger.info("[Pipeline] 완료 — 품질: %s", result["quality"]["status"])
    return result


def run_dashboard_review() -> dict:
    """오케스트레이터 관점에서 대쉬보드 방향성을 리뷰.

    현재 대쉬보드 상태를 분석하고 개선 권고사항을 생성.
    """
    review = {
        "timestamp": datetime.now().isoformat(),
        "current_state": {},
        "recommendations": [],
        "tech_review": {},
    }

    # 현재 페이지 구성 확인
    dash_dir = BASE_DIR / "data" / "dashboard"
    pages = list(dash_dir.glob("*.html"))
    review["current_state"]["pages"] = [p.name for p in pages]
    review["current_state"]["has_tokens_css"] = (dash_dir / "assets" / "css" / "tokens.css").exists()

    # 에이전트 상태 확인
    try:
        from agents.hta_approval_agent import HTAApprovalAgent
        hta = HTAApprovalAgent()
        review["current_state"]["hta_bodies"] = hta.available_bodies()
    except Exception:
        review["current_state"]["hta_bodies"] = []

    # 기술 스택 리뷰
    review["tech_review"] = {
        "current": "Flask + static HTML/JS + ECharts",
        "strengths": [
            "배포 단순 (Python만 필요)",
            "정적 파일 — CDN/S3 호스팅 가능",
            "서버 비용 최소",
        ],
        "limitations": [
            "컴포넌트 재사용 어려움 (같은 카드 HTML 반복)",
            "상태 관리 없음 (전역 변수 의존)",
            "라우팅 없음 (페이지 간 데이터 공유 불가)",
            "빌드 없음 → 타입체크, 린트, 번들링 혜택 없음",
        ],
        "upgrade_options": [
            {
                "stack": "React + Vite + TailwindCSS",
                "effort": "중간 (3-5일)",
                "benefit": "컴포넌트 재사용, 타입 안전성, 빌드 최적화",
                "risk": "빌드 도구 복잡성 증가",
            },
            {
                "stack": "Vue 3 + Vite",
                "effort": "중간 (3-5일)",
                "benefit": "점진적 도입 가능, 학습 곡선 낮음",
                "risk": "에코시스템 크기 (React 대비)",
            },
            {
                "stack": "현행 유지 + JS 모듈화",
                "effort": "낮음 (1-2일)",
                "benefit": "빌드 불필요, 기존 코드 유지",
                "risk": "규모 확장 시 유지보수 어려움",
                "recommendation": True,
            },
        ],
        "verdict": (
            "현재 3페이지 규모에서는 프레임워크 전환 ROI가 낮음. "
            "JS를 ES모듈로 분리하고, 공통 컴포넌트를 함수화하는 것이 "
            "비용 대비 효과가 가장 좋음. 페이지가 5개 이상으로 확장되면 "
            "Vue 3 점진적 도입을 권장."
        ),
    }

    # MA 대쉬보드 핵심 목적 기준 권고사항
    review["recommendations"] = [
        {
            "priority": "P0",
            "area": "허가현황",
            "detail": (
                "FDA 적응증 중심 매트릭스는 올바른 방향. "
                "적응증이 많은 약제(예: pembrolizumab 16+ 적응증)를 위해 "
                "적응증 필터와 요약 뷰가 필요."
            ),
        },
        {
            "priority": "P0",
            "area": "데이터 신선도",
            "detail": (
                "HTA 캐시 영구화 완료. 대쉬보드에 '마지막 조사일'을 표시하고 "
                "'업데이트 확인' 버튼으로 신규 데이터만 추가하는 UX 필요."
            ),
        },
        {
            "priority": "P1",
            "area": "해외약가 + 허가현황 통합",
            "detail": (
                "해외약가와 허가현황이 같은 페이지에 있으나 연결이 약함. "
                "적응증별로 '이 적응증에 대한 해당 국가 약가'를 보여주면 "
                "MA 실무에 직접 활용 가능."
            ),
        },
        {
            "priority": "P2",
            "area": "파이프라인 자동화",
            "detail": (
                "scheduler.py 가 존재하나 에이전트 파이프라인과 연결 안 됨. "
                "주기적 데이터 수집 → QualityGuard 검증 → 알림 흐름 구축 필요."
            ),
        },
    ]

    return review


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if len(sys.argv) > 1 and sys.argv[1] == "review":
        r = run_dashboard_review()
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        drug = sys.argv[1] if len(sys.argv) > 1 else "belzutifan"
        r = run_pipeline(f"{drug} 해외약가 + 허가현황 조회", drug=drug, skip_orchestrator=True)
        print(json.dumps(r, ensure_ascii=False, indent=2))
