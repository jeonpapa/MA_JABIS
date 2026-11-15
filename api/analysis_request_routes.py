"""분석 요청 API — 사용자(작성·목록·상세·취소·답변·파일) + Aside 워커(price_agent: 큐·claim·heartbeat·되묻기·파일·완료·실패).

서버는 분석을 실행하지 않는다. build_price_request_blueprint 안에 중첩 등록한다(api/server.py 수정 불필요).
"""
from __future__ import annotations

import base64
import binascii
from functools import wraps
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

from api.auth import require_auth
from agents.analysis_requests.store import AnalysisRequestStore, Conflict, FILE_MAX_BYTES


def build_analysis_request_blueprint(db_path, files_root=None) -> Blueprint:
    bp = Blueprint("analysis_requests", __name__)
    store = AnalysisRequestStore(db_path, files_root)

    def guarded(fn):
        @wraps(fn)
        def wrapped(*a, **kw):
            try:
                return fn(*a, **kw)
            except KeyError:
                return jsonify(error="request not found"), 404
            except Conflict as e:
                return jsonify(error=str(e)), 409
            except (ValueError, TypeError) as e:
                return jsonify(error=str(e)), 400
        return wrapped

    def who():
        return request.user["sub"], request.user.get("role", "user")

    def body(limit=200_000):
        if request.content_length and request.content_length > limit:
            raise ValueError("payload too large")
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError("JSON object required")
        return data

    def lease():
        return request.headers.get("X-Analysis-Lease", "")

    def human_only():
        _, role = who()
        if role not in ("user", "admin"):
            raise KeyError("service accounts cannot use the user API")

    # ── 사용자 ──────────────────────────────────────────────────────────────────
    @bp.post("/api/analysis-requests")
    @require_auth()
    @guarded
    def create():
        human_only()
        user, _ = who()
        d = body()
        return jsonify(store.create(user, d.get("question"), d.get("context_url"), bool(d.get("share_team", True)))), 201

    @bp.get("/api/analysis-requests")
    @require_auth()
    @guarded
    def listing():
        human_only()
        user, role = who()
        return jsonify(items=store.listing(user, role))

    @bp.get("/api/analysis-requests/<rid>")
    @require_auth()
    @guarded
    def detail(rid):
        human_only()
        user, role = who()
        return jsonify(store.detail(rid, user, role))

    @bp.post("/api/analysis-requests/<rid>/cancel")
    @require_auth()
    @guarded
    def cancel(rid):
        human_only()
        user, role = who()
        return jsonify(store.cancel(rid, user, role))

    @bp.post("/api/analysis-requests/<rid>/reply")
    @require_auth()
    @guarded
    def reply(rid):
        human_only()
        user, _ = who()
        return jsonify(store.reply(rid, user, body().get("reply")))

    @bp.get("/api/analysis-requests/<rid>/files/<int:fid>")
    @require_auth()
    @guarded
    def download(rid, fid):
        human_only()
        user, role = who()
        meta, path = store.file_for(rid, fid, user, role)
        return send_file(path, mimetype=meta["content_type"], as_attachment=True, download_name=meta["filename"])

    # ── 워커 ────────────────────────────────────────────────────────────────────
    @bp.get("/api/analysis-agent/requests")
    @require_auth(role="price_agent")
    @guarded
    def agent_queue():
        return jsonify(requests=store.queue())

    @bp.post("/api/analysis-agent/requests/<rid>/claim")
    @require_auth(role="price_agent")
    @guarded
    def agent_claim(rid):
        return jsonify(store.claim(rid, who()[0]))

    @bp.post("/api/analysis-agent/requests/<rid>/heartbeat")
    @require_auth(role="price_agent")
    @guarded
    def agent_heartbeat(rid):
        return jsonify(store.heartbeat(rid, who()[0], lease()))

    @bp.post("/api/analysis-agent/requests/<rid>/ask")
    @require_auth(role="price_agent")
    @guarded
    def agent_ask(rid):
        return jsonify(store.ask(rid, who()[0], lease(), body().get("question")))

    @bp.post("/api/analysis-agent/requests/<rid>/files")
    @require_auth(role="price_agent")
    @guarded
    def agent_file(rid):
        d = body(limit=FILE_MAX_BYTES * 2)
        try:
            content = base64.b64decode(d.get("content_b64") or "", validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("content_b64 must be base64")
        return jsonify(store.add_file(rid, who()[0], lease(), d.get("filename"), content))

    @bp.post("/api/admin/analysis-requests/<rid>/files")
    @require_auth(role="admin")
    @guarded
    def admin_supplement_file(rid):
        """완료된 요청에 보완 파일 추가(관리자). 요청자·팀 공개 범위는 원래 요청 설정을 따른다."""
        d = body(limit=FILE_MAX_BYTES * 2)
        try:
            content = base64.b64decode(d.get("content_b64") or "", validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("content_b64 must be base64")
        return jsonify(store.add_supplement_file(rid, who()[0], d.get("filename"), content, d.get("note") or ""))

    @bp.post("/api/analysis-agent/requests/<rid>/complete")
    @require_auth(role="price_agent")
    @guarded
    def agent_complete(rid):
        return jsonify(store.complete(rid, who()[0], lease(), body()))

    @bp.post("/api/analysis-agent/requests/<rid>/fail")
    @require_auth(role="price_agent")
    @guarded
    def agent_fail(rid):
        return jsonify(store.fail(rid, who()[0], lease(), body().get("reason")))

    return bp
