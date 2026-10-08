"""KEB 환율 캐시 가드 — 빈/깨진 다운로드가 캐시 최신 파일이 되지 않고, 로더는 최신 '유효' 파일을 고른다.

프로덕션 2026-10-02 04:00: KEB 가 0 바이트를 돌려줬는데 `fetch_keb_excel` 이 그것을 최종 캐시 이름으로 그대로 저장했고,
`_load_latest_cache` 는 이름순 최신 파일 하나만 파싱해 그 뒤 모든 해외 검색의 get_rate() 가 실패할 상태였다(옆에 유효한
2026-08-31 창 파일이 있었는데도). 네트워크·Playwright 는 쓰지 않는다 — downloader 를 가짜로 바꾼다. 실제 캐시 디렉터리도
건드리지 않는다(tmp_path).

실행: .venv/bin/python -m pytest tests/test_exchange_rate_cache_guard.py -q
"""
from __future__ import annotations

import asyncio
import logging
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents import exchange_rate as xr
from agents.exchange_rate import ExchangeRateFetcher, InvalidKebFileError, fetch_keb_excel, list_cache_files

HEADER = "통화\t현찰사실때\t현찰파실때\t송금보내실때\t송금받으실때\tT/C사실때\t외화수표파실때\t매매기준율\t환가료율\t미화환산율"
ROWS = {
    "USD": "미국 USD\t0\t0\t0\t0\t0\t0\t1,380.12\t0\t1",
    "EUR": "유로 EUR\t0\t0\t0\t0\t0\t0\t1,560.40\t0\t1.1",
    "JPY": "일본 JPY\t0\t0\t0\t0\t0\t0\t939.36\t0\t0.0068",
    "GBP": "영국 GBP\t0\t0\t0\t0\t0\t0\t1,810.55\t0\t1.3",
    "CHF": "스위스 CHF\t0\t0\t0\t0\t0\t0\t1,620.77\t0\t1.2",
}


def _tsv(*, without: str | None = None, usd: str = "1,380.12") -> bytes:
    rows = dict(ROWS)
    rows["USD"] = rows["USD"].replace("1,380.12", usd)
    if without:
        rows.pop(without)
    return ("기간평균환율\n" + HEADER + "\n" + "\n".join(rows.values()) + "\n").encode("euc-kr")


def _valid_file(cache_dir: Path, window: str, usd: str = "1,380.12") -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    p = cache_dir / f"keb_avg_rate_{window}.xlsx"
    p.write_bytes(_tsv(usd=usd))
    return p


def _downloader(payload: bytes):
    """fetch_keb_excel 의 Playwright 단계를 대신한다 — 받은 바이트를 임시 경로에 쓴다."""
    calls: list[dict] = []

    async def dl(start_str: str, end_str: str, save_path: Path, headless: bool) -> None:
        calls.append({"start": start_str, "end": end_str, "path": save_path})
        save_path.write_bytes(payload)
    dl.calls = calls  # type: ignore[attr-defined]
    return dl


# ── 1 다운로드 검증: 빈/깨진 응답은 최종 이름으로 내려앉지 않는다 ─────────────────

def test_empty_download_never_lands_under_final_name_and_previous_cache_stays_active(tmp_path):
    cache = tmp_path / "fx"
    _valid_file(cache, "20230903_20260831")
    dl = _downloader(b"")
    with pytest.raises(InvalidKebFileError) as ei:
        asyncio.run(fetch_keb_excel(cache, ref_date=date(2026, 10, 2), downloader=dl))
    msg = str(ei.value)
    assert "0 bytes" in msg and "keb_avg_rate_20231003_20260930" in msg
    # 임시 경로는 캐시 glob 에 걸리지 않고, 실패 뒤 남지도 않는다
    assert dl.calls[0]["path"].parent == cache and not dl.calls[0]["path"].name.startswith("keb_avg_rate_")
    assert not (cache / "keb_avg_rate_20231003_20260930.xlsx").exists()
    assert sorted(p.name for p in cache.iterdir()) == ["keb_avg_rate_20230903_20260831.xlsx"]
    # 직전 유효 파일이 그대로 활성 캐시다
    f = ExchangeRateFetcher(cache)
    assert f._load_latest_cache() is True
    assert f._rate_meta == {"from": "20230903", "to": "20260831"} and f.skipped_cache_files == []
    assert f.get_rate("USD")["rate"] == 1380.12 and f.get_rate("USD")["to_month"] == "20260831"


def test_error_page_download_is_rejected_with_size_and_preview(tmp_path):
    cache = tmp_path / "fx"
    body = b"<html><head><title>Service Unavailable</title></head><body>KEB error</body></html>"
    with pytest.raises(InvalidKebFileError) as ei:
        asyncio.run(fetch_keb_excel(cache, ref_date=date(2026, 10, 2), downloader=_downloader(body)))
    msg = str(ei.value)
    assert f"{len(body)} bytes" in msg and "Service Unavailable" in msg       # 다음 장애는 무엇을 받았는지 말해준다
    assert list(cache.glob("keb_avg_rate_*")) == [] and list(cache.glob(".*")) == []


