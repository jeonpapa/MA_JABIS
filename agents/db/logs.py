"""다운로드 로그 + 검색 이력 + 데이터 신선도."""
from __future__ import annotations

from datetime import datetime
from typing import Optional


class _LogsMixin:
    # ── 다운로드 / 처리 로그 ─────────────────────────────────────────────
    def log_download(self, brd_blt_no: int, post_number: int, apply_date: str = None,
                     filename: str = None, file_path: str = None,
                     status: str = "pending", error_msg: str = None):
        sql = """
            INSERT OR IGNORE INTO download_log
                (brd_blt_no, post_number, apply_date, filename, file_path,
                 download_status, downloaded_at)
            VALUES (?,?,?,?,?,'pending',NULL)
        """
        update_sql = """
            UPDATE download_log
            SET apply_date=?, filename=?, file_path=?,
                download_status=?, downloaded_at=?, error_msg=?
            WHERE brd_blt_no=?
        """
        with self._connect() as conn:
            conn.execute(sql, (brd_blt_no, post_number, apply_date, filename, file_path))
            conn.execute(update_sql, (
                apply_date, filename, file_path, status,
                datetime.now().isoformat(), error_msg, brd_blt_no,
            ))

    def log_process(self, brd_blt_no: int, status: str, record_count: int = 0, error_msg: str = None):
        sql = """
            UPDATE download_log
            SET process_status=?, record_count=?, processed_at=?, error_msg=?
            WHERE brd_blt_no=?
        """
        with self._connect() as conn:
            conn.execute(sql, (status, record_count, datetime.now().isoformat(), error_msg, brd_blt_no))

    def is_downloaded(self, brd_blt_no: int) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT download_status FROM download_log WHERE brd_blt_no=?",
                (brd_blt_no,)
            ).fetchone()
        return row is not None and row["download_status"] == "success"

    def is_processed(self, brd_blt_no: int) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT process_status FROM download_log WHERE brd_blt_no=?",
                (brd_blt_no,)
            ).fetchone()
        return row is not None and row["process_status"] == "success"

    def get_pending_files(self) -> list[dict]:
        """다운로드는 됐지만 아직 DB에 처리되지 않은 파일 목록"""
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT * FROM download_log
                WHERE download_status='success' AND process_status!='success'
                ORDER BY post_number ASC
            """).fetchall()
        return [dict(r) for r in rows]

    # ── 검색 이력 ─────────────────────────────────────────────────────────
    def log_search(self, query: str, search_type: str,
                   resolved_to: str = None, result_count: int = 0,
                   status: str = "complete") -> int:
        """검색 이력 기록. 삽입된 row id 반환."""
        sql = """
            INSERT INTO search_log (query, resolved_to, search_type, searched_at, result_count, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """
        with self._connect() as conn:
            cur = conn.execute(sql, (query, resolved_to, search_type,
                                     datetime.now().isoformat(), result_count, status))
        return cur.lastrowid

    def get_search_history(self, search_type: str = None, limit: int = 50) -> list[dict]:
        """검색 이력 조회. search_type 필터 가능."""
        if search_type:
            sql = """
                SELECT * FROM search_log WHERE search_type = ?
                ORDER BY searched_at DESC LIMIT ?
            """
            params = (search_type, limit)
        else:
            sql = "SELECT * FROM search_log ORDER BY searched_at DESC LIMIT ?"
            params = (limit,)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # ── 데이터 신선도 ─────────────────────────────────────────────────────
    def update_freshness(self, data_type: str, scope_key: str,
                         next_check: str = None, etag: str = None) -> None:
        """데이터 신선도 기록/갱신."""
        sql = """
            INSERT INTO data_freshness (data_type, scope_key, last_fetched, next_check, etag)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(data_type, scope_key) DO UPDATE SET
                last_fetched = excluded.last_fetched,
                next_check = excluded.next_check,
                etag = COALESCE(excluded.etag, data_freshness.etag)
        """
        with self._connect() as conn:
            conn.execute(sql, (data_type, scope_key, datetime.now().isoformat(),
                               next_check, etag))

    def get_freshness(self, data_type: str, scope_key: str) -> Optional[dict]:
        """특정 데이터의 신선도 조회."""
        sql = "SELECT * FROM data_freshness WHERE data_type = ? AND scope_key = ?"
        with self._connect() as conn:
            row = conn.execute(sql, (data_type, scope_key)).fetchone()
        return dict(row) if row else None

    def is_data_fresh(self, data_type: str, scope_key: str) -> bool:
        """데이터가 존재하고 한번이라도 수집된 적 있으면 True (영구 캐시)."""
        info = self.get_freshness(data_type, scope_key)
        return info is not None
