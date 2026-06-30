#!/usr/bin/env python3
"""Operational CLI for Policy Intelligence Gmail ingest.

Usage examples:
  python3 scripts/run_policy_intelligence_ingest.py --max 20
  python3 scripts/run_policy_intelligence_ingest.py --query 'label:krpia newer_than:7d'
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agents.policy_intelligence import write_dashboard_json
from agents.policy_intelligence_ingest import DEFAULT_QUERY, DEFAULT_ROOT, run_ingest


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Gmail ingest for MA Policy Intelligence")
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--max", type=int, default=20)
    parser.add_argument("--manifest-name")
    args = parser.parse_args()

    result = run_ingest(
        root=args.root,
        query=args.query,
        max_results=args.max,
        manifest_name=args.manifest_name,
    )
    dashboard_path = write_dashboard_json(root=args.root, manifest_path=result["status"]["manifest_path"])
    status = {**result["status"], "dashboard_json": str(dashboard_path)}
    print(json.dumps(status, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
