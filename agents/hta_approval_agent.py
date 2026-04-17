"""HTA Approval Agent

4개 HTA 기관(PBAC/CADTH/NICE/SMC)의 평가 결과를 수집·캐싱·조회.
- SQLite 캐시 (data/db/drug_prices.db, hta_approvals 테이블)
- TTL 14일 (재평가 빈도 고려)
- 약제별 4개국 일괄 조회
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
DB_PATH  = BASE_DIR / "data" / "db" / "drug_prices.db"

# HTA 허가현황은 "과거 자료" — 한번 수집 후 영구 캐시.
# refresh=True 시에만 재수집하며, 이때 기존 데이터는 유지하고 신규만 추가.
CACHE_TTL_DAYS = None  # None = 영구

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS hta_approvals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    drug_query      TEXT NOT NULL,
    country         TEXT NOT NULL,
    body            TEXT NOT NULL,
    title           TEXT,
    indication      TEXT,
    decision        TEXT,
    decision_date   TEXT,
    detail_url      TEXT,
    pdf_url         TEXT,
    pdf_local       TEXT,
    extra           TEXT,
    fetched_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hta_drug_body
    ON hta_approvals(drug_query, body);
CREATE INDEX IF NOT EXISTS idx_hta_fetched
    ON hta_approvals(fetched_at);
"""


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def _ensure_schema() -> None:
    with _conn() as c:
        c.executescript(SCHEMA_SQL)


