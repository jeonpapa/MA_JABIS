"""
MediaCalibrator — 한국 의약전문 매체 신뢰도 분기별 자동 보정

동작 방식:
  1. 10개 기준 약제(키트루다·아토젯·자누비아 포함)를 대상으로
     각 매체에서 실제 약가 변동 기사를 수집
  2. GPT-4o가 각 기사를 4개 축으로 평가
     ① 기전 특정성(Specificity): 약가 변동 기전을 구체적으로 설명하는가
     ② 시의성(Timeliness):       변동 시점과 가까운 시기에 보도했는가
     ③ 맥락 풍부성(Context):     배경·의미·파급 효과를 함께 다루는가
     ④ MA 인사이트(MA Depth):    Market Access 관점의 심층 분석인가
  3. 매체별 평균 점수를 산출 → 기존 기본값과 가중 평균으로 업데이트
  4. 결과를 data/dashboard/media_calibration/calibration_{date}.json에 저장
  5. MarketIntelligenceAgent가 다음 실행 시 최신 캘리브레이션 자동 로드

실행:
  python agents/media_calibrator.py             # 전체 보정 실행
  python agents/media_calibrator.py --dry-run   # 수집만 하고 저장 안 함
  python agents/media_calibrator.py --report    # 최근 보정 결과 출력
"""

import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
# 프로젝트 루트를 sys.path에 추가 (CLI 직접 실행 시 필요)
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
CALIB_DIR = BASE_DIR / "data" / "dashboard" / "media_calibration"
CALIB_DIR.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
# 1) 기준 약제 10종 (키트루다·아토젯·자누비아 필수 포함)
#    선정 기준: 다양한 약가 변동 기전을 커버하고 보도량이 충분한 제품
# ─────────────────────────────────────────────────────────────────────────────

CALIBRATION_DRUGS = [
    # 필수 3종
    {
        "name_ko": "키트루다",
        "ingredient": "펨브롤리주맙",
        "reason": "적응증 확대 + 사용량-연동 인하 대표 사례. 보도량 최다",
    },
    {
        "name_ko": "아토젯",
        "ingredient": "에제티미브+아토르바스타틴",
        "reason": "복합제 특허 만료 + 제네릭 진입 후 오리지널 인하 사례",
    },
    {
        "name_ko": "자누비아",
        "ingredient": "시타글립틴",
        "reason": "특허 만료 후 제네릭 대거 등재로 인한 오리지널 약가 인하",
    },
    # 추가 7종 (다양한 기전·약효군 커버)
    {
        "name_ko": "옵디보",
        "ingredient": "니볼루맙",
        "reason": "PD-1 면역항암제. 키트루다와 함께 사용량-연동 보도 빈번",
    },
    {
        "name_ko": "허셉틴",
        "ingredient": "트라스투주맙",
        "reason": "바이오시밀러 다수 등재 → 오리지널 가격 인하. 특허만료 기전 대표",
    },
    {
        "name_ko": "자렐토",
        "ingredient": "리바록사반",
        "reason": "항응고제. 특허 만료 후 제네릭 경쟁으로 인한 급격한 약가 인하",
    },
    {
        "name_ko": "타그리소",
        "ingredient": "오시머티닙",
        "reason": "3세대 EGFR 억제제. 적응증 확대(1차치료) 후 급여 재협상 사례",
    },
    {
        "name_ko": "아바스틴",
        "ingredient": "베바시주맙",
        "reason": "바이오시밀러 4종 이상 등재. 실거래가 연동 및 특허 만료 기전",
    },
    {
        "name_ko": "엔브렐",
        "ingredient": "에타너셉트",
        "reason": "류마티스 생물학적 제제. 바이오시밀러 시장 경쟁 전형적 사례",
    },
    {
        "name_ko": "자누메트",
        "ingredient": "시타글립틴+메트포르민",
        "reason": "자누비아 복합제. 성분특허 만료 + 복합제 제네릭 등재 패턴",
    },
]

# ─────────────────────────────────────────────────────────────────────────────
# 2) 기사 수집 (media_intelligence_agent의 검색 함수 재활용)
# ─────────────────────────────────────────────────────────────────────────────

_DDG_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Referer": "https://duckduckgo.com/",
}


def _ddg_search(query: str, max_results: int = 5) -> list:
    """DuckDuckGo HTML 검색."""
    from urllib.parse import unquote
    try:
        resp = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query, "kl": "kr-kr"},
            headers=_DDG_HEADERS,
            timeout=12,
        )
        results = []
        for m in re.finditer(
            r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>(.+?)</a>.*?'
            r'<a[^>]+class="result__snippet"[^>]*>(.+?)</a>',
            resp.text, re.DOTALL
        ):
            url_raw = m.group(1)
            title   = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            snippet = re.sub(r"<[^>]+>", "", m.group(3)).strip()
            uddg = re.search(r"uddg=([^&]+)", url_raw)
            url = unquote(uddg.group(1)) if uddg else url_raw
            if title:
                results.append({"title": title, "url": url, "snippet": snippet})
            if len(results) >= max_results:
                break
        return results
    except Exception as e:
        logger.warning("DDG 검색 실패: %s", e)
        return []


