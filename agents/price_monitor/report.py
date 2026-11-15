"""모니터링 회차 메일(HTML·텍스트)과 엑셀 첨부. 순수 함수 — 발송하지 않는다."""
from __future__ import annotations

import html
import io
from datetime import datetime

from agents.price_monitor.schedule import KST

KIND_KO = {
    "first": "첫 확인", "same": "변동 없음", "price": "가격 변동", "basis": "기준·출처 변경",
    "packs": "포장 구성 변경", "now_priced": "새로 확인", "lost": "미확인 전환", "unpriced": "가격 미확인",
}
VERDICT_KO = {
    "not_marketed": "미허가·미출시 확인", "not_listed": "가격·급여 목록 미등재", "no_public_price": "공개 가격 없음(판매 확인)",
    "access_blocked": "접속 차단 — 검증 불가", "unresolved": "판단 불가",
}
STATUS_KO = {"priced": "가격 확인", "unconfirmed": "미확정", "failed": "수집 실패", "pending": "미처리"}
COUNTRY_KO = {"JP": "일본", "IT": "이탈리아", "FR": "프랑스", "CH": "스위스", "UK": "영국", "DE": "독일", "CA": "캐나다", "US": "미국"}
NOTES = [
    "비교 기준은 현지 통화 원가격(같은 출처·가격 단계)입니다. 원화 조정가는 환율 영향이 섞여 가격 변동 판정에 쓰지 않습니다.",
    "'기준·출처 변경'은 가격 단계나 출처가 바뀐 것이라 인하·인상으로 읽지 않습니다.",
    "공개 표시가 기준이며 실거래가·비공개 할인(위험분담 등)은 반영하지 않습니다. 심평원·공단 A8 조정가 산정과는 환율 기준일·산식이 다를 수 있습니다.",
]


def _kst(iso: str | None) -> str:
    if not iso:
        return "-"
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(KST).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso


def _money(v, cur="") -> str:
    if v is None:
        return "-"
    if isinstance(v, list):
        return " / ".join(_money(x, cur) for x in v)
    return f"{float(v):,.2f} {cur}".strip()


def _pct(p) -> str:
    return "-" if p is None else f"{p * 100:+.1f}%"


def subject(project_name: str, run: dict, diff: dict) -> str:
    d = _kst(run.get("due_at"))[:10]
    n = diff.get("highlight_count") or 0
    tail = "첫 회차(기준 가격)" if not diff.get("has_previous") else (f"변경 {n}건" if n else "변경 없음")
    return f"[해외약가 모니터링] {project_name} {d} — {tail}"


def change_rows(diff: dict) -> list[dict]:
    """하이라이트 대상 행(메일·화면 공통)."""
    out = []
    for it in diff.get("items", []):
        for cc, c in it["countries"].items():
            if c["kind"] in ("price", "basis", "packs"):
                for ch in c.get("changes") or [{}]:
                    out.append({"drug": it["label"], "cc": cc, "kind": c["kind"], "label": ch.get("label"),
                                "old": ch.get("old"), "new": ch.get("new"), "pct": ch.get("pct"), "currency": ch.get("currency", "")})
            elif c["kind"] in ("now_priced", "lost"):
                out.append({"drug": it["label"], "cc": cc, "kind": c["kind"], "label": VERDICT_KO.get(c.get("verdict") or "", c.get("reason") or ""),
                            "old": None, "new": None, "pct": None, "currency": ""})
    return out


def unpriced_rows(snapshot: dict) -> list[dict]:
    out = []
    for it in snapshot.get("items", []):
        for cc, c in (it.get("countries") or {}).items():
            if c.get("status") != "priced":
                out.append({"drug": it.get("label") or it["query"], "cc": cc, "status": c.get("status"),
                            "verdict": c.get("verdict"), "reason": c.get("reason"), "sources": c.get("verdict_sources") or []})
    return out


