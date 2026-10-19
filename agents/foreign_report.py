"""해외약가 A8 리포트 생성 — xlsx(openpyxl) + hwpx(python-hwpx).

첨부 양식: 국가별 컬럼 × (외국약가/병·/정·적용환율·환산가·조정가·국내신청가비율·출처) 행.
데이터원: ForeignPriceAgent.get_cached_results (단일 진실원). 조정가는 용량 정규화가
(normalized) 우선. 국내 신청가(표시가/실제가)는 다운로드 시 입력폼으로 받음.
"""
from __future__ import annotations

import io
from datetime import date

from agents.exchange_rate import per_unit_rate

# 표시 순서 (데이터 있는 국가만 노출)
COUNTRY_ORDER = ["US", "UK", "CA", "DE", "FR", "IT", "CH", "JP"]
COUNTRY_KR = {"US": "미국", "UK": "영국", "CA": "캐나다", "DE": "독일",
              "FR": "프랑스", "IT": "이탈리아", "CH": "스위스", "JP": "일본"}
CUR_SYM = {"USD": "$", "GBP": "£", "CAD": "C$", "EUR": "€", "JPY": "¥", "CHF": "CHF"}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


EXCLUDE_REASON_KR = {
    "pack_count_unverified": "포장수량 미확인 (팩가가 단위가로 저장 — 재수집 필요)",
    "no_adjusted_price": "조정가 미산출",
}


def build_report_model(cached: dict, applied_display=None, applied_actual=None) -> dict:
    """get_cached_results 결과 → A8 리포트 모델 (컬럼=국가, 행=지표).

    집계 안전망: `a8_aggregate_eligible` 가 False 인 행(pack_count_source=default 인 pack 국가 oral 행 등)은
    국가 컬럼·평균에 넣지 않고 `excluded` 에 사유와 함께 남긴다 — 틀린 단위가를 공식 리포트에 싣지 않는다.
    """
    cols = []
    excluded = []
    for c in COUNTRY_ORDER:
        rows = cached.get(c) or []
        priced = [r for r in rows if r.get("adjusted_price_krw") is not None]
        if not priced:
            continue
        eligible = [r for r in priced if r.get("a8_aggregate_eligible", True)]
        if not eligible:
            reason = priced[0].get("a8_exclude_reason") or "pack_count_unverified"
            excluded.append({"country": c, "country_kr": COUNTRY_KR.get(c, c), "reason": reason})
            continue
        r = eligible[0]
        per_unit = _num(r.get("per_unit_local"))
        # '적용환율' 은 통화 1단위당 — JPY 레거시 행은 100엔당(923.21) 로 저장돼 있어 그대로 쓰면 환산가와 자기모순
        fx = per_unit_rate(r.get("currency"), _num(r.get("exchange_rate")))
        krw_conv = _num(r.get("krw_converted"))
        if krw_conv is None and per_unit is not None and fx is not None:
            krw_conv = per_unit * fx
        adj = _num(r.get("adjusted_price_krw_normalized")) or _num(r.get("adjusted_price_krw"))
        cols.append({
            "country": c,
            "country_kr": COUNTRY_KR.get(c, c),
            "currency": r.get("currency") or "",
            "sym": CUR_SYM.get(r.get("currency") or "", ""),
            "local_price": _num(r.get("local_price")),
            "per_unit_local": per_unit,
            "pack_count": int(r.get("pack_count") or 1),
            "exchange_rate": fx,
            "fx_from": r.get("exchange_rate_from") or "",
            "fx_to": r.get("exchange_rate_to") or "",
            "krw_converted": krw_conv,
            "adjusted": adj,
            "source": r.get("source_label") or "",
            "dose_norm_note": r.get("dose_norm_note") or "",
        })

    actual = _num(applied_actual)
    adj_vals = [c["adjusted"] for c in cols if c["adjusted"] is not None]
    conv_vals = [c["krw_converted"] for c in cols if c["krw_converted"] is not None]
    avg_adj = sum(adj_vals) / len(adj_vals) if adj_vals else None
    avg_conv = sum(conv_vals) / len(conv_vals) if conv_vals else None
    # 국내신청가비율: 국가별 = 조정가/실제가, 평균칸 = 실제가/평균조정가 (양식 관례)
    for c in cols:
        c["ratio"] = (round(c["adjusted"] / actual * 100) if (actual and c["adjusted"]) else None)
    avg_ratio = round(actual / avg_adj * 100) if (actual and avg_adj) else None

    return {
        "cols": cols,
        "excluded": excluded,
        "avg": {"krw_converted": avg_conv, "adjusted": avg_adj, "ratio": avg_ratio},
        "applied_display": _num(applied_display),
        "applied_actual": actual,
        "fx_period": (cols[0]["fx_from"], cols[0]["fx_to"]) if cols else ("", ""),
        "pack_count": cols[0]["pack_count"] if cols else 1,
    }