def _extract_domain(url: str) -> str:
    m = re.search(r"https?://(?:www\.)?([^/]+)", url or "")
    return m.group(1) if m else ""


def collect_articles_for_drug(drug: dict) -> list:
    """
    약제 1종에 대해 약가 변동 관련 기사 수집.
    site: 필터 없이 단순 키워드 검색 → 도메인으로 사후 채점.
    """
    name      = drug["name_ko"]
    short_ing = drug["ingredient"].split("+")[0].strip()

    queries = [
        f"{name} 약가 인하",
        f"{name} 약가 변동",
        f"{name} 급여 보험 약가",
        f"{short_ing} 약가 인하 한국",
        f"{name} 건강보험 급여",
    ]
    seen: set = set()
    articles = []
    for q in queries:
        for r in _ddg_search(q, max_results=6):
            if r["url"] not in seen:
                seen.add(r["url"])
                r["drug"]   = name
                r["domain"] = _extract_domain(r["url"])
                articles.append(r)
        time.sleep(0.5)
    return articles


# ─────────────────────────────────────────────────────────────────────────────
# 3) GPT-4o 기사 품질 평가
# ─────────────────────────────────────────────────────────────────────────────

EVAL_SYSTEM = """
당신은 한국 의약전문 저널리즘의 품질을 평가하는 전문가입니다.
아래 약가 변동 관련 기사 목록을 읽고, 각 기사를 4개 축으로 1~5점 평가하세요.

평가 축:
1. specificity (기전 특정성, 1~5)
   - 5: 사용량-연동, 적응증 확대, 특허 만료, 실거래가 등 구체적 기전 명시
   - 3: 약가 인하 사실은 전달하나 기전 설명 불충분
   - 1: 단순 약가 수치만 나열, 이유 없음

2. timeliness (시의성, 1~5)
   - 5: 고시 발표 당일~1주 내 보도, 독자적 취재
   - 3: 수주 후 후속 보도
   - 1: 이미 알려진 사실의 후행 정리

3. context (맥락 풍부성, 1~5)
   - 5: 환자 영향, 시장 파급, 제약사 입장, 향후 전망 포함
   - 3: 사실 중심, 일부 배경 포함
   - 1: 수치/날짜만 나열

4. ma_depth (Market Access 인사이트, 1~5)
   - 5: 급여 협상 과정, 건보공단-제약사 협상 배경, 보험 재정 영향까지 다룸
   - 3: 급여 관련 언급 있으나 피상적
   - 1: MA 관련 내용 없음

출력 형식 (JSON 배열, 기사 순서 유지):
[
  {
    "idx": 0,
    "specificity": 0,
    "timeliness": 0,
    "context": 0,
    "ma_depth": 0,
    "comment": "한 줄 평"
  },
  ...
]
"""


def _load_openai_key() -> None:
    env_path = BASE_DIR / "config" / ".env"
    if env_path.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_path, override=False)
        except ImportError:
            pass


def evaluate_articles_batch(articles: list) -> list:
    """
    기사 목록을 GPT-4o에 보내 품질 점수 반환.
    articles: [{"title", "url", "snippet", "domain", "drug"}, ...]
    반환: 위와 동일 + {"specificity","timeliness","context","ma_depth","comment"}
    """
    if not articles:
        return []

    _load_openai_key()
    try:
        from openai import OpenAI
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
    except Exception as e:
        logger.warning("OpenAI 로드 실패: %s", e)
        return articles

    # 배치 크기 제한 (한 번에 최대 15개)
    batch_size = 15
    evaluated = []
    for start in range(0, len(articles), batch_size):
        batch = articles[start:start + batch_size]
        art_text = "\n".join(
            f"[{i}] 약제:{a['drug']} | 매체:{a['domain']}\n"
            f"    제목: {a['title']}\n"
            f"    요약: {a.get('snippet','')[:200]}"
            for i, a in enumerate(batch)
        )
        try:
            resp = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": EVAL_SYSTEM},
                    {"role": "user",   "content": art_text},
                ],
                temperature=0.1,
                max_tokens=2000,
            )
            raw = resp.choices[0].message.content.strip()
            if "```" in raw:
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            scores = json.loads(raw)
            for item in scores:
                idx = item.get("idx", 0)
                if 0 <= idx < len(batch):
                    enriched = dict(batch[idx])
                    enriched.update({
                        "specificity": item.get("specificity", 3),
                        "timeliness":  item.get("timeliness",  3),
                        "context":     item.get("context",     3),
                        "ma_depth":    item.get("ma_depth",    3),
                        "comment":     item.get("comment",     ""),
                    })
                    evaluated.append(enriched)
        except Exception as e:
            logger.warning("GPT-4o 평가 실패 (배치 %d~): %s", start, e)
            evaluated.extend(batch)   # 평가 없이 그대로
        time.sleep(1.0)

    return evaluated


