"""급여 기준 화면 API (심평원 항암 공고·일반약제 고시·KCD). 읽기 전용, 로그인 사용자(user·admin).

GET  /api/criteria/meta                       원문 기준일(통합본·최신 공고·약제 최신 시행일·상병마스터)
GET  /api/criteria/drug/search?q=             약제 → 항암 공고요법·허가초과·일반약제 급여기준
GET  /api/criteria/disease/suggest?q=         질환 이름 매칭 후보(급여기준이 있는 KCD 3단만)
POST /api/criteria/disease/clarify            {query, answers?, targets?} → LLM 해석·필터 자동 적용·확인 질문 (DB 실재 값만)
POST /api/criteria/disease/results            {targets, settings?, line?, markers?, drugs?, keywords?} → 결과 + 판정
POST /api/criteria/compare                    {drugs: [최대 3], base: 0, focus?} → 기준 약제 대비 칸·문단 정렬 diff

테이블이 아직 없으면(급여기준 DB 미반영 환경) 503 CRITERIA_DB_MISSING.
오류 JSON 은 {error, code}.
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from typing import Iterator

from flask import Blueprint, jsonify, request

from agents.hira_criteria import search as S
from api.auth import require_auth

log = logging.getLogger(__name__)
MAX_Q = 200


def _age(v):
    """{lo, hi, label, months} 검증 — 숫자 범위 0~1500개월, label 30자."""
    if not isinstance(v, dict):
        return None
    try:
        lo, hi = int(v.get("lo")), int(v.get("hi"))
    except (TypeError, ValueError):
        return None
    if not (0 <= lo <= hi <= 1500):
        return None
    m = v.get("months")
    return {"lo": lo, "hi": hi, "months": int(m) if isinstance(m, (int, float)) else None, "label": str(v.get("label") or "")[:30]}


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    c = S.connect()
    try:
        yield c
    finally:
        c.close()


def _q(name: str = "q") -> str:
    return (request.args.get(name) or "").strip()[:MAX_Q]


def build_hira_criteria_blueprint() -> Blueprint:
    bp = Blueprint("hira_criteria", __name__)

    @bp.errorhandler(sqlite3.OperationalError)
    def _db_missing(e):  # noqa: ANN001
        if "no such table" in str(e):
            return jsonify({"error": "급여기준 DB 가 아직 반영되지 않았습니다.", "code": "CRITERIA_DB_MISSING"}), 503
        log.exception("criteria db error")
        return jsonify({"error": "DB 오류", "code": "DB_ERROR"}), 500

    @bp.get("/api/criteria/meta")
    @require_auth()
    def meta():
        with _conn() as c:
            src = S._source_meta(c)
            counts = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                      for t in ("onco_criteria_regimen", "drug_criteria_item")}
        return jsonify({"source": src, "counts": counts})

    @bp.get("/api/criteria/drug/search")
    @require_auth()
    def drug_search():
        q = _q()
        if len(q) < 2:
            return jsonify({"error": "두 글자 이상 입력하세요", "code": "QUERY_SHORT"}), 400
        with _conn() as c:
            return jsonify(S.search_drug(c, q))

    @bp.get("/api/criteria/disease/suggest")
    @require_auth()
    def disease_suggest():
        q = _q()
        if not q:
            return jsonify({"candidates": []})
        with _conn() as c:
            return jsonify({"candidates": S.disease_candidates(c, q)})

    @bp.post("/api/criteria/disease/clarify")
    @require_auth()
    def disease_clarify():
        body = request.get_json(silent=True) or {}
        query = str(body.get("query") or "").strip()[:MAX_Q]
        targets = [str(t)[:12] for t in (body.get("targets") or []) if t][:8] or None
        if not query and not targets:
            return jsonify({"error": "질환을 입력하세요", "code": "QUERY_EMPTY"}), 400
        answers = body.get("answers") if isinstance(body.get("answers"), dict) else {}
        with _conn() as c:
            return jsonify(S.clarify(c, query, answers=answers, targets=targets))

    @bp.post("/api/criteria/disease/results")
    @require_auth()
    def disease_results():
        b = request.get_json(silent=True) or {}
        targets = [str(t)[:12] for t in (b.get("targets") or []) if t][:8]
        if not targets:
            return jsonify({"error": "질환을 선택하세요", "code": "TARGETS_EMPTY"}), 400
        line = b.get("line")
        line = int(line) if isinstance(line, (int, float, str)) and str(line).isdigit() else None
        lst = lambda k, n=20: [str(x)[:60] for x in (b.get(k) or []) if x not in (None, "")][:n]  # noqa: E731
        with _conn() as c:
            return jsonify(S.results(c, targets, settings=lst("settings"), line=line, markers=lst("markers"),
                                     drugs=lst("drugs", 6), keywords=lst("keywords", 6), age=_age(b.get("age"))))

    @bp.post("/api/criteria/compare")
    @require_auth()
    def compare():
        b = request.get_json(silent=True) or {}
        drugs = [str(d).strip()[:MAX_Q] for d in (b.get("drugs") or []) if str(d).strip()][:3]
        if len(drugs) < 2:
            return jsonify({"error": "비교할 약제를 2개 이상 입력하세요", "code": "DRUGS_FEW"}), 400
        base = b.get("base") if isinstance(b.get("base"), int) else 0
        focus = str(b.get("focus"))[:60] if b.get("focus") else None
        line = b.get("line")
        line = int(line) if isinstance(line, (int, float, str)) and str(line).isdigit() else None
        with _conn() as c:
            return jsonify(S.compare(c, drugs, base, focus, line))

    return bp
