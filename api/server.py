"""
대쉬보드 검색 API 서버 (Flask)
- 대쉬보드에서 약제명 검색 시 해외 약가 실시간 조회
- 국내 약가 DB 검색도 제공
- 국내 약가 변동 이력 및 변동 사유 제공
- 로컬 전용 (127.0.0.1)
"""

import asyncio
import logging
import re
import sys
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

# 프로젝트 루트를 sys.path에 추가
BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from agents.db import DrugPriceDB
from agents.foreign_price_agent import ForeignPriceAgent, AVAILABLE_COUNTRIES
from agents.market_intelligence import MarketIntelligenceAgent, MI_RULES_TEXT
from agents.review_agent import ReviewAgent
from agents.drug_enrichment_agent import DrugEnrichmentAgent

app = Flask(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

db = DrugPriceDB(BASE_DIR / "data" / "db" / "drug_prices.db")
foreign_agent = ForeignPriceAgent(BASE_DIR)


def _check_calibration_age() -> None:
    """서버 시작 시 마지막 캘리브레이션 경과일 확인. 90일 초과면 경고."""
    try:
        from datetime import datetime
        from agents.media_calibrator import load_latest_calibration
        cal = load_latest_calibration()
        if cal is None:
            logger.warning(
                "[MediaCalibrator] 초기 보정 미실행. "
                "POST /api/admin/calibrate-media 또는 "
                "bash scripts/run_calibration.sh 를 실행하세요."
            )
            return
        cal_dt   = datetime.fromisoformat(cal["calibrated_at"])
        days_ago = (datetime.now() - cal_dt).days
        if days_ago >= 90:
            logger.warning(
                "[MediaCalibrator] 마지막 보정 %d일 전 (%s) — 분기 재보정 권장",
                days_ago, cal["calibrated_at"][:10]
            )
        else:
            logger.info("[MediaCalibrator] 최근 보정 %s (%d일 전)", cal["calibrated_at"][:10], days_ago)
    except Exception as e:
        logger.debug("[MediaCalibrator] 상태 확인 실패: %s", e)


_check_calibration_age()


# ──────────────────────────────────────────────────────────────────────────────
# 국내 약가 검색
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/api/domestic/search")
def domestic_search():
    """
    국내 약가 검색
    GET /api/domestic/search?q=키트루다&limit=20
    """
    query = request.args.get("q", "").strip()
    limit = min(int(request.args.get("limit", 50)), 200)
    if not query:
        return jsonify({"error": "검색어(q)를 입력하세요."}), 400

    results = db.search_drug(query, limit=limit)
    return jsonify({"query": query, "count": len(results), "results": results})


@app.get("/api/domestic/history/<insurance_code>")
def domestic_history(insurance_code: str):
    """
    보험코드별 국내 약가 이력
    GET /api/domestic/history/652902770
    """
    results = db.get_price_history(insurance_code)
    return jsonify({"insurance_code": insurance_code, "count": len(results), "results": results})


@app.get("/api/domestic/stats")
def domestic_stats():
    return jsonify(db.get_stats())


# ──────────────────────────────────────────────────────────────────────────────
# 국내 약가 변동 이력
# ──────────────────────────────────────────────────────────────────────────────

_NAME_RE = re.compile(r"^([^(（]+)")          # 브랜드명
_ING_RE  = re.compile(r"\(([^)]+)\)")         # 첫 번째 괄호 = 주성분
_DOSE_RE = re.compile(r"_\(([^)]+)\)\s*$")   # 말미 _(...) = 제형


def _normalize_brand(name: str) -> str:
    """
    제품명을 정규화하여 병합 키로 사용.
    예:
      '자누비아정100mg'                                              → '자누비아정100mg'
      '자누비아정100밀리그램(인산시타글립틴일수화물)'                 → '자누비아정100mg'
      '자누비아정100밀리그램(시타글립틴인산염수화물)_(0.1289g/1정)'    → '자누비아정100mg'
      '리피토정10밀리그람(아토르바스타틴칼슘)'                         → '리피토정10mg'

    규칙:
      1) 뒤쪽 _(...) 제형/함량 suffix 제거
      2) 남은 괄호(...) 성분명 변형 제거
      3) 용량 단위 통일: 밀리그램/밀리그람 → mg, 마이크로그램 → mcg,
         밀리리터 → ml, 그램(단독) → g
      4) 공백·후행 underscore 제거
    """
    if not name:
        return ""
    s = re.sub(r"_\([^)]*\)\s*$", "", name)      # 제형 suffix
    s = re.sub(r"\([^)]*\)", "", s)              # 성분 괄호
    s = s.rstrip("_").strip()
    s = s.replace("밀리그램", "mg").replace("밀리그람", "mg")
    s = s.replace("마이크로그램", "mcg").replace("마이크로그람", "mcg")
    s = s.replace("밀리리터", "ml").replace("밀리리타", "ml")
    # '그램'은 '밀리그램/마이크로그램' 치환 후에만 단독 g 처리
    s = re.sub(r"(\d+)\s*그램", r"\1g", s)
    # 공백 전체 제거 (HIRA는 공백 변형 많음)
    s = re.sub(r"\s+", "", s)
    return s.strip()


def _parse_product(name: str) -> dict:
    """product_name_kr 에서 브랜드명·주성분·제형을 추출."""
    brand_m = _NAME_RE.search(name)
    brand   = brand_m.group(1).strip() if brand_m else name

    # 괄호 목록 전부 추출 → 첫 번째가 주성분 (단, 숫자만이면 농도표기이므로 건너뜀)
    all_parens = re.findall(r"\(([^)]+)\)", name)
    ingredient = ""
    for p in all_parens:
        if not re.match(r"^[\d%\.]+$", p):   # 순수 숫자/% 아닌 것
            ingredient = p
            break

    dose_m  = _DOSE_RE.search(name)
    dosage_form = dose_m.group(1) if dose_m else ""

    return {"brand": brand, "ingredient": ingredient, "dosage_form": dosage_form}


def _build_price_changes(rows: list) -> list:
    """
    DB rows(apply_date 순 정렬) → 가격이 바뀐 시점만 추출.
    반환: [{"date", "price", "price_change", "delta_pct",
           "base_price_change_rate", "change_type", "is_first"}, ...]
      - price_change            : 직전 대비 절대 변동액 (원). 최초는 0.
      - delta_pct               : 직전 대비 변동률 (%). 최초는 None.
      - base_price_change_rate  : 최초 등재가 대비 누적 변동률 (%). 최초는 0.
      - change_type             : '최초' / '인상' / '인하'
    """
    changes = []
    prev_price = None
    base_price = None
    for row in rows:
        price = row["max_price"]
        if price is None:
            continue
        if prev_price is None:
            base_price = price
            changes.append({
                "date": row["apply_date"],
                "price": price,
                "price_change": 0,
                "delta_pct": None,
                "base_price_change_rate": 0.0,
                "change_type": "최초",
                "is_first": True,
            })
        elif price != prev_price:
            abs_delta = price - prev_price
            delta_pct = round(abs_delta / prev_price * 100, 2)
            base_rate = round((price - base_price) / base_price * 100, 2) if base_price else 0.0
            changes.append({
                "date": row["apply_date"],
                "price": price,
                "price_change": abs_delta,
                "delta_pct": delta_pct,
                "base_price_change_rate": base_rate,
                "change_type": "인상" if abs_delta > 0 else "인하",
                "is_first": False,
            })
        prev_price = price
    return changes


@app.get("/api/domestic/price-changes")
def price_changes():
    """
    약제명·주성분명으로 검색 → 보험코드별 가격 변동 이력 반환.
    GET /api/domestic/price-changes?q=키트루다

    반환 구조:
    {
      "query": "...",
      "products": [
        {
          "insurance_code": "...",
          "product_name": "...",
          "brand_name": "...",
          "ingredient": "...",
          "dosage_form": "...",
          "company": "...",
          "first_date": "...",
          "current_price": 0,
          "price_history": [{"date","price","delta_pct","is_first"}, ...]
        }
      ],
      "dosage_forms": ["0.1g/4mL", ...]   // 복수일 때만 필터 활성화
    }
    """
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "검색어(q)를 입력하세요."}), 400

    # 1) 키워드로 최신 레코드 검색 → 보험코드 목록 확보
    matches = db.search_drug(query, limit=200)
    if not matches:
        return jsonify({"query": query, "products": [], "dosage_forms": []})

    # 보험코드 중복 제거 (최신 레코드 기준)
    code_map: dict = {}
    for m in matches:
        code = m["insurance_code"]
        if code not in code_map or m["apply_date"] > code_map[code]["apply_date"]:
            code_map[code] = m

    # 2) 보험코드별 상품 조립
    raw_products = []
    for code, latest in code_map.items():
        history_rows = db.get_price_history(code)
        if not history_rows:
            continue

        parsed = _parse_product(latest["product_name_kr"])
        changes = _build_price_changes(history_rows)
        if not changes:
            continue

        raw_products.append({
            "insurance_code": code,
            "product_name": latest["product_name_kr"],
            "brand_name": parsed["brand"],
            "ingredient": parsed["ingredient"],
            "dosage_form": parsed["dosage_form"],
            "company": latest["company"],
            "first_date": changes[0]["date"],
            "current_price": changes[-1]["price"],
            "price_history": changes,
            "_apply_date": latest.get("apply_date", ""),
        })

    # 3) 동일 제품 병합 — _normalize_brand() 단일 키로 통합
    #    브랜드명+용량만 일치하면 (회사·코드·성분표기·제형 suffix 무시) 같은 제품으로 간주.
    #    예: '자누비아정100mg' / '자누비아정100밀리그램(인산시타글립틴일수화물)' /
    #        '자누비아정100밀리그램(시타글립틴인산염수화물)_(0.1289g/1정)' → 모두 '자누비아정100mg'
    merge_map: dict = {}
    for rp in raw_products:
        norm_key = _normalize_brand(rp["product_name"])
        if not norm_key:  # 정규화 실패 — 원본 코드로 개별 유지
            merge_map[f"__single__::{rp['insurance_code']}"] = rp
            continue
        if norm_key not in merge_map:
            rp["normalized_name"] = norm_key
            merge_map[norm_key] = rp
            continue
        # 병합
        existing = merge_map[norm_key]
        by_date: dict = {h["date"]: h for h in existing["price_history"]}
        for h in rp["price_history"]:
            prev = by_date.get(h["date"])
            if not prev or (h.get("price") or 0) > (prev.get("price") or 0):
                by_date[h["date"]] = h
        merged_hist = sorted(by_date.values(), key=lambda x: x["date"])
        synth_rows = [{"apply_date": h["date"], "max_price": h["price"]} for h in merged_hist]
        existing["price_history"] = _build_price_changes(synth_rows)
        existing["first_date"]    = existing["price_history"][0]["date"]
        existing["current_price"] = existing["price_history"][-1]["price"]
        # 대표 메타 = 최신 apply_date 레코드
        if rp["_apply_date"] > existing.get("_apply_date", ""):
            existing["insurance_code"] = rp["insurance_code"]
            existing["product_name"]   = rp["product_name"]
            existing["company"]        = rp["company"]
            existing["ingredient"]     = rp["ingredient"] or existing["ingredient"]
            existing["dosage_form"]    = rp["dosage_form"] or existing["dosage_form"]
            existing["_apply_date"]    = rp["_apply_date"]
        # 이력 메타
        existing.setdefault("merged_codes", [])
        if rp["insurance_code"] not in existing["merged_codes"]:
            existing["merged_codes"].append(rp["insurance_code"])
        existing.setdefault("merged_companies", [])
        if rp["company"] and rp["company"] not in existing["merged_companies"]:
            existing["merged_companies"].append(rp["company"])

    products = list(merge_map.values())

    # 4) 대표 코드도 merged_codes에 포함되도록 보정 + status 산출
    from datetime import datetime as _dt
    today = _dt.now()
    for p in products:
        p.setdefault("merged_codes", [])
        if p["insurance_code"] not in p["merged_codes"]:
            p["merged_codes"].insert(0, p["insurance_code"])
        p.setdefault("merged_companies", [])
        if p["company"] and p["company"] not in p["merged_companies"]:
            p["merged_companies"].insert(0, p["company"])
        # 최신 가격 상태 판정: None/0 또는 마지막 apply_date > 12개월 경과 시 delisted 의심
        last = p["price_history"][-1] if p["price_history"] else None
        status = "active"
        status_detail = ""
        if last:
            try:
                last_dt = _dt.strptime(last["date"], "%Y.%m.%d")
                gap_days = (today - last_dt).days
            except Exception:
                gap_days = 0
            if last.get("price") in (None, 0):
                status = "delisted_probable"
                status_detail = "최신 레코드에 약가 정보 없음 — 급여기준 삭제 또는 제품명 변경 가능성"
            elif gap_days > 365:
                status = "stale"
                status_detail = f"마지막 약가 갱신 이후 {gap_days//30}개월 경과 — 급여 삭제 가능성 검토 필요"
        p["status"]        = status
        p["status_detail"] = status_detail
        p.pop("_apply_date", None)

    # 제형 기준 정렬
    products.sort(key=lambda x: x["dosage_form"])

    dosage_forms = sorted({p["dosage_form"] for p in products if p["dosage_form"]})

    return jsonify({
        "query": query,
        "products": products,
        "dosage_forms": dosage_forms,
    })


