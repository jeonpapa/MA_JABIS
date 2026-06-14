"""검색어 피드백 리뷰 (개발환경 보완용).

사용자가 통합검색에 입력한 시멘틱 → 실제 의도 약제 매핑을 모아,
검색 로직(동의어/도메인 술어/AND 그룹)을 어떻게 보완할지 판단하는 도구.

두 가지 소스:
  1. 로컬 DB (data/db/drug_prices.db) 의 analog_search_feedback
  2. 배포 서버 API (GET /api/analog/search-feedback, admin 토큰) → 로컬로 가져와 병합

사용법:
  # 로컬에 쌓인 피드백 리뷰 (검색어→의도 + 현재 검색이 의도를 얼마나 못 잡는지)
  .venv/bin/python -m agents.analog.feedback_review review

  # 배포 서버 피드백을 로컬 DB 로 가져오기 (개발환경에서 분석)
  ANALOG_API_BASE=https://<host> ANALOG_ADMIN_TOKEN=<token> \
    .venv/bin/python -m agents.analog.feedback_review pull
"""
from __future__ import annotations

import json
import os

from agents.analog import store


def review(limit: int = 500) -> None:
    """로컬 피드백을 리뷰. 각 피드백에 대해 현재 검색이 의도 약제를
    상위에 노출하는지 재현 → 검색 로직 보완 우선순위를 보여준다."""
    rows = store.list_search_feedback(limit=limit)
    if not rows:
        print("피드백 없음.")
        return

    print(f"총 {len(rows)}건의 검색어 피드백\n" + "=" * 60)
    miss = 0
    for r in rows:
        q = r.get("query") or "(검색어 없음)"
        intended = r.get("intended_text") or ""
        # 현재 검색 로직으로 같은 검색어를 재현 → 결과 상위 브랜드
        try:
            res = store.search(filters=r.get("filters_json") or {},
                               q=q if q != "(검색어 없음)" else None, limit=10)
            tops = [(x.get("brand_name") or x.get("brand_name_raw") or "")
                    for x in res.get("results", [])]
        except Exception as e:  # noqa: BLE001
            tops = []
            print(f"  (재현 실패: {e})")

        # 의도 약제가 현재 상위 10위 안에 잡히는지 (느슨한 부분일치)
        intended_norm = intended.replace(" ", "").lower()
        hit = any(intended_norm and intended_norm[:3] in (t or "").replace(" ", "").lower()
                  for t in tops)
        flag = "OK " if hit else "MISS"
        if not hit:
            miss += 1

        print(f"\n[{flag}] 검색어: {q!r}  →  의도: {intended!r}")
        if r.get("note"):
            print(f"       비고: {r['note']}")
        print(f"       현재 상위: {', '.join(t for t in tops[:5] if t) or '(없음)'}")

    print("\n" + "=" * 60)
    print(f"의도 미달(MISS) {miss}/{len(rows)} — 이 검색어들이 로직 보완 1순위")
    print("보완 위치: analog_ontology.json(동의어) · domain_terms.py(필드 술어) · concept_resolver.py(그룹화)")


def pull(limit: int = 1000) -> None:
    """배포 서버의 피드백을 로컬 DB 로 가져온다 (개발환경 분석용)."""
    import urllib.request

    base = os.environ.get("ANALOG_API_BASE", "").rstrip("/")
    token = os.environ.get("ANALOG_ADMIN_TOKEN", "")
    if not base or not token:
        print("ANALOG_API_BASE 와 ANALOG_ADMIN_TOKEN 환경변수 필요.")
        return

    url = f"{base}/api/analog/search-feedback?limit={limit}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        payload = json.loads(resp.read().decode("utf-8"))

    items = payload.get("items", [])
    added = 0
    for it in items:
        store.add_search_feedback(
            query=it.get("query"),
            filters=it.get("filters_json") or {},
            returned_ids=it.get("returned_ids") or [],
            returned_top=it.get("returned_top"),
            intended_text=it.get("intended_text") or "(서버 동기화)",
            note=it.get("note"),
        )
        added += 1
    print(f"서버에서 {len(items)}건 조회, 로컬 DB 에 {added}건 추가.")
    print("이제 `review` 로 분석하세요.")


if __name__ == "__main__":
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else "review"
    if cmd == "pull":
        pull()
    else:
        review()
