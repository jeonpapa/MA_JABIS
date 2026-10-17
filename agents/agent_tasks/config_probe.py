"""해외약가 설정 프로브 (P1.6 관측) — 네트워크 없이 "지금 가격을 가져올 수 있는 설정인가" 를 매일 본다.

감사가 본 결함: US 는 Micromedex 로그인 33회 연속 실패, UK 는 scrapling 이 설치된 playwright 와 import 되지 않으면
(scrapling 0.4.11 + playwright 1.61.0: cannot import name 'BrowserBindResult') 조용히 [] — 둘 다 원장에 한 줄도 없었다.
라이브 검색 실패는 search_all 이 사후에 기록하지만(foreign_search:*), 사용자가 검색하기 전에 설정 수준에서 잡을 수 있는 것은
여기서 매일 07:40 에 본다(신선도 07:45·스모크 07:50 앞). 세 가지, 전부 로컬·무네트워크:
  1) REQUIRES_LOGIN(LOGIN_OPTIONAL 아님) 스크레이퍼의 ENV_KEY_MAP 키가 환경에 있는가 — **키 이름만**, 값은 절대 적지 않는다
  2) scrapling.fetchers.StealthySession(UK 가 쓰는 바로 그 심볼)이 import 되는가
  3) PROXY_ENV_KEYS 를 선언한 스크레이퍼는 그중 하나라도 설정돼 있는가
문제마다 record_job_failure(error_class="config", problem=키) → 과제 1건/문제, 사람이 닫을 때까지(재발하면 같은 행을 다시 연다).
설정 문제는 과제이지 잡 실패가 아니다(ok=True) — 아니면 foreign_config_probe:config 집계 과제가 하나 더 생긴다.
"""
from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Mapping

from .jobs import resolve_db_path
from .signals import count_source_tasks, record_job_failure
from .store import DEFAULT_DB_PATH

logger = logging.getLogger(__name__)
JOB_ID = "foreign_config_probe"
REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Problem:
    key: str       # 과제 키(source_ref 꼬리) — "US:credentials" · "UK:proxy" · "scrapling:import"
    detail: str    # 사람용 한 줄 — 키 이름·버전만. 값 금지


def _is_set(env: Mapping[str, str], key: str) -> bool:
    return bool((env.get(key) or "").strip())


