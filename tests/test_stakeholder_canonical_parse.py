# tests/test_stakeholder_canonical_parse.py
"""정본 발언 타임라인 MD 파서(T3) — 합성 데이터만 사용(실제 기사 문장·실제 매체 금지)."""
from __future__ import annotations

from pathlib import Path

from agents.stakeholder import canonical_parse as cp

_MD = """# 최근 1년 테스트 타임라인

# 1. 전체 행사 마스터 타임라인

| 행사일 | 행사·맥락 | 핵심 인물과 당시 직책 | 핵심 주제 | 발언근거 |
|---|---|---|---|---|
| **2026-09-03** | 가상 협회 약가 토론 | 홍길동 가상부 가상과장 | 약가개편 | C-quote |
| **2026-06-19** | 가상 RWE 심포지엄 | 김가상 가상원 실장 | RWE | B-summary |

# 2. 주제 ① 약가제도 개편

## 2026-09-03｜가상 협회 약가 토론

### 홍길동 — 가상부 가상과장

> “신약 보상과 재정은 상충하지 않는다고 본다”며 “하반기에 신약을 어떻게 가져갈지가 핵심”

- **내용:** 개편 의제를 설명했다고 보도됐다.
- **해석:** 양자택일이 아닌 동시 설계 과제로 제시했다.
- **등급:** C-quote/C-summary
- **근거:** `sources/fake_20260903_hong.txt:3-5`
- **원문:** https://example.test/news/1

# 3. 주제 ② 신약 접근성

## 2026-09-03｜가상 협회 약가 토론

### 홍길동 — 가상부 가상과장

> “신약 보상과 재정은 상충하지 않는다고 본다”며 “하반기에 신약을 어떻게 가져갈지가 핵심”

- **한계:** 본사업 전환이 확정됐다는 발언은 아니다.
- **등급:** C-quote
- **근거:** `sources/fake_20260903_hong.txt:3-5`

# 4. 주제 ③ RWE

## 2026-06-19｜가상 RWE 심포지엄

### 김가상·박가상

- **내용:** 국가 레지스트리 필요성을 제시했다.
- **등급:** B-summary
- **근거:** `sources/fake_20260619_rwe.txt`

# 7. 인물별 반복 메시지 변화

홍길동은 반복해서 균형을 말했다.

# 9. 보류·한계

1. 가상 보류 항목.
"""

_TXT = "제목\n\n홍길동 과장은 “신약 보상과 재정은 상충하지 않는다고 본다”며\n“하반기에 신약을 어떻게 가져갈지가 핵심”이라고 말했다.\n끝\n"


def _root(tmp_path) -> Path:
    (tmp_path / "sources").mkdir()
    (tmp_path / "sources" / "fake_20260903_hong.txt").write_text(_TXT, encoding="utf-8")
    (tmp_path / "sources" / "fake_20260619_rwe.txt").write_text("요약만 있는 보도자료\n", encoding="utf-8")
    (tmp_path / "t.md").write_text(_MD, encoding="utf-8")
    return tmp_path


def test_parse_events_statements_quotes(tmp_path):
    root = _root(tmp_path)
    res = cp.parse_timeline(root / "t.md", root=root)
    assert [e.event_id for e in res.events] == ["2026-09-03-가상-협회-약가-토론", "2026-06-19-가상-RWE-심포지엄"]
    assert res.events[0].context == "가상 협회 약가 토론" and res.events[0].grades == ["C-quote"]
    assert len(res.statements) == 3                       # (행사×발언자×주제) 블록 3
    s0 = res.statements[0]
    assert s0.topic == "price_reform" and s0.speaker_name == "홍길동" and s0.speaker_role_at_event == "가상부 가상과장"
    assert s0.evidence_grade == "C-quote/C-summary" and s0.locators == [{"path": "sources/fake_20260903_hong.txt", "start": 3, "end": 5}]
    assert s0.source_url == "https://example.test/news/1" and s0.summary_text.startswith("개편 의제")
    assert res.statements[1].limitation_text.startswith("본사업") and res.statements[2].multi_speaker is True
    assert len(res.quotes) == 1                            # 같은 인용이 두 주제에 교차 표시 → 1행, topics 2
    q = res.quotes[0]
    assert q.topics == ["price_reform", "access"] and len(q.statement_ids) == 2
    assert q.segments == ["신약 보상과 재정은 상충하지 않는다고 본다", "하반기에 신약을 어떻게 가져갈지가 핵심"]
    assert q.locator_match == "range"
    assert res.meta["repeat_messages_md"].startswith("홍길동은") and "가상 보류" in res.meta["withheld_md"]
    assert res.raw_quote_blocks == 2                       # 손실 없음 검증용: 원본 `> ` 블록 수


