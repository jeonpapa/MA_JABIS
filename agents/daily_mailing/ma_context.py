"""Daily Mailing MA 컨텍스트 팩 — 작성 LLM 에 주는 '한국MSD 포트폴리오 + 직접 경쟁약' 사실 블록.

2026-09-30 파일럿: '사용범위 넓어진 키트루다·테빔브라, RSA 도 다시 계약' 기사에 '키트루다에 긍정적' 이라는 시사점이 붙었다.
MA 리더의 실제 논점은 반대다 — 같은 PD-1 계열 테빔브라가 같은 급여 적응증을 훨씬 낮은 약가로 가져가면 키트루다 RSA 조건이
압박받는다. 작성자가 이것을 말할 수 없었던 이유는 시사점 근거가 '기사 본문' 뿐이라 포트폴리오·경쟁약 약가·RSA 상태를 몰랐기 때문이다.

구성:
- 큐레이션 `config/ma_context.yaml` (PyYAML 없이 통제된 형태만 파싱): MSD 자산 + 계열별 직접 경쟁약. 확신 없는 항목은
  `needs_review` 에만 두고 팩에 넣지 않는다 (사용자 확인 후 승격).
- DB 보강 (읽기 전용, storage.connect 공용 헬퍼): 최신 `drug_prices` 행(상한가·적용일·최소단위), `indications_master` 적응증 수,
  `indications_by_agency` MFDS 적응증 수, `indication_reimbursement` 급여 적응증 수, `drug_enrichment` RSA 여부.
  없는 데이터는 그냥 '부재' — 절대 지어내지 않는다 (렌더에서도 생략).
- 렌더 `render_context_block` 은 약제당 한 줄, 수백 토큰 상한. MSD 포트폴리오는 항상, 경쟁약은 스콥 관련(같은 계열의 MSD 자산이
  스콥에 있거나 이름이 스콥에 있음) 또는 기사 본문에 이름이 나올 때만.
- 코드 검증용: `ContextPack.numbers()` (시사점의 숫자가 팩·본문에 있어야 함), `resolve_msd_asset()` (affected_asset 화이트리스트).
"""
from __future__ import annotations

import ast
import logging
import re
import sqlite3
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable

from .storage import connect

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MA_CONTEXT_PATH = REPO_ROOT / "config" / "ma_context.yaml"
DEFAULT_BLOCK_MAX_CHARS = 1500
MIN_NAME_MATCH_CHARS = 2
_NUMBER_TOKEN = re.compile(r"\d+(?:[.,]\d+)*")
_PRICE_UNIT_TAIL = re.compile(r"_\((.+)\)\s*$")
_GRAMS = re.compile(r"^(\d+(?:\.\d+)?)g(?=/|$)")
_KV_LINE = re.compile(r"^([^:#][^:]*):(?:\s+(.*))?$")


# ---------------------------------------------------------------------------
# YAML (통제된 형태: 최상위 스칼라 또는 '- k: v' 항목 리스트, 인라인 리스트, 따옴표, 주석)
# ---------------------------------------------------------------------------
def _strip_comment(value: str) -> str:
    if value.startswith(("'", '"')):
        return value
    return re.split(r"\s+#", value, maxsplit=1)[0].rstrip()


def _scalar(value: str) -> Any:
    value = _strip_comment(value.strip())
    if not value:
        return ""
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        return [_scalar(part) for part in _split_inline_list(inner)] if inner else []
    if value[0] in {"'", '"'}:
        try:
            return ast.literal_eval(value)
        except Exception:
            return value.strip("\"'")
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    if value.lower() in {"null", "~"}:
        return None
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return value


