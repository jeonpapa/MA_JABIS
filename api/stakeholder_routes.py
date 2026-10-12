"""Stakeholder & Verbatim Intelligence API Blueprint (T6).

권한 3계층 (agents/rules/stakeholder_verbatim_rules.md §4):
- user  (`require_auth()` — user·admin): 인용문·요약·해석·등급·locator·URL·SHA-256·무결성 + **locator 창 발췌 ≤600자**.
         응답 어디에도 `photo_url`·`photo_path`·`local_path`·`text_path` 가 없다(마지막에 _scrub 로 한 번 더 거른다).
- admin (`require_auth(role="admin")`): 추출 전문(`/text`)·원본 다운로드(`/raw`)·인물 CRUD·사진·제안 승인/반려/충돌 확인·sync.
         reviewer/actor 는 항상 토큰 `sub`(이메일). 인물 삭제 API 는 없다(paused 로만).
- agent (`require_auth(role="agent")` — agent·admin): `/api/agent/stakeholder/{registry,proposals}` 만.

원칙: 대쉬보드는 DB(sv_*)와 볼륨 파일만 읽는다(요청 경로에 네트워크 없음 — 수동 sync 라우트만 예외적으로 잡과 같은 경로를
부른다). 쓰기는 모두 store/approval 의 트랜잭션 함수로. 볼륨 경로는 DB·manifest 에서 오므로 신뢰하지 않고 매번
paths.resolve_under 로 root 안에 있는지 확인한다(심볼릭 링크 탈출 포함). 오류 JSON 은 `{error, code[, details]}`.
"""
from __future__ import annotations

import importlib
import logging
import mimetypes
import re
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Iterator

from flask import Blueprint, Response, jsonify, request, send_file

from agents.stakeholder import approval, paths, photos, store
from api.auth import require_auth

logger = logging.getLogger(__name__)

EXCERPT_MAX_CHARS = 600
USER_HIDDEN_KEYS = frozenset({"photo_url", "photo_path", "local_path", "text_path"})
TOPIC_ORDER = ("price_reform", "access", "rwe", "finance", "negotiation")   # canonical_parse.TOPIC_SLUGS 순서
# 기관 필터 — speaker_role_at_event / person.organization 에 매칭. '공단' 단독은 앞이 한글이 아닐 때만(근로복지공단 등 배제).
ORG_PATTERNS: dict[str, re.Pattern] = {
    "보건복지부": re.compile(r"보건복지부|복지부|MOHW", re.I),
    "심평원": re.compile(r"건강보험심사평가원|심사평가원|심평원|HIRA", re.I),
    "공단": re.compile(r"국민건강보험공단|건강보험공단|건보공단|NHIS|(?<![가-힣])공단", re.I),
}
_LOCATOR_RE = re.compile(r"(?P<path>[^:\s]+)(?::(?P<a>\d+)(?:-(?P<b>\d+))?)?")
_SPEAKER_SPLIT = re.compile(r"[·,，/]")
PHOTO_UPLOAD_SLACK = 64 * 1024        # multipart 경계·헤더 여유
SVG_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; sandbox"


class BadRequest(store.StoreError):
    status = 400


# ── 순수 헬퍼 ────────────────────────────────────────────────────────────────────

def grade_tokens(grade: str | None) -> list[str]:
    return [t.strip() for t in re.split(r"[/+]", grade or "") if t.strip()]


def grade_class_of_token(token: str) -> str:
    if token == "A" or token.startswith("A-"):
        return "a"
    if token == "B" or token.startswith("B-"):
        return "b"
    if token == "C" or token.startswith("C-"):     # 'Candidate-only' 는 C 가 아니다
        return "c"
    return "role"


def grade_class(grade: str | None) -> str:
    """'a'|'b'|'c'|'role' — 복합 등급(`C-quote/C-summary`)은 첫 토큰."""
    toks = grade_tokens(grade)
    return grade_class_of_token(toks[0]) if toks else "role"


def grade_matches(grade: str | None, wanted: str) -> bool:
    """wanted 가 어휘 토큰(`B-summary`)이면 토큰 일치, 등급군(`A`·`B`·`C`·`role`)이면 어느 토큰이든 그 군."""
    toks = grade_tokens(grade)
    if wanted in toks:
        return True
    w = wanted.strip().lower()
    if w in ("a", "b", "c", "role"):
        return any(grade_class_of_token(t) == w for t in toks)
    return False


def matches_org(org: str, role_text: str | None, person_org: str | None) -> bool:
    pat = ORG_PATTERNS[org]
    return any(pat.search(s) for s in (role_text, person_org) if s)


def split_speakers(name: str | None) -> list[str]:
    return [s.strip() for s in _SPEAKER_SPLIT.split(name or "") if s.strip()]


def _scrub(obj: Any) -> Any:
    """user 응답 최종 거름망 — 숨김 키를 어느 깊이에서든 제거(런 payload 의 scope 스냅숏 등)."""
    if isinstance(obj, dict):
        return {k: _scrub(v) for k, v in obj.items() if k not in USER_HIDDEN_KEYS}
    if isinstance(obj, list):
        return [_scrub(v) for v in obj]
    return obj