class HTAApprovalAgent:
    """FDA + 4개 HTA 기관 결과 통합 조회.

    파이프라인: scraper → QualityGuard 검증 → 캐시 저장
    """

    def __init__(self):
        _ensure_schema()
        self._guard = None
        try:
            from agents.quality_guard import QualityGuardAgent
            self._guard = QualityGuardAgent()
        except Exception:
            logger.warning("[HTA] QualityGuard 로드 실패 — 검증 없이 진행")
        # 동적 import (스크레이퍼 일부 미구현 가능)
        self._scrapers = {}
        for name, modpath, clsname in [
            ("FDA",   "agents.hta_scrapers.us_fda",           "USFDAScraper"),
            ("PBAC",  "agents.hta_scrapers.australia_pbac",  "AustraliaPBACScraper"),
            ("CADTH", "agents.hta_scrapers.canada_cadth",    "CanadaCADTHScraper"),
            ("NICE",  "agents.hta_scrapers.uk_nice",         "UKNICEScraper"),
            ("SMC",   "agents.hta_scrapers.scotland_smc",    "ScotlandSMCScraper"),
        ]:
            try:
                mod = __import__(modpath, fromlist=[clsname])
                self._scrapers[name] = getattr(mod, clsname)()
            except Exception as e:
                logger.warning("[HTA] %s 로드 실패: %s", name, e)

    def available_bodies(self) -> list[str]:
        return list(self._scrapers.keys())

    # ──────────────────────────────────────────────
    def get(self, drug: str, body: Optional[str] = None, force_refresh: bool = False) -> list[dict]:
        """약제별 결과 조회. body=None 이면 전체."""
        bodies = [body] if body else self.available_bodies()
        out: list[dict] = []
        for b in bodies:
            cached = self._read_cache(drug, b) if not force_refresh else []
            if cached:
                out.extend(cached)
                continue
            scraper = self._scrapers.get(b)
            if not scraper:
                continue
            try:
                results = scraper.search(drug)
                logger.info("[HTA] %s → %d건 수집", b, len(results))
            except Exception as e:
                logger.error("[HTA] %s 검색 실패: %s", b, e)
                if self._guard:
                    from agents.quality_guard import _write_deviation
                    _write_deviation({
                        "severity": "ERROR",
                        "agent": "HTAApprovalAgent",
                        "deviation_type": "hta_scraper_error",
                        "description": f"[{b}] {drug} 스크레이퍼 오류: {e}",
                        "corrective_action": "스크레이퍼 점검 필요",
                    })
                results = []
            self._write_cache(drug, b, results)
            cached_back = self._read_cache(drug, b)
            if cached_back:
                out.extend(cached_back)
            else:
                out.extend(r.to_dict() if hasattr(r, "to_dict") else r for r in results)
        return out

    # ──────────────────────────────────────────────
    def _read_cache(self, drug: str, body: str) -> list[dict]:
        with _conn() as c:
            rows = c.execute(
                "SELECT * FROM hta_approvals "
                "WHERE LOWER(drug_query)=LOWER(?) AND body=? "
                "ORDER BY decision_date DESC",
                (drug, body),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["extra"] = json.loads(d.get("extra") or "{}")
            except json.JSONDecodeError:
                d["extra"] = {}
            out.append(d)
        return out

    def _write_cache(self, drug: str, body: str, results: list) -> None:
        if not results:
            return
        now = datetime.now().isoformat()
        with _conn() as c:
            # 기존 데이터 삭제 후 최신 결과로 교체 (refresh 시)
            c.execute(
                "DELETE FROM hta_approvals WHERE LOWER(drug_query)=LOWER(?) AND body=?",
                (drug, body),
            )
            for r in results:
                d = self._normalize_for_cache(r, body)
                c.execute(
                    "INSERT INTO hta_approvals "
                    "(drug_query, country, body, title, indication, decision, "
                    " decision_date, detail_url, pdf_url, pdf_local, extra, fetched_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        drug, d.get("country"), d.get("body"),
                        d.get("title"), d.get("indication"), d.get("decision"),
                        d.get("decision_date"), d.get("detail_url"),
                        d.get("pdf_url"), d.get("pdf_local"),
                        json.dumps(d.get("extra") or {}, ensure_ascii=False),
                        now,
                    ),
                )

    @staticmethod
    def _normalize_for_cache(r, body: str) -> dict:
        """FDARecord 는 평면 스키마와 모양이 달라 어댑팅. 다른 record 는 그대로."""
        d = r.to_dict() if hasattr(r, "to_dict") else r
        if body != "FDA":
            return d
        # FDA: 단일 라벨을 한 행으로 저장. indications 는 extra 에.
        brand = (d.get("brand_names") or [""])[0]
        return {
            "country":       "US",
            "body":          "FDA",
            "title":         brand or (d.get("generic_names") or [""])[0],
            "indication":    "; ".join(i["label"] for i in d.get("indications", [])),
            "decision":      "Approved",
            "decision_date": d.get("effective_time"),
            "detail_url":    d.get("label_url"),
            "pdf_url":       None,
            "pdf_local":     None,
            "extra": {
                "brand_names":   d.get("brand_names"),
                "generic_names": d.get("generic_names"),
                "manufacturer":  d.get("manufacturer"),
                "indications":   d.get("indications"),
                "raw_indication": d.get("raw_indication"),
            },
        }

    # ──────────────────────────────────────────────
    def get_indication_matrix(self, drug: str, force_refresh: bool = False) -> dict:
        """FDA 적응증을 축으로 PBAC/CADTH/NICE/SMC 결과를 매칭한 매트릭스.

        Returns:
            {
              "drug": str,
              "fda": { "brand": str, "generic": str, "manufacturer": str,
                       "effective_time": str, "label_url": str },
              "indications": [
                  { "code": "1.1", "label": str, "body": str, "keywords": [...],
                    "by_country": { "PBAC": [...], "CADTH": [...],
                                    "NICE":  [...], "SMC":   [...] } },
                  ...
              ],
              "unmatched": { "PBAC": [...], ... }   # 적응증 매칭 실패
            }
        """
        # 1) FDA — 구조화된 적응증
        fda_rows = self.get(drug, body="FDA", force_refresh=force_refresh)
        fda_meta: dict = {}
        fda_indications: list[dict] = []
        if fda_rows:
            row = fda_rows[0]  # 보통 하나의 라벨
            extra = row.get("extra") or {}
            fda_meta = {
                "brand":          (extra.get("brand_names") or [""])[0],
                "generic":        (extra.get("generic_names") or [""])[0],
                "manufacturer":   extra.get("manufacturer"),
                "effective_time": row.get("decision_date"),
                "label_url":      row.get("detail_url"),
            }
            fda_indications = extra.get("indications") or []

        # 2) 타국 결과
        per_body: dict[str, list[dict]] = {}
        for body in ["PBAC", "CADTH", "NICE", "SMC"]:
            if body in self._scrapers:
                per_body[body] = self.get(drug, body=body, force_refresh=force_refresh)

        # 3) 매칭
        matrix: list[dict] = []
        unmatched: dict[str, list[dict]] = {b: [] for b in per_body}
        for ind in fda_indications:
            matrix.append({
                **ind,
                "by_country": {b: [] for b in per_body},
            })

        if not fda_indications:
            # FDA 결과 없음 → 모두 unmatched
            for b, rows in per_body.items():
                unmatched[b] = rows
        else:
            for body, rows in per_body.items():
                for r in rows:
                    idx = self._match_indication(r, fda_indications)
                    if idx is None:
                        unmatched[body].append(r)
                    else:
                        matrix[idx]["by_country"][body].append(r)

        return {
            "drug":        drug,
            "fda":         fda_meta,
            "indications": matrix,
            "unmatched":   unmatched,
        }

    @staticmethod
    def _match_indication(record: dict, fda_indications: list[dict]) -> Optional[int]:
        """record(타국 평가) 를 FDA 적응증 목록에서 가장 잘 맞는 인덱스에 매칭.

        Strategy: keyword 중첩 점수. tie/0점 시 None.
        """
        text = " ".join([
            record.get("title") or "",
            record.get("indication") or "",
            json.dumps(record.get("extra") or {}, ensure_ascii=False),
        ]).upper()
        if not text.strip():
            return None
        scores: list[int] = []
        for ind in fda_indications:
            score = 0
            for kw in ind.get("keywords") or []:
                kw_u = kw.upper()
                if len(kw_u) < 3:
                    continue
                if re.search(rf"\b{re.escape(kw_u)}\b", text):
                    # 약어(전부 대문자, 길이 ≥3) 가중
                    score += 3 if kw.isupper() else 1
            scores.append(score)
        best = max(scores) if scores else 0
        if best == 0:
            return None
        # 동점 처리: 첫 번째 우선 (FDA 라벨 순서)
        return scores.index(best)


# CLI
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    drug = sys.argv[1] if len(sys.argv) > 1 else "belzutifan"
    agent = HTAApprovalAgent()
    print(json.dumps(agent.get(drug), ensure_ascii=False, indent=2))
