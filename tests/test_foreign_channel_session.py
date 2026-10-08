"""다채널 국가(IT CSV+TNF, FR BDPM+Vidal)의 한 검색 세션은 하나의 searched_at 이다 (P1.6 수집 가능성).

감사가 본 결함: 채널마다 자기 run() 에서 `datetime.now()` 를 찍어(실제 DB: sitagliptin IT CSV 03:02:57.080 vs TNF
03:02:57.158 — 80ms 차) `compact_foreign_prices` 가 두 세션으로 보고 **먼저 찍힌 채널을 지웠다**(keytruda IT 의 CSV 행이
사라지고 TNF 만 남음). `get_foreign_prices` 도 국가당 MAX(searched_at) 1세션만 노출해 카드에 한 채널만 보였다.

수정(작은 쪽): 채널이 아니라 **에이전트(search_one_country)가 국가 검색 1회당 세션 타임스탬프를 한 번 찍어** 모든 채널
행에 같은 값을 쓴다. 압축·조회 SQL 은 그대로. 네트워크 금지 — 스크레이퍼는 가짜, DB 는 임시 파일.

실행: .venv/bin/python -m pytest tests/test_foreign_channel_session.py -q
"""
from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents import foreign_price_agent as fpa
from agents.db import DrugPriceDB

CSV_LABEL = "AIFA Lista Classe H (Prezzo Ex-factory)"
TNF_LABEL = "AIFA TrovaNorme (Determina prezzo, ex-factory)"
BDPM_LABEL = "BDPM/ANSM (프랑스 공개 약품 DB)"
VIDAL_LABEL = "VIDAL.fr"


def _rec(**kw) -> dict:
    base = {"searched_at": "2026-07-27T03:02:57.080401", "query_name": "keytruda", "country": "IT",
            "product_name": "KEYTRUDA*1 flaconcino infus EV 4 ml 25 mg/ml", "ingredient": "Pembrolizumab",
            "dosage_strength": "KEYTRUDA*1 flaconcino infus EV 4 ml 25 mg/ml", "dosage_form": "USO PARENTERALE",
            "package_unit": "", "local_price": 3428.0, "currency": "EUR", "exchange_rate": None, "exchange_rate_from": None,
            "exchange_rate_to": None, "factory_price_krw": None, "vat_rate": None, "distribution_margin": None,
            "adjusted_price_krw": None, "source_url": "", "source_label": CSV_LABEL, "raw_data": "{}", "form_type": "injection"}
    return {**base, **kw}


# ── DB 수준: 왜 타임스탬프가 하나여야 하는가 ───────────────────────────────────────────────────────────────

def test_compaction_deletes_the_earlier_channel_when_timestamps_differ(tmp_path):
    db = DrugPriceDB(tmp_path / "f.db")
    db.save_foreign_price(_rec(searched_at="2026-07-27T03:02:57.080401", source_label=CSV_LABEL))
    db.save_foreign_price(_rec(searched_at="2026-07-27T03:02:57.158115", source_label=TNF_LABEL, product_name="KEYTRUDA",
                               dosage_strength="25 mg/ml ... 1 flaconcino", local_price=3798.34))
    assert db.compact_foreign_prices("keytruda") == 1                         # 80ms 차이가 '다른 세션' — CSV 행이 지워진다
    assert [r["source_label"] for r in db.get_foreign_prices("keytruda")] == [TNF_LABEL]


def test_compaction_keeps_both_channels_when_they_share_the_session_timestamp(tmp_path):
    db = DrugPriceDB(tmp_path / "f.db")
    ts = "2026-07-27T03:02:57.080401"
    db.save_foreign_price(_rec(searched_at=ts, source_label=CSV_LABEL))
    db.save_foreign_price(_rec(searched_at=ts, source_label=TNF_LABEL, product_name="KEYTRUDA",
                               dosage_strength="25 mg/ml ... 1 flaconcino", local_price=3798.34))
    assert db.compact_foreign_prices("keytruda") == 0
    assert sorted(r["source_label"] for r in db.get_foreign_prices("keytruda")) == sorted([CSV_LABEL, TNF_LABEL])


# ── 에이전트 수준: 채널이 제각각 찍어도 저장 행은 한 세션 ───────────────────────────────────────────────────

