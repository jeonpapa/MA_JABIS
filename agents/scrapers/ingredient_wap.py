"""HIRA 주성분별 가중평균가격(WAP) 외부 API 프록시.

투약비용비교 탭에서 '주성분 가중평균' 소스로 사용. 특허 만료 약제는 동일성분
제네릭 다수라 단일 브랜드가 아닌 주성분 가중평균가로 투약비교하는 실무 관행 반영.

- 공개 URL: https://hira-wap-api.fly.dev (프로덕션 fly.io·로컬 모두 도달 가능)
- 인증: X-API-Key 헤더
- 핵심 endpoint: GET /prices/as-of?date=YYYY-MM-DD&q=<영문성분> | &code=<주성분코드>
  반환 results[]: main_ingredient_code, ingredient_name(영문), weighted_avg_price(규격당 단위가),
  period(반기), match_mode, fallback_previous. HIRA 데이터는 반기 단위.

graceful: BASE_URL/KEY 미설정·타임아웃·non-200 시 raise 대신
{"available": False, "reason": ...} 반환 (국내약가 경로엔 영향 없음). 성공 응답만
sqlite 캐시(캐시-DB-first). 자격증명은 config/.env (하드코딩 금지).
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[2]
ENV_PATH = BASE_DIR / "config" / ".env"
DB_PATH = BASE_DIR / "data" / "db" / "drug_prices.db"

DEFAULT_BASE_URL = "https://hira-wap-api.fly.dev"
CACHE_TTL_DAYS = 30


def _load_env() -> None:
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def _config() -> tuple[str, str]:
    """(base_url, api_key). key 미설정이면 빈 문자열.

    변수명 호환: HIRA_WAP_* (config/.env 실제) 우선, INGREDIENT_WAP_* fallback.
    """
    _load_env()
    base = (os.environ.get("HIRA_WAP_BASE_URL", "").strip()
            or os.environ.get("INGREDIENT_WAP_BASE_URL", "").strip()
            or DEFAULT_BASE_URL).rstrip("/")
    key = (os.environ.get("HIRA_WAP_API_KEY", "").strip()
           or os.environ.get("INGREDIENT_WAP_KEY", "").strip())
    return base, key


# ── sqlite 캐시 ───────────────────────────────────────────────────────────────

def _ensure_cache() -> None:
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ingredient_wap_cache (
                cache_key  TEXT PRIMARY KEY,
                payload    TEXT NOT NULL,
                fetched_at TEXT NOT NULL
            )
            """
        )


def _cache_key(date: str, q: str | None, code: str | None) -> str:
    return f"{date}|q={q or ''}|code={code or ''}"


def _cache_read(key: str) -> dict | None:
    try:
        _ensure_cache()
        with sqlite3.connect(str(DB_PATH)) as conn:
            row = conn.execute(
                "SELECT payload, fetched_at FROM ingredient_wap_cache WHERE cache_key = ?",
                (key,),
            ).fetchone()
    except sqlite3.Error as e:
        logger.debug("WAP 캐시 read 실패: %s", e)
        return None
    if not row:
        return None
    try:
        fetched = datetime.fromisoformat(row[1])
    except (ValueError, TypeError):
        return None
    if (datetime.now() - fetched).days >= CACHE_TTL_DAYS:
        return None
    try:
        return json.loads(row[0])
    except json.JSONDecodeError:
        return None


def _cache_write(key: str, payload: dict) -> None:
    try:
        _ensure_cache()
        with sqlite3.connect(str(DB_PATH)) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO ingredient_wap_cache (cache_key, payload, fetched_at) "
                "VALUES (?, ?, ?)",
                (key, json.dumps(payload, ensure_ascii=False), datetime.now().isoformat()),
            )
    except sqlite3.Error as e:
        logger.debug("WAP 캐시 write 실패: %s", e)


# ── 조회 ──────────────────────────────────────────────────────────────────────

def lookup(date: str, q: str | None = None, code: str | None = None,
           use_cache: bool = True, timeout: int = 8) -> dict:
    """특정 시점 주성분 가중평균가 조회.

    Args:
        date: 'YYYY-MM-DD' (HIRA 반기로 매핑됨)
        q:    영문 INN 성분명 (substring 검색 → 다수 규격 반환)
        code: 주성분코드 (예 '689001BIJ', 단일 규격 — 재가격용)

    Returns:
        성공 {"available": True, "period": "...", "results": [{main_ingredient_code,
              ingredient_name, weighted_avg_price, match_mode}], "fallback_previous": bool}
        실패 {"available": False, "reason": "..."}
    """
    if not date or (not q and not code):
        return {"available": False, "reason": "date 와 q/code 중 하나 필수"}

    key = _cache_key(date, q, code)
    if use_cache:
        cached = _cache_read(key)
        if cached is not None:
            return cached

    base, api_key = _config()
    if not api_key:
        return {"available": False, "reason": "INGREDIENT_WAP_KEY 미설정 — 주성분 가중평균 비활성"}

    params = {"date": date}
    if q:
        params["q"] = q
    if code:
        params["code"] = code
    url = f"{base}/prices/as-of?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"X-API-Key": api_key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"available": False, "reason": f"WAP API HTTP {e.code}"}
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        return {"available": False, "reason": f"WAP API 도달 불가: {e}"}
    except (json.JSONDecodeError, ValueError) as e:
        return {"available": False, "reason": f"WAP API 응답 파싱 실패: {e}"}

    results = []
    for r in raw.get("results", []) or []:
        results.append({
            "main_ingredient_code": r.get("main_ingredient_code"),
            "ingredient_name": r.get("ingredient_name"),
            "weighted_avg_price": r.get("weighted_avg_price"),
            "match_mode": r.get("match_mode"),
            "period": r.get("period"),
        })
    out = {
        "available": True,
        "period": f"{raw.get('target_data_year', '')} {raw.get('target_period', '')}".strip(),
        "fallback_previous": bool(raw.get("fallback_previous")),
        "results": results,
    }
    _cache_write(key, out)
    return out
