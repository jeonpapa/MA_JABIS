"""투약비용비교 → 수식이 살아있는 xlsx 생성.

엑셀에서 환자 파라미터·용량을 바꾸면 BSA/GFR·용량(mg)·치료비가 재계산되도록
수식으로 작성(레지멘DB 계산기 방식). 약가(단위가·함량)는 as-of DB 조회 스냅샷이라
값으로 기입하고, 그 외(1회mg·주기총량·사이클/코스/월/연)는 수식.

시트: 계산기(환자) · 비교(레지멘별 요약) · 레지멘별 1시트씩.
"""
from __future__ import annotations

import io
import re

CALC = "계산기"


# ── 공용 비용 로직 (POST /api/regimen/onco/cost · export 공유, 순수 함수 — DB/flask 비의존) ──

WAP_NOT_CONNECTED_NOTE = "주성분 가중평균 데이터 연결 전 — 국내 상한금액 기준으로 계산합니다"
WAP_NO_DATA_NOTE = "주성분 가중평균 없음 — 국내 상한금액 기준으로 계산합니다"

COUNT_UNITS = {"정", "tab", "tablet", "캡슐", "cap", "capsule", "포", "환", "매",
               "vial", "바이알", "unit", "단위"}

# 레지멘 정본 약제명 → drug_latest 영문 ingredient 접두 (제형 구분이 필요한 것만)
_PRICE_KEY_ALIASES = {
    "nab-paclitaxel": "paclitaxel(albumin-bound)",
    "liposomal doxorubicin": "liposomal doxorubicin",
}
_SALT_WORDS = {"hydrochloride", "hcl", "acetate", "sodium", "mesylate", "mesilate", "maleate",
               "sulfate", "tosylate", "citrate", "phosphate", "dihydrochloride", "besylate",
               "malate", "fumarate", "tartrate", "bromide", "calcium", "potassium", "hydrate",
               "monohydrate", "anhydrous", "disodium", "dimaleate", "succinate"}
_INJECTION_ROUTES = {"iv", "sc", "im", "it", "ivp", "civ", "주사"}
_ORAL_ROUTES = {"po", "경구"}


def friendly_price_reason(reason: str | None) -> str:
    """내부 사유(환경변수명·HTTP 코드 등)를 사용자 문구로. 환경변수명은 절대 노출 금지."""
    r = (reason or "").strip()
    if not r:
        return "국내 약가 매칭 없음"
    if re.search(r"[A-Z][A-Z0-9]*_[A-Z0-9_]+", r) or "미설정" in r or "비활성" in r:
        return "주성분 가중평균 데이터 연결 전"
    if "HTTP" in r or "timeout" in r.lower() or "timed out" in r.lower() or "URLError" in r:
        return "주성분 가중평균 조회 실패 (일시 오류)"
    return r


def price_lookup_keys(ingredient: str | None) -> list[str]:
    """정본 약제명 → 가격조회용 INN 후보(우선순위). 'Fluorouracil(bolus)'→'Fluorouracil' 등."""
    s = re.sub(r"\s+", " ", (ingredient or "").strip())
    if not s:
        return []
    out: list[str] = []
    alias = _PRICE_KEY_ALIASES.get(s.lower())
    if alias:
        out.append(alias)
    out.append(s)
    stripped = re.sub(r"\([^)]*\)", "", s).strip()                 # (bolus)/(CI)/(HD)/(ATRA)
    stripped = re.sub(r"\s+(LAR|SR|XR|ER)$", "", stripped, flags=re.I).strip()
    if stripped and stripped not in out:
        out.append(stripped)
    if "/" in stripped:                                            # 복합제 첫 성분
        first = stripped.split("/")[0].strip()
        if first and first not in out:
            out.append(first)
    return out


def ingredient_name_part(text: str | None) -> str:
    """drug_latest 영문 ingredient → 성분명 부분(첫 숫자/괄호 전, 소문자). 'pembrolizumab   0.1g(…)'→'pembrolizumab'."""
    s = (text or "").strip().lower()
    m = re.match(r"[^0-9]*", s)
    name = (m.group(0) if m else s)
    # 'paclitaxel(albumin-bound)' 같은 제형 괄호는 보존, 이후 공백 정리
    return re.sub(r"\s+", " ", name).strip(" ,(")


def inn_from_ingredient_text(text: str | None) -> str:
    """drug_latest ingredient → dosing 조회용 INN(염 단어 제거). 'doxorubicin hydrochloride 10mg'→'doxorubicin'."""
    name = ingredient_name_part(text)
    name = re.sub(r"\(.*$", "", name).strip()
    words = [w for w in name.split() if w not in _SALT_WORDS]
    return " ".join(words) or name