def parse_locator(locator: str) -> tuple[str, int | None, int | None]:
    """`sources/<file>.txt[:a[-b]]` 하나만. 복수 범위·세미콜론·역순·0행은 BadRequest."""
    m = _LOCATOR_RE.fullmatch((locator or "").strip())
    if not m:
        raise BadRequest("locator 형식이 올바르지 않습니다 (sources/<file>.txt:<a>-<b>).", "BAD_LOCATOR")
    a = int(m.group("a")) if m.group("a") else None
    b = int(m.group("b")) if m.group("b") else a
    if a is not None and (a < 1 or b < a):
        raise BadRequest("locator 줄 범위가 올바르지 않습니다.", "BAD_LOCATOR")
    return m.group("path"), a, b


def _locator_str(loc: Any) -> str | None:
    if isinstance(loc, str):
        return loc or None
    if isinstance(loc, dict) and loc.get("path"):
        s, e = loc.get("start"), loc.get("end")
        if s is None:
            return loc["path"]
        return f"{loc['path']}:{s}-{e if e is not None else s}"
    return None


def _safe_file(root: Path, rel: str | None) -> Path | None:
    """root 아래 실존 파일. 안전하지 않은 경로는 BadRequest, 없는 파일은 None."""
    if not rel:
        return None
    try:
        p = paths.resolve_under(root, rel)
    except ValueError as exc:
        raise BadRequest("허용되지 않는 경로입니다.", "BAD_PATH") from exc
    return p if p.is_file() else None


def read_excerpt(root: Path, source: dict, locator: str) -> dict:
    """source 의 text_path 에 대한 locator 창 발췌(≤600자). locator 경로가 text_path 와 다르면 400."""
    path, a, b = parse_locator(locator)
    if not source.get("text_path") or path != source["text_path"]:
        raise BadRequest("locator 경로가 이 출처의 추출 텍스트와 다릅니다.", "LOCATOR_MISMATCH")
    f = _safe_file(root, path)
    if f is None:
        raise store.NotFoundError("추출 텍스트 파일이 볼륨에 없습니다.", "TEXT_MISSING")
    lines = f.read_bytes().decode("utf-8-sig", errors="replace").splitlines()
    start = a or 1
    end = min(b if b is not None else len(lines), len(lines))
    if start > len(lines):
        raise BadRequest("locator 줄 범위가 파일 길이를 벗어납니다.", "LOCATOR_RANGE")
    text = "\n".join(lines[start - 1:end])
    truncated = len(text) > EXCERPT_MAX_CHARS
    text = text[:EXCERPT_MAX_CHARS]
    return {"source_id": source["source_id"], "locator": locator, "text": text, "start": start, "end": end,
            "chars": len(text), "truncated": truncated}


# ── 요청 컨텍스트 (한 연결로 사람·출처·인용 색인) ─────────────────────────────────────

class _Index:
    def __init__(self, conn):
        self.conn = conn
        self.people = {p["person_id"]: p for p in store.list_people(conn=conn)}
        names: dict[str, list[str]] = {}
        for p in self.people.values():
            names.setdefault(p["name"], []).append(p["person_id"])
        self.person_by_name = {n: ids[0] for n, ids in names.items() if len(ids) == 1}
        self.sources = {s["source_id"]: s for s in store.list_sources(conn=conn)}
        self.source_by_text = {s["text_path"]: sid for sid, s in self.sources.items() if s.get("text_path")}
        self.quotes = store.list_quotes(conn=conn)
        self.quotes_by_stmt: dict[str, list[dict]] = {}
        for q in self.quotes:
            if not q.get("source_id"):
                q["source_id"] = self.source_by_text.get((q.get("locator") or "").split(":")[0])
            for sid in q.get("statement_ids") or []:
                self.quotes_by_stmt.setdefault(sid, []).append(q)

    def person_of(self, person_id: str | None, speaker_name: str | None) -> dict | None:
        pid = person_id or self.person_by_name.get(speaker_name or "")
        return self.people.get(pid) if pid else None

    def person_ref(self, person_id: str | None, speaker_name: str | None) -> dict | None:
        p = self.person_of(person_id, speaker_name)
        return {"person_id": p["person_id"], "name": p["name"], "photo": bool(p.get("photo_path"))} if p else None

    def source_ids_of(self, st: dict) -> list[str]:
        ids = [s for s in st.get("source_ids") or [] if s]
        for loc in st.get("locators") or []:
            sid = self.source_by_text.get(loc.get("path")) if isinstance(loc, dict) else None
            if sid:
                ids.append(sid)
        ids += [q["source_id"] for q in self.quotes_by_stmt.get(st["statement_id"], []) if q.get("source_id")]
        return list(dict.fromkeys(ids))

    def headline(self, st: dict) -> dict | None:
        qs = self.quotes_by_stmt.get(st["statement_id"], [])
        if not qs:
            return None
        q = next((x for x in qs if x["quote_id"] == st.get("headline_quote_id")), qs[0])
        return q

    def statement_row(self, st: dict) -> dict:
        sids = self.source_ids_of(st)
        src = self.sources.get(sids[0]) if sids else None
        q = self.headline(st)
        return {
            "statement_id": st["statement_id"],
            "event": {"event_id": st["event_id"], "event_date": st.get("event_date"), "title": st.get("event_title")},
            "topic": st.get("topic"),
            "speaker_name": st.get("speaker_name"),
            "speaker_role_at_event": st.get("speaker_role_at_event"),
            "person": self.person_ref(st.get("person_id"), st.get("speaker_name")),
            "evidence_grade": st.get("evidence_grade"),
            "grade_class": grade_class(st.get("evidence_grade")),
            "headline_quote": ({"quote_id": q["quote_id"], "quote_text": q["quote_text"],
                                "locator_match": q.get("locator_match")} if q else None),
            "summary_text": st.get("summary_text"),
            "quotes_count": len(self.quotes_by_stmt.get(st["statement_id"], [])),
            "source": ({"source_id": src["source_id"], "publisher_title": src.get("title"), "url": src.get("url"),
                        "publication_date": src.get("publication_date"),
                        "integrity_status": src.get("integrity_status")} if src else None),
        }

    def matches(self, st: dict, *, org: str | None, grade: str | None) -> bool:
        if grade and not grade_matches(st.get("evidence_grade"), grade):
            return False
        if org:
            p = self.person_of(st.get("person_id"), st.get("speaker_name"))
            if not matches_org(org, st.get("speaker_role_at_event"), p.get("organization") if p else None):
                return False
        return True