def test_locator_match_falls_back_to_file_then_none(tmp_path):
    root = _root(tmp_path)
    md = _MD.replace("hong.txt:3-5`", "hong.txt:1-1`")          # 두 블록(두 주제) 모두 — 인용 1행이 두 블록을 공유한다
    (root / "t2.md").write_text(md, encoding="utf-8")
    assert cp.parse_timeline(root / "t2.md", root=root).quotes[0].locator_match == "file"
    md2 = _MD.replace("상충하지 않는다고 본다", "완전히 다른 문장")
    (root / "t3.md").write_text(md2, encoding="utf-8")
    assert cp.parse_timeline(root / "t3.md", root=root).quotes[0].locator_match == "none"


def test_norm_ignores_whitespace_and_quote_marks():
    assert cp.norm(" “a b”  c ") == "abc"


def test_master_table_mismatch_is_warning_not_error(tmp_path):
    root = _root(tmp_path)
    (root / "t4.md").write_text(_MD.replace("| **2026-06-19** | 가상 RWE 심포지엄", "| **2026-06-19** | 다른 제목"), encoding="utf-8")
    res = cp.parse_timeline(root / "t4.md", root=root)
    assert any("마스터 표" in w for w in res.warnings) and len(res.events) == 2


def test_importance_table(tmp_path):
    md = """## 2.2 모니터링 리스트 평가
| 인물 | 직접성/30 | 조직/25 | 발언/20 | 근거/15 | 반복/10 | 총점 | 중요도 | 업무중요도 | 공개확신도 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **홍길동** | 30 | 25 | 20 | 10 | 10 | **95** | **5** | 5 | 5 |
| **박가상** | 18 | 18 | 0 | 3 | 0 | **39** | **1** | 3(잠정) | 1 |
"""
    (tmp_path / "m.md").write_text(md, encoding="utf-8")
    t = cp.parse_importance_table(tmp_path / "m.md")
    assert t["홍길동"]["importance"] == 5 and t["홍길동"]["score_total"] == 95
    assert t["박가상"]["work_provisional"] is True and t["박가상"]["components"] == [18, 18, 0, 3, 0]


# ── 실데이터 문법(소스 정본 실측)에서 나온 추가 경계 ─────────────────────────────

_MD_EXTRA = """# 테스트 정본

## 0. 읽는 법

### 근거등급

> 동일 행사에 대한 여러 보도는 별도 사건으로 세지 않았다.

# 1. 전체 행사 마스터 타임라인

| 행사일 | 행사·맥락 | 핵심 인물과 당시 직책 | 핵심 주제 | 발언근거 |
|---|---|---|---|---|
| **2026-07-23** | 가상 결정기준 개정 가산 설명회 | 이가상 가상공단 부장 | 가산, 사후관리 | C-quote/C-summary |
| **2026-09-11** | 가상원 「재평가 설명회」 | 최가상 가상원 부장 | 재평가 | C-quote |
| **2026-09-11** | 가상공단 기자 간담회 | 정가상 가상공단 실장 | 협상 | C-quote + Role-only |

# 2. 주제 ① 약가제도 개편

## 2026-07-23｜가상 가산 설명회

### 이가상 — 가상공단 부장

- **직접발언:** 가산요건 유지와 자동검증을 설명했다.
- **내용:** 운영계획이 보도됐다.
- **시점 한계:** 시행 중인 제도로 단정하지 않는다.
- **제도상태:** **검토안**이다.
- **등급:** C-quote/C-summary
- **근거:** `sources/fake_a.txt:1-2`
- **행사일/게시일:** 2026-07-23 / 2026-07-24

## 2026-09-11｜재평가 설명회

### 최가상 — 가상원 부장

> “가산 구조에는 들어오지 않는다”

> “별도 [그리고] 조정신청을 [및] 할 수 있다”

> “신청이 몰리면 늦춰질 수 있다”[…]“가급적 4월에 맞춘다”

- **등급:** C-quote
- **근거:** `sources/fake_b.txt:2,4`

## 2026-09-11｜가상공단 기자 간담회

### 정가상 — 가상공단 실장

> “상사로부터 ‘국민 관점에서 하라’는 당부를 받았다”

- **맥락:** 공급중단 질의에 대한 답변이다.
- **공식 발표요약:** 협상 원칙을 발표했다.
- **직접인용:** 미확보
- **성격:** 재정 메시지이며 절감액 발표가 아니다.
- **정량 한계:** 금액이 제시되지 않았다.
- **등급:** C-quote
- **근거:** `sources/fake_a.txt:1-1, 4-5`; `sources/fake_b.txt:9-9`

### 한가상·두가상

- 한가상은 접근성을, 두가상은 레지스트리를 연결했다.
- **등급:** B-quote
- **근거:** `sources/fake_a.txt`

# 7. 반복 메시지

| 인물 | 메시지 |
|---|---|

# 9. 보류·한계

1. 보류.
"""