def _product_form(product_name_kr: str | None) -> str:
    """제품명 → 'injection' | 'oral' | 'topical' | ''. (drug_latest.dosage_form 은 대부분 비어 있음)"""
    n = product_name_kr or ""
    core = re.split(r"[(_]", n, maxsplit=1)[0]
    if re.search(r"점안|점이|연고|크림|겔|패취|패치|외용|흡입|좌제|스프레이", n):
        return "topical"
    if core.endswith("주") or "주사" in core or re.search(r"주\d|주$", core) or "프리필드" in n:
        return "injection"
    if re.search(r"정|캡슐|시럽|현탁|산$|과립|액$", core):
        return "oral"
    return ""


def rank_domestic_candidates(cands: list[dict], inn: str, route: str | None = None) -> list[dict]:
    """find_by_ingredient_strength 후보 → 동일 성분·단일제·투여경로 일치 우선 정렬(원 순서 보존).

    접두 LIKE 는 'paclitaxel%' 에 아브락산(paclitaxel(albumin-bound)), 'prednisolone%' 에
    점안액까지 섞는다. 성분명 부분이 INN 과 같거나(INN+염) 일치하는 단일제를 앞으로.
    """
    key = (inn or "").strip().lower()
    r = (route or "").strip().lower()
    want = "injection" if r in _INJECTION_ROUTES else "oral" if r in _ORAL_ROUTES else ""

    def score(c: dict) -> tuple:
        ing = (c.get("ingredient") or "").lower()
        name = ingredient_name_part(ing)
        exact = name == key or (name.startswith(key + " ") and all(
            w in _SALT_WORDS for w in name[len(key):].split()))
        single = "," not in ing
        form = _product_form(c.get("product_name_kr"))
        form_ok = (not want) or form == want or (form == "" and want == "injection")
        # 경구는 정·캡슐 우선 (시럽은 상한금액이 mL 단위라 병 함량과 어긋남)
        solid = bool(re.search(r"정|캡슐", re.split(r"[(_]", c.get("product_name_kr") or "", maxsplit=1)[0]))
        liquid_penalty = 1 if (want == "oral" and not solid) else 0
        return (0 if exact else 1, 0 if single else 1, 0 if form_ok else 1,
                1 if form == "topical" else 0, liquid_penalty)

    pool = list(cands or [])
    if want:   # 전신 투여 레지멘에 점안액·연고 등 국소제는 가격 후보에서 제외
        pool = [c for c in pool if _product_form(c.get("product_name_kr")) != "topical"]
    indexed = list(enumerate(pool))
    indexed.sort(key=lambda t: (score(t[1]), t[0]))
    return [c for _, c in indexed]


def compute_drug_cost(d: dict, pm: dict) -> dict:
    """compute_drug_dose 결과 행 + 단가 → {cost, missing_reason, course_reason}. 산출 불가 항목은 None(₩0 금지)."""
    cost = {"cycle": None, "course": None, "monthly": None, "yearly": None, "daily": None}
    unit = (d.get("unit") or "").strip().lower()
    cyc_mg = d.get("cycle_total_mg")
    reason = None
    cyc = None
    if d.get("dose_value") in (None, ""):
        reason = "용량 정보 없음"
    elif not pm.get("available"):
        reason = friendly_price_reason(pm.get("reason"))
    elif cyc_mg is not None and pm.get("price_per_mg"):
        cyc = cyc_mg * pm["price_per_mg"]
    elif unit in COUNT_UNITS and pm.get("unit_price"):
        try:
            cyc = float(d["dose_value"]) * float(d.get("per_cycle") or 1) * pm["unit_price"]
        except (TypeError, ValueError):
            reason = "용량 정보 없음"
    else:
        reason = f"용량 단위 계산 불가 ({d.get('unit') or '단위 없음'})"
    try:
        cycle_days = float(d.get("cycle_days") or 0)
    except (TypeError, ValueError):
        cycle_days = 0
    if cyc is not None and cycle_days <= 0:
        cyc, reason = None, "주기(일) 미입력"
    course_reason = None
    if cyc is not None:
        yearly = cyc * 365 / cycle_days
        cost.update(cycle=round(cyc), monthly=round(cyc * 30 / cycle_days),
                    yearly=round(yearly), daily=round(yearly / 365))
        tc = d.get("total_cycles")
        try:
            tc = float(tc) if tc not in (None, "") else None
        except (TypeError, ValueError):
            tc = None
        if tc and tc > 0:
            cost["course"] = round(cyc * tc)
        else:
            course_reason = "총사이클 미입력"
    return {"cost": cost, "missing_reason": reason, "course_reason": course_reason}


