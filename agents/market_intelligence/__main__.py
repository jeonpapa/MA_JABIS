"""CLI entrypoint — `python -m agents.market_intelligence [leaderboard | <drug> <ing> <date> <delta>]`."""
from __future__ import annotations

import json
import logging
import sys

from .agent import MarketIntelligenceAgent


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    agent = MarketIntelligenceAgent()

    if len(sys.argv) > 1 and sys.argv[1] == "leaderboard":
        board = agent.get_media_leaderboard()
        rows = board["leaderboard"] if isinstance(board, dict) else board
        print(f"\n마지막 캘리브레이션: {board.get('last_calibrated','미보정') if isinstance(board, dict) else '미보정'}")
        print(f"\n{'매체명':<28} {'가중치':>5}  Tier  V  N  MA  설명")
        print("-" * 90)
        for r in rows:
            print(
                f"{r['media']:<28} {r['weight']:>5.1f}  {r['tier']:>4}  "
                f"{r['volume']}  {r['novelty']}  {r['ma_depth']:>2}  {r['desc'][:40]}"
            )
        sys.exit(0)

    drug    = sys.argv[1] if len(sys.argv) > 1 else "키트루다주"
    ing     = sys.argv[2] if len(sys.argv) > 2 else "펨브롤리주맙,유전자재조합"
    date    = sys.argv[3] if len(sys.argv) > 3 else "2022.03.01"
    delta   = float(sys.argv[4]) if len(sys.argv) > 4 else -25.61

    result = agent.analyze_price_change(
        drug_ko=drug, ingredient_ko=ing,
        change_date=date, delta_pct=delta,
        force_refresh=True,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
