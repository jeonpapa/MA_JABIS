#!/usr/bin/env python3
"""급여 기준 시드 → 운영(또는 임의) DB 적재. 급여 기준 '파생 테이블 8개'만 건드린다 — 다른 테이블은 읽지도 쓰지도 않는다.

  python scripts/seed_hira_criteria.py --status              # 현재 DB 상태·시드 버전
  python scripts/seed_hira_criteria.py                       # dry-run(기본): 무엇이 바뀔지만 출력
  python scripts/seed_hira_criteria.py --apply               # 적용 (한 트랜잭션, 실패 시 전부 롤백)
  python scripts/seed_hira_criteria.py --apply --if-changed  # 시드 버전이 DB 의 것과 다를 때만 (부팅용·멱등)
  python scripts/seed_hira_criteria.py --db /tmp/x.db        # 다른 DB 로 리허설

안전장치
- dry-run 이 기본. 시드가 없거나 어느 테이블이든 비어 있으면 적용 거부(빈 결과 apply 거부).
- 같은 이름의 테이블이 이미 있는데 열 구성이 시드와 다르면 중단(다른 용도의 테이블을 덮어쓰지 않는다).
- criteria_disease_kcd 의 method='manual' 행(사람이 고친 질병코드)은 보존 — 시드가 같은 키를 갖고 있어도 manual 이 우선.
- 적용 전 기존 파생 테이블을 data/hira_criteria/backup/ 에 sqlite 로 백업하고, undo 정보(JSON)를 남긴다.
  (처음 적용이면 되돌리기 = 아래 UNDO_DROP 테이블 DROP + hira_criteria_seed_meta DROP)
"""
from __future__ import annotations

import argparse
import gzip
import json
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from agents.hira_criteria.schema import ensure_schema  # noqa: E402

DEFAULT_DB = BASE / "data" / "db" / "drug_prices.db"
SEED_DIR = BASE / "seed" / "hira_criteria"
SEED_FILE = SEED_DIR / "hira_criteria_seed.sqlite.gz"
VERSION_FILE = SEED_DIR / "VERSION.json"

# 파생 테이블 (전량 교체) / 부분 교체(criteria_disease_kcd: 시드가 다루는 subject_type 만, manual 보존)
REPLACE = ["kcd_code", "kcd_alias", "onco_criteria_regimen", "onco_criteria_regimen_drug", "drug_criteria_item", "criteria_section_kcd"]
PARTIAL = ("criteria_disease_kcd", "subject_type IN ('onco_regimen','drug_item')")
SOURCE = "hira_criteria_source"      # INSERT OR IGNORE (UNIQUE kind+sha256), 기존 행 보존
ALL_TABLES = REPLACE + [PARTIAL[0], SOURCE]
# ensure_schema 가 쓰지 않는 빈 허가초과 테이블 2개도 함께 만든다(화면에서 제외한 기능이라 비어 있음) — 되돌릴 때 같이 DROP
UNDO_DROP = ALL_TABLES + ["hira_criteria_seed_meta", "onco_offlabel_regimen", "onco_offlabel_rejected"]
META_DDL = ("CREATE TABLE IF NOT EXISTS hira_criteria_seed_meta ("
            "id INTEGER PRIMARY KEY CHECK(id=1), version TEXT, applied_at TEXT, counts_json TEXT)")


class SeedError(RuntimeError):
    pass


def _cols(conn: sqlite3.Connection, table: str, schema: str = "main") -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA {schema}.table_info({table})")]


def _exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def _count(conn: sqlite3.Connection, table: str, where: str | None = None, schema: str = "main") -> int | None:
    if schema == "main" and not _exists(conn, table):
        return None
    return conn.execute(f"SELECT COUNT(*) FROM {schema}.{table}" + (f" WHERE {where}" if where else "")).fetchone()[0]


def seed_version() -> dict:
    if not SEED_FILE.exists() or not VERSION_FILE.exists():
        raise SeedError(f"시드 없음: {SEED_FILE}")
    return json.loads(VERSION_FILE.read_text(encoding="utf-8"))


def db_version(conn: sqlite3.Connection) -> str | None:
    if not _exists(conn, "hira_criteria_seed_meta"):
        return None
    r = conn.execute("SELECT version FROM hira_criteria_seed_meta WHERE id=1").fetchone()
    return r[0] if r else None


def _open_seed(td: str) -> sqlite3.Connection:
    tmp = Path(td) / "seed.sqlite"
    with gzip.open(SEED_FILE, "rb") as fi, open(tmp, "wb") as fo:
        shutil.copyfileobj(fi, fo)
    c = sqlite3.connect(tmp)
    return c


def plan(conn: sqlite3.Connection, seed: sqlite3.Connection) -> dict:
    """적용하면 어떻게 되는지(테이블별 현재 → 시드 행 수, 스키마 충돌). 쓰기 없음."""
    out, problems = {}, []
    for t in ALL_TABLES:
        sc = _count(seed, t, schema="main")
        if not sc:
            problems.append(f"시드의 {t} 가 비어 있음")
        cur = _count(conn, t)
        if cur is not None:
            have, want = _cols(conn, t), _cols(seed, t)
            if [c for c in have] != [c for c in want]:
                problems.append(f"{t}: 기존 테이블 열 구성이 시드와 다름 ({have} ≠ {want}) — 다른 용도의 테이블일 수 있어 중단")
        entry = {"now": cur, "seed": sc}
        if t == PARTIAL[0]:
            entry["now_manual"] = (conn.execute("SELECT COUNT(*) FROM criteria_disease_kcd WHERE method='manual'").fetchone()[0]
                                   if cur is not None else 0)
            entry["now_replaced_scope"] = (_count(conn, t, PARTIAL[1] + " AND method!='manual'") if cur is not None else 0)
        out[t] = entry
    return {"tables": out, "problems": problems}


