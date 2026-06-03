"""
식약처 의약품 특허정보 (data.go.kr — MdcinPatentInfoService2)
- operation: getMdcinPatentInfoList2
- 검색 키: item_name (한글 제품명) / ingr_name (한글 성분명) / ingr_eng_name / item_eng_name
- 응답: DOMESTIC_PATENT_STATUS, DOMESTIC_END_DATE, PAGE_GB_NM(제품특허/기타특허), PATENT_GB_CODE(물질/제법/...)

특허 상태 판정 로직:
- 제품특허(PAGE_GB_NM='제품특허') 중 '등록' 상태 + 만료일 미래 → 유효
- 제품특허 모두 소멸/만료 → 만료
- 제품특허 부재 + 기타특허만 → 'unknown' (기타특허는 제3자 권리 포함이라 LOE 판정 부적합)
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[2]
ENV_PATH = BASE_DIR / "config" / ".env"
DB_PATH = BASE_DIR / "data" / "db" / "drug_prices.db"

API_BASE = "https://apis.data.go.kr/1471000/MdcinPatentInfoService2/getMdcinPatentInfoList2"

# 캐시 TTL — 특허 상태/만료일은 거의 변동 없음. 90일 유지.
CACHE_TTL_DAYS = 90


def _load_env() -> None:
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def _get_service_key() -> str:
    _load_env()
    # 호환성: 두 변수명 모두 허용
    for var in ("MFDS_PATENT_API_KEY", "MFDS_PATENT_SERVICE_KEY"):
        key = os.environ.get(var, "").strip()
        if key:
            return key
    raise RuntimeError(
        "MFDS_PATENT_API_KEY 가 config/.env 에 없습니다. "
        "공공데이터포털에서 발급 후 환경변수에 추가하세요."
    )


# ── Raw API 호출 ────────────────────────────────────────────────────────────

def fetch_raw(
    item_name: str | None = None,
    ingr_name: str | None = None,
    ingr_eng_name: str | None = None,
    item_eng_name: str | None = None,
    item_seq: str | None = None,
    num_of_rows: int = 50,
    page_no: int = 1,
    timeout: int = 20,
) -> list[dict]:
    """
    data.go.kr API 직접 호출 (캐시 미경유). 모든 검색조건은 None 가능, 최소 1개 필요.
    Returns: API items 리스트 (없으면 빈 리스트).
    """
    if not any([item_name, ingr_name, ingr_eng_name, item_eng_name, item_seq]):
        raise ValueError("최소 하나의 검색조건이 필요합니다.")

    params = {
        "serviceKey": _get_service_key(),
        "type": "json",
        "numOfRows": str(num_of_rows),
        "pageNo": str(page_no),
    }
    if item_name:     params["item_name"]     = item_name
    if ingr_name:     params["ingr_name"]     = ingr_name
    if ingr_eng_name: params["ingr_eng_name"] = ingr_eng_name
    if item_eng_name: params["item_eng_name"] = item_eng_name
    if item_seq:      params["item_seq"]      = item_seq

    qs = urllib.parse.urlencode(params, doseq=True)
    url = f"{API_BASE}?{qs}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = r.read().decode("utf-8", errors="replace")
    except Exception as e:
        logger.warning("MFDS 특허 API 호출 실패: %s", e)
        return []

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        logger.warning("MFDS 응답 JSON 파싱 실패: %s", body[:200])
        return []

    header = data.get("header") or {}
    if header.get("resultCode") and header.get("resultCode") != "00":
        logger.info("MFDS 특허 응답 비정상: %s — %s", header.get("resultCode"), header.get("resultMsg"))
        return []
    body_obj = data.get("body") or {}
    items = body_obj.get("items") or []
    if isinstance(items, dict):  # 단일 결과는 dict 로 옴
        items = [items]
    return items


# ── 캐시 (sqlite) ───────────────────────────────────────────────────────────

def _ensure_table() -> None:
    """schema.py 의 mfds_patent_cache 가 이미 init 되어 있으나, 단독 사용 대비 idempotent 보장."""
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mfds_patent_cache (
                item_seq        TEXT,
                item_name       TEXT NOT NULL,
                ingredient      TEXT,
                page_gb_nm      TEXT,
                patent_gb_code  TEXT,
                patent_no       TEXT,
                patent_status   TEXT,
                patent_end_date TEXT,
                invn_name       TEXT,
                patentee        TEXT,
                fetched_at      TEXT,
                raw_json        TEXT,
                PRIMARY KEY (item_name, patent_no, page_gb_nm)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_patent_item_name ON mfds_patent_cache(item_name)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_patent_ingredient ON mfds_patent_cache(ingredient)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_patent_item_seq ON mfds_patent_cache(item_seq)")


def _cache_read(item_name: str) -> tuple[list[dict], Optional[datetime]]:
    """item_name 기준으로 캐시 조회. (rows, fetched_at) 반환."""
    _ensure_table()
    with sqlite3.connect(str(DB_PATH)) as conn:
        cur = conn.execute(
            "SELECT raw_json, fetched_at FROM mfds_patent_cache WHERE item_name = ?",
            (item_name,),
        )
        rows = cur.fetchall()
    if not rows:
        return [], None
    items = []
    fetched_at = None
    for raw, fa in rows:
        try:
            items.append(json.loads(raw))
        except Exception:
            continue
        if fetched_at is None or (fa and fa > fetched_at):
            fetched_at = fa
    fa_dt = None
    if fetched_at:
        try:
            fa_dt = datetime.fromisoformat(fetched_at)
        except ValueError:
            pass
    return items, fa_dt


def _cache_write(item_name: str, ingredient: str | None, items: list[dict]) -> None:
    _ensure_table()
    if not items:
        # 빈 결과도 캐시 — sentinel row 로 fetched_at 만 기록
        now = datetime.now().isoformat(timespec="seconds")
        with sqlite3.connect(str(DB_PATH)) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO mfds_patent_cache
                  (item_seq, item_name, ingredient, page_gb_nm, patent_gb_code,
                   patent_no, patent_status, patent_end_date, invn_name, patentee,
                   fetched_at, raw_json)
                VALUES (?, ?, ?, '', '', '__EMPTY__', '', '', '', '', ?, ?)
                """,
                (None, item_name, ingredient, now, json.dumps({}, ensure_ascii=False)),
            )
        return

    now = datetime.now().isoformat(timespec="seconds")
    with sqlite3.connect(str(DB_PATH)) as conn:
        # 같은 item_name 의 기존 row 제거 후 새로 적재 (특허번호 변동 대비)
        conn.execute("DELETE FROM mfds_patent_cache WHERE item_name = ?", (item_name,))
        for it in items:
            conn.execute(
                """
                INSERT OR REPLACE INTO mfds_patent_cache
                  (item_seq, item_name, ingredient, page_gb_nm, patent_gb_code,
                   patent_no, patent_status, patent_end_date, invn_name, patentee,
                   fetched_at, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    it.get("ITEM_SEQ"),
                    item_name,
                    ingredient or it.get("INGR_NAME"),
                    it.get("PAGE_GB_NM"),
                    it.get("PATENT_GB_CODE"),
                    it.get("DOMESTIC_PATENT_NO"),
                    it.get("DOMESTIC_PATENT_STATUS"),
                    it.get("DOMESTIC_END_DATE"),
                    it.get("DOMESTIC_INVN_NM"),
                    it.get("PATENTEE"),
                    now,
                    json.dumps(it, ensure_ascii=False),
                ),
            )


def _cache_fresh(fetched_at: Optional[datetime]) -> bool:
    if fetched_at is None:
        return False
    age_days = (datetime.now() - fetched_at).total_seconds() / 86400.0
    return age_days < CACHE_TTL_DAYS


# ── 특허 상태 판정 ──────────────────────────────────────────────────────────

# 후속 특허 modifier 키워드 — '물질' 과 함께 등장하면 secondary 로 분류
_SECONDARY_MODIFIERS = ("조성", "용도", "제법", "결합", "접합", "결정형", "제제")

# PATENTEE 가 학술·연구기관일 때 → 통상 합성공정/중간체 특허. LOE 결정 무관 (generic 진입 무영향).
# 원개발사(MSD, Genentech, Pfizer 등) 가 아닌 학술기관 보유는 약가 인하 예측에 영향 X.
_ACADEMIC_PATENTEE_KEYWORDS = (
    "대학교", "산학협력단", "연구소", "연구원", "학원", "재단법인", "재단",
    "University", "Institute", "Foundation", "Research", "Laboratories", "Laboratory",
)

# INVN_NAME 에 등장 시 후속 modality 로 분류 — 별개 약품이라 원약 LOE 와 무관
# 예: 허셉틴 ITEM_SEQ 에 등록된 ADC (Kadcyla=trastuzumab emtansine) 특허는 trastuzumab 자체 LOE 와 별개
_FOLLOW_ON_MODALITY_KEYWORDS = (
    # 영문
    "antibody drug conjugate", "antibody-drug conjugate", "antibody-drug",
    "bispecific", "trispecific",
    "biosimilar",
    # 한글
    "항체-약물 접합체", "항체-약물접합체", "항체-약물 결합체", "항체-약물결합체",
    "이중특이", "삼중특이",
)


def _is_academic_patentee(patentee: str | None) -> bool:
    if not patentee:
        return False
    s = patentee.lower()
    return any(kw.lower() in s for kw in _ACADEMIC_PATENTEE_KEYWORDS)


def _is_follow_on_modality(invn_name: str | None) -> bool:
    if not invn_name:
        return False
    s = invn_name.lower()
    return any(kw.lower() in s for kw in _FOLLOW_ON_MODALITY_KEYWORDS)


def _is_core_substance(gb_code: str) -> bool:
    """
    PATENT_GB_CODE 가 LOE 결정적 핵심 물질특허이면 True.
    규칙: '물질' 키워드 포함 + secondary modifier 부재.
    - '물질' / '물질(염)' / '물질(기타)' / '물질물질(염)' (DB 라벨 변형) → core
    - '물질(기타)조성' / '물질(기타)용도' → secondary (modifier 검출)
    """
    code = (gb_code or "").strip()
    if "물질" not in code:
        return False
    return not any(mod in code for mod in _SECONDARY_MODIFIERS)


def _is_secondary_substance_like(gb_code: str) -> bool:
    """물질 키워드 포함 + modifier 도 함께 있는 후속 특허."""
    code = (gb_code or "").strip()
    return ("물질" in code) and any(mod in code for mod in _SECONDARY_MODIFIERS)


def summarize(items: list[dict]) -> dict:
    """
    원본 row 리스트를 받아 특허 상태 요약 dict 반환.

    LOE 판정 규칙:
    - 물질특허(PATENT_GB_CODE='물질*') 가 모두 소멸/만료 → 만료 (제네릭/바이오시밀러 진입 가능)
    - 물질특허 중 등록 상태 + 만료일 미래 1건 이상 → 유효
    - 제품특허이지만 물질특허 부재 → 제형/조성 특허만으로는 LOE 판정 어려움 → 'unknown_no_substance'

    Returns:
      {
        "status": "유효" | "만료" | "unknown",
        "expiry_date": "YYYY-MM-DD" | None,   # 등록 상태 물질특허의 가장 늦은 만료일
        "active_substance_count": int,        # 등록 상태 물질특허 수
        "expired_substance_count": int,       # 소멸/만료 물질특허 수
        "product_patents": [...],             # 제품특허 전체 (참조용)
        "substance_patents": [...],           # 물질특허 부분만
        "other_patents_count": int,           # 기타특허 수 (제3자 권리)
        "judgment_basis": str,                # 어떤 근거로 판정했는지
      }
    """
    base = {
        "status": "unknown",
        "expiry_date": None,
        "active_substance_count": 0,
        "expired_substance_count": 0,
        "product_patents": [],
        "substance_patents": [],          # 핵심 물질특허 (LOE 결정용)
        "secondary_patents": [],          # 후속 특허 (ADC/조성/용도/제법 등)
        "other_patents_count": 0,
        "judgment_basis": "no_data",
    }
    if not items:
        return base

    today = datetime.now().date()
    all_rows: list[dict] = []
    core_substance_rows: list[dict] = []
    secondary_rows: list[dict] = []
    active_core_count = 0
    expired_core_count = 0
    latest_active_core_expiry: str | None = None

    for it in items:
        # PATENT_GB_CODE 만 보고 분류 — PAGE_GB_NM 은 제품특허/기타특허 라벨일 뿐,
        # 트라스투주맙처럼 제3자(제넨테크) 보유 활성성분 특허도 '기타특허' 로 등재되므로 필터링하지 않음.
        gb_code = (it.get("PATENT_GB_CODE") or "").strip()
        page_gb = (it.get("PAGE_GB_NM") or "").strip()
        status_kr = (it.get("DOMESTIC_PATENT_STATUS") or "").strip()
        end_date = (it.get("DOMESTIC_END_DATE") or "").strip() or None

        row = {
            "patent_no": it.get("DOMESTIC_PATENT_NO"),
            "patent_status": status_kr,
            "patent_end_date": end_date,
            "patent_gb_code": gb_code,
            "invn_name": it.get("DOMESTIC_INVN_NM"),
            "patentee": it.get("PATENTEE"),
            "page_gb_nm": page_gb,
        }
        all_rows.append(row)

        if _is_core_substance(gb_code):
            invn = it.get("DOMESTIC_INVN_NM")
            # PATENTEE 가 학술·연구기관 → 합성중간체/제조방법 특허
            if _is_academic_patentee(it.get("PATENTEE")):
                row["reclassified_reason"] = "academic_patentee"
                secondary_rows.append(row)
            # ADC/이중특이/biosimilar 등 후속 modality → 별개 약품, 원약 LOE 무관
            elif _is_follow_on_modality(invn):
                row["reclassified_reason"] = "follow_on_modality"
                secondary_rows.append(row)
            else:
                core_substance_rows.append(row)
        elif _is_secondary_substance_like(gb_code) or gb_code in ("조성", "결정형", "용도", "제법"):
            secondary_rows.append(row)
        # 그 외 분류 불명 코드는 secondary 에 포함 (참고용)
        elif gb_code:
            secondary_rows.append(row)

    # row 중복 제거 (patent_no 기준) — API 가 ITEM_SEQ 별로 같은 특허를 중복 반환할 수 있음
    def _dedup(rows: list[dict]) -> list[dict]:
        seen = set()
        out = []
        for r in rows:
            key = (r.get("patent_no"), r.get("patent_gb_code"))
            if key in seen:
                continue
            seen.add(key)
            out.append(r)
        return out

    core_substance_rows = _dedup(core_substance_rows)
    secondary_rows = _dedup(secondary_rows)

    # active/expired 카운트 재집계 (dedup 반영)
    active_core_count = 0
    expired_core_count = 0
    latest_active_core_expiry = None
    for r in core_substance_rows:
        end_date = r.get("patent_end_date")
        is_active = False
        if r.get("patent_status") == "등록":
            if end_date:
                try:
                    if datetime.strptime(end_date, "%Y-%m-%d").date() >= today:
                        is_active = True
                except ValueError:
                    pass
            else:
                is_active = True
        if is_active:
            active_core_count += 1
            if end_date and (latest_active_core_expiry is None or end_date > latest_active_core_expiry):
                latest_active_core_expiry = end_date
        else:
            expired_core_count += 1

    base["product_patents"] = all_rows
    base["substance_patents"] = core_substance_rows
    base["secondary_patents"] = secondary_rows
    base["other_patents_count"] = sum(1 for r in all_rows if r.get("page_gb_nm") != "제품특허")
    base["active_substance_count"] = active_core_count
    base["expired_substance_count"] = expired_core_count

    if not core_substance_rows:
        # core substance 부재 — PATENT_GB_CODE 가 빈 제품특허 fallback
        # (MFDS DB 일부 약품은 GB 코드 누락. 글리벡, 일부 노바티스/구약 케이스)
        empty_code_product_rows = [
            r for r in all_rows
            if r.get("page_gb_nm") == "제품특허" and not r.get("patent_gb_code")
        ]
        # dedup
        seen = set()
        empty_dedup = []
        for r in empty_code_product_rows:
            key = (r.get("patent_no"),)
            if key in seen:
                continue
            seen.add(key)
            empty_dedup.append(r)

        if empty_dedup:
            any_active = False
            latest_active = None
            for r in empty_dedup:
                if r.get("patent_status") == "등록":
                    end = r.get("patent_end_date")
                    if not end or _date_in_future(end, today):
                        any_active = True
                        if end and (latest_active is None or end > latest_active):
                            latest_active = end
            base["product_patents"] = empty_dedup  # surface for UI
            base["substance_patents"] = empty_dedup  # GB 코드 부재라 일단 core 로 간주 (보수적)
            base["active_substance_count"] = sum(
                1 for r in empty_dedup if r.get("patent_status") == "등록"
                and (not r.get("patent_end_date") or _date_in_future(r.get("patent_end_date"), today))
            )
            base["expired_substance_count"] = len(empty_dedup) - base["active_substance_count"]
            if any_active:
                base["status"] = "유효"
                base["expiry_date"] = latest_active
                base["judgment_basis"] = "empty_gb_code_active_product_patent"
            else:
                base["status"] = "만료"
                base["judgment_basis"] = "empty_gb_code_all_expired"
            return base
        # 진짜 데이터 없음
        if all_rows:
            base["status"] = "unknown"
            base["judgment_basis"] = "no_core_substance_patent"
        return base

    if active_core_count > 0:
        base["status"] = "유효"
        base["expiry_date"] = latest_active_core_expiry
        base["judgment_basis"] = "active_core_substance_patent"
    else:
        # 모든 핵심 물질특허 만료 → LOE 도래. secondary(ADC/조성 등)가 살아있어도 generic/biosimilar 진입 가능
        base["status"] = "만료"
        base["expiry_date"] = None
        base["judgment_basis"] = "all_core_substance_expired"
    return base


def _date_in_future(end_date: str, today) -> bool:
    try:
        return datetime.strptime(end_date, "%Y-%m-%d").date() >= today
    except (ValueError, TypeError):
        return False


# ── 한글 표기 변형 / prefix 축약 변형 ───────────────────────────────────────

def _name_variants(item_name: str) -> list[str]:
    """
    제품명 검색 변형 목록 — hit 률 높은 순. 첫 hit 즉시 break 하므로 순서가 latency 결정.
    실측 hit 률: '괄호 이전 prefix' > '한글 변형' > '괄호 포함 풀 표기' > '아주 짧은 prefix'.
    """
    import re
    seen: list[str] = []
    def _push(s: str):
        s = (s or "").strip()
        if s and s not in seen:
            seen.append(s)

    base = item_name.split("(")[0].strip()
    # 1순위: 괄호 이전 (가장 자주 hit) — '옵디보주100mg(니볼루맙)_(0.1g/10mL)' → '옵디보주100mg'
    _push(base)
    # 2순위: 한글 표기 변형 (람 ↔ 램)
    swaps = [("밀리그람", "밀리그램"), ("밀리그램", "밀리그람"), ("그람", "그램"), ("그램", "그람")]
    for src, dst in swaps:
        if src in base:
            _push(base.replace(src, dst))
    # 3순위: 원본 (괄호 포함 풀 표기)
    _push(item_name)
    # 4순위: 더 짧은 prefix (함량 단위 직전까지)
    cut_keywords = ["밀리그", "마이크로그", "그램", "그람", "유닛", "단위"]
    for kw in cut_keywords:
        idx = base.find(kw)
        if idx > 1:
            _push(base[:idx])
    # 5순위: 가장 짧은 한글 prefix (브랜드명 only)
    m = re.match(r"^([가-힣]+)", base)
    if m and len(m.group(1)) >= 2:
        _push(m.group(1))
    return seen


def _try_variants(item_name: str, ingredient: str | None) -> list[dict]:
    """다단계 검색: item_name 변형 → ingredient fallback. 첫 hit 의 결과 반환."""
    for variant in _name_variants(item_name):
        items = fetch_raw(item_name=variant, num_of_rows=100)
        if items:
            logger.info("MFDS 특허 hit: variant='%s' (원본='%s') → %d건", variant, item_name, len(items))
            return items
    # ingredient fallback
    if ingredient:
        ingr_clean = ingredient.split("(")[0].split(",")[0].strip()
        if ingr_clean:
            items = fetch_raw(ingr_name=ingr_clean, num_of_rows=200)
            if items:
                logger.info("MFDS 특허 ingredient fallback hit: '%s' → %d건", ingr_clean, len(items))
                return items
    return []


# ── Public API ──────────────────────────────────────────────────────────────

def lookup_patent(
    item_name: str,
    ingredient: str | None = None,
    *,
    use_cache: bool = True,
    refresh: bool = False,
) -> dict:
    """
    제품명 기반 특허 정보 조회 (캐시 우선).
    Args:
      item_name: 한글 제품명 (예: '키트루다주', '허셉틴주150밀리그람')
      ingredient: 성분명 (캐시 컬럼 채우기용)
      use_cache: True 면 캐시 hit 시 API 미호출
      refresh: True 면 캐시 무시하고 API 강제 재호출
    Returns:
      summarize() 결과 + {'item_name', 'fetched_at', 'source'}
    """
    item_name = (item_name or "").strip()
    if not item_name:
        return {"status": "unknown", "expiry_date": None, "item_name": item_name,
                "active_substance_count": 0, "expired_substance_count": 0,
                "product_patents": [], "substance_patents": [], "secondary_patents": [],
                "other_patents_count": 0, "judgment_basis": "skip_empty",
                "fetched_at": None, "source": "skip_empty"}

    if use_cache and not refresh:
        cached_items, fetched_at = _cache_read(item_name)
        if cached_items and _cache_fresh(fetched_at):
            # 빈 sentinel ('__EMPTY__') 만 있는 경우는 빈 응답 캐시 → status=unknown
            non_empty = [it for it in cached_items if it]
            summary = summarize(non_empty)
            summary.update({
                "item_name": item_name,
                "fetched_at": fetched_at.isoformat() if fetched_at else None,
                "source": "cache",
            })
            return summary

    # API 호출 — 한글 표기 변형 + prefix 축약 + ingredient fallback 다단계
    items = _try_variants(item_name, ingredient)

    _cache_write(item_name, ingredient, items)
    summary = summarize(items)
    summary.update({
        "item_name": item_name,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "source": "api",
    })
    return summary


# ── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

    ap = argparse.ArgumentParser(description="MFDS 의약품 특허정보 조회")
    ap.add_argument("item_name", help="한글 제품명")
    ap.add_argument("--ingredient", default=None, help="성분명 (fallback 용)")
    ap.add_argument("--refresh", action="store_true", help="캐시 무시 강제 재호출")
    ap.add_argument("--raw", action="store_true", help="원본 API 응답 출력")
    args = ap.parse_args()

    if args.raw:
        rows = fetch_raw(item_name=args.item_name)
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        result = lookup_patent(args.item_name, ingredient=args.ingredient, refresh=args.refresh)
        print(json.dumps(result, ensure_ascii=False, indent=2))
