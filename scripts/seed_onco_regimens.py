#!/usr/bin/env python3
"""항암 레지멘 정본(onco_regimen / onco_regimen_drug) 시드 — 안전·멱등.

왜 필요한가:
    프로덕션(Fly)은 볼륨이 /app/data 를 마운트해 이미지의 data/ 를 가린다(.dockerignore 도 data/ 제외).
    정본 원본 data/onco_regimen_db.xlsx 는 컨테이너에 없으므로 onco_regimen 이 비어 Regimen Cost
    검색(/api/regimen/onco/search)이 항상 빈 결과. → data/ 밖 seed/onco_regimen/onco_regimens.json
    (xlsx 파싱 결과의 JSON 스냅샷, 이미지에 포함)으로 적재한다.

안전 원칙:
    - 기본은 dry-run (DB 를 read-only 로 열어 건수/예정 동작만 출력, 쓰기 없음)
    - --apply --if-empty : onco_regimen 이 비었을 때만 적재 (부팅 훅용). 이미 있으면 no-op
    - --apply           : ref / (regimen_ref, seq) 안정키 기준 upsert. DELETE 없음 (추가 행 보존)
    - 단일 트랜잭션(BEGIN IMMEDIATE) — 실패 시 전부 롤백
    - custom_regimen / regimen_comparisons / user_drug_dosing (사용자 데이터) 는 절대 건드리지 않음
    - agents 패키지(pandas 등 무거운 의존성) import 없이 표준 라이브러리만으로 동작

사용:
    python scripts/seed_onco_regimens.py                      # dry-run
    python scripts/seed_onco_regimens.py --apply --if-empty   # 비었을 때만 적재 (start_production.sh)
    python scripts/seed_onco_regimens.py --apply              # upsert (정본 갱신 반영, 삭제 없음)
    python scripts/seed_onco_regimens.py --export-from-xlsx   # xlsx → seed JSON 재생성 (로컬, openpyxl 필요)
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DB = BASE_DIR / "data" / "db" / "drug_prices.db"
DEFAULT_SEED = BASE_DIR / "seed" / "onco_regimen" / "onco_regimens.json"
DEFAULT_XLSX = BASE_DIR / "data" / "onco_regimen_db.xlsx"
SCHEMA_PY = BASE_DIR / "agents" / "db" / "schema.py"

SEED_FORMAT = "onco_regimen_seed/v1"
REG_COLS = ("ref", "regimen_id", "cancer_no", "cancer", "regimen_name", "therapy", "line", "drug_group")
DRUG_COLS = ("regimen_ref", "seq", "ingredient", "drug_group", "dose_value", "unit", "dose_days",
             "per_cycle", "cycle_days", "cycle_label", "total_cycles", "route", "note", "src", "verify")
# 시드가 쓰는 유일한 테이블 — 사용자 테이블(custom_regimen 등)은 여기에 절대 추가하지 말 것
TARGET_TABLES = ("onco_regimen", "onco_regimen_drug")

# schema.py 를 못 읽을 때의 폴백 DDL (agents/db/schema.py 와 동일 — 변경 시 함께 갱신)
_FALLBACK_DDL = {
    "onco_regimen": """CREATE TABLE IF NOT EXISTS onco_regimen (
    ref INTEGER PRIMARY KEY, regimen_id TEXT, cancer_no INTEGER, cancer TEXT,
    regimen_name TEXT, therapy TEXT, line TEXT, drug_group TEXT)""",
    "onco_regimen_drug": """CREATE TABLE IF NOT EXISTS onco_regimen_drug (
    regimen_ref INTEGER NOT NULL, seq INTEGER NOT NULL, ingredient TEXT, drug_group TEXT,
    dose_value REAL, unit TEXT, dose_days TEXT, per_cycle REAL, cycle_days INTEGER,
    cycle_label TEXT, total_cycles REAL, route TEXT, note TEXT, src TEXT, verify TEXT,
    PRIMARY KEY (regimen_ref, seq))""",
}


class SeedError(Exception):
    pass


# ── seed 로드 / 검증 ─────────────────────────────────────────────────────────

def load_seed(seed_path: Path = DEFAULT_SEED) -> tuple[list[tuple], list[tuple], dict]:
    """seed JSON → (regimen 행 튜플, drug 행 튜플, meta). 구조/키 무결성 검증."""
    data = json.loads(Path(seed_path).read_text(encoding="utf-8"))
    if data.get("format") != SEED_FORMAT:
        raise SeedError(f"unknown seed format: {data.get('format')!r}")
    regs: list[tuple] = []
    drugs: list[tuple] = []
    seen_ref: set[int] = set()
    seen_drug: set[tuple[int, int]] = set()
    for r in data.get("regimens") or []:
        ref = r.get("ref")
        if not isinstance(ref, int) or ref in seen_ref:
            raise SeedError(f"invalid/duplicate regimen ref: {ref!r}")
        seen_ref.add(ref)
        regs.append(tuple(r.get(c) for c in REG_COLS))
        for d in r.get("drugs") or []:
            d = {**d, "regimen_ref": ref}
            key = (ref, d.get("seq"))
            if not isinstance(key[1], int) or key in seen_drug:
                raise SeedError(f"invalid/duplicate drug key: {key!r}")
            seen_drug.add(key)
            drugs.append(tuple(d.get(c) for c in DRUG_COLS))
    if not regs:
        raise SeedError("seed has no regimens")
    counts = data.get("counts") or {}
    if counts and (counts.get("onco_regimen") != len(regs) or counts.get("onco_regimen_drug") != len(drugs)):
        raise SeedError(f"seed counts mismatch: meta={counts} actual={len(regs)}/{len(drugs)}")
    return regs, drugs, data


# ── DB ───────────────────────────────────────────────────────────────────────

def _target_ddl() -> list[str]:
    """agents/db/schema.py 의 DB_SCHEMA 에서 onco 2개 테이블 DDL 만 추출 (패키지 import 회피)."""
    try:
        spec = importlib.util.spec_from_file_location("_onco_seed_schema", SCHEMA_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # schema.py 는 순수 문자열 모듈
        schema = mod.DB_SCHEMA
        out = []
        for t in TARGET_TABLES:
            m = re.search(rf"CREATE TABLE IF NOT EXISTS {t} \(.*?\n\);", schema, re.S)
            out.append(m.group(0) if m else _FALLBACK_DDL[t])
        return out
    except Exception:
        return [_FALLBACK_DDL[t] for t in TARGET_TABLES]


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _count(conn: sqlite3.Connection, name: str) -> int | None:
    if not _table_exists(conn, name):
        return None
    return conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]


def _diff(conn: sqlite3.Connection, regs: list[tuple], drugs: list[tuple]) -> dict:
    """seed 대비 DB 현황 — new(없음) / changed(값 다름) / same."""
    out = {"reg_new": 0, "reg_changed": 0, "drug_new": 0, "drug_changed": 0}
    if _table_exists(conn, "onco_regimen"):
        cur = {r[0]: tuple(r) for r in conn.execute(f"SELECT {','.join(REG_COLS)} FROM onco_regimen")}
    else:
        cur = {}
    for r in regs:
        if r[0] not in cur:
            out["reg_new"] += 1
        elif cur[r[0]] != r:
            out["reg_changed"] += 1
    if _table_exists(conn, "onco_regimen_drug"):
        curd = {(r[0], r[1]): tuple(r) for r in conn.execute(f"SELECT {','.join(DRUG_COLS)} FROM onco_regimen_drug")}
    else:
        curd = {}
    for d in drugs:
        if (d[0], d[1]) not in curd:
            out["drug_new"] += 1
        elif curd[(d[0], d[1])] != d:
            out["drug_changed"] += 1
    return out


def _upsert(conn: sqlite3.Connection, regs: list[tuple], drugs: list[tuple]) -> None:
    reg_upd = ",".join(f"{c}=excluded.{c}" for c in REG_COLS if c != "ref")
    drug_upd = ",".join(f"{c}=excluded.{c}" for c in DRUG_COLS if c not in ("regimen_ref", "seq"))
    conn.executemany(
        f"INSERT INTO onco_regimen ({','.join(REG_COLS)}) VALUES ({','.join('?' * len(REG_COLS))}) "
        f"ON CONFLICT(ref) DO UPDATE SET {reg_upd}", regs)
    conn.executemany(
        f"INSERT INTO onco_regimen_drug ({','.join(DRUG_COLS)}) VALUES ({','.join('?' * len(DRUG_COLS))}) "
        f"ON CONFLICT(regimen_ref, seq) DO UPDATE SET {drug_upd}", drugs)


def run(db_path: Path = DEFAULT_DB, seed_path: Path = DEFAULT_SEED, apply: bool = False,
        if_empty: bool = False, log=print) -> dict:
    """시드 실행. 반환 dict: action(dry-run|seeded|upserted|skipped) + 건수."""
    regs, drugs, meta = load_seed(seed_path)
    db_path = Path(db_path)
    log(f"[onco-seed] seed={seed_path} regimens={len(regs)} drugs={len(drugs)} "
        f"source_sha256={(meta.get('source') or {}).get('sha256', '?')[:12]}")

    if not apply:
        if not db_path.exists():
            log(f"[onco-seed] DRY-RUN: DB 없음({db_path}) — --apply 시 테이블 생성 후 전량 적재")
            return {"action": "dry-run", "db_exists": False, "seed_regimens": len(regs), "seed_drugs": len(drugs)}
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            n_reg, n_drug = _count(conn, "onco_regimen"), _count(conn, "onco_regimen_drug")
            diff = _diff(conn, regs, drugs)
        finally:
            conn.close()
        would = ("skip (populated)" if if_empty and n_reg else
                 "seed (empty)" if not n_reg else "upsert")
        log(f"[onco-seed] DRY-RUN: db onco_regimen={n_reg} onco_regimen_drug={n_drug} diff={diff} → would {would}. "
            f"쓰기 없음 (--apply 로 실행)")
        return {"action": "dry-run", "db_exists": True, "onco_regimen": n_reg, "onco_regimen_drug": n_drug,
                "would": would, **diff, "seed_regimens": len(regs), "seed_drugs": len(drugs)}

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), isolation_level=None, timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("BEGIN IMMEDIATE")  # 쓰기 락 선점 → 비었는지 판정과 적재가 원자적
        try:
            for ddl in _target_ddl():
                conn.execute(ddl)
            n_reg_before = _count(conn, "onco_regimen") or 0
            n_drug_before = _count(conn, "onco_regimen_drug") or 0
            if if_empty and n_reg_before > 0:
                conn.execute("ROLLBACK")
                log(f"[onco-seed] skip — onco_regimen 이미 {n_reg_before}건 (--if-empty)")
                return {"action": "skipped", "onco_regimen": n_reg_before, "onco_regimen_drug": n_drug_before}
            diff = _diff(conn, regs, drugs)
            _upsert(conn, regs, drugs)
            n_reg, n_drug = _count(conn, "onco_regimen"), _count(conn, "onco_regimen_drug")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    action = "seeded" if n_reg_before == 0 else "upserted"
    log(f"[onco-seed] {action}: onco_regimen {n_reg_before}→{n_reg}, onco_regimen_drug {n_drug_before}→{n_drug} "
        f"diff={diff}")
    return {"action": action, "onco_regimen": n_reg, "onco_regimen_drug": n_drug,
            "before_regimen": n_reg_before, "before_drug": n_drug_before, **diff}


# ── seed 재생성 (로컬 전용) ──────────────────────────────────────────────────

def export_from_xlsx(xlsx_path: Path = DEFAULT_XLSX, seed_path: Path = DEFAULT_SEED, log=print) -> dict:
    """정본 xlsx → seed JSON. agents.scrapers.onco_regimen_ingest.parse_rows 재사용 (단일 파서)."""
    spec = importlib.util.spec_from_file_location(
        "_onco_ingest", BASE_DIR / "agents" / "scrapers" / "onco_regimen_ingest.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # openpyxl 필요
    regimens, drugs = mod.parse_rows(Path(xlsx_path))
    by_ref: dict[int, list[dict]] = {}
    for d in drugs:
        by_ref.setdefault(d["regimen_ref"], []).append({c: d.get(c) for c in DRUG_COLS if c != "regimen_ref"})
    out_regs = [{**{c: r.get(c) for c in REG_COLS}, "drugs": by_ref.get(r["ref"], [])} for r in regimens]
    payload = {
        "format": SEED_FORMAT,
        "description": "항암 레지멘 정본 (HIRA 항암요법 공고 기반, 용량 NCCN/허가사항 대조). "
                       "scripts/seed_onco_regimens.py 로 onco_regimen / onco_regimen_drug 에 적재. "
                       "사용자 데이터(custom_regimen 등) 미포함.",
        "source": {"file": "data/onco_regimen_db.xlsx", "sheet": getattr(mod, "SHEET", "레지멘DB"),
                   "sha256": hashlib.sha256(Path(xlsx_path).read_bytes()).hexdigest(),
                   "parser": "agents/scrapers/onco_regimen_ingest.py:parse_rows"},
        "counts": {"onco_regimen": len(out_regs), "onco_regimen_drug": len(drugs)},
        "regimen_columns": list(REG_COLS),
        "drug_columns": [c for c in DRUG_COLS if c != "regimen_ref"],
        "regimens": out_regs,
    }
    seed_path = Path(seed_path)
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    seed_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    load_seed(seed_path)  # 왕복 검증
    log(f"[onco-seed] exported {len(out_regs)} regimens / {len(drugs)} drugs → {seed_path} "
        f"({seed_path.stat().st_size:,} bytes)")
    return payload["counts"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--seed", type=Path, default=DEFAULT_SEED)
    ap.add_argument("--apply", action="store_true", help="실제 쓰기 (없으면 dry-run)")
    ap.add_argument("--if-empty", action="store_true", help="onco_regimen 이 비었을 때만 적재")
    ap.add_argument("--export-from-xlsx", nargs="?", const=DEFAULT_XLSX, type=Path, metavar="XLSX",
                    help="xlsx → seed JSON 재생성 (DB 미접근)")
    a = ap.parse_args(argv)
    try:
        if a.export_from_xlsx:
            export_from_xlsx(a.export_from_xlsx, a.seed)
        else:
            run(a.db, a.seed, apply=a.apply, if_empty=a.if_empty)
    except (SeedError, OSError, sqlite3.Error, json.JSONDecodeError) as e:
        print(f"[onco-seed] ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
