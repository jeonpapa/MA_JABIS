"""암질심 상정 후보 자동 발굴 — 주간 신선 신호 크롤(access_insight_fresh_signals)의 부속 단계.

2026-11-05 사용자 결정:
  - '암질심' 기사에 거명된, 보드에 없는 약제는 **일단 자동으로 후보 추가**한다 (origin='news_auto').
  - 상정되지 않거나 이후 논의가 없으면 **갱신하지 않는다** — 위원회 이벤트가 없는 자동 후보는 60일 stale 경고·
    신선도 과제에서 제외(reimb_pipeline._pipeline_drug_dict, freshness.check_pipeline_stale_cards).
  - **공단(NHIS) 연동으로는 후보를 만들지 않는다** — nhis_negotiation_import 의 미매칭 행은 지금처럼 audit 만.

흐름
----
  TieredArticle(신선 크롤 결과) → 암질심 표면어(_AMJILSIM_TOKENS) 포함 기사만
    → extractor(기사 배치) : 기사에 거명된 약제(브랜드) 후보 추출 (기본 LLM, 테스트는 주입)
    → 검증 ① 브랜드가 기사 제목+발췌에 **문자 그대로** 있어야 함 (LLM 환각 차단)
           ② 이미 보드에 있는 약제(alias index·brand core 일치)면 skip
           ③ MFDS 허가정보로 실명 확인 — ok 면 성분·업체를 MFDS 값으로 채움.
              miss/error(허가-평가 연계로 허가 전 상정 가능)면 **서로 다른 매체 2곳 이상**이 거명했을 때만 추가
    → amjilsim_drugs INSERT (origin='news_auto', origin_evidence=JSON) — 큐 이벤트는 만들지 않는다
      (위원회 이벤트는 헤르메스 committee_results.json 이 단일 소스 — 기사만으로 상정을 단정하지 않음)

멱등: 같은 brand core 가 이미 있으면 skip. 1회 최대 MAX_NEW_PER_RUN 건(폭주 방지).
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from datetime import date
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence, Union

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = BASE_DIR / "data" / "db" / "drug_prices.db"
PathLike = Union[str, Path]

ORIGIN_NEWS_AUTO = "news_auto"
AMJILSIM_TOKENS = ("암질심", "암질환심의위원회", "중증암질환", "중증(암)질환")
MAX_NEW_PER_RUN = 10
MIN_OUTLETS_UNVERIFIED = 2          # MFDS 로 확인 못 한 이름은 서로 다른 매체 2곳 이상이 거명해야 추가
LLM_BATCH = 15
DEFAULT_MODEL = os.environ.get("AMJILSIM_CANDIDATE_MODEL", "gpt-4.1-mini")

# 브랜드가 아닌 일반어 — LLM 이 가끔 뽑는 것들 (기사 표면 검증을 통과해도 후보가 아님)
_STOPWORDS = {
    "암질심", "약평위", "심평원", "건보공단", "공단", "복지부", "면역항암제", "항암제", "신약", "급여", "비급여",
    "표적항암제", "바이오시밀러", "제네릭", "치료제", "항체", "백신", "세포치료제", "car-t",
}
_FORM_SUFFIXES = ("프리필드시린지주", "프리필드펜주", "주사액", "캡슐", "정", "주", "액", "산")
# 함량 꼬리: '공백+숫자…' 또는 '숫자+단위…' 부터 끝까지 (이름 안의 숫자 '후보1맙' 은 보존)
_DOSE_RE = re.compile(r"(\s+\d.*|\d[\d.,·/]*\s*(mg|밀리그램|밀리그람|mcg|㎎|ml|g)\b.*)$", re.IGNORECASE)


def brand_core(name: str) -> str:
    """비교용 브랜드 핵심: 공백·함량 꼬리·제형 접미 제거, 소문자."""
    s = re.sub(r"\(.*?\)", "", name or "").strip()
    s = _DOSE_RE.sub("", s).strip()
    s = re.sub(r"\s+", "", s)
    for suf in _FORM_SUFFIXES:
        if s.endswith(suf) and len(s) - len(suf) >= 2:
            s = s[: -len(suf)]
            break
    return s.lower()


def has_amjilsim_token(text: str) -> bool:
    return any(t in (text or "") for t in AMJILSIM_TOKENS)


def ensure_origin_columns(conn: sqlite3.Connection) -> None:
    """amjilsim_drugs.origin / origin_evidence 멱등 보강 (없는 DB 는 스킵)."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(amjilsim_drugs)")}
    if not cols:
        return
    if "origin" not in cols:
        conn.execute("ALTER TABLE amjilsim_drugs ADD COLUMN origin TEXT")
    if "origin_evidence" not in cols:
        conn.execute("ALTER TABLE amjilsim_drugs ADD COLUMN origin_evidence TEXT")


