"""등재 아날로그 보조 API (2026-11-09 사용자 요청).

- POST /api/analog/export.xlsx — 화면에 보이는 검색 결과 목록 전체를 엑셀로(요청 ids 순서 유지, 최대 1000건).
- GET  /api/analog/ai-analyze/history — 내 AI 분석 이력(최근순 요약). 다시 열면 기존 /api/analog/ai-analyze/<id>.
server.py 를 크게 건드리지 않도록 해외약가 블루프린트에 중첩 등록한다.
"""
from __future__ import annotations

import io
import json
from datetime import datetime

from flask import Blueprint, jsonify, request, send_file

from api.auth import require_auth

EXPORT_MAX = 1000

# (칼럼, 머리글, 너비)
_COLS = [
    ("id", "ID", 7), ("brand_name", "제품명", 26), ("generic_name", "성분(한글)", 20), ("generic_name_en", "성분(영문)", 22),
    ("company", "제약사", 18), ("session_date", "약평위일", 11), ("review_result", "심의결과", 18),
    ("reimbursement_track_ko", "급여 트랙", 24), ("pe_waiver", "경평면제", 8), ("has_rsa", "위험분담", 8),
    ("rsa_types", "위험분담 유형", 18), ("disease_category", "질환군", 10), ("disease_category_detail", "세부 질환군", 14),
    ("disease_name_ko", "질환", 26), ("cancer_type", "암종", 14), ("line_of_therapy", "치료차수", 10),
    ("coverage_gap_type", "급여범위(허가 대비)", 12), ("approval_driver", "승인 동인", 14),
    ("mfds_permit_date", "식약처 허가일", 12), ("first_reimbursement_date", "최초 급여일", 12),
    ("lag_days_approval_to_reimb", "허가→급여(일)", 10), ("post_url", "약평위 게시물", 40), ("file_name", "보고서 파일", 36),
]
_RESULT_KO = {"APPROVED": "급여", "CONDITIONAL_PRICE": "조건부 급여(평가금액 수용 시)", "CONDITIONAL_APPROVED": "조건부 급여",
              "APPROVED_WITH_POSTMARKET": "사후관리 조건부 급여", "REJECTED": "비급여", "UNKNOWN": "미상"}


def _cell(col, v):
    if v is None:
        return ""
    if col == "review_result":
        return _RESULT_KO.get(v, v)
    if col in ("pe_waiver", "has_rsa"):
        return "예" if v in (1, "1", True) else "아니오"
    if col == "rsa_types":
        try:
            arr = json.loads(v) if isinstance(v, str) else v
            return ", ".join(map(str, arr)) if isinstance(arr, list) else str(v)
        except Exception:
            return str(v)
    return v


def build_analog_list_xlsx(rows: list[dict], context: str = "") -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    wb = Workbook()
    ws = wb.active
    ws.title = "검색 결과"
    ws.append([h for _, h, _ in _COLS])
    for c in ws[1]:
        c.font = Font(name="맑은 고딕", bold=True, color="FFFFFF", size=10)
        c.fill = PatternFill("solid", fgColor="1F3A5F")
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for r in rows:
        ws.append([_cell(col, r.get(col)) for col, _, _ in _COLS])
    for i, (col, _, w) in enumerate(_COLS, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.font = Font(name="맑은 고딕", size=10)
            c.alignment = Alignment(vertical="top", wrap_text=False)
    url_idx = [i for i, (col, _, _) in enumerate(_COLS, 1) if col == "post_url"][0]
    for row in ws.iter_rows(min_row=2, min_col=url_idx, max_col=url_idx):
        for c in row:
            if c.value:
                c.hyperlink = c.value
                c.font = Font(name="맑은 고딕", size=10, color="0563C1", underline="single")
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = ws.dimensions
    info = wb.create_sheet("조건")
    info.append(["항목", "내용"])
    info.append(["검색 조건", context or "(없음)"])
    info.append(["건수", len(rows)])
    info.append(["내려받은 시각", datetime.now().strftime("%Y-%m-%d %H:%M")])
    info.append(["출처", "MA AI Dossier 등재 아날로그 (약평위 평가결과 공개 보고서 기반)"])
    info.column_dimensions["A"].width = 14
    info.column_dimensions["B"].width = 100
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_analog_extra_blueprint() -> Blueprint:
    bp = Blueprint("analog_extra", __name__)

    @bp.post("/api/analog/export.xlsx")
    @require_auth()
    def analog_list_export():
        body = request.get_json(silent=True) or {}
        ids = []
        for i in body.get("ids") or []:
            try:
                i = int(i)
            except (TypeError, ValueError):
                continue
            if i not in ids:
                ids.append(i)
        if not ids:
            return jsonify({"error": "내려받을 결과가 없습니다."}), 400
        if len(ids) > EXPORT_MAX:
            return jsonify({"error": f"한 번에 {EXPORT_MAX}건까지 내려받을 수 있습니다."}), 400
        from agents.analog.store import _connect, ensure_schema
        ensure_schema()
        with _connect() as c:
            have = {r[1] for r in c.execute("PRAGMA table_info(analog_reports)")}
            cols = [col for col, _, _ in _COLS if col in have]
            q = ",".join("?" * len(ids))
            rows = {r["id"]: dict(r) for r in c.execute(
                f"SELECT {', '.join(cols)} FROM analog_reports WHERE id IN ({q})", ids).fetchall()}
        ordered = [rows[i] for i in ids if i in rows]
        data = build_analog_list_xlsx(ordered, str(body.get("context") or "")[:500])
        name = f"analog-search-{datetime.now():%Y%m%d-%H%M}.xlsx"
        resp = send_file(io.BytesIO(data), as_attachment=True, download_name=name,
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @bp.get("/api/analog/ai-analyze/history")
    @require_auth()
    def analog_ai_history():
        from agents.analog import ai_analyze as _ai
        u = request.user  # type: ignore[attr-defined]
        try:
            limit = int(request.args.get("limit", "30"))
        except ValueError:
            limit = 30
        return jsonify({"items": _ai.list_jobs(u["sub"], u.get("role") == "admin", limit)})

    return bp
