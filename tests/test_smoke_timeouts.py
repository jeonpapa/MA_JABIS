"""스모크 체크별 타임아웃 (P1.5 fix F).

재현됐던 결함: government-keyword-summary 는 소켓이 즉시 거부돼도 6.9초가 걸렸다 — 07:50 프로덕션 첫 호출(일자 캐시 미스)은
LLM 2회(OpenAI + Gemini 45초)라 10초를 넘겨 거짓 platform_smoke 과제가 생긴다. fix C 가 동기 크롤을 막아도 LLM 호출은 남으므로
무거운 체크에 넉넉한 값을 준다. 나머지는 기본값(run_smoke(timeout=)) 그대로.
"""
from __future__ import annotations

import io
import json

from scripts import smoke


def test_per_check_timeout_overrides_default_only_for_heavy_checks():
    seen: dict[str, float] = {}

    def opener(req, timeout=10):
        seen[req.full_url.split("http://test", 1)[1]] = timeout
        out = io.BytesIO(json.dumps({"tasks": []}).encode())
        out.status = 200
        return out
    smoke.run_smoke("http://test", lambda role: "tok" if role else None, opener=opener, timeout=7.0)
    assert seen["/api/home/government-keyword-summary"] == smoke.HEAVY_TIMEOUT >= 60
    assert seen["/api/home/media-intelligence"] == smoke.SLOW_TIMEOUT >= 30
    assert seen["/api/home/gov-agency-clouds"] == smoke.SLOW_TIMEOUT
    assert seen["/api/hta/approvals?drug=pembrolizumab"] == smoke.SLOW_TIMEOUT
    light = {p: t for p, t in seen.items() if t != 7.0}
    assert set(light) == {"/api/home/government-keyword-summary", "/api/home/media-intelligence",
                          "/api/home/gov-agency-clouds", "/api/hta/approvals?drug=pembrolizumab"}
    assert seen["/api/domestic/stats"] == 7.0 and seen["/api/auth/me"] == 7.0


def test_check_default_timeout_is_none_and_table_declares_heavy_ones():
    assert smoke.Check("/x").timeout is None
    heavy = {c.path: c.timeout for sc in smoke.SCREENS for c in sc.checks if c.timeout}
    assert heavy["/api/home/government-keyword-summary"] == smoke.HEAVY_TIMEOUT
    assert all(t >= 30 for t in heavy.values()) and len(heavy) == 4
