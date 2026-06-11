"""약평위·암질심 인텔리전스 리포트 PDF 인제스트.

흐름: 사용자가 PDF 를 inbox 폴더에 드롭 (또는 admin 업로드)
  → 텍스트 추출 (pypdf) → LLM 분석 (제목/위원회/사전·사후/회차/요약/하이라이트)
  → reimb_reports 테이블 INSERT → PDF 는 archive 폴더로 이동.

- inbox  : data/hira_pipeline/보고서/inbox/
- archive: data/hira_pipeline/보고서/archive/
- 중복 방지: 파일 내용 sha1 해시 UNIQUE
- LLM 실패 시: 파일명 힌트만으로 등록 (analyzed=0) — 값 날조 금지, 재분석 가능
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

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
        existing = {r[1] for r in conn.execute("PRAGMA table_info(amjilsim_drugs)")}
        for col, ddl in _DRUG_ALTERS:
            if col not in existing:
                conn.execute(ddl)
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

    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO reimb_reports
               (file_hash, file_name, pdf_path, file_size, pages, title, committee,
                report_type, year, cycle, session_date, summary, highlights_json,
                analyzed, analysis_model, analysis_error, source, analyzed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                file_hash, path.name, str(dest.relative_to(BASE_DIR)), file_size, pages,
                analysis.get("title") or path.stem,
                analysis.get("committee") or hints.get("committee"),
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
    with _connect() as conn:
        conn.execute(
            """UPDATE reimb_reports SET title=?, committee=?, report_type=?, year=?, cycle=?,
               session_date=?, summary=?, highlights_json=?, analyzed=1, analysis_model=?,
               analysis_error=NULL, analyzed_at=? WHERE id=?""",
            (
                analysis.get("title") or row["title"],
                analysis.get("committee") or row["committee"],
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
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["highlights"] = json.loads(d.pop("highlights_json") or "[]")
        except Exception:
            d["highlights"] = []
        d.pop("file_hash", None)
        out.append(d)
    return out


def get_report(report_id: int) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM reimb_reports WHERE id = ?", (report_id,)).fetchone()
    return dict(row) if row else None
