"""약평위·암질심 인텔리전스 리포트 PDF 인제스트.

흐름: 사용자가 PDF 를 inbox 폴더에 드롭 (또는 admin 업로드)
  → 텍스트 추출 (pypdf) → LLM 분석 (제목/위원회/사전·사후/회차/요약/하이라이트)
  → reimb_reports 테이블 INSERT → PDF 는 archive 폴더로 이동.

- inbox  : data/hira_pipeline/보고서/inbox/
- archive: data/hira_pipeline/보고서/archive/
- 중복 방지: 파일 내용 sha1 해시 UNIQUE
- LLM 실패 시: 파일명 힌트만으로 등록 (analyzed=0) — 값 날조 금지, 재분석 가능

위원회 범위(committee) 두 컬럼 — 2026-10-08, 헤르메스 병합 보고서(cc25e4ff) 이후:
- `committee`       : 프로덕션 테이블에 박힌 CHECK (cancer|evaluation|NULL). SQLite 는 CHECK 를 못 바꾸고(테이블 재생성)
                      `CREATE TABLE IF NOT EXISTS` 도 손대지 않으므로 이 컬럼의 허용값은 영구히 그 셋이다.
- `committee_scope` : 매니페스트/판정의 권위값 cancer|evaluation|combined (ALTER ADD COLUMN — pdf_blob 과 같은 멱등 보강).
                      combined(약평위+암질심 한 보고서) 는 committee=NULL + committee_scope='combined' 로 저장한다.
- 읽기는 `report_scope(row)` 한 곳 — scope 가 없으면 committee, 둘 다 NULL 이면 'combined'(위원회 미지정 = 양쪽) 로 읽는다.
- 보고서 ↔ 회차 연결 규칙은 `report_covers_session()` 하나 — reimb_pipeline._linked_report(UI 결과 모달) 와
  agent_tasks.freshness.check_committee_reports(에이전트 팀) 가 같은 함수를 쓴다. 두 곳이 따로 판단하면 어긋난다.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)

# 매니페스트 계약 (agents/ingest/REIMB_DATA_README.md 트랙 B 가 사람용 문서)
COMMITTEE_SCOPES = ("cancer", "evaluation", "combined")      # committee_scope 허용값 — combined = 약평위+암질심 한 보고서
COMMITTEE_CHECK_VALUES = ("cancer", "evaluation")            # reimb_reports.committee CHECK 가 허용하는 값 (NULL 별도)
REPORT_TYPES = ("pre", "post", "monthly", "other")
COMBINED_LINK_WINDOW_DAYS = 3   # combined/NULL 보고서는 session_date ±N일 안의 두 위원회 회차에 붙는다 (10-01 약평위 ↔ 09-30 암질심)
_SESSION_COMMITTEE_ALIAS = {"AMJILSIM": "cancer", "YAKPYUNGWI": "evaluation", "cancer": "cancer", "evaluation": "evaluation"}

BASE_DIR = Path(__file__).resolve().parents[1]
DB_PATH = BASE_DIR / "data" / "db" / "drug_prices.db"
INBOX_DIR = BASE_DIR / "data" / "hira_pipeline" / "보고서" / "inbox"
ARCHIVE_DIR = BASE_DIR / "data" / "hira_pipeline" / "보고서" / "archive"

ANALYSIS_MODEL = "gpt-4o-mini"
MAX_TEXT_CHARS = 16000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reimb_reports (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    file_hash      TEXT UNIQUE,
    file_name      TEXT NOT NULL,
    pdf_path       TEXT NOT NULL,
    file_size      INTEGER,
    pages          INTEGER,
    title          TEXT,
    committee      TEXT CHECK (committee IS NULL OR committee IN ('cancer','evaluation')),
    report_type    TEXT CHECK (report_type IS NULL OR report_type IN ('pre','post','monthly','other')),
    year           INTEGER,
    cycle          INTEGER,
    session_date   TEXT,
    summary        TEXT,
    highlights_json TEXT,
    analyzed       INTEGER NOT NULL DEFAULT 0,
    analysis_model TEXT,
    analysis_error TEXT,
    source         TEXT DEFAULT 'inbox',
    created_at     TEXT DEFAULT (datetime('now')),
    analyzed_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_rr_committee ON reimb_reports(committee, report_type);
"""