def _split_inline_list(inner: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    for ch in inner:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif ch in {"'", '"'}:
            quote = ch
            buf.append(ch)
        elif ch == ",":
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def parse_ma_context_yaml(text: str) -> dict:
    """`config/ma_context.yaml` 의 통제된 형태만 파싱한다 (source_registry 와 같은 이유로 PyYAML 의존 없음).

    지원: `key: scalar` · `key:` + `- k: v` 항목(연속 `k: v` 줄) · `[a, b]` 인라인 리스트 · 따옴표 문자열 · `#` 주석."""
    data: dict[str, Any] = {}
    current_list: list[dict] | None = None
    current_item: dict | None = None
    for lineno, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        if indent == 0:
            m = _KV_LINE.match(line)
            if not m:
                raise ValueError(f"ma_context.yaml {lineno}행: 최상위 'key:' 형태가 아님: {line!r}")
            key, value = m.group(1).strip(), (m.group(2) or "").strip()
            if _strip_comment(value):
                data[key] = _scalar(value)
                current_list, current_item = None, None
            else:
                current_list = data.setdefault(key, [])
                current_item = None
            continue
        if line.startswith("- "):
            if current_list is None:
                raise ValueError(f"ma_context.yaml {lineno}행: 리스트 항목이 최상위 키 밖에 있음")
            current_item = {}
            current_list.append(current_item)
            line = line[2:].strip()
            if not line:
                continue
        if current_item is None:
            raise ValueError(f"ma_context.yaml {lineno}행: 항목('- ') 밖의 들여쓰기 줄: {line!r}")
        m = _KV_LINE.match(line)
        if not m:
            raise ValueError(f"ma_context.yaml {lineno}행: 'k: v' 형태가 아님: {line!r}")
        current_item[m.group(1).strip()] = _scalar(m.group(2) or "")
    return data


# ---------------------------------------------------------------------------
# 모델
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DrugContext:
    brand_kr: str
    brand_en: str = ""
    ingredient: str = ""            # 한글 성분명
    inn: str = ""                   # 영문 INN
    drug_class: str = ""
    role: str = "msd"               # msd | competitor
    company: str = ""
    watch_for: tuple[str, ...] = ()   # 경쟁약 → 압박하는 MSD 자산(brand_kr)
    aliases: tuple[str, ...] = ()
    note: str = ""
    price_match: str = ""           # drug_prices.product_name_kr 접두 (기본 brand_kr) — 다중 SKU 고정용
    indication_slug: str = ""       # indications_master.product (기본 brand_en 소문자)
    enrichment_name: str = ""       # drug_enrichment.normalized_name 접두 (기본 price_match 또는 brand_kr)
    rsa_override: bool | None = None   # 큐레이션 RSA 여부 — DB 가 NULL 일 때만 사용
    rsa_type_override: str = ""
    # ---- DB 보강 (없으면 None / "") ----
    price_krw: int | None = None
    price_unit: str = ""            # '100mg/4mL'
    price_date: str = ""            # '2026.07.01'
    indication_count: int | None = None
    mfds_indication_count: int | None = None
    reimbursed_count: int | None = None
    reimbursement_rows: int | None = None
    is_rsa: bool | None = None
    rsa_type: str = ""
    in_scope: bool = False

    def names(self) -> tuple[str, ...]:
        seen: list[str] = []
        for n in (self.brand_kr, self.brand_en, self.ingredient, self.inn, *self.aliases):
            n = (n or "").strip()
            if n and n not in seen:
                seen.append(n)
        return tuple(seen)

    def mentioned_in(self, text: str) -> bool:
        low = (text or "").lower()
        return bool(low) and any(len(n) >= MIN_NAME_MATCH_CHARS and n.lower() in low for n in self.names())


@dataclass(frozen=True)
class MaContextConfig:
    msd_assets: tuple[DrugContext, ...]
    competitors: tuple[DrugContext, ...]
    needs_review: tuple[dict, ...] = ()


@dataclass(frozen=True)
class ContextPack:
    msd_assets: tuple[DrugContext, ...]
    competitors: tuple[DrugContext, ...]
    scope_terms: tuple[str, ...]
    db_path: str = ""
    _by_name: dict = field(default_factory=dict, repr=False, compare=False)

    def asset(self, brand_kr: str) -> DrugContext | None:
        return next((a for a in self.msd_assets if a.brand_kr == brand_kr), None)

    def competitor(self, brand_kr: str) -> DrugContext | None:
        return next((c for c in self.competitors if c.brand_kr == brand_kr), None)

    def msd_asset_names(self) -> tuple[str, ...]:
        return tuple(a.brand_kr for a in self.msd_assets)

    def resolve_msd_asset(self, name: str) -> str | None:
        """작성자가 적은 affected_asset(한글·영문·성분·별칭) → MSD 자산 brand_kr. 경쟁약·미등록 → None."""
        key = (name or "").strip().lower()
        if not key:
            return None
        for a in self.msd_assets:
            if key in {n.lower() for n in a.names()}:
                return a.brand_kr
        return None

    def numbers(self) -> set[str]:
        """팩 전체(스콥 무관, 모든 경쟁약 포함)를 렌더한 텍스트의 숫자 토큰 — 시사점 숫자 검증용."""
        return extract_number_tokens(render_context_block(self, include_all=True, max_chars=10 ** 6))


# ---------------------------------------------------------------------------
# 큐레이션 로드
# ---------------------------------------------------------------------------
_CONFIG_CACHE: dict[tuple[str, int], MaContextConfig] = {}


def _tuple_of_str(value: Any) -> tuple[str, ...]:
    if value is None or value == "":
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(v).strip() for v in value if str(v).strip())
    return (str(value).strip(),)