def _person_public(p: dict, *, admin: bool, statements_count: int | None = None) -> dict:
    d = {k: v for k, v in p.items() if k not in ("photo_path", "photo_url")}
    d["has_photo"] = bool(p.get("photo_path"))
    if statements_count is not None:
        d["statements_count"] = statements_count
    if admin:
        d["photo_url"] = p.get("photo_url")      # 언론 CDN 메타 — admin 에게 텍스트로만(렌더링 금지)
    return d


def _org_param() -> str | None:
    org = (request.args.get("org") or "").strip() or None
    if org and org not in ORG_PATTERNS:
        raise BadRequest(f"org 는 {', '.join(ORG_PATTERNS)} 중 하나여야 합니다.", "BAD_ORG")
    return org


def _int_param(name: str, default: int | None, lo: int = 1, hi: int = 1000) -> int | None:
    raw = request.args.get(name)
    if raw is None or raw == "":
        return default
    try:
        v = int(raw)
    except ValueError as exc:
        raise BadRequest(f"{name} 는 정수여야 합니다.", f"BAD_{name.upper()}") from exc
    return max(lo, min(hi, v))


def _kind(kind: str | None) -> str:
    if kind not in store.RUN_KINDS:
        raise BadRequest(f"kind 는 {', '.join(store.RUN_KINDS)} 중 하나여야 합니다.", "BAD_KIND")
    return kind


def _is_admin() -> bool:
    return (getattr(request, "user", None) or {}).get("role") == "admin"


def _actor() -> str:
    return str((getattr(request, "user", None) or {}).get("sub") or "")


def _json_body() -> dict:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise store.ValidationError("요청 본문은 JSON 객체여야 합니다.", "BAD_BODY")
    return body


def _error(status: int, message: str, code: str, details: Any = None):
    payload: dict[str, Any] = {"error": message, "code": code}
    if details is not None:
        payload["details"] = details
    return jsonify(payload), status