# amjilsim_drugs 보강 — reimbursement-status 파이프라인 보드 UI 필드 (M3 대비, 멱등)
_DRUG_ALTERS = [
    ("indication", "ALTER TABLE amjilsim_drugs ADD COLUMN indication TEXT"),
    ("listing_type", "ALTER TABLE amjilsim_drugs ADD COLUMN listing_type TEXT"),
    ("submitted_date", "ALTER TABLE amjilsim_drugs ADD COLUMN submitted_date DATE"),
    ("notes", "ALTER TABLE amjilsim_drugs ADD COLUMN notes TEXT"),
    # 핵심 쟁점 (JSON 배열) — D±1 보고서 전사. 칸반 모달 인사이트 섹션 소스
    ("key_issues", "ALTER TABLE amjilsim_drugs ADD COLUMN key_issues TEXT"),
]


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema() -> None:
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(_SCHEMA)
        # reimb_reports.pdf_blob — PDF 바이너리 (배포 서버는 파일시스템에 PDF 없음 → DB 서빙)
        rr_cols = {r[1] for r in conn.execute("PRAGMA table_info(reimb_reports)")}
        if "pdf_blob" not in rr_cols:
            conn.execute("ALTER TABLE reimb_reports ADD COLUMN pdf_blob BLOB")
        # reimb_reports.committee_scope — combined 보고서 표식 (committee CHECK 는 못 바꾸므로 옆 컬럼, 모듈 docstring 참조)
        if "committee_scope" not in rr_cols:
            conn.execute("ALTER TABLE reimb_reports ADD COLUMN committee_scope TEXT")
            conn.execute("UPDATE reimb_reports SET committee_scope = committee WHERE committee IS NOT NULL")
        # amjilsim_drugs 가 아직 없는 DB(최초 배포 빈 볼륨 등)에선 ALTER 스킵 —
        # 테이블 생성 시점(amjilsim ingest) 이후 재호출되면 그때 보강된다.
        existing = {r[1] for r in conn.execute("PRAGMA table_info(amjilsim_drugs)")}
        if existing:
            for col, ddl in _DRUG_ALTERS:
                if col not in existing:
                    conn.execute(ddl)
        else:
            logger.info("[reimb_reports] amjilsim_drugs 미존재 — 컬럼 보강 스킵 (빈 DB)")
        conn.commit()


def _load_openai_key() -> Optional[str]:
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        return key
    env_path = BASE_DIR / "config" / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("OPENAI_API_KEY="):
                return line.split("=", 1)[1].strip()
    return None


def _extract_pdf_text(path: Path) -> tuple[str, int]:
    """PDF 텍스트 + 페이지 수. 실패 시 ('', 0)."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        pages = len(reader.pages)
        chunks = []
        total = 0
        for pg in reader.pages:
            t = pg.extract_text() or ""
            chunks.append(t)
            total += len(t)
            if total >= MAX_TEXT_CHARS:
                break
        return "\n".join(chunks)[:MAX_TEXT_CHARS], pages
    except Exception as e:
        logger.warning("[reimb_reports] PDF 텍스트 추출 실패 %s: %s", path.name, e)
        return "", 0


_FN_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_FN_CYCLE_RE = re.compile(r"(?:yakpyungwi|amjilsim)[-_](\d+)", re.I)


def _filename_hints(name: str) -> dict:
    """파일명 규칙 힌트: 2026-06-05_yakpyungwi-6_d_plus_1.pdf 등."""
    low = name.lower()
    hints: dict = {"committee": None, "report_type": None, "year": None,
                   "cycle": None, "session_date": None}
    if "yakpyungwi" in low or "약평" in name:
        hints["committee"] = "evaluation"
    elif "amjilsim" in low or "암질" in name:
        hints["committee"] = "cancer"
    if "d_plus" in low or "d+1" in low or "결과" in name:
        hints["report_type"] = "post"
    elif "d_minus" in low or "d-2" in low or "예측" in name:
        hints["report_type"] = "pre"
    elif "monthly" in low or "월간" in name or "트렌드" in name:
        hints["report_type"] = "monthly"
    m = _FN_DATE_RE.search(name)
    if m:
        hints["session_date"] = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        hints["year"] = int(m.group(1))
    m = _FN_CYCLE_RE.search(low)
    if m:
        hints["cycle"] = int(m.group(1))
    return hints


def _analyze_llm(text: str, hints: dict, file_name: str) -> dict:
    """OpenAI 로 리포트 메타+요약 추출. 실패 시 예외 — 호출부에서 힌트 fallback."""
    from openai import OpenAI
    key = _load_openai_key()
    if not key:
        raise RuntimeError("OPENAI_API_KEY 미설정")
    client = OpenAI(api_key=key)
    prompt = f"""다음은 한국 심평원 약제 급여 관련 위원회 (암질심=중증암질환심의위원회 / 약평위=약제급여평가위원회) 분석 리포트 본문이다.