def _drug_from_entry(entry: dict, *, role: str) -> DrugContext:
    brand_kr = str(entry.get("brand_kr") or "").strip()
    if not brand_kr:
        raise ValueError(f"ma_context.yaml: brand_kr 없는 항목: {entry!r}")
    brand_en = str(entry.get("brand_en") or "").strip()
    price_match = str(entry.get("price_match") or "").strip() or brand_kr
    rsa = entry.get("rsa")
    return DrugContext(
        brand_kr=brand_kr,
        brand_en=brand_en,
        ingredient=str(entry.get("ingredient") or "").strip(),
        inn=str(entry.get("inn") or "").strip(),
        drug_class=str(entry.get("class") or "").strip(),
        role=role,
        company=str(entry.get("company") or "").strip(),
        watch_for=_tuple_of_str(entry.get("watch_for")),
        aliases=_tuple_of_str(entry.get("aliases")),
        note=str(entry.get("note") or "").strip(),
        price_match=price_match,
        indication_slug=str(entry.get("indication_slug") or "").strip() or brand_en.lower(),
        enrichment_name=str(entry.get("enrichment_name") or "").strip() or price_match,
        rsa_override=rsa if isinstance(rsa, bool) else None,
        rsa_type_override=str(entry.get("rsa_type") or "").strip(),
    )


def load_ma_context(yaml_path: str | Path = DEFAULT_MA_CONTEXT_PATH) -> MaContextConfig:
    """큐레이션 YAML → MaContextConfig. 파일 (경로, mtime) 별 캐시. 경쟁약의 watch_for 는 등록된 MSD 자산이어야 한다."""
    path = Path(yaml_path)
    key = (str(path.resolve()), path.stat().st_mtime_ns)
    cached = _CONFIG_CACHE.get(key)
    if cached is not None:
        return cached
    data = parse_ma_context_yaml(path.read_text(encoding="utf-8"))
    assets = tuple(_drug_from_entry(e, role="msd") for e in (data.get("msd_assets") or []) if isinstance(e, dict))
    competitors = tuple(_drug_from_entry(e, role="competitor") for e in (data.get("competitors") or [])
                        if isinstance(e, dict))
    known = {a.brand_kr for a in assets}
    for c in competitors:
        unknown = [w for w in c.watch_for if w not in known]
        if unknown:
            raise ValueError(f"ma_context.yaml: 경쟁약 {c.brand_kr} 의 watch_for 에 등록되지 않은 MSD 자산: {unknown}")
    names = [d.brand_kr for d in assets + competitors]
    if len(names) != len(set(names)):
        raise ValueError("ma_context.yaml: brand_kr 중복")
    config = MaContextConfig(msd_assets=assets, competitors=competitors,
                             needs_review=tuple(e for e in (data.get("needs_review") or []) if isinstance(e, dict)))
    _CONFIG_CACHE.clear()
    _CONFIG_CACHE[key] = config
    return config