_TXT_A = "가산요건 유지 자동검증\n둘째 줄\n셋째 줄\n상사로부터 ‘국민 관점에서\n하라’는 당부를 받았다\n"
_TXT_B = ("머리\n“가산 구조에는 들어오지 않는다”\n중간 문단\n“별도 조정신청을 할 수 있다”\n"
          "“신청이 몰리면 늦춰질 수 있다” 다른 말 “가급적 4월에 맞춘다”\n")


def _root_extra(tmp_path) -> Path:
    (tmp_path / "sources").mkdir()
    (tmp_path / "sources" / "fake_a.txt").write_text(_TXT_A, encoding="utf-8")
    (tmp_path / "sources" / "fake_b.txt").write_text(_TXT_B, encoding="utf-8")
    (tmp_path / "x.md").write_text(_MD_EXTRA, encoding="utf-8")
    return tmp_path


def test_preamble_blockquote_and_headings_are_not_statements(tmp_path):
    root = _root_extra(tmp_path)
    res = cp.parse_timeline(root / "x.md", root=root)
    assert {s.speaker_name for s in res.statements} == {"이가상", "최가상", "정가상", "한가상·두가상"}
    # §0 의 `> ` 는 편집 주석 — 인용으로 승격하지 않되 손실 없이 별도 보존
    assert res.raw_quote_blocks == 4
    assert [b["text"] for b in res.excluded_blockquotes] == ["동일 행사에 대한 여러 보도는 별도 사건으로 세지 않았다."]
    assert all("동일 행사" not in q.quote_text for q in res.quotes)


def test_master_title_abbreviation_is_matched_without_warning(tmp_path):
    root = _root_extra(tmp_path)
    res = cp.parse_timeline(root / "x.md", root=root)
    # 유일한 경고는 의도적으로 심은 원문 불일치 인용(q2) — 마스터 표 관련 경고 없음
    assert len(res.warnings) == 1 and "원문 대조 실패" in res.warnings[0]
    assert [e.event_date for e in res.events] == ["2026-07-23", "2026-09-11", "2026-09-11"]
    # 같은 날 두 행사 — 제목으로 구분해 각자의 마스터 행에 붙는다
    assert res.events[1].title == "재평가 설명회" and res.events[1].context == "가상원 「재평가 설명회」"
    assert res.events[2].grades == ["C-quote", "Role-only"]
    assert res.events[0].topics == ["가산", "사후관리"]


def test_non_quote_fields_never_become_verbatim(tmp_path):
    root = _root_extra(tmp_path)
    res = cp.parse_timeline(root / "x.md", root=root)
    st = {s.speaker_name: s for s in res.statements}
    a = st["이가상"]
    assert a.headline_quote_id is None and a.quote_ids == []              # '직접발언' 라벨이어도 인용 아님
    assert a.summary_text == "가산요건 유지와 자동검증을 설명했다.\n운영계획이 보도됐다."
    assert a.limitation_text == "시행 중인 제도로 단정하지 않는다.\n**검토안**이다."
    assert a.policy_status == "**검토안**이다."
    assert a.publication_date == "2026-07-24" and a.analysis_text is None
    c = st["정가상"]
    assert c.summary_text == "공급중단 질의에 대한 답변이다.\n협상 원칙을 발표했다."
    assert c.analysis_text == "재정 메시지이며 절감액 발표가 아니다."
    assert c.limitation_text == "직접인용: 미확보\n금액이 제시되지 않았다."
    d = st["한가상·두가상"]
    assert d.multi_speaker is True and d.summary_text == "한가상은 접근성을, 두가상은 레지스트리를 연결했다."
    # 라벨 원본은 순서대로 손실 없이 보존
    assert [f[0] for f in c.fields] == ["맥락", "공식 발표요약", "직접인용", "성격", "정량 한계", "등급", "근거"]


def test_comma_and_multi_source_locators(tmp_path):
    root = _root_extra(tmp_path)
    res = cp.parse_timeline(root / "x.md", root=root)
    st = {s.speaker_name: s for s in res.statements}
    assert st["최가상"].locators == [{"path": "sources/fake_b.txt", "start": 2, "end": 2},
                                    {"path": "sources/fake_b.txt", "start": 4, "end": 4}]
    assert st["정가상"].locators == [{"path": "sources/fake_a.txt", "start": 1, "end": 1},
                                    {"path": "sources/fake_a.txt", "start": 4, "end": 5},
                                    {"path": "sources/fake_b.txt", "start": 9, "end": 9}]
    assert st["한가상·두가상"].locators == [{"path": "sources/fake_a.txt", "start": None, "end": None}]


