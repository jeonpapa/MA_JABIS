"""Dashboard v2 Playwright E2E sweep — 순차 실행 + 요약.

전제: localhost:3000 (Vite) + localhost:5001 (Flask) 동시 구동.
사용: python3 data/dashboard_v2/tests/e2e/run_all.py
"""
from __future__ import annotations

import importlib.util
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ORDER = [
    "smoke_auth",
    "smoke_home",
    "smoke_domestic",
    "smoke_international",
    "smoke_market_share",
    "smoke_admin_market_share",
    "smoke_brand_traffic",
    "smoke_pipeline",
    "smoke_reimbursement",
    "smoke_competitor_trends",
    "smoke_workbench",
    "smoke_daily_mailing",
]


def _load(name: str):
    path = HERE / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    results: list[tuple[str, bool, float, str]] = []
    for name in ORDER:
        print(f"\n{'=' * 70}\n▶ {name}\n{'=' * 70}")
        t0 = time.time()
        try:
            mod = _load(name)
            mod.run()
            results.append((name, True, time.time() - t0, ""))
        except Exception as e:
            tb = traceback.format_exc()
            results.append((name, False, time.time() - t0, tb))
            print(f"\n✗ {name} FAILED: {e}")

    print(f"\n\n{'=' * 70}\nE2E SWEEP 요약\n{'=' * 70}")
    passed = sum(1 for _, ok, _, _ in results if ok)
    for name, ok, dt, tb in results:
        flag = "✓" if ok else "✗"
        print(f"  {flag} {name:32s} {dt:6.1f}s")
    print(f"\n{passed}/{len(results)} pass")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
