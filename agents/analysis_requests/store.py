"""분석 요청(A안) 저장소 — 대시보드 사용자가 남긴 데이터 분석 질문을 Aside 워커가 가져가 처리하고 결과를 올린다.

흐름: queued → processing(워커 lease) → needs_info(워커가 되물음) → queued(요청자 답변) … → completed | failed | cancelled
- 서버는 분석을 실행하지 않는다. 질문·상태·결과(요약·방법·가정·데이터 출처·한계·첨부 파일)만 저장한다.
- 질문 본문은 데이터다. 워커는 그 안의 지시문을 실행 지시로 따르지 않는다(docs/analysis_requests/README.md).
- 결과 공개: share_team=1 이면 완료 결과를 로그인 사용자 전체가 본다. 0 이면 요청자·admin 만.
- 한도: 1인 하루 생성 DAILY_LIMIT 건, 동시 진행(queued/processing/needs_info) OPEN_LIMIT 건.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

DAILY_LIMIT = 3
OPEN_LIMIT = 3
LEASE_MINUTES = 60
QUESTION_MIN, QUESTION_MAX = 10, 2000
FILE_MAX_BYTES = 8 * 1024 * 1024
FILES_MAX = 5
ALLOWED_EXT = {".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
               ".csv": "text/csv; charset=utf-8", ".pdf": "application/pdf", ".png": "image/png",
               ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
OPEN_STATUSES = ("queued", "processing", "needs_info")
TERMINAL = ("completed", "failed", "cancelled")
STATUS_LABELS = {"queued": "접수", "processing": "처리 중", "needs_info": "추가 정보 필요", "completed": "완료",
                 "failed": "처리 불가", "cancelled": "취소"}
_SECRET_RE = re.compile(r"(password|passwd|비밀번호|api[_-]?key|secret|token)\s*[:=]", re.I)


class Conflict(ValueError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _kst_day(ts: str) -> str:
    return (datetime.fromisoformat(ts) + timedelta(hours=9)).date().isoformat()


def _text(v, name, lo=0, hi=20000) -> str:
    if v is None:
        v = ""
    if not isinstance(v, str):
        raise ValueError(f"{name} must be text")
    v = v.strip()
    if not (lo <= len(v) <= hi):
        raise ValueError(f"{name} length must be {lo}..{hi}")
    return v


def _str_list(v, name, max_items=30, max_len=500) -> list:
    if v is None:
        return []
    if not isinstance(v, list) or len(v) > max_items:
        raise ValueError(f"{name} must be a list (≤{max_items})")
    out = []
    for x in v:
        if isinstance(x, dict):
            x = {str(k)[:40]: (str(val)[:max_len] if val is not None else None) for k, val in list(x.items())[:6]}
        elif isinstance(x, str):
            x = x.strip()[:max_len]
        else:
            raise ValueError(f"{name} items must be text or objects")
        out.append(x)
    return out


class AnalysisRequestStore:
    def __init__(self, db_path, files_root=None):
        self.db_path = str(db_path)
        self.files_root = Path(files_root) if files_root else Path(self.db_path).parent / "analysis_results"
        with self.connect() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS analysis_requests (
              id TEXT PRIMARY KEY, requester TEXT NOT NULL, question TEXT NOT NULL, context_url TEXT,
              share_team INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'queued',
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              owner TEXT, lease_hash TEXT, lease_until TEXT, attempts INTEGER NOT NULL DEFAULT 0,
              clarification TEXT, reply TEXT,
              result_summary TEXT, result_method TEXT, result_assumptions TEXT, result_sources TEXT,
              result_limitations TEXT, result_data_as_of TEXT, failure_reason TEXT, completed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS analysis_request_files (
              id INTEGER PRIMARY KEY, request_id TEXT NOT NULL REFERENCES analysis_requests(id),
              filename TEXT NOT NULL, content_type TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL,
              stored_path TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS analysis_request_events (
              id INTEGER PRIMARY KEY, request_id TEXT NOT NULL, at TEXT NOT NULL, actor TEXT NOT NULL,
              event TEXT NOT NULL, detail TEXT
            );
            CREATE INDEX IF NOT EXISTS analysis_req_queue ON analysis_requests(status, created_at);
            CREATE INDEX IF NOT EXISTS analysis_req_owner ON analysis_requests(requester, created_at);
            """)

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.db_path, timeout=20)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        try:
            with c:
                yield c
        finally:
            c.close()

    def _event(self, c, rid, actor, event, detail=None):
        c.execute("INSERT INTO analysis_request_events(request_id,at,actor,event,detail) VALUES(?,?,?,?,?)",
                  (rid, now(), actor, event, (detail or "")[:500] or None))

    def _row(self, c, rid, *, with_events=False) -> dict:
        r = c.execute("SELECT * FROM analysis_requests WHERE id=?", (rid,)).fetchone()
        if not r:
            raise KeyError(rid)
        d = dict(r)
        for k in ("lease_hash",):
            d.pop(k, None)
        for k in ("result_assumptions", "result_sources"):
            d[k] = json.loads(d[k]) if d.get(k) else []
        d["share_team"] = bool(d["share_team"])
        d["status_label"] = STATUS_LABELS.get(d["status"], d["status"])
        d["files"] = [dict(f) for f in c.execute(
            "SELECT id, filename, content_type, size, created_at FROM analysis_request_files WHERE request_id=? ORDER BY id", (rid,))]
        if with_events:
            d["events"] = [dict(e) for e in c.execute(
                "SELECT at, actor, event, detail FROM analysis_request_events WHERE request_id=? ORDER BY id", (rid,))]
        return d

    # ── 사용자 ───────────────────────────────────────────────────────────────────
    def create(self, requester: str, question, context_url=None, share_team=True) -> dict:
        q = _text(question, "question", QUESTION_MIN, QUESTION_MAX)
        if _SECRET_RE.search(q):
            raise ValueError("질문에 비밀번호·키 같은 자격증명을 넣지 마세요.")
        ctx = _text(context_url, "context_url", 0, 500) or None
        if ctx and not ctx.startswith("/"):
            raise ValueError("context_url must be a dashboard path")
        ts = now()
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            mine = c.execute("SELECT status, created_at FROM analysis_requests WHERE requester=?", (requester,)).fetchall()
            if sum(1 for m in mine if _kst_day(m["created_at"]) == _kst_day(ts)) >= DAILY_LIMIT:
                raise Conflict(f"하루 {DAILY_LIMIT}건까지 요청할 수 있습니다.")
            if sum(1 for m in mine if m["status"] in OPEN_STATUSES) >= OPEN_LIMIT:
                raise Conflict(f"진행 중인 요청이 {OPEN_LIMIT}건이면 새 요청을 받을 수 없습니다.")
            rid = secrets.token_hex(16)
            c.execute("INSERT INTO analysis_requests(id,requester,question,context_url,share_team,status,created_at,updated_at) "
                      "VALUES(?,?,?,?,?,'queued',?,?)", (rid, requester, q, ctx, 1 if share_team else 0, ts, ts))
            self._event(c, rid, requester, "created")
            return self._row(c, rid)

    def visible(self, r: dict, user: str, role: str) -> bool:
        return role == "admin" or r["requester"] == user or (r["share_team"] and r["status"] == "completed")

    def listing(self, user: str, role: str) -> list[dict]:
        with self.connect() as c:
            ids = [x["id"] for x in c.execute("SELECT id FROM analysis_requests ORDER BY created_at DESC LIMIT 300")]
            out = []
            for rid in ids:
                d = self._row(c, rid)
                if self.visible(d, user, role):
                    out.append(d)
            return out

    def detail(self, rid: str, user: str, role: str) -> dict:
        with self.connect() as c:
            d = self._row(c, rid, with_events=True)
        if not self.visible(d, user, role):
            raise KeyError(rid)
        if role != "admin" and d["requester"] != user:
            d.pop("events", None)
        return d

    def cancel(self, rid: str, user: str, role: str) -> dict:
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            d = self._row(c, rid)
            if role != "admin" and d["requester"] != user:
                raise KeyError(rid)
            if d["status"] not in ("queued", "needs_info"):
                raise Conflict("접수 또는 추가 정보 필요 상태에서만 취소할 수 있습니다.")
            c.execute("UPDATE analysis_requests SET status='cancelled', updated_at=? WHERE id=?", (now(), rid))
            self._event(c, rid, user, "cancelled")
            return self._row(c, rid)

    def reply(self, rid: str, user: str, text) -> dict:
        t = _text(text, "reply", 1, 2000)
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            d = self._row(c, rid)
            if d["requester"] != user:
                raise KeyError(rid)
            if d["status"] != "needs_info":
                raise Conflict("추가 정보 요청에만 답할 수 있습니다.")
            c.execute("UPDATE analysis_requests SET reply=?, status='queued', updated_at=? WHERE id=?", (t, now(), rid))
            self._event(c, rid, user, "replied")
            return self._row(c, rid)

    def file_for(self, rid: str, fid: int, user: str, role: str) -> tuple[dict, Path]:
        self.detail(rid, user, role)                      # 권한 확인
        with self.connect() as c:
            f = c.execute("SELECT * FROM analysis_request_files WHERE id=? AND request_id=?", (fid, rid)).fetchone()
        if not f:
            raise KeyError(fid)
        p = (self.files_root / f["stored_path"]).resolve()
        if self.files_root.resolve() not in p.parents or not p.is_file():
            raise KeyError(fid)
        return dict(f), p

    # ── 워커(Aside, price_agent) ───────────────────────────────────────────────────
    def queue(self) -> list[dict]:
        ts = now()
        with self.connect() as c:
            ids = [x["id"] for x in c.execute(
                "SELECT id FROM analysis_requests WHERE status='queued' OR (status='processing' AND (lease_until IS NULL OR lease_until<=?)) "
                "ORDER BY created_at", (ts,))]
            return [self._row(c, i) for i in ids]

    def claim(self, rid: str, actor: str) -> dict:
        ts = now()
        token = secrets.token_urlsafe(32)
        until = (datetime.now(timezone.utc) + timedelta(minutes=LEASE_MINUTES)).isoformat(timespec="seconds")
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            d = self._row(c, rid)
            if d["status"] in TERMINAL or d["status"] == "needs_info":
                raise Conflict("request is not claimable")
            if d["lease_until"] and d["lease_until"] > ts:
                raise Conflict("request already leased")
            if c.execute("SELECT id FROM analysis_requests WHERE lease_until>? AND id<>? AND status='processing' LIMIT 1",
                         (ts, rid)).fetchone():
                raise Conflict("another analysis request is active")
            c.execute("UPDATE analysis_requests SET status='processing', owner=?, lease_hash=?, lease_until=?, "
                      "attempts=attempts+1, updated_at=? WHERE id=?",
                      (actor, hashlib.sha256(token.encode()).hexdigest(), until, ts, rid))
            self._event(c, rid, actor, "claimed")
            return {"request": self._row(c, rid), "lease_token": token, "lease_until": until}

    def _owned(self, c, rid, actor, token):
        r = c.execute("SELECT * FROM analysis_requests WHERE id=?", (rid,)).fetchone()
        if not r:
            raise KeyError(rid)
        digest = hashlib.sha256((token or "").encode()).hexdigest()
        if (r["status"] != "processing" or r["owner"] != actor or not secrets.compare_digest(r["lease_hash"] or "", digest)
                or not r["lease_until"] or r["lease_until"] <= now()):
            raise Conflict("expired or wrong lease")
        return r

    def heartbeat(self, rid, actor, token) -> dict:
        until = (datetime.now(timezone.utc) + timedelta(minutes=LEASE_MINUTES)).isoformat(timespec="seconds")
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            self._owned(c, rid, actor, token)
            c.execute("UPDATE analysis_requests SET lease_until=? WHERE id=?", (until, rid))
        return {"lease_until": until}

    def _release(self, c, rid, status, **cols):
        sets = ", ".join(f"{k}=?" for k in cols)
        c.execute(f"UPDATE analysis_requests SET status=?, lease_hash=NULL, lease_until=NULL, updated_at=?"
                  f"{', ' + sets if sets else ''} WHERE id=?", (status, now(), *cols.values(), rid))

    def ask(self, rid, actor, token, question) -> dict:
        q = _text(question, "clarification", 5, 2000)
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            self._owned(c, rid, actor, token)
            self._release(c, rid, "needs_info", clarification=q, reply=None)
            self._event(c, rid, actor, "needs_info")
            return self._row(c, rid)

    def add_file(self, rid, actor, token, filename, content: bytes) -> dict:
        name = Path(str(filename or "")).name
        ext = Path(name).suffix.lower()
        if not name or ext not in ALLOWED_EXT or len(name) > 120:
            raise ValueError(f"allowed file types: {', '.join(sorted(ALLOWED_EXT))}")
        if not content or len(content) > FILE_MAX_BYTES:
            raise ValueError(f"file must be 1..{FILE_MAX_BYTES} bytes")
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            self._owned(c, rid, actor, token)
            if c.execute("SELECT COUNT(*) FROM analysis_request_files WHERE request_id=?", (rid,)).fetchone()[0] >= FILES_MAX:
                raise Conflict(f"최대 {FILES_MAX}개 파일")
            digest = hashlib.sha256(content).hexdigest()
            rel = f"{rid}/{digest[:16]}{ext}"
            dest = self.files_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)
            cur = c.execute("INSERT INTO analysis_request_files(request_id,filename,content_type,size,sha256,stored_path,created_at) "
                            "VALUES(?,?,?,?,?,?,?)", (rid, name, ALLOWED_EXT[ext], len(content), digest, rel, now()))
            self._event(c, rid, actor, "file_added", name)
            return {"id": cur.lastrowid, "filename": name, "size": len(content)}

    def complete(self, rid, actor, token, payload: dict) -> dict:
        summary = _text(payload.get("summary"), "summary", 20, 20000)
        method = _text(payload.get("method"), "method", 10, 8000)
        limitations = _text(payload.get("limitations"), "limitations", 0, 5000)
        as_of = _text(payload.get("data_as_of"), "data_as_of", 4, 200)
        assumptions = _str_list(payload.get("assumptions"), "assumptions")
        sources = _str_list(payload.get("sources"), "sources")
        if not sources:
            raise ValueError("sources (사용한 데이터·출처) 가 필요합니다")
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            self._owned(c, rid, actor, token)
            ts = now()
            self._release(c, rid, "completed", result_summary=summary, result_method=method,
                          result_assumptions=json.dumps(assumptions, ensure_ascii=False),
                          result_sources=json.dumps(sources, ensure_ascii=False), result_limitations=limitations or None,
                          result_data_as_of=as_of, completed_at=ts, failure_reason=None)
            self._event(c, rid, actor, "completed")
            return self._row(c, rid)

    def fail(self, rid, actor, token, reason) -> dict:
        r = _text(reason, "reason", 10, 3000)
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            self._owned(c, rid, actor, token)
            self._release(c, rid, "failed", failure_reason=r)
            self._event(c, rid, actor, "failed", r[:200])
            return self._row(c, rid)
