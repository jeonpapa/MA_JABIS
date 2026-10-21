"""등재 아날로그 AI 질문형 분석 — LLM 모킹. 원문 검증·범위·한도·캐시·엑셀."""
import io
import sqlite3

from agents.analog import ai_analyze as ai
from agents.analog import store

BODY = ("▢ 효능 효과 - 판상 건선 치료\n"
        "○ 비용 효과성 - 신청품과 동일 계열의 P-CAB 약제에 대해 교과서 및 임상진료지침에서 위식도역류질환 치료에 사용되는 약제로 언급되어 있음.\n"
        "- 관련 학회에서는 같은 계열로서 임상비교연구가 수행된 dulaglutide 수준의 급여범위 인정이 적절하다는 의견을 제시함.\n"
        "- 재정영향은 비공개임.")


def fake_llm(system, user, max_tokens):
    if "분석을 설계한다" in system:
        return ({"interpretation": "같은 계열의 근거를 준용한 표현을 찾는다",
                 "terms": ["동일 계열", "같은 계열", "계열 약제", "class effect", "기전이 동일"], "stems": ["계열", "기전"],
                 "categories": [{"key": "class_evidence", "label": "계열 근거 준용", "definition": "같은 계열 근거를 신청품에 적용"}]},
                {"in": 300, "out": 120})
    # 판정: 문단 1번은 유효 인용, 2번은 원문에 없는 인용(환각), 3번은 없는 분류, 99번은 범위 밖
    return ({"items": [
        {"p": 1, "category": "class_evidence", "strength": "strong", "quote": "동일 계열의 P-CAB 약제에 대해 교과서 및 임상진료지침에서", "reason": "같은 계열 약제의 근거를 인용"},
        {"p": 1, "category": "class_evidence", "quote": "이 약은 모든 PPI와 같은 계열 효과를 가진다", "reason": "환각"},
        {"p": 1, "category": "nope", "quote": "동일 계열의 P-CAB 약제에 대해", "reason": "없는 분류"},
        {"p": 99, "category": "class_evidence", "quote": "동일 계열의 P-CAB 약제에 대해", "reason": "범위 밖"}]},
        {"in": 900, "out": 200})


def _reports(n=1, body=BODY):
    return [{"id": i, "brand_name": f"약{i}", "session_date": f"2024-0{i}-01", "review_result": "APPROVED",
             "post_url": "http://x/%d" % i, "file_name": f"f{i}.pdf", "file_hash": f"h{i}", "body_text": body} for i in range(1, n + 1)]


def test_split_passages_splits_on_bullets_respects_limit_and_loses_no_text():
    ps = ai.split_passages(BODY, max_chars=60)
    assert len(ps) >= 3 and all(len(p) <= 60 for p in ps)
    assert ai._compact("".join(ps)) == ai._compact(BODY)                 # 본문 손실 없음
    assert len(ai.split_passages(BODY)) == 1                              # 짧은 본문은 한 문단
    long = "가나다. " * 400
    out = ai.split_passages(long, max_chars=200)
    assert len(out) > 5 and all(len(p) <= 200 for p in out)


def test_candidates_match_despite_pdf_spacing_and_case():
    spaced = "신청품과 동 일  계 열의 약제가 있음. 무관한 문단입니다."
    c = ai.candidate_passages(spaced, ["동일 계열"])
    assert len(c) == 1
    assert ai.candidate_passages("CLASS  Effect 가 언급됨", ["class effect"])
    assert ai.candidate_passages("아무 관련 없음", ["동일 계열"]) == []


def test_quote_is_verbatim_requires_real_text_and_minimum_length():
    p = "신청품과 동일 계열의 P-CAB 약제에 대해 교과서에서 언급됨"
    assert ai.quote_is_verbatim("동일 계열의 P-CAB 약제에 대해", p)
    assert ai.quote_is_verbatim("동일  계열의  P-CAB 약제에 대해", p)          # 공백 차이 허용
    assert not ai.quote_is_verbatim("모든 PPI와 같은 계열 효과", p)
    assert not ai.quote_is_verbatim("동일 계열", p)                              # 8자 미만은 근거로 인정 안 함


def test_run_analysis_keeps_only_verified_quotes_and_reports_dropped():
    res = ai.run_analysis("계열 효과 표현이 있나?", _reports(2), llm=fake_llm)
    assert res["scope"]["reports"] == 2
    assert res["dropped_unverified"] == 3                       # 판정 1회(배치 1개) × (환각·없는 분류·범위 밖)
    assert len(res["hits"]) == 1 and len(res["hits"][0]["items"]) == 1
    for h in res["hits"]:
        for it in h["items"]:
            assert it["category"] == "class_evidence"
            assert "P-CAB" in it["quote"]
    assert res["by_category"][0]["quotes"] == sum(len(h["items"]) for h in res["hits"])
    assert res["terms"] and res["limitations"] and res["cost_usd"] > 0


