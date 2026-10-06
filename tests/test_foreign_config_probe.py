"""해외약가 설정 프로브 (P1.6 관측) — 네트워크 없이 '지금 수집할 수 있는 설정인가' 를 매일 본다.

감사가 본 결함: US 는 2026-06-08 부터 Micromedex 로그인 33회 연속 실패인데 원장에 한 줄도 없었고, UK 는 scrapling 이
playwright 와 import 되지 않으면(scrapling 0.4.11 + playwright 1.61.0: cannot import name 'BrowserBindResult') 조용히 [] 였다.
프로브는 (1) REQUIRES_LOGIN 스크레이퍼의 ENV_KEY_MAP 키 **이름** 존재, (2) scrapling.fetchers import, (3) 선언된 프록시 키를
본다. 값은 절대 적지 않는다. 문제마다 record_job_failure(error_class="config", problem=키) — 사람이 닫을 때까지 1건.
"""
from __future__ import annotations

from agents.agent_tasks import config_probe as cp
from agents.agent_tasks.signals import JOB_RUNBOOK, record_job_failure
from agents.agent_tasks.store import claim_task, get_task, list_events, list_tasks, triage_task, update_task
from agents.scrapers.base import ENV_KEY_MAP, BaseScraper, load_credentials

SECRET = "SECRET-VALUE-xyz-123"
FULL_ENV = {
    "MICROMEDEX_US_USERNAME": "u", "MICROMEDEX_US_PASSWORD": SECRET,
    "MIMS_UK_USERNAME": "u", "MIMS_UK_PASSWORD": SECRET,
    "ROTE_LISTE_DE_USERNAME": "u", "ROTE_LISTE_DE_PASSWORD": SECRET,
    "FOREIGN_PROXY": f"http://user:{SECRET}@proxy:8080", "MIMS_UK_PROXY": f"http://user:{SECRET}@proxy:8081",
}


def _ok_import():
    return None


def _bad_import():
    raise ImportError("cannot import name 'BrowserBindResult' from 'scrapling.engines._browsers._base'")


# ── 선언: ENV_KEY_MAP 은 모듈 상수, 스크레이퍼는 로그인 선택·프록시 키를 선언한다 ──────────

def test_env_key_map_is_module_level_and_load_credentials_still_reads_it(monkeypatch, tmp_path):
    assert ENV_KEY_MAP["US"] == ("MICROMEDEX_US_USERNAME", "MICROMEDEX_US_PASSWORD")
    monkeypatch.setenv("MICROMEDEX_US_USERNAME", "u")
    monkeypatch.setenv("MICROMEDEX_US_PASSWORD", "p")
    assert load_credentials(tmp_path / "config" / "foreign_credentials.json", "US") == {"username": "u", "password": "p"}


def test_scrapers_declare_login_optional_and_proxy_keys():
    from agents.scrapers.ch_compendium import ChCompendiumScraper
    from agents.scrapers.fr_vidal import FrVidalScraper
    from agents.scrapers.jp_mhlw import JpMhlwScraper
    from agents.scrapers.uk_mims import UkMimsScraper
    from agents.scrapers.us_micromedex import UsMicromedexScraper
    assert BaseScraper.LOGIN_OPTIONAL is False and BaseScraper.PROXY_ENV_KEYS == ()
    assert FrVidalScraper.REQUIRES_LOGIN and FrVidalScraper.LOGIN_OPTIONAL           # 비로그인 모드로도 동작
    assert UsMicromedexScraper.REQUIRES_LOGIN and not UsMicromedexScraper.LOGIN_OPTIONAL
    assert JpMhlwScraper.PROXY_ENV_KEYS == ("JP_MHLW_PROXY", "FOREIGN_PROXY")
    assert ChCompendiumScraper.PROXY_ENV_KEYS == ("CH_COMPENDIUM_PROXY", "FOREIGN_PROXY")
    assert UkMimsScraper.PROXY_ENV_KEYS == ("MIMS_UK_PROXY",)
    classes = cp.scraper_classes()
    assert {c.COUNTRY for c in classes} >= {"JP", "IT", "FR", "CH", "UK", "DE", "CA", "US"}
    assert all(issubclass(c, BaseScraper) for c in classes)


