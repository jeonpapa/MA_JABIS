"""서비스 개선 요청 위임 루프 CLI — 에이전트 채널.

Claude Code 에이전트가 in-repo(또는 fly ssh) 로 outbox 를 소비하고 결과를 동기화한다.
서버 불필요 — store 직접 호출 (기본 DB: data/db/drug_prices.db).

    python -m agents.service_requests.cli outbox [--dir PATH] [--limit N]
    python -m agents.service_requests.cli claim <id> [--by claude-code]
    python -m agents.service_requests.cli resolve <id> --status done|wont_fix --note "..." [--commit SHA]
    python -m agents.service_requests.cli list [--status S] [--limit N]

패턴: 대쉬보드=입력/기록, 에이전트=실작업(claim→구현→검증→resolve), 결과=내 개선 요청에 표시.
런북: docs/service_requests/AGENT_HANDOFF.md
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from agents.service_requests import store

DEFAULT_ACTOR = "claude-code"


def _slugify(title: str, max_len: int = 60) -> str:
    """파일명 안전 슬러그 — 영숫자/한글 외 '-' 치환, 연속 '-' 축약."""
    slug = re.sub(r"[^0-9A-Za-z가-힣]+", "-", (title or "").strip()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:max_len].rstrip("-") or "request"


def _print_json(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _summary_table(items: list[dict]) -> None:
    if not items:
        print("(비어 있음)")
        return
    print(f"{'id':>5}  {'status':<12} {'priority':<8} {'requester':<28} title")
    print("-" * 100)
    for it in items:
        print(
            f"{it['id']:>5}  {it['status']:<12} {(it.get('priority') or '-'):<8} "
            f"{(it.get('owner_email') or '-'):<28} {it.get('title') or ''}"
        )


def _request_markdown(it: dict) -> str:
    """outbox 파일 본문 — 헤더(id/title/requester/page) + 패키지 마크다운."""
    package = it.get("package_markdown") or it.get("sent_markdown") or "(패키지 없음)"
    header = [
        "---",
        f"request_id: {it['id']}",
        f"title: {it.get('title') or ''}",
        f"requester: {it.get('owner_email') or '-'}",
        f"page: {it.get('page_label') or '-'} ({it.get('page_path') or '-'})",
        f"priority: {it.get('priority') or '-'}",
        f"sent_at: {it.get('sent_at') or '-'}",
        "---",
        "",
    ]
    return "\n".join(header) + package.rstrip() + "\n"


def cmd_outbox(args) -> int:
    items = store.list_outbox(limit=args.limit, db_path=args.db)
    if not args.dir:
        print(f"outbox: {len(items)}건 (status=sent)")
        _summary_table(items)
        return 0
    out_dir = Path(args.dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    index_lines = [
        "# Service Request Outbox",
        "",
        f"- 건수: {len(items)}",
        "",
    ]
    for it in items:
        fname = f"{it['id']}-{_slugify(it.get('title') or '')}.md"
        (out_dir / fname).write_text(_request_markdown(it), encoding="utf-8")
        index_lines.append(
            f"- [#{it['id']}] [{it.get('title') or ''}]({fname}) — "
            f"{it.get('priority') or '-'} / {it.get('owner_email') or '-'} / sent {it.get('sent_at') or '-'}"
        )
        print(f"wrote {out_dir / fname}")
    index_lines.append("")
    (out_dir / "index.md").write_text("\n".join(index_lines), encoding="utf-8")
    print(f"wrote {out_dir / 'index.md'} ({len(items)}건)")
    return 0


def cmd_claim(args) -> int:
    item = store.claim_request(args.id, args.by, db_path=args.db)
    if item is None:
        print(f"claim 실패 — #{args.id} 는 status='sent' 가 아니거나 미존재", file=sys.stderr)
        return 1
    _print_json({"id": item["id"], "status": item["status"],
                 "claimed_by": item["claimed_by"], "claimed_at": item["claimed_at"]})
    return 0


def cmd_resolve(args) -> int:
    result = store.resolve_request(
        args.id, args.by,
        status=args.status, resolution_note=args.note, commit_ref=args.commit,
        db_path=args.db,
    )
    if isinstance(result, tuple):
        _, msg = result
        print(f"resolve 실패 — {msg}", file=sys.stderr)
        return 1
    _print_json({
        "id": result["id"], "status": result["status"],
        "resolution_note": result["resolution_note"], "commit_ref": result["commit_ref"],
        "resolved_by": result["resolved_by"], "resolved_at": result["resolved_at"],
    })
    return 0


def cmd_list(args) -> int:
    items = store.list_all(status=args.status, limit=args.limit, db_path=args.db)
    _summary_table(items)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m agents.service_requests.cli",
        description="서비스 개선 요청 위임 루프 (outbox/claim/resolve)",
    )
    p.add_argument("--db", default=None, help="sqlite 경로 (기본 data/db/drug_prices.db)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("outbox", help="status=sent 요청 목록 (--dir 시 md 파일로 저장)")
    sp.add_argument("--dir", default=None, help="요청별 <id>-<slug>.md + index.md 저장 경로")
    sp.add_argument("--limit", type=int, default=200)
    sp.set_defaults(func=cmd_outbox)

    sp = sub.add_parser("claim", help="요청 픽업 (sent → in_progress)")
    sp.add_argument("id", type=int)
    sp.add_argument("--by", default=DEFAULT_ACTOR)
    sp.set_defaults(func=cmd_claim)

    sp = sub.add_parser("resolve", help="결과 동기화 (→ done|wont_fix)")
    sp.add_argument("id", type=int)
    sp.add_argument("--status", required=True, choices=list(store.RESOLUTION_STATUSES))
    sp.add_argument("--note", required=True, help="해결 요약 (requester 에게 표시)")
    sp.add_argument("--commit", default=None, help="로컬 커밋 SHA")
    sp.add_argument("--by", default=DEFAULT_ACTOR)
    sp.set_defaults(func=cmd_resolve)

    sp = sub.add_parser("list", help="요청 목록")
    sp.add_argument("--status", default=None, choices=list(store.STATUSES))
    sp.add_argument("--limit", type=int, default=200)
    sp.set_defaults(func=cmd_list)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
