"""스테이징 DB → 본 DB 반영.

- 트랙별 스테이징: data/hira_criteria/staging/{common,onco,drug}.db
  common = kcd_code·kcd_alias (+ KCD 원문 source). onco/drug 는 common 을 'common' 으로 ATTACH 해 읽는다.
- dry-run = 스테이징까지만. apply_to_main() 만 data/db/drug_prices.db 에 쓴다(한 트랜잭션).
- 본 DB 반영 규칙
  * 파생 테이블은 트랙 단위로 통째 교체(최신만 유지).
  * criteria_disease_kcd 는 해당 트랙 subject_type 의 자동 행만 지우고 다시 넣는다. method='manual' 은 보존.
  * 스테이징 결과가 비어 있으면 반영 거부(빈 결과로 정본이 지워지는 사고 방지, data_contracts §5 와 같은 원칙).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .schema import ensure_schema

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data" / "hira_criteria"
STAGING_DIR = DATA_DIR / "staging"
RAW_DIR = DATA_DIR / "raw"
MD_DIR = DATA_DIR / "md"
MAIN_DB = BASE_DIR / "data" / "db" / "drug_prices.db"

TRACK_TABLES = {
    "common": ("kcd_code", "kcd_alias"),
    "onco": ("onco_criteria_regimen", "onco_criteria_regimen_drug", "onco_offlabel_regimen", "onco_offlabel_rejected"),
    "drug": ("drug_criteria_item", "criteria_section_kcd"),
}
TRACK_SUBJECTS = {"onco": ("onco_regimen", "onco_offlabel"), "drug": ("drug_item",), "common": ()}


def staging_path(track: str) -> Path:
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    return STAGING_DIR / f"{track}.db"


def connect_staging(track: str) -> sqlite3.Connection:
    conn = sqlite3.connect(staging_path(track))
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    if track != "common":
        common = staging_path("common")
        if common.exists():
            conn.execute("ATTACH DATABASE ? AS common", (str(common),))
    return conn


def record_source(conn: sqlite3.Connection, *, kind: str, sha256: str, **f) -> int:
    cols = ["kind", "sha256"] + list(f)
    vals = [kind, sha256] + list(f.values())
    conn.execute(f"INSERT OR IGNORE INTO hira_criteria_source({','.join(cols)}) VALUES ({','.join('?'*len(cols))})", vals)
    sets = ",".join(f"{k}=?" for k in f)
    if sets:
        conn.execute(f"UPDATE hira_criteria_source SET {sets} WHERE kind=? AND sha256=?", list(f.values()) + [kind, sha256])
    return conn.execute("SELECT source_id FROM hira_criteria_source WHERE kind=? AND sha256=?", (kind, sha256)).fetchone()[0]


def _count(conn: sqlite3.Connection, table: str, schema: str = "main") -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {schema}.{table}").fetchone()[0]


def apply_to_main(tracks: list[str], main_db: Path = MAIN_DB) -> dict:
    """스테이징 → 본 DB (한 트랜잭션). 반환: 테이블별 행 수."""
    report: dict = {}
    conn = sqlite3.connect(main_db, isolation_level=None)
    try:
        ensure_schema(conn)
        for t in tracks:
            st = staging_path(t)
            if not st.exists():
                raise RuntimeError(f"staging 없음: {st}")
            conn.execute(f"ATTACH DATABASE ? AS st_{t}", (str(st),))
        report["changes"] = _change_report(conn, tracks)      # 반영 전: 본 DB 대비 신설·변경·삭제
        conn.execute("BEGIN IMMEDIATE")
        for t in tracks:
            a = f"st_{t}"
            for table in TRACK_TABLES[t]:
                n = _count(conn, table, a)
                if n == 0:
                    raise RuntimeError(f"staging {t}.{table} 가 비어 있음 — 반영 거부")
                cols = [r[1] for r in conn.execute(f"PRAGMA {a}.table_info({table})")]
                cl = ",".join(cols)
                conn.execute(f"DELETE FROM main.{table}")
                conn.execute(f"INSERT INTO main.{table}({cl}) SELECT {cl} FROM {a}.{table}")
                report[table] = n
            src_cols = [r[1] for r in conn.execute(f"PRAGMA {a}.table_info(hira_criteria_source)") if r[1] != "source_id"]
            cl = ",".join(src_cols)
            conn.execute(f"INSERT OR IGNORE INTO main.hira_criteria_source({cl}) SELECT {cl} FROM {a}.hira_criteria_source")
            subjects = TRACK_SUBJECTS[t]
            if subjects:
                q = ",".join("?" * len(subjects))
                conn.execute(f"DELETE FROM main.criteria_disease_kcd WHERE subject_type IN ({q}) AND method!='manual'", subjects)
                cols = [r[1] for r in conn.execute(f"PRAGMA {a}.table_info(criteria_disease_kcd)")]
                cl = ",".join(cols)
                conn.execute(f"INSERT OR IGNORE INTO main.criteria_disease_kcd({cl}) SELECT {cl} FROM {a}.criteria_disease_kcd "
                             f"WHERE subject_type IN ({q})", subjects)
                report[f"criteria_disease_kcd[{t}]"] = conn.execute(
                    f"SELECT COUNT(*) FROM main.criteria_disease_kcd WHERE subject_type IN ({q})", subjects).fetchone()[0]
        conn.execute("COMMIT")
        if Path(main_db).resolve() == MAIN_DB.resolve():          # 테스트용 DB 는 보고서 파일을 남기지 않는다
            report["change_report"] = _write_change_md(report["changes"])
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    return report


# ── 갱신 변경 보고 ─────────────────────────────────────────────────────────────
_DIFF_SPEC = {
    "onco": ("onco_criteria_regimen", "regimen_key",
             "cancer_name_kr || ' · ' || regimen_text", "regimen_text || target_text || coalesce(last_notice_no,'')"),
    "drug": ("drug_criteria_item", "item_key", "title", "criteria_text || coalesce(gosi_no,'')"),
}


def _change_report(conn: sqlite3.Connection, tracks: list[str]) -> dict:
    out: dict = {}
    for t in tracks:
        if t not in _DIFF_SPEC:
            continue
        table, key, label, body = _DIFF_SPEC[t]
        try:
            old = {r[0]: (str(r[1] or r[0]), r[2]) for r in conn.execute(f"SELECT {key}, {label}, {body} FROM main.{table}")}
        except sqlite3.OperationalError:
            old = {}
        new = {r[0]: (str(r[1] or r[0]), r[2]) for r in conn.execute(f"SELECT {key}, {label}, {body} FROM st_{t}.{table}")}
        out[t] = {"added": sorted(new[k][0] for k in new.keys() - old.keys())[:200],
                  "removed": sorted(old[k][0] for k in old.keys() - new.keys())[:200],
                  "changed": sorted(new[k][0] for k in new.keys() & old.keys() if new[k][1] != old[k][1])[:200]}
        sub = TRACK_SUBJECTS.get(t) or ()
        if sub:
            q = ",".join("?" * len(sub))
            out[t]["needs_review"] = conn.execute(
                f"SELECT COUNT(*) FROM st_{t}.criteria_disease_kcd WHERE subject_type IN ({q}) AND review_status='needs_review'",
                sub).fetchone()[0]
    return out


def _write_change_md(changes: dict) -> str:
    from datetime import datetime
    d = DATA_DIR / "reports"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"apply_{datetime.now():%Y%m%d_%H%M}.md"
    L = [f"# 급여기준 DB 반영 변경 보고 ({datetime.now():%Y-%m-%d %H:%M})", ""]
    names = {"onco": "항암 요법", "drug": "일반약제 항목"}
    for t, c in changes.items():
        L.append(f"## {names.get(t, t)} — 신설 {len(c['added'])} · 변경 {len(c['changed'])} · 삭제 {len(c['removed'])}"
                 + (f" · 질병코드 검토 필요 {c['needs_review']}" if "needs_review" in c else ""))
        for k, title in (("added", "신설"), ("changed", "변경"), ("removed", "삭제")):
            if c[k]:
                L.append(f"### {title}")
                L += [f"- {x}" for x in c[k][:60]]
        L.append("")
    p.write_text("\n".join(L), encoding="utf-8")
    return str(p.relative_to(BASE_DIR))