class _Chan:
    """채널 스크레이퍼 가짜 — run() 이 자기 타임스탬프를 찍은 레코드를 돌려준다(현재 BaseScraper/TNF/Vidal 과 같은 행동)."""
    FALLBACK_TO_INN = False

    def __init__(self, country, label, ts, rows, **kw):
        self.COUNTRY, self.label, self.ts, self.rows = country, label, ts, rows
        self.warnings = []

    def _resolve_form_type(self, item):
        return item.get("form_type") or "unknown"

    async def run(self, term):
        return [_rec(country=self.COUNTRY, source_label=self.label, searched_at=self.ts, **r) for r in self.rows]


def _agent(monkeypatch, tmp_path, primary, secondary_attr, secondary):
    (tmp_path / "data" / "db").mkdir(parents=True)
    agent = fpa.ForeignPriceAgent(tmp_path)
    monkeypatch.setattr(agent, "_build_scraper", lambda c: primary)
    monkeypatch.setattr(fpa, secondary_attr, lambda **kw: secondary)
    monkeypatch.setattr(agent.rate_fetcher, "get_36m_average", lambda *a, **k: None)
    return agent


def _rows(agent, country):
    with sqlite3.connect(agent.db.db_path) as c:
        return c.execute("SELECT searched_at, source_label, local_price FROM foreign_drug_prices WHERE country=? ORDER BY id",
                         (country,)).fetchall()


def test_it_csv_and_tnf_rows_share_one_session_timestamp_and_survive_compaction(monkeypatch, tmp_path):
    csv = _Chan("IT", CSV_LABEL, "2026-07-27T03:02:57.080401", [{}])
    tnf = _Chan("IT", TNF_LABEL, "2026-07-27T03:02:57.158115", [{"product_name": "KEYTRUDA", "local_price": 3798.34,
                                                                  "dosage_strength": "25 mg/ml ... 1 flaconcino"}])
    agent = _agent(monkeypatch, tmp_path, csv, "ItAifaTnfScraper", tnf)
    saved = asyncio.run(agent.search_one_country("keytruda", "IT"))
    assert len(saved) == 2
    rows = _rows(agent, "IT")
    assert len({r[0] for r in rows}) == 1, rows                          # 한 세션 = 한 searched_at
    assert rows[0][0] not in ("2026-07-27T03:02:57.080401", "2026-07-27T03:02:57.158115")   # 채널 타임스탬프가 아니라 세션 것
    assert agent.db.compact_foreign_prices("keytruda") == 0
    assert sorted(r["source_label"] for r in agent.db.get_foreign_prices("keytruda")) == sorted([CSV_LABEL, TNF_LABEL])


def test_fr_bdpm_and_vidal_rows_share_one_session_timestamp(monkeypatch, tmp_path):
    bdpm = _Chan("FR", BDPM_LABEL, "2026-07-28T10:05:00.299099", [{"local_price": None, "currency": "EUR"}])
    vidal = _Chan("FR", VIDAL_LABEL, "2026-07-28T10:05:00.447611", [{"local_price": 2000.74, "currency": "EUR",
                                                                     "product_name": "KEYTRUDA 25 mg/ml sol diluer p perf"}])
    agent = _agent(monkeypatch, tmp_path, bdpm, "FrVidalScraper", vidal)
    asyncio.run(agent.search_one_country("keytruda", "FR"))
    rows = _rows(agent, "FR")
    assert len(rows) == 2 and len({r[0] for r in rows}) == 1, rows
    assert agent.db.compact_foreign_prices("keytruda") == 0
    assert sorted(r["source_label"] for r in agent.db.get_foreign_prices("keytruda")) == sorted([BDPM_LABEL, VIDAL_LABEL])


def test_two_searches_are_two_sessions_and_compaction_keeps_the_latest_only(monkeypatch, tmp_path):
    csv = _Chan("IT", CSV_LABEL, "x", [{}])
    tnf = _Chan("IT", TNF_LABEL, "y", [{"product_name": "KEYTRUDA", "local_price": 3798.34}])
    agent = _agent(monkeypatch, tmp_path, csv, "ItAifaTnfScraper", tnf)
    asyncio.run(agent.search_one_country("keytruda", "IT"))
    asyncio.run(agent.search_one_country("keytruda", "IT"))
    assert len({r[0] for r in _rows(agent, "IT")}) == 2                     # 검색 2회 = 세션 2개
    assert agent.db.compact_foreign_prices("keytruda") == 2                  # 과거 세션(2행) 정리, 최신 세션 2채널 유지
    assert sorted(r["source_label"] for r in agent.db.get_foreign_prices("keytruda")) == sorted([CSV_LABEL, TNF_LABEL])