# ── 추출기 (기본: LLM) ──────────────────────────────────────────────────────

_SYSTEM_PROMPT = """너는 한국 의약품 급여 뉴스에서 '중증(암)질환심의위원회(암질심)' 심의 대상 약제를 뽑는 추출기다.
각 기사(제목+발췌)에서 암질심 상정·심의·상정 예정·재상정·급여기준 설정/미설정 대상으로 거명된 **개별 의약품 브랜드명**만 뽑아라.
규칙:
- 기사에 실제로 쓰인 표기 그대로(brand_kr). 성분명만 있고 브랜드가 없으면 성분명을 brand_kr 로 쓰지 말고 건너뛴다.
- 회사명·질환명·제도명·일반명사(면역항암제, 신약 등)는 제외.
- 암질심과 무관하게 스치듯 언급된 약(해외 허가, 실적 등)은 제외.
- 모르면 빼라. 추측 금지.
JSON 만 출력: {"items":[{"index":<기사 인덱스>,"brand_kr":"...","ingredient":"성분(있으면)","manufacturer":"회사(있으면)","indication":"적응증(있으면)","quote":"근거가 된 원문 구절"}]}"""


def llm_extract(articles: Sequence, model: str = DEFAULT_MODEL) -> list[dict]:
    """기사 배치 → [{index, brand_kr, ingredient, manufacturer, indication, quote}]. 키·패키지 없거나 실패 시 []."""
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key or not articles:
        if not api_key:
            logger.warning("[candidate_discovery] OPENAI_API_KEY 없음 — 후보 추출 skip")
        return []
    try:
        from openai import OpenAI
    except ImportError:
        logger.error("[candidate_discovery] openai 패키지 미설치")
        return []
    client = OpenAI(api_key=api_key)
    out: list[dict] = []
    for start in range(0, len(articles), LLM_BATCH):
        chunk = articles[start:start + LLM_BATCH]
        payload = [{"index": start + i, "title": a.title, "snippet": (a.snippet or "")[:600],
                    "outlet": a.outlet} for i, a in enumerate(chunk)]
        try:
            resp = client.chat.completions.create(
                model=model, temperature=0,
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": _SYSTEM_PROMPT},
                          {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}])
            data = json.loads(resp.choices[0].message.content or "{}")
        except Exception as e:   # 한 배치 실패가 전체를 막지 않는다
            logger.warning("[candidate_discovery] LLM 배치 실패(%d~): %s", start, e)
            continue
        for it in data.get("items", []) if isinstance(data, dict) else []:
            if isinstance(it, dict):
                out.append(it)
    return out


def _default_permit_lookup(brand: str) -> dict:
    from agents.scrapers.kr_mfds_permit import lookup_permit
    return lookup_permit(brand)


def _permit_ok(res: dict | None) -> bool:
    from agents.scrapers.kr_mfds_permit import permit_status
    return permit_status(res) in ("ok", "stale")


# ── 본체 ────────────────────────────────────────────────────────────────────