파일명: {file_name}
파일명 힌트: {json.dumps(hints, ensure_ascii=False)}

본문에서 아래 JSON 을 추출하라. 본문에 없는 값은 null. 날조 금지.
{{
  "title": "리포트 제목 (본문 첫 제목, 없으면 내용 기반 1줄)",
  "committee": "cancer(암질심) | evaluation(약평위)",
  "report_type": "pre(사전 예측 D-2) | post(결과 리뷰 D+1) | monthly(월간 트렌드) | other",
  "year": 2026,
  "cycle": 회차 숫자,
  "session_date": "관련 회의일 YYYY-MM-DD",
  "summary": "핵심 요약 3~4문장 (한국어)",
  "highlights": ["핵심 포인트 5개 이내, 약제명·결과·전략 시사점 중심"]
}}

본문:
{text}"""
    resp = client.chat.completions.create(
        model=ANALYSIS_MODEL,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0,
        timeout=90,
    )
    data = json.loads(resp.choices[0].message.content)
    # 검증/정규화
    if data.get("committee") not in ("cancer", "evaluation"):
        data["committee"] = hints.get("committee")
    if data.get("report_type") not in ("pre", "post", "monthly", "other"):
        data["report_type"] = hints.get("report_type") or "other"
    if not isinstance(data.get("highlights"), list):
        data["highlights"] = []
    return data


def _sha1(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def ingest_pdf(path: Path, source: str = "inbox") -> dict:
    """단일 PDF 인제스트. 반환: {status: ingested|duplicate|error, ...}"""
    path = Path(path)
    if not path.exists() or path.suffix.lower() != ".pdf":
        return {"status": "error", "file": path.name, "error": "PDF 파일 아님"}

    file_hash = _sha1(path)
    with _connect() as conn:
        dup = conn.execute("SELECT id FROM reimb_reports WHERE file_hash = ?", (file_hash,)).fetchone()
    if dup:
        return {"status": "duplicate", "file": path.name, "id": dup["id"]}

    hints = _filename_hints(path.name)
    text, pages = _extract_pdf_text(path)
    file_size = path.stat().st_size

    analysis: dict = {}
    analyzed = 0
    analysis_error = None
    if text.strip():
        try:
            analysis = _analyze_llm(text, hints, path.name)
            analyzed = 1
        except Exception as e:
            analysis_error = str(e)[:300]
            logger.warning("[reimb_reports] LLM 분석 실패 %s: %s", path.name, e)
    else:
        analysis_error = "PDF 텍스트 추출 결과 없음"

    # archive 이동 (동명 충돌 시 hash prefix)
    dest = ARCHIVE_DIR / path.name
    if dest.exists():
        dest = ARCHIVE_DIR / f"{file_hash[:8]}_{path.name}"
    shutil.move(str(path), str(dest))

    # PDF 바이너리 — 배포 서버(파일시스템에 PDF 없음)에서 DB 만으로 다운로드 서빙
    try:
        pdf_blob = dest.read_bytes()
    except Exception as e:
        pdf_blob = None
        logger.warning("[reimb_reports] pdf_blob 읽기 실패 %s: %s", dest.name, e)

    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO reimb_reports
               (file_hash, file_name, pdf_path, pdf_blob, file_size, pages, title, committee, committee_scope,
                report_type, year, cycle, session_date, summary, highlights_json,
                analyzed, analysis_model, analysis_error, source, analyzed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                file_hash, path.name, str(dest.relative_to(BASE_DIR)), pdf_blob,
                file_size, pages,
                analysis.get("title") or path.stem,
                analysis.get("committee") or hints.get("committee"),
                analysis.get("committee") or hints.get("committee"),   # committee_scope — inbox 는 위원회 지정만 (combined 는 매니페스트 경로)
                analysis.get("report_type") or hints.get("report_type"),
                analysis.get("year") or hints.get("year"),
                analysis.get("cycle") or hints.get("cycle"),
                analysis.get("session_date") or hints.get("session_date"),
                analysis.get("summary"),
                json.dumps(analysis.get("highlights") or [], ensure_ascii=False),
                analyzed, ANALYSIS_MODEL if analyzed else None, analysis_error, source,
                now if analyzed else None,
            ),
        )
        conn.commit()
        rid = cur.lastrowid
    return {"status": "ingested", "file": path.name, "id": rid, "analyzed": bool(analyzed),
            "title": analysis.get("title") or path.stem, "error": analysis_error}


def scan_inbox() -> list[dict]:
    """inbox 폴더의 모든 PDF 인제스트."""
    ensure_schema()
    results = []
    for p in sorted(INBOX_DIR.glob("*.pdf")):
        results.append(ingest_pdf(p, source="inbox"))
    return results


def reanalyze(report_id: int) -> dict:
    """등록된 리포트 재분석 (LLM 실패분 재시도)."""
    with _connect() as conn:
        row = conn.execute("SELECT * FROM reimb_reports WHERE id = ?", (report_id,)).fetchone()
    if not row:
        return {"status": "error", "error": "리포트 없음"}
    pdf = BASE_DIR / row["pdf_path"]
    hints = _filename_hints(row["file_name"])
    text, _ = _extract_pdf_text(pdf)
    if not text.strip():
        return {"status": "error", "id": report_id, "error": "PDF 텍스트 추출 결과 없음"}
    try:
        analysis = _analyze_llm(text, hints, row["file_name"])
    except Exception as e:
        return {"status": "error", "id": report_id, "error": str(e)[:300]}
    now = datetime.now().isoformat(timespec="seconds")
    if row["source"] == "git_sync":   # 매니페스트 메타가 권위 — LLM 이 위원회(combined→evaluation 등)·회차·날짜를 덮지 않는다
        analysis = {k: v for k, v in analysis.items() if k in ("title", "summary", "highlights")}
    committee = analysis.get("committee") or row["committee"]
    with _connect() as conn:
        conn.execute(
            """UPDATE reimb_reports SET title=?, committee=?, committee_scope=?, report_type=?, year=?, cycle=?,
               session_date=?, summary=?, highlights_json=?, analyzed=1, analysis_model=?,
               analysis_error=NULL, analyzed_at=? WHERE id=?""",
            (
                analysis.get("title") or row["title"],
                committee,
                committee if analysis.get("committee") else row["committee_scope"],
                analysis.get("report_type") or row["report_type"],
                analysis.get("year") or row["year"],
                analysis.get("cycle") or row["cycle"],
                analysis.get("session_date") or row["session_date"],
                analysis.get("summary"),
                json.dumps(analysis.get("highlights") or [], ensure_ascii=False),
                ANALYSIS_MODEL, now, report_id,
            ),
        )
        conn.commit()
    return {"status": "reanalyzed", "id": report_id, "title": analysis.get("title")}


def list_reports() -> list[dict]:
    ensure_schema()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM reimb_reports ORDER BY session_date DESC, id DESC"
        ).fetchall()
        sessions = _load_sessions(conn)
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["highlights"] = json.loads(d.pop("highlights_json") or "[]")
        except Exception:
            d["highlights"] = []
        d.pop("file_hash", None)
        blob = d.pop("pdf_blob", None)  # BLOB 은 목록 응답에서 제외 (다운로드 전용)
        d["has_blob"] = blob is not None
        d.update(report_dates(d, sessions))
        out.append(d)
    return out


# ── 보고서 작성일 vs 회의일 (표시용 파생 — DB 값은 바꾸지 않는다) ────────────────────────
#
# reimb_reports.session_date 는 출처별로 의미가 섞여 있다: git_sync 보고서는 파일명 앞 날짜(= 보고서 작성일,
# D-2/D-1/D+1) 가 들어가고, inbox LLM 분석 보고서는 회의일이 들어간다. 그래서 '2026년 8차 (2026-09-29)' 가
# 연간 일정의 8차 암질심 09.30 과 다른 날처럼 보였다 (2026-09-29 = D-1 사전 검토 보고서 작성일).
#   report_date  : 파일명 날짜(보고서 작성일). 파일명에 날짜가 없으면 None.
#   meeting_date : amjilsim_sessions 에서 (위원회, 연도, 차수) 로 찾은 회의일 → 없으면 session_date 가 그 위원회
#                  회의일과 정확히 같을 때만 그 날. 못 찾으면 None (날조 금지).
#   report_offset_days : report_date - meeting_date (예: -1 = 회의 하루 전 작성).

_COMMITTEE_TO_DB = {"cancer": "AMJILSIM", "evaluation": "YAKPYUNGWI"}


def _load_sessions(conn: sqlite3.Connection) -> list[dict]:
    try:
        rows = conn.execute(
            "SELECT session_id, committee_type, year, session_date, "
            "COALESCE(ordinal_official, ordinal_assumed) AS cycle FROM amjilsim_sessions").fetchall()
    except sqlite3.Error:
        return []  # 일정 테이블 없는 환경 (테스트·빈 DB)
    return [dict(r) for r in rows]


def report_dates(report: Mapping[str, Any], sessions: list[dict]) -> dict:
    """보고서 한 건의 (작성일, 회의일) 분리. report: file_name/committee/committee_scope/year/cycle/session_date."""
    m = _FN_DATE_RE.search(report.get("file_name") or "")
    report_date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None
    scope = report_scope(report)
    committee_db = _COMMITTEE_TO_DB.get(scope)
    meeting = None
    if committee_db and report.get("year") is not None and report.get("cycle") is not None:
        meeting = next((s for s in sessions if s["committee_type"] == committee_db
                        and s["year"] == report.get("year") and s["cycle"] == report.get("cycle")), None)
    if meeting is None and report.get("session_date"):
        meeting = next((s for s in sessions if s["session_date"] == report.get("session_date")
                        and (committee_db is None or s["committee_type"] == committee_db)), None)
    meeting_date = meeting["session_date"] if meeting else None
    offset = None
    rd, md = _parse_date(report_date), _parse_date(meeting_date)
    if rd and md:
        offset = (rd - md).days
    return {"report_date": report_date, "meeting_date": meeting_date,
            "meeting_session_id": meeting["session_id"] if meeting else None,
            "report_offset_days": offset}


def get_report(report_id: int) -> Optional[dict]:
    """메타만 (pdf_blob 제외 — 직렬화 방지)."""
    with _connect() as conn:
        cols = [c[1] for c in conn.execute("PRAGMA table_info(reimb_reports)")]
        sel = ", ".join(c for c in cols if c != "pdf_blob")
        row = conn.execute(f"SELECT {sel} FROM reimb_reports WHERE id = ?", (report_id,)).fetchone()
    return dict(row) if row else None


def get_pdf_bytes(report_id: int) -> Optional[bytes]:
    """다운로드 서빙용 — pdf_blob 우선, 없으면 pdf_path 파일 fallback."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT pdf_blob, pdf_path FROM reimb_reports WHERE id = ?", (report_id,)).fetchone()
    if not row:
        return None
    if row["pdf_blob"]:
        return bytes(row["pdf_blob"])
    if row["pdf_path"]:
        p = BASE_DIR / row["pdf_path"]
        if p.exists():
            return p.read_bytes()
    return None