# ---------------------------------------------------------------------------
# DB 보강 (읽기 전용)
# ---------------------------------------------------------------------------
def _prefix_bounds(prefix: str) -> tuple[str, str]:
    """`col >= lo AND col < hi` 로 접두 검색 — 인덱스를 타고(LIKE 는 pragma 없이 인덱스 미사용) 한글도 안전하다."""
    return prefix, prefix + "￿"


def price_unit_label(product_name_kr: str) -> str:
    """'키트루다주(...)_(0.1g/4mL)' → '100mg/4mL', '테빔브라주100밀리그램(...)_(0.1g/1병)' → '100mg/1병'. 꼬리 없으면 ''."""
    m = _PRICE_UNIT_TAIL.search(product_name_kr or "")
    if not m:
        return ""
    label = m.group(1).strip()

    def to_mg(g: re.Match) -> str:
        mg = float(g.group(1)) * 1000
        return (f"{mg:.3f}".rstrip("0").rstrip(".") if mg != int(mg) else str(int(mg))) + "mg"

    return _GRAMS.sub(to_mg, label, count=1)


def _latest_price(conn: sqlite3.Connection, prefix: str) -> tuple[int, str, str] | None:
    lo, hi = _prefix_bounds(prefix)
    rows = conn.execute(
        "SELECT product_name_kr, max_price, apply_date FROM drug_prices "
        "WHERE product_name_kr >= ? AND product_name_kr < ? AND max_price IS NOT NULL "
        "AND apply_date = (SELECT MAX(apply_date) FROM drug_prices WHERE product_name_kr >= ? AND product_name_kr < ?) "
        "ORDER BY max_price ASC, product_name_kr ASC LIMIT 1",
        (lo, hi, lo, hi)).fetchall()
    if not rows:
        return None
    name, price, date = rows[0]
    return int(price), price_unit_label(str(name or "")), str(date or "")


def _group_counts(conn: sqlite3.Connection, sql: str) -> dict[str, tuple[int, ...]]:
    return {str(r[0]): tuple(int(x or 0) for x in r[1:]) for r in conn.execute(sql).fetchall() if r[0]}


def _safe(fn, *args, default=None):
    try:
        return fn(*args)
    except sqlite3.Error as exc:          # 테이블 없음 등 — 데이터 부재로 취급
        logger.debug("[daily_mailing.ma_context] DB 조회 생략: %s", exc)
        return default


def _pick_enrichment(rows: list[tuple], drug: DrugContext) -> tuple[bool | None, str]:
    cands = [r for r in rows if str(r[0] or "").startswith(drug.brand_kr)]
    if not cands:
        return None, ""
    pinned = [r for r in cands if str(r[0] or "").startswith(drug.enrichment_name)]
    known = [r for r in (pinned or cands) if r[1] is not None]
    row = (known or pinned or cands)[0]
    is_rsa = None if row[1] is None else bool(row[1])
    return is_rsa, str(row[2] or "") if is_rsa else ""