# ──────────────────────────────────────────────────────────────────────────────
# 엑셀/CSV 다운로드 (가격 변동 이력 · 약가 정보)
# ──────────────────────────────────────────────────────────────────────────────

def _export_rows(
    rows: list[dict],
    columns: list[tuple[str, str]],
    filename_base: str,
    fmt: str,
):
    """
    rows: list of dicts
    columns: [(key, header_label), ...]  — 출력 순서·헤더명
    fmt: 'csv' | 'xlsx'
    """
    import io
    from flask import Response
    from datetime import datetime
    from urllib.parse import quote

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_base = re.sub(r"[^\w\-]", "_", filename_base)[:60] or "export"

    if fmt == "xlsx":
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, PatternFill, Alignment
        except ImportError:
            return jsonify({"error": "openpyxl 미설치 — pip install openpyxl"}), 500
        wb = Workbook()
        ws = wb.active
        ws.title = "export"
        header_font = Font(bold=True, color="FFFFFF")
        header_fill = PatternFill("solid", fgColor="1A56DB")
        ws.append([label for _, label in columns])
        for cell in ws[1]:
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center")
        for r in rows:
            ws.append([r.get(k, "") for k, _ in columns])
        # 컬럼 폭 자동
        for col_idx, (k, _) in enumerate(columns, start=1):
            max_len = max(
                [len(str(r.get(k, ""))) for r in rows] + [len(columns[col_idx-1][1])]
            ) + 2
            ws.column_dimensions[ws.cell(1, col_idx).column_letter].width = min(max_len, 40)
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        fname = f"{safe_base}_{stamp}.xlsx"
        return Response(
            buf.read(),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"},
        )

    # CSV (엑셀 한글 호환 BOM 추가)
    import csv as _csv
    buf = io.StringIO()
    buf.write("\ufeff")
    writer = _csv.writer(buf)
    writer.writerow([label for _, label in columns])
    for r in rows:
        writer.writerow([r.get(k, "") for k, _ in columns])
    fname = f"{safe_base}_{stamp}.csv"
    return Response(
        buf.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"},
    )