def _rows_for_render(m: dict) -> tuple[list[str], list[list[str]]]:
    """(header, rows) — 첫 열은 행라벨, 이후 국가별, 마지막 평균."""
    cols = m["cols"]
    pack = m["pack_count"]
    header = ["등재국가 (통화)"] + [f"{c['country_kr']} ({c['sym']})" for c in cols] + ["평균"]

    def money(v, dash="-"):
        return f"{round(v):,}" if v is not None else dash

    def local(v, dash="-"):
        return f"{v:,.2f}" if v is not None else dash

    body = [
        [f"외국약가/병 (={pack}정) (해당국 화폐)"] + [local(c["local_price"]) for c in cols] + ["-"],
        ["외국약가/정 (해당국 화폐)"] + [local(c["per_unit_local"]) for c in cols] + ["-"],
        ["적용환율 (36개월 최종 평균 매매기준율)"] + [local(c["exchange_rate"]) for c in cols] + ["-"],
        ["환산가 (원)"] + [money(c["krw_converted"]) for c in cols] + [money(m["avg"]["krw_converted"])],
        ["조정가 (원)"] + [money(c["adjusted"]) for c in cols] + [money(m["avg"]["adjusted"])],
        ["국내신청가격비율 (실제가) (%)"]
            + [f"{c['ratio']}%" if c["ratio"] is not None else "-" for c in cols]
            + [f"{m['avg']['ratio']}%" if m["avg"]["ratio"] is not None else "-"],
        ["근거 (출처)"] + [c["source"] or "-" for c in cols] + ["-"],
    ]
    if m.get("excluded"):
        note = " · ".join(f"{e['country_kr']}: {EXCLUDE_REASON_KR.get(e['reason'], e['reason'])}" for e in m["excluded"])
        body.append(["집계 제외국"] + [f"{note}"] + ["" for _ in cols])
    return header, body


def _title(m: dict, query: str) -> str:
    disp = f"{round(m['applied_display']):,}" if m["applied_display"] else "-"
    act = f"{round(m['applied_actual']):,}" if m["applied_actual"] else "-"
    return f"{query} A8 약가 비교 (국내 신청가격: 표시가 {disp}원/정, 실제가 {act}/정)"


def export_xlsx(query: str, model: dict) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    THIN = Side(border_style="thin", color="BBBBBB")
    BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
    CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
    HFILL = PatternFill("solid", fgColor="00857C")
    LBLFILL = PatternFill("solid", fgColor="EAF2F1")

    header, body = _rows_for_render(model)
    wb = Workbook()
    ws = wb.active
    ws.title = "A8 비교"
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(header))
    tc = ws.cell(row=1, column=1, value=_title(model, query))
    tc.font = Font(bold=True, size=12)
    tc.alignment = CENTER
    # 헤더
    for j, h in enumerate(header, 1):
        cc = ws.cell(row=2, column=j, value=h)
        cc.fill = HFILL
        cc.font = Font(bold=True, color="FFFFFF", size=10)
        cc.alignment = CENTER
        cc.border = BORDER
    # 본문
    for i, row in enumerate(body, 3):
        for j, val in enumerate(row, 1):
            cc = ws.cell(row=i, column=j, value=val)
            cc.alignment = CENTER if j > 1 else Alignment(vertical="center", wrap_text=True)
            cc.border = BORDER
            if j == 1:
                cc.fill = LBLFILL
                cc.font = Font(bold=True, size=10)
    ws.column_dimensions["A"].width = 30
    for j in range(2, len(header) + 1):
        ws.column_dimensions[chr(64 + j) if j <= 26 else "A"].width = 14
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def export_hwpx(query: str, model: dict) -> bytes:
    from hwpx.document import HwpxDocument
    header, body = _rows_for_render(model)
    ncols = len(header)
    nrows = len(body) + 1  # +헤더

    doc = HwpxDocument.new()
    doc.add_paragraph(_title(model, query))
    table = doc.add_table(nrows, ncols)
    for j, h in enumerate(header):
        table.set_cell_text(0, j, str(h))
    for i, row in enumerate(body, 1):
        for j, val in enumerate(row):
            table.set_cell_text(i, j, str(val))
    return doc.to_bytes()