def test_run_analysis_guards():
    import pytest as _p
    for q, rep, code in [("", _reports(1), "EMPTY"), ("가" * 301, _reports(1), "TOO_LONG"),
                         ("질문", [], "NO_SCOPE"), ("질문", _reports(ai.MAX_REPORTS + 1, body="x"), "SCOPE_TOO_BIG")]:
        try:
            ai.run_analysis(q, rep, llm=fake_llm)
            assert False, code
        except ai.AnalysisError as e:
            assert e.code == code


def test_plan_failure_is_a_clear_error():
    def bad(system, user, mt):
        return ({"interpretation": "x", "terms": ["가"], "categories": []}, {"in": 1, "out": 1})
    try:
        ai.run_analysis("질문", _reports(1), llm=bad)
        assert False
    except ai.AnalysisError as e:
        assert e.code == "PLAN_FAILED"


def test_run_cost_cap_blocks_before_judging(monkeypatch):
    monkeypatch.setattr(ai, "RUN_COST_CAP_USD", 0.0000001)
    calls = []

    def llm(system, user, mt):
        calls.append(1)
        return fake_llm(system, user, mt)
    try:
        ai.run_analysis("질문", _reports(2), llm=llm)
        assert False
    except ai.AnalysisError as e:
        assert e.code == "RUN_COST_CAP"
    assert len(calls) == 1                                       # plan 만 호출, 판정 호출 없음


def _db(tmp_path, monkeypatch, n=2):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.db")
    ai.ensure_job_schema()
    with sqlite3.connect(store.DB_PATH) as c:
        for r in _reports(n):
            c.execute("INSERT INTO analog_reports (id, file_name, file_hash, brand_name, session_date, review_result, post_url, body_text) "
                      "VALUES (?,?,?,?,?,?,?,?)", (r["id"], r["file_name"], r["file_hash"], r["brand_name"], r["session_date"], r["review_result"], r["post_url"], r["body_text"]))


