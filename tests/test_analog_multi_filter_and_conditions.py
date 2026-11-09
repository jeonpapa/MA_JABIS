"""2026-11-09 등재 아날로그 개선: 복수 선택 필터, AI 분석 구조 조건(경평면제 등), 이력, 목록 엑셀, 분석 요청 보완 파일."""
import io
import sqlite3

import pytest

from agents.analog import ai_analyze as ai
from agents.analog import store

WAIVED_BODY = "○ 제외국 3개국 이상에 등재되어 경제성평가 자료 제출 생략 가능 약제에 해당함. 대상 환자 수는 약 120명으로 추정됨.\n- 재정영향은 비공개임."
PLAIN_BODY = "○ 대상 환자 수는 약 3,000명으로 추정됨. 비용효과성 평가 결과 ICER 수용 가능.\n- 재정영향은 비공개임."


def _seed(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.db")
    ai.ensure_job_schema()
    rows = [
        # id, brand, result, pe_waiver, has_rsa, track, disease, body
        (1, "가약", "APPROVED", 1, 0, None, "종양", PLAIN_BODY),
        (2, "나약", "APPROVED", 0, 0, "경제성평가 생략 (PE Waiver)", "희귀", PLAIN_BODY),
        (3, "다약", "REJECTED", 0, 0, None, "희귀", WAIVED_BODY),          # 본문 문구로만 확인
        (4, "라약", "APPROVED", 0, 1, None, "종양", PLAIN_BODY),           # 경평면제 아님
        (5, "마약", "CONDITIONAL_PRICE", 0, 0, None, "만성", PLAIN_BODY),  # 경평면제 아님
    ]
    with sqlite3.connect(store.DB_PATH) as c:
        for i, b, rr, pe, rsa, tr, dc, body in rows:
            c.execute("INSERT INTO analog_reports (id, file_name, file_hash, brand_name, session_date, review_result, post_url, body_text, "
                      "pe_waiver, has_rsa, reimbursement_track_ko, disease_category) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                      (i, f"f{i}.pdf", f"h{i}", b, f"2024-0{i}-01", rr, f"http://x/{i}", body, pe, rsa, tr, dc))


# ── 복수 선택 필터 ─────────────────────────────────────────────────────────
def test_multi_value_filter_is_or_within_column_and_with_across(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    ids = lambda res: sorted(r["id"] for r in res["results"])
    assert ids(store.search({"disease_category": ["종양", "희귀"]}, limit=50)) == [1, 2, 3, 4]
    assert ids(store.search({"disease_category": ["종양", "희귀"], "review_result": ["REJECTED"]}, limit=50)) == [3]
    assert ids(store.search({"disease_category": "만성"}, limit=50)) == [5]          # 단일 값 하위호환
    f = store.facet_values({"disease_category": ["종양", "희귀"]})
    rr = {o["value"]: o["count"] for o in f["review_result"]}
    assert rr.get("APPROVED") == 3 and rr.get("REJECTED") == 1 and "CONDITIONAL_PRICE" not in rr


@pytest.mark.parametrize("text,ok", [
    ("제외국 3개국 이상에 등재되어 경제성 평가 자료 제출 생략 가능 약제에 해당함.", True),
    ("경제성평가자료 제출 생략 가능 약제에 해당하는 것으로 검토됨", True),
    ("제6조의 2에 따른 경제성 평가 자료 제출 생략 가능 약제로 수용 가능함", True),
    ("- (경제성평가 생략) 신청품은 항암제로", True),
    ("경제성 평가 자료 제출 생략 가능 약제에 해당하지 않음.", False),
    ("6조의 2(경제성 평가 자료 생략가능약제)에 해당한다고 보기 어려우므로", False),
    ("경제성평가 자료 제출 생략 가능 약제임을 주장하였으 나, 인정하기는 어려움", False),
    ("fedratinib은 경제성평가 자료 제출 생략 가능 약제로 심의되어", False),           # 비교약 설명
    ("- 경제성평가 자료 제출 생략 가능 약제 해당 여부 ▪신청품은", False),              # 제목만
])
def test_pe_text_requires_affirmative_statement(text, ok):
    assert ai._pe_text_affirmed(text) is ok


# ── 구조 조건 ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("q,keys", [
    ("경평면제 약제 중 대상 환자 수가 기재된 사례", {"pe_waiver"}),
    ("경제성평가 자료제출 생략 품목의 환자수", {"pe_waiver"}),
    ("위험분담 적용 약제의 환급률 언급", {"has_rsa"}),
    ("비급여된 약제의 사유", {"rejected"}),
    ("급여된 약제와 비급여 약제의 차이", set()),                     # 비교 질문은 결과 조건 미적용
    ("대상 환자 수 추정 방법", set()),
])
def test_structured_conditions_detection(q, keys):
    assert {c["key"] for c in ai.structured_conditions(q)} == keys


def _llm_accept_all(system, user, mt):
    if "분석을 설계한다" in system:
        return ({"interpretation": "대상 환자 수", "terms": ["환자 수", "대상 환자", "환자수"], "stems": ["환자"],
                 "categories": [{"key": "pt", "label": "대상 환자 수", "definition": "환자 수 추정"}]}, {"in": 10, "out": 10})
    # 일부러 모든 문단을 해당으로 판정 — 범위 제한은 코드가 책임져야 한다
    import re
    texts = re.findall(r"\[문단 \d+\] \(보고서: [^)]*\)\n(.+)", user)
    return ({"items": [{"p": i + 1, "category": "pt", "strength": "strong", "quote": t[:20], "reason": "환자 수"} for i, t in enumerate(texts)]},
            {"in": 10, "out": 10})


def test_pe_waiver_question_excludes_non_waived_reports(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    out = ai.submit("a@x.com", "경평면제 약제 중 대상 환자 수가 기재된 사례", [1, 2, 3, 4, 5], llm=_llm_accept_all, run_async=False)
    res = ai.get_job(out["job_id"], "a@x.com")["result"]
    assert sorted(h["report_id"] if "report_id" in h else h["id"] for h in res["hits"]) == [1, 2, 3]
    assert res["scope"]["reports_before_conditions"] == 5 and res["scope"]["reports"] == 3
    c = res["conditions"][0]
    assert c["key"] == "pe_waiver" and c["matched"] == 3
    assert set(c["bases"]) == {"DB 경평면제 표시", "DB 급여트랙(경제성평가 생략)", "본문 문구(경제성평가 자료제출 생략)"}
    assert any("먼저 걸렀습니다" in x for x in res["limitations"])
    wb_bytes = ai.build_xlsx(res)
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(wb_bytes))
    assert any(r[0].value == "적용한 조건" for r in wb["요약"].iter_rows())
    assert wb["근거 표"].cell(row=1, column=10).value == "조건 충족 근거"


def test_condition_with_no_match_returns_empty_without_llm(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    def never(*a):
        raise AssertionError("LLM 호출 금지")
    out = ai.submit("a@x.com", "경평면제 약제의 환자 수", [4, 5], llm=never, run_async=False)
    res = ai.get_job(out["job_id"], "a@x.com")["result"]
    assert res["hits"] == [] and res["scope"]["reports"] == 0 and res["scope"]["reports_before_conditions"] == 2


def test_list_jobs_is_owner_scoped_and_summarised(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    ai.submit("a@x.com", "경평면제 약제 중 대상 환자 수가 기재된 사례", [1, 2, 3, 4, 5], llm=_llm_accept_all, run_async=False)
    ai.submit("b@x.com", "대상 환자 수 추정 방법", [1, 2], llm=_llm_accept_all, run_async=False)
    a = ai.list_jobs("a@x.com")
    assert len(a) == 1 and a[0]["status"] == "done" and a[0]["hit_reports"] == 3
    assert a[0]["scope_count"] == 5 and a[0]["conditions"] == ["경평면제(경제성평가 자료제출 생략)"]
    assert "result" not in a[0]
    assert [j["question"] for j in ai.list_jobs("b@x.com")] == ["대상 환자 수 추정 방법"]


# ── 목록 엑셀 ─────────────────────────────────────────────────────────────
def test_list_xlsx_keeps_order_and_translates_values():
    from openpyxl import load_workbook
    from api.analog_extra_routes import build_analog_list_xlsx
    rows = [{"id": 9, "brand_name": "B", "review_result": "REJECTED", "pe_waiver": 1, "has_rsa": 0, "rsa_types": '["환급형"]', "post_url": "http://x/9"},
            {"id": 3, "brand_name": "A", "review_result": "APPROVED", "pe_waiver": 0, "has_rsa": 1}]
    wb = load_workbook(io.BytesIO(build_analog_list_xlsx(rows, "질환군: 희귀")))
    ws = wb["검색 결과"]
    hdr = [c.value for c in ws[1]]
    row2 = dict(zip(hdr, [c.value for c in ws[2]]))
    assert row2["ID"] == 9 and row2["심의결과"] == "비급여" and row2["경평면제"] == "예" and row2["위험분담 유형"] == "환급형"
    assert ws.cell(row=3, column=1).value == 3
    assert wb["조건"]["B2"].value == "질환군: 희귀"


# ── 분석 요청 보완 파일 ────────────────────────────────────────────────────
def test_supplement_file_only_on_completed(tmp_path):
    from agents.analysis_requests.store import AnalysisRequestStore, Conflict
    st = AnalysisRequestStore(tmp_path / "a.db", files_root=tmp_path / "files")
    with st.connect() as c:
        c.execute("INSERT INTO analysis_requests(id,requester,question,status,created_at,updated_at) VALUES('r1','u@x','q','completed','t','t')")
        c.execute("INSERT INTO analysis_requests(id,requester,question,status,created_at,updated_at) VALUES('r2','u@x','q','queued','t','t')")
    out = st.add_supplement_file("r1", "admin@x", "v2.xlsx", b"PK\x03\x04data", "건정심 보완")
    assert out["filename"] == "v2.xlsx" and (tmp_path / "files").exists()
    with st.connect() as c:
        ev = c.execute("SELECT event, detail FROM analysis_request_events WHERE request_id='r1'").fetchall()
    assert ev[-1][0] == "supplement_added" and "건정심 보완" in ev[-1][1]
    with pytest.raises(Conflict):
        st.add_supplement_file("r2", "admin@x", "v2.xlsx", b"PK", "")
    with pytest.raises(ValueError):
        st.add_supplement_file("r1", "admin@x", "x.exe", b"MZ", "")
