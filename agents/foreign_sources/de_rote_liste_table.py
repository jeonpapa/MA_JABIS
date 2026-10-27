"""독일 Rote Liste 상세 페이지 'PACKUNGSANGABEN' 표 파서 (DocCheck 로그인 후 화면 텍스트).

표 열: [포장] [N1/N2/N3] PZN | AVP (EB)/FB | AVP/UVP. 값 예: "EB 2.456,22 €" / "4.434,18 €".
기준 가격은 **AVP/UVP**(2026-10-26 사용자 결정). EB/FB 는 참고로만 보존한다. 한 열만 있으면 추측하지 않는다.
"""
from __future__ import annotations

import re

_DE_NUM = r"\d{1,3}(?:\.\d{3})*,\d{2}"
_PACK_ROW = re.compile(
    rf"(?P<pack>\d+\s*[A-Za-zÄÖÜäöü\.\- ]+?(?:\d[\d,\.]*\s*(?:mg|ml|µg|g)(?:/\s*(?:\d+\s*)?m[lL])?(?:\s*KIT)?)?)\s+"
    rf"(?:(?P<n>N[123])\s+)?(?P<pzn>\d{{8}})\s+"
    rf"(?P<c1>(?:(?:EB|FB)\s+)?{_DE_NUM}\s*€)?\s*(?P<c2>(?:(?:EB|FB)\s+)?{_DE_NUM}\s*€)?",
)
_PREISSTAND = re.compile(r"Preisstand:\s*(\d{1,2})\.\s*([A-Za-zäÄ]+)\s+(\d{4})")
_MONTHS = {"januar": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4, "mai": 5, "juni": 6, "juli": 7,
           "august": 8, "september": 9, "oktober": 10, "november": 11, "dezember": 12}


def de_to_float(txt: str) -> float:
    return float(re.search(_DE_NUM, txt).group(0).replace(".", "").replace(",", "."))


def parse_packungsangaben(text: str) -> list[dict]:
    """→ [{pack, n, pzn, avp_eb_fb, eb_label, avp_uvp}].

    두 값이 모두 있으면 앞=AVP (EB)/FB, 뒤=AVP/UVP. 값이 하나뿐이고 EB/FB 표시가 있으면 EB/FB 만(AVP/UVP=None).
    값이 하나뿐이고 표시가 없으면 AVP/UVP.
    """
    i = text.find("PACKUNGSANGABEN")
    seg = re.sub(r"\s+", " ", text[i:] if i >= 0 else text)
    rows = []
    for m in _PACK_ROW.finditer(seg):
        c1, c2 = m.group("c1"), m.group("c2")
        eb = uvp = label = None
        if c1 and c2:
            eb, uvp = de_to_float(c1), de_to_float(c2)
            label = c1.strip()[:2] if c1.strip()[:2] in ("EB", "FB") else None
        elif c1 or c2:
            only = c1 or c2
            if re.match(r"\s*(EB|FB)\b", only):
                eb, label = de_to_float(only), only.strip()[:2]
            else:
                uvp = de_to_float(only)
        if eb is None and uvp is None:
            continue
        rows.append({"pack": m.group("pack").strip(), "n": m.group("n"), "pzn": m.group("pzn"),
                     "avp_eb_fb": eb, "eb_label": label, "avp_uvp": uvp})
    return rows


def pack_count(pack_text: str) -> int | None:
    """'28 Filmtbl. 240 mg' → 28, '1 Durchstechfl.' → 1, '2 Vial KIT 45 mg' → 2, '30 Btl.' → 30."""
    m = re.match(r"\s*(\d+)\s*[A-Za-zÄÖÜäöü]", pack_text or "")
    return int(m.group(1)) if m else None


def preisstand(text: str) -> str | None:
    """화면 하단 'Preisstand: 15. Oktober 2026' → '2026-10-15' (자료 기준일 — 개별 가격 시행일 아님)."""
    m = _PREISSTAND.search(text or "")
    if not m:
        return None
    mon = _MONTHS.get(m.group(2).lower())
    return f"{int(m.group(3)):04d}-{mon:02d}-{int(m.group(1)):02d}" if mon else None