def sum_regimen_totals(costs: list[dict]) -> dict:
    """약제별 cost → 레지멘 합계. 산출 약제가 없으면 None(미산출), 일부 누락이면 partial 표시.

    전체코스는 하나라도 총사이클 미입력이면 None (부분합은 오해 소지).
    """
    keys = ("cycle", "course", "monthly", "yearly", "daily")
    out: dict = {}
    partial: dict = {}
    for k in keys:
        vals = [c.get(k) for c in costs]
        got = [v for v in vals if v is not None]
        if k == "course":
            ok = bool(costs) and len(got) == len(vals)
            out[k] = round(sum(got)) if ok else None
            partial[k] = False
        else:
            out[k] = round(sum(got)) if got else None
            partial[k] = bool(got) and len(got) < len(vals)
    missing = sum(1 for c in costs if c.get("cycle") is None)
    out["hasMissing"] = missing > 0
    out["missingCount"] = missing
    out["computedCount"] = len(costs) - missing
    out["partial"] = partial
    return out


def _safe_sheet(name: str, used: set, idx: int) -> str:
    s = re.sub(r"[\[\]:*?/\\]", " ", name or f"레지멘{idx}").strip()[:26] or f"레지멘{idx}"
    base = f"{idx}.{s}"[:31]
    out, n = base, 2
    while out in used:
        out = f"{base[:28]}_{n}"
        n += 1
    used.add(out)
    return out


def _calc_sheet(ws, patient: dict):
    p = {"height": 165, "weight": 62, "age": 60, "sex": "M", "scr": 0.9, **(patient or {})}
    ws["A1"] = "환자 파라미터 (노란칸 입력 → 자동 계산)"
    rows = [("키 (cm)", "height"), ("체중 (kg)", "weight"), ("나이", "age"),
            ("성별 (M/F)", "sex"), ("SCr (mg/dL)", "scr")]
    for i, (label, key) in enumerate(rows, start=2):
        ws.cell(i, 1, label)
        ws.cell(i, 2, p[key])
    ws["A8"] = "BSA Mosteller (m²)"; ws["B8"] = "=SQRT(B2*B3/3600)"
    ws["A9"] = "BSA DuBois (m²)"; ws["B9"] = "=0.007184*B2^0.725*B3^0.425"
    ws["A10"] = "적용 BSA (m²)"; ws["B10"] = "=B8"
    ws["A11"] = "CrCl Cockcroft-Gault"; ws["B11"] = '=((140-B4)*B3*IF(B5="F",0.85,1))/(72*B6)'
    ws["A12"] = "적용 GFR (cap125)"; ws["B12"] = "=MIN(B11,125)"
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 16


_HDR = ["약제", "용량값", "단위", "투여일", "회수/주기", "주기(일)", "총사이클",
        "1회(mg)", "주기총량(mg)", "단위가(₩)", "함량(mg)", "mg당단가",
        "사이클(₩)", "코스(₩)", "월(₩)", "연(₩)", "가격소스/규격"]