@app.get("/api/domestic/price-changes/export")
def price_changes_export():
    """
    가격 변동 이력을 엑셀/CSV 로 다운로드.
    GET /api/domestic/price-changes/export?q=키트루다&format=xlsx
    format: 'csv' | 'xlsx' (default 'xlsx')
    """
    query = request.args.get("q", "").strip()
    fmt   = (request.args.get("format", "xlsx") or "xlsx").lower()
    if fmt not in ("csv", "xlsx"):
        return jsonify({"error": "format 은 csv 또는 xlsx 여야 합니다."}), 400
    if not query:
        return jsonify({"error": "검색어(q)를 입력하세요."}), 400

    # /api/domestic/price-changes 와 동일 로직 재사용
    with app.test_request_context(f"/api/domestic/price-changes?q={query}"):
        payload = price_changes().get_json()
    products = payload.get("products", [])
    if not products:
        return jsonify({"error": f"'{query}' 검색 결과 없음"}), 404

    rows = []
    for p in products:
        base = {
            "product_name":   p.get("product_name", ""),
            "brand_name":     p.get("brand_name", ""),
            "ingredient":     p.get("ingredient", ""),
            "dosage_form":    p.get("dosage_form", ""),
            "company":        p.get("company", ""),
            "insurance_code": p.get("insurance_code", ""),
            "status":         p.get("status", ""),
        }
        for h in p.get("price_history", []):
            rows.append({
                **base,
                "date":      h.get("date", ""),
                "price":     h.get("price", ""),
                "delta_pct": ("" if h.get("is_first") else h.get("delta_pct", "")),
                "is_first":  "등재" if h.get("is_first") else "변동",
            })

    columns = [
        ("date",           "변동일"),
        ("is_first",       "구분"),
        ("price",          "상한금액(원)"),
        ("delta_pct",      "변동률(%)"),
        ("product_name",   "제품명"),
        ("brand_name",     "브랜드"),
        ("ingredient",     "주성분"),
        ("dosage_form",    "규격"),
        ("company",        "업체명"),
        ("insurance_code", "보험코드"),
        ("status",         "상태"),
    ]
    return _export_rows(rows, columns, f"price_history_{query}", fmt)


@app.get("/api/domestic/search/export")
def domestic_search_export():
    """
    약가 정보(최신 레코드)를 엑셀/CSV 로 다운로드.
    GET /api/domestic/search/export?q=키트루다&format=xlsx
    """
    query = request.args.get("q", "").strip()
    fmt   = (request.args.get("format", "xlsx") or "xlsx").lower()
    if fmt not in ("csv", "xlsx"):
        return jsonify({"error": "format 은 csv 또는 xlsx 여야 합니다."}), 400
    if not query:
        return jsonify({"error": "검색어(q)를 입력하세요."}), 400

    results = db.search_drug(query, limit=500)
    if not results:
        return jsonify({"error": f"'{query}' 검색 결과 없음"}), 404

    # DB 레코드 키 그대로 사용 (존재하는 키만 노출)
    sample = results[0]
    preferred = [
        ("apply_date",      "적용일"),
        ("product_name_kr", "제품명(한글)"),
        ("ingredient_kr",   "주성분"),
        ("company",         "업체명"),
        ("insurance_code",  "보험코드"),
        ("max_price",       "상한금액(원)"),
        ("dosage_form",     "제형/규격"),
        ("atc_code",        "ATC코드"),
        ("remark",          "비고"),
    ]
    columns = [(k, label) for k, label in preferred if k in sample]
    # 추가 키가 있으면 뒤에 붙임
    for k in sample.keys():
        if k not in {c[0] for c in columns}:
            columns.append((k, k))
    return _export_rows(results, columns, f"drug_info_{query}", fmt)