# ─────────────────────────────────────────────────────────────────────────────
# 4) 매체별 점수 집계 → 가중치 산출
# ─────────────────────────────────────────────────────────────────────────────

# 평가 축별 MA 관련성 가중치 (ma_depth를 가장 중시)
SCORE_WEIGHTS = {
    "specificity": 0.30,
    "timeliness":  0.20,
    "context":     0.20,
    "ma_depth":    0.30,
}

# 새 점수와 기존 기본값의 혼합 비율 (안정성 유지)
# blend = base_weight * (1 - ALPHA) + new_score * ALPHA
ALPHA = 0.35   # 새 평가가 35% 반영, 기존이 65% 유지


def aggregate_scores(evaluated: list) -> dict:
    """
    평가된 기사들을 도메인별로 집계 → 도메인별 평균 점수.
    반환: {"domain": {"avg_score": float, "article_count": int, "raw": {...}}}
    """
    from collections import defaultdict
    by_domain: dict = defaultdict(list)
    for a in evaluated:
        if "specificity" in a:
            by_domain[a["domain"]].append(a)

    result = {}
    for domain, arts in by_domain.items():
        n = len(arts)
        raw = {ax: sum(a.get(ax, 3) for a in arts) / n for ax in SCORE_WEIGHTS}
        avg = sum(raw[ax] * SCORE_WEIGHTS[ax] for ax in SCORE_WEIGHTS)
        result[domain] = {
            "avg_score":     round(avg, 3),
            "article_count": n,
            "raw":           {ax: round(v, 2) for ax, v in raw.items()},
        }
    return result


def compute_new_weights(domain_scores: dict, current_db: dict) -> dict:
    """
    현재 MEDIA_DB의 기본 가중치와 새 평가 점수를 ALPHA로 혼합.
    반환: {"media_name": {"old_weight": float, "new_weight": float, "domain": str}}
    """
    # domain → media_name 역조회
    domain_to_name = {info["domain"]: name for name, info in current_db.items()}
    updates = {}

    for domain, score_info in domain_scores.items():
        media_name = domain_to_name.get(domain)
        if not media_name:
            logger.info("미등록 도메인, 스킵: %s", domain)
            continue

        old_w = current_db[media_name]["weight"]
        # 5점 만점 점수를 0~3.5 범위로 정규화
        norm_score = score_info["avg_score"] / 5.0 * 3.5
        new_w = round(old_w * (1 - ALPHA) + norm_score * ALPHA, 3)
        # 범위 제한: 0.3 ~ 3.5
        new_w = max(0.3, min(3.5, new_w))

        updates[media_name] = {
            "domain":      domain,
            "old_weight":  old_w,
            "new_weight":  new_w,
            "avg_score":   score_info["avg_score"],
            "article_count": score_info["article_count"],
            "raw":         score_info["raw"],
        }

    return updates


# ─────────────────────────────────────────────────────────────────────────────
# 5) 캘리브레이션 저장 / 로드
# ─────────────────────────────────────────────────────────────────────────────

