"""
UsMicromedexScraper end-to-end 테스트.

목적:
  - BaseScraper 경유 run() 호출로 login → search → logout 보장 확인
  - 4중 안전장치 동작 검증 (Ctrl+C 해도 logout 됨)
  - 첫 실행 후 data/foreign/us/debug_*.html 에서 결과 구조 파악

Usage:
  python scripts/probe_micromedex_redbook.py [query]
  python scripts/probe_micromedex_redbook.py Keytruda
"""
import asyncio
import logging
import sys
from pathlib import Path

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

from agents.scrapers.base import load_credentials  # noqa: E402
from agents.scrapers.us_micromedex import UsMicromedexScraper  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


async def main(query: str):
    creds = load_credentials(BASE / "config" / "foreign_credentials.json", "US")
    if not creds.get("username"):
        print("[probe] MICROMEDEX_US_USERNAME/PASSWORD 가 설정되지 않았습니다", file=sys.stderr)
        sys.exit(1)

    scraper = UsMicromedexScraper(
        credentials=creds,
        cache_dir=BASE / "data" / "foreign" / "us",
        headless=False,  # 디버깅용 가시화. 운영에서는 True.
    )

    print(f"[probe] UsMicromedexScraper.run('{query}') 호출")
    try:
        results = await scraper.run(query)
    except RuntimeError as e:
        # 라이선스 한도 등
        print(f"[probe] RuntimeError: {e}", file=sys.stderr)
        sys.exit(2)

    print(f"[probe] 결과 {len(results)}건:")
    for i, r in enumerate(results[:5]):
        print(
            f"  [{i}] {r.get('product_name')!r} "
            f"${r.get('local_price')} | "
            f"{r.get('dosage_strength','')} | {r.get('form_type','')}"
        )
    if len(results) > 5:
        print(f"  ... (+{len(results)-5}건)")

    dbg = BASE / "data" / "foreign" / "us"
    hits = list(dbg.glob("debug_*.html"))
    if hits:
        print(f"[probe] 디버그 HTML: {hits[-1]}")


if __name__ == "__main__":
    query = sys.argv[1] if len(sys.argv) > 1 else "Keytruda"
    asyncio.run(main(query))