def status(db: Path) -> dict:
    conn = sqlite3.connect(db)
    try:
        sv = None
        try:
            sv = seed_version()
        except SeedError:
            pass
        return {"db": str(db), "db_seed_version": db_version(conn), "seed_version": sv and sv["version"],
                "tables": {t: _count(conn, t) for t in ALL_TABLES}}
    finally:
        conn.close()


def apply(db: Path, *, dry_run: bool, if_changed: bool) -> dict:
    sv = seed_version()
    conn = sqlite3.connect(db, timeout=60, isolation_level=None)
    conn.execute("PRAGMA busy_timeout=60000")
    try:
        cur_ver = db_version(conn)
        if if_changed and cur_ver == sv["version"]:
            return {"action": "skip", "reason": f"이미 최신 시드({cur_ver})"}
        with tempfile.TemporaryDirectory() as td:
            seed = _open_seed(td)
            pl = plan(conn, seed)
            report = {"action": "dry-run" if dry_run else "apply", "db": str(db), "seed_version": sv["version"],
                      "db_seed_version": cur_ver, **pl}
            if pl["problems"]:
                report["action"] = "refused"
                return report
            seed.close()
            if dry_run:
                return report
            # ── 백업(기존 파생 테이블) ─────────────────────────────────────────
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            existing = [t for t in ALL_TABLES if _exists(conn, t)]
            backup = None
            if existing:
                bdir = db.parent.parent / "hira_criteria" / "backup"
                bdir.mkdir(parents=True, exist_ok=True)
                backup = bdir / f"hira_criteria_before_{stamp}.sqlite"
                conn.execute("ATTACH DATABASE ? AS bk", (str(backup),))
                for t in existing:
                    conn.execute(f"CREATE TABLE bk.{t} AS SELECT * FROM main.{t}")
                conn.execute("DETACH DATABASE bk")
            created = [t for t in UNDO_DROP if not _exists(conn, t)]
            # ── 적용 (한 트랜잭션; ATTACH 는 트랜잭션 밖에서) ─────────────────────
            seed_path = Path(td) / "seed.sqlite"
            conn.execute("ATTACH DATABASE ? AS sd", (str(seed_path),))
            conn.execute("BEGIN IMMEDIATE")
            try:
                ensure_schema(conn)
                conn.execute(META_DDL)
                for t in REPLACE:
                    cols = ",".join(_cols(conn, t))
                    conn.execute(f"DELETE FROM main.{t}")
                    conn.execute(f"INSERT INTO main.{t}({cols}) SELECT {cols} FROM sd.{t}")
                t, where = PARTIAL
                cols = ",".join(_cols(conn, t))
                conn.execute(f"DELETE FROM main.{t} WHERE {where} AND method!='manual'")
                conn.execute(f"INSERT OR IGNORE INTO main.{t}({cols}) SELECT {cols} FROM sd.{t}")      # manual 이 같은 키면 manual 우선
                scols = ",".join(c for c in _cols(conn, SOURCE) if c != "source_id")
                conn.execute(f"INSERT OR IGNORE INTO main.{SOURCE}({scols}) SELECT {scols} FROM sd.{SOURCE}")
                after = {x: _count(conn, x) for x in ALL_TABLES}
                for x in REPLACE:
                    if after[x] != pl["tables"][x]["seed"]:
                        raise SeedError(f"{x}: 적재 후 행 수 불일치 {after[x]} ≠ {pl['tables'][x]['seed']}")
                conn.execute("INSERT OR REPLACE INTO hira_criteria_seed_meta(id,version,applied_at,counts_json) VALUES (1,?,?,?)",
                             (sv["version"], datetime.now().isoformat(timespec="seconds"), json.dumps(after, ensure_ascii=False)))
                conn.execute("COMMIT")
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            finally:
                try:
                    conn.execute("DETACH DATABASE sd")
                except sqlite3.Error:
                    pass
            undo = {"applied_at": stamp, "seed_version": sv["version"], "previous_version": cur_ver, "backup": str(backup) if backup else None,
                    "UNDO_DROP_if_first_apply": created, "after_counts": after}
            udir = db.parent.parent / "hira_criteria"
            udir.mkdir(parents=True, exist_ok=True)
            (udir / f"seed_apply_{stamp}.undo.json").write_text(json.dumps(undo, ensure_ascii=False, indent=2), encoding="utf-8")
            report.update(after_counts=after, undo=str(udir / f"seed_apply_{stamp}.undo.json"), backup=str(backup) if backup else None)
            return report
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--apply", action="store_true", help="실제 적용 (기본은 dry-run)")
    ap.add_argument("--if-changed", action="store_true", help="DB 의 시드 버전이 다를 때만")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args(argv)
    db = Path(a.db)
    if not db.exists():
        print(f"DB 없음: {db}", file=sys.stderr)
        return 2
    try:
        out = status(db) if a.status else apply(db, dry_run=not a.apply, if_changed=a.if_changed)
    except SeedError as e:
        print(f"[seed_hira_criteria] 거부: {e}", file=sys.stderr)
        return 2
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 2 if out.get("action") == "refused" else 0


if __name__ == "__main__":
    sys.exit(main())
