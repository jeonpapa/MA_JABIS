#!/usr/bin/env python3
"""Render HIRA Access Intelligence Gmail draft text.

This script intentionally renders a draft only. It does not send email.
Input is a JSON context matching the keys in
`docs/hira_access_intelligence/email_draft_template.md`.

Usage:
  python scripts/render_hira_email_draft.py --context sample.json
  python scripts/render_hira_email_draft.py --context sample.json --format json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

SUBJECT_TEMPLATE = "[AI_MAx_Report] 제{session_ordinal}차 {committee_short_kr} 후속 보고서_{session_date_dot}"

BODY_TEMPLATE = """*메일의 전체 내용은 AI 로 생성 되었습니다. 실제 사실과 다르거나 사용에 있어 신중한 해석이 필요할 수 있습니다.*

안녕하세요.
*{year}년 제{session_ordinal}차 {committee_full_kr}({session_date_kr}) 결과를 아래와 같이 보고드립니다.*
{opening_summary}

*1. 심의 결과 (HIRA 공식 용어 기준)*

{result_items}

*2. MSD 자산 영향 (모니터링 우선순위 기준)*

{msd_asset_impact}

*3. {next_session_date_kr} 제{next_session_ordinal}차 {next_committee_short_kr} 안건 후보*

{next_candidates}

*4. 정책 환경 변화*

{policy_context}

상세 내용은 *첨부 PDF*를 참고 부탁드립니다.
감사합니다.
"""

ALLOWED_TERMS = {
    "급여 적정성 있음",
    "급여의 적정성이 있음",
    "평가금액 이하 수용 시 적정",
    "평가금액 이하 수용시 급여의 적정성이 있음",
    "위험분담 확대 적정",
    "급여범위 확대의 적정성이 있음",
    "재심의",
    "급여기준 설정",
    "급여기준 미설정",
}

BANNED_REPORT_TOKENS = ["brdBltNo", "idxno", "PR-", "Precision", "Recall", "F1"]


def normalize_official_term(term: str) -> str:
    mapping = {
        "급여의 적정성이 있음": "급여 적정성 있음",
        "평가금액 이하 수용시 급여의 적정성이 있음": "평가금액 이하 수용 시 적정",
        "급여범위 확대의 적정성이 있음": "위험분담 확대 적정",
    }
    return mapping.get(term.strip(), term.strip())


def render_result_items(items: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for idx, item in enumerate(items, start=1):
        name = item["drug_name"]
        company = item.get("company", "")
        indication = item.get("indication_short", "")
        result = normalize_official_term(item["hira_official_result"])
        rationale = item.get("one_sentence_rationale", "").strip()
        if result not in {normalize_official_term(x) for x in ALLOWED_TERMS}:
            raise ValueError(f"Unsupported HIRA official result term: {result}")
        lines.append(f"{idx}. *{name}* ({company}, {indication})\n→ *{result}*")
        if rationale:
            lines.append(rationale)
    return "\n".join(lines)


def render_candidates(items: list[dict[str, Any]] | str) -> str:
    if isinstance(items, str):
        return items.strip()
    lines = []
    for item in items:
        label = item["name"]
        rationale = item.get("rationale", "").strip()
        probability = item.get("probability", "").strip()
        prefix = f"*{label}*"
        if probability:
            prefix += f" ({probability})"
        lines.append(f"{prefix}: {rationale}" if rationale else prefix)
    return "\n".join(lines)


def validate_body(subject: str, body: str) -> list[str]:
    warnings: list[str] = []
    haystack = subject + "\n" + body
    for token in BANNED_REPORT_TOKENS:
        if token in haystack:
            warnings.append(f"banned token found: {token}")
    if "조건부 통과" in haystack:
        warnings.append("avoid phrase '조건부 통과'; use official HIRA term")
    if "첨부 PDF" not in body:
        warnings.append("missing attachment reference")
    return warnings


def render(context: dict[str, Any]) -> dict[str, Any]:
    ctx = dict(context)
    if "year" not in ctx:
        m = re.match(r"(\d{4})", str(ctx.get("session_date_dot", "")))
        ctx["year"] = m.group(1) if m else ""
    if isinstance(ctx.get("result_items"), list):
        ctx["result_items"] = render_result_items(ctx["result_items"])
    if isinstance(ctx.get("next_candidates"), list):
        ctx["next_candidates"] = render_candidates(ctx["next_candidates"])
    subject = SUBJECT_TEMPLATE.format(**ctx)
    body = BODY_TEMPLATE.format(**ctx).strip() + "\n"
    warnings = validate_body(subject, body)
    return {"subject": subject, "body_markdown": body, "warnings": warnings}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Render HIRA email draft")
    p.add_argument("--context", required=True, help="JSON context path")
    p.add_argument("--format", choices=["text", "json"], default="text")
    args = p.parse_args(argv)

    context = json.loads(Path(args.context).read_text(encoding="utf-8"))
    out = render(context)
    if args.format == "json":
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print("SUBJECT:")
        print(out["subject"])
        print("\nBODY:")
        print(out["body_markdown"])
        if out["warnings"]:
            print("\nWARNINGS:", file=sys.stderr)
            for w in out["warnings"]:
                print(f"- {w}", file=sys.stderr)
    return 0 if not out["warnings"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