def save_calibration(weight_updates: dict, domain_scores: dict) -> Path:
    """캘리브레이션 결과를 JSON으로 저장하고 경로 반환."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = CALIB_DIR / f"calibration_{ts}.json"
    data = {
        "calibrated_at": datetime.now().isoformat(),
        "alpha":         ALPHA,
        "drug_count":    len(CALIBRATION_DRUGS),
        "weight_updates": weight_updates,
        "domain_scores":  domain_scores,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info("[Calibrator] 저장 완료: %s", path)
    return path


def load_latest_calibration() -> Optional[dict]:
    """가장 최근 캘리브레이션 파일을 로드."""
    files = sorted(CALIB_DIR.glob("calibration_*.json"), reverse=True)
    if not files:
        return None
    with open(files[0], encoding="utf-8") as f:
        return json.load(f)


def get_calibrated_weights() -> Optional[dict]:
    """
    최신 캘리브레이션에서 {media_name: new_weight} 매핑 반환.
    없으면 None.
    """
    cal = load_latest_calibration()
    if not cal:
        return None
    return {
        name: info["new_weight"]
        for name, info in cal.get("weight_updates", {}).items()
    }


# ─────────────────────────────────────────────────────────────────────────────
# 6) 메인 캘리브레이션 실행
# ─────────────────────────────────────────────────────────────────────────────

def run_calibration(dry_run: bool = False) -> dict:
    """
    전체 캘리브레이션 파이프라인 실행.
    반환: {"weight_updates": {...}, "domain_scores": {...}, "saved_path": str}
    """
    from agents.market_intelligence_agent import MEDIA_DB

    logger.info("=== MediaCalibrator 시작 (%d개 약제) ===", len(CALIBRATION_DRUGS))

    # Step 1: 모든 약제 기사 수집
    all_articles = []
    for i, drug in enumerate(CALIBRATION_DRUGS):
        logger.info("[%d/%d] %s 기사 수집 중...", i + 1, len(CALIBRATION_DRUGS), drug["name_ko"])
        arts = collect_articles_for_drug(drug)
        logger.info("  → %d건 수집", len(arts))
        all_articles.extend(arts)
        time.sleep(0.5)

    logger.info("총 수집 기사: %d건", len(all_articles))

    if dry_run:
        logger.info("[dry-run] 평가·저장 생략")
        # 수집 결과만 반환
        domain_counts: dict = {}
        for a in all_articles:
            d = a.get("domain", "unknown")
            domain_counts[d] = domain_counts.get(d, 0) + 1
        return {"dry_run": True, "total_articles": len(all_articles),
                "domain_counts": domain_counts}

    # Step 2: GPT-4o 품질 평가
    logger.info("GPT-4o 기사 품질 평가 중... (%d건)", len(all_articles))
    evaluated = evaluate_articles_batch(all_articles)

    # Step 3: 집계
    domain_scores = aggregate_scores(evaluated)
    logger.info("도메인 평가 완료: %d개 도메인", len(domain_scores))

    # Step 4: 가중치 업데이트 계산
    weight_updates = compute_new_weights(domain_scores, MEDIA_DB)
    for name, upd in weight_updates.items():
        delta = upd["new_weight"] - upd["old_weight"]
        logger.info(
            "  %-28s  %.2f → %.2f  (Δ%+.2f)  기사:%d건",
            name, upd["old_weight"], upd["new_weight"], delta, upd["article_count"]
        )

    # Step 5: 저장
    saved_path = save_calibration(weight_updates, domain_scores)

    return {
        "total_articles": len(all_articles),
        "evaluated_count": len(evaluated),
        "domain_count":  len(domain_scores),
        "weight_updates": weight_updates,
        "domain_scores":  domain_scores,
        "saved_path":    str(saved_path),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 7) 보고서 출력
# ─────────────────────────────────────────────────────────────────────────────

def print_report() -> None:
    """가장 최근 캘리브레이션 결과를 사람이 읽기 좋은 형식으로 출력."""
    cal = load_latest_calibration()
    if not cal:
        print("캘리브레이션 결과 없음. 먼저 python media_calibrator.py 실행 필요.")
        return

    print("\n" + "=" * 80)
    print(f"MediaCalibrator 최근 결과  ({cal['calibrated_at'][:16]})")
    print(f"평가 약제: {cal['drug_count']}개  |  혼합 비율(α): {cal['alpha']}")
    print("=" * 80)
    print(f"{'매체':<28} {'기존':>5}  {'신규':>5}  {'변화':>6}  "
          f"{'기사수':>5}  {'특정성':>5}  {'맥락':>5}  {'MA심도':>6}")
    print("-" * 80)

    for name, upd in sorted(cal["weight_updates"].items(),
                             key=lambda x: -x[1]["new_weight"]):
        delta = upd["new_weight"] - upd["old_weight"]
        raw   = upd.get("raw", {})
        print(
            f"{name:<28} {upd['old_weight']:>5.2f}  {upd['new_weight']:>5.2f}  "
            f"{delta:>+6.2f}  {upd['article_count']:>5}  "
            f"{raw.get('specificity',0):>5.2f}  {raw.get('context',0):>5.2f}  "
            f"{raw.get('ma_depth',0):>6.2f}"
        )
    print("=" * 80 + "\n")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if "--report" in sys.argv:
        print_report()
        sys.exit(0)

    dry_run = "--dry-run" in sys.argv
    result  = run_calibration(dry_run=dry_run)

    if dry_run:
        print("\n[dry-run 결과] 수집 기사만 확인")
        print(f"총 기사: {result['total_articles']}건")
        print("도메인별 수집 건수:")
        for domain, cnt in sorted(result["domain_counts"].items(),
                                   key=lambda x: -x[1]):
            print(f"  {domain:<40} {cnt}건")
    else:
        print_report()
        print(f"\n저장 완료: {result['saved_path']}")
