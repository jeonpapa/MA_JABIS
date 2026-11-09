"""해외약가 모니터링 API (2026-11-10). 해외약가 요청 블루프린트에 중첩 등록한다(server.py 수정 없음).

사용자: 프로젝트 CRUD · 즉시 실행 · 회차 목록/상세/엑셀/메일 미리보기 (본인 프로젝트, admin 은 전체)
워커(price_agent): 서버 Gmail 을 못 쓸 때 회차 메일을 Gmail 로 보내는 큐 · lease · 본문 · 영수증
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from functools import wraps
from io import BytesIO

from flask import Blueprint, jsonify, request, send_file

from api.auth import require_auth
from agents.price_monitor import schedule as sch, telegram
from agents.price_monitor.store import MonitorStore, Conflict, allowed_domains, dump, now as now_iso
from agents.price_monitor.tick import mail_content
from agents.price_request_store import PriceRequestStore, VERDICTS

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def build_price_monitor_blueprint(db_path) -> Blueprint:
    bp = Blueprint("price_monitor", __name__)
    ms, ps = MonitorStore(db_path), PriceRequestStore(db_path)

    def guarded(fn):
        @wraps(fn)
        def wrapped(*a, **kw):
            try:
                return fn(*a, **kw)
            except KeyError:
                return jsonify(error="not found"), 404
            except Conflict as e:
                return jsonify(error=str(e)), 409
            except (ValueError, TypeError) as e:
                return jsonify(error=str(e)), 400
        return wrapped

    def who():
        u = request.user  # type: ignore[attr-defined]
        return u["sub"], u.get("role") == "admin"

    def body():
        d = request.get_json(silent=True)
        if not isinstance(d, dict):
            raise ValueError("JSON object required")
        return d

    @bp.get("/api/price-monitor/config")
    @require_auth()
    def config():
        return jsonify(domains=allowed_domains(), countries=list(sch.A8), hours_per_item_a8=sch.HOURS_PER_ITEM_A8,
                       telegram=telegram.configured(), verdicts=VERDICTS, worker=ps.worker_status())

    @bp.get("/api/price-monitor/projects")
    @require_auth()
    @guarded
    def projects():
        actor, admin = who()
        return jsonify(projects=ms.list_projects(actor, admin))

    @bp.post("/api/price-monitor/projects")
    @require_auth()
    @guarded
    def create_project():
        actor, _ = who()
        b = body()
        p = ms.create_project(actor, b.get("name"), b.get("items"), b.get("countries") or list(sch.A8), b.get("schedule"), b.get("recipients"))
        return jsonify(project=p), 201

    @bp.get("/api/price-monitor/projects/<pid>")
    @require_auth()
    @guarded
    def get_project(pid):
        actor, admin = who()
        return jsonify(project=ms.get_project(pid, actor, admin), runs=ms.list_runs(pid))

    @bp.patch("/api/price-monitor/projects/<pid>")
    @require_auth()
    @guarded
    def patch_project(pid):
        actor, admin = who()
        b = body()
        patch = {k: b[k] for k in ("name", "items", "countries", "schedule", "recipients", "status") if k in b}
        return jsonify(project=ms.update_project(pid, actor, admin, patch))

    @bp.delete("/api/price-monitor/projects/<pid>")
    @require_auth()
    @guarded
    def delete_project(pid):
        actor, admin = who()
        ms.delete_project(pid, actor, admin)
        return jsonify(ok=True)

    @bp.post("/api/price-monitor/projects/<pid>/run-now")
    @require_auth()
    @guarded
    def run_now(pid):
        """지금 시작 — 수집이 끝나는 대로 발송. 예약만 된(시작 전) 회차는 취소하고 그 다음 일정으로 다시 잡힌다."""
        actor, admin = who()
        p = ms.get_project(pid, actor, admin)
        if p["status"] != "active":
            raise Conflict("일시정지된 프로젝트입니다")
        with ms.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            busy = c.execute("SELECT id FROM monitor_runs WHERE project_id=? AND status IN ('collecting','ready','mailing')", (pid,)).fetchone()
            if busy:
                raise Conflict("이미 진행 중인 회차가 있습니다")
            c.execute("UPDATE monitor_runs SET status='cancelled',error='즉시 실행으로 대체',updated_at=? WHERE project_id=? AND status='scheduled'", (now_iso(), pid))
            import os
            rid, ts = os.urandom(16).hex(), now_iso()
            c.execute("INSERT INTO monitor_runs(id,project_id,due_at,start_at,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                      (rid, pid, ts, ts, "scheduled", ts, ts))
            ms.event(c, pid, rid, actor, "run_now")
        # 다음 10분 tick 을 기다리지 않고 바로 요청을 만든다
        from agents.price_monitor.tick import start_runs
        start_runs(ms, ps, datetime.now(timezone.utc))
        return jsonify(run=ms.get_run(rid, actor, admin, with_snapshot=False)), 201

    def _progress(run):
        out = []
        for q, rids in (run.get("requests") or {}).items():
            details = []
            for rid in rids:
                try:
                    d = ps.detail(rid)
                except KeyError:
                    continue
                details.append({"id": d["id"], "status": d["status"],
                                "countries": [{k: x.get(k) for k in ("country", "status", "verdict")} for x in d["country_results"]]})
            out.append({"query": q, "requests": details})
        return out

    @bp.get("/api/price-monitor/runs/<rid>")
    @require_auth()
    @guarded
    def get_run(rid):
        actor, admin = who()
        run = ms.get_run(rid, actor, admin)
        if run["status"] in ("collecting", "ready", "mailing"):
            run["progress"] = _progress(run)
        return jsonify(run=run)

    @bp.get("/api/price-monitor/runs/<rid>/export.xlsx")
    @require_auth()
    @guarded
    def run_export(rid):
        actor, admin = who()
        ms.get_run(rid, actor, admin, with_snapshot=False)
        content = mail_content(ms, rid)
        resp = send_file(BytesIO(content["attachment"]), mimetype=XLSX, as_attachment=True, download_name=content["attachment_name"], max_age=0)
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @bp.get("/api/price-monitor/runs/<rid>/mail-preview")
    @require_auth()
    @guarded
    def mail_preview(rid):
        actor, admin = who()
        ms.get_run(rid, actor, admin, with_snapshot=False)
        c = mail_content(ms, rid)
        return jsonify(subject=c["subject"], html=c["html"], recipients=c["recipients"])

    # ── 워커 Gmail 발송 (서버 Gmail 토큰이 없을 때) ───────────────────────
    def mlease():
        return request.headers.get("X-Monitor-Lease", "")

    @bp.get("/api/price-agent/monitor-mails")
    @require_auth(role="price_agent")
    @guarded
    def worker_mail_queue():
        return jsonify(runs=ms.mail_queue())

    @bp.post("/api/price-agent/monitor-runs/<rid>/mail-claim")
    @require_auth(role="price_agent")
    @guarded
    def worker_mail_claim(rid):
        return jsonify(ms.mail_claim(rid, who()[0]))

    @bp.get("/api/price-agent/monitor-runs/<rid>/mail")
    @require_auth(role="price_agent")
    @guarded
    def worker_mail_content(rid):
        ms.mail_check_lease(rid, who()[0], mlease())
        c = mail_content(ms, rid)
        return jsonify(subject=c["subject"], recipients=c["recipients"], html=c["html"], text=c["text"],
                       attachment_name=c["attachment_name"], attachment_b64=base64.b64encode(c["attachment"]).decode(),
                       dashboard_url=c["dashboard_url"])

    @bp.post("/api/price-agent/monitor-runs/<rid>/mail-notify")
    @require_auth(role="price_agent")
    @guarded
    def worker_mail_notify(rid):
        b = body()
        return jsonify(run=ms.mail_notify(rid, who()[0], mlease(), b.get("phase"), b.get("receipt")))

    return bp