# ── 약가유연계약 신청 서식 (A8 비교표) ─────────────────────────────────────────────
# 서식 열 순서·자료원 표기는 공단 신청서 양식 그대로. 실제 수집 자료원이 서식과 다르면 그대로 밝힌다(대체하지 않는다).
FLEX_ORDER = ["US", "UK", "DE", "FR", "IT", "CH", "JP", "CA"]
FLEX_FORM_SOURCE = {
    "US": "Redbook (WAC)", "UK": "MIMS (약국판매가)", "DE": "ROTE LISTE (약국판매가)",
    "FR": "프랑스보건부 (공장도출하), Vidal (약국판매가)", "IT": "Codifa (공장도출하)",
    "CH": "SL GGSL (공장도출하)", "JP": "후생노동성 (약국판매가)", "CA": "PMPRB (공장도출하)",
}
TIER_KR = {"ex_factory": "공장도가", "pharmacy_purchase": "약국구입가", "pharmacy_retail": "약국판매가",
           "reimbursement": "급여가", "other": "기타"}
VAT_KR = {"included": "VAT 포함", "excluded": "VAT 제외", "unknown": "VAT 미확인"}


def _raw_meta(row: dict) -> dict:
    import json
    raw = row.get("raw_data")
    if isinstance(raw, dict):
        return raw
    try:
        val = json.loads(raw) if raw else {}
        return val if isinstance(val, dict) else {}
    except (TypeError, ValueError):
        return {}


def unit_word(row: dict) -> str:
    """최소단위 이름 — 원문 제형·포장 문구 우선(과립=포, 캡슐). 화면 unitWord.ts 와 같은 규칙."""
    import re
    text = f"{row.get('dosage_form') or ''} {row.get('package_unit') or ''}".lower()
    if (row.get("form_type") or "") == "injection":
        if re.search(r"prefilled|pre-filled|syringe|siringa|seringue|fertigspritze|シリンジ|주사기", text):
            return "시린지"
        if re.search(r"ampoule|ampul|fiala|アンプル|앰플", text):
            return "앰플"
        return "바이알"
    if re.search(r"granul|sachet|packet|bustin|beutel|顆粒|分包|과립", text):
        return "포"
    if re.search(r"capsul|gélule|gelule|kapsel|カプセル|캡슐", text):
        return "캡슐"
    if re.search(r"tablet|comprim|compress|tablett|錠|정", text):
        return "정"
    return "단위" if text.strip() and (row.get("form_type") == "oral") else ("정" if row.get("form_type") == "oral" else "단위")


def _fmt_mg(v) -> str:
    n = _num(v)
    if n is None:
        return ""
    return f"{int(n)} mg" if float(n).is_integer() else f"{n:g} mg"


