#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agents.daily_mailing.discovery import DEFAULT_KEYWORDS, discover_naver_news, select_daily_items
from agents.daily_mailing.source_registry import SourceRegistry


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"\''))


def main() -> int:
    parser = argparse.ArgumentParser(description="Discover MA daily mailing news via Naver News API.")
    parser.add_argument("--keywords", nargs="*", default=DEFAULT_KEYWORDS)
    parser.add_argument("--display", type=int, default=10)
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--registry", default=str(REPO_ROOT / "config" / "source_registry.yaml"))
    parser.add_argument("--env", default=str(REPO_ROOT / "config" / ".env"))
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    _load_env(Path(args.env))
    registry = SourceRegistry.from_file(args.registry)
    discovered = discover_naver_news(args.keywords, registry=registry, display=args.display)
    ranked = select_daily_items(discovered, args.keywords, limit=args.limit)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "keywords": args.keywords,
        "count": len(ranked),
        "items": [item.__dict__ for item in ranked],
        "note": "Naver News API is discovery only; final authority tier is assigned from publisher domain.",
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
