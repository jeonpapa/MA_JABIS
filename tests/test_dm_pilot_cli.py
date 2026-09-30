from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from agents.daily_mailing.article_body import ArticleBody
from agents.daily_mailing.engine import EngineConfig, EngineDeps
from agents.daily_mailing.llm import RELEVANCE_SYSTEM, SUPPORT_SYSTEM
from agents.daily_mailing.source_registry import SourceRegistry
from scripts.daily_mailing_pilot import run_pilot

NOW = datetime(2026, 9, 18, 8, 0, tzinfo=timezone(timedelta(hours=9)))
BODY = "파드셉 병용요법이 요로상피암 1차 치료로 급여 확대 논의에 들어갔다. " * 6


class Naver:
    def search(self, query, display=20, sort="date"):
        if query == "파드셉":
            return [{"title": "파드셉 급여 확대", "originallink": "https://www.yakup.com/p1",
                     "link": "https://www.yakup.com/p1", "description": "설명",
                     "pubDate": "Fri, 18 Sep 2026 06:00:00 +0900"}]
        return []


class LLM:
    def complete_json(self, *, system, user, model):
        data = json.loads(user)
        if system == RELEVANCE_SYSTEM:
            hits = [t for t in data["scope_terms"] if t in data["title"]]
            return {"relevant": bool(hits), "matched_scope_terms": hits, "reason": "주제"}
        if system == SUPPORT_SYSTEM:
            return {"supported": True, "reason": "검증"}
        return {"summary_bullets": ["요약"], "ma_implication": "키트루다 요로상피암 급여 경쟁 심화",
                "evidence_quote": data["article_body"][:30]}


def test_run_pilot_writes_outputs(tmp_path):
    scope_dir = tmp_path / "scopes"
    scope_dir.mkdir()
    (scope_dir / "19.json").write_text(json.dumps({"subscription_id": "19", "name": "파드셉 <테스트>",
                                                   "brands": ["파드셉"], "recipients": ["r@msd.com"]},
                                                  ensure_ascii=False), encoding="utf-8")
    deps = EngineDeps(naver=Naver(), llm=LLM(), registry=SourceRegistry([]),
                      fetch_body=lambda p, n: ArticleBody("ok", BODY, p))
    out = tmp_path / "out"
    summaries = run_pilot(sorted(scope_dir.glob("*.json")), out, deps=deps, config=EngineConfig(), now=NOW)
    assert summaries[0]["status"] == "ready"
    assert (out / "sub_19.html").read_text(encoding="utf-8").count("파드셉 급여 확대") >= 1
    judgments = json.loads((out / "sub_19.judgments.json").read_text(encoding="utf-8"))
    assert judgments["judgments"][0]["selected"] is True
    index = (out / "index.html").read_text(encoding="utf-8")
    assert "파드셉 &lt;테스트&gt;" in index
    assert "sub_19.html" in index
