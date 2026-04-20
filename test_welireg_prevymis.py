#!/usr/bin/env python3
"""
Test welireg & prevymis scrapers across all 7 countries.
验证 form_type 分类和价格检索完整性。

用法: python3 test_welireg_prevymis.py
"""

import asyncio
from pathlib import Path
from agents.foreign_price_agent import ForeignPriceAgent

async def main():
    print("=" * 80)
    print("TEST: Welireg & Prevymis across 7 countries")
    print("=" * 80)

    # Init agent with base directory
    base_dir = Path(__file__).resolve().parent
    agent = ForeignPriceAgent(base_dir)

    # Test both drugs
    for drug in ["welireg", "prevymis"]:
        print(f"\n{'=' * 80}")
        print(f"DRUG: {drug.upper()}")
        print("=" * 80)

        try:
            by_country = await agent.search_all(drug)
            total = sum(len(items) for items in by_country.values())
            print(f"\n✓ Total results: {total}")

            if not total:
                print(f"✗ No results found for {drug}")
                continue

            # Print summary
            for country in sorted(by_country.keys()):
                items = by_country[country]
                print(f"\n  {country}:")
                for item in items:
                    form_type = item.get("form_type", "unknown")
                    price = item.get("local_price")
                    dosage = item.get("dosage_strength", "")
                    print(f"    - {item.get('product_name')} | {dosage}")
                    print(f"      form_type={form_type} | {item.get('currency')} {price}")

            # Verify all countries represented
            expected_countries = {"JP", "IT", "FR", "CH", "UK", "DE", "CA"}
            found_countries = set(by_country.keys())
            missing = expected_countries - found_countries
            if missing:
                print(f"\n⚠ Missing countries: {missing}")
            else:
                print(f"\n✓ All 7 countries represented")

        except Exception as e:
            print(f"✗ Error testing {drug}: {e}")
            import traceback
            traceback.print_exc()

    print(f"\n{'=' * 80}")
    print("TEST COMPLETE")
    print("=" * 80)

if __name__ == "__main__":
    asyncio.run(main())
