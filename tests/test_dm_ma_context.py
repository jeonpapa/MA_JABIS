from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agents.daily_mailing import ma_context
from agents.daily_mailing.ma_context import (
    DEFAULT_MA_CONTEXT_PATH,
    ContextPack,
    DrugContext,
    build_context_pack,
    load_ma_context,
    parse_ma_context_yaml,
    render_context_block,
)

YAML = """# 주석
version: 1
msd_assets:
  - brand_kr: 키트루다
    brand_en: Keytruda
    ingredient: 펨브롤리주맙
    inn: pembrolizumab
    class: PD-1
    aliases: [키트루다주, "MK-3475"]
    indication_slug: keytruda
    note: "면역항암제 · 다수 적응증"
  - brand_kr: 웰리렉
    brand_en: Welireg
    ingredient: 벨주티판
    inn: belzutifan
    class: HIF-2α
    indication_slug: welireg
competitors:
  - brand_kr: 테빔브라   # 같은 PD-1
    brand_en: Tevimbra
    ingredient: 티슬렐리주맙
    inn: tislelizumab
    class: PD-1
    company: BeiGene Korea
    watch_for: [키트루다]
    price_match: 테빔브라주
  - brand_kr: 옵디보
    brand_en: Opdivo
    inn: nivolumab
    class: PD-1
    watch_for: [키트루다]
    price_match: 옵디보주100mg
needs_review:
  - brand_kr: 파드셉
    question: "키트루다 병용 파트너 — 경쟁으로 볼지"
"""


def _db(tmp_path: Path, *, with_tables: bool = True) -> str:
    db = tmp_path / "ctx.db"
    with sqlite3.connect(db) as c:
        if not with_tables:
            c.execute("CREATE TABLE other (x)")
            return str(db)
        c.execute("CREATE TABLE drug_prices (id INTEGER PRIMARY KEY, apply_date TEXT, insurance_code TEXT, "
                  "product_name_kr TEXT, ingredient TEXT, dosage_strength TEXT, package_unit TEXT, max_price INTEGER, company TEXT)")
        c.executemany("INSERT INTO drug_prices (apply_date, insurance_code, product_name_kr, ingredient, max_price, company) VALUES (?,?,?,?,?,?)", [
            ("2026.06.01", "K1", "키트루다주(펨브롤리주맙,유전자재조합)_(0.1g/4mL)", "pembrolizumab", 2050000, "한국엠에스디(유)"),
            ("2026.07.01", "K1", "키트루다주(펨브롤리주맙,유전자재조합)_(0.1g/4mL)", "pembrolizumab", 2100556, "한국엠에스디(유)"),
            ("2026.07.01", "T1", "테빔브라주100밀리그램(티슬렐리주맙)_(0.1g/1병)", "tislelizumab", 1206000, "비원메디슨코리아(유)"),
            ("2026.07.01", "O1", "옵디보주20mg(니볼루맙,유전자재조합)_(20mg/2mL)", "nivolumab", 279377, "오노"),
            ("2026.07.01", "O2", "옵디보주100mg(니볼루맙,유전자재조합)_(0.1g/10mL)", "nivolumab", 1117646, "오노"),
            ("2026.07.01", "O3", "옵디보주240mg(니볼루맙,유전자재조합)_(0.24g/24mL)", "nivolumab", 2531829, "오노"),
        ])
        c.execute("CREATE TABLE indications_master (indication_id TEXT PRIMARY KEY, product TEXT, disease TEXT)")
        c.executemany("INSERT INTO indications_master VALUES (?,?,?)",
                      [(f"keytruda_{i}", "keytruda", "NSCLC") for i in range(6)] + [("welireg_1", "welireg", "VHL")])
        c.execute("CREATE TABLE indications_by_agency (id INTEGER PRIMARY KEY, indication_id TEXT, agency TEXT)")
        c.executemany("INSERT INTO indications_by_agency (indication_id, agency) VALUES (?,?)",
                      [(f"keytruda_{i}", "MFDS") for i in range(3)] + [(f"keytruda_{i}", "FDA") for i in range(6)])
        c.execute("CREATE TABLE indication_reimbursement (indication_id TEXT PRIMARY KEY, is_reimbursed INTEGER, effective_date TEXT)")
        c.executemany("INSERT INTO indication_reimbursement VALUES (?,?,?)",
                      [("keytruda_0", 1, "2017-08-21"), ("keytruda_1", 1, None), ("keytruda_2", 0, None), ("keytruda_3", 0, None)])
        c.execute("CREATE TABLE drug_enrichment (normalized_name TEXT PRIMARY KEY, is_rsa INTEGER, rsa_type TEXT, approval_date TEXT)")
        c.executemany("INSERT INTO drug_enrichment VALUES (?,?,?,?)", [
            ("키트루다주", None, None, "2015-03-20"),
            ("옵디보주100mg", 1, "refund", None),
            ("옵디보주120mg", None, None, None),
            ("테빔브라주100mg", None, None, None),
        ])
    return str(db)


