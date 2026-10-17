#!/usr/bin/env python3
"""저장된 해외약가 행의 주사 per-vial 강도·투여경로·제형 배정 정정 (2026-10-17 감사 후속).

배경 (foreign_agent_rules.md §최소단위, tests/test_foreign_strength_regressions.py):
- IT Winrevair 분말+용제 키트: 용해 후 농도(50 mg/ml) × 용제 부피를 per-vial 로 써서 50/150 mg 로 저장 → 45/60 mg.
- 부피 소수점 쉼표("1,3 ml" → 3 ml) 오독.
- UK MIMS "; 2-vial kit" 분할 조각 행 → 직전 행 강도 상속.
- form_type unknown 행 → 텍스트/같은 약제 명시 경로로 route 판정 (주사 전용 약에 "45mg 경구" 탭 방지).
- CA Ontario EAP "20 mg/mL" + "Inj Sol-240 mL Vial Pk" → per-vial 모호 → unit_strength NULL + A8 집계 제외(읽기 시 판정).
- FR Keytruda 저장 unit_strength 25(농도)·보정계수 4.0 → 100mg 탭·×1 (Vidal 은 volume 없음 → daily_cost 비움).

저장 행은 get_cached_results 가 formulation_key 가 이미 있으면 재배정하지 않으므로 코드 수정만으로는 운영 화면이
바뀌지 않는다 — 이 스크립트가 **현재 코드**(ForeignPriceAgent._assign_formulations_and_persist + process_formulations)
를 저장 행에 다시 돌려 차이만 쓴다. LLM 은 호출하지 않는다(LLM 대상 pending 행은 기존 제형 배정 유지).
범위: 주사 행 + route 가 바뀌는 행만 (경구 행의 기존 LLM/제형 배정은 건드리지 않는다).

사용:
    python scripts/repair_foreign_strength.py                       # DRY-RUN (읽기 전용 mode=ro), 표 출력
    python scripts/repair_foreign_strength.py --query winrevair --country IT
    python scripts/repair_foreign_strength.py --apply               # 한 트랜잭션 + 감사 JSON(구값) 을 DB 옆에 기록
옵션: --db PATH (기본 data/db/drug_prices.db), --query (반복 가능, query_name 소문자 일치), --country (반복 가능),
      --json (계획을 JSON 으로도 출력). 멱등: 두 번째 실행은 변경 0건.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import sys
import types
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 이 스크립트가 다시 쓰는 컬럼 (제형 배정 + per-vial 강도 + 저장 daily_cost 정합)
FORMULATION_COLS = (
    "unit_strength_mg", "reference_strength_mg", "dose_norm_factor", "adjusted_price_krw_normalized",
    "dose_norm_note", "formulation_key", "formulation_label", "canonical_strength_mg", "route",
    "formulation_source", "is_us_listed",
)
DAILY_COLS = ("total_pkg_mg", "daily_cost_krw", "daily_cost_note")
WRITE_COLS = FORMULATION_COLS + DAILY_COLS
SHOW_COLS = ("unit_strength_mg", "reference_strength_mg", "dose_norm_factor", "formulation_key",
             "formulation_label", "route", "daily_cost_krw")


def _load_agent_module():
    """agents.foreign_price_agent — 정식 의존성이 없으면(pandas 등) 격리 stub 으로 모듈 파일만 로드한다.
    (이 스크립트가 쓰는 건 순수 파싱/배정 함수뿐 — DB·스크레이퍼·환율 클래스는 쓰지 않는다.)"""
    try:
        import agents.foreign_price_agent as mod
        return mod
    except ImportError:
        pass
    stubs: dict[str, types.ModuleType] = {}

    def _stub(name, **attrs):
        m = types.ModuleType(name)
        m.__dict__.update(attrs)
        stubs[name] = m

    class _Dummy:
        def __init__(self, *a, **k):
            pass

    _stub("agents.db", DrugPriceDB=_Dummy)
    _stub("agents.exchange_rate", ExchangeRateFetcher=_Dummy, PriceCalculator=_Dummy)
    _stub("agents.scrapers.base", ScraperConfigError=type("ScraperConfigError", (Exception,), {}),
          ScraperLoginError=type("ScraperLoginError", (Exception,), {}), load_credentials=lambda *a, **k: {})
    for mod_name, cls in [("jp_mhlw", "JpMhlwScraper"), ("it_aifa", "ItAifaScraper"),
                          ("it_aifa_tnf", "ItAifaTnfScraper"), ("fr_bdpm", "FrBdpmScraper"),
                          ("fr_vidal", "FrVidalScraper"), ("ch_compendium", "ChCompendiumScraper"),
                          ("uk_mims", "UkMimsScraper"), ("de_rote_liste", "DeRoteListeScraper"),
                          ("ca_ontario", "CaOntarioScraper"), ("us_micromedex", "UsMicromedexScraper")]:
        _stub(f"agents.scrapers.{mod_name}", **{cls: _Dummy})
    spec = importlib.util.spec_from_file_location("_repair_foreign_price_agent",
                                                  ROOT / "agents" / "foreign_price_agent.py")
    mod = importlib.util.module_from_spec(spec)
    import agents.scrapers  # noqa: F401
    with patch.dict(sys.modules, stubs):
        spec.loader.exec_module(mod)
    return mod


fpa = _load_agent_module()
A = fpa.ForeignPriceAgent
import agents.foreign_dose_normalize as dn  # noqa: E402


class _CaptureDB:
    """_assign_formulations_and_persist 의 DB 쓰기를 가로챈다 (실제 쓰기는 main 이 한 트랜잭션으로)."""
    def __init__(self):
        self.updates: dict[int, dict] = {}

    def update_foreign_dose_norm(self, row_id, fields):
        self.updates[row_id] = dict(fields)


def qg_reasons(row: dict) -> list[str]:
    """quality_guard.checks.check_foreign_price_data 의 injection_concentration_as_unit 판정 복제 (쓰기 없음)."""
    if row.get("local_price") is None or (row.get("form_type") or "").lower() != "injection":
        return []
    ds, pu = row.get("dosage_strength") or "", row.get("package_unit") or ""
    pn, df = row.get("product_name") or "", row.get("dosage_form") or ""
    derived = A._extract_per_unit_mg("injection", ds, pu, product_name=pn, dosage_form=df)
    conc = None
    for text in (ds, df, pn):
        m = A._MG_PER_ML.search(text.translate(A._ZENKAKU))
        if m:
            conc = float(m.group(1))
            break
    stored = row.get("unit_strength_mg")
    out = []
    if stored is not None and conc is not None and abs(float(stored) - conc) < 1e-6:
        out.append("stored==conc")
    if stored is not None and derived is not None and abs(float(stored) - derived) > 0.01:
        out.append("stored!=derived")
    if derived is None and row.get("daily_cost_krw") is not None:
        out.append("daily_cost_without_unit")
    return out


def _load_dosing(conn, query: str, rows: list[dict]) -> dict:
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='foreign_drug_dosing'").fetchone():
        return {}
    keys = {query.lower()}
    for r in rows:
        ing = (r.get("ingredient") or "").strip()
        if ing:
            keys.add(ing.replace(",", " ").replace("(", " ").split()[0].lower())
    out = {}
    for k in keys:
        row = conn.execute("SELECT * FROM foreign_drug_dosing WHERE ingredient_key = ?", (k,)).fetchone()
        if row:
            out[k] = dict(row)
    return out


def _same(a, b) -> bool:
    a = None if a == "" else a          # 빈 문자열 note ↔ NULL 은 같은 것으로 본다
    b = None if b == "" else b
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) < 1e-6
    return str(a) == str(b)


def _canonical_groups(conn, names: list[str]) -> dict[str, list[str]]:
    """query_name → canonical(molecule) 그룹. 읽기 경로(get_foreign_presentations)가 aliases 로 브랜드/INN 행을 함께
    묶으므로(keytruda+pembrolizumab) 제형 배정도 같은 묶음으로 돌려야 canonical·농도 remap 이 일치한다.
    1) 대상 DB 의 product_alias_map (있으면)  2) agents/db/drug_aliases.BRAND_TO_MOLECULE (파일에서 직접 로드 — pandas 불요)."""
    spec = importlib.util.spec_from_file_location("_repair_drug_aliases", ROOT / "agents" / "db" / "drug_aliases.py")
    da = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(da)
    table: dict[str, str] = {}
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='product_alias_map'").fetchone():
        for slug, inn, ba_json in conn.execute("SELECT product_slug, inn, brand_aliases_json FROM product_alias_map"):
            canon = (inn or slug or "").lower()
            try:
                ba = json.loads(ba_json or "[]")
            except ValueError:
                ba = []
            for k in [slug, inn, *ba]:
                if k:
                    table[str(k).lower()] = canon
    groups: dict[str, list[str]] = {}
    for n in names:
        canon = table.get(n) or da.BRAND_TO_MOLECULE.get(n, n)
        groups.setdefault(canon, []).append(n)
    return groups


def plan_query(conn, query: str, countries: set[str] | None, names: list[str] | None = None) -> list[dict]:
    names = sorted({n.lower() for n in (names or [query])})
    marks = ",".join("?" * len(names))
    rows = [dict(r) for r in conn.execute(
        f"SELECT * FROM foreign_drug_prices WHERE LOWER(query_name) IN ({marks}) ORDER BY id", names)]
    if not rows:
        return []
    items = [dict(r) for r in rows]
    agent = A.__new__(A)
    agent.db = _CaptureDB()
    pending_ids: set[int] = set()

    def _no_llm(drug, pending, canonical, us_based, updates):   # 네트워크 금지 — pending 행은 기존 배정 유지
        pending_ids.update(it.get("id") for it in pending if it.get("id"))

    with patch.object(dn, "_llm_match", _no_llm):
        agent._assign_formulations_and_persist(query, items)
    updates = agent.db.updates

    dosing_map = _load_dosing(conn, query, rows)
    consensus = dn.route_consensus(items)
    plans = []
    for old, it in zip(rows, items):
        if countries and (old.get("country") or "").upper() not in countries:
            continue
        rid = old["id"]
        form = (old.get("form_type") or "unknown").lower()
        new = {c: old.get(c) for c in WRITE_COLS}
        upd = updates.get(rid)
        if upd and rid not in pending_ids:
            new.update({k: upd[k] for k in FORMULATION_COLS if k in upd})
        elif form == "injection":
            new["unit_strength_mg"] = it.get("unit_strength_mg")       # pending: 강도만 (제형 배정은 유지)
        # 저장 daily_cost 정합 — 읽기 경로(get_cached_results)는 매번 재계산하지만 QG 는 저장값을 본다
        if form == "injection":
            unit_mg = A._extract_per_unit_mg(form, old.get("dosage_strength") or "", old.get("package_unit") or "",
                                             product_name=old.get("product_name") or "",
                                             dosage_form=old.get("dosage_form") or "")
            if unit_mg is None:
                amb = A._injection_unit_ambiguity(old.get("dosage_strength") or "", old.get("package_unit") or "",
                                                  old.get("product_name") or "", old.get("dosage_form") or "")
                new["daily_cost_krw"] = None
                new["total_pkg_mg"] = None
                new["daily_cost_note"] = "unit_mg_ambiguous" if amb else "unit_mg_unresolved"
            elif not _same(old.get("unit_strength_mg"), unit_mg) and old.get("daily_cost_krw") is not None:
                # per-vial mg 가 바뀐 행만 저장 daily_cost 재계산 (저장 adj·pack_count 기준 — 읽기 경로와 같은 산식)
                dosing = agent._match_dosing(query, old.get("ingredient") or "", dosing_map)
                probe = dict(old)
                probe["pack_count"] = old.get("pack_count") or 1
                agent._populate_daily_cost(probe, dosing)
                for c in DAILY_COLS:
                    new[c] = probe.get(c)
        # 정규화가는 보정계수가 바뀐 행만 다시 쓴다 (읽기 경로가 매번 adj×factor 로 재계산 — 무관한 stale diff 방지)
        if _same(old.get("dose_norm_factor"), new.get("dose_norm_factor")):
            new["adjusted_price_krw_normalized"] = old.get("adjusted_price_krw_normalized")
        elif new.get("dose_norm_factor") and old.get("adjusted_price_krw") is not None:
            new["adjusted_price_krw_normalized"] = int(round(old["adjusted_price_krw"] * new["dose_norm_factor"]))
        changed = {c for c in WRITE_COLS if not _same(old.get(c), new.get(c))}
        # 범위 제한: 주사 행, 또는 **이미 배정된** route 가 바뀌는 행(unknown 경로 판정)만. 미배정 경구 행은
        # get_cached_results 가 lazy 배정하므로 여기서 건드리지 않는다(무관한 경구 배정 diff 방지).
        route_flip = "route" in changed and old.get("route") is not None
        if not changed or not (form == "injection" or route_flip):
            continue
        if form != "injection":
            changed = {c for c in changed if c in FORMULATION_COLS}
        probe_old = dict(old)
        pc = agent._resolve_pack_count(old.get("country"), probe_old, agent._match_dosing(
            query, old.get("ingredient") or "", dosing_map))
        probe_old["pack_count"] = pc
        old_ok = fpa.a8_aggregate_eligibility(old.get("country"), probe_old, check_injection_unit=False)
        new_ok = fpa.a8_aggregate_eligibility(old.get("country"), dict(probe_old, **new))
        plans.append({
            "id": rid, "country": old.get("country"), "query": old.get("query_name"),
            "product_name": old.get("product_name"), "dosage_strength": old.get("dosage_strength"),
            "dosage_form": old.get("dosage_form"), "package_unit": old.get("package_unit"),
            "form_type": old.get("form_type"), "route_consensus": consensus,
            "llm_pending_kept": rid in pending_ids,
            "inherited_from": it.get("strength_inherited_from"),
            "ambiguity": it.get("unit_strength_ambiguous"),
            "pack_count_stored": old.get("pack_count"), "pack_count_read_time": pc,
            "old": {c: old.get(c) for c in WRITE_COLS}, "new": new, "changed": sorted(changed),
            "a8_old": list(old_ok), "a8_new": list(new_ok),
            "qg_old": qg_reasons(old), "qg_new": qg_reasons(dict(old, **new)),
        })
    return plans


def _fmt(v) -> str:
    if v is None:
        return "∅"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def print_table(plans: list[dict]) -> None:
    for p in plans:
        print(f"\n#{p['id']} {p['country']} {p['query']} form={p['form_type']} — {(p['product_name'] or '')[:70]}")
        print(f"    ds={(p['dosage_strength'] or '')[:80]!r} df={(p['dosage_form'] or '')[:50]!r} "
              f"pu={(p['package_unit'] or '')[:40]!r} pack(stored→read-time)={p['pack_count_stored']}→{p['pack_count_read_time']}")
        for c in SHOW_COLS + ("dose_norm_note", "daily_cost_note", "total_pkg_mg"):
            o, n = p["old"].get(c), p["new"].get(c)
            mark = "→" if not _same(o, n) else "="
            if mark == "→" or c in ("unit_strength_mg", "formulation_key", "route"):
                print(f"    {c:24s} {_fmt(o):>30s} {mark} {_fmt(n)}")
        extra = []
        if p["inherited_from"]:
            extra.append(f"strength inherited from {p['inherited_from']}")
        if p["ambiguity"]:
            extra.append(f"ambiguous: {p['ambiguity']}")
        if p["llm_pending_kept"]:
            extra.append("LLM-pending: formulation kept")
        print(f"    a8_eligible {p['a8_old']} → {p['a8_new']} | QG {p['qg_old'] or 'clean'} → {p['qg_new'] or 'clean'}"
              + (f" | {'; '.join(extra)}" if extra else ""))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="해외약가 주사 per-vial 강도·route·제형 배정 정정 (기본 dry-run)")
    ap.add_argument("--db", default=str(ROOT / "data" / "db" / "drug_prices.db"))
    ap.add_argument("--query", action="append", default=[])
    ap.add_argument("--country", action="append", default=[])
    ap.add_argument("--apply", action="store_true", help="실제 쓰기 (기본 dry-run, 읽기 전용)")
    ap.add_argument("--json", action="store_true", help="계획을 JSON 으로도 출력")
    args = ap.parse_args(argv)

    db = Path(args.db).resolve()
    if not db.exists():
        print(f"DB 없음: {db}", file=sys.stderr)
        return 2
    uri = f"{db.as_uri()}?mode={'rw' if args.apply else 'ro'}"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    countries = {c.upper() for c in args.country} or None
    sql = "SELECT DISTINCT LOWER(query_name) FROM foreign_drug_prices WHERE query_name IS NOT NULL"
    all_names = sorted(r[0] for r in conn.execute(sql))
    groups = _canonical_groups(conn, all_names)
    if args.query:
        wanted = {q.lower() for q in args.query}
        groups = {c: ns for c, ns in groups.items() if c in wanted or wanted & set(ns)}
    queries = sorted(groups)
    plans: list[dict] = []
    for canon in queries:
        plans.extend(plan_query(conn, canon, countries, names=groups[canon]))

    print(f"[repair_foreign_strength] db={db} mode={'APPLY' if args.apply else 'DRY-RUN (read-only)'} "
          f"queries={len(queries)} countries={sorted(countries) if countries else 'all'}")
    print_table(plans)
    qg_before = sum(1 for p in plans if p["qg_old"])
    qg_after = sum(1 for p in plans if p["qg_new"])
    a8_flip = sum(1 for p in plans if p["a8_old"][0] != p["a8_new"][0])
    by_country = {c: sum(1 for p in plans if p["country"] == c) for c in sorted({p["country"] for p in plans})}
    print(f"\nSUMMARY rows_to_change={len(plans)} qg_injection_findings(rows) {qg_before}→{qg_after} "
          f"a8_eligibility_changes={a8_flip} by_country={json.dumps(by_country)}")
    if args.json:
        print(json.dumps(plans, ensure_ascii=False, indent=1, default=str))

    if not args.apply:
        conn.close()
        return 0
    if not plans:
        print("변경 없음 — 쓰지 않음 (멱등)")
        conn.close()
        return 0
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    audit_path = db.parent / f"{db.name}.repair_foreign_strength_{ts}.json"
    audit = {"created_at": ts, "db": str(db), "filters": {"query": args.query, "country": args.country},
             "rows": [{"id": p["id"], "country": p["country"], "query": p["query"],
                       "old": {c: p["old"][c] for c in p["changed"]},
                       "new": {c: p["new"][c] for c in p["changed"]}} for p in plans]}
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    try:
        with conn:   # 한 트랜잭션 — 실패 시 전체 롤백
            for p in plans:
                sets = {c: p["new"][c] for c in p["changed"]}
                assignments = ", ".join(f"{c}=:{c}" for c in sets)
                conn.execute(f"UPDATE foreign_drug_prices SET {assignments} WHERE id=:_id", {**sets, "_id": p["id"]})
    finally:
        conn.close()
    print(f"APPLIED {len(plans)} rows. audit(old values)={audit_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