# ──────────────────────────────────────────────────────────────────────────────
# 가격 변동 사유 조회 (MarketIntelligenceAgent — 의학전문지 + 기전 분석)
# ──────────────────────────────────────────────────────────────────────────────

_mi_agent = MarketIntelligenceAgent(
    cache_dir=BASE_DIR / "data" / "dashboard" / "reason_cache"
)
_review_agent = ReviewAgent()
_enrichment_agent = DrugEnrichmentAgent(db)


# ──────────────────────────────────────────────────────────────────────────────
# 약제 부가정보 (RSA · 허가일 · 용법용량 · 치료비)
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/api/domestic/enrichment")
def domestic_enrichment():
    """
    GET /api/domestic/enrichment
        ?normalized_name=자누비아정100mg
        &code=498900030
        &product_name=자누비아정100밀리그램(인산시타글립틴일수화물)
        &ingredient=시타글립틴인산염수화물
        &current_price=866
        &codes=498900030,498900031      (병합 코드 목록)
        &refresh=0
    """
    normalized_name = request.args.get("normalized_name", "").strip()
    code            = request.args.get("code", "").strip()
    product_name    = request.args.get("product_name", "").strip()
    ingredient      = request.args.get("ingredient", "").strip()
    codes_raw       = request.args.get("codes", "").strip()
    codes           = [c.strip() for c in codes_raw.split(",") if c.strip()]
    force_refresh   = request.args.get("refresh", "0") == "1"
    try:
        current_price = float(request.args.get("current_price", "") or 0) or None
    except ValueError:
        current_price = None

    if not normalized_name:
        return jsonify({"error": "normalized_name 파라미터가 필요합니다."}), 400

    try:
        data = _enrichment_agent.get(
            normalized_name,
            representative_code=code,
            insurance_codes=codes,
            product_name=product_name,
            ingredient=ingredient,
            current_price=current_price,
            force_refresh=force_refresh,
        )
        return jsonify(data)
    except Exception as e:
        logger.error("enrichment 조회 실패: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.get("/api/domestic/change-reason")
def change_reason():
    """
    약가 변동 사유 조회.
    MarketIntelligenceAgent가 PubMed + HIRA/NECA + MA 전문지를 검색하고
    한국 약가 사후관리 4대 기전(적응증 확대/특허 만료/사용량-연동/실거래가 연동)
    프레임으로 GPT-4o가 분석한다. 결과는 JSON 캐시로 저장된다.

    GET /api/domestic/change-reason
        ?drug=키트루다주
        &drug_en=Keytruda
        &date=2022.03.01
        &ingredient=펨브롤리주맙,유전자재조합
        &ingredient_en=pembrolizumab
        &delta_pct=-25.61
        &refresh=0   (1이면 캐시 무시 재분석)
    """
    drug         = request.args.get("drug", "").strip()
    drug_en      = request.args.get("drug_en", "").strip()
    change_date  = request.args.get("date", "").strip()
    ingredient   = request.args.get("ingredient", "").strip()
    ingredient_en = request.args.get("ingredient_en", "").strip()
    force_refresh = request.args.get("refresh", "0") == "1"

    try:
        delta_pct = float(request.args.get("delta_pct", "0") or "0") or None
    except ValueError:
        delta_pct = None

    if not drug or not change_date:
        return jsonify({"error": "drug, date 파라미터가 필요합니다."}), 400

    result = _mi_agent.analyze_price_change(
        drug_ko=drug,
        drug_en=drug_en or drug,
        ingredient_ko=ingredient,
        ingredient_en=ingredient_en,
        change_date=change_date,
        delta_pct=delta_pct,
        force_refresh=force_refresh,
    )

    # ── ReviewAgent 게이트: 결과가 요청·룰에 부합하는지 최종 검증 (최대 1회 재시도) ──
    req_ctx = {"drug": drug, "date": change_date, "delta_pct": delta_pct,
               "ingredient": ingredient}
    verdict = _review_agent.review_price_change_reason(req_ctx, result, MI_RULES_TEXT)
    if not verdict.get("approved", False):
        logger.info("[Review] 1차 거부 — %s", verdict.get("final_verdict", ""))
        # 재시도: 캐시 무시 + corrective_actions 반영
        retry = _mi_agent.analyze_price_change(
            drug_ko=drug, drug_en=drug_en or drug,
            ingredient_ko=ingredient, ingredient_en=ingredient_en,
            change_date=change_date, delta_pct=delta_pct,
            force_refresh=True,
        )
        verdict2 = _review_agent.review_price_change_reason(req_ctx, retry, MI_RULES_TEXT)
        if verdict2.get("approved", False):
            result = retry
            verdict = verdict2
        else:
            # 재시도 후에도 거부 — 명시적 unknown/low 로 하향 + reason 도 일관되게 재작성
            logger.info("[Review] 재시도 거부 — unknown 하향")
            result = retry
            result["mechanism"] = "unknown"
            result["mechanism_label"] = "미분류"
            result["confidence"] = "low"
            win = result.get("window", {}) or {}
            win_txt = f"{win.get('from','')}~{win.get('to','')}"
            original_reason = (result.get("reason") or "").strip()
            # 원문 단정형을 제거하고 추정형으로 재포장
            result["reason"] = (
                f"추정: 변동 시점 윈도우({win_txt}) 내 공개 보도에서 단일 기전을 확정할 수 없음. "
                f"패널 리뷰어(OpenAI·Gemini)가 근거 부족 또는 윈도우 정합성 불일치로 거부함. "
                + (f"1차 분석 요지: {original_reason[:160]}…" if original_reason else "")
            ).strip()
            result["notes"] = (
                (result.get("notes", "") + " · ReviewAgent 거부 — "
                 + verdict2.get("final_verdict", "")).strip(" ·")
            )
            verdict = verdict2

    result["review"] = verdict
    return jsonify(result)


@app.get("/api/domestic/media-leaderboard")
def media_leaderboard():
    """매체 신뢰도 리더보드 조회 (캘리브레이션 날짜 포함)."""
    return jsonify(_mi_agent.get_media_leaderboard())


# ──────────────────────────────────────────────────────────────────────────────
# 매체 신뢰도 캘리브레이션 (분기 1회 권장)
# ──────────────────────────────────────────────────────────────────────────────

@app.post("/api/admin/calibrate-media")
def calibrate_media():
    """
    MediaCalibrator 실행 — 10개 기준 약제로 매체 신뢰도 재측정.
    dry_run=true이면 기사 수집만 하고 저장하지 않음.

    POST /api/admin/calibrate-media
    Body: {"dry_run": false}

    주의: 실행에 5~10분 소요됨 (DuckDuckGo 검색 + GPT-4o 평가).
    """
    import threading

    body    = request.get_json(silent=True) or {}
    dry_run = body.get("dry_run", False)

    def _run():
        from agents.media_calibrator import run_calibration
        try:
            result = run_calibration(dry_run=dry_run)
            logger.info("[Calibrator] 완료: %s", result.get("saved_path", "dry-run"))
            # 완료 후 에이전트 가중치 즉시 갱신
            if not dry_run:
                from agents.market_intelligence import _apply_calibrated_weights
                _apply_calibrated_weights()
        except Exception as e:
            logger.error("[Calibrator] 실패: %s", e, exc_info=True)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    return jsonify({
        "status": "started",
        "message": "캘리브레이션이 백그라운드에서 실행 중입니다. "
                   "완료까지 5~10분 소요됩니다.",
        "dry_run": dry_run,
    })


@app.get("/api/admin/calibration-status")
def calibration_status():
    """가장 최근 캘리브레이션 결과 요약 조회."""
    from agents.media_calibrator import load_latest_calibration
    cal = load_latest_calibration()
    if not cal:
        return jsonify({"status": "미보정", "message": "캘리브레이션 결과 없음"})
    return jsonify({
        "status": "완료",
        "calibrated_at": cal["calibrated_at"],
        "drug_count":    cal["drug_count"],
        "updated_media": len(cal.get("weight_updates", {})),
        "top_media": sorted(
            [
                {"media": name, "new_weight": info["new_weight"],
                 "old_weight": info["old_weight"]}
                for name, info in cal.get("weight_updates", {}).items()
            ],
            key=lambda x: -x["new_weight"]
        )[:5],
    })


@app.get("/dashboard/")
@app.get("/dashboard")
def serve_dashboard_index():
    """메인 대쉬보드 (통합 탭 뷰)."""
    return send_from_directory(str(BASE_DIR / "data" / "dashboard"), "index.html")


@app.get("/dashboard/<path:filename>")
def serve_dashboard(filename: str):
    """대쉬보드 파일 서빙."""
    return send_from_directory(str(BASE_DIR / "data" / "dashboard"), filename)


# ──────────────────────────────────────────────────────────────────────────────
# 해외 약가 검색
# ──────────────────────────────────────────────────────────────────────────────

@app.post("/api/foreign/search")
def foreign_search():
    """
    해외 약가 실시간 검색 (스크레이핑 실행)
    POST /api/foreign/search
    Body: {"query": "Keytruda", "countries": ["JP"], "use_cache": false}

    - use_cache=true: DB에 저장된 이전 결과 반환 (스크레이핑 없음)
    - use_cache=false: 실시간 스크레이핑 후 DB 저장 (기본)
    """
    body = request.get_json(silent=True) or {}
    query = body.get("query", "").strip()
    countries = body.get("countries") or AVAILABLE_COUNTRIES
    use_cache = body.get("use_cache", False)

    if not query:
        return jsonify({"error": "검색어(query)를 입력하세요."}), 400

    # 지원하지 않는 국가 필터링
    unsupported = [c for c in countries if c not in AVAILABLE_COUNTRIES]
    supported = [c for c in countries if c in AVAILABLE_COUNTRIES]

    if use_cache:
        # 캐시된 결과만 반환
        cached = foreign_agent.get_cached_results(query)
        return jsonify({
            "query": query,
            "mode": "cache",
            "results": cached,
            "unsupported_countries": unsupported,
        })

    if not supported:
        return jsonify({
            "error": "현재 구현된 국가가 없습니다.",
            "available": AVAILABLE_COUNTRIES,
            "requested": countries,
        }), 422

    # 실시간 스크레이핑 (async → sync 변환)
    loop = asyncio.new_event_loop()
    try:
        results = loop.run_until_complete(
            foreign_agent.search_all(query, countries=supported)
        )
    except Exception as e:
        logger.error("해외 약가 검색 오류: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500
    finally:
        loop.close()

    return jsonify({
        "query": query,
        "mode": "live",
        "results": results,
        "unsupported_countries": unsupported,
    })


@app.get("/api/foreign/cached")
def foreign_cached():
    """
    DB에 저장된 해외 약가 결과 조회 (스크레이핑 없음)
    GET /api/foreign/cached?q=Keytruda
    """
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "검색어(q)를 입력하세요."}), 400
    cached = foreign_agent.get_cached_results(query)
    # 검색 이력 기록
    try:
        total = sum(len(v) if isinstance(v, list) else 0 for v in cached.values())
        db.log_search(query, "foreign_price", result_count=total)
    except Exception:
        pass
    return jsonify({"query": query, "results": cached})