def _enrich_all(drugs: Iterable[DrugContext], db_path: str | Path | None) -> list[DrugContext]:
    drugs = list(drugs)
    if not db_path or not Path(db_path).exists():
        return drugs
    try:
        conn = connect(db_path)
    except sqlite3.Error as exc:
        logger.warning("[daily_mailing.ma_context] DB 연결 실패 — 컨텍스트 팩은 큐레이션만 사용: %s", exc)
        return drugs
    try:
        master = _safe(_group_counts, conn, "SELECT product, COUNT(*) FROM indications_master GROUP BY product", default={})
        mfds = _safe(_group_counts, conn,
                     "SELECT m.product, COUNT(*) FROM indications_by_agency b JOIN indications_master m USING(indication_id) "
                     "WHERE b.agency = 'MFDS' GROUP BY m.product", default={})
        reimb = _safe(_group_counts, conn,
                      "SELECT m.product, SUM(r.is_reimbursed), COUNT(*) FROM indication_reimbursement r "
                      "JOIN indications_master m USING(indication_id) GROUP BY m.product", default={})
        enrichment = _safe(lambda c: c.execute("SELECT normalized_name, is_rsa, rsa_type FROM drug_enrichment").fetchall(),
                           conn, default=[]) or []
        out: list[DrugContext] = []
        for d in drugs:
            price = _safe(_latest_price, conn, d.price_match)
            is_rsa, rsa_type = _pick_enrichment(enrichment, d)
            if is_rsa is None and d.rsa_override is not None:
                is_rsa, rsa_type = d.rsa_override, d.rsa_type_override if d.rsa_override else ""
            slug = d.indication_slug
            r = reimb.get(slug)
            out.append(replace(
                d,
                price_krw=price[0] if price else None,
                price_unit=price[1] if price else "",
                price_date=price[2] if price else "",
                indication_count=master.get(slug, (None,))[0] if slug in master else None,
                mfds_indication_count=mfds.get(slug, (None,))[0] if slug in mfds else None,
                reimbursed_count=r[0] if r else None,
                reimbursement_rows=r[1] if r else None,
                is_rsa=is_rsa,
                rsa_type=rsa_type,
            ))
        return out
    finally:
        conn.close()


def _term_hits(drug: DrugContext, terms: Iterable[str]) -> bool:
    names = [n.lower() for n in drug.names() if len(n) >= MIN_NAME_MATCH_CHARS]
    cls = drug.drug_class.lower()
    for term in terms:
        t = (term or "").strip().lower()
        if len(t) < MIN_NAME_MATCH_CHARS:
            continue
        if any(n in t or t in n for n in names) or (cls and t == cls):
            return True
    return False


def build_context_pack(scope_terms: Iterable[str], *, db_path: str | Path | None,
                       yaml_path: str | Path = DEFAULT_MA_CONTEXT_PATH) -> ContextPack:
    """run 당 1회: 큐레이션 전체를 DB 로 보강하고 스콥 관련성(in_scope)을 표시한다.

    보강은 모든 항목에 하되(약제당 접두 범위 쿼리 1회 + 집계 4회, 수 초 이내) 렌더가 관련 항목만 고른다 —
    정책 스콥에서 기사가 경쟁약을 언급할 때도 같은 팩으로 답할 수 있게."""
    terms = tuple(str(t) for t in scope_terms if str(t).strip())
    config = load_ma_context(yaml_path)
    enriched = _enrich_all(config.msd_assets + config.competitors, db_path)
    assets = [replace(d, in_scope=_term_hits(d, terms)) for d in enriched if d.role == "msd"]
    in_scope_assets = {a.brand_kr for a in assets if a.in_scope}
    competitors = [replace(d, in_scope=_term_hits(d, terms) or any(w in in_scope_assets for w in d.watch_for))
                   for d in enriched if d.role == "competitor"]
    return ContextPack(msd_assets=tuple(assets), competitors=tuple(competitors), scope_terms=terms,
                       db_path=str(db_path or ""))


# ---------------------------------------------------------------------------
# 렌더 (프롬프트 블록)
# ---------------------------------------------------------------------------
def _won(value: int) -> str:
    return f"{value:,}원"


def _price_fragment(d: DrugContext) -> str:
    if d.price_krw is None:
        return "약가 미등재"
    unit = f"/{d.price_unit}" if d.price_unit else ""
    date = f" ({d.price_date})" if d.price_date else ""
    return f"약가 {_won(d.price_krw)}{unit}{date}"


