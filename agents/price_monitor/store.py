"""모니터링 저장소 — 프로젝트 · 대상 약제 · 회차. 같은 DB(drug_prices.db)에 테이블을 추가한다(기존 테이블은 건드리지 않음).

회차 상태:
  scheduled  → (start_at 도달) collecting → (모든 약제·국가 완료) ready → mailing → sent
  failed(발송 실패 등 사람 확인 필요) · cancelled(프로젝트 일시정지·삭제·일정 변경)
메일 상태: pending → sending → sent | uncertain (워커 발송 시 Gmail 영수증 필수)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from agents.price_monitor import schedule as sch

RUN_OPEN = ("scheduled", "collecting", "ready", "mailing")
EMAIL_RE = re.compile(r"[^\s@,;<>]+@([^\s@,;<>]+\.[^\s@,;<>]+)")


class Conflict(Exception):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dump(v) -> str:
    return json.dumps(v, ensure_ascii=False, sort_keys=True)


def allowed_domains() -> list[str]:
    return [d.strip().lower() for d in os.environ.get("MONITOR_MAIL_DOMAINS", "msd.com").split(",") if d.strip()]


def validate_recipients(raw) -> list[str]:
    if isinstance(raw, str):
        raw = re.split(r"[\s,;]+", raw)
    if not isinstance(raw, list):
        raise ValueError("recipients must be a list")
    out, doms = [], allowed_domains()
    for x in raw:
        x = str(x or "").strip().lower()
        if not x:
            continue
        m = EMAIL_RE.fullmatch(x)
        if not m or len(x) > 254:
            raise ValueError(f"invalid email: {x[:60]}")
        dom = m.group(1)
        if not any(dom == d or dom.endswith("." + d) for d in doms):
            raise ValueError(f"회사 도메인({', '.join('@' + d for d in doms)}) 주소만 받을 수 있습니다: {x[:60]}")
        if x not in out:
            out.append(x)
    if not out:
        raise ValueError("at least one recipient required")
    if len(out) > 50:
        raise ValueError("recipients must be <= 50")
    return out


def validate_countries(raw) -> list[str]:
    if not isinstance(raw, list) or not raw or any(c not in sch.A8 for c in raw):
        raise ValueError("countries must be a non-empty subset of A8")
    return [c for c in sch.A8 if c in set(raw)]


def validate_items(raw) -> list[dict]:
    """대상 약제 — 개수 제한 없음(사용자 결정). query 1..160자, 중복 제거."""
    if not isinstance(raw, list) or not raw:
        raise ValueError("at least one drug required")
    out, seen = [], set()
    for it in raw:
        if isinstance(it, str):
            it = {"query": it}
        if not isinstance(it, dict):
            raise ValueError("invalid item")
        q = str(it.get("query") or "").strip()
        if not 1 <= len(q) <= 160:
            raise ValueError("drug name must be 1..160 characters")
        ident = it.get("identity")
        if ident is not None and (not isinstance(ident, dict) or any(not isinstance(ident.get(k), str) or len(ident[k]) > 200 for k in ("slug", "brand", "inn"))):
            raise ValueError("invalid identity")
        key = q.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"query": q, "identity": ident, "label": str(it.get("label") or q)[:120]})
    return out


class MonitorStore:
    def __init__(self, db_path):
        self.db_path = str(db_path)
        with self.connect() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS monitor_projects (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, owner TEXT NOT NULL,
              recipients TEXT NOT NULL, countries TEXT NOT NULL, schedule TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS monitor_items (
              id INTEGER PRIMARY KEY, project_id TEXT NOT NULL REFERENCES monitor_projects(id),
              query TEXT NOT NULL, identity TEXT, label TEXT, added_at TEXT NOT NULL, removed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS monitor_runs (
              id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES monitor_projects(id),
              due_at TEXT NOT NULL, start_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'scheduled',
              items TEXT, countries TEXT, recipients TEXT, requests TEXT NOT NULL DEFAULT '{}',
              retries TEXT NOT NULL DEFAULT '{}', snapshot TEXT, diff TEXT,
              mail_status TEXT NOT NULL DEFAULT 'pending', mail_receipt TEXT, mail_via TEXT, sent_at TEXT,
              mail_owner TEXT, mail_lease_hash TEXT, mail_lease_until TEXT,
              tg_eve_at TEXT, tg_done_at TEXT, tg_delay_at TEXT, started_at TEXT, ready_at TEXT,
              error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS monitor_events (
              id INTEGER PRIMARY KEY, project_id TEXT NOT NULL, run_id TEXT, at TEXT NOT NULL,
              actor TEXT NOT NULL, event TEXT NOT NULL, detail TEXT
            );
            CREATE INDEX IF NOT EXISTS monitor_runs_open ON monitor_runs(status, start_at);
            CREATE INDEX IF NOT EXISTS monitor_runs_project ON monitor_runs(project_id, due_at);
            CREATE INDEX IF NOT EXISTS monitor_items_project ON monitor_items(project_id, removed_at);
            """)

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.db_path, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def event(self, c, pid, rid, actor, event, detail=None):
        c.execute("INSERT INTO monitor_events(project_id,run_id,at,actor,event,detail) VALUES(?,?,?,?,?,?)",
                  (pid, rid, now(), actor, event, detail if detail is None else str(detail)[:2000]))

    # ── 프로젝트 ───────────────────────────────────────────────────────────
    def _project(self, c, pid) -> dict:
        r = c.execute("SELECT * FROM monitor_projects WHERE id=? AND status<>'deleted'", (pid,)).fetchone()
        if not r:
            raise KeyError(pid)
        p = dict(r)
        for k in ("recipients", "countries", "schedule"):
            p[k] = json.loads(p[k])
        p["items"] = [dict(x, identity=json.loads(x["identity"]) if x["identity"] else None) for x in c.execute(
            "SELECT id,query,identity,label,added_at FROM monitor_items WHERE project_id=? AND removed_at IS NULL ORDER BY id", (pid,))]
        p["schedule_text"] = sch.describe(p["schedule"])
        p["lead_hours"] = round(sch.lead_hours(len(p["items"]), p["countries"]), 1)
        nr = c.execute("SELECT id,due_at,start_at,status FROM monitor_runs WHERE project_id=? AND status IN ('scheduled','collecting','ready','mailing') ORDER BY due_at LIMIT 1", (pid,)).fetchone()
        p["next_run"] = dict(nr) if nr else None
        lr = c.execute("SELECT id,due_at,status,sent_at FROM monitor_runs WHERE project_id=? AND status='sent' ORDER BY due_at DESC LIMIT 1", (pid,)).fetchone()
        p["last_sent_run"] = dict(lr) if lr else None
        return p

    def get_project(self, pid, actor=None, is_admin=True) -> dict:
        with self.connect() as c:
            p = self._project(c, pid)
        if not is_admin and p["owner"] != actor:
            raise KeyError(pid)
        return p

    def list_projects(self, actor=None, is_admin=True) -> list[dict]:
        with self.connect() as c:
            ids = [r[0] for r in c.execute(
                "SELECT id FROM monitor_projects WHERE status<>'deleted'" + ("" if is_admin else " AND owner=?") + " ORDER BY created_at DESC",
                () if is_admin else (actor,))]
            return [self._project(c, i) for i in ids]

    def create_project(self, owner, name, items, countries, schedule, recipients) -> dict:
        name = str(name or "").strip()
        if not 1 <= len(name) <= 80:
            raise ValueError("project name must be 1..80 characters")
        items, countries = validate_items(items), validate_countries(countries)
        schedule, recipients = sch.validate_schedule(schedule), validate_recipients(recipients)
        pid, ts = uuid.uuid4().hex, now()
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("INSERT INTO monitor_projects(id,name,owner,recipients,countries,schedule,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                      (pid, name, owner, dump(recipients), dump(countries), dump(schedule), ts, ts))
            for it in items:
                c.execute("INSERT INTO monitor_items(project_id,query,identity,label,added_at) VALUES(?,?,?,?,?)",
                          (pid, it["query"], dump(it["identity"]) if it["identity"] else None, it["label"], ts))
            self.event(c, pid, None, owner, "project_created", f"{len(items)} drugs · {','.join(countries)} · {sch.describe(schedule)}")
            return self._project(c, pid)

    def update_project(self, pid, actor, is_admin, patch: dict) -> dict:
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            p = self._project(c, pid)
            if not is_admin and p["owner"] != actor:
                raise KeyError(pid)
            sets, args, notes = [], [], []
            if "name" in patch:
                name = str(patch["name"] or "").strip()
                if not 1 <= len(name) <= 80:
                    raise ValueError("project name must be 1..80 characters")
                sets.append("name=?"); args.append(name)
            if "recipients" in patch:
                sets.append("recipients=?"); args.append(dump(validate_recipients(patch["recipients"]))); notes.append("recipients")
            schedule_changed = False
            if "countries" in patch:
                sets.append("countries=?"); args.append(dump(validate_countries(patch["countries"]))); notes.append("countries")
                schedule_changed = True
            if "schedule" in patch:
                sets.append("schedule=?"); args.append(dump(sch.validate_schedule(patch["schedule"]))); notes.append("schedule")
                schedule_changed = True
            if "status" in patch:
                if patch["status"] not in ("active", "paused"):
                    raise ValueError("status must be active or paused")
                sets.append("status=?"); args.append(patch["status"]); notes.append("status=" + patch["status"])
                schedule_changed = True
            if "items" in patch:
                items = validate_items(patch["items"])
                keep = {it["query"].lower() for it in items}
                cur = {x["query"].lower(): x for x in p["items"]}
                ts = now()
                for q, x in cur.items():
                    if q not in keep:
                        c.execute("UPDATE monitor_items SET removed_at=? WHERE id=?", (ts, x["id"]))
                for it in items:
                    if it["query"].lower() not in cur:
                        c.execute("INSERT INTO monitor_items(project_id,query,identity,label,added_at) VALUES(?,?,?,?,?)",
                                  (pid, it["query"], dump(it["identity"]) if it["identity"] else None, it["label"], ts))
                notes.append(f"items={len(items)}")
                schedule_changed = True
            if sets:
                sets.append("updated_at=?"); args.append(now())
                c.execute("UPDATE monitor_projects SET " + ",".join(sets) + " WHERE id=?", (*args, pid))
            if schedule_changed:
                # 아직 수집을 시작하지 않은 예정 회차는 새 설정으로 다시 잡는다(시작된 회차는 그대로 진행).
                n = c.execute("UPDATE monitor_runs SET status='cancelled',updated_at=?,error='설정 변경으로 다시 예약' WHERE project_id=? AND status='scheduled'",
                              (now(), pid)).rowcount
                if n:
                    notes.append(f"rescheduled {n}")
            self.event(c, pid, None, actor, "project_updated", ", ".join(notes))
            return self._project(c, pid)

    def delete_project(self, pid, actor, is_admin) -> None:
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            p = self._project(c, pid)
            if not is_admin and p["owner"] != actor:
                raise KeyError(pid)
            c.execute("UPDATE monitor_projects SET status='deleted',updated_at=? WHERE id=?", (now(), pid))
            c.execute("UPDATE monitor_runs SET status='cancelled',updated_at=?,error='프로젝트 삭제' WHERE project_id=? AND status='scheduled'", (now(), pid))
            self.event(c, pid, None, actor, "project_deleted")

    # ── 회차 ──────────────────────────────────────────────────────────────
    @staticmethod
    def _run_dict(r) -> dict:
        d = dict(r)
        for k in ("items", "countries", "recipients", "requests", "retries", "snapshot", "diff"):
            d[k] = json.loads(d[k]) if d.get(k) else None
        for k in ("mail_lease_hash", "mail_owner", "mail_lease_until"):
            d.pop(k, None)
        return d

    def get_run(self, run_id, actor=None, is_admin=True, with_snapshot=True) -> dict:
        with self.connect() as c:
            r = c.execute("SELECT * FROM monitor_runs WHERE id=?", (run_id,)).fetchone()
            if not r:
                raise KeyError(run_id)
            p = c.execute("SELECT owner,name FROM monitor_projects WHERE id=?", (r["project_id"],)).fetchone()
        if not is_admin and (not p or p["owner"] != actor):
            raise KeyError(run_id)
        d = self._run_dict(r)
        d["project_name"] = p["name"] if p else None
        if not with_snapshot:
            d.pop("snapshot", None)
        return d

    def list_runs(self, pid, limit=30) -> list[dict]:
        with self.connect() as c:
            rows = c.execute("SELECT * FROM monitor_runs WHERE project_id=? AND status<>'cancelled' ORDER BY due_at DESC LIMIT ?",
                             (pid, max(1, min(int(limit), 200)))).fetchall()
        out = []
        for r in rows:
            d = self._run_dict(r)
            d.pop("snapshot", None)
            diff = d.pop("diff", None) or {}
            d["totals"] = diff.get("totals")
            d["highlight_count"] = diff.get("highlight_count")
            d["item_count"] = len(d.get("items") or [])
            out.append(d)
        return out

    # 워커 메일 발송 lease (가격 요청 lease 와 같은 방식, 30분)
    def mail_queue(self) -> list[dict]:
        ts = now()
        with self.connect() as c:
            rows = c.execute("SELECT * FROM monitor_runs WHERE status IN ('ready','mailing') AND mail_via='worker' "
                             "AND mail_status IN ('pending','uncertain') AND (mail_lease_until IS NULL OR mail_lease_until<?) ORDER BY due_at", (ts,)).fetchall()
        return [{k: v for k, v in self._run_dict(r).items() if k not in ("snapshot",)} for r in rows]

    def mail_claim(self, run_id, actor) -> dict:
        token = secrets.token_urlsafe(32)
        until = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(timespec="seconds")
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            r = c.execute("SELECT * FROM monitor_runs WHERE id=?", (run_id,)).fetchone()
            if not r:
                raise KeyError(run_id)
            if r["status"] not in ("ready", "mailing") or r["mail_via"] != "worker" or r["mail_status"] == "sent":
                raise Conflict("run is not waiting for worker mail")
            if r["mail_lease_until"] and r["mail_lease_until"] > now():
                raise Conflict("mail already leased")
            c.execute("UPDATE monitor_runs SET status='mailing',mail_owner=?,mail_lease_hash=?,mail_lease_until=?,updated_at=? WHERE id=?",
                      (actor, hashlib.sha256(token.encode()).hexdigest(), until, now(), run_id))
            self.event(c, r["project_id"], run_id, actor, "mail_claimed")
        return {"lease_token": token, "lease_until": until}

    def _mail_owned(self, c, run_id, actor, token):
        r = c.execute("SELECT * FROM monitor_runs WHERE id=?", (run_id,)).fetchone()
        if not r:
            raise KeyError(run_id)
        digest = hashlib.sha256((token or "").encode()).hexdigest()
        if r["mail_owner"] != actor or not secrets.compare_digest(r["mail_lease_hash"] or "", digest) or (r["mail_lease_until"] or "") <= now():
            raise Conflict("expired or wrong mail lease")
        return r

    def mail_check_lease(self, run_id, actor, token):
        with self.connect() as c:
            self._mail_owned(c, run_id, actor, token)

    def mail_notify(self, run_id, actor, token, phase, receipt=None) -> dict:
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            r = self._mail_owned(c, run_id, actor, token)
            if phase == "begin":
                if r["mail_status"] != "pending":
                    raise Conflict("mail already sent/in-flight/uncertain; reconcile in Gmail Sent first")
                c.execute("UPDATE monitor_runs SET mail_status='sending',updated_at=? WHERE id=?", (now(), run_id))
            elif phase == "sent":
                if r["mail_status"] not in ("sending", "uncertain") or not isinstance(receipt, str) \
                        or not re.fullmatch(r"gmail:(?:msg-[af]:[0-9]+|[0-9a-f]{16,64})", receipt):
                    raise Conflict("actual Gmail sent-message ID required")
                c.execute("UPDATE monitor_runs SET status='sent',mail_status='sent',mail_receipt=?,sent_at=?,mail_lease_until=NULL,mail_lease_hash=NULL,updated_at=? WHERE id=?",
                          (receipt, now(), now(), run_id))
            elif phase == "uncertain":
                if r["mail_status"] != "sending":
                    raise Conflict("mail not in sending state")
                c.execute("UPDATE monitor_runs SET mail_status='uncertain',updated_at=? WHERE id=?", (now(), run_id))
            else:
                raise ValueError("phase must be begin, sent or uncertain")
            self.event(c, r["project_id"], run_id, actor, "mail_" + phase, receipt)
        return self.get_run(run_id, with_snapshot=False)