def build_flex_model(cached: dict) -> dict:
    """국가별 '최저 수재' 1행(같은 제형 안에서 조정가가 가장 낮은 집계 대상 행) → 서식 칸 값.

    집계 제외 행(a8_aggregate_eligible=False)은 값 칸을 비우고 사유만 남긴다 — 틀린 단위가를 신청서에 싣지 않는다.
    """
    cols = []
    for c in FLEX_ORDER:
        rows = cached.get(c) or []
        priced = [r for r in rows if r.get("local_price") is not None and r.get("adjusted_price_krw") is not None]
        eligible = [r for r in priced if r.get("a8_aggregate_eligible", True)]
        col = {"country": c, "country_kr": COUNTRY_KR.get(c, c), "form_source": FLEX_FORM_SOURCE[c],
               "status": "ok", "note": ""}
        if not eligible:
            col["status"] = "excluded" if priced else "missing"
            col["note"] = (EXCLUDE_REASON_KR.get(priced[0].get("a8_exclude_reason") or "pack_count_unverified",
                                                 priced[0].get("a8_exclude_reason") or "")
                           if priced else "수집된 가격 없음")
            if priced:
                col["source"] = priced[0].get("source_label") or ""
            cols.append(col)
            continue

        def _adj(r):
            return _num(r.get("adjusted_price_krw_normalized")) or _num(r.get("adjusted_price_krw")) or float("inf")
        r = min(eligible, key=_adj)
        meta = _raw_meta(r)
        pack = int(r.get("pack_count") or 1)
        per_unit = _num(r.get("per_unit_local"))
        local_price = _num(r.get("local_price"))
        if per_unit is None and local_price is not None:
            per_unit = local_price / pack
        fx = per_unit_rate(r.get("currency"), _num(r.get("exchange_rate")))
        conv = _num(r.get("krw_converted"))
        if conv is None and per_unit is not None and fx is not None:
            conv = per_unit * fx
        word = unit_word(r)
        strength = _fmt_mg(r.get("unit_strength_mg")) or (r.get("dosage_strength") or "")
        tier = TIER_KR.get(meta.get("price_tier") or "", "")
        col.update({
            "source": r.get("source_label") or "",
            "tier": tier, "vat": VAT_KR.get(meta.get("vat_status") or "", ""),
            "product": " ".join(x for x in [r.get("product_name") or "", r.get("dosage_strength") or ""] if x).strip(),
            "package": r.get("package_unit") or "",
            "currency": r.get("currency") or "", "sym": CUR_SYM.get(r.get("currency") or "", ""),
            "local_price": local_price, "pack": pack, "unit": f"1{word}" + (f" ({strength})" if strength else ""),
            "unit_word": word, "per_unit": per_unit, "fx": fx,
            "fx_from": r.get("exchange_rate_from") or "", "fx_to": r.get("exchange_rate_to") or "",
            "krw_converted": conv,
            "adjusted": _num(r.get("adjusted_price_krw_normalized")) or _num(r.get("adjusted_price_krw")),
            "source_url": r.get("source_url") or "", "searched_at": (r.get("searched_at") or "")[:19],
            "observed_at": (meta.get("observed_at") or "")[:19], "effective_date": meta.get("effective_date") or "",
            "tier_original": meta.get("price_tier_original") or "", "evidence_ref": meta.get("evidence_ref") or "",
            "dose_norm_note": r.get("dose_norm_note") or "",
        })
        cols.append(col)
    ok = [c for c in cols if c["status"] == "ok" and c.get("adjusted") is not None]
    top = max(ok, key=lambda c: c["adjusted"]) if ok else None
    period = next(((c["fx_from"], c["fx_to"]) for c in ok if c.get("fx_from")), ("", ""))
    return {"cols": cols, "max": top, "fx_period": period}


def _period_text(p) -> str:
    def f(x):
        x = str(x or "")
        return f"{x[:4]}.{x[4:6]}" if len(x) >= 6 and x[:6].isdigit() else x
    a, b = p
    return f"{f(a)}~{f(b)}" if a or b else ""