def backfill_blobs() -> dict:
    """기존 행의 pdf_path 파일을 읽어 pdf_blob 채움 (로컬 1회 실행 — 아카이브 파일 존재 시)."""
    ensure_schema()
    filled = missing = 0
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, pdf_path FROM reimb_reports WHERE pdf_blob IS NULL").fetchall()
        for r in rows:
            p = BASE_DIR / (r["pdf_path"] or "")
            if p.exists() and p.suffix.lower() == ".pdf":
                conn.execute("UPDATE reimb_reports SET pdf_blob = ? WHERE id = ?",
                             (p.read_bytes(), r["id"]))
                filled += 1
            else:
                missing += 1
        conn.commit()
    return {"filled": filled, "missing_file": missing}


# ──────────────────────────────────────────────────────────────────────────────
# git-sync 전달 자동화 — 보고서 매니페스트 (헤르메스/Claude 가 PDF 커밋 + 매니페스트 1줄)
#   배포 앱이 매니페스트의 git raw URL 에서 신규 보고서를 download+ingest → Intelligence Reports.
#   data/ 볼륨 가림과 무관 (GitHub 에서 받음). 매니페스트만 비볼륨 이미지 경로.
# ──────────────────────────────────────────────────────────────────────────────

import urllib.request  # noqa: E402

