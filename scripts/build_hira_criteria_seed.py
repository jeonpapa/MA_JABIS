#!/usr/bin/env python3
"""심평원 급여기준 DB → 운영 시드 빌드 (로컬 본 DB 의 '급여 기준' 파생 테이블만 담은 sqlite.gz).

왜 시드인가: 운영 DB(sqlite, Fly 볼륨)는 스케줄러·원장이 계속 쓰는 살아 있는 DB 라 로컬 DB 를 통째로 덮어쓸 수 없다.
급여 기준 테이블은 모두 '새 테이블' 이므로, 이미지의 seed/hira_criteria/ 에 담아 부팅 때 그 테이블만 적재한다
(scripts/seed_hira_criteria.py). 기존 운영 테이블은 읽지도 쓰지도 않는다.

  python3 scripts/build_hira_criteria_seed.py            # 로컬 data/db/drug_prices.db → seed/hira_criteria/
  python3 scripts/build_hira_criteria_seed.py --check    # 현재 시드가 로컬 DB 와 같은지만 확인

시드에 담는 것: hira_criteria_source, kcd_code, kcd_alias, onco_criteria_regimen(+_drug), drug_criteria_item,
criteria_section_kcd, criteria_disease_kcd(항암 공고요법·일반약제 연결만).
담지 않는 것: 허가초과 요법(onco_offlabel_*) — 급여가 아니라 화면에서 제외했다.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
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

MAIN_DB = BASE / "data" / "db" / "drug_prices.db"
SEED_DIR = BASE / "seed" / "hira_criteria"
SEED_FILE = SEED_DIR / "hira_criteria_seed.sqlite.gz"
VERSION_FILE = SEED_DIR / "VERSION.json"

# (테이블, 복사 조건) — 순서 무관. criteria_disease_kcd 는 허가초과 연결 제외.
TABLES = [
    ("hira_criteria_source", None),
    ("kcd_code", None),
    ("kcd_alias", None),
    ("onco_criteria_regimen", None),
    ("onco_criteria_regimen_drug", None),
    ("drug_criteria_item", None),
    ("criteria_section_kcd", None),
    ("criteria_disease_kcd", "subject_type IN ('onco_regimen','drug_item')"),
]


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build(main_db: Path = MAIN_DB, out_dir: Path = SEED_DIR) -> dict:
    if not main_db.exists():
        raise SystemExit(f"본 DB 없음: {main_db}")
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "seed.sqlite"
        conn = sqlite3.connect(tmp)
        ensure_schema(conn)
        conn.execute("ATTACH DATABASE ? AS src", (str(main_db),))
        counts = {}
        for t, where in TABLES:
            cols = [r[1] for r in conn.execute(f"PRAGMA main.table_info({t})")]
            scols = {r[1] for r in conn.execute(f"PRAGMA src.table_info({t})")}
            if not scols:
                raise SystemExit(f"본 DB 에 테이블이 없음: {t} (먼저 pipeline apply)")
            use = [c for c in cols if c in scols]
            cl = ",".join(use)
            conn.execute(f"INSERT INTO main.{t}({cl}) SELECT {cl} FROM src.{t}" + (f" WHERE {where}" if where else ""))
            counts[t] = conn.execute(f"SELECT COUNT(*) FROM main.{t}").fetchone()[0]
        conn.commit()
        for t, n in counts.items():
            if n == 0:
                raise SystemExit(f"빈 테이블은 시드로 만들지 않음: {t}")
        src = {r[0]: r[1] for r in conn.execute(
            "SELECT kind, COALESCE(notice_no, title) FROM hira_criteria_source WHERE source_id IN "
            "(SELECT MAX(source_id) FROM hira_criteria_source GROUP BY kind)")}
        conn.execute("DETACH DATABASE src")
        conn.execute("VACUUM")
        conn.close()
        with open(tmp, "rb") as fi, gzip.GzipFile(out_dir / SEED_FILE.name, "wb", compresslevel=9, mtime=0) as fo:
            shutil.copyfileobj(fi, fo)
    sha = _sha(out_dir / SEED_FILE.name)
    meta = {"version": sha[:16], "sha256": sha, "built_at": datetime.now().isoformat(timespec="seconds"),
            "counts": counts, "sources": src, "bytes": (out_dir / SEED_FILE.name).stat().st_size}
    (out_dir / VERSION_FILE.name).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def check(main_db: Path = MAIN_DB) -> int:
    """현재 시드 파일이 로컬 DB 의 행 수와 일치하는지(버전 비교는 sha 가 gzip 시각에 영향받지 않게 mtime=0)."""
    if not VERSION_FILE.exists():
        print("시드 없음")
        return 1
    old = json.loads(VERSION_FILE.read_text(encoding="utf-8"))
    conn = sqlite3.connect(main_db)
    bad = []
    for t, where in TABLES:
        n = conn.execute(f"SELECT COUNT(*) FROM {t}" + (f" WHERE {where}" if where else "")).fetchone()[0]
        if n != old["counts"].get(t):
            bad.append(f"{t}: 시드 {old['counts'].get(t)} ≠ 로컬 {n}")
    print("시드 = 로컬 DB" if not bad else "시드가 오래됨:\n  " + "\n  ".join(bad))
    return 0 if not bad else 2


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if a.check:
        sys.exit(check())
    m = build()
    print(json.dumps(m, ensure_ascii=False, indent=2))
