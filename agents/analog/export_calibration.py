"""Export analog_reports → 우리 피처 정규화 (yakpyeong / amjilsim CalibrationCase JSON).

정규화 규칙(순서형만·decision_reason 정규식 첫 매치·부정어 우선·실패→conf low).
예측 피처에는 approval_driver/review_result 를 절대 쓰지 않는다 — 순서형 파생은 TEXT ONLY.
review_result 는 actual 라벨과 RuleBasis outcome-gate 에만 사용한다(타깃 누수 차단).

주의: PDF 추출 원문에는 한글 단어 내부에까지 개행/공백이 삽입되어 있어(예: "비용효\n과적"),
정규식은 공백을 모두 제거한 compact 텍스트에 대해 매칭한다. sourceExcerpt 역시 compact 기준.

실행:
    python3 export_calibration.py            # 기본 경로에 두 JSON 생성
    python3 export_calibration.py --db ... --yak-out ... --amj-out ...
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from pathlib import Path

# ── 기본 경로 ──
BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DB = BASE_DIR / "data" / "db" / "drug_prices.db"
REPO_DATA = Path(
    "/Users/kimjeong-ae/NegotiationAI/FrontEnd/project-Negotiation Solution"
    "/src/lib/negotiation/data"
)
DEFAULT_YAK_OUT = REPO_DATA / "yakpyeongCalibration.json"
DEFAULT_AMJ_OUT = REPO_DATA / "amjilsimCalibration.json"

RULESET_VERSION = "a1-v1"

# 컬럼 화이트리스트(embedding/body_text blob 제외)
COLUMNS = [
    "id",
    "file_name", "brand_name", "generic_name", "disease_category", "review_result",
    "approval_driver", "has_rsa", "pe_waiver", "rsa_type_hint", "policy_tags",
    "decision_reason", "session_date", "pass_session_date", "first_session_date",
    "os_months", "pfs_months", "orr_pct", "key_hr", "primary_endpoint",
    "comparator_drugs", "requeue_count", "amjilsim_history", "biomarker",
    "line_of_therapy", "consulted_societies", "session_year", "manufacturer",
]

# ─────────────────────────── 텍스트 정규화 ───────────────────────────

def compact(text: str | None) -> str:
    """모든 공백/개행 제거 (PDF 단어 내부 개행 대응)."""
    if not text:
        return ""
    return re.sub(r"\s+", "", text)


def excerpt_from(compact_text: str, m: re.Match, pad: int = 40) -> str:
    start = max(0, m.start() - pad)
    end = min(len(compact_text), m.end() + pad)
    return compact_text[start:end]


def first_nonneg_match(compact_text: str, pattern: re.Pattern,
                       before_neg: tuple[str, ...] = (),
                       after_neg: tuple[str, ...] = (),
                       before_win: int = 8, after_win: int = 14):
    """정규식 첫 매치 반환하되, 부정어(앞/뒤 창)로 무효화되는 매치는 건너뛴다(부정어 우선)."""
    for m in pattern.finditer(compact_text):
        if before_neg:
            before = compact_text[max(0, m.start() - before_win):m.start()]
            if any(tok in before for tok in before_neg):
                continue
        if after_neg:
            after = compact_text[m.end():m.end() + after_win]
            if any(tok in after for tok in after_neg):
                continue
        return m
    return None


# ─────────────────────────── 컴파일된 패턴 ───────────────────────────
# (모두 compact 텍스트 기준 — \s 없음)

# price_vs_eval — 전부 텍스트 전용(라벨 미사용)
RE_ACCEPT = re.compile(r"(이하수용|수용하(였으므로|여).{0,40}?적정성|수용할경우.{0,40}?적정성)")
RE_HIGHCOST = re.compile(
    r"(소요비용이고가|대비고가|(가중평균가|A7조정평균가).{0,12}보다.{0,6}고가|고가(로|이므로|임))"
)
# 저렴/비용효과적/생략기준금액 이하 (실데이터 보정: "비용효과성이 있"→0건 → 실제 표현으로 교체)
RE_COST_EFFECTIVE = re.compile(
    r"(저렴하여비용.?효과적|약가협상생략기준금액이하|비용.?효과적이므로|비용효과성이인정|비용효과성이있)"
)

# icer — 면제는 GRANTED 문구 AND NOT DENIED 문구 (명시적 생략대상 부인 행의 거짓 면제 차단)
# 협상생략기준금액은 '적용/수용' 형태만 GRANT — "이하로…생략함"(적용)·"이하를 수용하였으므로/수용하여"(수용).
# 조건부 제안("이하를 수용할 경우 …생략함", 부결문 상투구)·각주 정의부는 GRANT 아님(거짓 면제 차단).
RE_WAIVER = re.compile(
    r"(PEWaiver|경제성평가.{0,10}생략.{0,14}(가능|대상)"
    r"|약가협상생략기준금액.{0,40}?이하(로|이므로|를수용하(였으므로|여)))",
    re.IGNORECASE,
)
# DENIED: 실제 문구는 "경제성평가자료제출 생략가능 대상약제에 해당하지 아니하며/아니하고".
# 부인 토큰(해당되지아니 등)은 생략 문맥으로 스코프 — 무관 문맥(급여기준 대상자 조항 등) 오탐 차단.
RE_WAIVER_DENY = re.compile(
    r"(생략.{0,25}(해당하지아니|해당되지아니|대상에해당하지)|생략가능대상.{0,12}아니하며)"
)
RE_ICER_LOW = re.compile(r"(저렴하여비용.?효과적|비용.?효과적이므로|비용효과성이인정|비용효과성이있)")
RE_ICER_HIGH = re.compile(
    r"(비용.?효과성이불분명|비용.?효과적이라고보기는?어려|비용.?효과적이지않"
    r"|경제성이불분명|비용.?효과성이입증되지않)"
)

# comparator
RE_CMP_EQUAL = re.compile(r"(효과가?유사|동등|비열등)")
RE_CMP_SUP = re.compile(r"(개선|우월|우위|우수)")
# 임상적 유용성 개선 인정(강한 우위 신호) — '치료적위치가동등한제품이없고' 등의 동등 오탐보다 먼저 판정.
RE_CMP_IMPROVE = re.compile(
    r"(임상적유용성.{0,6}개선.{0,6}인정|유용성이개선"
    r"|(효과|반응률|무진행생존|생존).{0,10}개선.{0,8}인정|개선.{0,10}인정)"
)
_IMP_AFTER_NEG = ("못", "않", "없", "어렵", "아니")
# 어절경계 오탐 차단(lookbehind): '비열등'(=동등)·'발열/미열/고열/오열/한열 + 등'.
# 노이즈 문자는 항상 '열' 바로 앞에 붙으므로 window 가 아닌 직전 1자 lookbehind 로 판정.
RE_CMP_INF = re.compile(r"(?<![비발미고오한])(열등|열위)")
# 강한 열위 신호(임상적 열등 진술) — '동등'보다 먼저 판정해 동등어(유사) 동반 시에도 열위 확보.
RE_CMP_INF_STRONG = re.compile(
    r"(유용성이열등|효과가열등|효과면에서열등"
    r"|(?<![비발미고오한])열등(함으로|하여|하므로|하고)"
    r"|(?<![비발미고오한])열위)"
)
_INF_AFTER_NEG = ("않", "아니", "어렵", "어려", "없")
RE_CMP_NONE = re.compile(r"(대체(가능)?약제(가)?없|대체약제가없)")

# guideline
RE_GUIDELINE = re.compile(r"(진료지침|NCCN|ESMO|가이드라인)")

# RuleBasis excerpt anchors
RE_RSA = re.compile(r"(위험분담|환급|총액제한|RSA)", re.IGNORECASE)
RE_POLICY = re.compile(r"(사회적요구|미충족|보장성|중증|사회적|요구도)")
RE_BIA = re.compile(r"(총액제한|재정)")

# amjilsim endpoint
RE_ORR_PFS = re.compile(r"(ORR|PFS)", re.IGNORECASE)
RE_ATMP = re.compile(r"(CAR-?T|첨단바이오|ATMP)", re.IGNORECASE)


# ─────────────────────────── 라벨 ───────────────────────────

def label_of(review_result: str | None) -> str | None:
    if review_result in ("APPROVED", "CONDITIONAL_APPROVED"):
        return "통과"
    if review_result == "REJECTED":
        return "미통과"
    return None  # UNKNOWN → 제외


# ─────────────────────────── 순서형 파생 (yakpyeong) ───────────────────────────

def derive_price_vs_eval(ct: str) -> tuple[str, str]:
    """(value, confidence). TEXT ONLY — label(review_result) 미사용(타깃 누수 차단)."""
    if RE_ACCEPT.search(ct):
        return "소폭초과", "high"
    if RE_HIGHCOST.search(ct):
        return "대폭초과", "high"
    if RE_COST_EFFECTIVE.search(ct):
        return "이하", "high"
    return "이하", "low"


def derive_icer(ct: str, pe_waiver: int) -> tuple[str, str]:
    """TEXT ONLY — label 미사용. 면제는 GRANTED AND NOT DENIED(명시 부인 행 거짓 면제 차단)."""
    if (pe_waiver == 1 or RE_WAIVER.search(ct)) and not RE_WAIVER_DENY.search(ct):
        return "면제", "high"
    if RE_ICER_HIGH.search(ct):
        return "초과", "high"
    if RE_ICER_LOW.search(ct):
        return "이하", "high"
    return "근처", "low"


def derive_comparator(ct: str) -> tuple[str, str]:
    # 강한 열위(임상적 열등 진술) 먼저 — 동등어(유사) 동반 시에도 열위 확보. 부정어 우선(뒤 창).
    if first_nonneg_match(ct, RE_CMP_INF_STRONG, after_neg=_INF_AFTER_NEG):
        return "열위", "high"
    # 임상적 유용성 개선 인정 → 우위 — '치료적위치가동등한제품이없고'류 동등 오탐보다 먼저. 부정어 우선.
    if first_nonneg_match(ct, RE_CMP_IMPROVE, after_neg=_IMP_AFTER_NEG):
        return "우위", "med"
    if RE_CMP_EQUAL.search(ct):
        return "동등", "high"
    # 우위: 부정어 우선(앞 8자 열등/미/불, 뒤 창 않/못/없/아니/어렵/불충분)
    if first_nonneg_match(
        ct, RE_CMP_SUP,
        before_neg=("열등", "미", "불"),
        after_neg=("않", "못", "없", "아니", "어렵", "불충분"),
    ):
        return "우위", "med"
    # 열위(약한 신호): 부정어 우선 + 어절경계 오탐 차단(RE_CMP_INF lookbehind 로 발열/미열/비열등 제외).
    if first_nonneg_match(ct, RE_CMP_INF, after_neg=_INF_AFTER_NEG):
        return "열위", "high"
    if RE_CMP_NONE.search(ct):
        return "없음", "high"
    return "동등", "low"


def parse_json_list(raw: str | None) -> list:
    if not raw:
        return []
    try:
        v = json.loads(raw)
        return v if isinstance(v, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def derive_bia_high(row: dict, ct: str) -> bool:
    tags = row.get("policy_tags") or ""
    if "재정" in tags:  # policy_tags LIKE '%재정%' (재정/재정위험)
        return True
    if row.get("rsa_type_hint") == "총액제한":
        return True
    if "총액제한" in ct:
        return True
    return False


def derive_guideline(row: dict, ct: str) -> bool:
    if RE_GUIDELINE.search(ct):
        return True
    if parse_json_list(row.get("consulted_societies")):
        return True
    return False


def case_confidence(derivable: bool, confs: list[str]) -> str:
    if not derivable:
        return "low"
    lows = sum(1 for c in confs if c == "low")
    if lows == 0:
        return "high"
    if lows == 1:
        return "med"
    return "low"


# ─────────────────────────── RuleBasis (§3.3) ───────────────────────────
# outcome 일치 + 발췌 존재 시에만. ruleRef/label 은 빈 문자열(ruleMap.ts가 채움). sourceExcerpt 필수.

def make_basis(row: dict, ct: str, label: str, comparator: str, bia_high: bool) -> list[dict]:
    bases: list[dict] = []
    driver = row.get("approval_driver") or ""
    has_rsa = int(row.get("has_rsa") or 0)
    pe_waiver = int(row.get("pe_waiver") or 0)
    rsa_hint = row.get("rsa_type_hint") or ""

    def add(code: str, polarity: str, m: re.Match | None):
        if m is None:
            return  # 발췌 없으면 basis 생략
        bases.append({
            "code": code, "ruleRef": "", "label": "",
            "polarity": polarity, "sourceExcerpt": excerpt_from(ct, m),
        })

    # COST_EFFECTIVE + 통과 → KR-COST-EFFECTIVE (pass)
    if driver == "COST_EFFECTIVE" and label == "통과":
        add("KR-COST-EFFECTIVE", "pass", RE_COST_EFFECTIVE.search(ct))

    # REJECTED_COST + 미통과 → KR-REJECT-COST (reject). label=통과면 생략(오표기 차단).
    if driver == "REJECTED_COST" and label == "미통과":
        m = RE_ICER_HIGH.search(ct) or RE_HIGHCOST.search(ct)
        add("KR-REJECT-COST", "reject", m)

    # PE_WAIVER (driver 또는 pe_waiver=1), 통과 시만 → KR-PE-WAIVER (pass)
    if (driver == "PE_WAIVER" or pe_waiver == 1) and label == "통과":
        add("KR-PE-WAIVER", "pass", RE_WAIVER.search(ct))

    # has_rsa=1, 통과 시만 → 총액제한? KR-RSA-CAP : KR-RSA-REFUND (pass)
    if has_rsa == 1 and label == "통과":
        code = "KR-RSA-CAP" if rsa_hint == "총액제한" else "KR-RSA-REFUND"
        add(code, "pass", RE_RSA.search(ct))

    # POLICY_PRIORITY + 통과 → KR-POLICY-PRIORITY (pass)
    if driver == "POLICY_PRIORITY" and label == "통과":
        add("KR-POLICY-PRIORITY", "pass", RE_POLICY.search(ct))

    # comparator=열위 + 미통과 → KR-COMPARATOR-INF (reject)
    if comparator == "열위" and label == "미통과":
        add("KR-COMPARATOR-INF", "reject", RE_CMP_INF.search(ct))

    # bia_high + 미통과 → KR-BIA-RISK (reject). 발췌는 dr 내 총액제한/재정.
    if bia_high and label == "미통과":
        add("KR-BIA-RISK", "reject", RE_BIA.search(ct))

    return bases


# ─────────────────────────── 케이스 빌드 ───────────────────────────

def source_str(row: dict) -> str:
    yr = row.get("session_year")
    if yr:
        return f"analog {yr}"
    rr = row.get("review_result") or "analog"
    return f"analog {rr}"


def build_yakpyeong_case(row: dict) -> dict | None:
    label = label_of(row.get("review_result"))
    if label is None:
        return None

    dr = row.get("decision_reason") or ""
    derivable = len(dr) >= 40
    ct = compact(dr)

    if derivable:
        price_vs_eval, c_price = derive_price_vs_eval(ct)
        icer, c_icer = derive_icer(ct, int(row.get("pe_waiver") or 0))
        comparator, c_cmp = derive_comparator(ct)
        confs = [c_price, c_icer, c_cmp]
    else:
        # derivable=false → 순서형 지표 제외(구조적 기본값), confidence low
        price_vs_eval, icer, comparator = "이하", "근처", "동등"
        confs = []

    rsa_offered = int(row.get("has_rsa") or 0) == 1
    guideline_listed = derive_guideline(row, ct)
    bia_high = derive_bia_high(row, ct)

    inp = {
        "price_vs_eval": price_vs_eval,
        "icer": icer,
        "rsa_offered": rsa_offered,
        "comparator": comparator,
        "guideline_listed": guideline_listed,
        "bia_high": bia_high,
    }

    case = {
        "id": f"analog-{row['id']}",
        "kind": "yakpyeong",
        "drug": row.get("brand_name") or row.get("generic_name") or "(미상)",
        "source": source_str(row),
        "input": inp,
        "actual": label,
        "provenance": "analog",
        "confidence": case_confidence(derivable, confs),
        "derivable": derivable,
    }

    if derivable:
        bases = make_basis(row, ct, label, comparator, bia_high)
        if bases:
            case["basis"] = bases

    return case


# ─────────────────────────── amjilsim ───────────────────────────
# amjilsim_history JSON 에 result(통과/미통과류) 보유 행만. 없으면 정직하게 제외.

def extract_amjilsim_result(raw: str | None) -> str | None:
    entries = parse_json_list(raw)
    for e in entries:
        if not isinstance(e, dict):
            continue
        for k in ("result", "review_result", "outcome", "결과"):
            v = e.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def amjilsim_label(result_raw: str) -> str | None:
    r = result_raw
    if any(t in r for t in ("통과", "APPROVED", "설정", "급여")):
        if "미통과" in r or "미설정" in r or "부결" in r or "REJECT" in r.upper():
            return "미통과"
        return "통과"
    if any(t in r for t in ("미통과", "미설정", "부결", "REJECT")):
        return "미통과"
    return None


def build_amjilsim_case(row: dict) -> dict | None:
    result_raw = extract_amjilsim_result(row.get("amjilsim_history"))
    if result_raw is None:
        return None
    label = amjilsim_label(result_raw)
    if label is None:
        return None

    dr = row.get("decision_reason") or ""
    ct = compact(dr)
    derivable = len(dr) >= 40

    os_months = row.get("os_months")
    if os_months is not None:
        os_years = round(os_months / 12)
        c_os = "high"
    else:
        os_years, c_os = 2, "low"

    primary = row.get("primary_endpoint") or ""
    os_not_endpoint = bool(RE_ORR_PFS.search(primary)) and os_months is None

    comparator, _ = derive_comparator(ct)
    comparator_os = None
    if comparator == "우위":
        comparator_os = "superior"
    elif comparator == "열위":
        comparator_os = "inferior"

    bia_high = derive_bia_high(row, ct)
    domestic = bool(row.get("manufacturer")) and bool(RE_ATMP.search(ct))

    inp = {
        "os_years": os_years,
        "rsa_offered": int(row.get("has_rsa") or 0) == 1,
        "bia_high": bia_high,
        "resubmission": int(row.get("requeue_count") or 0) >= 1,
        "os_not_endpoint": os_not_endpoint,
        "domestic_atmp_no_standard": domestic,
    }
    if comparator_os is not None:
        inp["comparator_os"] = comparator_os

    return {
        "id": f"analog-{row['id']}",
        "kind": "amjilsim",
        "drug": row.get("brand_name") or row.get("generic_name") or "(미상)",
        "source": source_str(row),
        "input": inp,
        "actual": label,
        "provenance": "analog",
        "confidence": "low" if not derivable else c_os,
        "derivable": derivable,
    }


# ─────────────────────────── _meta ───────────────────────────

def is_placeholder_date(d: str | None) -> bool:
    return bool(d) and d.endswith("-01-01")


def is_real_date(d: str | None) -> bool:
    return bool(d) and not d.endswith("-01-01")


def db_content_hash(rows: list[dict]) -> str:
    """쿼리 결과셋 내용 해시(결정적) — rowid|review_result|decision_reason 연결의 sha256 앞 12hex.

    파일 st_size/st_mtime_ns 는 VACUUM/재복사만으로 바뀌어 비결정적이므로 내용 기반으로 대체.
    """
    h = hashlib.sha256()
    for r in rows:
        h.update(
            f"{r['id']}|{r.get('review_result') or ''}|{r.get('decision_reason') or ''}"
            .encode("utf-8")
        )
    return h.hexdigest()[:12]


def build_meta(source_hash: str, cases: list[dict], n_excluded_unknown: int,
               rows_for_dates: list[dict]) -> dict:
    n_total = len(cases)
    n_derivable = sum(1 for c in cases if c["derivable"])
    n_reject_derivable = sum(1 for c in cases if c["derivable"] and c["actual"] == "미통과")
    band = {"통과": 0, "미통과": 0}
    for c in cases:
        if c["actual"] in band:
            band[c["actual"]] += 1
    # 날짜 통계는 실제 포함 케이스의 원본 row session_date 기준
    n_real = sum(1 for r in rows_for_dates if is_real_date(r.get("session_date")))
    n_ph = sum(1 for r in rows_for_dates if is_placeholder_date(r.get("session_date")))
    return {
        "sourceDbHash": source_hash,
        "rulesetVersion": RULESET_VERSION,
        "nTotal": n_total,
        "nExcludedUnknown": n_excluded_unknown,
        "nRealDate": n_real,
        "nPlaceholder": n_ph,
        "nNoDate": n_total - n_real - n_ph,
        "nDerivable": n_derivable,
        "nRejectDerivable": n_reject_derivable,
        # 미통과 파생 케이스 — 순서형 파생이 전부 텍스트 전용이므로 라벨 오염 없음(clean)
        "nRejectClean": n_reject_derivable,
        "rejectSampleCaveat": "부결 유효 표본 소규모 — 부결측 지표는 신뢰구간 넓음, 참고용",
        "byBand": band,
    }


# ─────────────────────────── main ───────────────────────────

def fetch_rows(db_path: Path) -> list[dict]:
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    cols = ", ".join(COLUMNS)
    # ORDER BY id — 내용 해시(db_content_hash)의 결정성 보장
    rows = [dict(r) for r in conn.execute(f"SELECT {cols} FROM analog_reports ORDER BY id")]
    conn.close()
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="analog_reports → calibration JSON export")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--yak-out", type=Path, default=DEFAULT_YAK_OUT)
    ap.add_argument("--amj-out", type=Path, default=DEFAULT_AMJ_OUT)
    args = ap.parse_args()

    rows = fetch_rows(args.db)
    source_hash = db_content_hash(rows)

    # yakpyeong
    yak_cases: list[dict] = []
    yak_rows_included: list[dict] = []
    n_excluded_unknown = 0
    for row in rows:
        if label_of(row.get("review_result")) is None:
            n_excluded_unknown += 1
            continue
        case = build_yakpyeong_case(row)
        if case is not None:
            yak_cases.append(case)
            yak_rows_included.append(row)

    yak_meta = build_meta(source_hash, yak_cases, n_excluded_unknown, yak_rows_included)

    # amjilsim
    amj_cases: list[dict] = []
    amj_rows_included: list[dict] = []
    for row in rows:
        case = build_amjilsim_case(row)
        if case is not None:
            amj_cases.append(case)
            amj_rows_included.append(row)
    # amjilsim 은 UNKNOWN 개념이 다름 — result 미보유 행이 제외분(nExcludedUnknown 로 표기)
    amj_excluded = len(rows) - len(amj_cases)
    amj_meta = build_meta(source_hash, amj_cases, amj_excluded, amj_rows_included)

    args.yak_out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.yak_out, "w", encoding="utf-8") as f:
        json.dump({"_meta": yak_meta, "cases": yak_cases}, f, ensure_ascii=False, indent=2)
    with open(args.amj_out, "w", encoding="utf-8") as f:
        json.dump({"_meta": amj_meta, "cases": amj_cases}, f, ensure_ascii=False, indent=2)

    print(f"yakpyeong: {len(yak_cases)} cases -> {args.yak_out}")
    print(f"  meta: {json.dumps(yak_meta, ensure_ascii=False)}")
    print(f"amjilsim: {len(amj_cases)} cases -> {args.amj_out}")
    print(f"  meta: {json.dumps(amj_meta, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