MANIFEST_PATH = Path(__file__).resolve().parent / "ingest" / "reports_manifest.json"
GITHUB_RAW_BASE = "https://raw.githubusercontent.com/jeonpapa/AccessRoutineAnalystic/main"


def _sha1_bytes(data: bytes) -> str:
    h = hashlib.sha1()
    h.update(data)
    return h.hexdigest()


# ── 위원회 범위 + 보고서 ↔ 회차 연결 규칙 (단일 소스) ───────────────────────────

def report_scope(report: Mapping[str, Any]) -> str:
    """보고서가 다루는 위원회: cancer | evaluation | combined.

    committee_scope(권위) → committee(구 행·CHECK 컬럼) → 둘 다 없으면 'combined'. 위원회가 적히지 않은 보고서는
    '모른다' 가 아니라 '한 위원회에 묶이지 않는다' 로 읽는다 — 연결은 날짜가 결정한다.
    """
    scope = report.get("committee_scope")
    if scope in COMMITTEE_SCOPES:
        return scope
    committee = report.get("committee")
    return committee if committee in COMMITTEE_CHECK_VALUES else "combined"


def _session_committee(session: Mapping[str, Any]) -> Optional[str]:
    raw = session.get("committee") or session.get("committee_type")
    return _SESSION_COMMITTEE_ALIAS.get(raw) if raw else None