def env_from(base_dir: Path | str, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """config/.env 를 읽어(있으면) 환경 위에 얹은 사본 — override=False(이미 있는 환경값 우선, load_credentials 와 같은 규칙).

    프로세스 환경(os.environ)은 바꾸지 않는다. base=None 이면 os.environ 복사본 위에 얹는다."""
    env = dict(os.environ if base is None else base)
    path = Path(base_dir) / "config" / ".env"
    if path.exists():
        try:
            from dotenv import dotenv_values
            for k, v in (dotenv_values(path) or {}).items():
                if v is not None and not _is_set(env, k):
                    env[k] = v
        except ImportError:
            logger.warning("python-dotenv 없음 — 환경변수만으로 판정")
    return env


def scraper_classes() -> list[type]:
    """ForeignPriceAgent 가 쓰는 스크레이퍼 클래스 전부 — agents.foreign_price_agent 가 import 한 BaseScraper 서브클래스."""
    from agents import foreign_price_agent as fpa
    from agents.scrapers.base import BaseScraper
    seen: list[type] = []
    for obj in vars(fpa).values():
        if isinstance(obj, type) and issubclass(obj, BaseScraper) and obj is not BaseScraper and obj not in seen:
            seen.append(obj)
    return seen


def _import_scrapling() -> None:
    """UK(uk_mims._scrape_sync)가 쓰는 import 그대로 — playwright 와의 호환이 여기서 깨진다."""
    from scrapling.fetchers import StealthySession  # noqa: F401


def _versions(*dists: str) -> str:
    import importlib.metadata as md
    parts = []
    for d in dists:
        try:
            parts.append(f"{d} {md.version(d)}")
        except Exception:
            parts.append(f"{d} 미설치")
    return ", ".join(parts)


def probe_credentials(env: Mapping[str, str], scrapers: list[type]) -> list[Problem]:
    from agents.scrapers.base import ENV_KEY_MAP
    out: list[Problem] = []
    for cls in scrapers:
        if not getattr(cls, "REQUIRES_LOGIN", False) or getattr(cls, "LOGIN_OPTIONAL", False):
            continue
        keys = ENV_KEY_MAP.get(cls.COUNTRY)
        if not keys:
            out.append(Problem(f"{cls.COUNTRY}:credentials", f"{cls.COUNTRY} {cls.__name__}: 로그인 필수인데 ENV_KEY_MAP 에 키 없음"))
            continue
        missing = [k for k in keys if not _is_set(env, k)]
        if missing:
            out.append(Problem(f"{cls.COUNTRY}:credentials",
                               f"{cls.COUNTRY} {cls.SOURCE_LABEL}: 자격증명 키 미설정 {missing} (config/.env 또는 fly secret — 이름만 확인)"))
    return out


# 2026-10-17 사용자 결정: JP·CH 는 서버 라이브 수집(데이터센터 IP 차단 → 주거형 프록시 필요) 대신 Aside 요청 큐로만
# 수집한다. 프록시 키 미설정은 문제가 아니므로 과제를 만들지 않는다. 서버 수집으로 되돌리면 여기서 빼고 프록시를 설정한다.
REQUEST_ONLY_PROXY_COUNTRIES = frozenset({"JP", "CH"})


def probe_proxies(env: Mapping[str, str], scrapers: list[type],
                  request_only: frozenset[str] = REQUEST_ONLY_PROXY_COUNTRIES) -> list[Problem]:
    out: list[Problem] = []
    for cls in scrapers:
        if getattr(cls, "COUNTRY", None) in request_only:
            continue
        keys = tuple(getattr(cls, "PROXY_ENV_KEYS", ()) or ())
        if keys and not any(_is_set(env, k) for k in keys):
            out.append(Problem(f"{cls.COUNTRY}:proxy",
                               f"{cls.COUNTRY} {cls.SOURCE_LABEL}: 프록시 키 미설정 — {list(keys)} 중 하나 필요 (데이터센터 IP 차단 회피)"))
    return out


def probe_scrapling(importer: Callable[[], None] | None = None) -> list[Problem]:
    try:
        (importer or _import_scrapling)()           # 호출 시점에 고른다 — 테스트가 모듈 함수를 monkeypatch 할 수 있게
    except Exception as exc:                 # ImportError 가 보통이지만 import 중 어떤 예외든 'UK 는 지금 못 간다'
        return [Problem("scrapling:import",
                        f"scrapling.fetchers.StealthySession import 실패 ({_versions('scrapling', 'playwright')}): "
                        f"{type(exc).__name__}: {str(exc)[:200]} — UK MIMS 수집 불가. pip install 'scrapling[fetchers]' 와 playwright 버전 호환 확인")]
    return []


def probe(*, env: Mapping[str, str] | None = None, scrapers: list[type] | None = None,
          importer: Callable[[], None] | None = None) -> list[Problem]:
    env = os.environ if env is None else env
    classes = scraper_classes() if scrapers is None else scrapers
    return probe_credentials(env, classes) + probe_scrapling(importer) + probe_proxies(env, classes)


def record_problems(problems: list[Problem], *, db_path: str | Path = DEFAULT_DB_PATH) -> list[int]:
    """문제마다 config 과제 1건(foreign_config_probe:config:{key}). 같은 문제는 같은 행 — record_recurring 의미."""
    return [record_job_failure(JOB_ID, p.detail, error_class="config", problem=p.key, db_path=db_path) for p in problems]


def run_and_record(*, base_dir: Path | str = REPO_ROOT, db_path: str | Path | None = None,
                   env: Mapping[str, str] | None = None, importer: Callable[[], None] | None = None) -> dict:
    """잡·CLI 진입점. counts.tasks_new 는 이번 실행이 새로 만든 행 수 — 두 번째 실행은 0 이어야 한다."""
    ledger = Path(db_path) if db_path else resolve_db_path()
    problems = probe(env=env_from(base_dir) if env is None else env, importer=importer)
    before = count_source_tasks("scheduler_job", db_path=ledger)
    ids = record_problems(problems, db_path=ledger)
    new = count_source_tasks("scheduler_job", db_path=ledger) - before
    for p in problems:
        logger.warning("[config_probe] %s — %s", p.key, p.detail)
    return {"ok": True, "counts": {"problems": len(problems), "tasks": len(ids), "tasks_new": new},
            "problems": [asdict(p) for p in problems]}


def format_table(problems: list[Problem]) -> str:
    return "\n".join(f"CONFIG {p.key:22s} — {p.detail}" for p in problems) or "ok     설정 문제 없음"
