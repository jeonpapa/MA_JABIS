"""
식약처 의약품 제품 허가정보 (data.go.kr — DrugPrdtPrmsnInfoService07)
- operation: getDrugPrdtPrmsnDtlInq06
- 권위 소스: ITEM_PERMIT_DATE, ATC_CODE, MATERIAL_NAME, UD_DOC_DATA(용법용량 XML),
            EE_DOC_DATA(효능효과), STORAGE_METHOD, PACK_UNIT, RARE_DRUG_YN 등
- 활용: drug_enrichment 의 1차 권위 소스 (Perplexity LLM 결과보다 우선)

응답 row 핵심 필드:
- ITEM_SEQ / ITEM_NAME / ITEM_ENG_NAME — 품목 식별
- ITEM_PERMIT_DATE (YYYYMMDD) — 식약처 허가일자
- CANCEL_DATE / CANCEL_NAME — 취소 여부 ('정상' 이면 유효)
- ETC_OTC_CODE — 전문의약품/일반의약품
- ATC_CODE — WHO ATC 분류
- MAIN_ITEM_INGR / MATERIAL_NAME — 주성분 + 분량
- UD_DOC_DATA — 용법용량 XML
- EE_DOC_DATA — 효능효과 XML
- NB_DOC_DATA — 사용상 주의사항 XML
- STORAGE_METHOD / VALID_TERM / PACK_UNIT
- RARE_DRUG_YN — 희귀의약품 여부
- REEXAM_TARGET / REEXAM_DATE — 재심사 대상/만료
- BAR_CODE / EDI_CODE — 표준코드/보험코드
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[2]
ENV_PATH = BASE_DIR / "config" / ".env"
DB_PATH = BASE_DIR / "data" / "db" / "drug_prices.db"

API_BASE = "https://apis.data.go.kr/1471000/DrugPrdtPrmsnInfoService07/getDrugPrdtPrmsnDtlInq06"
CACHE_TTL_DAYS = 30  # 허가/변경 이력은 비교적 정적 — 30일 TTL


def _load_env() -> None:
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def _get_service_key() -> str:
    _load_env()
    for var in ("MFDS_PATENT_API_KEY", "MFDS_PATENT_SERVICE_KEY"):
        # 동일 키를 양 서비스에서 공유 (data.go.kr 의 같은 키로 두 endpoint 호출 가능)
        key = os.environ.get(var, "").strip()
        if key:
            return key
    raise RuntimeError(
        "MFDS_PATENT_API_KEY 가 config/.env 에 없습니다. "
        "공공데이터포털 발급키를 환경변수에 추가하세요."
    )


# ── Raw API 호출 ────────────────────────────────────────────────────────────

def fetch_raw(
    item_name: str | None = None,
    item_seq: str | None = None,
    entp_name: str | None = None,
    edi_code: str | None = None,
    bar_code: str | None = None,
    main_item_ingr: str | None = None,
    num_of_rows: int = 10,
    page_no: int = 1,
    timeout: int = 30,
) -> list[dict]:
    """직접 API 호출. 모든 검색조건은 None 가능, 최소 1개 필요."""
    if not any([item_name, item_seq, entp_name, edi_code, bar_code, main_item_ingr]):
        raise ValueError("최소 하나의 검색조건이 필요합니다.")

    params = {
        "serviceKey": _get_service_key(),
        "type": "json",
        "numOfRows": str(num_of_rows),
        "pageNo": str(page_no),
    }
    if item_name:      params["item_name"]      = item_name
    if item_seq:       params["item_seq"]       = item_seq
    if entp_name:      params["entp_name"]      = entp_name
    if edi_code:       params["edi_code"]       = edi_code
    if bar_code:       params["bar_code"]       = bar_code
    if main_item_ingr: params["main_item_ingr"] = main_item_ingr

    qs = urllib.parse.urlencode(params, doseq=True)
    url = f"{API_BASE}?{qs}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = r.read().decode("utf-8", errors="replace")
    except Exception as e:
        logger.warning("MFDS 허가 API 호출 실패: %s", e)
        return []

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        logger.warning("MFDS 허가 응답 JSON 파싱 실패: %s", body[:200])
        return []

    header = data.get("header") or {}
    if header.get("resultCode") and header.get("resultCode") != "00":
        logger.info("MFDS 허가 응답 비정상: %s — %s", header.get("resultCode"), header.get("resultMsg"))
        return []
    body_obj = data.get("body") or {}
    items = body_obj.get("items") or []
    if isinstance(items, dict):
        items = [items]
    return items


# ── 캐시 (sqlite) ───────────────────────────────────────────────────────────

def _ensure_table() -> None:
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mfds_permit_cache (
                item_seq        TEXT PRIMARY KEY,
                item_name       TEXT NOT NULL,
                edi_code        TEXT,
                permit_date     TEXT,
                cancel_status   TEXT,
                etc_otc         TEXT,
                atc_code        TEXT,
                main_ingr       TEXT,
                ingr_name       TEXT,
                storage_method  TEXT,
                valid_term      TEXT,
                pack_unit       TEXT,
                rare_drug       TEXT,
                reexam_target   TEXT,
                reexam_date     TEXT,
                fetched_at      TEXT,
                raw_json        TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_permit_item_name ON mfds_permit_cache(item_name)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_permit_edi ON mfds_permit_cache(edi_code)")


def _cache_read_by_name(item_name: str) -> tuple[Optional[dict], Optional[datetime]]:
    _ensure_table()
    with sqlite3.connect(str(DB_PATH)) as conn:
        cur = conn.execute(
            "SELECT raw_json, fetched_at FROM mfds_permit_cache WHERE item_name = ? LIMIT 1",
            (item_name,),
        )
        row = cur.fetchone()
    if not row:
        return None, None
    try:
        raw = json.loads(row[0])
    except Exception:
        raw = None
    fa = None
    try:
        fa = datetime.fromisoformat(row[1])
    except (ValueError, TypeError):
        pass
    return raw, fa


def _cache_write(item_name: str, item: dict | None) -> None:
    _ensure_table()
    now = datetime.now().isoformat(timespec="seconds")
    if not item:
        # 빈 sentinel
        with sqlite3.connect(str(DB_PATH)) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO mfds_permit_cache
                  (item_seq, item_name, edi_code, permit_date, cancel_status,
                   etc_otc, atc_code, main_ingr, ingr_name, storage_method,
                   valid_term, pack_unit, rare_drug, reexam_target, reexam_date,
                   fetched_at, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"__EMPTY__:{item_name}", item_name, None, None, None,
                    None, None, None, None, None,
                    None, None, None, None, None,
                    now, json.dumps({}, ensure_ascii=False),
                ),
            )
        return
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO mfds_permit_cache
              (item_seq, item_name, edi_code, permit_date, cancel_status,
               etc_otc, atc_code, main_ingr, ingr_name, storage_method,
               valid_term, pack_unit, rare_drug, reexam_target, reexam_date,
               fetched_at, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.get("ITEM_SEQ"),
                item_name,
                item.get("EDI_CODE"),
                item.get("ITEM_PERMIT_DATE"),
                item.get("CANCEL_NAME"),
                item.get("ETC_OTC_CODE"),
                item.get("ATC_CODE"),
                item.get("MAIN_ITEM_INGR"),
                item.get("INGR_NAME"),
                item.get("STORAGE_METHOD"),
                item.get("VALID_TERM"),
                item.get("PACK_UNIT"),
                item.get("RARE_DRUG_YN"),
                item.get("REEXAM_TARGET"),
                item.get("REEXAM_DATE"),
                now,
                json.dumps(item, ensure_ascii=False),
            ),
        )


def _cache_fresh(fetched_at: Optional[datetime]) -> bool:
    if fetched_at is None:
        return False
    return (datetime.now() - fetched_at).total_seconds() / 86400.0 < CACHE_TTL_DAYS


# ── XML DOC 파싱 ────────────────────────────────────────────────────────────

def parse_doc_xml(xml_str: str | None) -> str:
    """
    EE/UD/NB DOC_DATA 의 XML 을 단순 텍스트로 변환.
    구조: <DOC><SECTION><ARTICLE title="..."><PARAGRAPH>...</PARAGRAPH></ARTICLE></SECTION></DOC>
    """
    if not xml_str:
        return ""
    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError:
        # 일부 응답은 인코딩 문제로 깨짐 — 단순 stripping fallback
        return re.sub(r"<[^>]+>", " ", xml_str).strip()

    out_lines: list[str] = []
    for section in root.iter("SECTION"):
        sec_title = (section.attrib.get("title") or "").strip()
        if sec_title:
            out_lines.append(f"## {sec_title}")
        for article in section.iter("ARTICLE"):
            art_title = (article.attrib.get("title") or "").strip()
            if art_title:
                out_lines.append(f"### {art_title}")
            for para in article.iter("PARAGRAPH"):
                txt = "".join(para.itertext()).strip()
                if txt:
                    out_lines.append(txt)
    return "\n".join(out_lines).strip()


# ── 변형 검색 (특허 모듈과 동일 전략) ──────────────────────────────────────────

def _name_variants(item_name: str) -> list[str]:
    """hit 률 높은 순으로 정렬. 첫 hit 즉시 break."""
    import re
    seen: list[str] = []
    def _push(s: str):
        s = (s or "").strip()
        if s and s not in seen:
            seen.append(s)
    base = item_name.split("(")[0].strip()
    # 1순위: 괄호 이전 (가장 hit 률 높음)
    _push(base)
    # 2순위: 한글 변형
    swaps = [("밀리그람", "밀리그램"), ("밀리그램", "밀리그람"), ("그람", "그램"), ("그램", "그람")]
    for src, dst in swaps:
        if src in base:
            _push(base.replace(src, dst))
    # 3순위: 원본 (괄호 포함)
    _push(item_name)
    # 4순위: 함량 단위 직전 prefix
    cut_keywords = ["밀리그", "마이크로그", "그램", "그람", "유닛", "단위"]
    for kw in cut_keywords:
        idx = base.find(kw)
        if idx > 1:
            _push(base[:idx])
    # 5순위: 한글 brand prefix only
    m = re.match(r"^([가-힣]+)", base)
    if m and len(m.group(1)) >= 2:
        _push(m.group(1))
    return seen


def _try_variants(
    item_name: str,
    ingredient: str | None,
    edi_code: str | None,
) -> list[dict]:
    # EDI 코드 우선 (가장 정확)
    if edi_code:
        items = fetch_raw(edi_code=edi_code, num_of_rows=10)
        if items:
            logger.info("MFDS 허가 EDI hit: %s → %d건", edi_code, len(items))
            return items

    # 제품명 변형 시도
    for variant in _name_variants(item_name):
        items = fetch_raw(item_name=variant, num_of_rows=10)
        if items:
            logger.info("MFDS 허가 hit: variant='%s' (원본='%s') → %d건", variant, item_name, len(items))
            return items

    # ingredient fallback
    if ingredient:
        ingr_clean = ingredient.split("(")[0].split(",")[0].strip()
        if ingr_clean:
            items = fetch_raw(main_item_ingr=ingr_clean, num_of_rows=20)
            if items:
                logger.info("MFDS 허가 ingredient fallback hit: '%s' → %d건", ingr_clean, len(items))
                return items
    return []


# ── 핵심 변환: API row → 정규 dict ──────────────────────────────────────────

def _yyyymmdd_to_iso(d: str | None) -> str | None:
    if not d or len(d) != 8 or not d.isdigit():
        return None
    return f"{d[:4]}.{d[4:6]}.{d[6:8]}"


def to_summary(item: dict) -> dict:
    """API row 를 enrichment 친화적 dict 로 변환."""
    if not item:
        return {}
    return {
        "item_seq":       item.get("ITEM_SEQ"),
        "item_name":      item.get("ITEM_NAME"),
        "item_eng_name":  item.get("ITEM_ENG_NAME"),
        "entp_name":      item.get("ENTP_NAME"),
        "permit_date":    _yyyymmdd_to_iso(item.get("ITEM_PERMIT_DATE")),
        "cancel_status":  item.get("CANCEL_NAME"),  # '정상' / '취소' / '취하' 등
        "etc_otc":        item.get("ETC_OTC_CODE"),  # '전문의약품' / '일반의약품'
        "atc_code":       item.get("ATC_CODE"),
        "main_ingr":      item.get("MAIN_ITEM_INGR"),
        "main_ingr_eng":  item.get("MAIN_INGR_ENG"),
        "material_name":  item.get("MATERIAL_NAME"),  # 총량/분량/규격
        "ingr_name":      item.get("INGR_NAME"),  # 첨가제 포함 전성분
        "chart":          item.get("CHART"),  # 성상
        "pack_unit":      item.get("PACK_UNIT"),
        "valid_term":     item.get("VALID_TERM"),
        "storage_method": item.get("STORAGE_METHOD"),
        "rare_drug_yn":   item.get("RARE_DRUG_YN"),
        "newdrug_class":  item.get("NEWDRUG_CLASS_NAME"),
        "reexam_target":  item.get("REEXAM_TARGET"),
        "reexam_date":    _yyyymmdd_to_iso(item.get("REEXAM_DATE")),
        "edi_code":       item.get("EDI_CODE"),
        "bar_code":       item.get("BAR_CODE"),
        "change_date":    _yyyymmdd_to_iso(item.get("CHANGE_DATE")),
        "narcotic_kind":  item.get("NARCOTIC_KIND_CODE"),
        "permit_kind":    item.get("PERMIT_KIND_NAME"),
        # XML 본문 → text
        "usage_text":     parse_doc_xml(item.get("UD_DOC_DATA")),
        "effect_text":    parse_doc_xml(item.get("EE_DOC_DATA")),
        "caution_text":   parse_doc_xml(item.get("NB_DOC_DATA")),
        # PDF 다운로드 링크
        "ud_doc_url":     item.get("UD_DOC_ID"),
        "ee_doc_url":     item.get("EE_DOC_ID"),
        "nb_doc_url":     item.get("NB_DOC_ID"),
    }


# ── Public API ──────────────────────────────────────────────────────────────

def lookup_permit(
    item_name: str,
    ingredient: str | None = None,
    edi_code: str | None = None,
    *,
    use_cache: bool = True,
    refresh: bool = False,
) -> dict:
    """
    제품명/EDI 코드 기반 허가정보 조회 (캐시 우선).
    Returns: to_summary() 결과 + {'fetched_at', 'source': 'mfds_api' | 'cache' | 'miss'}
    """
    item_name = (item_name or "").strip()
    if not item_name and not edi_code:
        return {"source": "miss", "fetched_at": None}

    if use_cache and not refresh and item_name:
        cached, fa = _cache_read_by_name(item_name)
        if cached and _cache_fresh(fa):
            summary = to_summary(cached)
            summary["fetched_at"] = fa.isoformat() if fa else None
            summary["source"] = "cache"
            return summary

    items = _try_variants(item_name, ingredient, edi_code)
    if not items:
        _cache_write(item_name, None)
        return {"source": "miss", "fetched_at": datetime.now().isoformat(timespec="seconds"),
                "item_name": item_name}

    # 1차: cancel_status='정상' 인 row 우선
    item = next((it for it in items if (it.get("CANCEL_NAME") or "").strip() == "정상"), items[0])
    _cache_write(item_name, item)
    summary = to_summary(item)
    summary["fetched_at"] = datetime.now().isoformat(timespec="seconds")
    summary["source"] = "mfds_api"
    return summary


# ── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

    ap = argparse.ArgumentParser(description="MFDS 의약품 허가정보 조회")
    ap.add_argument("item_name", help="한글 제품명")
    ap.add_argument("--ingredient", default=None)
    ap.add_argument("--edi", default=None, help="보험코드 (EDI)")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--raw", action="store_true", help="원본 API row 출력")
    args = ap.parse_args()

    if args.raw:
        rows = fetch_raw(item_name=args.item_name)
        print(json.dumps(rows[:1], ensure_ascii=False, indent=2))
    else:
        result = lookup_permit(args.item_name, ingredient=args.ingredient,
                               edi_code=args.edi, refresh=args.refresh)
        # XML 본문은 길어서 head 만 출력
        for k in ("usage_text", "effect_text", "caution_text"):
            if k in result and isinstance(result[k], str) and len(result[k]) > 400:
                result[k] = result[k][:400] + "...(truncated)"
        print(json.dumps(result, ensure_ascii=False, indent=2))