def _parse_date(s: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(s)[:10]) if s else None
    except ValueError:
        return None


def report_covers_session(report: Mapping[str, Any], session: Mapping[str, Any],
                          window_days: int = COMBINED_LINK_WINDOW_DAYS) -> bool:
    """보고서 한 건이 회차 한 건을 다루는가 — UI(reimb_pipeline._linked_report)와 에이전트 팀(freshness.committee_reports)의 유일한 규칙.

    report : committee / committee_scope / session_date / year / cycle (reimb_reports 행 또는 매니페스트 메타)
    session: committee('cancer'|'evaluation' 또는 committee_type 'AMJILSIM'|'YAKPYUNGWI') / session_date 또는 date / year / cycle

    - 위원회 지정 보고서(cancer|evaluation): 같은 위원회이고, 회의일이 같거나 (연도+차수) 가 같다.
    - combined(또는 위원회 미지정): 두 위원회 어느 회차든 회의일이 보고서 session_date ±window_days 안이면 다룬다.
      (연도+차수) 는 쓰지 않는다 — 차수는 한 위원회의 번호라 '10차' 가 약평위 10차이지 암질심 10차(12-23)가 아니다.
    """
    scope = report_scope(report)
    s_committee = _session_committee(session)
    s_date = _parse_date(session.get("session_date") or session.get("date"))
    r_date = _parse_date(report.get("session_date"))
    if scope == "combined":
        return bool(s_date and r_date and abs((s_date - r_date).days) <= window_days)
    if s_committee != scope:
        return False
    if s_date and r_date and s_date == r_date:
        return True
    r_year, r_cycle = report.get("year"), report.get("cycle")
    return (r_year is not None and r_cycle is not None
            and r_year == session.get("year") and r_cycle == session.get("cycle"))


# ── 매니페스트 항목 검증 — 다운로드·LLM 보다 먼저 ────────────────────────────────

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_META_KEYS = ("file_name", "committee", "report_type", "year", "cycle", "session_date", "title")


def _coerce_int(value: Any, key: str, problems: list[str]) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        problems.append(f"{key}={value!r} 정수 아님")
        return None


def normalize_report_meta(meta: Mapping[str, Any]) -> tuple[dict, Optional[str]]:
    """매니페스트 메타 → 저장 가능한 메타. 반환 (meta, error). error 가 있으면 저장할 수 없는 항목이다.

    저장 불가를 다운로드·LLM 뒤에 IntegrityError 로 알면 행이 안 써져 file_hash 도 안 남고, 다음 밤 또 내려받고 또 분석한다
    (2026-10-02 combined 항목이 그랬다). 그래서 CHECK 가 거절할 것을 여기서 먼저 거절한다.
    반환 meta 의 committee 는 CHECK 호환값(cancer|evaluation|None), committee_scope 는 매니페스트 원값이다.
    """
    problems: list[str] = []
    out: dict = {k: meta.get(k) for k in _META_KEYS}
    scope = meta.get("committee_scope") or out.get("committee")    # 멱등: 이미 정규화된 meta(committee=NULL, scope=combined) 도 통과
    if scope not in COMMITTEE_SCOPES:
        problems.append(f"committee={scope!r} — 허용값 {'|'.join(COMMITTEE_SCOPES)} (combined = 약평위+암질심 한 보고서)")
    out["committee_scope"] = scope if scope in COMMITTEE_SCOPES else None
    out["committee"] = scope if scope in COMMITTEE_CHECK_VALUES else None
    if out.get("report_type") not in REPORT_TYPES:
        problems.append(f"report_type={out.get('report_type')!r} — 허용값 {'|'.join(REPORT_TYPES)}")
    sd = out.get("session_date")
    if sd is not None and not (isinstance(sd, str) and _ISO_DATE_RE.match(sd) and _parse_date(sd)):
        problems.append(f"session_date={sd!r} — YYYY-MM-DD 여야 회차와 연결된다")
    out["year"] = _coerce_int(out.get("year"), "year", problems)
    out["cycle"] = _coerce_int(out.get("cycle"), "cycle", problems)
    if not out.get("file_name"):
        problems.append("file_name 없음")
    return out, ("; ".join(problems) or None)


