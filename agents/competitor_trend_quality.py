"""경쟁사 동향 카드 품질 가드 — 결정론적(LLM 아님) 후처리.

사용자 리포트(2026-10 persona L1/S3/J3) 대응:
  1) 같은 사건 중복 카드 (빌로이 급여 등재 ×4, 파드셉 ×3 …)
     → `group_trends()` : 읽기 시점 묶음 (행 삭제 없음). 같은 브랜드 + 같은 이벤트 유형 +
       연속 간격 ≤10일(전체 span ≤21일) + (느슨한 유형이 아니면) 정규화 헤드라인 유사도.
  2) badge 오분류 (FDA/EMA 승인 = '신규 출시', 추진/신청 = '급여 등재', 약가 타결 = '급여 등재')
     → `guard_badge()` : 가격 > 해외 승인 > 급여 추진 > 임상 순서의 규칙.
  3) 회사 오귀속 (머크 웰리렉 FDA 뉴스가 BMS Korea 카드) / 요약 오염 (윈레브에어 카드에 림카토)
     → `guard_company()` : 브랜드→회사 맵으로 검증·교정, 타사 브랜드가 요약 주어로 등장하면
       low-confidence 플래그만 (내용을 지어내 고치지 않음).

모든 함수는 순수 함수(DB 접근 없음) — API 읽기 경로, 크롤 후처리, 재분류 스크립트, 테스트가 공유.
manual 카드(source_type='manual')는 admin 편집 보존 원칙상 badge/company 를 바꾸지 않는다 (플래그만).
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Iterable

# ─────────────────────────────────────────────────────────────────────────────
# Badge 정의
# ─────────────────────────────────────────────────────────────────────────────

BADGE_LAUNCH = "신규 출시"          # 국내 허가·출시 전용
BADGE_PRICE = "가격 변동"           # 약가 인하/인상, 약가(협상) 타결, 상한금액 변경
BADGE_CLINICAL = "임상 진행"
BADGE_LISTED = "급여 등재"          # 국내 급여 등재/확대가 확정·시행된 경우만
BADGE_PIPELINE = "파이프라인"
BADGE_STRATEGY = "전략 변화"
BADGE_FOREIGN = "해외 승인"         # FDA/EMA/CHMP/NICE 등 해외 허가·급여 결정 (국내 출시 아님)
BADGE_PENDING = "급여 추진"         # 급여 신청·추진·심의·협상 중·가능성 (미확정)

ALLOWED_BADGES: list[str] = [
    BADGE_LAUNCH, BADGE_PRICE, BADGE_CLINICAL, BADGE_LISTED,
    BADGE_PIPELINE, BADGE_STRATEGY, BADGE_FOREIGN, BADGE_PENDING,
]

BADGE_COLOR: dict[str, str] = {
    BADGE_LAUNCH:   "bg-emerald-500/20 text-emerald-400",
    BADGE_PRICE:    "bg-amber-500/20 text-amber-400",
    BADGE_CLINICAL: "bg-violet-500/20 text-violet-400",
    BADGE_LISTED:   "bg-emerald-500/20 text-emerald-400",
    BADGE_PIPELINE: "bg-blue-500/20 text-blue-400",
    BADGE_STRATEGY: "bg-rose-500/20 text-rose-400",
    BADGE_FOREIGN:  "bg-sky-500/20 text-sky-400",
    BADGE_PENDING:  "bg-teal-500/20 text-teal-400",
}

# ─────────────────────────────────────────────────────────────────────────────
# 규칙 패턴
# ─────────────────────────────────────────────────────────────────────────────

# 가격 — 헤드라인 기준 (detail 의 '약가협상을 거쳐 등재' 류 서술은 가격 이벤트가 아님)
_PRICE_RE = re.compile(
    r"(약가\s*(?:협상\s*)?타결|협상\s*(?:최종\s*)?타결|가격\s*협상\s*타결|"
    r"약가\s*(?:인하|인상|조정|재평가)|가격\s*(?:인하|인상)|"
    r"상한\s*(?:금액|액|가)\s*(?:인하|인상|조정|변경)|약가\s*협상\s*(?:개시|돌입))"
)

# 해외 규제기관/국가 — 이들 표지가 있고 국내 표지가 없으면 '국내 출시/등재' 가 아님
_FOREIGN_RE = re.compile(
    r"(FDA|EMA|CHMP|NICE|MHRA|PMDA|NMPA|PBAC|TGA|\bEC\b|\bEU\b|美|英|日|"
    r"미국|유럽|영국|일본|중국|캐나다|호주|독일|프랑스|후생노동성|유럽연합|유럽의약품청|NHS)"
)
_DOMESTIC_RE = re.compile(
    r"(식약처|식품의약품안전처|국내\s*(?:허가|출시|승인|도입|시판|급여|발매)|국내에서|국내\s*최초|"
    r"건강보험|건보|심평원|건강보험심사평가원|약평위|약제급여평가위원회|암질심|복지부|보건복지부|국민건강보험공단)"
)
_APPROVAL_RE = re.compile(r"(승인|허가(?!\s*신청)|권고|급여\s*(?:등재|결정|승인|권고)|출시)")
_APPLICATION_ONLY_RE = re.compile(r"(허가\s*신청|승인\s*신청|심사\s*(?:연장|착수|개시)|신청서\s*제출)")

# 급여 진행 상태
_PENDING_HEAD_RE = re.compile(
    r"(추진|신청|도전|재도전|가능성|예정|예고|촉구|지연|연기|유력|전망|심의|논의|"
    r"협상(?!\s*타결)|앞두|검토|노크|청신호|기대|주목|관문|문턱|임박|과제|초읽기|"
    r"통과|상정|적정성\s*인정|여부|연장|장기화|단계|시급|멈춰|눈앞|대기)"
)
# 헤드라인 확정 표지 — pending 표지보다 우선 (예: '급여 등재 완료', '급여 적용 시작')
_DONE_HEAD_RE = re.compile(
    r"(등재\s*완료|완료|확정|적용\s*시작|성공|시행|부터|등재됐|등재되|적용된|급여권\s*진입)"
)
_PENDING_DETAIL_RE = re.compile(
    r"(추진\s*중|추진하|추진할|신청했|신청하|신청한|신청을|도전하|도전한|재도전|촉구|지연되|지연돼|연기되|연기됐|"
    r"예정이다|예정이며|제출될|예고|유력|가능성이|가능성을|협상\s*중|협상이\s*진행|협상에\s*돌입|"
    r"검토\s*중|심의\s*중|앞두고|진입을\s*위한)"
)
_DONE_RE = re.compile(
    r"(등재되었|등재됐|등재된다|등재되면서|등재되며|등재\s*완료|등재\s*확정|급여\s*확정|확정되|확정됐|"
    r"적용된다|적용되며|적용되면서|적용됐|적용되었|적용받|적용을\s*받|적용\s*시작|적용되기\s*시작|"
    r"부터\s*(?:건강보험\s*)?급여|급여가\s*적용|급여\s*적용(?!\s*(?:유력|가능|촉구|추진|기대|전망|신청))|"
    r"인정받|급여권에\s*진입|결정(?!\s*신청)|고시|포함된다|포함되었|포함됐|시행|성공하|성공했|에\s*성공)"
)

# 임상 헤드라인 (급여 표지 없음)
_CLINICAL_HEAD_RE = re.compile(r"(연구|임상|생존|결과|입증|PFS|OS|DFS|무진행|3상|2상|1상|데이터)")
_REIMB_HEAD_RE = re.compile(r"(급여|등재|약가|보험|약평위|암질심|신속등재|협상)")


def _text(*parts: Any) -> str:
    return " ".join(str(p or "") for p in parts)


def guard_badge(headline: str, detail: str, badge: str) -> tuple[str, str | None]:
    """LLM badge → 결정론 가드 적용. 반환 (badge, reason|None). reason None = 변경 없음.

    우선순위: 가격 > 해외 승인 > 급여 추진 > 임상(급여 카드의 임상 헤드라인).
    """
    headline = headline or ""
    detail = detail or ""
    full = _text(headline, detail)

    # 1) 가격 — 약가/협상 타결·인하·인상 (헤드라인)
    if badge != BADGE_PRICE and _PRICE_RE.search(headline):
        return BADGE_PRICE, f"price:{_PRICE_RE.search(headline).group(0)}"

    # 2) 해외 승인 — 국내 표지 없이 해외 규제기관/국가 표지 + 승인 계열 단어
    if badge not in (BADGE_FOREIGN, BADGE_PRICE) and not _DOMESTIC_RE.search(full):
        f_head = _FOREIGN_RE.search(headline)
        f_any = f_head or _FOREIGN_RE.search(detail)
        if badge in (BADGE_LAUNCH, BADGE_LISTED):
            # 국내 출시/등재 라벨은 해외 표지 + 승인 단어(어디든) 만으로 교정
            if f_any and _APPROVAL_RE.search(full) and not _APPLICATION_ONLY_RE.search(headline):
                return BADGE_FOREIGN, f"foreign:{f_any.group(0)}"
        elif f_head and _APPROVAL_RE.search(headline) and not _APPLICATION_ONLY_RE.search(headline):
            # 그 외 라벨은 헤드라인에 해외 표지 + 승인이 함께 있을 때만
            return BADGE_FOREIGN, f"foreign:{f_head.group(0)}"

    # 3) 급여 추진 — '급여 등재' 라벨인데 확정 표지 없이 추진/신청/가능성
    if badge == BADGE_LISTED:
        head_done = _DONE_HEAD_RE.search(headline)
        head_pending = None if head_done else _PENDING_HEAD_RE.search(headline)
        if head_pending:
            return BADGE_PENDING, f"pending:{head_pending.group(0)}"
        if not head_done and not _DONE_RE.search(headline) and not _DONE_RE.search(detail):
            det_pending = _PENDING_DETAIL_RE.search(detail)
            if det_pending:
                return BADGE_PENDING, f"pending:{det_pending.group(0)}"
        # 4) 국내 허가/출시 — 급여 카드인데 헤드라인이 허가·출시이고 급여 표지 없음 (해외 표지는 2)에서 처리됨)
        if re.search(r"(허가|출시)", headline) and not _REIMB_HEAD_RE.search(headline):
            return BADGE_LAUNCH, "launch:domestic_approval"
        # 5) 임상 — 급여 카드인데 헤드라인이 연구/임상 결과이고 급여 표지 없음
        if _CLINICAL_HEAD_RE.search(headline) and not _REIMB_HEAD_RE.search(headline):
            return BADGE_CLINICAL, f"clinical:{_CLINICAL_HEAD_RE.search(headline).group(0)}"

    return badge, None


# ─────────────────────────────────────────────────────────────────────────────
# 브랜드 → 회사
# ─────────────────────────────────────────────────────────────────────────────

MSD_COMPANY = "MSD Korea"
MSD_META = {"company": MSD_COMPANY, "logo": "MSD", "color": "#00857C"}

# 한국MSD 자사 브랜드 (agents/daily_mailing/scope_validation.MSD_BRANDS 와 동일 계열)
MSD_BRANDS: tuple[str, ...] = (
    "키트루다", "웰리렉", "윈레브에어", "가다실", "브리디온", "에멘드", "박스뉴반스",
    "자누비아", "자누메트", "저박사", "프리베나", "카프박스", "녹사필", "레비시오",
)

# 교정에는 쓰지 않고 '요약 오염' 플래그에만 쓰는 타사 브랜드 (추적 대상 아님)
OTHER_BRAND_COMPANY: dict[str, str] = {
    "림카토": "큐로셀",
    "렉라자": "유한양행",
    "리브리반트": "Janssen Korea",
    "다잘렉스": "Janssen Korea",
    "여보이": "BMS Korea",
    "레블리미드": "BMS Korea",
    "카보메틱스": "Ipsen Korea",
    "렌비마": "Eisai Korea",
    "젭젤카": "Jazz",
    "임델트라": "Amgen Korea",
    "허셉틴": "Roche Korea",
    "테쎈트릭": "Roche Korea",
    "린파자": "AstraZeneca Korea",
    "오르파티스": "HUTCHMED/AZ",
    "위고비": "Novo Nordisk Korea",
    "마운자로": "Lilly Korea",
    "지헤라": "Jazz",
}

# 같은 회사 별칭 (MNC 칩 중복 'MSD' vs 'MSD Korea')
COMPANY_ALIASES: dict[str, str] = {
    "MSD": MSD_COMPANY,
    "한국MSD": MSD_COMPANY,
    "한국 MSD": MSD_COMPANY,
    "Merck Sharp & Dohme": MSD_COMPANY,
    "MSD Korea": MSD_COMPANY,
}


def normalize_company(company: str | None) -> str:
    c = (company or "").strip()
    return COMPANY_ALIASES.get(c, c)


def build_brand_map(tracked: Iterable[dict] | None = None) -> dict[str, dict]:
    """브랜드 → {company, logo, color, tracked:bool, repairable:bool}.

    tracked: competitor_brand 행(dict: query/company/logo/color). None 이면 상수 폴백.
    repairable = 회사 교정 대상으로 써도 되는 브랜드 (추적 브랜드 + MSD 자사).
    """
    if tracked is None:
        try:
            from agents.competitor_news_agent import COMPETITOR_BRANDS
            tracked = COMPETITOR_BRANDS
        except Exception:
            tracked = []
    out: dict[str, dict] = {}
    for b, comp in OTHER_BRAND_COMPANY.items():
        out[b] = {"company": comp, "logo": None, "color": None, "tracked": False, "repairable": False}
    for b in MSD_BRANDS:
        out[b] = {**MSD_META, "tracked": False, "repairable": True}
    for t in tracked or []:
        q = (t.get("query") or "").strip()
        if not q:
            continue
        comp = normalize_company(t.get("company"))
        out[q] = {"company": comp, "logo": t.get("logo"), "color": t.get("color"),
                  "tracked": True, "repairable": True}
    # 표기 변형
    if "티쎈트릭" in out and "테쎈트릭" not in out:
        out["테쎈트릭"] = dict(out["티쎈트릭"])
    if "테쎈트릭" in out and out["테쎈트릭"].get("company") == "Roche Korea" and "티쎈트릭" in out:
        out["테쎈트릭"] = dict(out["티쎈트릭"])
    return out


def find_brands(text: str, brand_map: dict[str, dict]) -> list[str]:
    """text 에 등장하는 알려진 브랜드 — 첫 등장 위치 순."""
    text = text or ""
    hits = []
    for b in brand_map:
        i = text.find(b)
        if i >= 0:
            hits.append((i, -len(b), b))
    hits.sort()
    seen: list[str] = []
    for _, _, b in hits:
        if b not in seen:
            seen.append(b)
    return seen


def _company_of(brand: str, brand_map: dict[str, dict]) -> str:
    return normalize_company((brand_map.get(brand) or {}).get("company"))


def subject_brand(headline: str, detail: str, company: str,
                  brand_map: dict[str, dict]) -> str | None:
    """카드의 주어 브랜드.
    헤드라인: 카드 회사 브랜드 > 비-MSD 브랜드 > 첫 브랜드. 없으면 detail 의 첫 브랜드."""
    company = normalize_company(company)
    head = find_brands(headline, brand_map)
    if head:
        own = [b for b in head if _company_of(b, brand_map) == company]
        if own:
            return own[0]
        non_msd = [b for b in head if _company_of(b, brand_map) != MSD_COMPANY]
        return (non_msd or head)[0]
    det = find_brands(detail, brand_map)
    return det[0] if det else None


_COMBO_NEAR_RE = re.compile(r"(병용|\+|＋|·|ㆍ|와\s|과\s|대비|비교|함께|및)")
_SUBJECT_JOSA_RE = r"(?:주)?['\"’”]?(?:의|가|이|은|는|를|을)(?=\s|$|[,.])"


def foreign_brand_mentions(headline: str, detail: str, company: str,
                           brand_map: dict[str, dict]) -> list[str]:
    """요약(detail)에 타사 브랜드가 '주어/소유격'으로 등장하는데 헤드라인엔 없는 경우.
    (예: 윈레브에어 카드 detail '림카토의 약가 협상에 돌입') — 병용/비교 문맥은 제외."""
    company = normalize_company(company)
    head = set(find_brands(headline, brand_map))
    out: list[str] = []
    for b in find_brands(detail, brand_map):
        if b in head or _company_of(b, brand_map) == company:
            continue
        for m in re.finditer(re.escape(b) + _SUBJECT_JOSA_RE, detail):
            lo, hi = max(0, m.start() - 8), min(len(detail), m.end() + 8)
            if _COMBO_NEAR_RE.search(detail[lo:m.start()]) or _COMBO_NEAR_RE.search(detail[m.end():hi]):
                continue
            out.append(b)
            break
    return out


def guard_company(headline: str, detail: str, company: str,
                  brand_map: dict[str, dict]) -> dict[str, Any]:
    """회사 귀속 검증. 반환:
      {"company": 교정(또는 원래) 회사, "meta": 교정 시 {logo,color}|None,
       "brand": 주어 브랜드|None, "reason": 교정 사유|None, "flags": [..]}
    교정은 주어 브랜드가 repairable(추적/MSD)일 때만. 그 외 불일치는 플래그만.
    """
    orig = (company or "").strip()
    comp = normalize_company(orig)
    flags: list[str] = []
    reason = None
    meta = None
    if comp != orig:
        reason = f"alias:{orig}->{comp}"
    brand = subject_brand(headline, detail, comp, brand_map)
    head_brands = find_brands(headline, brand_map)
    own_in_head = any(_company_of(b, brand_map) == comp for b in head_brands)
    if brand and not own_in_head:
        bcomp = _company_of(brand, brand_map)
        info = brand_map.get(brand) or {}
        if bcomp and bcomp != comp:
            if info.get("repairable"):
                reason = f"brand:{brand}->{bcomp}"
                comp = bcomp
                meta = {"logo": info.get("logo"), "color": info.get("color")}
            else:
                flags.append(f"company_unverified:{brand}({bcomp})")
    others = foreign_brand_mentions(headline, detail, comp, brand_map)
    if others:
        flags.append("summary_mentions_other_brand:" + ",".join(others))
    return {"company": comp, "meta": meta, "brand": brand, "reason": reason, "flags": flags}


# ─────────────────────────────────────────────────────────────────────────────
# 카드 단위 가드 (크롤 후처리 · 재분류 스크립트 · API 공용)
# ─────────────────────────────────────────────────────────────────────────────

def guard_card(card: dict[str, Any], brand_map: dict[str, dict]) -> dict[str, Any]:
    """card(dict: headline/detail/badge/company[/source_type]) → 제안값.
    반환 {"badge","badge_reason","company","company_reason","meta","brand","flags","low_confidence"}.
    manual 카드는 badge/company 를 바꾸지 않는다 (플래그·brand 만 계산)."""
    headline = card.get("headline") or ""
    detail = card.get("detail") or ""
    badge = card.get("badge") or ""
    manual = (card.get("source_type") or "") == "manual"
    new_badge, b_reason = guard_badge(headline, detail, badge)
    cg = guard_company(headline, detail, card.get("company") or "", brand_map)
    if manual:
        new_badge, b_reason = badge, None
        cg = {**cg, "company": card.get("company") or "", "reason": None, "meta": None}
    flags = list(cg["flags"])
    return {
        "badge": new_badge,
        "badge_reason": b_reason,
        "company": cg["company"],
        "company_reason": cg["reason"],
        "meta": cg["meta"],
        "brand": cg["brand"],
        "flags": flags,
        "low_confidence": any(f.startswith(("summary_mentions_other_brand", "company_unverified"))
                              for f in flags),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 중복 묶음 (읽기 시점)
# ─────────────────────────────────────────────────────────────────────────────

GROUP_GAP_DAYS = 10      # 연속 멤버 간 최대 간격
GROUP_SPAN_DAYS = 21     # 묶음 전체 최대 span
SIMILARITY_MIN = 0.34    # 엄격 유형(임상·전략 등)의 헤드라인 bigram Jaccard 하한
# 브랜드 + 유형 + 기간만으로 같은 사건으로 보는 유형 (규제/급여 이벤트는 브랜드당 동시 다발이 드묾)
LOOSE_GROUP_BADGES = frozenset({BADGE_LISTED, BADGE_PENDING, BADGE_PRICE, BADGE_FOREIGN})

_NORM_DROP_RE = re.compile(r"[\s\W_]+", re.UNICODE)
_HEAD_STOP = ("건강보험", "치료제", "치료", "국내", "발표", "소식")


def normalize_headline(headline: str, brand_map: dict[str, dict] | None = None) -> str:
    h = headline or ""
    for b in sorted((brand_map or {}).keys(), key=len, reverse=True):
        h = h.replace(b, " ")
    for s in _HEAD_STOP:
        h = h.replace(s, " ")
    return _NORM_DROP_RE.sub("", h).lower()


def _bigrams(s: str) -> set[str]:
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else ({s} if s else set())


def headline_similarity(a: str, b: str, brand_map: dict[str, dict] | None = None) -> float:
    A, B = _bigrams(normalize_headline(a, brand_map)), _bigrams(normalize_headline(b, brand_map))
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


def _to_date(s: Any) -> date | None:
    try:
        return datetime.strptime(str(s or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def group_trends(items: list[dict[str, Any]], brand_map: dict[str, dict]) -> list[dict[str, Any]]:
    """API 아이템(dict) 목록 → 같은 리스트에 묶음 필드 추가 (순서·행 보존).

    각 item 에 추가:
      group_id   : 대표 카드 id (단독이면 자기 id)
      group_size : 묶음 카드 수 (단독 1)
      related    : 대표 카드에만 — 나머지 멤버 [{id,headline,date,source,url,company,badge}]
    대표 = manual 우선 → 최신 date → 최저 source_tier → 큰 id.
    item 은 `quality.brand` 또는 `_brand` 키로 주어 브랜드를 전달한다 (없으면 단독).
    """
    def _brand(it):
        q = it.get("quality") or {}
        return q.get("brand") or it.get("_brand")

    buckets: dict[tuple, list[dict]] = {}
    for it in items:
        b = _brand(it)
        d = _to_date(it.get("date"))
        if not b or d is None:
            continue
        buckets.setdefault((b, it.get("badge") or ""), []).append(it)

    groups: list[list[dict]] = []
    for (brand, badge), members in buckets.items():
        members = sorted(members, key=lambda x: (str(x.get("date") or ""), x.get("id") or 0), reverse=True)
        local: list[list[dict]] = []
        for it in members:
            d = _to_date(it.get("date"))
            placed = False
            for g in local:
                newest = _to_date(g[0].get("date"))
                oldest = _to_date(g[-1].get("date"))
                if (oldest - d).days > GROUP_GAP_DAYS or (newest - d).days > GROUP_SPAN_DAYS:
                    continue
                if badge not in LOOSE_GROUP_BADGES:
                    sim = max(headline_similarity(it.get("headline") or "", m.get("headline") or "", brand_map)
                              for m in g)
                    if sim < SIMILARITY_MIN:
                        continue
                g.append(it)
                placed = True
                break
            if not placed:
                local.append([it])
        groups.extend(local)

    for it in items:
        it["group_id"] = it.get("id")
        it["group_size"] = 1
        it["related"] = []
    for g in groups:
        if len(g) < 2:
            continue
        primary = sorted(
            g,
            key=lambda x: (
                1 if (x.get("source_type") == "manual") else 0,
                str(x.get("date") or ""),
                -(x.get("source_tier") or 3),
                x.get("id") or 0,
            ),
            reverse=True,
        )[0]
        for m in g:
            m["group_id"] = primary.get("id")
            m["group_size"] = len(g)
        primary["related"] = [
            {k: m.get(k) for k in ("id", "headline", "date", "source", "url", "company", "badge")}
            for m in sorted(g, key=lambda x: str(x.get("date") or ""), reverse=True)
            if m is not primary
        ]
    return items


def annotate_trends(items: list[dict[str, Any]], brand_map: dict[str, dict] | None = None,
                    apply_overrides: bool = True) -> list[dict[str, Any]]:
    """GET /api/competitor-trends 응답 아이템에 품질 가드 + 묶음 적용 (읽기 시점, DB 무변경).

    apply_overrides=True 면 auto 카드의 badge/company(+logo/color) 를 가드 값으로 표시하고
    원래 값은 quality.badge_original / quality.company_original 에 보존.
    """
    if brand_map is None:
        brand_map = build_brand_map(_tracked_brands())
    for it in items:
        g = guard_card(it, brand_map)
        q: dict[str, Any] = {
            "brand": g["brand"],
            "flags": g["flags"],
            "low_confidence": g["low_confidence"],
        }
        if apply_overrides and g["badge"] != it.get("badge"):
            q["badge_original"] = it.get("badge")
            q["badge_reason"] = g["badge_reason"]
            it["badge"] = g["badge"]
            it["badgeColor"] = BADGE_COLOR.get(g["badge"], it.get("badgeColor"))
        if apply_overrides and g["company"] and g["company"] != it.get("company"):
            q["company_original"] = it.get("company")
            q["company_reason"] = g["company_reason"]
            it["company"] = g["company"]
            meta = g.get("meta") or {}
            if meta.get("logo"):
                it["logo"] = meta["logo"]
            if meta.get("color"):
                it["color"] = meta["color"]
        it["quality"] = q
    return group_trends(items, brand_map)


def _tracked_brands() -> list[dict] | None:
    try:
        from agents.editable_factors import get_competitor_brands
        return get_competitor_brands()
    except Exception:
        return None
