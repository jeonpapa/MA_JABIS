#!/usr/bin/env python3
"""HIRA press-release extractor.

Fetches the HIRA press-release list and detail pages directly from
https://www.hira.or.kr/bbsDummy.do?pgmid=HIRAA020041000100 .

Outputs JSON by default and stores raw/clean evidence files under
/opt/data/hira_pipeline/evidence/raw/hira.

Examples:
  python hira_press_extractor.py list --limit 10
  python hira_press_extractor.py detail --brdBltNo 11814
  python hira_press_extractor.py find --query '약제급여평가위원회 심의결과' --limit 5
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable

BASE_URL = "https://www.hira.or.kr/bbsDummy.do?pgmid=HIRAA020041000100"
DEFAULT_OUT_DIR = Path("/opt/data/hira_pipeline/evidence/raw/hira")
USER_AGENT = "Mozilla/5.0 (Hermes HIRA Access Analyst; contact: routine-service)"


@dataclass
class PressItem:
    brdBltNo: str
    title: str
    href: str
    detail_url: str


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        s = data.strip()
        if s:
            self.parts.append(s)


def fetch(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return raw.decode("utf-8", "ignore")


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def clean_text_from_html(doc: str) -> str:
    parser = TextExtractor()
    parser.feed(doc)
    lines: list[str] = []
    for line in html.unescape("\n".join(parser.parts)).splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def parse_list(doc: str) -> list[PressItem]:
    items: list[PressItem] = []
    for m in re.finditer(r'<td class="col-tit"><a href="([^"]+)">(.*?)</a>', doc, re.S | re.I):
        href = html.unescape(m.group(1))
        title = html.unescape(re.sub(r"<.*?>", " ", m.group(2))).strip()
        bno = re.search(r"brdBltNo=(\d+)", href)
        if not bno:
            continue
        brd_blt_no = bno.group(1)
        detail_url = detail_url_for(brd_blt_no)
        items.append(PressItem(brd_blt_no, title, href, detail_url))
    return items


def detail_url_for(brd_blt_no: str) -> str:
    params = {
        "pgmid": "HIRAA020041000100",
        "brdScnBltNo": "4",
        "brdBltNo": brd_blt_no,
        "pageIndex": "1",
        "pageIndex2": "1",
    }
    return "https://www.hira.or.kr/bbsDummy.do?" + urllib.parse.urlencode(params)


def save_text(path: Path, text: str) -> None:
    ensure_dir(path.parent)
    path.write_text(text, encoding="utf-8")


def cmd_list(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    ensure_dir(out_dir)
    doc = fetch(BASE_URL)
    save_text(out_dir / "press_list_latest.html", doc)
    items = parse_list(doc)
    result = {
        "status": "ok",
        "source_url": BASE_URL,
        "fetched_at_epoch": int(time.time()),
        "raw_path": str(out_dir / "press_list_latest.html"),
        "count": len(items),
        "items": [asdict(x) for x in items[: args.limit]],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_find(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    ensure_dir(out_dir)
    doc = fetch(BASE_URL)
    save_text(out_dir / "press_list_latest.html", doc)
    items = parse_list(doc)
    query = args.query.strip()
    filtered = [x for x in items if query in x.title]
    result = {
        "status": "ok",
        "source_url": BASE_URL,
        "query": query,
        "count": len(filtered),
        "items": [asdict(x) for x in filtered[: args.limit]],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_detail(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    ensure_dir(out_dir)
    brd_blt_no = str(args.brdBltNo)
    url = detail_url_for(brd_blt_no)
    doc = fetch(url)
    clean = clean_text_from_html(doc)
    raw_path = out_dir / f"brdBltNo_{brd_blt_no}_detail.html"
    clean_path = out_dir / f"brdBltNo_{brd_blt_no}_clean_text.txt"
    save_text(raw_path, doc)
    save_text(clean_path, clean)

    keywords = args.keywords or []
    keyword_counts = {k: clean.count(k) for k in keywords}
    result = {
        "status": "ok",
        "brdBltNo": brd_blt_no,
        "detail_url": url,
        "raw_path": str(raw_path),
        "clean_text_path": str(clean_path),
        "html_bytes": len(doc.encode("utf-8")),
        "clean_chars": len(clean),
        "keyword_counts": keyword_counts,
        "title_hits": [line for line in clean.splitlines() if "심의결과 공개" in line][:5],
        "snippet": make_result_snippet(clean),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def make_result_snippet(clean: str) -> str:
    markers = [
        "결정신청 약제의 요양급여 적정성 심의결과",
        "위험분담계약 약제의 사용범위 확대 적정성",
        "중증질환심의위원회",
        "심의결과",
    ]
    for marker in markers:
        idx = clean.find(marker)
        if idx >= 0:
            return clean[max(0, idx - 200) : idx + 2200]
    return clean[:2200]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Extract HIRA press releases")
    p.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    sub = p.add_subparsers(dest="command", required=True)

    lp = sub.add_parser("list", help="Fetch and parse recent press-release list")
    lp.add_argument("--limit", type=int, default=10)
    lp.set_defaults(func=cmd_list)

    fp = sub.add_parser("find", help="Find recent press releases by title substring")
    fp.add_argument("--query", required=True)
    fp.add_argument("--limit", type=int, default=10)
    fp.set_defaults(func=cmd_find)

    dp = sub.add_parser("detail", help="Fetch and extract a press-release detail page")
    dp.add_argument("--brdBltNo", required=True)
    dp.add_argument("--keywords", nargs="*", default=[])
    dp.set_defaults(func=cmd_detail)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # noqa: BLE001 - CLI should surface failures as JSON
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
