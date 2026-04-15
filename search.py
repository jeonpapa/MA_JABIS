"""
약가 검색 CLI 도구
사용법:
  python search.py "아스피린"                  # 제품명/성분명 검색
  python search.py --code 647902860           # 보험코드 검색
  python search.py --history 647902860        # 특정 코드의 전체 가격 이력
  python search.py --stats                    # DB 현황
"""

import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "data" / "db" / "drug_prices.db"


def load_config() -> dict:
    with open(BASE_DIR / "config" / "settings.json", encoding="utf-8") as f:
        return json.load(f)


def fmt_price(p) -> str:
    if p is None:
        return "-"
    try:
        return f"{int(p):,}원"
    except (ValueError, TypeError):
        return str(p)


def search_cmd(keyword: str, limit: int = 30):
    from agents.db import DrugPriceDB
    db = DrugPriceDB(DB_PATH)
    results = db.search_drug(keyword, limit=limit)

    if not results:
        print(f"검색 결과 없음: '{keyword}'")
        return

    # 최신 날짜 기준으로 중복 코드 제거 (검색 결과 정리)
    seen = {}
    for r in results:
        code = r["insurance_code"]
        if code not in seen or r["apply_date"] > seen[code]["apply_date"]:
            seen[code] = r
    unique = list(seen.values())

    print(f"\n[검색 결과] '{keyword}' — {len(unique)}건 (기준일: 최신)")
    print("-" * 100)
    print(f"{'보험코드':<15} {'제품명':<30} {'업체명':<20} {'성분명':<20} {'상한금액':>12} {'기준일'}")
    print("-" * 100)
    for r in unique:
        print(
            f"{r['insurance_code']:<15} "
            f"{(r['product_name_kr'] or ''):<30} "
            f"{(r['company'] or ''):<20} "
            f"{(r['ingredient'] or ''):<20} "
            f"{fmt_price(r['max_price']):>12}  "
            f"{r['apply_date']}"
        )
    print()
    print("💡 가격 이력 조회: python search.py --history <보험코드>")


def history_cmd(insurance_code: str):
    from agents.db import DrugPriceDB
    db = DrugPriceDB(DB_PATH)
    history = db.get_price_history(insurance_code)

    if not history:
        print(f"이력 없음: 보험코드 '{insurance_code}'")
        return

    first = history[0]
    print(f"\n[가격 이력] {first.get('product_name_kr', '')} ({insurance_code})")
    print(f"  업체명: {first.get('company', '')}  |  성분명: {first.get('ingredient', '')}")
    print(f"  함량: {first.get('dosage_strength', '')}  |  기간: {history[0]['apply_date']} ~ {history[-1]['apply_date']}")
    print("-" * 60)
    print(f"{'적용일':<15} {'상한금액':>12}  {'전월 대비':>12}  {'비고'}")
    print("-" * 60)

    prev_price = None
    for r in history:
        cur_price = r["max_price"]
        if prev_price is not None and cur_price is not None and prev_price != 0:
            try:
                rate = (cur_price - prev_price) / prev_price * 100
                diff = f"{rate:+.2f}%"
                marker = " ▲" if rate > 0 else (" ▼" if rate < 0 else "  =")
            except ZeroDivisionError:
                diff = "N/A"
                marker = ""
        else:
            diff = "-"
            marker = ""

        print(
            f"{r['apply_date']:<15} "
            f"{fmt_price(cur_price):>12}  "
            f"{diff:>12}{marker}  "
            f"{r.get('remark', '') or ''}"
        )
        prev_price = cur_price
    print()


def stats_cmd():
    from agents.db import DrugPriceDB
    db = DrugPriceDB(DB_PATH)
    stats = db.get_stats()

    print("\n[약가 데이터베이스 현황]")
    print("-" * 40)
    print(f"  누적 레코드:   {stats['total_records']:>12,}건")
    print(f"  월별 데이터:   {stats['total_dates']:>12}개월")
    print(f"  최초 기준일:   {stats['oldest_date']:>12}")
    print(f"  최신 기준일:   {stats['latest_date']:>12}")
    print(f"  다운로드 파일: {stats['downloaded_files']:>12}")
    print()

    dates = db.get_available_dates()
    if dates:
        print("  수록 기준일 목록:")
        for i, d in enumerate(dates):
            print(f"    {d}", end="  ")
            if (i + 1) % 6 == 0:
                print()
        print()


def main():
    if not DB_PATH.exists():
        print("⚠️  DB가 없습니다. 먼저 백필을 실행하세요:")
        print("   python -m agents.backfill_agent")
        sys.exit(1)

    parser = argparse.ArgumentParser(
        description="약가 검색 CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python search.py "아스피린"
  python search.py "트라스투주맙"
  python search.py --code 647902860
  python search.py --history 647902860
  python search.py --stats
        """
    )
    parser.add_argument("keyword", nargs="?", help="검색어 (제품명 / 성분명)")
    parser.add_argument("--code", help="보험코드로 최신 정보 조회")
    parser.add_argument("--history", help="보험코드의 전체 가격 이력 조회")
    parser.add_argument("--stats", action="store_true", help="DB 현황 출력")
    parser.add_argument("--limit", type=int, default=30, help="검색 결과 최대 수 (기본: 30)")

    args = parser.parse_args()

    if args.stats:
        stats_cmd()
    elif args.history:
        history_cmd(args.history)
    elif args.code:
        search_cmd(args.code, limit=args.limit)
    elif args.keyword:
        search_cmd(args.keyword, limit=args.limit)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