def build_html(project: dict, run: dict, snapshot: dict, diff: dict, dashboard_url: str) -> tuple[str, str]:
    e = html.escape
    t = diff.get("totals") or {}
    rows = change_rows(diff)
    unp = unpriced_rows(snapshot)
    n_items = len(snapshot.get("items", []))
    parts = [
        '<div style="font-family:-apple-system,Segoe UI,Malgun Gothic,sans-serif;font-size:14px;color:#1f2937;max-width:880px">',
        f'<h2 style="margin:0 0 4px;font-size:18px">{e(project["name"])} · 해외약가 모니터링</h2>',
        f'<p style="margin:0 0 12px;color:#6b7280">회차 {e(_kst(run.get("due_at")))} KST · 약제 {n_items}개 · '
        f'{e(", ".join(COUNTRY_KO.get(c, c) for c in run.get("countries") or []))}'
        + (f' · 직전 회차 {e(_kst(diff.get("previous_due_at")))}' if diff.get("previous_due_at") else "") + "</p>",
    ]
    if not diff.get("has_previous"):
        parts.append('<p style="background:#f3f4f6;padding:10px;border-radius:8px">첫 회차입니다. 비교할 이전 회차가 없어 이번 가격을 기준으로 저장했고, 다음 회차부터 변경을 표시합니다. 전체 가격은 첨부 엑셀에 있습니다.</p>')
    else:
        pills = [("가격 변동", t.get("price", 0), "#fde2e1"), ("기준·출처 변경", t.get("basis", 0), "#fff4d6"),
                 ("포장 구성", t.get("packs", 0), "#eef2ff"), ("새로 확인", t.get("now_priced", 0), "#e7f6ec"),
                 ("미확인 전환", t.get("lost", 0), "#e5e7eb"), ("환율 영향만", t.get("fx", 0), "#f3f4f6")]
        parts.append("<p>" + " ".join(f'<span style="display:inline-block;background:{bg};border-radius:12px;padding:3px 10px;margin:0 4px 4px 0">{e(k)} <b>{v}</b></span>' for k, v, bg in pills) + "</p>")
        if rows:
            parts.append('<h3 style="font-size:15px;margin:16px 0 6px">변경된 항목</h3>')
            parts.append('<table style="border-collapse:collapse;width:100%;font-size:13px"><tr style="background:#1f3a5f;color:#fff">'
                         '<th style="padding:6px;text-align:left">약제</th><th style="padding:6px">국가</th><th style="padding:6px">유형</th>'
                         '<th style="padding:6px;text-align:left">포장</th><th style="padding:6px;text-align:right">이전</th>'
                         '<th style="padding:6px;text-align:right">현재</th><th style="padding:6px;text-align:right">변동</th></tr>')
            for r in rows:
                if r["kind"] == "price":
                    bg = "#e8f0fe" if (r["pct"] or 0) < 0 else "#fde2e1"
                else:
                    bg = {"basis": "#fff4d6", "packs": "#eef2ff", "now_priced": "#e7f6ec", "lost": "#e5e7eb"}.get(r["kind"], "#fff")
                parts.append(f'<tr style="background:{bg}"><td style="padding:6px;border-bottom:1px solid #e5e7eb">{e(r["drug"])}</td>'
                             f'<td style="padding:6px;text-align:center;border-bottom:1px solid #e5e7eb">{e(COUNTRY_KO.get(r["cc"], r["cc"]))}</td>'
                             f'<td style="padding:6px;text-align:center;border-bottom:1px solid #e5e7eb">{e(KIND_KO[r["kind"]])}</td>'
                             f'<td style="padding:6px;border-bottom:1px solid #e5e7eb">{e(r["label"] or "")}</td>'
                             f'<td style="padding:6px;text-align:right;border-bottom:1px solid #e5e7eb">{e(_money(r["old"], r["currency"]))}</td>'
                             f'<td style="padding:6px;text-align:right;border-bottom:1px solid #e5e7eb"><b>{e(_money(r["new"], r["currency"]))}</b></td>'
                             f'<td style="padding:6px;text-align:right;border-bottom:1px solid #e5e7eb">{e(_pct(r["pct"]) if r["kind"] == "price" else "")}</td></tr>')
            parts.append("</table>")
        else:
            parts.append('<p style="background:#f3f4f6;padding:10px;border-radius:8px">직전 회차 대비 가격 변경이 없습니다.</p>')
        same = t.get("same", 0)
        if same:
            parts.append(f'<p style="color:#6b7280">변동 없음 {same}건(약제×국가)' + (f" · 그중 환율 영향으로 원화만 바뀐 {t.get('fx', 0)}건" if t.get("fx") else "") + ".</p>")
    if unp:
        parts.append('<h3 style="font-size:15px;margin:16px 0 6px">가격 미확인 국가 — 검증 결과</h3>'
                     '<table style="border-collapse:collapse;width:100%;font-size:13px"><tr style="background:#374151;color:#fff">'
                     '<th style="padding:6px;text-align:left">약제</th><th style="padding:6px">국가</th><th style="padding:6px;text-align:left">판정</th>'
                     '<th style="padding:6px;text-align:left">확인한 출처</th></tr>')
        for u in unp:
            src = "<br>".join(f'<a href="{e(s["url"])}">{e(s["url"][:60])}</a> — {e(s["finding"][:120])}' for s in u["sources"][:3]) or e((u["reason"] or "")[:200])
            parts.append(f'<tr><td style="padding:6px;border-bottom:1px solid #e5e7eb">{e(u["drug"])}</td>'
                         f'<td style="padding:6px;text-align:center;border-bottom:1px solid #e5e7eb">{e(COUNTRY_KO.get(u["cc"], u["cc"]))}</td>'
                         f'<td style="padding:6px;border-bottom:1px solid #e5e7eb">{e(VERDICT_KO.get(u["verdict"] or "", STATUS_KO.get(u["status"], u["status"] or "")))}</td>'
                         f'<td style="padding:6px;border-bottom:1px solid #e5e7eb;font-size:12px">{src}</td></tr>')
        parts.append("</table>")
    parts.append(f'<p style="margin-top:14px"><a href="{e(dashboard_url)}">대시보드에서 회차 보기</a> · 전체 가격·이전 값·판정 근거는 첨부 엑셀에 있습니다.</p>')
    parts.append('<p style="font-size:12px;color:#6b7280;margin-top:12px">' + "<br>".join("· " + e(n) for n in NOTES) + "</p></div>")
    body_html = "".join(parts)
    text = [f"{project['name']} 해외약가 모니터링 {_kst(run.get('due_at'))} KST", f"약제 {n_items}개"]
    if diff.get("has_previous"):
        text.append(f"가격 변동 {t.get('price', 0)} · 기준 변경 {t.get('basis', 0)} · 포장 {t.get('packs', 0)} · 새로 확인 {t.get('now_priced', 0)} · 미확인 전환 {t.get('lost', 0)}")
        for r in rows[:40]:
            text.append(f"- {r['drug']} {r['cc']} {KIND_KO[r['kind']]}: {_money(r['old'], r['currency'])} → {_money(r['new'], r['currency'])} {_pct(r['pct']) if r['kind'] == 'price' else ''}")
    else:
        text.append("첫 회차 — 기준 가격 저장")
    text.append(dashboard_url)
    return body_html, "\n".join(text)


