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


def get_brand_traffic(days: int | None = None, refresh: bool = False) -> dict:
    """Home 미디어 인텔리전스 카드용 — 오늘 기준 '지난 1개월' 브랜드 트래픽.

    `days` 를 명시하지 않으면 `days_in_last_month()` (28~31) 로 계산.

    캐시 포맷:
    {
        "updated_at": "2026-04-18T10:15:00",
        "days": 31,
        "brands": [...aggregate_brand_traffic 결과...]
    }

    캐시 키에는 활성 브랜드 그룹(시드 + 승인된 보조 검색어)의 해시가 포함된다 —
    admin 이 브랜드 목록/보조 검색어를 편집하면 다음 로드에서 즉시 재수집
    (같은 날이라도 stale 캐시 파일을 재사용하지 않음).
    """
    if days is None:
        days = days_in_last_month()
    today = datetime.now().strftime("%Y-%m-%d")

    # 브랜드 그룹을 캐시 판정보다 먼저 읽는다 — 목록 변경 ⇒ 캐시 키 변경 ⇒ 재수집.
    from agents.editable_factors import get_home_brand_groups
    groups = get_home_brand_groups()
    brands_sig = hashlib.sha1(
        "|".join(f"{g['brand']}:{','.join(g.get('terms') or [])}" for g in groups)
        .encode("utf-8")
    ).hexdigest()[:8]
    cache_file = CACHE_DIR / f"brand_traffic_{today}_{brands_sig}.json"

    if not refresh and cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            pass

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