def test_submit_runs_caches_and_counts_usage(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    out = ai.submit("a@x.com", "계열 효과 표현이 있나?", [1, 2], llm=fake_llm, run_async=False)
    assert out["status"] == "running" and not out["cached"]
    job = ai.get_job(out["job_id"], "a@x.com")
    assert job["status"] == "done" and job["result"]["hits"]
    assert ai.get_job(out["job_id"], "b@x.com") is None                 # 남의 작업은 못 봄
    assert ai.get_job(out["job_id"], "b@x.com", is_admin=True)["status"] == "done"
    u = ai.usage_today("a@x.com")
    assert u["used"] == 1 and u["remaining"] == ai.DAILY_PER_USER - 1
    again = ai.submit("a@x.com", "계열 효과 표현이 있나?", [2, 1], llm=fake_llm, run_async=False)   # 같은 질문·같은 범위
    assert again["cached"] and again["job_id"] == out["job_id"]
    assert ai.usage_today("a@x.com")["used"] == 1                       # 캐시는 한도 소모 없음


def test_daily_limits_and_busy_and_scope_errors(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    monkeypatch.setattr(ai, "DAILY_PER_USER", 1)
    ai.submit("a@x.com", "질문 하나", [1], llm=fake_llm, run_async=False)
    for who, q, ids, code in [("a@x.com", "다른 질문", [1], "USER_DAILY"), ("c@x.com", "", [1], "EMPTY"),
                              ("c@x.com", "질문", [], "NO_SCOPE"), ("c@x.com", "질문", [999], "NO_SCOPE")]:
        try:
            ai.submit(who, q, ids, llm=fake_llm, run_async=False)
            assert False, code
        except ai.AnalysisError as e:
            assert e.code == code
    with sqlite3.connect(store.DB_PATH) as c:                           # 진행 중 작업이 최대치면 BUSY
        for i in range(ai.MAX_RUNNING):
            c.execute("INSERT INTO analog_ai_job(id,owner,question,scope_ids,cache_key,model,status,created_at,updated_at) "
                      "VALUES (?,?,?,?,?,?,'running',?,?)", (f"r{i}", f"z{i}@x.com", "q", "[1]", f"k{i}", "m", ai._now(), ai._now()))
    try:
        ai.submit("d@x.com", "새 질문", [1], llm=fake_llm, run_async=False)
        assert False
    except ai.AnalysisError as e:
        assert e.code == "BUSY"


def test_stale_running_job_is_failed(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    with sqlite3.connect(store.DB_PATH) as c:
        c.execute("INSERT INTO analog_ai_job(id,owner,question,scope_ids,cache_key,model,status,created_at,updated_at) "
                  "VALUES ('old','a@x.com','q','[1]','k','m','running','2020-01-01T00:00:00+09:00','2020-01-01T00:00:00+09:00')")
    job = ai.get_job("old", "a@x.com")
    assert job["status"] == "failed" and job["error_code"] == "STALE"


def test_llm_failure_marks_job_failed_not_running(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)

    def boom(system, user, mt):
        raise RuntimeError("boom")
    out = ai.submit("a@x.com", "질문", [1], llm=boom, run_async=False)
    job = ai.get_job(out["job_id"], "a@x.com")
    assert job["status"] == "failed" and job["error_code"] == "INTERNAL" and "boom" not in job["error"]


def test_build_xlsx_has_three_sheets_and_quotes():
    from openpyxl import load_workbook
    res = ai.run_analysis("계열 효과 표현이 있나?", _reports(2), llm=fake_llm)
    wb = load_workbook(io.BytesIO(ai.build_xlsx(res)))
    assert wb.sheetnames == ["요약", "근거 표", "방법·한계"]
    rows = list(wb["근거 표"].iter_rows(min_row=2, values_only=True))
    assert rows and all("P-CAB" in r[5] for r in rows) and rows[0][0] == "강" and rows[0][1] == "계열 근거 준용"


def test_cache_hit_by_another_user_gives_own_readable_job_and_no_usage(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    first = ai.submit("a@x.com", "계열 효과 표현이 있나?", [1, 2], llm=fake_llm, run_async=False)
    other = ai.submit("b@x.com", "계열  효과 표현이 있나?", [2, 1], llm=fake_llm, run_async=False)   # 공백·순서만 다름
    assert other["cached"] and other["job_id"] != first["job_id"]
    job = ai.get_job(other["job_id"], "b@x.com")
    assert job["status"] == "done" and job["result"]["hits"]
    assert ai.get_job(other["job_id"], "a@x.com") is None               # 복사본은 b 소유
    assert ai.usage_today("b@x.com")["used"] == 0


def test_stems_widen_candidates_without_loosening_the_judge():
    body = "신청품은 새로운 계열 약제로 대체약제 가중평균금액에 비율을 곱한 금액임. 무관한 문단입니다."
    assert ai.candidate_passages(body, ["동일 계열", "class effect"]) == []                  # 구체 표현만으로는 놓친다
    c = ai.candidate_passages(body, ["동일 계열", "class effect"], stems=["계열"])           # 어근이 후보로 건져 올린다
    assert len(c) == 1 and c[0]["score"] == 1
    both = "동일 계열 약제와 계열 기반 평가임. 별개 문단. 계열만 언급."
    s_specific = ai.candidate_passages("동일 계열 약제가 있음", ["동일 계열"], stems=["계열"])[0]["score"]
    s_stem_only = ai.candidate_passages("새로운 계열 약제가 있음", ["동일 계열"], stems=["계열"])[0]["score"]
    assert s_specific > s_stem_only                                                         # 구체 표현 일치가 더 높은 우선순위


def test_plan_parses_stems_and_drops_duplicates_of_terms():
    def llm(system, user, mt):
        return ({"interpretation": "x", "terms": ["동일 계열", "같은 계열", "계열 약제"], "stems": ["계열", "동일 계열", "기전", "가"],
                 "categories": [{"key": "a", "label": "라벨", "definition": "d"}]}, {"in": 1, "out": 1})
    pl, _ = ai.plan("질문", llm)
    assert pl["stems"] == ["계열", "기전"]


def test_strength_defaults_to_weak_when_missing_and_sorting_puts_strong_first():
    def llm(system, user, mt):
        if "분석을 설계한다" in system:
            return fake_llm(system, user, mt)
        return ({"items": [
            {"p": 1, "category": "class_evidence", "quote": "동일 계열의 P-CAB 약제에 대해 교과서", "reason": "r"},                       # strength 누락
            {"p": 2, "category": "class_evidence", "strength": "strong", "quote": "동일 계열의 P-CAB 약제에 대해 교과서", "reason": "r"},
            {"p": 3, "category": "class_evidence", "strength": "엉뚱", "quote": "동일 계열의 P-CAB 약제에 대해 교과서", "reason": "r"}]},
            {"in": 10, "out": 10})
    reps = _reports(3)
    res = ai.run_analysis("질문", reps, llm=llm)
    by_report = {h["report_id"]: h["top_strength"] for h in res["hits"]}
    assert by_report == {1: "weak", 2: "strong", 3: "weak"}                                   # 누락·이상 값 = weak
    assert res["hits"][0]["report_id"] == 2                                                   # 강한 근거 보고서가 맨 앞
    assert res["strength_counts"] == {"strong": 1, "moderate": 0, "weak": 2}
    assert res["strength_reports"] == {"strong": 1, "moderate": 0, "weak": 2}
