"""모니터링 진행기 — 스케줄러가 10분마다 호출한다(price_monitor_tick).

1. 예약   : 활성 프로젝트마다 열린 회차가 없으면 다음 발송 시각으로 회차를 만든다(시작 = 발송 − 예상 수집시간 − 여유).
2. 시작   : 시작 시각이 되면 약제마다 해외약가 요청(origin=monitor:<run>)을 만든다. 워커가 기존 검증 경로로 수집한다.
3. 수집   : 모든 요청이 끝날 때까지 기다린다(사용자 결정: 덜 끝나면 발송을 미룬다). 접속 차단·실패 국가는 최대 2회 재요청.
4. 준비   : 끝나면 국가별 결과를 회차 스냅샷으로 고정하고 직전 발송 회차와 비교한다.
5. 발송   : 서버 Gmail 토큰이 유효하면 서버가 보내고, 아니면 워커(Aside)가 Gmail 로 보낸다(영수증 필수).
6. 텔레그램: 발송 하루 전 안내 · 발송 지연(12시간마다) · 완료 보고.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from agents.price_monitor import report, schedule as sch, telegram
from agents.price_monitor.diff import compare_runs
from agents.price_monitor.store import MonitorStore, dump, now as now_iso
from agents.price_request_store import PriceRequestStore

logger = logging.getLogger(__name__)
MAX_RETRIES = 2
TERMINAL = ("completed", "partial_completed", "blocked")
RETRY_VERDICTS = ("access_blocked",)
DELAY_ALERT_HOURS = 12
SNAP_FIELDS = ("product_name", "package_unit", "pack_count", "formulation_key", "dosage_strength", "local_price", "currency",
               "source_label", "source_url", "adjusted_price_krw", "exchange_rate", "searched_at", "id")


def base_url() -> str:
    return os.environ.get("PUBLIC_BASE_URL", "https://ma-ai-dossier.fly.dev").rstrip("/")


def run_url(pid: str, rid: str) -> str:
    return f"{base_url()}/international-pricing?view=monitor&project={pid}&run={rid}"


def _dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


# ── 1. 예약 ───────────────────────────────────────────────────────────────
def schedule_runs(ms: MonitorStore, at: datetime) -> list[str]:
    created = []
    with ms.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        for p in c.execute("SELECT * FROM monitor_projects WHERE status='active'").fetchall():
            if c.execute("SELECT 1 FROM monitor_runs WHERE project_id=? AND status IN ('scheduled','collecting','ready','mailing')", (p["id"],)).fetchone():
                continue
            n_items = c.execute("SELECT COUNT(*) FROM monitor_items WHERE project_id=? AND removed_at IS NULL", (p["id"],)).fetchone()[0]
            if not n_items:
                continue
            s, countries = json.loads(p["schedule"]), json.loads(p["countries"])
            last = c.execute("SELECT MAX(due_at) FROM monitor_runs WHERE project_id=? AND status IN ('sent','failed')", (p["id"],)).fetchone()[0]
            after = max(at, _dt(last)) if last else at
            due = sch.next_due(s, after)
            if not due:
                continue
            start = due - timedelta(hours=sch.lead_hours(n_items, countries))
            start = max(start, at)
            rid, ts = os.urandom(16).hex(), now_iso()
            c.execute("INSERT INTO monitor_runs(id,project_id,due_at,start_at,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                      (rid, p["id"], due.astimezone(timezone.utc).isoformat(timespec="seconds"),
                       start.astimezone(timezone.utc).isoformat(timespec="seconds"), "scheduled", ts, ts))
            ms.event(c, p["id"], rid, "system", "run_scheduled", f"due {due.isoformat()} start {start.isoformat()}")
            created.append(rid)
    return created


# ── 2. 시작 ───────────────────────────────────────────────────────────────
def start_runs(ms: MonitorStore, ps: PriceRequestStore, at: datetime) -> list[str]:
    started = []
    with ms.connect() as c:
        due_runs = c.execute("SELECT r.*, p.owner, p.recipients AS p_recipients, p.countries AS p_countries FROM monitor_runs r "
                             "JOIN monitor_projects p ON p.id=r.project_id WHERE r.status='scheduled' AND r.start_at<=? AND p.status='active'",
                             (at.astimezone(timezone.utc).isoformat(timespec="seconds"),)).fetchall()
        items_by = {r["id"]: [dict(x) for x in c.execute("SELECT query,identity,label FROM monitor_items WHERE project_id=? AND removed_at IS NULL ORDER BY id", (r["project_id"],))]
                    for r in due_runs}
    for r in due_runs:
        items, countries, recipients = items_by[r["id"]], json.loads(r["p_countries"]), json.loads(r["p_recipients"])
        requests = {}
        for it in items:
            ident = json.loads(it["identity"]) if it["identity"] else None
            query = it["query"]
            try:
                from agents.drug_identity import derive_identity_offline
                d = derive_identity_offline(query)
                if d:
                    query, ident = d["slug"], d
            except Exception:
                pass
            req = ps.create(query, "a8", countries, recipients[0], r["owner"], ident, origin="monitor:" + r["id"])
            requests[it["query"]] = [req["id"]]
            it["resolved_query"] = query
        with ms.connect() as c:
            c.execute("UPDATE monitor_runs SET status='collecting',items=?,countries=?,recipients=?,requests=?,started_at=?,updated_at=? WHERE id=? AND status='scheduled'",
                      (dump(items), dump(countries), dump(recipients), dump(requests), now_iso(), now_iso(), r["id"]))
            ms.event(c, r["project_id"], r["id"], "system", "run_started", f"{len(items)} requests")
        started.append(r["id"])
    return started


# ── 3·4. 수집 대기 · 재시도 · 스냅샷 ──────────────────────────────────────
def _latest_by_country(details: list[dict]) -> dict[str, tuple[dict, dict]]:
    """요청 목록(원요청 + 재요청, 오래된 순) → 국가별 마지막 결과."""
    out = {}
    for d in details:
        for cr in d["country_results"]:
            if cr["status"] != "pending" or cr["country"] not in out:
                out[cr["country"]] = (d, cr)
    return out


def _rows_for(ps: PriceRequestStore, rid: str, cc: str, row_ids: list[int]) -> list[dict]:
    if not row_ids:
        return []
    with ps.connect() as c:
        q = ",".join("?" * len(row_ids))
        got = c.execute(f"SELECT row_id,payload FROM price_request_price_snapshots WHERE request_id=? AND country=? AND row_id IN ({q})",
                        (rid, cc, *row_ids)).fetchall()
    rows = []
    for g in got:
        p = json.loads(g["payload"])
        rows.append({k: p.get(k) for k in SNAP_FIELDS})
    return sorted(rows, key=lambda x: (str(x.get("formulation_key")), x.get("pack_count") or 0, x.get("local_price") or 0))


def advance_collecting(ms: MonitorStore, ps: PriceRequestStore) -> list[str]:
    ready = []
    with ms.connect() as c:
        runs = [dict(r) for r in c.execute("SELECT * FROM monitor_runs WHERE status='collecting'")]
    for r in runs:
        requests, retries = json.loads(r["requests"] or "{}"), json.loads(r["retries"] or "{}")
        items, countries = json.loads(r["items"] or "[]"), json.loads(r["countries"] or "[]")
        all_done, changed = True, False
        per_item = {}
        for it in items:
            rids = requests.get(it["query"], [])
            details = [ps.detail(x) for x in rids]
            if any(d["status"] not in TERMINAL for d in details):
                all_done = False
                continue
            latest = _latest_by_country(details)
            # 접속 차단·판정 없는 실패 → 그 국가만 재요청(최대 2회)
            redo = [cc for cc in countries
                    if cc in latest and (latest[cc][1].get("verdict") in RETRY_VERDICTS
                                         or (latest[cc][1]["status"] in ("failed", "pending") and not latest[cc][1].get("verdict")))]
            if redo and retries.get(it["query"], 0) < MAX_RETRIES:
                base = details[0]
                req = ps.create(base["query"], "a8", redo, base["notify_email"], base["requester"], base.get("identity"), origin="monitor:" + r["id"])
                requests[it["query"]] = rids + [req["id"]]
                retries[it["query"]] = retries.get(it["query"], 0) + 1
                changed, all_done = True, False
                with ms.connect() as c:
                    ms.event(c, r["project_id"], r["id"], "system", "country_retry", f"{it['query']}: {','.join(redo)}")
                continue
            per_item[it["query"]] = latest
        if changed:
            with ms.connect() as c:
                c.execute("UPDATE monitor_runs SET requests=?,retries=?,updated_at=? WHERE id=?", (dump(requests), dump(retries), now_iso(), r["id"]))
        if not all_done:
            continue
        snap_items = []
        for it in items:
            latest = per_item.get(it["query"], {})
            cmap = {}
            for cc in countries:
                if cc not in latest:
                    cmap[cc] = {"status": "pending", "rows": []}
                    continue
                d, cr = latest[cc]
                cmap[cc] = {"status": cr["status"], "reason": cr.get("reason"), "verdict": cr.get("verdict"),
                            "verdict_sources": cr.get("verdict_sources") or [], "request_id": d["id"],
                            "rows": _rows_for(ps, d["id"], cc, cr.get("row_ids") or [])}
            snap_items.append({"query": it["query"], "label": it.get("label") or it["query"], "countries": cmap})
        snapshot = {"items": snap_items, "countries": countries, "built_at": now_iso()}
        with ms.connect() as c:
            prev = c.execute("SELECT due_at,snapshot FROM monitor_runs WHERE project_id=? AND status='sent' AND due_at<? ORDER BY due_at DESC LIMIT 1",
                             (r["project_id"], r["due_at"])).fetchone()
            prev_snap = json.loads(prev["snapshot"]) if prev and prev["snapshot"] else None
            diff = compare_runs(prev_snap, snapshot)
            diff["previous_due_at"] = prev["due_at"] if prev else None
            c.execute("UPDATE monitor_runs SET status='ready',snapshot=?,diff=?,ready_at=?,updated_at=? WHERE id=? AND status='collecting'",
                      (dump(snapshot), dump(diff), now_iso(), now_iso(), r["id"]))
            ms.event(c, r["project_id"], r["id"], "system", "run_ready", f"highlight {diff['highlight_count']}")
        ready.append(r["id"])
    return ready


# ── 5. 발송 ───────────────────────────────────────────────────────────────
def mail_content(ms: MonitorStore, run_id: str) -> dict:
    with ms.connect() as c:
        r = c.execute("SELECT * FROM monitor_runs WHERE id=?", (run_id,)).fetchone()
        if not r or not r["snapshot"]:
            raise KeyError(run_id)
        p = ms._project(c, r["project_id"]) if c.execute("SELECT 1 FROM monitor_projects WHERE id=? AND status<>'deleted'", (r["project_id"],)).fetchone() \
            else dict(c.execute("SELECT * FROM monitor_projects WHERE id=?", (r["project_id"],)).fetchone())
        prev = c.execute("SELECT snapshot FROM monitor_runs WHERE project_id=? AND status='sent' AND due_at<? ORDER BY due_at DESC LIMIT 1",
                         (r["project_id"], r["due_at"])).fetchone()
    run = ms._run_dict(r)
    snapshot, diff = run["snapshot"], run["diff"]
    prev_snap = json.loads(prev["snapshot"]) if prev and prev["snapshot"] else None
    url = run_url(r["project_id"], run_id)
    body_html, body_text = report.build_html(p, run, snapshot, diff, url)
    xlsx = report.build_xlsx(p, run, snapshot, diff, prev_snap)
    due = report._kst(run["due_at"])[:10].replace("-", "")
    return {"run_id": run_id, "subject": report.subject(p["name"], run, diff), "recipients": run["recipients"],
            "html": body_html, "text": body_text, "attachment_name": f"monitoring_{due}_{run_id[:8]}.xlsx",
            "attachment": xlsx, "dashboard_url": url}


def _server_gmail():
    """서버 Gmail 토큰이 실제로 갱신되면 서비스 객체, 아니면 None(→ 워커 발송)."""
    if os.environ.get("MONITOR_MAIL_VIA", "auto") == "worker":
        return None
    try:
        from agents.notify import gmail_delivery as g
        path = g.materialize_token_from_env() or g.resolve_token_path()
        if not path or not g.gmail_configured(path):
            return None
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        creds = Credentials.from_authorized_user_file(str(path), scopes=[g.REQUIRED_SEND_SCOPE])
        creds.refresh(Request())
        from googleapiclient.discovery import build
        return build("gmail", "v1", credentials=creds, cache_discovery=False)
    except Exception as e:
        logger.info("monitor mail: server Gmail unavailable (%s) — worker will send", type(e).__name__)
        return None


def send_ready(ms: MonitorStore, *, gmail_factory=_server_gmail) -> list[str]:
    sent = []
    with ms.connect() as c:
        runs = [dict(r) for r in c.execute("SELECT id,project_id FROM monitor_runs WHERE status='ready' AND (mail_via IS NULL OR mail_via='server') AND mail_status='pending'")]
    if not runs:
        return sent
    service = gmail_factory()
    for r in runs:
        if service is None:
            with ms.connect() as c:
                c.execute("UPDATE monitor_runs SET mail_via='worker',updated_at=? WHERE id=?", (now_iso(), r["id"]))
                ms.event(c, r["project_id"], r["id"], "system", "mail_via_worker", "server Gmail token unavailable")
            continue
        content = mail_content(ms, r["id"])
        import base64
        msg = EmailMessage()
        msg["To"] = ", ".join(content["recipients"])
        msg["Subject"] = content["subject"]
        msg.set_content(content["text"])
        msg.add_alternative(content["html"], subtype="html")
        msg.add_attachment(content["attachment"], maintype="application",
                           subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename=content["attachment_name"])
        with ms.connect() as c:
            c.execute("UPDATE monitor_runs SET mail_via='server',mail_status='sending',status='mailing',updated_at=? WHERE id=? AND mail_status='pending'", (now_iso(), r["id"]))
        try:
            res = service.users().messages().send(userId="me", body={"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}).execute()
            receipt = "gmail:" + str(res.get("id"))
            with ms.connect() as c:
                c.execute("UPDATE monitor_runs SET status='sent',mail_status='sent',mail_receipt=?,sent_at=?,updated_at=? WHERE id=?",
                          (receipt, now_iso(), now_iso(), r["id"]))
                ms.event(c, r["project_id"], r["id"], "system", "mail_sent", receipt)
            sent.append(r["id"])
        except Exception as e:
            with ms.connect() as c:   # 보냈는지 모호 — 다시 보내지 않고 사람 확인
                c.execute("UPDATE monitor_runs SET mail_status='uncertain',status='failed',error=?,updated_at=? WHERE id=?",
                          (f"server send error {type(e).__name__}", now_iso(), r["id"]))
                ms.event(c, r["project_id"], r["id"], "system", "mail_uncertain", type(e).__name__)
    return sent


# ── 6. 텔레그램 ───────────────────────────────────────────────────────────
def _progress(ps: PriceRequestStore, run: dict) -> tuple[int, int]:
    done = total = 0
    for rids in (json.loads(run["requests"] or "{}")).values():
        for rid in rids[:1]:
            try:
                d = ps.detail(rid)
            except KeyError:
                continue
            total += len(d["country_results"])
            done += sum(1 for x in d["country_results"] if x["status"] != "pending")
    return done, total


def notify_telegram(ms: MonitorStore, ps: PriceRequestStore, at: datetime, send=telegram.send) -> list[str]:
    out = []
    at_iso = at.astimezone(timezone.utc).isoformat(timespec="seconds")
    with ms.connect() as c:
        runs = [dict(r) for r in c.execute(
            "SELECT r.*, p.name AS pname, p.countries AS pcountries FROM monitor_runs r JOIN monitor_projects p ON p.id=r.project_id "
            "WHERE r.status IN ('scheduled','collecting','ready','mailing','sent','failed') AND (r.tg_done_at IS NULL OR r.status<>'sent')")]
    worker = ps.worker_status(at)
    for r in runs:
        due = _dt(r["due_at"])
        name, url = r["pname"], run_url(r["project_id"], r["id"])
        n_items = len(json.loads(r["items"])) if r["items"] else None
        msg, col = None, None
        if r["status"] == "sent" and not r["tg_done_at"]:
            diff = json.loads(r["diff"] or "{}")
            t = diff.get("totals") or {}
            recips = json.loads(r["recipients"] or "[]")
            body = (f"✅ 해외약가 모니터링 완료 — {name}\n회차 {report._kst(r['due_at'])} KST · 발송 {report._kst(r['sent_at'])}\n"
                    f"약제 {n_items}개 · 수신 {len(recips)}명\n")
            if diff.get("has_previous"):
                body += (f"가격 변동 {t.get('price', 0)} · 기준 변경 {t.get('basis', 0)} · 포장 {t.get('packs', 0)} · "
                         f"새로 확인 {t.get('now_priced', 0)} · 미확인 전환 {t.get('lost', 0)} · 환율만 {t.get('fx', 0)}\n")
                for row in report.change_rows(diff)[:8]:
                    body += f"· {row['drug']} {row['cc']} {report.KIND_KO[row['kind']]} {report._pct(row['pct']) if row['kind'] == 'price' else ''}\n"
            else:
                body += "첫 회차 — 기준 가격 저장(다음 회차부터 비교)\n"
            unp = len(report.unpriced_rows(json.loads(r["snapshot"] or "{}")))
            if unp:
                body += f"가격 미확인 {unp}건(판정·근거는 메일/엑셀)\n"
            msg, col = body + url, "tg_done_at"
        elif r["status"] in ("scheduled", "collecting", "ready", "mailing") and not r["tg_eve_at"] and at >= due - timedelta(hours=24) and at < due:
            done, total = _progress(ps, r)
            state = {"scheduled": "수집 시작 전", "collecting": f"수집 중 {done}/{total} 국가", "ready": "수집 완료 · 발송 대기",
                     "mailing": "발송 중"}[r["status"]]
            warn = "" if worker["state"] == "active" else "\n⚠️ 수집 워커(Aside)가 최근 응답하지 않습니다. 노트북·Aside 실행 상태를 확인해 주세요."
            msg = (f"⏰ 내일 해외약가 모니터링 — {name}\n발송 예정 {report._kst(r['due_at'])} KST · 약제 {n_items or '-'}개\n"
                   f"현재: {state}{warn}\n수집이 끝나지 않으면 끝날 때까지 발송을 미룹니다.\n{url}")
            col = "tg_eve_at"
        elif r["status"] in ("scheduled", "collecting", "ready", "mailing", "failed") and at > due and \
                (not r["tg_delay_at"] or at - _dt(r["tg_delay_at"]) >= timedelta(hours=DELAY_ALERT_HOURS)):
            done, total = _progress(ps, r)
            why = {"collecting": f"수집 {done}/{total} 국가 완료", "scheduled": "수집 시작 전",
                   "ready": "발송 대기(워커 Gmail 발송)", "mailing": "발송 중",
                   "failed": f"발송 실패·확인 필요 ({r.get('error') or ''})"}[r["status"]]
            warn = "" if worker["state"] == "active" else "\n⚠️ 수집 워커(Aside)가 응답하지 않습니다."
            msg = f"⌛ 모니터링 발송 지연 — {name}\n예정 {report._kst(r['due_at'])} KST · 현재 {why}{warn}\n{url}"
            col = "tg_delay_at"
        if not msg:
            continue
        res = send(msg)
        if res.get("ok") or res.get("reason") == "not_configured":
            with ms.connect() as c:
                c.execute(f"UPDATE monitor_runs SET {col}=? WHERE id=?", (at_iso, r["id"]))
                ms.event(c, r["project_id"], r["id"], "system", "telegram_" + col[3:-3], "sent" if res.get("ok") else "not_configured")
            out.append(f"{r['id']}:{col}")
    return out


def tick(db_path, at: datetime | None = None, *, gmail_factory=_server_gmail, tg_send=telegram.send) -> dict:
    at = at or datetime.now(timezone.utc)
    ms, ps = MonitorStore(db_path), PriceRequestStore(db_path)
    res = {"scheduled": schedule_runs(ms, at), "started": start_runs(ms, ps, at)}
    res["ready"] = advance_collecting(ms, ps)
    res["sent"] = send_ready(ms, gmail_factory=gmail_factory)
    res["telegram"] = notify_telegram(ms, ps, at, send=tg_send)
    return res