@app.get("/api/foreign/drugs")
def foreign_drug_list():
    """
    지금까지 검색된 해외 약제 목록 (검색 히스토리 사이드바용).
    GET /api/foreign/drugs
    반환: [{"query_name", "last_searched_at", "country_count", "has_price"}]
    """
    return jsonify(db.get_foreign_drug_list())


@app.get("/api/foreign/available_countries")
def available_countries():
    return jsonify({"available": AVAILABLE_COUNTRIES})


@app.get("/api/search/history")
def search_history():
    """검색 이력 조회. GET /api/search/history?type=hta&limit=20"""
    search_type = request.args.get("type")
    limit = int(request.args.get("limit", 20))
    return jsonify(db.get_search_history(search_type, limit))


@app.get("/api/data/freshness")
def data_freshness():
    """데이터 신선도 조회. GET /api/data/freshness?type=hta&key=belzutifan_FDA"""
    data_type = request.args.get("type", "").strip()
    scope_key = request.args.get("key", "").strip()
    if data_type and scope_key:
        info = db.get_freshness(data_type, scope_key)
        return jsonify(info or {"status": "not_found"})
    # 전체 목록
    with db._connect() as conn:
        rows = conn.execute("SELECT * FROM data_freshness ORDER BY last_fetched DESC").fetchall()
    return jsonify([dict(r) for r in rows])


# ──────────────────────────────────────────────────────────────────────────────
# HIRA 에이전트 (2025.3월 개정 SOP + 외국약가 조정가 검증)
# ──────────────────────────────────────────────────────────────────────────────

