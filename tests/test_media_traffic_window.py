"""A2 — 브랜드 트래픽 14일 창 + 캐시 키 days 포함 + 7v7 상승률 산식.

get_brand_traffic 는 네트워크 없이 aggregate_brand_traffic 를 monkeypatch 해 검증.
7v7 산식은 frontend/src/api/home.ts computeChange 와 동일 계약(패리티) 테스트.
"""
import agents.media_intelligence as mi
import agents.editable_factors as ef


class _FakeClient:
    is_configured = True


def _patch_common(monkeypatch, tmp_path, captured):
    monkeypatch.setattr(mi, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(ef, "get_home_brand_groups", lambda: [{"brand": "키트루다", "terms": []}])
    monkeypatch.setattr(mi, "get_client", lambda: _FakeClient())

    def fake_agg(groups, days):
        captured["days"] = days
        return [{"brand": "키트루다", "total_count": days, "daily": {},
                 "sparkline": [1] * days, "latest_news": []}]

    monkeypatch.setattr(mi, "aggregate_brand_traffic", fake_agg)


def test_default_window_is_14_days(monkeypatch, tmp_path):
    captured = {}
    _patch_common(monkeypatch, tmp_path, captured)
    out = mi.get_brand_traffic(refresh=True)
    assert out["days"] == 14
    assert captured["days"] == 14
    assert len(out["brands"][0]["sparkline"]) == 14  # 최근 7 + 이전 7


def test_cache_filename_includes_days(monkeypatch, tmp_path):
    captured = {}
    _patch_common(monkeypatch, tmp_path, captured)
    mi.get_brand_traffic(refresh=True)
    assert len(list(tmp_path.glob("brand_traffic_*_14d_*.json"))) == 1
    # 다른 창은 같은 날에도 별도 캐시 — 창 변경이 당일 즉시 반영됨
    mi.get_brand_traffic(days=30, refresh=True)
    assert len(list(tmp_path.glob("brand_traffic_*_30d_*.json"))) == 1
    # 14d 캐시 재사용 (refresh=False) — 재수집 없이 동일 결과
    captured.clear()
    out = mi.get_brand_traffic()
    assert "days" not in captured  # aggregate 미호출 (캐시 히트)
    assert out["days"] == 14


# ── 7v7 상승률 — home.ts computeChange 패리티 (change = last7 vs prev7 %) ──────

def _compute_change(daily: list) -> int:
    """frontend/src/api/home.ts computeChange 와 동일 산식 (계약 고정용 미러)."""
    if not daily:
        return 0
    curr = sum(daily[-7:])
    prev = sum(daily[-14:-7])
    if not prev:
        return 100 if curr else 0
    return round((curr - prev) / prev * 100)


def test_7v7_rising_math():
    # 이전 7일 합 7, 최근 7일 합 14 → +100%
    assert _compute_change([1] * 7 + [2] * 7) == 100
    # 이전 7일 합 14, 최근 7일 합 7 → -50%
    assert _compute_change([2] * 7 + [1] * 7) == -50
    # 변화 없음 → 0%
    assert _compute_change([3] * 14) == 0
    # 이전 7일 0 이고 최근 활동 존재 → +100% (division guard)
    assert _compute_change([0] * 7 + [1] * 7) == 100
    # 완전 무활동 → 0%
    assert _compute_change([0] * 14) == 0
    # 14일 미만 입력도 뒤에서부터 slice (prev 부분 부족 시 있는 만큼만)
    assert _compute_change([1, 1, 1, 1, 1, 1, 1]) == 100  # prev 창 없음 → curr>0 → 100
