"""data/rsa_media/*.md (서브에이전트 리서치 결과) → rsa_media_signals + analog_reports 보완.

각 .md 의 ```json``` 블록(구조화)을 파싱. frontmatter 의 report_ids 로 analog_reports 의
rsa_media_* 필드 갱신(PDF 원본 RSA 와 분리). found:false 는 skip.

CLI: python -m agents.ingest.rsa_media_import
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BASE_DIR / "data" / "db" / "drug_prices.db"
MD_DIR = BASE_DIR / "data" / "rsa_media"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS rsa_media_signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    report_ids      TEXT,         -- JSON [id, ...]
    brand           TEXT,
    ingredient      TEXT,
    listing_date    TEXT,
    window_from     TEXT,
    window_to       TEXT,
    found           INTEGER,
    rsa_types       TEXT,         -- JSON
    rsa_type_primary TEXT,
    conditions      TEXT,         -- JSON
    monitoring      TEXT,         -- JSON
    patient_restrictions TEXT,    -- JSON
    confidence      TEXT,
    sources         TEXT,         -- JSON [{title,url,media,date}]
    md_file         TEXT,
    fetched_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_rms_brand ON rsa_media_signals(brand);
"""

_RE_FM = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
_RE_JSON = re.compile(r"```json\s*\n(.*?)```", re.DOTALL)


def _parse_md(path: Path) -> dict | None:
    text = path.read_text(encoding="utf-8")
    m = _RE_JSON.search(text)
    if not m:
        logger.warning("[rsa_media] json 블록 없음: %s", path.name)
        return None
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        logger.warning("[rsa_media] json 파싱 실패 %s: %s", path.name, e)
        return None
    # report_ids: frontmatter 우선, json 내 보조
    fm = _RE_FM.search(text)
    rids = data.get("report_ids")
    if not rids and fm:
        mm = re.search(r"report_ids:\s*\[([0-9,\s]+)\]", fm.group(1))
        if mm:
            rids = [int(x) for x in re.findall(r"\d+", mm.group(1))]
    data["report_ids"] = rids or []
    data["_md_file"] = path.name
    return data


def run() -> dict:
    if not MD_DIR.exists():
        return {"error": "data/rsa_media 없음"}
    conn = sqlite3.connect(str(DB_PATH))
    conn.executescript(_SCHEMA)
    now = datetime.now().isoformat(timespec="seconds")
    files = sorted(p for p in MD_DIR.glob("*.md") if not p.name.startswith("_"))
    loaded = applied = found_n = 0
    for p in files:
        d = _parse_md(p)
        if d is None:
            continue
        loaded += 1
        found = bool(d.get("found"))
        if found:
            found_n += 1
        conn.execute(
            "INSERT INTO rsa_media_signals (report_ids, brand, ingredient, listing_date, "
            "window_from, window_to, found, rsa_types, rsa_type_primary, conditions, monitoring, "
            "patient_restrictions, confidence, sources, md_file, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (json.dumps(d.get("report_ids"), ensure_ascii=False), d.get("brand"), d.get("ingredient"),
             d.get("listing_date"), d.get("window_from"), d.get("window_to"), int(found),
             json.dumps(d.get("rsa_types"), ensure_ascii=False), d.get("rsa_type_primary"),
             json.dumps(d.get("conditions"), ensure_ascii=False), json.dumps(d.get("monitoring"), ensure_ascii=False),
             json.dumps(d.get("patient_restrictions"), ensure_ascii=False), d.get("confidence"),
             json.dumps(d.get("sources"), ensure_ascii=False), d["_md_file"], now),
        )
        if found and d.get("report_ids"):
            for rid in d["report_ids"]:
                conn.execute(
                    "UPDATE analog_reports SET rsa_media_types=?, rsa_media_conditions=?, "
                    "rsa_media_monitoring=?, rsa_media_sources=?, rsa_media_confidence=?, rsa_media_fetched_at=? "
                    "WHERE id=?",
                    (json.dumps(d.get("rsa_types"), ensure_ascii=False),
                     json.dumps(d.get("conditions"), ensure_ascii=False),
                     json.dumps(d.get("monitoring"), ensure_ascii=False),
                     json.dumps(d.get("sources"), ensure_ascii=False),
                     d.get("confidence"), now, rid),
                )
                applied += 1
    conn.commit()
    conn.close()
    res = {"md_files": loaded, "found": found_n, "reports_updated": applied}
    logger.info("[rsa_media] %s", res)
    return res


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print(run())
