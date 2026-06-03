"""CLI: `python -m agents.rule_compliance [--write-report] [--memory-dir PATH]`"""
from __future__ import annotations

import argparse
import logging
import sys

from .agent import RuleComplianceAgent

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-report", action="store_true", help="quality_guard/compliance_YYYY-MM-DD.md 로 저장")
    ap.add_argument("--memory-dir", type=str, default=None, help="메모리 디렉터리 override")
    ap.add_argument("--fail-on-fail", action="store_true", help="FAIL 발생 시 exit code 1")
    args = ap.parse_args()

    from pathlib import Path
    mem = Path(args.memory_dir) if args.memory_dir else None
    agent = RuleComplianceAgent(memory_dir=mem)
    results = agent.audit()

    for line in agent.summary_lines(results):
        print(line)

    passes = sum(1 for r in results if r.status == "pass")
    fails  = sum(1 for r in results if r.status == "fail")
    skips  = sum(1 for r in results if r.status == "skip")
    print(f"\n요약 — ✅ {passes} / ❌ {fails} / ⏭ {skips}")

    if args.write_report:
        path = agent.write_report(results)
        print(f"보고서: {path}")

    if args.fail_on_fail and fails > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
