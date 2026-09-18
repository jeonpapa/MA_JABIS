"""Daily Mailing 파일럿 — 실제 구독 스콥으로 브리핑 메일을 생성만 한다 (발송·DB 쓰기 없음).

설계 §9 파일럿 검수 게이트: 사용자에게 HTML 미리보기 + 기사별 판정 근거를 제출한다.

사용:
  .venv/bin/python scripts/daily_mailing_pilot.py --scope-dir <dir> --out-dir <dir>
  (<dir>/<subscription_id>.json = dashboard_scope 계약. 선택 키 'name' 은 메일 제목에 사용)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from html import escape
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agents.daily_mailing.brief import BriefItem  # noqa: E402
from agents.daily_mailing.dashboard_scope import load_dashboard_scope  # noqa: E402
from agents.daily_mailing.engine import (  # noqa: E402
    EngineConfig,
    EngineDeps,
    generate_run,
    generate_top_ma_news,
)


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"\''))


def _judgments(articles: list[dict]) -> list[dict]:
    return [{
        "title": a.get("title"),
        "url": a.get("publisher_url"),
        "source": a.get("source_name"),
        "selected": bool(a.get("selected_for_draft")),
        "review_status": a.get("review_status"),
        "matched": a.get("matched_keywords") or [],
        "note": a.get("reviewer_note"),
        "body": a.get("verification_caveat"),
    } for a in articles]


def render_index(summaries: list[dict], top: list[BriefItem], generated_at: str) -> str:
    blocks = []
    for s in summaries:
        rows = "".join(
            "<tr>"
            f"<td>{'✅' if j['selected'] else ('—' if j['review_status'] != 'excluded' else '✗')}</td>"
            f"<td><a href=\"{escape(j['url'] or '', quote=True)}\">{escape(j['title'] or '')}</a></td>"
            f"<td>{escape(j['source'] or '')}</td>"
            f"<td>{escape(', '.join(j['matched']))}</td>"
            f"<td>{escape(j['note'] or '')}</td>"
            f"<td>{escape(j['body'] or '')}</td>"
            "</tr>"
            for j in s["judgments"]
        )
        blocks.append(
            f"<h2>{escape(s['name'])} <small>(구독 {escape(str(s['subscription_id']))})</small></h2>"
            f"<p>상태 <b>{escape(s['status'])}</b> · 메일 기사 {s['items']}건 · 주요 MA 뉴스 {s['top_ma_news']}건 · "
            f"판정 {s['judged']}건(제외 {s['excluded']}) · "
            f"<a href=\"{escape(s['html'], quote=True)}\">메일 미리보기 열기</a></p>"
            "<table><thead><tr><th>선정</th><th>기사</th><th>매체</th><th>매칭 스콥</th><th>판정 근거</th><th>본문</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
        )
    top_list = "".join(f"<li>{escape(t.title)}</li>" for t in top) or "<li>(없음)</li>"
    return (
        "<!doctype html><html lang=\"ko\"><head><meta charset=\"utf-8\"><title>Daily Mailing 파일럿</title>"
        "<style>body{font-family:-apple-system,'Malgun Gothic',sans-serif;max-width:1100px;margin:24px auto;padding:0 16px;color:#0F172A}"
        "table{border-collapse:collapse;width:100%;font-size:13px}td,th{border:1px solid #E5E7EB;padding:6px;vertical-align:top;text-align:left}"
        "h2{margin-top:36px}small{color:#64748B;font-weight:400}</style></head><body>"
        f"<h1>Daily Mailing 파일럿 검수</h1><p>생성 {escape(generated_at)} · 발송 없음 · DB 쓰기 없음</p>"
        f"<h3>오늘의 주요 MA 뉴스 (구독 공통)</h3><ul>{top_list}</ul>"
        + "".join(blocks) + "</body></html>"
    )


def run_pilot(scope_paths: list[Path], out_dir: Path, *, deps: EngineDeps, config: EngineConfig,
              now: datetime | None = None) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    top = generate_top_ma_news(deps=deps, config=config, now=now)
    summaries: list[dict] = []
    for path in scope_paths:
        raw = json.loads(path.read_text(encoding="utf-8"))
        scope = load_dashboard_scope(path)
        name = str(raw.get("name") or scope.subscription_id)
        result = generate_run(scope, deps=deps, config=config, name=name, now=now, top_ma_news=top)
        stem = f"sub_{scope.subscription_id}"
        (out_dir / f"{stem}.html").write_text(result.html, encoding="utf-8")
        (out_dir / f"{stem}.md").write_text(result.markdown, encoding="utf-8")
        judgments = _judgments(result.articles)
        (out_dir / f"{stem}.judgments.json").write_text(json.dumps({
            "subject": result.subject, "status": result.status, "quality_report": result.quality_report,
            "stats": result.stats, "judgments": judgments,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        summaries.append({
            "subscription_id": scope.subscription_id, "name": name, "status": result.status,
            "subject": result.subject, "items": len(result.items), "top_ma_news": len(result.top_ma_news),
            "judged": len(result.articles),
            "excluded": sum(1 for a in result.articles if a.get("review_status") == "excluded"),
            "html": f"{stem}.html", "judgments": judgments, "stats": result.stats,
        })
    generated_at = (now or datetime.now()).isoformat(timespec="minutes")
    (out_dir / "index.html").write_text(render_index(summaries, top, generated_at), encoding="utf-8")
    return summaries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Daily Mailing 파일럿 (발송·DB 쓰기 없음)")
    parser.add_argument("--scope-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--env", default=str(REPO_ROOT / "config" / ".env"))
    parser.add_argument("--db", default=str(REPO_ROOT / "data" / "db" / "drug_prices.db"),
                        help="뉴스 아카이브(competitor_news) 읽기 전용 소스. 없으면 Naver 만 사용")
    parser.add_argument("--settings", default=str(REPO_ROOT / "config" / "settings.json"))
    args = parser.parse_args(argv)

    _load_env(Path(args.env))
    settings = json.loads(Path(args.settings).read_text(encoding="utf-8"))
    config = EngineConfig.from_settings(settings)
    db_path = args.db if Path(args.db).exists() else None
    deps = EngineDeps.default(db_path=db_path)
    paths = [p for p in sorted(Path(args.scope_dir).glob("*.json")) if p.name != "scopes_index.json"]
    summaries = run_pilot(paths, Path(args.out_dir), deps=deps, config=config)
    print(json.dumps([{k: s[k] for k in ("subscription_id", "name", "status", "items", "judged", "excluded")}
                      for s in summaries], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