# ── 프로브 ─────────────────────────────────────────────────────────────────────

def test_probe_all_set_reports_nothing():
    assert cp.probe(env=FULL_ENV, importer=_ok_import) == []


def test_probe_missing_credentials_names_keys_only_and_skips_login_optional():
    env = {k: v for k, v in FULL_ENV.items() if not k.startswith("MICROMEDEX_US_")}
    env["ROTE_LISTE_DE_PASSWORD"] = ""                                         # 빈 값도 미설정
    problems = {p.key: p for p in cp.probe(env=env, importer=_ok_import)}
    assert set(problems) == {"US:credentials", "DE:credentials"}                 # FR(Vidal) 은 LOGIN_OPTIONAL → 제외
    assert "MICROMEDEX_US_USERNAME" in problems["US:credentials"].detail and "MICROMEDEX_US_PASSWORD" in problems["US:credentials"].detail
    assert "ROTE_LISTE_DE_PASSWORD" in problems["DE:credentials"].detail and "ROTE_LISTE_DE_USERNAME" not in problems["DE:credentials"].detail
    assert all(SECRET not in p.detail and "u" != p.detail for p in problems.values())


def test_probe_proxy_alternatives_and_required_keys():
    env = {k: v for k, v in FULL_ENV.items() if k not in ("FOREIGN_PROXY", "MIMS_UK_PROXY")}
    keys = {p.key for p in cp.probe(env=env, importer=_ok_import)}
    assert keys == {"JP:proxy", "CH:proxy", "UK:proxy"}
    env["JP_MHLW_PROXY"] = "http://x"                                           # 국가 전용 키 하나로 충족
    keys = {p.key for p in cp.probe(env=env, importer=_ok_import)}
    assert keys == {"CH:proxy", "UK:proxy"}
    env["FOREIGN_PROXY"] = "http://x"                                           # 공용 키는 JP·CH 둘 다, UK 는 아님
    assert {p.key for p in cp.probe(env=env, importer=_ok_import)} == {"UK:proxy"}


def test_probe_scrapling_import_failure_is_a_problem_with_versions():
    problems = {p.key: p for p in cp.probe(env=FULL_ENV, importer=_bad_import)}
    assert set(problems) == {"scrapling:import"}
    d = problems["scrapling:import"].detail
    assert "BrowserBindResult" in d and "playwright" in d and "scrapling" in d


def test_probe_never_leaks_values(monkeypatch):
    env = dict(FULL_ENV)
    env.pop("MIMS_UK_PASSWORD")
    for p in cp.probe(env=env, importer=_bad_import):
        assert SECRET not in p.detail and SECRET not in p.key


def test_default_importer_imports_the_symbol_uk_uses():
    """실제 import(네트워크 없음). 이 환경에서 실패하면 그 자체가 보고 대상이라 skip 이 아니라 결과를 출력한다."""
    try:
        cp._import_scrapling()
        print("\nscrapling.fetchers.StealthySession import: ok")
    except Exception as exc:                                                   # noqa: BLE001
        print(f"\nscrapling.fetchers.StealthySession import: FAIL {type(exc).__name__}: {exc}")
    assert cp.probe(env=FULL_ENV, importer=_ok_import) == []


# ── 기록: 문제마다 config 과제 1건, 사람이 닫을 때까지 ─────────────────────────────