def validate_manifest_entry(entry: Mapping[str, Any]) -> tuple[dict, Optional[str]]:
    """매니페스트 항목 전체(url·file_hash 포함) 검증. 반환 (meta, error)."""
    meta, err = normalize_report_meta(entry)
    problems = [err] if err else []
    url = entry.get("url")
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        problems.append(f"url={url!r} — http(s) 여야 한다")
    fh = entry.get("file_hash")
    if fh is not None and not re.fullmatch(r"[0-9a-f]{40}", str(fh)):
        problems.append(f"file_hash={fh!r} — sha1 16진 40자")
    return meta, ("; ".join(problems) or None)


def _download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "ma-reports-sync/1.0"})
    token = os.environ.get("REPORTS_DATA_TOKEN") or os.environ.get("REIMB_DATA_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def build_reports_manifest(reports: "list[dict]", out: Path = MANIFEST_PATH,
                           raw_base: str = GITHUB_RAW_BASE) -> dict:
    """큐레이션 보고서 목록 → 매니페스트 JSON 생성 (1회/갱신).

    reports: [{repo_path(레포 상대경로), committee, report_type, year, cycle,
               session_date, title}] — repo_path 의 PDF 를 읽어 file_hash·url 계산.
    """
    from urllib.parse import quote
    entries = []
    for r in reports:
        p = BASE_DIR / r["repo_path"]
        if not p.exists():
            logger.warning("[reports_manifest] 파일 없음: %s", r["repo_path"])
            continue
        _, err = normalize_report_meta({**r, "file_name": p.name})
        if err:   # 적재 못 할 항목은 매니페스트에 쓰지 않는다 — 02:00 sync 가 아니라 작성 시점에 알린다
            raise ValueError(f"[reports_manifest] {p.name}: {err}")
        data = p.read_bytes()
        url = raw_base.rstrip("/") + "/" + quote(r["repo_path"])
        entries.append({
            "file_name": p.name,
            "file_hash": _sha1_bytes(data),
            "url": url,
            "committee": r.get("committee"),
            "report_type": r.get("report_type"),
            "year": r.get("year"),
            "cycle": r.get("cycle"),
            "session_date": r.get("session_date"),
            "title": r.get("title"),
        })
    payload = {"schema_version": 1, "reports": entries}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"path": str(out), "reports": len(entries)}


def load_reports_manifest(source: "str | Path | dict | None" = None) -> dict:
    """매니페스트 로드. source: None→로컬 MANIFEST_PATH, dict→그대로, URL→GET, 경로→읽기."""
    if isinstance(source, dict):
        return source
    if source is None:
        if MANIFEST_PATH.exists():
            return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        return {"schema_version": 1, "reports": []}
    src = str(source)
    if src.startswith(("http://", "https://")):
        req = urllib.request.Request(src, headers={"User-Agent": "ma-reports-sync/1.0"})
        token = os.environ.get("REPORTS_DATA_TOKEN") or os.environ.get("REIMB_DATA_TOKEN")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    return json.loads(Path(src).read_text(encoding="utf-8"))


