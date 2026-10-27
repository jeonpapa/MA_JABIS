"""프랑스 관보(Journal officiel) 약가 공시 파서 — 공장도가(PFHT) 자료원 (2026-10-27).

별첨5 의 프랑스 기준은 BdM_IT(Assurance Maladie) 공장도가 × 0.77 이다. BdM_IT 는 2026-10 중순부터 전 세계에서 응답하지 않아
같은 CEPS 공식 가격이 실리는 관보 공시를 읽는다(Légifrance, 브라우저로 열 것 — 스크립트 요청은 Cloudflare 403).

두 형식:
- 약국(ville) 「Avis relatif aux prix de spécialités pharmaceutiques」 — N° CIP | Présentation | PFHT | PPTTC [| Date d'effet]
- 병원(liste en sus, L.162-16-6) — Code UCD | Libellé | Laboratoire | Tarif de responsabilité HT par UCD | Prix limite de vente HT

입력은 Légifrance 본문 화면 텍스트(innerText). 값은 화면 그대로 — 임의 보정 없음.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

_CODE = re.compile(r"(3400[89](?:[ \u00a0\u202f]?\d){8})")
_EUR2 = r"\d{1,3}(?:[ \u00a0\u202f.]\d{3})*,\d{2}"
_EUR3 = r"\d{1,3}(?:[ \u00a0\u202f.]\d{3})*,\d{3}"
_DATE = re.compile(r"(\d{2})/(\d{2})/(\d{4})")
_JORF = re.compile(r"JORF\s*n°\s*(\d+)\s+du\s+(\d{1,2})(?:er)?\s+([a-zéû]+)\s+(\d{4})", re.I)
_NOR = re.compile(r"NOR\s*:\s*([A-Z0-9]+)")
_MONTHS = {"janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6, "juillet": 7,
           "août": 8, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12}
_ORDINAL = {"premier": 1, "deuxième": 2, "troisième": 3, "quatrième": 4, "cinquième": 5, "sixième": 6, "septième": 7,
            "huitième": 8, "neuvième": 9, "dixième": 10, "onzième": 11, "douzième": 12, "treizième": 13,
            "quatorzième": 14, "quinzième": 15}


def fr_to_float(txt: str) -> float:
    return float(re.sub(r"[ \u00a0\u202f.]", "", txt).replace(",", "."))


def _digits(code: str) -> str:
    return re.sub(r"\D", "", code)


def publication(text: str) -> dict:
    """JORF 번호·발행일·NOR. 발행일은 'JORF n°0134 du 10 juin 2026'."""
    out = {"jorf": None, "publication_date": None, "nor": None}
    m = _JORF.search(text or "")
    if m and _MONTHS.get(m.group(3).lower()):
        out["jorf"] = m.group(0)
        out["publication_date"] = date(int(m.group(4)), _MONTHS[m.group(3).lower()], int(m.group(2))).isoformat()
    n = _NOR.search(text or "")
    if n:
        out["nor"] = n.group(1)
    return out


def effective_date(text: str, publication_date: str | None) -> str | None:
    """'à compter du 15 juillet 2024' 또는 '... du quinzième jour suivant la publication' → YYYY-MM-DD. 못 읽으면 None."""
    t = (text or "").lower()
    m = re.search(r"à compter du (\d{1,2})(?:er)?\s+([a-zéû]+)\s+(\d{4})", t)
    if m and _MONTHS.get(m.group(2)):
        return date(int(m.group(3)), _MONTHS[m.group(2)], int(m.group(1))).isoformat()
    m = re.search(r"à compter du ([a-zéè]+) jour suivant la publication", t)
    if m and publication_date and m.group(1) in _ORDINAL:
        return (date.fromisoformat(publication_date) + timedelta(days=_ORDINAL[m.group(1)])).isoformat()
    return None


def pack_count(presentation: str) -> int | None:
    """'(B/3x30)' → 90, '(B/28)' → 28, '2 flacons de 45 mg' → 2, '1 flacon' → 1. 모르면 None(추측하지 않는다)."""
    p = presentation or ""
    m = re.search(r"B/(\d+)\s*x\s*(\d+)", p, re.I)
    if m:
        return int(m.group(1)) * int(m.group(2))
    m = re.search(r"B/(\d+)", p, re.I)
    if m:
        return int(m.group(1))
    m = re.search(r"\b(\d+)\s+flacons?\b", p, re.I)
    if m:
        return int(m.group(1))
    return None


def parse_notice(text: str) -> dict:
    """→ {kind: 'cip'|'ucd'|None, rows: [...], publication: {...}, effective_date}.

    cip 행: {code, presentation, pfht, ppttc, date_effet}
    ucd 행: {code, libelle, tarif_ht, plv_ht}
    """
    text = text or ""
    pub = publication(text)
    eff = effective_date(text, pub["publication_date"])
    kind = "ucd" if re.search(r"Code\s+UCD", text, re.I) else ("cip" if re.search(r"PFHT", text) else None)
    rows = []
    starts = [(m.start(), m.group(1)) for m in _CODE.finditer(text)]
    for i, (pos, code) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(text)
        seg = re.sub(r"\s+", " ", text[pos + len(code):end]).strip()
        if kind == "cip":
            amounts = list(re.finditer(rf"({_EUR2})\s*€", seg))
            if len(amounts) < 2:
                continue
            d = _DATE.search(seg[amounts[1].end():])
            rows.append({
                "code": _digits(code), "presentation": seg[:amounts[0].start()].strip(),
                "pfht": fr_to_float(amounts[0].group(1)), "ppttc": fr_to_float(amounts[1].group(1)),
                "date_effet": f"{d.group(3)}-{d.group(2)}-{d.group(1)}" if d else None,
            })
        elif kind == "ucd":
            nums = list(re.finditer(rf"(?<![\d,])({_EUR3})(?!\d)", seg))
            if not nums:
                continue
            rows.append({
                "code": _digits(code), "libelle": seg[:nums[0].start()].strip(),
                "tarif_ht": fr_to_float(nums[0].group(1)),
                "plv_ht": fr_to_float(nums[1].group(1)) if len(nums) > 1 else None,
            })
    return {"kind": kind, "rows": rows, "publication": pub, "effective_date": eff}
