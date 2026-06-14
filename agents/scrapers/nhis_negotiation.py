"""건강보험공단(NHIS) 약가협상 공개자료 크롤러 — 공단협상 단계 1차 권위 소스.

페이지(Spring MVC `.do` 서버렌더, plain requests POST 가능 — CSRF/Playwright 불필요):
  - 신약        https://www.nhis.or.kr/nhis/together/retrieveMediList.do  → list_type='신규'
  - 사용범위확대 https://www.nhis.or.kr/nhis/together/retrieveMediList2.do → list_type='확대'

컬럼(7): 번호·제품명·제약사명·효능군·등록연월·협상결과·협상완료연월.
  - 협상완료연월 '-'/'' = 협상중, 채워짐 = 완료.
  - 연월 'YYYY.MM.' → 'YYYY-MM' 정규화.

조회 전용 모듈 (DB 적재는 agents/ingest/nhis_negotiation_import.py 가 담당).
- fetch_list(list_type): 단일 목록 전 페이지 수집
- fetch_all(): 신규+확대 통합
- 각 행 content_hash 부여(멱등 UPSERT 키)

실행: python -m agents.scrapers.nhis_negotiation
"""
from __future__ import annotations

import hashlib
import logging
import re
import time

import requests
from lxml import html as lhtml

logger = logging.getLogger(__name__)

URLS = {
    "신규": "https://www.nhis.or.kr/nhis/together/retrieveMediList.do",
    "확대": "https://www.nhis.or.kr/nhis/together/retrieveMediList2.do",
}

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

_YM_RE = re.compile(r"(\d{4})[.\-/]\s*(\d{1,2})")
_EMPTY = {"", "-", "−", "–", None}

_MAX_PAGES = 30


def _norm_ym(raw: str | None) -> str | None:
    """'2026.04.' / '2026.4' → '2026-04'. '-'/'' → None."""
    if raw is None:
        return None
    s = raw.strip()
    if s in _EMPTY:
        return None
    m = _YM_RE.search(s)
    if not m:
        return None
    return f"{m.group(1)}-{int(m.group(2)):02d}"


def _clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _row_hash(list_type: str, product: str, manufacturer: str,
              registered_ym: str | None, result: str, completed_ym: str | None) -> str:
    key = "|".join([
        list_type, product, manufacturer,
        registered_ym or "", result, completed_ym or "",
    ])
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _parse_page(text: str) -> list[dict]:
    """단일 페이지 HTML → 원시 행 dict 목록 (7셀 이상만)."""
    doc = lhtml.fromstring(text)
    rows: list[dict] = []
    for tr in doc.xpath("//table//tbody//tr"):
        tds = [td.text_content().strip() for td in tr.xpath("./td")]
        if len(tds) >= 7:
            rows.append({
                "no": tds[0], "product": tds[1], "manufacturer": tds[2],
                "efficacy_group": tds[3], "registered_ym": tds[4],
                "result": tds[5], "completed_ym": tds[6],
            })
    return rows


def fetch_list(list_type: str, *, session: requests.Session | None = None,
               pause: float = 0.4) -> list[dict]:
    """단일 목록(신규/확대)의 전 페이지를 수집 → 정규화 행 목록.

    각 행: list_type, product_name, manufacturer, efficacy_group,
           registered_ym, result, completed_ym, source_url, content_hash, is_in_progress.
    """
    if list_type not in URLS:
        raise ValueError(f"list_type 은 {set(URLS)} 중 하나여야 함: {list_type!r}")
    url = URLS[list_type]
    sess = session or requests.Session()
    out: list[dict] = []
    seen_first = None
    for pidx in range(1, _MAX_PAGES + 1):
        try:
            r = sess.post(url, data={
                "seqNo": "", "searchCondition": "PRDCT_NM",
                "searchKeyword": "", "pageIndex": str(pidx),
            }, headers={"User-Agent": _UA}, timeout=20)
            r.raise_for_status()
        except Exception as e:
            logger.warning("[nhis] %s page %d 요청 실패: %s", list_type, pidx, e)
            break
        raw = _parse_page(r.text)
        if not raw:
            break
        # 페이지 초과 시 동일 첫 행 반복 → 중단 (가드)
        if raw[0]["no"] == seen_first:
            break
        seen_first = raw[0]["no"]

        for row in raw:
            product = _clean(row["product"])
            manufacturer = _clean(row["manufacturer"])
            result = _clean(row["result"])
            registered_ym = _norm_ym(row["registered_ym"])
            completed_ym = _norm_ym(row["completed_ym"])
            if not product:
                continue
            out.append({
                "list_type": list_type,
                "product_name": product,
                "manufacturer": manufacturer,
                "efficacy_group": _clean(row["efficacy_group"]),
                "registered_ym": registered_ym,
                "result": result,
                "completed_ym": completed_ym,
                "is_in_progress": completed_ym is None,
                "source_url": url,
                "content_hash": _row_hash(list_type, product, manufacturer,
                                          registered_ym, result, completed_ym),
            })
        if len(raw) < 10:
            break
        if pause:
            time.sleep(pause)
    logger.info("[nhis] %s 수집 %d행", list_type, len(out))
    return out


def fetch_all() -> list[dict]:
    """신규 + 확대 통합 수집."""
    sess = requests.Session()
    rows: list[dict] = []
    for lt in URLS:
        rows.extend(fetch_list(lt, session=sess))
    return rows


def _summary(rows: list[dict]) -> dict:
    inprog = [r for r in rows if r["is_in_progress"]]
    done = [r for r in rows if not r["is_in_progress"]]
    by_type: dict[str, int] = {}
    for r in rows:
        by_type[r["list_type"]] = by_type.get(r["list_type"], 0) + 1
    return {
        "total": len(rows),
        "in_progress": len(inprog),
        "completed": len(done),
        "by_type": by_type,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    all_rows = fetch_all()
    s = _summary(all_rows)
    print(f"\n총 {s['total']}행  |  협상중 {s['in_progress']}  /  완료 {s['completed']}")
    print(f"유형별: {s['by_type']}")
    kt = [r for r in all_rows if "키트루다" in r["product_name"]]
    for r in kt:
        flag = "협상중" if r["is_in_progress"] else f"완료({r['completed_ym']})"
        print(f"  ★키트루다 [{r['list_type']}] {r['product_name'][:36]} | "
              f"{r['manufacturer']} | 등록{r['registered_ym']} | {r['result']} | {flag}")
