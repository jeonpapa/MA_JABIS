from __future__ import annotations
import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.foreign_price_agent import ForeignPriceAgent


class _StubScraper:
    COUNTRY = "US"; CURRENCY = "USD"; SOURCE_TYPE = None
    FALLBACK_TO_INN = True
    def __init__(self): self.calls = []
    def _resolve_form_type(self, item): return "injection"
    async def run(self, term):
        self.calls.append(term)
        # brand("keytruda") 는 0건, INN("pembrolizumab") 은 1건 → 폴백 검증
        if term == "pembrolizumab":
            return [{"query_name": term, "country": "US", "local_price": 100.0,
                     "ingredient": "pembrolizumab", "form_type": "injection"}]
        return []


def test_brand_term_then_inn_fallback(monkeypatch, tmp_path):
    (tmp_path / "data" / "db").mkdir(parents=True)
    agent = ForeignPriceAgent(tmp_path)
    stub = _StubScraper()
    monkeypatch.setattr(agent, "_build_scraper", lambda c: stub)
    monkeypatch.setattr(agent.rate_fetcher, "get_36m_average", lambda *a, **k: None)
    monkeypatch.setattr(agent.db, "save_foreign_price", lambda item: 1)
    identity = {"slug": "keytruda", "brand": "keytruda", "inn": "pembrolizumab"}
    rows = asyncio.run(agent.search_one_country("keytruda", "US", identity=identity))
    # brand 먼저, 0건이라 INN 폴백
    assert stub.calls == ["keytruda", "pembrolizumab"]
    # 저장 query_name 은 canonical slug 로 통일
    assert rows and rows[0]["query_name"] == "keytruda"