from agents.hira_agent import HIRAAgent
_hira_agent = HIRAAgent()


@app.get("/api/hira/pricing-summary")
def hira_pricing_summary():
    """약제결정신청(요양급여 등재) 핵심 조항 요약."""
    try:
        return jsonify(_hira_agent.pricing_application_summary())
    except Exception as e:
        logger.error("HIRA summary 실패: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.get("/api/hira/checklist")
def hira_checklist():
    return jsonify({"items": _hira_agent.submission_checklist()})


@app.get("/api/hira/article")
def hira_article():
    """GET /api/hira/article?label=제3조의2"""
    label = request.args.get("label", "").strip()
    if not label:
        return jsonify({"error": "label 파라미터 필요 (예: 제3조의2)"}), 400
    art = _hira_agent.get_article(label)
    if not art:
        return jsonify({"error": f"조항 없음: {label}"}), 404
    return jsonify({"label": art.label, "title": art.title, "page": art.page, "body": art.body})


@app.get("/api/hira/audit-adjustment")
def hira_audit_adjustment():
    """_resource/산출식.xlsx 의 수식·비율이 규정과 일치하는지 더블체크."""
    return jsonify(_hira_agent.audit_adjustment_excel())


@app.post("/api/hira/compute-a8")
def hira_compute_a8():
    """
    외국약가 → A8 조정가 산출.
    Body: {
      "prices": {"UK": 132.63, "US": 339.46, ...},   // 최소단위당 현지 통화
      "fx_rates": {"UK": 1821.01, ...},              // optional, 기본값 2025.3월 기준
      "subset": ["UK","US","CA","JP","FR","DE","IT","CH"]  // optional, 최저가 산출 대상
    }
    """
    body = request.get_json(silent=True) or {}
    prices = body.get("prices") or {}
    if not prices:
        return jsonify({"error": "prices 는 필수 — {국가코드: 현지가격} 형식"}), 400
    try:
        result = _hira_agent.compute_a8(
            prices_local=prices,
            fx_rates=body.get("fx_rates"),
            subset=body.get("subset"),
        )
        return jsonify(result)
    except Exception as e:
        logger.error("A8 산출 실패: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


# ──────────────────────────────────────────────────────────────────────────────
# 약제명 리졸버 (제품명 ↔ 성분명)
# ──────────────────────────────────────────────────────────────────────────────

# 국내 DB 에 없는 해외 신약용 최소 매핑 (INN ↔ 대표 상품명).
# 확장 필요 시 data/resource/drug_alias.json 으로 분리 가능.
_KNOWN_ALIASES = {
    "belzutifan":    ["welireg"],
    "pembrolizumab": ["keytruda", "키트루다"],
    "nivolumab":     ["opdivo", "옵디보"],
    "atezolizumab":  ["tecentriq", "티쎈트릭"],
    "durvalumab":    ["imfinzi", "임핀지"],
    "lenvatinib":    ["lenvima", "렌비마"],
    "osimertinib":   ["tagrisso", "타그리소"],
    "sotorasib":     ["lumakras", "lumykras"],
    "enfortumab vedotin": ["padcev", "패드세브"],
}
_ALIAS_TO_INGREDIENT = {}
for ing, products in _KNOWN_ALIASES.items():
    for p in products + [ing]:
        _ALIAS_TO_INGREDIENT[p.lower()] = ing


def _resolve_drug(query: str) -> dict:
    """입력(제품명·성분명·한글·영문)을 성분명 + 제품명 리스트로 해석."""
    q = (query or "").strip()
    if not q:
        return {"query": q, "ingredient": "", "products": [], "source": "empty"}

    # 1) 국내 DB drug_latest 에서 ingredient 검색
    try:
        import sqlite3 as _sqlite3
        like = f"%{q}%"
        with _sqlite3.connect(str(db.db_path)) as c:
            cur = c.execute(
                "SELECT DISTINCT ingredient, product_name_kr, product_name_en "
                "FROM drug_latest "
                "WHERE ingredient LIKE ? OR product_name_kr LIKE ? OR product_name_en LIKE ? "
                "LIMIT 20",
                (like, like, like),
            )
            rows = cur.fetchall()
        if rows:
            ingredients = [r[0] for r in rows if r[0]]
            products = [p for r in rows for p in (r[1], r[2]) if p]
            from collections import Counter
            top_ing = Counter(ingredients).most_common(1)[0][0] if ingredients else ""
            # DB에서 성분명을 못 찾았으면 alias 맵에서 product→ingredient 역매핑 시도
            if not top_ing:
                for prod in products + [q]:
                    ing = _ALIAS_TO_INGREDIENT.get(str(prod).lower().split("(")[0].strip())
                    if ing:
                        top_ing = ing
                        break
            return {
                "query": q,
                "ingredient": top_ing,
                "products": list(dict.fromkeys(products))[:10],
                "source": "domestic_db" + ("+alias" if top_ing and not ingredients else ""),
            }
    except Exception as e:
        logger.warning("resolve domestic lookup 실패: %s", e)

    # 2) 알려진 INN↔상품 매핑
    ing = _ALIAS_TO_INGREDIENT.get(q.lower())
    if ing:
        return {
            "query": q,
            "ingredient": ing,
            "products": _KNOWN_ALIASES.get(ing, []),
            "source": "alias_map",
        }

    # 3) fallback — 입력을 성분명으로 간주
    return {"query": q, "ingredient": q, "products": [], "source": "fallback"}


@app.get("/api/drug/resolve")
def drug_resolve():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"error": "q 는 필수"}), 400
    return jsonify(_resolve_drug(q))


# ──────────────────────────────────────────────────────────────────────────────
# HTA 허가현황 (PBAC/CADTH/NICE/SMC)
# ──────────────────────────────────────────────────────────────────────────────

from agents.hta_approval_agent import HTAApprovalAgent
_hta_agent = HTAApprovalAgent()


