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
            return {"supported": True, "msd_specific": True, "reason": "검증"}
        return {"summary_bullets": ["요약"], "ma_implication": "키트루다 요로상피암 급여 경쟁 심화",
                "evidence_quote": data["article_body"][:30], "impact": "threat", "affected_asset": "키트루다"}


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
    assert judgments["judgments"][0]["impact"] == "threat" and judgments["judgments"][0]["affected_asset"] == "키트루다"
    assert judgments["judgments"][0]["implication"] == "키트루다 요로상피암 급여 경쟁 심화"
    index = (out / "index.html").read_text(encoding="utf-8")
    assert "파드셉 &lt;테스트&gt;" in index
    assert "sub_19.html" in index


class ClusterNaver:
    def search(self, query, display=20, sort="date"):
        if query != "약가":
            return []
        mk = lambda t, u: {"title": t, "originallink": u, "link": u, "description": "설명…",
                           "pubDate": "Fri, 18 Sep 2026 06:00:00 +0900"}
        return [mk("듀카브·램시마 등 80개 품목 약가 평균 4.6% 인하…339억원 절감", "https://www.newsmp.com/u1"),
                mk("사용량 늘어난 의약품 80개 약가 평균 4.6% 인하", "https://www.yakup.com/u2")]


def test_run_pilot_review_page_shows_cluster_flags_and_display_source(tmp_path):
    scope_dir = tmp_path / "scopes"
    scope_dir.mkdir()
    (scope_dir / "19.json").write_text(json.dumps({"subscription_id": "19", "name": "약가", "keywords": ["약가"],
                                                   "recipients": ["r@msd.com"]}, ensure_ascii=False), encoding="utf-8")
    deps = EngineDeps(naver=ClusterNaver(), llm=LLM(), registry=SourceRegistry([]),
                      fetch_body=lambda p, n: ArticleBody("ok", BODY, p))
    out = tmp_path / "out"
    run_pilot(sorted(scope_dir.glob("*.json")), out, deps=deps, config=EngineConfig(), now=NOW)
    judgments = json.loads((out / "sub_19.judgments.json").read_text(encoding="utf-8"))["judgments"]
    by_url = {j["url"]: j for j in judgments}
    kept = next(j for j in judgments if j["selected"])
    absorbed = next(j for j in judgments if not j["selected"])
    assert kept["also_reported_by"] == [{"source_name": absorbed["source"], "url": absorbed["url"]}]
    assert absorbed["also_reported_by"] == []
    assert "duplicate_story" in absorbed["flags"]
    assert kept["verification"] == "llm_relevance+quote_check+support_check"
    assert {j["source"] for j in judgments} == {"newsmp.com", "yakup.com"}      # 'Unknown publisher' 아님
    assert by_url["https://www.newsmp.com/u1"]["source"] == "newsmp.com"
    index = (out / "index.html").read_text(encoding="utf-8")
    assert "duplicate_story" in index and "외 1개 매체" in index
    assert "Unknown publisher" not in index
    assert "<th>플래그</th>" in index and "<th>동일 보도</th>" in index
    assert "<th>시사점</th>" in index


def test_pilot_review_flags_include_context_pack_rejections():
    from scripts.daily_mailing_pilot import REVIEW_FLAGS

    assert "implication_unverified_number" in REVIEW_FLAGS and "implication_unknown_asset" in REVIEW_FLAGS
    assert "implication_not_msd_specific" in REVIEW_FLAGS