def test_download_missing_core_currency_is_rejected(tmp_path):
    cache = tmp_path / "fx"
    with pytest.raises(InvalidKebFileError, match="GBP"):
        asyncio.run(fetch_keb_excel(cache, ref_date=date(2026, 10, 2), downloader=_downloader(_tsv(without="GBP"))))
    assert list(cache.glob("keb_avg_rate_*")) == []


def test_valid_download_replaces_active_cache(tmp_path):
    cache = tmp_path / "fx"
    _valid_file(cache, "20230903_20260831", usd="1,350.00")
    f = ExchangeRateFetcher(cache)
    rates = asyncio.run(f.refresh(ref_date=date(2026, 10, 2), downloader=_downloader(_tsv(usd="1,380.12"))))
    assert rates["USD"] == 1380.12 and rates["JPY"] == pytest.approx(9.3936)
    assert f._rate_meta == {"from": "20231003", "to": "20260930"} and f.active_cache_file.name == "keb_avg_rate_20231003_20260930.xlsx"
    assert sorted(p.name for p in cache.iterdir()) == ["keb_avg_rate_20230903_20260831.xlsx", "keb_avg_rate_20231003_20260930.xlsx"]
    g = ExchangeRateFetcher(cache)                     # 새 인스턴스도 새 창을 고른다
    assert g.get_rate("USD") == {"currency": "USD", "rate": 1380.12, "from_month": "20231003", "to_month": "20260930", "data_points": 1}
    assert g.skipped_cache_files == []


# ── 2 로더: 최신 → 과거로 돌며 무효 파일은 건너뛰고, 건너뛴 사실을 남긴다 ────────────

def test_loader_skips_newest_empty_file_and_exposes_the_skip(tmp_path, caplog):
    cache = tmp_path / "fx"
    _valid_file(cache, "20230903_20260831")
    (cache / "keb_avg_rate_20231003_20260930.xlsx").write_bytes(b"")          # 프로덕션 10-02 상태
    f = ExchangeRateFetcher(cache)
    with caplog.at_level(logging.WARNING, logger="agents.exchange_rate"):
        assert f._load_latest_cache() is True
    assert f._rate_meta == {"from": "20230903", "to": "20260831"}
    assert f.active_cache_file.name == "keb_avg_rate_20230903_20260831.xlsx"
    assert [s["file"] for s in f.skipped_cache_files] == ["keb_avg_rate_20231003_20260930.xlsx"]
    assert "0 bytes" in f.skipped_cache_files[0]["reason"]
    assert any("keb_avg_rate_20231003_20260930.xlsx" in r.getMessage() for r in caplog.records)
    status = f.cache_status()
    assert status["active_file"] == "keb_avg_rate_20230903_20260831.xlsx" and status["to"] == "20260831"
    assert status["skipped_newer"] == f.skipped_cache_files
    # get_rate 는 과거 창을 쓰되 응답에 건너뛴 파일이 드러난다
    r = f.get_rate("EUR")
    assert r["to_month"] == "20260831" and r["rate"] == 1560.40


def test_loader_skips_unparseable_then_uses_next_valid(tmp_path):
    cache = tmp_path / "fx"
    _valid_file(cache, "20230703_20260630")
    (cache / "keb_avg_rate_20230803_20260731.xlsx").write_bytes(b"<html>login</html>")
    (cache / "keb_avg_rate_20230903_20260831.xlsx").write_bytes(_tsv(without="CHF"))
    f = ExchangeRateFetcher(cache)
    assert f._load_latest_cache() is True and f._rate_meta["to"] == "20260630"
    assert [s["file"] for s in f.skipped_cache_files] == ["keb_avg_rate_20230903_20260831.xlsx",
                                                          "keb_avg_rate_20230803_20260731.xlsx"]


def test_loader_with_only_invalid_files_returns_false_and_lists_them(tmp_path):
    cache = tmp_path / "fx"
    cache.mkdir()
    (cache / "keb_avg_rate_20231003_20260930.xlsx").write_bytes(b"")
    f = ExchangeRateFetcher(cache)
    assert f._load_latest_cache() is False
    assert [s["file"] for s in f.skipped_cache_files] == ["keb_avg_rate_20231003_20260930.xlsx"]
    with pytest.raises(RuntimeError, match="keb_avg_rate_20231003_20260930.xlsx"):
        f.get_rate("USD")