def _indication_fragment(d: DrugContext) -> str:
    if d.mfds_indication_count is not None and d.indication_count is not None:
        return f"허가 적응증 MFDS {d.mfds_indication_count} (master {d.indication_count})"
    if d.mfds_indication_count is not None:
        return f"허가 적응증 MFDS {d.mfds_indication_count}"
    if d.indication_count is not None:
        return f"허가 적응증 master {d.indication_count}"
    return ""


def _rsa_fragment(d: DrugContext) -> str:
    if d.is_rsa is True:
        return f"RSA 대상({d.rsa_type})" if d.rsa_type else "RSA 대상"
    if d.is_rsa is False:
        return "RSA 비대상"
    return ""


def drug_line(d: DrugContext) -> str:
    head_parts = [d.ingredient or d.inn]
    if d.role == "msd":
        head_parts.append(d.drug_class)
    else:
        head_parts.append(d.company)
    head = ", ".join(p for p in head_parts if p)
    frags = [_price_fragment(d), _indication_fragment(d)]
    if d.reimbursed_count is not None and d.reimbursement_rows:
        frags.append(f"급여 적응증 {d.reimbursed_count}/{d.reimbursement_rows}")
    frags.append(_rsa_fragment(d))
    if d.note:
        frags.append(d.note)
    return f"{d.brand_kr}({head}) " + " · ".join(f for f in frags if f)


def compact_label(d: DrugContext) -> str:
    head = ", ".join(p for p in (d.ingredient or d.inn, d.drug_class) if p)
    return f"{d.brand_kr}({head})" if head else d.brand_kr


def render_context_block(pack: ContextPack, *, article_text: str = "", max_chars: int = DEFAULT_BLOCK_MAX_CHARS,
                         include_all: bool = False) -> str:
    """작성·검증 프롬프트용 텍스트. MSD 포트폴리오 전부 + (스콥 관련 또는 기사 언급) 경쟁약을 계열별로.
    수백 토큰 상한(max_chars): 넘치면 뒤 줄부터 버린다."""
    if not pack.msd_assets and not pack.competitors:
        return ""
    detailed = [a for a in pack.msd_assets if include_all or a.in_scope or a.mentioned_in(article_text)]
    compact = [a for a in pack.msd_assets if a not in detailed]
    lines = ["[한국MSD 포트폴리오]"] + [f"- {drug_line(a)}" for a in detailed]
    if compact:
        # 스콥·기사와 무관한 자산은 이름·성분·계열만 (기전형 시사점에서 자산명을 고를 수 있게, 수치는 관련 자산만)
        lines.append("- 그 외: " + " · ".join(compact_label(a) for a in compact))
    chosen = [c for c in pack.competitors if include_all or c.in_scope or c.mentioned_in(article_text)]
    groups: dict[tuple[str, tuple[str, ...]], list[DrugContext]] = {}
    for c in chosen:
        groups.setdefault((c.drug_class, c.watch_for), []).append(c)
    for (cls, watch), members in groups.items():
        target = f" → {'·'.join(watch)}" if watch else ""
        lines.append(f"[경쟁 계열 {cls or '기타'}{target}]")
        lines += [f"- {drug_line(c)}" for c in members]
    text = "\n".join(lines)
    while len(text) > max_chars and len(lines) > 1:
        lines.pop()
        text = "\n".join(lines)
    return text[:max_chars]


def extract_number_tokens(text: str) -> set[str]:
    """숫자 토큰 정규화 집합: 쉼표 제거, 점 구분 날짜는 전체와 부분 모두, 앞자리 0 제거형도 포함."""
    out: set[str] = set()
    for tok in _NUMBER_TOKEN.findall(text or ""):
        plain = tok.replace(",", "")
        out.add(plain)
        if "." in plain:
            for part in plain.split("."):
                if part:
                    out.add(part)
                    out.add(part.lstrip("0") or "0")
    return out
