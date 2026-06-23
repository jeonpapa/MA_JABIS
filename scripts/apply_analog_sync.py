"""프로덕션 볼륨 DB 에 analog 데이터셋을 외과적으로 동기화한다.

대상 4개 테이블만 교체 (DROP 없이 DELETE+INSERT — 인덱스/스키마 보존):
  analog_reports, rsa_media_signals, yakpyungwi_meta, yakpyungwi_match_audit
drug_prices 등 스케줄러 관리 테이블은 절대 건드리지 않는다.

전제: data/deploy/analog_sync.db (로컬에서 export, sftp 로 볼륨 업로드) 가 존재.
절차: ① 백업(VACUUM INTO) → ② ensure_schema(컬럼 ALTER) → ③ 테이블별 DELETE+INSERT
      (공통 컬럼 교집합, id 보존) → ④ analog_fts rebuild → ⑤ 검증 카운트.

실행(프로덕션): python -m scripts.apply_analog_sync
이후 임베딩 백필: python -m scripts.backfill_analog_embeddings
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from agents.analog import store

SYNC_DB = store.BASE_DIR / "data" / "deploy" / "analog_sync.db"
TABLES = ["analog_reports", "rsa_media_signals", "yakpyungwi_meta", "yakpyungwi_match_audit"]


def _cols(conn: sqlite3.Connection, table: str, schema: str = "main") -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA {schema}.table_info({table})")]


def main() -> dict:
    if not SYNC_DB.exists():
        raise SystemExit(f"[apply] sync DB 없음: {SYNC_DB} (sftp 업로드 먼저)")

    # ① 스키마 호환 (analog_reports 신규 컬럼 ALTER + 신규 테이블 생성)
    store.ensure_schema()

    conn = store._connect()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    # ① 백업 — 라이브 DB 전체를 타임스탬프 파일로 (롤백 대비)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = store.DB_PATH.parent / f"drug_prices.pre_analog_sync.{stamp}.db"
    conn.execute("VACUUM INTO ?", (str(backup),))
    print(f"[apply] 백업 생성: {backup}")

    conn.execute("ATTACH DATABASE ? AS sync", (str(SYNC_DB),))
    report = {}
    try:
        conn.execute("BEGIN")
        for t in TABLES:
            sync_cols = set(_cols(conn, t, "sync"))
            if not sync_cols:
                print(f"[apply] sync 에 {t} 없음 — skip")
                continue
            main_cols = set(_cols(conn, t, "main"))
            common = [c for c in _cols(conn, t, "sync") if c in main_cols]
            collist = ",".join(common)
            before = conn.execute(f"SELECT COUNT(*) FROM main.{t}").fetchone()[0]
            conn.execute(f"DELETE FROM main.{t}")
            conn.execute(f"INSERT INTO main.{t} ({collist}) SELECT {collist} FROM sync.{t}")
            after = conn.execute(f"SELECT COUNT(*) FROM main.{t}").fetchone()[0]
            report[t] = {"before": before, "after": after, "cols": len(common)}
            print(f"[apply] {t}: {before} → {after} 행 (공통컬럼 {len(common)})")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("DETACH DATABASE sync")

    # ④ FTS 재빌드 (analog_reports 교체 반영)
    try:
        conn.execute("INSERT INTO analog_fts(analog_fts) VALUES('rebuild')")
        print("[apply] analog_fts rebuild 완료")
    except Exception as e:
        print(f"[apply] analog_fts rebuild 경고: {e}")

    # ⑤ 검증
    emb = conn.execute("SELECT COUNT(*) FROM analog_reports WHERE embedding IS NOT NULL").fetchone()[0]
    nul = conn.execute("SELECT COUNT(*) FROM analog_reports WHERE embedding IS NULL AND file_name LIKE '%.pdf'").fetchone()[0]
    rsa = conn.execute("SELECT COUNT(*) FROM analog_reports WHERE rsa_media_conditions IS NOT NULL").fetchone()[0]
    reimb = conn.execute("SELECT COUNT(*) FROM analog_reports WHERE first_reimbursement_date IS NOT NULL").fetchone()[0]
    conn.commit()
    conn.close()
    report["verify"] = {"embedding_filled": emb, "embedding_null_pdf": nul,
                        "rsa_media_rows": rsa, "first_reimb_rows": reimb}
    print(f"[apply] 검증: 임베딩 {emb} 채움 / NULL(pdf) {nul} / RSA미디어 {rsa}행 / 급여등재일 {reimb}행")
    print(f"[apply] 다음: python -m scripts.backfill_analog_embeddings  (NULL {nul}행 임베딩)")
    return report


if __name__ == "__main__":
    import json
    print(json.dumps(main(), ensure_ascii=False, indent=1))
