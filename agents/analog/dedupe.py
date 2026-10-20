"""등재 아날로그 중복 병합·표기 정규화.

같은 제품·같은 약평위가 두 소스(기존 수집분, 'DREC 추가' 평가결과_*.pdf)로 따로 들어와 두 줄로 보이는 문제를 푼다.
(예: '씨트렐린구강붕해정5밀리그램' 과 '씨트렐린구강붕해정', 2025-07-11)

원칙
  - **상표가 다른 행은 합치지 않는다**: 한 보고서에 여러 상표가 묶인 경우(아프레밀라스트 8상표 등)는 지금처럼 각각 둔다.
    합치는 기준은 '표기만 다른 같은 제품' — product_key(공백·함량 꼬리·'등 N품목…' 제거) 가 같고 약평위일이 같은 행.
  - 차수가 서로 다른 행(둘 다 값이 있고 다름)이나 날짜가 연도만 아는 자리표시(YYYY-01-01)인데 차수가 다르면 다른 회차다 → 합치지 않는다.
  - 병합 시 남기는 행: 기존 수집분(파일명이 '평가결과_' 로 시작하지 않는 쪽 — 임베딩·보강·수동 정정이 붙어 있음). 빈 칸만 반대편 값으로 채운다.
  - 없앤 행의 파일명·해시는 analog_file_alias 에 남겨, 같은 파일이 다시 수집돼도 새 행이 생기지 않는다.
  - 표기 정규화: 같은 product_key 그룹의 brand_name 을 가장 흔한 '함량 꼬리 없는' 표기로 통일(원문 표기는 brand_name_raw 에 보존).
"""
from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict

ALIAS_DDL = """CREATE TABLE IF NOT EXISTS analog_file_alias (
    file_name TEXT PRIMARY KEY, file_hash TEXT, canonical_id INTEGER NOT NULL, reason TEXT, created_at TEXT)"""

_RE_STRENGTH_TAIL = re.compile(
    r"\s*\d[\d.,/\- ]*\s*(?:밀리그램|밀리그람|마이크로그램|그램|밀리리터|국제단위|단위|mg|㎎|μg|㎍|mcg|g|ml|mL|IU|%)"
    r"(?:\s*[-/]\s*\d*[\d.,]*\s*(?:ml|mL|밀리리터|g|mg|㎖|L))?\s*$", re.I)
_RE_ETC = re.compile(r"\s*등\s*\d+\s*(?:품목|개).*$")
_PLACEHOLDER = re.compile(r"-01-01$")
SKIP_COLS = {"id", "file_name", "file_hash", "embedding"}


def clean_brand(name: str | None) -> str:
    """표시용 상표: NFC, 함량 꼬리 제거('씨트렐린구강붕해정5밀리그램' → '씨트렐린구강붕해정')."""
    s = unicodedata.normalize("NFC", name or "").strip()
    prev = None
    while prev != s:
        prev = s
        s = _RE_STRENGTH_TAIL.sub("", s).strip()
    return s


def product_key(name: str | None) -> str:
    """같은 제품 판정용 키: 함량 꼬리·'등 N품목…'·공백·구두점 제거. '(성분)' 괄호 꼬리도 제거."""
    s = clean_brand(name)
    s = _RE_ETC.sub("", s)
    s = re.sub(r"\(.*$", "", s)                  # '(성분…' 이후
    s = re.sub(r"[\s\-_·.,\[\]]+", "", s)
    s = re.sub(r"\d.*$", "", s)                   # 남은 숫자 이후(8,16밀리그램 등)
    return s.lower()


def _same_session(a, b) -> bool:
    if not a["session_date"] or a["session_date"] != b["session_date"]:
        return False
    oa, ob = a["ordinal"], b["ordinal"]
    if oa is not None and ob is not None and oa != ob:
        return False
    if _PLACEHOLDER.search(a["session_date"]) and (oa is None or ob is None):
        return False                              # 연도만 아는 날짜인데 차수 정보도 없으면 같은 회차라 단정 못 함
    return True