def export_flex_xlsx(query: str, model: dict, product_label: str = "", applied=None) -> bytes:
    """공단 약가유연계약 신청서 A8 비교표 서식 + '자료원 증빙' 시트."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    THIN = Side(border_style="thin", color="808080")
    BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
    CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
    LEFT = Alignment(horizontal="left", vertical="center", wrap_text=True)
    LBL = PatternFill("solid", fgColor="F2F2F2")
    cols = model["cols"]
    n = len(cols)
    wb = Workbook()
    ws = wb.active
    ws.title = "A8 비교표"

    def money(v):
        return round(v) if v is not None else None

    ws.cell(row=1, column=1, value=f"○ 제품명: {product_label or query}").font = Font(bold=True, size=12)
    amt = _num(applied)
    ws.cell(row=2, column=1, value=f"○ 약가유연계약 신청금액(원): {f'{round(amt):,}원' if amt else '          원'}").font = Font(bold=True, size=12)

    top = 4
    # 헤더(국가) — 라벨은 A:B 두 칸 병합
    ws.merge_cells(start_row=top, start_column=1, end_row=top, end_column=2)
    ws.cell(row=top, column=1, value="국가")
    for j, c in enumerate(cols, 3):
        ws.cell(row=top, column=j, value=c["country_kr"])
    labels = [
        ("자료원¹⁾", None), ("최저수재\n제품명", None),
        ("최저가", "수재가²⁾"), ("최저가", "단위"), ("최저가", "단위가"),
        ("환율³⁾", None), ("환율적용\n환산가(원)", None), ("환율적용\n조정가(원)", None),
    ]
    for i, (a, b) in enumerate(labels, top + 1):
        if b is None:
            ws.merge_cells(start_row=i, start_column=1, end_row=i, end_column=2)
            ws.cell(row=i, column=1, value=a.replace("\\n", "\n"))
        else:
            ws.cell(row=i, column=2, value=b)
    ws.merge_cells(start_row=top + 3, start_column=1, end_row=top + 5, end_column=1)
    ws.cell(row=top + 3, column=1, value="최\n저\n가")

    for j, c in enumerate(cols, 3):
        r0 = top + 1
        if c["status"] != "ok":
            ws.cell(row=r0, column=j, value=(c.get("source") or c["form_source"]))
            ws.merge_cells(start_row=r0 + 1, start_column=j, end_row=r0 + 7, end_column=j)
            ws.cell(row=r0 + 1, column=j, value=("집계 제외\n" if c["status"] == "excluded" else "미수집\n") + c["note"])
            continue
        src = c["source"] + (f"\n({c['tier']}{', ' + c['vat'] if c['vat'] else ''})" if c.get("tier") else "")
        ws.cell(row=r0, column=j, value=src)
        ws.cell(row=r0 + 1, column=j, value=c["product"] + (f"\n{c['package']}" if c["package"] else ""))
        ws.cell(row=r0 + 2, column=j, value=f"{c['local_price']:,.2f} {c['currency']}\n({c['pack']}{c['unit_word']})")
        ws.cell(row=r0 + 3, column=j, value=c["unit"])
        ws.cell(row=r0 + 4, column=j, value=f"{c['per_unit']:,.2f} {c['currency']}" if c["per_unit"] is not None else "-")
        ws.cell(row=r0 + 5, column=j, value=f"{c['fx']:,.4f}" if c["fx"] is not None else "-")
        cv = ws.cell(row=r0 + 6, column=j, value=money(c["krw_converted"]))
        av = ws.cell(row=r0 + 7, column=j, value=money(c["adjusted"]))
        cv.number_format = av.number_format = "#,##0"
    mrow = top + 9
    ws.merge_cells(start_row=mrow, start_column=1, end_row=mrow, end_column=2)
    ws.cell(row=mrow, column=1, value="A8 국가\n조정최고가(원)")
    ws.merge_cells(start_row=mrow, start_column=3, end_row=mrow, end_column=2 + n)
    t = model["max"]
    ws.cell(row=mrow, column=3, value=(f"{round(t['adjusted']):,}원 ({t['country_kr']})" if t else "산출 불가 (조정가 있는 국가 없음)"))

    for row in ws.iter_rows(min_row=top, max_row=mrow, min_col=1, max_col=2 + n):
        for cell in row:
            cell.border = BORDER
            cell.alignment = CENTER
            if cell.column <= 2 or cell.row == top:
                cell.fill = LBL
                cell.font = Font(bold=True, size=10)
            else:
                cell.font = Font(size=10)
    period = _period_text(model["fx_period"])
    notes = [
        "1) 자료원 증빙: 국가별 약가 자료원 스크린샷·스캔본 등 제출 — 검색 날짜 등 입증 필요 ('자료원 증빙' 시트에 원문 URL·검색일·증빙 위치).",
        "2) 수재가: 자료원에 수재된 포장단위 가격(원문 가격단계 그대로). 단위가 = 수재가 ÷ 포장 내 최소단위 수.",
        f"3) 환율: 하나은행 매매기준율 36개월 평균{(' (' + period + ')') if period else ''}, 통화 1단위당 원화.",
        "※ 조정가는 공단 A8 조정 산식(공장도율·VAT·유통마진) 적용 최소단위당 원화. 비공개 할인·환급은 반영되지 않음.",
    ]
    diff = [c for c in cols if c["status"] == "ok" and c.get("source") and c["form_source"].split(" (")[0].split(",")[0].lower() not in c["source"].lower()]
    if diff:
        notes.append("※ 서식 기준과 다른 자료원: " + " · ".join(f"{c['country_kr']}({c['source']} — 서식 기준 {c['form_source']})" for c in diff))
    for k, line in enumerate(notes, mrow + 2):
        ws.cell(row=k, column=1, value=line).alignment = LEFT
        ws.merge_cells(start_row=k, start_column=1, end_row=k, end_column=2 + n)
    ws.column_dimensions["A"].width = 6
    ws.column_dimensions["B"].width = 14
    from openpyxl.utils import get_column_letter
    for j in range(3, 3 + n):
        ws.column_dimensions[get_column_letter(j)].width = 20
    for r_ in range(top + 1, top + 3):
        ws.row_dimensions[r_].height = 48
    ws.row_dimensions[top + 3].height = 34

    ev = wb.create_sheet("자료원 증빙")
    head = ["국가", "서식 기준 자료원", "실제 자료원", "원문 가격단계", "VAT", "원문 URL", "검색(관찰) 일시", "가격 적용일", "증빙 위치", "비고"]
    for j, h in enumerate(head, 1):
        cell = ev.cell(row=1, column=j, value=h)
        cell.font = Font(bold=True)
        cell.fill = LBL
        cell.border = BORDER
    for i, c in enumerate(cols, 2):
        vals = [c["country_kr"], c["form_source"], c.get("source") or "-", c.get("tier_original") or c.get("tier") or "-",
                c.get("vat") or "-", c.get("source_url") or "-", c.get("observed_at") or c.get("searched_at") or "-",
                c.get("effective_date") or "-", c.get("evidence_ref") or "-",
                c["note"] if c["status"] != "ok" else (c.get("dose_norm_note") or "")]
        for j, v in enumerate(vals, 1):
            cell = ev.cell(row=i, column=j, value=v)
            cell.border = BORDER
            cell.alignment = LEFT
            if j == 6 and str(v).startswith("http"):
                cell.hyperlink = v
                cell.font = Font(color="0563C1", underline="single")
    for j, w in enumerate([10, 26, 30, 22, 11, 50, 20, 12, 40, 30], 1):
        ev.column_dimensions[get_column_letter(j)].width = w
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def generate(query: str, fmt: str, cached: dict,
             applied_display=None, applied_actual=None, product_label: str = "") -> tuple[bytes, str, str]:
    """리포트 생성 → (bytes, mimetype, filename). fmt: xlsx | hwpx | flex(약가유연계약 서식 xlsx)."""
    if fmt == "flex":
        fm = build_flex_model(cached)
        if not any(c["status"] == "ok" for c in fm["cols"]):
            raise ValueError("서식에 넣을 가격 데이터가 없습니다.")
        stamp = date.today().isoformat()
        applied = applied_display if _num(applied_display) else applied_actual
        return (export_flex_xlsx(query, fm, product_label, applied),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                f"{query}_A8_약가유연계약서식_{stamp}.xlsx")
    model = build_report_model(cached, applied_display, applied_actual)
    if not model["cols"]:
        raise ValueError("리포트 생성할 가격 데이터가 없습니다.")
    stamp = date.today().isoformat()
    if fmt == "hwpx":
        return (export_hwpx(query, model),
                "application/octet-stream", f"{query}_A8_report_{stamp}.hwpx")
    return (export_xlsx(query, model),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            f"{query}_A8_report_{stamp}.xlsx")