@pytest.fixture
def yaml_path(tmp_path: Path) -> Path:
    p = tmp_path / "ma_context.yaml"
    p.write_text(YAML, encoding="utf-8")
    return p


# ---- YAML (PyYAML 없이 통제된 형태만) ----
def test_parse_controlled_yaml_shape():
    data = parse_ma_context_yaml(YAML)
    assert data["version"] == 1
    assert [a["brand_kr"] for a in data["msd_assets"]] == ["키트루다", "웰리렉"]
    k = data["msd_assets"][0]
    assert k["aliases"] == ["키트루다주", "MK-3475"]
    assert k["note"] == "면역항암제 · 다수 적응증"
    assert k["class"] == "PD-1"
    t = data["competitors"][0]
    assert t["brand_kr"] == "테빔브라"                     # 줄 끝 주석 제거
    assert t["watch_for"] == ["키트루다"]
    assert data["needs_review"][0]["question"] == "키트루다 병용 파트너 — 경쟁으로 볼지"


def test_load_ma_context_builds_drug_contexts(yaml_path):
    cfg = load_ma_context(yaml_path)
    assert [a.brand_kr for a in cfg.msd_assets] == ["키트루다", "웰리렉"]
    k = cfg.msd_assets[0]
    assert k.role == "msd" and k.drug_class == "PD-1" and k.inn == "pembrolizumab"
    assert k.aliases == ("키트루다주", "MK-3475")
    t = cfg.competitors[0]
    assert t.role == "competitor" and t.watch_for == ("키트루다",) and t.price_match == "테빔브라주"
    assert cfg.needs_review[0]["brand_kr"] == "파드셉"