def _existing_cores(conn: sqlite3.Connection) -> set[str]:
    cores: set[str] = set()
    for brand_kr, brand_en, inn in conn.execute("SELECT brand_kr, brand_en, ingredient_inn FROM amjilsim_drugs"):
        for part in re.split(r"\s*\+\s*", brand_kr or ""):
            if part:
                cores.add(brand_core(part))
        if brand_en:
            cores.add(brand_core(brand_en))
        if inn:
            cores.add((inn or "").strip().lower())
    cores.discard("")
    return cores


def discover_candidates(
    articles: Iterable,
    db_path: Optional[PathLike] = None,
    *,
    extractor: Optional[Callable[[Sequence], list[dict]]] = None,
    permit_lookup: Optional[Callable[[str], dict]] = None,
    dry_run: bool = False,
    today: Optional[date] = None,
    max_new: int = MAX_NEW_PER_RUN,
) -> dict:
    """암질심 기사 → 보드에 없는 약제를 자동 후보로 추가. 통계 dict 반환(added/skipped 사유 포함)."""
    today = today or date.today()
    extractor = extractor or llm_extract
    permit_lookup = permit_lookup or _default_permit_lookup
    pool = [a for a in articles if has_amjilsim_token(f"{a.title} {a.snippet or ''}")]
    stats: dict = {"amjilsim_articles": len(pool), "extracted": 0, "added": [], "skipped": [], "dry_run": dry_run}
    if not pool:
        return stats

    raw = extractor(pool)
    stats["extracted"] = len(raw)

    # 기사 표면 검증 + brand core 로 묶기
    groups: dict[str, dict] = {}
    for it in raw:
        try:
            idx = int(it.get("index"))
            art = pool[idx]
        except (TypeError, ValueError, IndexError):
            continue
        brand = (it.get("brand_kr") or "").strip()
        core = brand_core(brand)
        surface = f"{art.title} {art.snippet or ''}"
        if not brand or len(core) < 2 or core in _STOPWORDS:
            continue
        if brand not in surface and core not in surface.lower().replace(" ", ""):
            stats["skipped"].append({"brand_kr": brand, "reason": "기사 표면에 없음(추출 오류)"})
            continue
        g = groups.setdefault(core, {"brand_kr": brand, "ingredient": None, "manufacturer": None,
                                     "indication": None, "articles": []})
        for k in ("ingredient", "manufacturer", "indication"):
            if not g[k] and (it.get(k) or "").strip():
                g[k] = it[k].strip()
        if all(x["url"] != art.url for x in g["articles"]):
            g["articles"].append({"url": art.url, "outlet": art.outlet, "title": art.title,
                                  "published_at": art.published_at.strftime("%Y-%m-%d") if getattr(art, "published_at", None) else None,
                                  "quote": (it.get("quote") or "")[:200]})

    path = str(db_path or DEFAULT_DB_PATH)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        if not {r[1] for r in conn.execute("PRAGMA table_info(amjilsim_drugs)")}:
            stats["error"] = "amjilsim_drugs 없음"
            return stats
        if not dry_run:   # dry-run 은 스키마도 건드리지 않는다
            ensure_origin_columns(conn)
        has_onco = "is_oncology" in {r[1] for r in conn.execute("PRAGMA table_info(amjilsim_drugs)")}
        existing = _existing_cores(conn)
        from agents.access_insight.link import build_alias_index, resolve_drug
        index = build_alias_index(path)

        for core, g in sorted(groups.items(), key=lambda kv: -len(kv[1]["articles"])):
            brand = g["brand_kr"]
            if core in existing or resolve_drug(brand, index) is not None:
                stats["skipped"].append({"brand_kr": brand, "reason": "이미 보드에 있음"})
                continue
            if len(stats["added"]) >= max_new:
                stats["skipped"].append({"brand_kr": brand, "reason": f"1회 상한 {max_new}건 초과 — 다음 실행"})
                continue
            outlets = {a["outlet"] for a in g["articles"]}
            try:
                permit = permit_lookup(brand) or {}
            except Exception as e:
                permit = {"source": "error", "error": str(e)}
            verified = _permit_ok(permit)
            if not verified and len(outlets) < MIN_OUTLETS_UNVERIFIED:
                stats["skipped"].append({"brand_kr": brand, "reason": f"MFDS 미확인({permit.get('source')}) + 매체 {len(outlets)}곳"})
                continue
            atc = ((permit.get("atc_code") or "").strip().upper() if verified else "")
            if atc and not atc.startswith(("L01", "L02")):
                # 암질심은 항암제 심의 — MFDS ATC 가 항암(L01/L02)이 아니면 같은 기사에 함께 나온 약평위 약제 등
                stats["skipped"].append({"brand_kr": brand, "reason": f"비항암 ATC {atc} — 암질심 후보 아님"})
                continue
            ingredient = ((permit.get("main_ingr_eng") or "").strip().lower() if verified else None) or g["ingredient"]
            manufacturer = (permit.get("entp_name") if verified else None) or g["manufacturer"]
            mf = (manufacturer or "").upper()
            msd = 1 if ("MSD" in mf or "엠에스디" in mf) else 0
            first = g["articles"][0]
            evidence = {"first_seen": today.isoformat(), "mfds": "verified" if verified else f"unverified:{permit.get('source')}",
                        "mfds_item_name": permit.get("item_name") if verified else None,
                        "outlets": sorted(outlets), "articles": g["articles"][:5]}
            notes = (f"자동 후보(암질심 기사) — 첫 발견 {today.isoformat()} · {first['outlet']} '{first['title'][:60]}'"
                     f" · MFDS {'확인' if verified else '미확인'} · 상정/논의 확인 전까지 갱신 대상 아님")
            row = {"brand_kr": brand, "ingredient_inn": ingredient, "manufacturer": manufacturer,
                   "indication": g["indication"], "msd_flag": msd,
                   "tracking_priority": "msd_asset" if msd else "generic_new_drug",
                   "notes": notes, "origin": ORIGIN_NEWS_AUTO,
                   "origin_evidence": json.dumps(evidence, ensure_ascii=False)}
            if atc:
                row["atc"] = atc
            if has_onco:
                row["is_oncology"] = 1 if atc else None   # ATC 미확인이면 미상(NULL) — 기사 근거만으로 항암 단정 안 함
            if not dry_run:
                try:
                    cur = conn.execute(f"INSERT INTO amjilsim_drugs ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
                                       list(row.values()))
                    row["drug_id"] = cur.lastrowid
                except sqlite3.IntegrityError as e:
                    stats["skipped"].append({"brand_kr": brand, "reason": f"무결성 제약: {e}"})
                    continue
            existing.add(core)
            stats["added"].append({k: row.get(k) for k in ("drug_id", "brand_kr", "ingredient_inn", "manufacturer", "indication")}
                                  | {"mfds": evidence["mfds"], "outlets": len(outlets)})
        if dry_run:
            conn.rollback()
        else:
            conn.commit()
    finally:
        conn.close()
    if stats["added"] and not dry_run:
        from agents.access_insight.link import invalidate_index_cache
        invalidate_index_cache()
    return stats


def find_auto_candidate(conn: sqlite3.Connection, brand_kr: str) -> Optional[int]:
    """헤르메스 importer 용 — 같은 brand core 의 자동 후보 행(drug_id). origin 컬럼 없으면 None."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(amjilsim_drugs)")}
    if "origin" not in cols:
        return None
    core = brand_core(brand_kr)
    for drug_id, b in conn.execute("SELECT drug_id, brand_kr FROM amjilsim_drugs WHERE origin = ?", (ORIGIN_NEWS_AUTO,)):
        if brand_core(b) == core:
            return drug_id
    return None