@app.get("/api/hta/approvals")
def hta_approvals():
    """GET /api/hta/approvals?drug=belzutifan[&body=SMC][&refresh=1]"""
    drug = (request.args.get("drug") or "").strip()
    if not drug:
        return jsonify({"error": "drug 는 필수"}), 400
    body = (request.args.get("body") or "").strip().upper() or None
    refresh = request.args.get("refresh") in ("1", "true", "True")
    try:
        results = _hta_agent.get(drug, body=body, force_refresh=refresh)
        return jsonify({
            "drug": drug,
            "available_bodies": _hta_agent.available_bodies(),
            "count": len(results),
            "results": results,
        })
    except Exception as e:
        logger.error("HTA 조회 실패: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.get("/api/hta/pdf")
def hta_pdf():
    """GET /api/hta/pdf?path=<pdf_local 절대경로>  — 캐시된 평가 PDF 다운로드"""
    from flask import send_file, abort
    p = request.args.get("path") or ""
    if not p:
        abort(400)
    fp = Path(p).resolve()
    cache_root = (Path(__file__).parent.parent / "data" / "hta_cache").resolve()
    if not str(fp).startswith(str(cache_root)) or not fp.exists():
        abort(404)
    return send_file(str(fp), mimetype="application/pdf", as_attachment=False, download_name=fp.name)


@app.get("/api/hta/indication-matrix")
def hta_indication_matrix():
    """GET /api/hta/indication-matrix?drug=belzutifan[&refresh=1]

    FDA 적응증을 축으로 PBAC/CADTH/NICE/SMC 평가를 매칭한 매트릭스 반환.
    캐시 데이터가 있으면 즉시 반환 (refresh=1 시에만 재수집).
    """
    drug = (request.args.get("drug") or "").strip()
    if not drug:
        return jsonify({"error": "drug 는 필수"}), 400
    refresh = request.args.get("refresh") in ("1", "true", "True")
    try:
        matrix = _hta_agent.get_indication_matrix(drug, force_refresh=refresh)
        # 검색 이력 + 신선도 기록
        try:
            n_ind = len(matrix.get("indications", []))
            db.log_search(drug, "hta", result_count=n_ind)
            if n_ind > 0:
                for body in ["FDA", "PBAC", "CADTH", "NICE", "SMC"]:
                    db.update_freshness("hta", f"{drug}_{body}")
        except Exception:
            pass
        return jsonify(matrix)
    except Exception as e:
        logger.error("Indication matrix 조회 실패: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


# ──────────────────────────────────────────────────────────────────────────────
# Regulatory Approval Matrix (6-agency)
# ──────────────────────────────────────────────────────────────────────────────

from agents.foreign_approval import ForeignApprovalAgent
_approval_agent = ForeignApprovalAgent()

@app.get("/api/approval/matrix")
def approval_matrix():
    """GET /api/approval/matrix?product=keytruda"""
    product = (request.args.get("product") or "").strip().lower()
    if not product:
        return jsonify({"error": "product 는 필수"}), 400
    try:
        m = _approval_agent.matrix(product)
        return jsonify(m)
    except Exception as e:
        logger.error("Approval matrix 조회 실패: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


_TRANSLATE_CACHE_DIR = BASE_DIR / "data" / "hta_cache" / "translations"
_TRANSLATE_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _translate_ja_to_ko(text: str) -> str:
    """Gemini 2.5-flash 로 일본어 → 한국어 번역 (파일 캐시)."""
    if not text or not text.strip():
        return text
    import hashlib, json as _json, urllib.request, ssl, os
    cache_key = hashlib.md5(text.encode()).hexdigest()
    cache_file = _TRANSLATE_CACHE_DIR / f"ja_ko_{cache_key}.txt"
    if cache_file.exists():
        return cache_file.read_text("utf-8")

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return text

    body = {
        "contents": [{"parts": [{"text":
            f"다음 일본어 의약품 허가사항을 한국어로 번역하세요. 의약품/질환 전문용어를 정확히 사용하세요. 번역문만 출력하세요.\n\n{text}"
        }]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 1024},
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    try:
        req = urllib.request.Request(url, data=_json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            payload = _json.loads(resp.read().decode("utf-8"))
        translated = (payload.get("candidates", [{}])[0]
                      .get("content", {}).get("parts", [{}])[0]
                      .get("text", "")).strip()
        if translated:
            cache_file.write_text(translated, "utf-8")
            return translated
    except Exception as e:
        logger.warning("번역 실패: %s", e)
    return text


def _is_japanese(text: str) -> bool:
    if not text:
        return False
    for ch in text[:100]:
        if '\u3040' <= ch <= '\u30ff' or '\u4e00' <= ch <= '\u9fff':
            return True
    return False


@app.get("/api/approval/detail")
def approval_detail():
    """GET /api/approval/detail?id=keytruda_nsclc_1l_metastatic_chemo"""
    ind_id = (request.args.get("id") or "").strip()
    if not ind_id:
        return jsonify({"error": "id 는 필수"}), 400
    try:
        rec = db.get_indication(ind_id)
        if not rec:
            return jsonify({"error": "not found"}), 404
        product = rec.get("product")
        initial_auth: dict[str, str] = {}
        if product:
            with db._connect() as conn:
                for row in conn.execute(
                    "SELECT a.agency, MIN(a.approval_date) "
                    "FROM indications_by_agency a "
                    "JOIN indications_master m ON m.indication_id = a.indication_id "
                    "WHERE m.product = ? AND a.approval_date IS NOT NULL "
                    "GROUP BY a.agency",
                    (product,),
                ):
                    initial_auth[row[0]] = row[1]
        for a in (rec.get("agencies") or []):
            ag = a.get("agency")
            a["initial_auth_date"] = initial_auth.get(ag)
            if ag == "PMDA":
                excerpt = a.get("label_excerpt") or ""
                if _is_japanese(excerpt):
                    a["label_excerpt_original"] = excerpt
                    a["label_excerpt"] = _translate_ja_to_ko(excerpt)
                combo = a.get("combination_label") or ""
                if _is_japanese(combo):
                    a["combination_label_original"] = combo
                    a["combination_label"] = _translate_ja_to_ko(combo)
        return jsonify(rec)
    except Exception as e:
        logger.error("Approval detail 조회 실패: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.get("/api/approval/products")
def approval_products():
    """등록된 product 목록 + 간단한 통계."""
    try:
        with db._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT product FROM indications_master ORDER BY product"
            ).fetchall()
        products = []
        for r in rows:
            slug = r[0]
            m = _approval_agent.matrix(slug)
            products.append({
                "product": slug,
                "masters": m["totals"]["masters"],
                "agencies": {
                    "FDA": m["totals"]["fda_agency"],
                    "EMA": m["totals"]["ema_agency"],
                    "PMDA": m["totals"]["pmda_agency"],
                    "MFDS": m["totals"]["mfds_agency"],
                    "MHRA": m["totals"]["mhra_agency"],
                    "TGA": m["totals"]["tga_agency"],
                },
                "all_six": m["totals"]["all_six"],
            })
        products.sort(key=lambda p: p["masters"], reverse=True)
        return jsonify({"products": products})
    except Exception as e:
        logger.error("Approval products 조회 실패: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


# ──────────────────────────────────────────────────────────────────────────────
# Negotiation Workbench (Phase 1 MVP)
# ──────────────────────────────────────────────────────────────────────────────

from datetime import datetime
from flask import send_file
from agents.workbench import (
    DEFAULT_ASSUMPTIONS,
    compute_all_scenarios,
    export_workbook,
    list_available_products,
    load_assumptions,
    load_hta_for_product,
    save_assumptions,
    summarize_hta,
)


@app.get("/api/workbench/assumptions")
def workbench_assumptions_get():
    """현재 가정치 (없으면 DEFAULT) 반환. 설정 화면용."""
    return jsonify(load_assumptions())


@app.put("/api/workbench/assumptions")
def workbench_assumptions_put():
    """가정치 전체 저장. Body: assumptions dict 전체."""
    body = request.get_json(silent=True) or {}
    if not body or "countries" not in body:
        return jsonify({"error": "countries 키 필수"}), 400
    save_assumptions(body, user=body.get("_user", "dashboard"))
    return jsonify({"ok": True, "saved": load_assumptions()})


@app.get("/api/workbench/defaults")
def workbench_defaults():
    """HIRA 고시 기본값 (복원용)."""
    return jsonify(DEFAULT_ASSUMPTIONS)


@app.get("/api/workbench/hta")
def workbench_hta():
    """
    제품별 Tier-3 HTA 교차검증 캐시 조회.
    Query: ?product=keytruda&summary=1 (summary=1 이면 요약만 반환)

    Returns:
      - full=True:  {"data": {nice:..., pbac:..., has:..., gba:...}, "summary": {...}}
      - full=False: {"summary": {...}}
    """
    product = request.args.get("product", "").strip()
    only_summary = request.args.get("summary") in ("1", "true")

    if not product:
        # 제품 인자 없으면 사용 가능한 목록 반환
        return jsonify({
            "available_products": list_available_products(),
            "hint": "?product=keytruda",
        })

    data = load_hta_for_product(product)
    if data is None:
        return jsonify({
            "error": f"제품 '{product}' 의 HTA 캐시 없음",
            "available_products": list_available_products(),
        }), 404

    summary = summarize_hta(data)
    if only_summary:
        return jsonify({"product": product, "summary": summary})
    return jsonify({"product": product, "data": data, "summary": summary})


@app.post("/api/workbench/compute")
def workbench_compute():
    """
    시나리오 병렬 계산 + (옵션) dose 정규화.

    Body: {
      "prices":       {"JP": 88300, "IT": 1200, ...},   # 국가별 현지가격
      "rows_meta":    {country: {product_name, strength, pack, form}}  (선택)
      "product_slug": "keytruda"                         (선택, REFERENCE_SKU 폴백용)
      "reference_mg": 100                                (선택, 기준 mg)
      "scenarios":    [...],
      "assumptions":  {...}                              (선택)
    }

    rows_meta 가 있으면 국가별 SKU 의 strength/pack 을 파싱해 equivalent_price
    (reference_mg 기준 환산가) 를 계산 후 A8 비교. 없으면 raw local_price 비교.
    응답의 각 시나리오 rows[country] 에 mg_pack_total/price_per_mg/dose_confidence 추가,
    excluded dict 에 동등비교 불가 국가와 사유 표기.
    """
    body = request.get_json(silent=True) or {}
    prices = body.get("prices") or {}
    if not prices:
        return jsonify({"error": "prices 필수"}), 400
    scenarios = body.get("scenarios") or []
    if not scenarios:
        return jsonify({"error": "scenarios 필수 (최소 1개)"}), 400
    assumptions = body.get("assumptions") or load_assumptions()
    rows_meta    = body.get("rows_meta")
    product_slug = body.get("product_slug")
    reference_mg = body.get("reference_mg")

    try:
        results = compute_all_scenarios(
            prices, scenarios, assumptions,
            rows_meta=rows_meta,
            product_slug=product_slug,
            reference_mg=reference_mg,
        )
        # HTA 캐시 자동 attach (있으면 summary, 없으면 null)
        hta_summary = None
        if product_slug:
            hta_data = load_hta_for_product(product_slug)
            if hta_data:
                hta_summary = summarize_hta(hta_data)
        return jsonify({"scenarios": results, "hta_summary": hta_summary})
    except Exception as e:
        logger.error("workbench compute 실패: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.post("/api/workbench/export")
def workbench_export():
    """
    세션 전체 → xlsx 생성 후 다운로드.
    Body: {
      "project":   {...},
      "prices":    {...},
      "scenarios": [...],   # compute 결과 그대로
      "selected":  "B안",
      "source_raw": [...],
      "matching":  [...],
      "hta":       [...] | null,
      "audit_log": [...],
    }
    """
    body = request.get_json(silent=True) or {}
    if not body.get("scenarios"):
        return jsonify({"error": "scenarios 필수"}), 400

    session = dict(body)
    session.setdefault("assumptions", load_assumptions())

    # 파일명
    proj = session.get("project", {})
    drug = (proj.get("drug_name_en") or proj.get("drug_name_kr") or "product").replace(" ", "_").replace("(", "").replace(")", "")

    # HTA 자동 로드 — 클라이언트가 보내지 않았거나 dict 가 아닌 경우 캐시에서 attach
    if not isinstance(session.get("hta"), dict):
        for key in ("drug_name_en", "drug_name_kr"):
            cached = load_hta_for_product(proj.get(key, ""))
            if cached:
                session["hta"] = cached
                break
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out_path = BASE_DIR / "data" / "workbench" / "exports" / f"MA_A8_Workbench_{drug}_{stamp}.xlsx"

    try:
        export_workbook(session, out_path)
        return send_file(
            str(out_path),
            as_attachment=True,
            download_name=out_path.name,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except Exception as e:
        logger.error("workbench export 실패: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


# ──────────────────────────────────────────────────────────────────────────────
# 헬스체크
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "available_countries": AVAILABLE_COUNTRIES})


if __name__ == "__main__":
    logger.info("대쉬보드 API 서버 시작: http://127.0.0.1:5001")
    app.run(host="127.0.0.1", port=5001, debug=False)