def _handled(fn: Callable) -> Callable:
    """StoreError(ApprovalError·PhotoError 포함) → {error, code[, details]} + 그 status. 그 외 예외는 500(로그)."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except store.StoreError as exc:
            return _error(int(getattr(exc, "status", 500) or 500), exc.message, exc.code,
                          getattr(exc, "details", None))
        except Exception:  # noqa: BLE001 — 라우트 경계
            logger.exception("stakeholder API 실패: %s %s", request.method, request.path)
            return _error(500, "내부 오류가 발생했습니다.", "INTERNAL")
    return wrapper


def _user_json(data: Any, status: int = 200):
    return jsonify(data if _is_admin() else _scrub(data)), status


# ── 수동 sync 실행기 해석 (T8 런북 우선 → T5 모듈 직접 → 없음) ─────────────────────────

def _resolve_sync_runner() -> Callable[[Path, Path], dict] | None:
    """반환 runner(db_path, root) -> {ok, summary, detail}. T5·T8 이 병렬 구현이라 둘 다 지연 import 로 찾는다."""
    try:
        from agents.agent_tasks import runbooks
        if "stakeholder_data_sync" in runbooks.RUNBOOKS:
            return lambda db_path, root: runbooks.run("stakeholder_data_sync")
    except Exception as exc:  # noqa: BLE001
        logger.warning("runbooks import 실패 — T5 sync 직접 호출로 대체: %s", exc)
    try:
        mod = importlib.import_module("agents.stakeholder.sync")
        fn = getattr(mod, "sync_now")
    except Exception:  # noqa: BLE001 — T5 미탑재
        return None

    def run_direct(db_path: Path, root: Path) -> dict:
        res = fn(root=root, db_path=db_path)
        errors = res.get("errors") or ([res["error"]] if res.get("error") else [])
        state = "완료" if res.get("ok") else "실패"
        tail = f"; 오류: {errors[0]}" if errors else ""
        return {"ok": bool(res.get("ok")), "summary": f"stakeholder sync {state} — {res.get('counts')}{tail}"[:300],
                "detail": res}
    return run_direct


def sync_failure_status(res: dict) -> tuple[int, str]:
    """실패한 sync 결과 → (HTTP status, code). 런북 결과면 detail(=잡 결과)도 본다.
    동시 실행(잠금) 409 · 설정(토큰 부재 등 config) 503 · 그 외 502."""
    detail = res.get("detail") if isinstance(res.get("detail"), dict) else {}
    texts = " ".join(str(x) for x in [res.get("summary"), res.get("error"), detail.get("error"),
                                      *(detail.get("errors") or [])] if x)
    if "already running" in texts or "lock held" in texts:
        return 409, "SYNC_RUNNING"
    if (res.get("error_class") or detail.get("error_class")) == "config":
        return 503, "SYNC_CONFIG"
    return 502, "SYNC_FAILED"


_TOPIC_HEADING_CACHE: dict[tuple[str, int, int], list[dict]] = {}


def topics_from_markdown(md: Path) -> list[dict]:
    """정본 MD 의 `# N. 주제 ①…` 헤딩만 읽어 [{slug,title}] (파서 전체를 돌리지 않는다 — 읽기 전용, mtime 캐시)."""
    from agents.stakeholder import canonical_parse as cp
    st = md.stat()
    key = (str(md), st.st_mtime_ns, st.st_size)
    if key not in _TOPIC_HEADING_CACHE:
        out = []
        for line in md.read_bytes().decode("utf-8-sig", errors="replace").splitlines():
            m = cp._RE_TOPIC.match(line)
            if m and cp.TOPIC_SLUGS.get(m.group(2)):
                out.append({"slug": cp.TOPIC_SLUGS[m.group(2)], "title": m.group(3)})
        _TOPIC_HEADING_CACHE.clear()
        _TOPIC_HEADING_CACHE[key] = out
    return list(_TOPIC_HEADING_CACHE[key])


def _topic_list(raw: Any) -> list[dict]:
    if not isinstance(raw, list):
        return []
    return [{"slug": t.get("slug"), "title": t.get("title")} for t in raw if isinstance(t, dict) and t.get("slug")]


# ── Blueprint ───────────────────────────────────────────────────────────────────

def build_stakeholder_blueprint(db_path: str | Path | None = None, *, root: str | Path | None = None) -> Blueprint:
    """db_path 기본 공용 drug_prices.db(sv_* 는 첫 요청에서 지연 생성), root 기본 paths.stakeholder_root()(요청마다 해석)."""
    bp = Blueprint("stakeholder", __name__)
    db = Path(db_path) if db_path else store.DEFAULT_DB_PATH

    def vroot() -> Path:
        return Path(root) if root else paths.stakeholder_root()

    @contextmanager
    def conn_ctx() -> Iterator:
        store.ensure_tables(db)
        c = store.connect(db)
        try:
            yield c
        finally:
            c.close()

    def statements_count_by_person(c) -> dict[str, int]:
        return {r[0]: r[1] for r in c.execute(
            "SELECT person_id, COUNT(*) FROM sv_statement WHERE person_id IS NOT NULL GROUP BY person_id")}

    # ── user 읽기 ──────────────────────────────────────────────────────────────

    @bp.get("/api/stakeholder/overview")
    @require_auth()
    @_handled
    def overview():
        today = store.today_kst().isoformat()
        with conn_ctx() as c:
            n = store.counts(conn=c)
            speakers = {s for (name,) in c.execute("SELECT speaker_name FROM sv_statement")
                        for s in split_speakers(name)}
            one = lambda sql, *a: c.execute(sql, a).fetchone()[0]  # noqa: E731
            kpi = {
                "events": n["sv_event"], "statements": n["sv_statement"], "quotes": n["sv_quote"],
                "speakers_observed": len(speakers),
                "people_active": one("SELECT COUNT(*) FROM sv_person WHERE status='active'"),
                "people_priority": one("SELECT COUNT(*) FROM sv_person WHERE status='active' AND importance_score>=5"),
                "people_candidates": one("SELECT COUNT(*) FROM sv_person WHERE status='active' "
                                         "AND verification_status='candidate'"),
                "pending_proposals": one("SELECT COUNT(*) FROM sv_audit_proposal WHERE status IN "
                                         "('needs_review','needs_evidence')"),
            }
            mon = store.list_runs("monitoring", limit=1, conn=c)
            aud = store.list_runs("audit", limit=1, conn=c)
            events = store.list_events(conn=c)
            integ = {r[0]: r[1] for r in c.execute(
                "SELECT integrity_status, COUNT(*) FROM sv_source GROUP BY integrity_status")}
            meta = lambda k: store.get_meta(k, conn=c)  # noqa: E731
            scope_start = meta("scope_start_kst") or meta("scope_start")
            scope_end = meta("scope_end_kst") or meta("scope_end")
            # 주제 제목: sv_meta(T5 canonical_stats_json.topics) → 볼륨 정본 MD 헤딩 → 발언의 slug(제목 없음)
            stats = store._jo(meta("canonical_stats_json"))
            topics = (_topic_list(store._jo(meta("canonical_topics_json")))
                      or _topic_list(stats.get("topics") if isinstance(stats, dict) else None))
            md_rel = meta("canonical_timeline_path")
            found = {r[0] for r in c.execute("SELECT DISTINCT topic FROM sv_statement") if r[0]}
            last_sync_at = meta("last_sync_at")
            copyright_notice = meta("copyright_notice")
        if not topics and md_rel:
            try:
                md = _safe_file(vroot(), md_rel)
                topics = topics_from_markdown(md) if md else []
            except (BadRequest, OSError):
                topics = []
        if not topics:
            ordered = [s for s in TOPIC_ORDER if s in found] + sorted(found - set(TOPIC_ORDER))
            topics = [{"slug": s, "title": None} for s in ordered]
        if events and not scope_start:
            scope_start = min(e["event_date"] for e in events)
            scope_end = scope_end or max(e["event_date"] for e in events)
        m = mon[0] if mon else None
        a = aud[0] if aud else None
        data = {
            "as_of": today,
            "scope": {"start": scope_start, "end": scope_end},
            "kpi": kpi,
            "latest_monitoring_run": ({k: m.get(k) for k in ("run_id", "run_date", "run_status", "saturation_status",
                                                             "active_people", "approved_domains", "new_quotes")}
                                      if m else None),
            "latest_audit_run": ({k: a.get(k) for k in ("audit_run_id", "run_date", "run_status", "claim",
                                                        "why_this_matters", "proposals_created")} if a else None),
            "recent_events": [{"event_id": e["event_id"], "event_date": e["event_date"], "title": e["title"],
                               "topics": e.get("topics") or [], "speakers": e.get("speakers") or []}
                              for e in events[:3]],
            "integrity": {"sources_verified": integ.get("verified", 0),
                          "sources_total": sum(v for k, v in integ.items() if k != "no_local"),
                          "mismatch": integ.get("mismatch", 0), "missing": integ.get("missing", 0),
                          "last_sync_at": last_sync_at},
            "copyright_notice": copyright_notice,
            "topics": topics,
        }
        return _user_json(data)

    @bp.get("/api/stakeholder/events")
    @require_auth()
    @_handled
    def events_list():
        topic = request.args.get("topic") or None
        org, grade = _org_param(), request.args.get("grade") or None
        with conn_ctx() as c:
            idx = _Index(c)
            events = store.list_events(date_from=request.args.get("from") or None,
                                       date_to=request.args.get("to") or None, conn=c)
            sts = store.list_statements(conn=c)
        by_event: dict[str, list[dict]] = {}
        for st in sts:
            if topic and st.get("topic") != topic:
                continue
            if idx.matches(st, org=org, grade=grade):
                by_event.setdefault(st["event_id"], []).append(st)
        items = []
        for e in events:
            mine = by_event.get(e["event_id"], [])
            if (topic or org or grade) and not mine:
                continue
            head_st = next((s for s in mine if idx.headline(s)), mine[0] if mine else None)
            headline = None
            if head_st:
                q = idx.headline(head_st)
                headline = {"speaker_name": head_st.get("speaker_name"),
                            "person": idx.person_ref(head_st.get("person_id"), head_st.get("speaker_name")),
                            "quote_text": q["quote_text"] if q else None,
                            "evidence_grade": head_st.get("evidence_grade")}
            items.append({**e, "statements_count": len(mine), "headline": headline})
        return _user_json({"items": items})

    @bp.get("/api/stakeholder/statements")
    @require_auth()
    @_handled
    def statements_list():
        org, grade = _org_param(), request.args.get("grade") or None
        limit = _int_param("limit", None, 1, 2000)
        with conn_ctx() as c:
            idx = _Index(c)
            sts = store.list_statements(topic=request.args.get("topic") or None,
                                        person_id=request.args.get("person_id") or None,
                                        q=request.args.get("q") or None,
                                        date_from=request.args.get("from") or None,
                                        date_to=request.args.get("to") or None, conn=c)
        rows = [idx.statement_row(st) for st in sts if idx.matches(st, org=org, grade=grade)]
        return _user_json({"items": rows[:limit] if limit else rows})

    @bp.get("/api/stakeholder/statements/<statement_id>")
    @require_auth()
    @_handled
    def statement_detail(statement_id: str):
        with conn_ctx() as c:
            idx = _Index(c)
            st = store.get_statement(statement_id, conn=c)
        if st is None:
            raise store.NotFoundError("발언을 찾을 수 없습니다.", "NOT_FOUND")
        for q in st.get("quotes") or []:
            if not q.get("source_id"):
                q["source_id"] = idx.source_by_text.get((q.get("locator") or "").split(":")[0])
            if not q.get("evidence_grade"):
                q["evidence_grade"] = st.get("evidence_grade")
        sids = idx.source_ids_of(st)
        provenance = []
        for sid in sids:
            s = idx.sources.get(sid)
            if not s:
                continue
            provenance.append({k: s.get(k) for k in ("source_id", "title", "url", "source_type", "evidence_grade",
                                                     "publication_date", "sha256", "text_sha256", "integrity_status",
                                                     "last_live_check")})
        excerpt = None
        for loc in st.get("locators") or []:
            sid = idx.source_by_text.get(loc.get("path")) if isinstance(loc, dict) else None
            if not sid:
                continue
            try:
                ex = read_excerpt(vroot(), idx.sources[sid], _locator_str(loc) or "")
            except store.StoreError:
                continue
            excerpt = {"source_id": sid, "locator": ex["locator"], "text": ex["text"]}
            break
        data = {**st, "person": idx.person_ref(st.get("person_id"), st.get("speaker_name")),
                "event": {"event_id": st["event_id"], "event_date": st.get("event_date"),
                          "title": st.get("event_title")},
                "grade_class": grade_class(st.get("evidence_grade")), "source_ids": sids,
                "provenance": provenance, "excerpt": excerpt}
        return _user_json(data)

    @bp.get("/api/stakeholder/people")
    @require_auth()
    @_handled
    def people_list():
        today = store.today_kst().isoformat()
        with conn_ctx() as c:
            counts = statements_count_by_person(c)
            people = store.list_people(status=request.args.get("status") or None, q=request.args.get("q") or None,
                                       conn=c)
            everyone = store.list_people(conn=c)
        return _user_json({
            "active_effective_count": sum(1 for p in everyone
                                          if p["status"] == "active" and (p.get("effective_from") or "") <= today),
            "updated_at": max((p.get("updated_at") or "" for p in everyone), default=None) or None,
            "items": [_person_public(p, admin=False, statements_count=counts.get(p["person_id"], 0))
                      for p in people],
        })

    @bp.get("/api/stakeholder/people/<person_id>")
    @require_auth()
    @_handled
    def person_detail(person_id: str):
        with conn_ctx() as c:
            p = store.get_person(person_id, conn=c)
            if p is None:
                raise store.NotFoundError("인물을 찾을 수 없습니다.", "NOT_FOUND")
            idx = _Index(c)
            history = store.list_role_history(person_id, conn=c)
            sts = store.list_statements(person_id=person_id, conn=c)
        data = _person_public(p, admin=_is_admin(), statements_count=len(sts))
        data["role_history"] = history
        data["recent_statements"] = [idx.statement_row(s) for s in sts[:5]]
        return _user_json(data)

    @bp.get("/api/stakeholder/people/<person_id>/photo")
    @require_auth()
    @_handled
    def person_photo(person_id: str):
        p = store.get_person(person_id, db_path=db)
        f = photos.photo_file(p.get("photo_path") if p else None, vroot())
        if f is None:
            raise store.NotFoundError("사진이 없습니다.", "NO_PHOTO")
        resp = send_file(f, mimetype="image/webp", max_age=0)
        resp.headers["Cache-Control"] = "private, max-age=300"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

    @bp.get("/api/stakeholder/sources")
    @require_auth()
    @_handled
    def sources_list():
        items = store.list_sources(db_path=db)
        if not _is_admin():
            items = [{k: v for k, v in s.items() if k not in ("local_path", "text_path")} for s in items]
        return _user_json({"items": items})

    @bp.get("/api/stakeholder/sources/<source_id>/excerpt")
    @require_auth()
    @_handled
    def source_excerpt(source_id: str):
        src = store.get_source(source_id, db_path=db)
        if src is None:
            raise store.NotFoundError("출처를 찾을 수 없습니다.", "NOT_FOUND")
        locator = request.args.get("locator") or ""
        if not locator.strip():
            raise BadRequest("locator 가 필요합니다.", "BAD_LOCATOR")
        ex = read_excerpt(vroot(), src, locator)
        return _user_json(ex)

    @bp.get("/api/stakeholder/sources/<source_id>/text")
    @require_auth(role="admin")
    @_handled
    def source_text(source_id: str):
        src = store.get_source(source_id, db_path=db)
        if src is None:
            raise store.NotFoundError("출처를 찾을 수 없습니다.", "NOT_FOUND")
        f = _safe_file(vroot(), src.get("text_path"))
        if f is None:
            raise store.NotFoundError("추출 텍스트 파일이 없습니다.", "TEXT_MISSING")
        resp = Response(f.read_bytes().decode("utf-8-sig", errors="replace"), mimetype="text/plain")
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Cache-Control"] = "private, no-store"
        return resp

    @bp.get("/api/stakeholder/sources/<source_id>/raw")
    @require_auth(role="admin")
    @_handled
    def source_raw(source_id: str):
        src = store.get_source(source_id, db_path=db)
        if src is None:
            raise store.NotFoundError("출처를 찾을 수 없습니다.", "NOT_FOUND")
        f = _safe_file(vroot(), src.get("local_path"))
        if f is None:
            raise store.NotFoundError("원본 파일이 없습니다.", "RAW_MISSING")
        mime = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        resp = send_file(f, mimetype=mime, as_attachment=True, download_name=f.name, max_age=0)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
        resp.headers["Cache-Control"] = "private, no-store"
        return resp

    @bp.get("/api/stakeholder/artifacts/relationship-map.svg")
    @require_auth()
    @_handled
    def relationship_map():
        with conn_ctx() as c:
            cands = [r[0] for r in c.execute(
                "SELECT path FROM sv_artifact WHERE lower(path) LIKE '%.svg' ORDER BY "
                "(lower(path) LIKE '%relationship%' OR path LIKE '%관계%' OR lower(COALESCE(name,'')) "
                "LIKE '%relationship%') DESC, path")]
            cands += [r[0] for r in c.execute(
                "SELECT path FROM sv_sync_file WHERE lower(path) LIKE '%.svg' AND instr(path, '/')=0 ORDER BY path")]
        for rel in cands:
            try:
                f = _safe_file(vroot(), rel)
            except BadRequest:
                continue
            if f is not None:
                resp = send_file(f, mimetype="image/svg+xml", max_age=0)
                resp.headers["Content-Security-Policy"] = SVG_CSP
                resp.headers["X-Content-Type-Options"] = "nosniff"
                resp.headers["Cache-Control"] = "private, max-age=300"
                return resp
        raise store.NotFoundError("관계도 SVG 가 없습니다.", "NOT_FOUND")

    @bp.get("/api/stakeholder/runs")
    @require_auth()
    @_handled
    def runs_list():
        kind = _kind(request.args.get("kind") or "monitoring")
        return _user_json({"items": store.list_runs(kind, limit=_int_param("limit", 30, 1, 200), db_path=db)})

    @bp.get("/api/stakeholder/runs/<kind>/<run_id>")
    @require_auth()
    @_handled
    def run_detail(kind: str, run_id: str):
        run = store.get_run(_kind(kind), run_id, db_path=db)
        if run is None:
            raise store.NotFoundError("런을 찾을 수 없습니다.", "NOT_FOUND")
        md = None
        try:
            f = _safe_file(vroot(), run.get("md_path"))
        except BadRequest:
            f = None
        if f is not None:
            md = f.read_bytes().decode("utf-8-sig", errors="replace")
        return _user_json({**run, "md": md})

    # ── admin: 인물 ────────────────────────────────────────────────────────────

    def _admin_person(p: dict) -> dict:
        with conn_ctx() as c:
            n = c.execute("SELECT COUNT(*) FROM sv_statement WHERE person_id=?", (p["person_id"],)).fetchone()[0]
        return _person_public(p, admin=True, statements_count=n)

    @bp.post("/api/admin/stakeholder/people")
    @require_auth(role="admin")
    @_handled
    def admin_create_person():
        p = store.create_person(_json_body(), actor=_actor(), db_path=db)
        return jsonify(_admin_person(p)), 201

    @bp.put("/api/admin/stakeholder/people/<person_id>")
    @require_auth(role="admin")
    @_handled
    def admin_update_person(person_id: str):
        body = request.get_json(silent=True)
        if store.get_person(person_id, db_path=db) is None:
            raise store.NotFoundError("인물을 찾을 수 없습니다.", "NOT_FOUND")
        if not isinstance(body, dict):
            raise store.ValidationError("요청 본문은 JSON 객체여야 합니다.", "BAD_BODY")
        return jsonify(_admin_person(store.update_person(person_id, body, actor=_actor(), db_path=db)))

    @bp.patch("/api/admin/stakeholder/people/<person_id>/status")
    @require_auth(role="admin")
    @_handled
    def admin_set_status(person_id: str):
        status = _json_body().get("status")
        return jsonify(_admin_person(store.set_person_status(person_id, status, actor=_actor(), db_path=db)))

    @bp.post("/api/admin/stakeholder/people/<person_id>/photo")
    @require_auth(role="admin")
    @_handled
    def admin_upload_photo(person_id: str):
        if (request.content_length or 0) > photos.MAX_BYTES + PHOTO_UPLOAD_SLACK:
            raise photos.PhotoError(413, "사진은 5MB 이하만 업로드할 수 있습니다.", "PHOTO_TOO_LARGE")
        before = store.get_person(person_id, db_path=db)
        if before is None:
            raise store.NotFoundError("인물을 찾을 수 없습니다.", "NOT_FOUND")
        f = request.files.get("photo")
        if f is None:
            raise store.ValidationError("multipart 필드 'photo' 가 필요합니다.", "PHOTO_MISSING")
        declared = (f.mimetype or "").lower()
        if declared and declared not in ("image/jpeg", "image/png", "image/webp", "image/jpg",
                                         "application/octet-stream"):
            raise photos.PhotoError(415, "JPG·PNG·WEBP 사진만 업로드할 수 있습니다.", "PHOTO_TYPE")
        raw = f.stream.read(photos.MAX_BYTES + 1)
        if len(raw) > photos.MAX_BYTES:
            raise photos.PhotoError(413, "사진은 5MB 이하만 업로드할 수 있습니다.", "PHOTO_TOO_LARGE")
        root_now = vroot()
        rel = photos.save_person_photo(person_id, raw, root_now)
        try:
            p = store.set_person_photo(person_id, rel, actor=_actor(), db_path=db)
        except BaseException:
            photos.delete_photo(rel, root_now)
            raise
        if before.get("photo_path") and before["photo_path"] != rel:
            photos.delete_photo(before["photo_path"], root_now)
        return jsonify(_admin_person(p))

    @bp.delete("/api/admin/stakeholder/people/<person_id>/photo")
    @require_auth(role="admin")
    @_handled
    def admin_delete_photo(person_id: str):
        before = store.get_person(person_id, db_path=db)
        if before is None:
            raise store.NotFoundError("인물을 찾을 수 없습니다.", "NOT_FOUND")
        p = store.set_person_photo(person_id, None, actor=_actor(), db_path=db)
        if before.get("photo_path"):
            photos.delete_photo(before["photo_path"], vroot())
        return jsonify(_admin_person(p))

    # ── admin: 감사 제안 ────────────────────────────────────────────────────────

    def _with_events(p: dict) -> dict:
        return {**p, "events": store.list_proposal_events(p["proposal_id"], db_path=db)}

    @bp.get("/api/admin/stakeholder/proposals")
    @require_auth(role="admin")
    @_handled
    def admin_proposals():
        status = request.args.get("status") or None
        statuses = [s.strip() for s in status.split(",") if s.strip()] if status else None
        if statuses and any(s not in store.PROPOSAL_STATUSES for s in statuses):
            raise BadRequest("status 값이 올바르지 않습니다.", "BAD_STATUS")
        ptype = request.args.get("type") or None
        if ptype and ptype not in store.PROPOSAL_TYPES:
            raise BadRequest("type 값이 올바르지 않습니다.", "BAD_TYPE")
        with conn_ctx() as c:
            props = store.list_proposals(status=statuses, type=ptype, conn=c)
            props = approval.with_conflicts_status(props, conn=c)
            pending = c.execute("SELECT COUNT(*) FROM sv_audit_proposal WHERE status IN "
                                "('needs_review','needs_evidence')").fetchone()[0]
            updated = c.execute("SELECT MAX(updated_at) FROM sv_audit_proposal").fetchone()[0]
            out = [{**p, "events": store.list_proposal_events(p["proposal_id"], conn=c)} for p in props]
        return jsonify({"pending_count": pending, "updated_at": updated, "proposals": out})

    @bp.get("/api/admin/stakeholder/proposals/<proposal_id>")
    @require_auth(role="admin")
    @_handled
    def admin_proposal_detail(proposal_id: str):
        p = approval.proposal_view(proposal_id, db_path=db)
        if p is None:
            raise store.NotFoundError("감사 제안을 찾을 수 없습니다.", "NOT_FOUND")
        return jsonify(_with_events(p))

    @bp.post("/api/admin/stakeholder/proposals/<proposal_id>/approve")
    @require_auth(role="admin")
    @_handled
    def admin_approve(proposal_id: str):
        body = request.get_json(silent=True) or {}
        p = approval.approve(proposal_id, reviewer=_actor(), reason=body.get("reason") if isinstance(body, dict)
                             else None, db_path=db)
        return jsonify(_with_events(p))

    @bp.post("/api/admin/stakeholder/proposals/<proposal_id>/reject")
    @require_auth(role="admin")
    @_handled
    def admin_reject(proposal_id: str):
        body = request.get_json(silent=True) or {}
        approval.reject(proposal_id, reviewer=_actor(), reason=body.get("reason") if isinstance(body, dict)
                        else None, db_path=db)
        return jsonify(_with_events(approval.proposal_view(proposal_id, db_path=db)))

    @bp.post("/api/admin/stakeholder/proposals/<proposal_id>/conflicts/<index>/ack")
    @require_auth(role="admin")
    @_handled
    def admin_ack_conflict(proposal_id: str, index: str):
        try:
            idx = int(index)
        except ValueError as exc:
            raise approval.ApprovalError(422, f"충돌 index 가 올바르지 않습니다: {index!r}",
                                         "BAD_CONFLICT_INDEX") from exc
        body = request.get_json(silent=True) or {}
        p = approval.acknowledge_conflict(proposal_id, idx, reviewer=_actor(),
                                          reason=body.get("reason") if isinstance(body, dict) else None, db_path=db)
        return jsonify(_with_events(p))

    @bp.get("/api/admin/stakeholder/proposals/<proposal_id>/conflict-acks")
    @require_auth(role="admin")
    @_handled
    def admin_conflict_acks(proposal_id: str):
        if store.get_proposal(proposal_id, db_path=db) is None:
            raise store.NotFoundError("감사 제안을 찾을 수 없습니다.", "NOT_FOUND")
        return jsonify({"items": approval.list_conflict_acks(proposal_id, db_path=db)})

    # ── admin: 레지스트리 · 무결성 · sync ────────────────────────────────────────

    @bp.get("/api/admin/stakeholder/source-domains")
    @require_auth(role="admin")
    @_handled
    def admin_source_domains():
        today = store.today_kst().isoformat()
        items = store.list_source_domains(db_path=db)
        eff = sum(1 for s in items if s["status"] == "approved" and (s.get("effective_from") or "") <= today)
        return jsonify({"effective_count": eff, "items": items})

    @bp.get("/api/admin/stakeholder/integrity")
    @require_auth(role="admin")
    @_handled
    def admin_integrity():
        with conn_ctx() as c:
            summary = store._jo(store.get_meta("integrity_summary_json", conn=c))
            last_sync = store.get_meta("last_sync_at", conn=c)
            counts = {r[0]: r[1] for r in c.execute(
                "SELECT integrity_status, COUNT(*) FROM sv_source GROUP BY integrity_status")}
            art_counts = {r[0]: r[1] for r in c.execute(
                "SELECT integrity_status, COUNT(*) FROM sv_artifact GROUP BY integrity_status")}
            problems = [dict(r) for r in c.execute(
                "SELECT source_id, title, integrity_status, integrity_checked_at, local_path, sha256, text_path, "
                "text_sha256 FROM sv_source WHERE integrity_status IN ('mismatch','missing') ORDER BY source_id")]
            art_problems = [dict(r) for r in c.execute(
                "SELECT path, name, integrity_status, integrity_checked_at, sha256 FROM sv_artifact "
                "WHERE integrity_status IN ('mismatch','missing') ORDER BY path")]
        return jsonify({"summary": summary, "last_sync_at": last_sync, "counts": counts,
                        "artifact_counts": art_counts, "problems": problems, "artifact_problems": art_problems})

    @bp.post("/api/admin/stakeholder/sync")
    @require_auth(role="admin")
    @_handled
    def admin_sync():
        runner = _resolve_sync_runner()
        if runner is None:
            return _error(503, "stakeholder sync 런북·모듈이 아직 탑재되지 않았습니다.", "RUNBOOK_MISSING")
        try:
            res = runner(db, vroot())
        except Exception as exc:  # noqa: BLE001
            logger.exception("stakeholder 수동 sync 실패")
            res = {"ok": False, "summary": f"sync 예외: {type(exc).__name__}: {exc}"[:300], "detail": {}}
        logger.info("[stakeholder] 수동 sync by %s: ok=%s", _actor(), res.get("ok"))
        if res.get("ok"):
            return jsonify(res)
        status, code = sync_failure_status(res)
        return jsonify({**res, "error": res.get("summary") or "sync 실패", "code": code}), status

    # ── agent 채널 ──────────────────────────────────────────────────────────────

    @bp.get("/api/agent/stakeholder/registry")
    @require_auth(role="agent")
    @_handled
    def agent_registry():
        return jsonify(store.registry_snapshot(today=store.today_kst(), db_path=db))

    @bp.post("/api/agent/stakeholder/proposals")
    @require_auth(role="agent")
    @_handled
    def agent_create_proposal():
        p = approval.create_proposal(request.get_json(silent=True), origin="agent_api", actor=_actor() or None,
                                     db_path=db)
        return jsonify(p), 201

    @bp.get("/api/agent/stakeholder/proposals")
    @require_auth(role="agent")
    @_handled
    def agent_list_proposals():
        run_id = request.args.get("audit_run_id") or None
        return jsonify({"audit_run_id": run_id, "items": store.list_proposals(audit_run_id=run_id, db_path=db)})

    return bp
