"""허가초과 항암요법 xlsx('사전신청요법(용법용량 포함) 및 불승인요법_YYMMDD.xlsx') 파서.

- '인정되고 있는 허가초과 항암요법…' 시트: 암질환심의위원회에서 **신청기관에 국한하여** 인정된 요법(요법코드 단위).
- '불승인 요법' 시트: 불승인 요법과 사유.
시트·헤더 이름은 공백/줄바꿈 차이를 무시하고 찾는다. 헤더를 못 찾으면 예외.
"""
from __future__ import annotations

import re
from pathlib import Path

from .onco_parser import line_code, setting_code


def _norm(s) -> str:
    return re.sub(r"\s+", "", str(s or ""))


def _find_header(ws, must: tuple[str, ...]):
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        cells = [_norm(c) for c in row]
        if all(any(m in c for c in cells) for m in must):
            return i, cells
        if i > 30:
            break
    raise ValueError(f"header {must} not found in sheet {ws.title}")


def parse(xlsx: Path) -> dict:
    import openpyxl
    wb = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
    approved, rejected = [], []
    for ws in wb.worksheets:
        t = _norm(ws.title)
        if "인정되고있는" in t:
            hi, hdr = _find_header(ws, ("요법코드", "항암화학요법"))
            col = {name: hdr.index(name) for name in hdr if name}
            def g(row, *names):
                for n in names:
                    for k, i in col.items():
                        if k.startswith(n):
                            v = row[i]
                            return "" if v is None else str(v).strip()
                return ""
            for row in list(ws.iter_rows(values_only=True))[hi + 1:]:
                code = g(row, "요법코드")
                if not re.fullmatch(r"\d{3,6}", code or ""):
                    continue
                lraw = g(row, "투여단계")
                sraw = g(row, "투여요법")
                approved.append({
                    "regimen_code": code, "cancer": g(row, "암종"), "subcancer": g(row, "세부암종"),
                    "regimen_text": g(row, "항암화학요법"), "target_text": g(row, "투여대상"),
                    "line_raw": lraw, "line_code": line_code(lraw), "setting_raw": sraw,
                    "setting_code": setting_code(sraw), "cost_note": g(row, "급여상세사항"),
                    "dosing_note": g(row, "참고사항"),
                })
        elif "불승인" in t:
            hi, hdr = _find_header(ws, ("항암화학요법", "불인정사유"))
            ci = {k: i for i, k in enumerate(hdr) if k}
            def gi(row, key):
                for k, i in ci.items():
                    if k.startswith(key):
                        v = row[i]
                        return "" if v is None else str(v).strip()
                return ""
            n = 0
            for row in list(ws.iter_rows(values_only=True))[hi + 1:]:
                reg = gi(row, "항암화학요법")
                if not reg:
                    continue
                n += 1
                rejected.append({"seq": n, "cancer": gi(row, "암종"), "regimen_text": reg,
                                 "reason": gi(row, "불인정사유"), "note": gi(row, "비고")})
    if not approved:
        raise ValueError("허가초과 인정 요법 0건 — 시트 구조 변경 의심")
    return {"approved": approved, "rejected": rejected}