def _num_tokens(*texts) -> set:
    """함량 비교용 숫자 토큰. '80.120밀리그램'(=80, 120 나열)처럼 점으로 이어진 나열도 각각 취급하고, '등 N품목' 꼬리는 무시."""
    out = set()
    for t in texts:
        t = _RE_ETC.sub("", unicodedata.normalize("NFC", t or ""))
        for tok in re.findall(r"\d+(?:\.\d+)?", t):
            out.add(tok)
            out.update(tok.split("."))
    return out


def _dosage_compatible(a, b) -> bool:
    ba, bb = _num_tokens(a["brand_name"]), _num_tokens(b["brand_name"])
    if ba and bb and not (ba & bb):          # 상표 자체의 규격 숫자가 다르면(트렐리지100 vs 200) 다른 제품
        return False
    ta, tb = _num_tokens(a["dosage"], a["brand_name"]), _num_tokens(b["dosage"], b["brand_name"])
    if not ta or not tb:
        return True
    return bool(ta & tb)


def _is_addon(row) -> bool:
    return (row["file_name"] or "").startswith("평가결과_")


def plan(conn: sqlite3.Connection) -> dict:
    """DB 를 읽어 병합·정규화 계획을 만든다(쓰지 않음). 반환: {'merges': [...], 'renames': [...]}"""
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM analog_reports ORDER BY id").fetchall()
    groups: dict = defaultdict(list)
    for r in rows:
        k = product_key(r["brand_name"])
        if k:
            groups[k].append(r)
    merges, drop_ids = [], set()
    for k, rs in groups.items():
        for i, a in enumerate(rs):
            if a["id"] in drop_ids:
                continue
            for b in rs[i + 1:]:
                if b["id"] in drop_ids or not _same_session(a, b) or not _dosage_compatible(a, b):
                    continue
                keep, drop = (b, a) if (_is_addon(a) and not _is_addon(b)) else (a, b)
                fills = {c: drop[c] for c in drop.keys()
                         if c not in SKIP_COLS and (keep[c] is None or (isinstance(keep[c], str) and not keep[c].strip()))
                         and drop[c] is not None and not (isinstance(drop[c], str) and not drop[c].strip())}
                merges.append({"key": k, "keep": keep["id"], "drop": drop["id"], "keep_file": keep["file_name"],
                               "drop_file": drop["file_name"], "drop_hash": drop["file_hash"], "keep_brand": keep["brand_name"],
                               "drop_brand": drop["brand_name"], "session": keep["session_date"], "fills": fills})
                drop_ids.add(drop["id"])
    # 표기 정규화(병합으로 사라지는 행 제외): 같은 제품 그룹에서 가장 깨끗한 표기(숫자·공백이 적고 짧은 것)로 통일.
    # 묶음 보고서('등 N품목…')·이미 가장 깨끗한 행은 건드리지 않는다.
    renames = []
    for k, rs in groups.items():
        live = [r for r in rs if r["id"] not in drop_ids]
        cands = {clean_brand(r["brand_name"]) for r in live} - {""}
        if not cands:
            continue
        target = min(cands, key=lambda c: (len(re.findall(r"\d", c)), c.count(" "), len(c)))
        if product_key(target) != k or re.search(r"[,，]\s*$", target):
            continue
        for r in live:
            if _RE_ETC.search(unicodedata.normalize("NFC", r["brand_name"] or "")):
                continue
            ba, bt = _num_tokens(clean_brand(r["brand_name"])), _num_tokens(target)
            if ba and bt and not (ba & bt):      # 규격 숫자가 다른 상표(트렐리지100 vs 200)는 서로 덮어쓰지 않는다
                continue
            if r["brand_name"] != target:
                renames.append({"id": r["id"], "old": r["brand_name"], "new": target})
    return {"merges": merges, "renames": renames}


