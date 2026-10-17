"""발언 검색(q) — 공백 무시 · 인용문 포함 · 한국어 복합어 변형(약가유연계약제 ⊃ 유연계약)."""
from __future__ import annotations

from agents.stakeholder import store

EV = "2026-09-30-가상-토론"


def _seed(db):
    events = [{"event_id": EV, "event_date": "2026-09-30", "title": "약가 유연계약 토론회", "context": "",
               "topics": ["negotiation"], "speakers": ["홍길동"], "grades": ["C-quote"]}]
    statements = [
        {"statement_id": "st-sum", "event_id": EV, "topic": "negotiation", "speaker_name": "홍길동",
         "speaker_role_at_event": "가상과장", "multi_speaker": False,
         "summary_text": "약가유연계약제 확대를 설명했다.", "evidence_grade": "C-summary", "locators": []},
        {"statement_id": "st-quote", "event_id": EV, "topic": "access", "speaker_name": "김철수",
         "speaker_role_at_event": "가상국장", "multi_speaker": False, "summary_text": "접근성 개선",
         "evidence_grade": "C-quote", "locators": [], "headline_quote_id": "q1"},
        {"statement_id": "st-none", "event_id": EV, "topic": "finance", "speaker_name": "이영희",
         "speaker_role_at_event": "가상사무관", "multi_speaker": False, "summary_text": "재정 효율화",
         "evidence_grade": "C-summary", "locators": []},
    ]
    quotes = [{"quote_id": "q1", "event_id": EV, "speaker_name": "김철수",
               "quote_text": "“유연 계약 방식을 검토하겠다”", "segments": ["유연 계약 방식을 검토하겠다"],
               "statement_ids": ["st-quote"], "topics": ["access"],
               "locator": {"path": "sources/fake.txt", "start": 1, "end": 2},
               "locators": [{"path": "sources/fake.txt", "start": 1, "end": 2}],
               "locator_match": "range", "evidence_grade": "C-quote"}]
    store.ensure_tables(db)
    with store.transaction(db) as conn:
        store.replace_canonical(events, statements, quotes, "sha1", conn)


def _ids(db, q):
    return sorted(s["statement_id"] for s in store.list_statements(q=q, db_path=db))


def test_search_variants_strip_prefix_suffix():
    v = store.search_variants("약가유연계약제")
    assert v[0] == "약가유연계약제"
    assert "유연계약" in v and "유연계약제" in v and "약가유연계약" in v
    assert store.search_variants("  유연 계약 ") == ["유연계약"]
    assert store.search_variants("") == [] and store.search_variants(None) == []
    assert "제" not in store.search_variants("약제")          # 2자 미만 변형 금지


def test_partial_compound_matches_summary_and_spaced_quote(tmp_path):
    db = tmp_path / "s.db"
    _seed(db)
    # '유연계약' ⊂ '약가유연계약제'(요약) · '유연 계약'(인용문, 공백 무시) · 행사명은 공통이라 셋 다 걸리면 안 되므로 행사명 분리 검증은 아래
    assert "st-sum" in _ids(db, "유연계약")
    assert "st-quote" in _ids(db, "유연계약")


def test_full_compound_query_matches_shorter_text(tmp_path):
    db = tmp_path / "s.db"
    _seed(db)
    # 질의 '약가유연계약제' → 변형 '유연계약' 으로 인용문 '유연 계약' 까지 매칭
    assert "st-quote" in _ids(db, "약가유연계약제")


def test_quote_text_and_person_role_search(tmp_path):
    db = tmp_path / "s.db"
    _seed(db)
    assert _ids(db, "검토하겠다") == ["st-quote"]            # 인용문만 가진 문구
    assert _ids(db, "가상 사무관") == ["st-none"]           # 직책(공백 무시)
    assert _ids(db, "이영희") == ["st-none"]
    assert _ids(db, "존재하지않는문구") == []
