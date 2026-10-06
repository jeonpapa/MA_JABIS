"""미디어 인텔리전스 — Home 위젯 / Daily Mailing / CompetitorTrends 의 공통 데이터 소스.

- 네이버 뉴스 API 로 1개월 트래픽(기사 건수) + 최신뉴스 집계
- 일자별 cache (`data/cache/naver/YYYY-MM-DD.json`) — 하루 1회만 실제 호출
- 트래픽 기준: 기사 건수 (리뷰어 상의 필요 — 후속 버전에서 tier weight 가중 가능)
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from agents.naver_news import aggregate_brand_traffic, get_client

BASE_DIR = Path(__file__).parent.parent
CACHE_DIR = BASE_DIR / "data" / "cache" / "naver"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger(__name__)

# Home 브랜드 트래픽 기본 창 — 14일 (최근 7일 표시 + 이전 7일 상승 비교 기준).
# A2(2026-07-06): '지난 1개월' → 14일. 스파크라인/상승률은 프런트가 last7 vs prev7 로 계산.
DEFAULT_TRAFFIC_DAYS = 14


def days_in_last_month() -> int:
    """오늘 기준 '지난 1개월' 을 일 단위로 환산.

    예) 2026-04-18 → 2026-03-18 → 31일.
    월 길이가 달라 28~31 사이 값 반환.
    """
    today = datetime.now().date()
    y, m, d = today.year, today.month, today.day
    if m == 1:
        prev_y, prev_m = y - 1, 12
    else:
        prev_y, prev_m = y, m - 1
    import calendar
    prev_month_last = calendar.monthrange(prev_y, prev_m)[1]
    prev_day = min(d, prev_month_last)
    prev_date = datetime(prev_y, prev_m, prev_day).date()
    return (today - prev_date).days

# Home 기본 모니터링 브랜드 — MSD 포트폴리오 + 경쟁사 핵심 + 시장 관심 브랜드
# DB화(home_brand 테이블, agents.editable_factors) 의 seed 값 + 폴백 상수.
# 실제 조회는 get_brand_traffic() 이 agents.editable_factors.get_home_brands() 를 통해
# home_brand(active=1) 를 우선 사용하고, DB 가 비어있거나 접근 불가하면 이 상수로 폴백.
# admin CRUD: /api/admin/home-brands (활성화/비활성화/추가/삭제).
DEFAULT_BRANDS = [
    "키트루다", "렌비마", "자누비아", "가다실", "프로리아",
    "옵디보", "타그리소", "임핀지", "테쎈트릭",
    "레블리미드", "다잘렉스", "린파자",
]


def looks_poisoned(data: dict) -> bool:
    """캐시로 서빙하면 안 되는 집계 모양.

    - `error` 키가 있다 (수집 실패가 섞임), 또는
    - 브랜드가 있는데 전부 total_count 0 — 2026-10-01 에 소켓이 막힌 스모크가 만든 파일의 모양.
      12개 브랜드(키트루다 포함) 14일 전부 0건은 실제로 일어나지 않는다; 구버전(실패를 [] 로 삼키던) 파일이 배포 뒤에도
      남아 자정까지 서빙되는 것을 막는다. 브랜드 0개(모니터링 목록 비어 있음)는 정상.
    """
    if not isinstance(data, dict) or data.get("error"):
        return True
    brands = data.get("brands") or []
    return bool(brands) and all(int(b.get("total_count") or 0) == 0 for b in brands)


def cache_file_for(groups: list, days: int, today: str | None = None) -> Path:
    """오늘자 캐시 파일 경로 — `days` 창 + 활성 브랜드 그룹(시드 + 승인된 보조 검색어) 해시.
    창 변경/admin 편집이 같은 날에도 즉시 재수집으로 이어진다 (stale 캐시 파일을 재사용하지 않음)."""
    today = today or datetime.now().strftime("%Y-%m-%d")
    brands_sig = hashlib.sha1(
        "|".join(f"{g['brand']}:{','.join(g.get('terms') or [])}" for g in groups)
        .encode("utf-8")
    ).hexdigest()[:8]
    return CACHE_DIR / f"brand_traffic_{today}_{days}d_{brands_sig}.json"


def get_brand_traffic(days: int | None = None, refresh: bool = False) -> dict:
    """Home 미디어 인텔리전스 카드용 — 14일(최근 7 + 이전 7) 브랜드 트래픽.

    `days` 를 명시하지 않으면 `DEFAULT_TRAFFIC_DAYS`(14) 사용 —
    프런트가 sparkline last7 표시 + last7 vs prev7 상승률을 계산한다.

    캐시 포맷:
    {
        "updated_at": "2026-04-18T10:15:00",
        "days": 14,
        "brands": [...aggregate_brand_traffic 결과...]
    }

    실패 규약 (2026-10-01 캐시 오염 재발 방지):
    - 어느 브랜드든 수집 실패(NaverApiError)면 결과에 `error`(사람용 한 줄) + `failed_brands` 가 붙는다.
      살아남은 브랜드 데이터는 그대로 준다 — 프런트는 error 를 보고 카드에 실패를 표시한다.
    - error 가 있거나 전 브랜드 0건(looks_poisoned)인 결과는 **캐시에 쓰지 않는다** — 다음 호출이 다시 수집한다.
    - 읽기도 같은 판정: 그 모양의 캐시 파일은 서빙하지 않고 재수집한다.
    """
    if days is None:
        days = DEFAULT_TRAFFIC_DAYS

    # 브랜드 그룹을 캐시 판정보다 먼저 읽는다 — 목록 변경 ⇒ 캐시 키 변경 ⇒ 재수집.
    from agents.editable_factors import get_home_brand_groups
    groups = get_home_brand_groups()
    cache_file = cache_file_for(groups, days)

    if not refresh and cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            cached = None
        if cached is not None and not looks_poisoned(cached):
            return cached
        logger.warning("[MI] 오염된 캐시 무시 — 재수집: %s", cache_file.name)

    client = get_client()
    if not client.is_configured:
        return {
            "updated_at": datetime.now().isoformat(),
            "days": days,
            "brands": [],
            "error": "NAVER_API_CLIENT_ID/SECRET 미설정",
        }

    logger.info("[MI] %s 브랜드 그룹 트래픽 수집 시작 (%d일)", len(groups), days)
    brands_data = aggregate_brand_traffic(groups, days=days)
    result = {
        "updated_at": datetime.now().isoformat(),
        "days": days,
        "brands": brands_data,
    }
    failed = [b["brand"] for b in brands_data if b.get("error")]
    if failed:
        first = next(b["error"] for b in brands_data if b.get("error"))
        result["failed_brands"] = failed
        result["error"] = (f"네이버 뉴스 수집 실패 {len(failed)}/{len(brands_data)} 브랜드 "
                           f"({', '.join(failed[:3])}{' …' if len(failed) > 3 else ''}) — {first}")
        logger.error("[MI] %s", result["error"])
        return result                      # 실패는 캐시에 쓰지 않는다
    if looks_poisoned(result):
        logger.warning("[MI] 전 브랜드 0건 집계 — 캐시에 쓰지 않는다 (수집 경로 점검)")
        return result
    try:
        cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("[MI] cache 쓰기 실패: %s", e)
    return result


def get_latest_brand_news(brand: str, limit: int = 10) -> list[dict]:
    """브랜드 클릭 시 — 오늘 기준 최신 뉴스 N건.

    캐시하지 않음 — 클릭 시 실시간 조회.
    """
    client = get_client()
    if not client.is_configured:
        return []
    items = client.latest_news(brand, limit=limit)
    return [{
        "title": n.title,
        "url": n.original_link or n.link,
        "source": n.source,
        "date": n.date_str,
        "description": n.description[:200],
    } for n in items]


def invalidate_brand_traffic_cache() -> int:
    """오늘 날짜의 brand_traffic 캐시 파일 삭제 — admin 브랜드 편집 즉시 반영용.

    캐시 키에 브랜드 해시가 들어가므로(get_brand_traffic) 이 삭제 없이도 다음 로드에서
    재수집되지만, 구 포맷 파일 잔존/해시 충돌에 대비한 belt-and-suspenders."""
    today = datetime.now().strftime("%Y-%m-%d")
    removed = 0
    for p in CACHE_DIR.glob(f"brand_traffic_{today}*.json"):
        try:
            p.unlink()
            removed += 1
        except Exception:
            continue
    return removed


def cleanup_old_cache(keep_days: int = 7):
    """7일 지난 캐시 파일 정리.

    파일명은 구 포맷 `brand_traffic_YYYY-MM-DD.json` 과
    신 포맷 `brand_traffic_YYYY-MM-DD_<hash8>.json` 둘 다 허용."""
    cutoff = datetime.now() - timedelta(days=keep_days)
    for p in CACHE_DIR.glob("brand_traffic_*.json"):
        try:
            date_str = p.stem.replace("brand_traffic_", "").split("_")[0]
            d = datetime.strptime(date_str, "%Y-%m-%d")
            if d < cutoff:
                p.unlink()
        except Exception:
            continue