def _regimen_sheet(ws, reg: dict):
    """reg: {name, drugs:[{ingredient, dose_value, unit, dose_days, per_cycle, cycle_days,
            total_cycles, price:{unit_price, content_mg, label, available}}]}."""
    for c, h in enumerate(_HDR, start=1):
        ws.cell(1, c, h)
    drugs = reg.get("drugs") or []
    r0 = 2
    for i, d in enumerate(drugs):
        r = r0 + i
        price = d.get("price") or {}
        is_count = (d.get("unit") or "").strip().lower() in (
            "정", "tab", "tablet", "캡슐", "cap", "capsule", "포", "환", "매", "vial", "바이알", "unit", "단위")
        ws.cell(r, 1, d.get("ingredient"))
        ws.cell(r, 2, d.get("dose_value"))
        ws.cell(r, 3, d.get("unit"))
        ws.cell(r, 4, d.get("dose_days"))
        ws.cell(r, 5, d.get("per_cycle") or 1)
        ws.cell(r, 6, d.get("cycle_days") or 1)
        ws.cell(r, 7, d.get("total_cycles"))
        # 1회(mg) — 단위별 수식 (계산기 BSA/체중/GFR 참조)
        ws.cell(r, 8, (
            f'=IF($C{r}="mg/m2",$B{r}*\'{CALC}\'!$B$10,'
            f'IF($C{r}="mg/m2/day",$B{r}*\'{CALC}\'!$B$10,'
            f'IF($C{r}="g/m2",$B{r}*1000*\'{CALC}\'!$B$10,'
            f'IF($C{r}="mg/kg",$B{r}*\'{CALC}\'!$B$3,'
            f'IF($C{r}="mg/kg/day",$B{r}*\'{CALC}\'!$B$3,'
            f'IF($C{r}="AUC",$B{r}*(\'{CALC}\'!$B$12+25),$B{r}))))))'
        ))
        ws.cell(r, 9, f'=IF(N($B{r})=0,"",$H{r}*$E{r})')                 # 주기총량
        up = price.get("unit_price")
        ws.cell(r, 10, up if up is not None else None)                   # 단위가(값)
        # 함량: count 단위는 1(주기총량=count, mg당단가=단위가)
        ws.cell(r, 11, 1 if is_count else price.get("content_mg"))
        ws.cell(r, 12, f'=IF(N($K{r})=0,"",$J{r}/$K{r})')               # mg당단가
        ws.cell(r, 13, f'=IF(OR($I{r}="",$L{r}=""),"",$I{r}*$L{r})')     # 사이클
        ws.cell(r, 14, f'=IF(OR($M{r}="",N($G{r})=0),"",$M{r}*$G{r})')   # 코스
        ws.cell(r, 15, f'=IF(OR($M{r}="",N($F{r})=0),"",$M{r}*30/$F{r})')   # 월
        ws.cell(r, 16, f'=IF(OR($M{r}="",N($F{r})=0),"",$M{r}*365/$F{r})')  # 연
        eff_src = price.get("source") or d.get("price_source") or ""
        ws.cell(r, 17, (("가중평균" if eff_src == "weighted_avg" else "국내 상한금액")
                        + (f" · {price.get('label')}" if price.get("label") else "")
                        + (" · 가중평균 연결 전(국내가 대체)" if price.get("fallback") else "")
                        + ("" if price.get("available", True)
                           else f" · 미산출: {friendly_price_reason(price.get('reason'))}")))
    last = r0 + len(drugs) - 1 if drugs else r0
    tot = last + 1
    ws.cell(tot, 1, "합계")
    for col in (13, 14, 15, 16):
        L = chr(64 + col)
        ws.cell(tot, col, f'=SUM({L}{r0}:{L}{last})' if drugs else 0)
    for c, w in [(1, 22), (3, 12), (9, 13), (10, 12), (12, 11), (13, 13), (14, 13), (15, 13), (16, 13), (17, 30)]:
        ws.column_dimensions[chr(64 + c)].width = w
    return tot  # 합계 행 번호


def build_regimen_xlsx(date: str, source: str, patient: dict, regimens: list[dict]) -> io.BytesIO:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    calc = wb.active
    calc.title = CALC
    _calc_sheet(calc, patient)

    cmp_ws = wb.create_sheet("비교")
    used = {CALC, "비교"}
    sheet_refs = []  # (regimen_name, sheet_name, total_row, has_cycle)
    for i, reg in enumerate(regimens, start=1):
        sn = _safe_sheet(reg.get("name") or f"레지멘{i}", used, i)
        ws = wb.create_sheet(sn)
        tot = _regimen_sheet(ws, reg)
        has_cycle = any((d.get("cycle_days") or 1) > 1 for d in (reg.get("drugs") or []))
        sheet_refs.append((reg.get("name") or sn, sn, tot, has_cycle))

    # 비교 시트
    cmp_ws["A1"] = f"레지멘별 비용 비교  (기준일 {date} · 소스 {'주성분 가중평균' if source == 'weighted_avg' else '국내 상한금액'})"
    hdr = ["레지멘", "1사이클", "전체코스", "일", "월", "연"]
    for c, h in enumerate(hdr, start=1):
        cell = cmp_ws.cell(3, c, h)
        cell.font = Font(bold=True)
    for i, (name, sn, tot, has_cycle) in enumerate(sheet_refs):
        r = 4 + i
        cmp_ws.cell(r, 1, name)
        q = f"'{sn}'"
        cmp_ws.cell(r, 2, f"={q}!M{tot}" if has_cycle else "—")        # 1사이클
        cmp_ws.cell(r, 3, f"={q}!N{tot}" if has_cycle else "—")        # 전체코스
        cmp_ws.cell(r, 4, f"={q}!P{tot}/365")                          # 일 = 연/365
        cmp_ws.cell(r, 5, f"={q}!O{tot}")                              # 월
        cmp_ws.cell(r, 6, f"={q}!P{tot}")                              # 연
    for c, w in [(1, 30), (2, 14), (3, 14), (4, 12), (5, 14), (6, 16)]:
        cmp_ws.column_dimensions[chr(64 + c)].width = w
    cmp_ws["A2"] = "※ 1사이클·전체코스는 항암 레지멘(주기 투여)만 · 약가(단위가·함량)는 기준일 DB 스냅샷, 그 외 셀은 수식으로 재계산"

    # 통화 서식
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and cell.value.startswith("=") and any(
                        t in cell.value for t in ("!M", "!N", "!O", "!P", "$L", "$J", "$M")):
                    cell.number_format = "#,##0"

    # 노란 입력칸 강조
    yellow = PatternFill("solid", fgColor="FFF6C0")
    for r in range(2, 7):
        calc.cell(r, 2).fill = yellow

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