def apply_plan(conn: sqlite3.Connection, pl: dict, *, verify: bool = True) -> dict:
    """계획 적용. 운영 DB 에 적용할 때는 keep/drop 의 파일명이 계획과 같을 때만 한다(compare-and-set)."""
    from datetime import datetime
    conn.row_factory = sqlite3.Row
    conn.execute(ALIAS_DDL)
    done = skipped = renamed = 0
    now = datetime.now().isoformat(timespec="seconds")
    with conn:
        for m in pl["merges"]:
            keep = conn.execute("SELECT file_name FROM analog_reports WHERE id=?", (m["keep"],)).fetchone()
            drop = conn.execute("SELECT file_name FROM analog_reports WHERE id=?", (m["drop"],)).fetchone()
            if verify and (not keep or not drop or keep["file_name"] != m["keep_file"] or drop["file_name"] != m["drop_file"]):
                skipped += 1
                continue
            for col, val in m["fills"].items():
                conn.execute(f"UPDATE analog_reports SET {col}=? WHERE id=? AND ({col} IS NULL OR TRIM(CAST({col} AS TEXT))='')", (val, m["keep"]))
            conn.execute("INSERT OR REPLACE INTO analog_file_alias (file_name, file_hash, canonical_id, reason, created_at) VALUES (?,?,?,?,?)",
                         (m["drop_file"], m["drop_hash"], m["keep"], f"동일 제품·약평위({m['session']}) 중복 병합", now))
            conn.execute("DELETE FROM analog_reports WHERE id=?", (m["drop"],))
            done += 1
        for rn in pl["renames"]:
            cur = conn.execute("UPDATE analog_reports SET brand_name=? WHERE id=? AND brand_name=?", (rn["new"], rn["id"], rn["old"]))
            renamed += cur.rowcount
        try:
            conn.execute("INSERT INTO analog_fts(analog_fts) VALUES('rebuild')")
        except sqlite3.Error:
            pass
    return {"merged": done, "skipped": skipped, "renamed": renamed}


def find_existing(conn: sqlite3.Connection, report: dict):
    """신규 보고서(report dict)와 같은 제품·같은 약평위의 기존 행 id. 없으면 None. (drec_sync 가 새 행을 만들기 전에 호출)"""
    conn.row_factory = sqlite3.Row
    key = product_key(report.get("brand_name"))
    if not key or not report.get("session_date"):
        return None
    probe = {"session_date": report["session_date"], "ordinal": report.get("ordinal")}
    for r in conn.execute("SELECT id, brand_name, session_date, ordinal FROM analog_reports WHERE session_date=?", (report["session_date"],)):
        if product_key(r["brand_name"]) == key and _same_session(probe, r):
            return r["id"]
    return None


def absorb_report(conn: sqlite3.Connection, canonical_id: int, report: dict, insert_cols: list[str]) -> list[str]:
    """신규 보고서가 기존 행과 같은 제품·같은 약평위일 때 새 행을 만들지 않고 흡수: 기존 행의 빈 칸만 보고서 값으로 채우고,
    보고서 파일을 별칭으로 남긴다(같은 파일이 다시 수집돼도 새 행이 생기지 않음). 채운 칼럼 목록을 반환."""
    from datetime import datetime
    conn.execute(ALIAS_DDL)
    filled = []
    for col in insert_cols:
        if col in SKIP_COLS or col in ("brand_name", "brand_name_raw", "review_result"):
            continue
        val = report.get(col)
        if val is None or (isinstance(val, str) and not val.strip()) or isinstance(val, (list, dict)):
            continue
        if isinstance(val, bool):
            val = int(val)
        cur = conn.execute(f"UPDATE analog_reports SET {col}=? WHERE id=? AND ({col} IS NULL OR TRIM(CAST({col} AS TEXT))='')", (val, canonical_id, ))
        if cur.rowcount:
            filled.append(col)
    conn.execute("INSERT OR REPLACE INTO analog_file_alias (file_name, file_hash, canonical_id, reason, created_at) VALUES (?,?,?,?,?)",
                 (report["file_name"], report.get("file_hash"), canonical_id, "신규 수집분이 기존 행과 동일 제품·약평위", datetime.now().isoformat(timespec="seconds")))
    return filled