def ingest_from_url(url: str, meta: dict) -> dict:
    """매니페스트 항목의 PDF 를 다운로드 → file_hash dedup → blob+메타로 UPSERT.

    meta(committee/report_type/year/cycle/session_date/title/file_hash/file_name)는 권위값.
    LLM 분석은 summary/highlights 보조(키 없으면 graceful skip — 메타는 유지).
    검증(normalize_report_meta)은 다운로드·LLM 보다 먼저 — 저장 못 할 항목에 비용을 쓰지 않는다.
    """
    ensure_schema()
    meta, err = normalize_report_meta(meta)
    if err:
        return {"status": "error", "url": url, "file": meta.get("file_name"), "error": f"매니페스트 메타 오류: {err}"}
    try:
        data = _download(url)
    except Exception as e:
        return {"status": "error", "url": url, "file": meta.get("file_name"), "error": f"download 실패: {e}"}

    file_hash = _sha1_bytes(data)
    with _connect() as conn:
        dup = conn.execute("SELECT id FROM reimb_reports WHERE file_hash = ?",
                           (file_hash,)).fetchone()
    if dup:
        return {"status": "duplicate", "id": dup["id"], "file_hash": file_hash}

    # 임시파일로 LLM 분석(보조) — 실패해도 메타로 등록
    import tempfile
    tmp = Path(tempfile.mktemp(suffix=".pdf"))
    tmp.write_bytes(data)
    summary = None
    highlights: list = []
    analyzed = 0
    analysis_error = None
    try:
        text, pages = _extract_pdf_text(tmp)
        if text.strip():
            try:
                a = _analyze_llm(text, _filename_hints(meta.get("file_name") or ""),
                                 meta.get("file_name") or "")
                summary = a.get("summary")
                highlights = a.get("highlights") or []
                analyzed = 1
            except Exception as e:
                analysis_error = str(e)[:300]
    except Exception as e:
        pages = None
        analysis_error = f"pdf 추출 실패: {e}"
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass

    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO reimb_reports
               (file_hash, file_name, pdf_path, pdf_blob, file_size, pages, title, committee, committee_scope,
                report_type, year, cycle, session_date, summary, highlights_json,
                analyzed, analysis_model, analysis_error, source, analyzed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (file_hash, meta.get("file_name") or "report.pdf", url, data, len(data), pages,
             meta.get("title"), meta.get("committee"), meta.get("committee_scope"), meta.get("report_type"),
             meta.get("year"), meta.get("cycle"), meta.get("session_date"),
             summary, json.dumps(highlights, ensure_ascii=False),
             analyzed, ANALYSIS_MODEL if analyzed else None, analysis_error,
             "git_sync", now if analyzed else None),
        )
        conn.commit()
        rid = cur.lastrowid
    return {"status": "ingested", "id": rid, "file": meta.get("file_name"), "file_hash": file_hash,
            "analyzed": bool(analyzed), "error": analysis_error}


def sync_reports(source: "str | Path | dict | None" = None) -> dict:
    """매니페스트 기반 보고서 git-sync. 각 항목 file_hash 가 DB 에 없으면 download+ingest.

    반환에 manifest_hash 포함 (스케줄러 해시게이트용). 멱등·무재다운로드(file_hash dedup).
    항목마다 ① file_hash 가 있으면 skip ② 검증(validate_manifest_entry) — 실패면 다운로드·LLM 없이 errors
    ③ ingest. 한 항목의 오류·예외는 errors 에 모으고 다른 항목은 계속한다.
    """
    ensure_schema()
    manifest = load_reports_manifest(source)
    m_hash = hashlib.sha256(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    entries = manifest.get("reports", [])

    with _connect() as conn:
        existing = {r["file_hash"] for r in conn.execute(
            "SELECT file_hash FROM reimb_reports").fetchall()}

    ingested, skipped, errors, warnings = [], 0, [], []
    for e in entries:
        if not isinstance(e, dict):
            errors.append({"file": None, "url": None, "error": f"항목이 객체가 아님: {e!r}"[:200]})
            continue
        fh = e.get("file_hash")
        if fh and fh in existing:
            skipped += 1
            continue
        meta, err = validate_manifest_entry(e)
        if err:
            logger.warning("[reimb_reports] 매니페스트 항목 거절(다운로드 안 함) %s: %s", e.get("file_name"), err)
            errors.append({"file": e.get("file_name"), "url": e.get("url"), "error": f"매니페스트 메타 오류: {err}"})
            continue
        try:
            res = ingest_from_url(e["url"], meta)
        except Exception as exc:   # 한 항목의 예외가 나머지 적재를 막지 않는다 — 잡 결과의 errors 로 올라간다
            logger.exception("[reimb_reports] 적재 예외 %s", e.get("file_name"))
            errors.append({"file": e.get("file_name"), "url": e.get("url"), "error": f"{type(exc).__name__}: {exc}"[:300]})
            continue
        if res["status"] == "ingested":
            ingested.append(res.get("file"))
            if fh and res.get("file_hash") and fh != res["file_hash"]:
                # 적재는 됐지만 매니페스트 hash 가 PDF 와 다르면 다음 sync 마다 다시 내려받는다(내용 hash 로 duplicate 처리) — 고치라고 알린다
                warnings.append({"file": e.get("file_name"), "url": e.get("url"),
                                 "warning": f"매니페스트 file_hash {fh[:12]}… ≠ PDF sha1 {res['file_hash'][:12]}… — 매니페스트를 고쳐야 재다운로드가 멈춘다"})
        elif res["status"] == "duplicate":
            skipped += 1
        else:
            errors.append({"file": e.get("file_name"), "url": e.get("url"), "error": res.get("error")})
    return {"manifest_hash": m_hash, "reports_in_manifest": len(entries),
            "ingested": ingested, "skipped": skipped, "errors": errors, "warnings": warnings}