def test_load_ma_context_rejects_competitor_watching_unknown_asset(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("msd_assets:\n  - brand_kr: 키트루다\ncompetitors:\n  - brand_kr: X\n    watch_for: [없는약]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="없는약"):
        load_ma_context(bad)


# ---- DB 보강 ----
def test_build_context_pack_pulls_latest_price_indications_reimbursement_and_rsa(tmp_path, yaml_path):
    pack = build_context_pack(["키트루다", "급여"], db_path=_db(tmp_path), yaml_path=yaml_path)
    k = pack.asset("키트루다")
    assert k.price_krw == 2100556 and k.price_date == "2026.07.01"        # 최신 apply_date
    assert k.price_unit == "100mg/4mL"                                     # product_name_kr 꼬리 _(0.1g/4mL)
    assert k.indication_count == 6 and k.mfds_indication_count == 3
    assert k.reimbursed_count == 2 and k.reimbursement_rows == 4
    assert k.is_rsa is None                                                # enrichment NULL → 미확인 (지어내지 않음)
    t = pack.competitor("테빔브라")
    assert t.price_krw == 1206000 and t.price_unit == "100mg/1병" and t.price_date == "2026.07.01"
    assert t.indication_count is None and t.reimbursed_count is None       # 마스터에 없음 → 부재
    o = pack.competitor("옵디보")
    assert o.price_krw == 1117646 and o.price_unit == "100mg/10mL"        # price_match 로 SKU 고정
    assert o.is_rsa is True and o.rsa_type == "refund"                     # enrichment 여러 행 중 is_rsa 있는 행
    w = pack.asset("웰리렉")
    assert w.price_krw is None and w.price_unit == "" and w.indication_count == 1   # 약가 미등재는 부재


def test_build_context_pack_without_db_or_tables_has_no_db_fields(tmp_path, yaml_path):
    none = build_context_pack(["키트루다"], db_path=None, yaml_path=yaml_path)
    assert none.asset("키트루다").price_krw is None and none.asset("키트루다").indication_count is None
    empty = build_context_pack(["키트루다"], db_path=_db(tmp_path, with_tables=False), yaml_path=yaml_path)
    assert empty.asset("키트루다").price_krw is None
    missing = build_context_pack(["키트루다"], db_path=str(tmp_path / "nope.db"), yaml_path=yaml_path)
    assert missing.asset("키트루다").price_krw is None
    assert [a.brand_kr for a in missing.msd_assets] == ["키트루다", "웰리렉"]


def test_build_context_pack_multi_sku_without_price_match_takes_lowest_price(tmp_path, yaml_path):
    yaml_path.write_text(YAML.replace("    price_match: 옵디보주100mg\n", ""), encoding="utf-8")
    pack = build_context_pack(["옵디보"], db_path=_db(tmp_path), yaml_path=yaml_path)
    assert pack.competitor("옵디보").price_krw == 279377 and pack.competitor("옵디보").price_unit == "20mg/2mL"


def test_build_context_pack_uses_shared_storage_connect(tmp_path, yaml_path, monkeypatch):
    calls = []
    real = ma_context.connect

    def spy(db, **kw):
        calls.append(db)
        return real(db, **kw)

    monkeypatch.setattr(ma_context, "connect", spy)
    build_context_pack(["키트루다"], db_path=_db(tmp_path), yaml_path=yaml_path)
    assert calls and all(str(c).endswith("ctx.db") for c in calls)


# ---- 스콥 관련성 · 이름 해석 ----
def test_pack_scope_relevance_and_asset_resolution(tmp_path, yaml_path):
    pack = build_context_pack(["Keytruda", "약가 인하"], db_path=_db(tmp_path), yaml_path=yaml_path)
    assert pack.asset("키트루다").in_scope is True and pack.asset("웰리렉").in_scope is False
    assert pack.competitor("테빔브라").in_scope is True          # watch_for 가 스콥 자산
    assert pack.resolve_msd_asset("Keytruda") == "키트루다"
    assert pack.resolve_msd_asset("펨브롤리주맙") == "키트루다"
    assert pack.resolve_msd_asset("키트루다주") == "키트루다"
    assert pack.resolve_msd_asset(" 키트루다 ") == "키트루다"
    assert pack.resolve_msd_asset("테빔브라") is None            # 경쟁약은 MSD 자산이 아니다
    assert pack.resolve_msd_asset("") is None
    assert pack.msd_asset_names() == ("키트루다", "웰리렉")
    policy = build_context_pack(["제네릭 약가"], db_path=None, yaml_path=yaml_path)
    assert not any(a.in_scope for a in policy.msd_assets) and not any(c.in_scope for c in policy.competitors)


# ---- 프롬프트 블록 렌더 ----
def test_render_context_block_lines(tmp_path, yaml_path):
    pack = build_context_pack(["키트루다", "웰리렉"], db_path=_db(tmp_path), yaml_path=yaml_path)
    block = render_context_block(pack)
    assert "키트루다(펨브롤리주맙, PD-1) 약가 2,100,556원/100mg/4mL (2026.07.01) · 허가 적응증 MFDS 3 (master 6) · 급여 적응증 2/4" in block
    assert "RSA" not in block.split("키트루다(")[1].split("\n")[0]     # NULL 은 렌더하지 않는다
    assert "웰리렉(벨주티판, HIF-2α) 약가 미등재 · 허가 적응증 master 1" in block
    assert "그 외:" not in block
    assert "경쟁 계열 PD-1" in block
    assert "테빔브라(티슬렐리주맙, BeiGene Korea) 약가 1,206,000원/100mg/1병 (2026.07.01)" in block
    assert "옵디보(nivolumab) 약가 1,117,646원/100mg/10mL (2026.07.01) · RSA 대상(refund)" in block


def test_render_context_block_policy_scope_adds_competitors_only_when_article_mentions_them(tmp_path, yaml_path):
    pack = build_context_pack(["제네릭 약가"], db_path=_db(tmp_path), yaml_path=yaml_path)
    plain = render_context_block(pack)
    assert "- 그 외: 키트루다(펨브롤리주맙, PD-1) · 웰리렉(벨주티판, HIF-2α)" in plain   # 포트폴리오는 항상, 수치는 관련 자산만
    assert "2,100,556" not in plain
    assert "테빔브라" not in plain and "경쟁 계열" not in plain
    with_article = render_context_block(pack, article_text="테빔브라 급여 확대로 키트루다와 같은 적응증")
    assert "키트루다(펨브롤리주맙, PD-1) 약가 2,100,556원" in with_article        # 기사 언급 자산은 상세
    assert "- 그 외: 웰리렉(벨주티판, HIF-2α)" in with_article
    assert "테빔브라(" in with_article and "옵디보" not in with_article


def test_render_context_block_is_bounded(tmp_path, yaml_path):
    pack = build_context_pack(["키트루다"], db_path=_db(tmp_path), yaml_path=yaml_path)
    assert len(render_context_block(pack, max_chars=120)) <= 120
    assert render_context_block(ContextPack(msd_assets=(), competitors=(), scope_terms=())) == ""


def test_rendered_numbers_are_extractable_for_validation(tmp_path, yaml_path):
    pack = build_context_pack(["키트루다"], db_path=_db(tmp_path), yaml_path=yaml_path)
    nums = pack.numbers()
    assert {"2100556", "1206000", "100", "4", "2026.07.01", "2026", "3", "6", "2"} <= nums


# ---- 실제 config/ma_context.yaml ----
def test_repo_yaml_seeds_keytruda_class_and_tevimbra_competitor():
    assert DEFAULT_MA_CONTEXT_PATH.name == "ma_context.yaml"
    cfg = load_ma_context(DEFAULT_MA_CONTEXT_PATH)
    names = {a.brand_kr for a in cfg.msd_assets}
    for brand in ("키트루다", "가다실", "린파자", "웰리렉", "브리디온", "에멘드", "윈레브에어", "박스뉴반스", "엔플론시아"):
        assert brand in names, brand
    k = next(a for a in cfg.msd_assets if a.brand_kr == "키트루다")
    assert k.inn == "pembrolizumab" and k.drug_class == "PD-(L)1"
    t = next(c for c in cfg.competitors if c.brand_kr == "테빔브라")
    assert t.inn == "tislelizumab" and "키트루다" in t.watch_for and t.drug_class == k.drug_class
    assert len(cfg.msd_assets) + len(cfg.competitors) <= 40          # 작게 유지
    assert len({d.brand_kr for d in cfg.msd_assets + cfg.competitors}) == len(cfg.msd_assets) + len(cfg.competitors)
    assert cfg.needs_review                                            # 확신 없는 항목은 확인 목록에


def test_default_pack_renders_without_db(tmp_path):
    pack = build_context_pack(["키트루다"], db_path=None)
    block = render_context_block(pack)
    assert "키트루다(펨브롤리주맙, PD-(L)1" in block and "테빔브라(" in block
    assert len(block) <= 1800


def test_drug_context_defaults():
    d = DrugContext(brand_kr="x")
    assert d.price_krw is None and d.price_unit == "" and d.in_scope is False and d.role == "msd"
