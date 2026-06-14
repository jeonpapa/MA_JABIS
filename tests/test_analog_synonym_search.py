"""약제 등재 아날로그 검색 — 동의어 인지 회귀 테스트.

통합 검색의 핵심 요구사항(Wave4-4): 같은 의미의 서로 다른 표기가 같은 결과를 반환해야 한다.
  "고지혈증 주사제" = "이상지질혈증 주사제" = "PCSK9 주사제"
  → 모두 레파타(#686) + 프랄런트(#1528/#1529) 를 상위에 surface.

**tag_seeds / concept_resolver / store.search 의 동의어·태그 오버랩 로직 수정 시 반드시 통과.**

설계 근거:
  - grounded tagging: report 는 구조화 필드 evidence 가 있는 concept 만 태깅 (drift 방지)
  - query-time alias expansion + drug_class↔target 링크 (PCSK9 ↔ PCSK9억제제)
  - 태그 오버랩 재정렬 (의미 일치 우선)

pytest 없이도 실행 가능: `.venv/bin/python tests/test_analog_synonym_search.py`
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.analog import store

# 동의어 트리플 — 세 표기 모두 동일 PCSK9 억제제 주사제를 가리킨다
SYNONYM_TRIPLE = ["고지혈증 주사제", "이상지질혈증 주사제", "PCSK9 주사제"]

# 기대 retrieval: 레파타주프리필드펜(#686) + 프랄런트펜주(#1528/#1529)
PCSK9_REPORT_IDS = {686, 1528, 1529}

TOP_N = 3   # 상위 N 안에 PCSK9 약제가 들어와야 함


def _top_ids(query: str, limit: int = 10) -> list[int]:
    res = store.search(q=query, limit=limit)
    return [r["id"] for r in res.get("results", [])]


def test_synonym_triple_share_top_results():
    """세 동의어 표기가 모두 상위 N 에서 PCSK9 약제(레파타/프랄런트)를 surface."""
    per_query_top = {}
    for q in SYNONYM_TRIPLE:
        ids = _top_ids(q)
        top = set(ids[:TOP_N])
        per_query_top[q] = top
        hit = top & PCSK9_REPORT_IDS
        assert hit, (
            f"'{q}' 상위 {TOP_N} 에 PCSK9 약제 없음. "
            f"top{TOP_N}={ids[:TOP_N]}, 기대 교집합={PCSK9_REPORT_IDS}"
        )

    # 세 쿼리의 상위 결과가 동일 PCSK9 약제로 겹쳐야 한다 (동의어 = 같은 결과)
    common = set.intersection(*per_query_top.values())
    pcsk9_common = common & PCSK9_REPORT_IDS
    assert pcsk9_common, (
        f"세 동의어 표기의 상위 {TOP_N} 교집합에 공통 PCSK9 약제 없음. "
        f"per_query={per_query_top}"
    )


def test_synonym_concepts_recognized():
    """동의어 쿼리는 query_debug 로 concept 를 인식하고 태그 재정렬을 적용한다."""
    for q in SYNONYM_TRIPLE:
        res = store.search(q=q, limit=10)
        dbg = res.get("query_debug") or {}
        assert dbg.get("concept_count", 0) > 0, (
            f"'{q}' concept 인식 실패 — query_debug={dbg}"
        )
        cids = {c["concept_id"] for c in dbg.get("matched_concepts", [])}
        # 세 표기 모두 form-injection(주사제) 을 인식해야 함
        assert "form-injection" in cids, (
            f"'{q}' 제형(form-injection) 미인식 — concepts={cids}"
        )


def test_and_condition_top_results():
    """공백 구분 검색어는 AND 결합 — 모든 조건 충족 결과가 상위에 온다.

    각 케이스: 상위 N 이 전부 두 조건을 동시에 충족(_groups_matched==2)해야 함.
    """
    cases = ["희귀 항암제", "난소암 항암제", "경평면제 총액제한"]
    for q in cases:
        res = store.search(q=q, limit=5, debug=True)
        dbg = res.get("query_debug") or {}
        assert dbg.get("and_rerank") is True, f"'{q}' AND 재정렬 비활성 — debug={dbg}"
        assert len(dbg.get("groups", [])) == 2, f"'{q}' 그룹 2개 미인식 — {dbg.get('groups')}"
        rows = res["results"]
        assert rows, f"'{q}' 결과 없음"
        # 상위 3건은 두 조건을 모두 충족 (_groups_matched == 2)
        for d in rows[:3]:
            assert d.get("_groups_matched") == 2, (
                f"'{q}' 상위에 부분일치 혼입: "
                f"#{d['id']} {d.get('brand_name')} gm={d.get('_groups_matched')}"
            )


def test_and_condition_excludes_fertility_for_ovarian():
    """'난소암' 은 grounded concept 라 난소자극(난임) 약을 배제한다 (오탐 방지)."""
    res = store.search(q="난소암 항암제", limit=10)
    names = [(d.get("brand_name") or "") for d in res["results"][:5]]
    # 불임/난소자극 약(고나도핀/레코벨/폴리트롭 등)이 상위에 없어야 함
    fertility = ["고나도핀", "레코벨", "폴리트롭", "가니레버", "오가루트란", "세트로타이드"]
    leaked = [n for n in names if any(f in n for f in fertility)]
    assert not leaked, f"난소암 검색에 난임 약 혼입: {leaked}"


def test_brand_exact_match_regression():
    """정확 브랜드명 검색은 해당 브랜드가 최상위 (동의어 확장이 망치지 않음)."""
    ids = _top_ids("키트루다", limit=10)
    assert ids, "키트루다 검색 결과 없음"
    res = store.search(q="키트루다", limit=10)
    top = res["results"][0]
    name = (top.get("brand_name") or top.get("brand_name_raw") or "")
    assert "키트루다" in name, (
        f"키트루다 검색 1위가 키트루다가 아님: '{name}' (id={top['id']})"
    )


if __name__ == "__main__":
    print("=== 약제 아날로그 동의어 인지 회귀 ===")
    test_synonym_triple_share_top_results()
    print("  동의어 트리플 상위 공유 OK (고지혈증=이상지질혈증=PCSK9 주사제)")
    test_synonym_concepts_recognized()
    print("  concept 인식 + form-injection OK")
    test_and_condition_top_results()
    print("  AND 결합 상위 OK (희귀·항암제 / 난소암·항암제 / 경평면제·총액제한)")
    test_and_condition_excludes_fertility_for_ovarian()
    print("  난소암 grounded concept — 난임약 배제 OK")
    test_brand_exact_match_regression()
    print("  정확 브랜드(키트루다) 1위 회귀 OK")
    print("\n✔ All analog synonym-search checks passed")
