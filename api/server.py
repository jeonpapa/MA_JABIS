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
from agents.market_intelligence_agent import MarketIntelligenceAgent, MI_RULES_TEXT
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
            # 재시도 후에도 거부 — 명시적 unknown/low 로 하향하여 반환
            logger.info("[Review] 재시도 거부 — unknown 하향")
            result = retry
            result["mechanism"] = "unknown"
            result["mechanism_label"] = "미분류"
            result["confidence"] = "low"
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
                from agents.market_intelligence_agent import _apply_calibrated_weights
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


# ──────────────────────────────────────────────────────────────────────────────
# 헬스체크
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "available_countries": AVAILABLE_COUNTRIES})


if __name__ == "__main__":
    logger.info("대쉬보드 API 서버 시작: http://127.0.0.1:5001")
    app.run(host="127.0.0.1", port=5001, debug=False)