def test_list_cache_files_newest_first_and_skips_zero_byte(tmp_path):
    cache = tmp_path / "fx"
    _valid_file(cache, "20230803_20260731")
    _valid_file(cache, "20230903_20260831")
    (cache / "keb_avg_rate_20231003_20260930.xlsx").write_bytes(b"")
    (cache / ".keb_avg_rate_20231103_20261031.xlsx.part").write_bytes(b"partial")     # 진행 중 임시 파일은 캐시가 아니다
    assert [p.name for p in list_cache_files(cache)] == ["keb_avg_rate_20230903_20260831.xlsx",
                                                         "keb_avg_rate_20230803_20260731.xlsx"]
    assert [p.name for p in list_cache_files(cache, skip_empty=False)][0] == "keb_avg_rate_20231003_20260930.xlsx"


# ── 3 신선도 판정도 같은 목록을 본다 — 0 바이트 파일이 '최신 창' 을 가장하지 못한다 ────

def test_freshness_window_ignores_zero_byte_newest_file(tmp_path):
    from agents.agent_tasks import freshness as fr
    d = tmp_path / "data" / "foreign" / "exchange_rate"
    _valid_file(d, "20230903_20260831")
    (d / "keb_avg_rate_20231003_20260930.xlsx").write_bytes(b"")
    ctx = fr.Ctx(db_path=":memory:", base_dir=tmp_path, today=date(2026, 10, 8))
    r = fr.check_exchange_rate_cache(ctx)
    assert r.observed == "20260831" and r.deadline == "20260930" and r.stale is True and r.runbook == "exchange_rate_refresh"


# ── 4 월간 잡: 1회 재시도 — 실패는 삼키지 않고 guarded_job 으로 간다 ─────────────────

class _NoDb:
    def __init__(self, *a, **k):
        raise AssertionError("환율 갱신이 실패하면 DB 재계산에 들어가면 안 된다")


class _Db:
    def __init__(self, path):
        pass

    def recompute_foreign_fx(self, rates, meta):
        return {"candidates": 3, "updated": 3, "skipped": 0}


def _fetcher_factory(outcomes: list):
    """outcomes: 호출 순서대로 예외(raise) 또는 환율 dict(반환)."""
    class Fetcher:
        attempts = 0
        _rate_meta = {"from": "20231003", "to": "20260930"}
        skipped_cache_files: list = []
        active_cache_file = Path("keb_avg_rate_20231003_20260930.xlsx")

        async def refresh(self, ref_date=None, headless=True, **kw):
            Fetcher.attempts += 1
            out = outcomes[Fetcher.attempts - 1]
            if isinstance(out, Exception):
                raise out
            self._rates = out
            return out
    return Fetcher


def test_refresh_job_retries_once_after_invalid_download(monkeypatch):
    import scheduler as sch
    good = {"USD": 1380.12, "EUR": 1560.4, "JPY": 9.3936, "GBP": 1810.55, "CHF": 1620.77}
    F = _fetcher_factory([InvalidKebFileError("빈 다운로드 (0 bytes)"), good])
    monkeypatch.setattr("agents.exchange_rate.ExchangeRateFetcher", F)
    monkeypatch.setattr("agents.db.DrugPriceDB", _Db)
    waits: list[float] = []
    monkeypatch.setattr(sch.time, "sleep", lambda s: waits.append(s))
    res = sch.exchange_rate_refresh_job()
    assert res["ok"] is True and res["errors"] == []
    assert F.attempts == 2 and waits == [sch.FX_REFRESH_RETRY_WAIT_SEC]
    assert res["counts"]["attempts"] == 2 and res["counts"]["to"] == "20260930" and res["counts"]["currencies"] == 5


def test_refresh_job_fails_after_bounded_retries_without_touching_db(monkeypatch):
    import scheduler as sch
    F = _fetcher_factory([InvalidKebFileError("빈 다운로드 (0 bytes)"), InvalidKebFileError("빈 다운로드 (0 bytes) 재시도")])
    monkeypatch.setattr("agents.exchange_rate.ExchangeRateFetcher", F)
    monkeypatch.setattr("agents.db.DrugPriceDB", _NoDb)
    monkeypatch.setattr(sch.time, "sleep", lambda s: None)
    res = sch.exchange_rate_refresh_job()
    assert res["ok"] is False and F.attempts == sch.FX_REFRESH_ATTEMPTS == 2
    assert len(res["errors"]) == 1 and "InvalidKebFileError" in res["errors"][0] and "재시도" in res["errors"][0]


# ── 5 라디오(최종 고시회차) 로직은 그대로다 — CLAUDE.md 과거 실수 2026-06-14 ─────────────

def test_final_round_radio_logic_is_untouched():
    import inspect
    src = inspect.getsource(xr._download_keb_excel)
    assert "r.checked = (r.id === 'tmpPbldDvCd_2')" in src and "setVal('pbldDvCd', '0')" in src
    assert "doExcelDown('Y')" in src


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
