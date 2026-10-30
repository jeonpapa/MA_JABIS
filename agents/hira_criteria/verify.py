"""최신성 점검 (네트워크 조회 + 스테이징 대조). 쓰기 없음.

  python3 -m agents.hira_criteria.pipeline check onco drug

- onco: 스테이징 통합본 기준일 vs 공고 게시판 최신 공고 시행일. 더 새 공고가 있으면 'stale' + 재실행 필요.
         공고예고(의견조회) 최신 건도 함께 보여준다(다음 달 변경 예고).
- drug: 스테이징 drug_criteria_item 의 최신 시행일 vs 보험인정기준 고시(약제) 최신 게시 — 스테이징 이후
        새 약제 고시가 있으면 'stale'.
- kcd : 스테이징 상병마스터 게시일 vs 자료실 최신 상병마스터.
"""
from __future__ import annotations

import re
import sqlite3

from . import sources
from .store import staging_path


def _q(track: str, sql: str, args=()):
    p = staging_path(track)
    if not p.exists():
        return None
    c = sqlite3.connect(p)
    try:
        return c.execute(sql, args).fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        c.close()


def check_latest(tracks: list[str]) -> dict:
    out: dict = {}
    if "onco" in tracks:
        st = _q("onco", "SELECT effective_date FROM hira_criteria_source WHERE kind='onco_consolidated' ORDER BY source_id DESC LIMIT 1")
        notice = sources.onco_notices(1)[0]
        cons_date = st[0] if st else None
        out["onco"] = {"staging_consolidated_date": cons_date, "latest_notice": notice["notice_no"],
                       "latest_notice_effective": notice["effective_date"],
                       "status": "missing" if not cons_date else ("stale" if notice["effective_date"] > cons_date else "current")}
        try:
            h = sources.fetch_text(sources.ONCO_PRENOTICE_BOARD)
            t = sources.strip_html(h)
            m = re.search(r"(공고예고(?:완료|중))\s*(\d{4}-\d{2}-\d{2}\s*~\s*\d{4}-\d{2}-\d{2})", t)
            out["onco"]["latest_prenotice"] = f"{m.group(2)} ({m.group(1)})" if m else None
        except Exception as e:  # noqa: BLE001
            out["onco"]["latest_prenotice"] = f"error: {e}"
    if "drug" in tracks:
        st = _q("drug", "SELECT MAX(effective_date) FROM drug_criteria_item")
        rows = sources.insu_list("01", 1, 50)["rows"]
        drug_rows = [r for r in rows if "(약제)" in r["basis"]]
        latest = max((r["posted_date"] or "" for r in drug_rows), default="")
        out["drug"] = {"staging_max_effective": st[0] if st else None, "latest_drug_gosi_posted": latest,
                       "latest_drug_gosi": [f"{r['basis']} {r['title']}" for r in drug_rows[:5]]}
        out["drug"]["status"] = "missing" if not (st and st[0]) else (
            "check" if latest and latest > (st[0] or "") else "current")
    if "kcd" in tracks or "common" in tracks:
        st = _q("common", "SELECT posted_date FROM hira_criteria_source WHERE kind='kcd_master' ORDER BY source_id DESC LIMIT 1")
        k = sources.kcd_master_latest()
        out["kcd"] = {"staging_posted": st[0] if st else None, "latest_posted": k["posted_date"],
                      "status": "current" if st and st[0] == k["posted_date"] else "stale"}
    return out