def test_quote_text_is_byte_exact_and_ellipsis_pieces_verified(tmp_path):
    root = _root_extra(tmp_path)
    res = cp.parse_timeline(root / "x.md", root=root)
    by_text = {q.quote_text: q for q in res.quotes}
    # 블록 원문 그대로(`> ` 만 제거) — 생략표시·연결어 포함, 정규화 없음
    q3 = by_text["“신청이 몰리면 늦춰질 수 있다”[…]“가급적 4월에 맞춘다”"]
    assert q3.segments == ["신청이 몰리면 늦춰질 수 있다", "가급적 4월에 맞춘다"]
    assert q3.locator_match == "file"                      # 2,4 행 밖(5행) — 파일에는 있음
    q1 = by_text["“가산 구조에는 들어오지 않는다”"]
    assert q1.locator_match == "range" and q1.locator == "sources/fake_b.txt:2"
    # 세그먼트 내부 `[…]` 아닌 대괄호 삽입은 원문에 없으므로 none (생략표시만 조각 분할)
    q2 = by_text["“별도 [그리고] 조정신청을 [및] 할 수 있다”"]
    assert q2.locator_match == "none"
    assert any("원문 대조 실패" in w and "조정신청" in w for w in res.warnings)
    # 중첩 홑따옴표 — 줄바꿈을 넘는 원문도 정규화 비교로 range
    q4 = by_text["“상사로부터 ‘국민 관점에서 하라’는 당부를 받았다”"]
    assert q4.segments == ["상사로부터 ‘국민 관점에서 하라’는 당부를 받았다"] and q4.locator_match == "range"
    assert q4.locator == "sources/fake_a.txt:4-5"
    assert all(q.quote_norm == cp.norm(q.quote_text) for q in res.quotes)


def test_ellipsis_marker_splits_pieces(tmp_path):
    (tmp_path / "sources").mkdir()
    (tmp_path / "sources" / "e.txt").write_text("앞부분 문장이다 그리고 중간 생략 뒷부분 문장이다\n", encoding="utf-8")
    md = _MD.replace("“신약 보상과 재정은 상충하지 않는다고 본다”며 “하반기에 신약을 어떻게 가져갈지가 핵심”",
                     "“앞부분 문장이다 […] 뒷부분 문장이다”").replace("fake_20260903_hong.txt:3-5", "e.txt:1-1")
    (tmp_path / "e.md").write_text(md, encoding="utf-8")
    res = cp.parse_timeline(tmp_path / "e.md", root=tmp_path)
    assert res.quotes[0].segments == ["앞부분 문장이다 […] 뒷부분 문장이다"]
    assert res.quotes[0].locator_match == "range"


def test_unknown_grade_and_field_warn_not_raise(tmp_path):
    root = _root(tmp_path)
    md = _MD.replace("- **등급:** C-quote\n", "- **등급:** Z-rumor\n- **추측:** 아무 말\n")
    (root / "w.md").write_text(md, encoding="utf-8")
    res = cp.parse_timeline(root / "w.md", root=root)
    assert any("Z-rumor" in w for w in res.warnings) and any("추측" in w for w in res.warnings)
    assert res.statements[1].evidence_grade == "Z-rumor"
    assert ("추측", "아무 말") in res.statements[1].fields   # 미지 필드도 손실 없이 보존


def test_missing_locator_is_unchecked_and_missing_file_warns(tmp_path):
    root = _root(tmp_path)
    md = _MD.replace("- **근거:** `sources/fake_20260903_hong.txt:3-5`\n", "")
    (root / "u.md").write_text(md, encoding="utf-8")
    assert cp.parse_timeline(root / "u.md", root=root).quotes[0].locator_match == "unchecked"
    md2 = _MD.replace("fake_20260903_hong.txt", "gone.txt")
    (root / "g.md").write_text(md2, encoding="utf-8")
    res = cp.parse_timeline(root / "g.md", root=root)
    assert res.quotes[0].locator_match == "unchecked" and any("gone.txt" in w for w in res.warnings)


def test_ids_are_deterministic_and_canonical_sha(tmp_path):
    root = _root(tmp_path)
    a = cp.parse_timeline(root / "t.md", root=root)
    b = cp.parse_timeline(root / "t.md", root=root)
    assert [s.statement_id for s in a.statements] == [s.statement_id for s in b.statements]
    assert a.quotes[0].quote_id == b.quotes[0].quote_id and len(a.quotes[0].quote_id) == 16
    assert a.statements[0].headline_quote_id == a.quotes[0].quote_id == a.statements[1].headline_quote_id
    import hashlib
    assert a.canonical_sha256 == hashlib.sha256((root / "t.md").read_bytes()).hexdigest()
    assert a.to_dict()["quotes"][0]["segments"] == a.quotes[0].segments