def build_xlsx(project: dict, run: dict, snapshot: dict, diff: dict, prev_snapshot: dict | None) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    font = "맑은 고딕"
    hfill, hfont = PatternFill("solid", fgColor="1F3A5F"), Font(name=font, bold=True, color="FFFFFF", size=10)
    fills = {"price_down": "E8F0FE", "price_up": "FDE2E1", "basis": "FFF4D6", "packs": "EEF2FF", "now_priced": "E7F6EC", "lost": "E5E7EB"}

    def sheet(ws, header, widths):
        ws.append(header)
        for c in ws[1]:
            c.font, c.fill = hfont, hfill
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.freeze_panes = "A2"

    wb = Workbook()
    ws = wb.active
    ws.title = "요약"
    t = diff.get("totals") or {}
    for row in [["항목", "내용"], ["프로젝트", project["name"]], ["회차", _kst(run.get("due_at")) + " KST"],
                ["직전 회차", _kst(diff.get("previous_due_at")) if diff.get("previous_due_at") else "없음(첫 회차)"],
                ["대상 약제", len(snapshot.get("items", []))], ["국가", ", ".join(run.get("countries") or [])],
                ["가격 변동", t.get("price", 0)], ["기준·출처 변경", t.get("basis", 0)], ["포장 구성 변경", t.get("packs", 0)],
                ["새로 확인", t.get("now_priced", 0)], ["미확인 전환", t.get("lost", 0)], ["변동 없음", t.get("same", 0)],
                ["환율 영향만", t.get("fx", 0)], ["가격 미확인(이번)", t.get("unpriced", 0) + t.get("lost", 0)]]:
        ws.append(row)
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 20, 60

    w2 = wb.create_sheet("변경 내역")
    sheet(w2, ["약제", "국가", "유형", "포장", "이전(현지통화)", "현재(현지통화)", "변동률", "통화"], [24, 8, 14, 46, 18, 18, 10, 8])
    for r in change_rows(diff):
        w2.append([r["drug"], r["cc"], KIND_KO[r["kind"]], r["label"], _money(r["old"]), _money(r["new"]),
                   _pct(r["pct"]) if r["kind"] == "price" else "", r["currency"]])
        key = ("price_down" if (r["pct"] or 0) < 0 else "price_up") if r["kind"] == "price" else r["kind"]
        for c in w2[w2.max_row]:
            c.fill = PatternFill("solid", fgColor=fills.get(key, "FFFFFF"))

    w3 = wb.create_sheet("전체 가격")
    sheet(w3, ["약제", "국가", "상태", "변경 유형", "제품", "포장", "포장 수", "현지가", "통화", "이전 현지가", "원화 조정가", "출처", "출처 URL", "조회 시각"],
          [22, 6, 10, 14, 30, 34, 8, 14, 7, 14, 14, 30, 40, 18])
    prev_items = {it["query"]: it for it in (prev_snapshot or {}).get("items", [])}
    diff_items = {it["query"]: it for it in diff.get("items", [])}
    from agents.price_monitor.diff import row_key
    for it in snapshot.get("items", []):
        for cc, c in (it.get("countries") or {}).items():
            kind = ((diff_items.get(it["query"]) or {}).get("countries", {}).get(cc) or {}).get("kind")
            prev_rows = ((prev_items.get(it["query"]) or {}).get("countries", {}).get(cc) or {}).get("rows") or []
            prev_by = {}
            for pr in prev_rows:
                prev_by.setdefault(row_key(pr), []).append(pr.get("local_price"))
            rows = c.get("rows") or []
            if not rows:
                w3.append([it.get("label"), cc, STATUS_KO.get(c.get("status"), c.get("status")), KIND_KO.get(kind, ""), "", "", "", "", "", "", "", "",
                           "", ""])
                continue
            for r in rows:
                w3.append([it.get("label"), cc, "가격 확인", KIND_KO.get(kind, ""), r.get("product_name"), r.get("package_unit"), r.get("pack_count"),
                           r.get("local_price"), r.get("currency"), ", ".join(str(x) for x in prev_by.get(row_key(r), [])),
                           r.get("adjusted_price_krw"), r.get("source_label"), r.get("source_url"), _kst(r.get("searched_at"))])

    w4 = wb.create_sheet("미확인 판정")
    sheet(w4, ["약제", "국가", "상태", "판정", "사유", "확인한 출처 URL", "확인 내용"], [22, 6, 10, 22, 50, 50, 50])
    for u in unpriced_rows(snapshot):
        srcs = u["sources"] or [{}]
        for s in srcs:
            w4.append([u["drug"], u["cc"], STATUS_KO.get(u["status"], u["status"]), VERDICT_KO.get(u["verdict"] or "", "판정 없음"),
                       u["reason"], s.get("url", ""), s.get("finding", "")])

    w5 = wb.create_sheet("방법·주의")
    w5.append(["항목", "내용"])
    w5.append(["수집", "회차마다 약제별 해외약가 요청을 만들어 국가별 공식 출처에서 새로 조사(스위스 SL·독일 Rote Liste 는 서버 수집, 그 외 브라우저)."])
    w5.append(["미확인 검증", "가격이 나오지 않은 국가는 가격 출처 외 독립 출처(허가·출시·급여 목록)로 '실제로 가격이 없는지' 확인한 판정을 남긴다. 접속 차단은 회차 안에서 재시도."])
    for n in NOTES:
        w5.append(["주의", n])
    w5.column_dimensions["A"].width, w5.column_dimensions["B"].width = 14, 120
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