def test_record_job_failure_problem_key_makes_one_task_per_problem(tmp_path):
    db = tmp_path / "a.db"
    a = record_job_failure("foreign_config_probe", "US 자격증명 미설정", error_class="config", problem="US:credentials", db_path=db)
    b = record_job_failure("foreign_config_probe", "UK 프록시 미설정", error_class="config", problem="UK:proxy", db_path=db)
    assert a != b and len(list_tasks(db_path=db)) == 2
    t = get_task(a, db_path=db)
    assert t["source_ref"] == "foreign_config_probe:config:US:credentials" and "US:credentials" in t["title"]
    assert "설정 문제" in t["title"] and "런북 없음" in t["detail"]
    assert record_job_failure("foreign_config_probe", "x", error_class="config", problem="US:credentials", db_path=db) == a


def test_record_problems_second_run_adds_zero_and_closed_reopens(tmp_path):
    db = tmp_path / "a.db"
    problems = cp.probe(env={}, importer=_bad_import)
    ids = cp.record_problems(problems, db_path=db)
    assert len(ids) == len(problems) == len(list_tasks(db_path=db)) > 0
    assert cp.record_problems(problems, db_path=db) == ids and len(list_tasks(db_path=db)) == len(ids)
    triage_task(ids[0], actor="dispatcher", db_path=db)                        # 상태 기계대로: new → triaged → in_progress → closed
    claim_task(ids[0], actor="dispatcher", db_path=db)
    update_task(ids[0], db_path=db, status="closed", actor="dispatcher", resolution="접수")
    assert cp.record_problems(problems, db_path=db) == ids                     # 고친 게 아니면 같은 행이 다시 열린다
    assert get_task(ids[0], db_path=db)["status"] == "triaged"
    assert "reopened" in [e["event"] for e in list_events(ids[0], db_path=db)]


def test_run_and_record_reads_env_file_names_only(tmp_path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / ".env").write_text("MICROMEDEX_US_USERNAME=u\nMICROMEDEX_US_PASSWORD=" + SECRET + "\n", encoding="utf-8")
    env = cp.env_from(tmp_path, base={})
    assert env.get("MICROMEDEX_US_USERNAME") == "u"
    res = cp.run_and_record(base_dir=tmp_path, db_path=tmp_path / "a.db", env=env, importer=_ok_import)
    assert res["ok"] is True                                                    # 설정 문제는 과제이지 잡 실패가 아니다
    assert res["counts"]["problems"] == res["counts"]["tasks"] == res["counts"]["tasks_new"] > 0
    assert "US:credentials" not in {p["key"] for p in res["problems"]}
    assert {"UK:credentials", "DE:credentials"} <= {p["key"] for p in res["problems"]}
    assert all(SECRET not in p["detail"] for p in res["problems"])
    res2 = cp.run_and_record(base_dir=tmp_path, db_path=tmp_path / "a.db", env=env, importer=_ok_import)
    assert res2["counts"]["tasks_new"] == 0


# ── 스케줄러 잡 ────────────────────────────────────────────────────────────────

def test_scheduler_registers_config_probe_job(monkeypatch, tmp_path):
    import scheduler as sch
    ledger = tmp_path / "ledger.db"
    monkeypatch.setenv("AGENT_TASK_DB", str(ledger))
    specs = {s.id: s for s in sch.build_job_specs({"day": "1-7", "hour": 9, "minute": 0})}
    spec = specs["foreign_config_probe"]
    assert spec.fn.__job_id__ == "foreign_config_probe" and JOB_RUNBOOK["foreign_config_probe"] is None
    fields = {f.name: str(f) for f in spec.trigger.fields}
    assert fields["hour"] == "7" and fields["minute"] == "40"                  # 신선도(07:45)·스모크(07:50) 앞
    monkeypatch.setattr(cp, "_import_scrapling", _bad_import)
    monkeypatch.setattr(cp, "env_from", lambda base_dir, base=None: {})
    res = sch.foreign_config_probe_job()
    assert res["ok"] is True and res["errors"] == [] and res["counts"]["problems"] >= 4
    tasks = list_tasks(db_path=ledger)
    assert tasks and all(t["source"] == "scheduler_job" and t["source_ref"].startswith("foreign_config_probe:config:") for t in tasks)
    assert any(t["source_ref"].endswith(":scrapling:import") for t in tasks)
