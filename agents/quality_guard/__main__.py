"""CLI entrypoint — `python -m agents.quality_guard [scan|report|summary|review]`."""
from __future__ import annotations

import logging
import sys

from .agent import QualityGuardAgent


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    guard = QualityGuardAgent()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"

    if cmd == "scan":
        issues = guard.scan_codebase()
        print(f"\n발견된 문제: {len(issues)}건")
        for issue in issues:
            print(f"  - {issue}")

    elif cmd == "report":
        path = guard.generate_daily_report()
        print(f"보고서 생성: {path}")
        guard.print_summary()

    elif cmd == "summary":
        guard.print_summary()

    elif cmd == "review":
        result = guard.review_codebase()
        print(f"\n리뷰 보고서: {result['report_path']}")
        print(f"  코드 위반: {len(result['code_issues'])}건")
        print(f"  규칙 drift: {len(result['rule_drifts'])}건")
        print(f"  MFDS 회귀: {len(result['mfds_regressions'])}건")
        print(f"  개선 제안: {len(result['suggestions'])}건")
        if result["mfds_regressions"]:
            print("\n❌ MFDS baseline 회귀 발견 — 즉시 확인 요망:")
            for m in result["mfds_regressions"]:
                print(f"   {m['indication_id']}: expected {m['expected']} / actual {m.get('actual')}")
        for s in result["suggestions"]:
            print(f"  💡 {s}")

    else:
        print("사용법: python -m agents.quality_guard [scan|report|summary|review]")


if __name__ == "__main__":
    main()
