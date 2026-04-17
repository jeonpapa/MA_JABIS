"""CLI — `python -m agents.foreign_approval <build|merge|matrix> ...`."""
from __future__ import annotations

import argparse
import json
import logging

from .agent import ForeignApprovalAgent


def main() -> None:
    ap = argparse.ArgumentParser(
        description="ForeignApprovalAgent — FDA/EMA 적응증 빌드 및 매트릭스 조회",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    bp = sub.add_parser("build", help="허가사항 수집·적재")
    bp.add_argument("drug", help="openFDA generic|brand 검색어 (예: pembrolizumab)")
    bp.add_argument("product_slug", help="DB product slug (예: keytruda)")
    bp.add_argument("--brand-slug", default=None, help="EMA EPAR URL slug (기본: product_slug)")
    bp.add_argument("--agencies", default="FDA,EMA,PMDA,MFDS,MHRA,TGA",
                    help="처리 기관 콤마 목록 (FDA, EMA, PMDA, MFDS, MHRA, TGA) — 기본 전부")
    bp.add_argument("--wipe", action="store_true",
                    help="product 의 기존 indication 데이터 전부 삭제 후 재빌드")
    bp.add_argument("--limit", type=int, default=None)
    bp.add_argument("--codes", default=None,
                    help="특정 ind code 콤마 목록 (예: 1.4_a,ema_5)")

    mgp = sub.add_parser("merge", help="fragmented masters 병합")
    mgp.add_argument("product_slug")
    mgp.add_argument("--dry-run", action="store_true", help="실제 병합 없이 미리보기")

    mp = sub.add_parser("matrix", help="커버리지 매트릭스 조회")
    mp.add_argument("product_slug")
    mp.add_argument("--format", choices=["json", "table"], default="table")

    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    agent = ForeignApprovalAgent()

    if args.cmd == "build":
        codes = [c for c in args.codes.split(",")] if args.codes else None
        summary = agent.build(
            drug=args.drug,
            product_slug=args.product_slug,
            brand_slug=args.brand_slug,
            agencies=tuple(a.strip().upper() for a in args.agencies.split(",")),
            wipe=args.wipe,
            limit=args.limit,
            codes=codes,
        )
        print(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2))
        for ar in summary.agencies:
            print(f"\n[{ar.agency}] ok={ar.ok} failed={ar.failed} "
                  f"matched={ar.matched} new={ar.new} elapsed={ar.elapsed:.1f}s")
            for e in ar.errors[:5]:
                print(f"  ERR: {e}")

    elif args.cmd == "merge":
        result = agent.merge(args.product_slug, dry_run=args.dry_run)
        mode = "DRY-RUN" if args.dry_run else "MERGED"
        print(f"\n[{mode}] {result['merged']}건 병합 대상")
        for d in result["details"]:
            print(f"  {d['src'][:30]:30} → {d['dst'][:30]:30}  "
                  f"({d['reason']}) src=[{','.join(d['src_agencies'])}]")

    elif args.cmd == "matrix":
        m = agent.matrix(args.product_slug)
        if args.format == "json":
            print(json.dumps(m, ensure_ascii=False, indent=2))
        else:
            t = m["totals"]
            print(f"\n=== {m['product']} 커버리지 ===")
            print(f"masters={t['masters']} | FDA={t['fda_agency']} "
                  f"EMA={t['ema_agency']} PMDA={t['pmda_agency']} "
                  f"MFDS={t['mfds_agency']} MHRA={t['mhra_agency']} "
                  f"TGA={t['tga_agency']} "
                  f"| both(FDA+EMA)={t['both']} all3={t['all_three']} "
                  f"all4={t['all_four']} all5={t['all_five']} "
                  f"all6={t['all_six']} "
                  f"| FDA-only={t['fda_only']} EMA-only={t['ema_only']} "
                  f"PMDA-only={t['pmda_only']} MFDS-only={t['mfds_only']} "
                  f"MHRA-only={t['mhra_only']} TGA-only={t['tga_only']}")
            print(f"\n=== Disease 별 ===")
            for d in m["by_disease"]:
                print(f"  {d['disease'][:14]:14} | masters={d['masters']:2} "
                      f"FDA={d['fda']:2} EMA={d['ema']:2} "
                      f"PMDA={d['pmda']:2} MFDS={d['mfds']:2} "
                      f"MHRA={d['mhra']:2} TGA={d['tga']:2}")


if __name__ == "__main__":
    main()
