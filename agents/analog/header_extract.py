"""약평위 평가결과 PDF 1페이지 헤더 추출 — 제형·성분·함량 / 브랜드·제조사 / 정확한 심의일.

검증(2026-10-19)에서 확인된 누락의 직접 원인: 보고서 1페이지 헤더에 아래 항목이 있는데 파서가 읽지 않았다.
  (A) ▢ 제형, 성분․함량 : - 1병 중 Obinuzutumab 1g (25mg/mL)           ← 용량·성분
      (가싸이바주, 한국로슈(주))                                       ← 브랜드·제조사
      2016년 제16차 약제급여평가위원회 : 2016년 12월 15일              ← 정확한 심의일
  (B) 2018년 이후 표 형식: '주성분 함량  Ceftazidime/Avibactam 2g/0.5g'
2007~2009년 보고서는 위 항목이 원문에서 비어 있다(공개 제외) — 이 경우 None 을 돌려주며, 값을 지어내지 않는다.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date

_NOISE = re.compile(r"INSID|MS_0001|^\s*-\s*\d+\s*-\s*$")

# '2016년 제16차 약제급여평가위원회 : 2016년 12월 15일'  (차수가 '7,12차' 처럼 여러 개일 수 있고 '[재평가]' 꼬리표가 붙기도 한다)
_RE_SESSION = re.compile(
    r"(\d{4})\s*년\s*(?:제\s*)?(\d+(?:\s*[,，·]\s*\d+)*)\s*차\s*약제\s*급여\s*평가\s*위원회\s*[:：]\s*"
    r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일\s*(\[[^\]]{1,10}\])?"
)
# 성분·함량 값 — '1병 중 Obinuzutumab 1g (25mg/mL)' / 'trabectedin 1.0mg'
_RE_STRENGTH_START = re.compile(r"\s(?=\d)")
_RE_HAS_LATIN = re.compile(r"[A-Za-z]{3}")
# ── 값 검증 게이트 — 틀린 값을 넣느니 비워 둔다(2026-10-19 표본 대조에서 9건 중 5건이 오추출) ───────────────────────
_U = r"(?:mg|g|㎎|㎍|μg|µg|ug|mcg|iu|i\.u\.?|단위|ml|㎖|%|밀리그램|아이유)"
_CLEAN_STRENGTH = re.compile(
    rf"^\d[\d.,]*\s*{_U}(?:\s*[/:]\s*\d[\d.,]*\s*{_U})?(?:\s*\([^()]{{0,30}}\))?(?:\s*,\s*\d[\d.,]*\s*{_U})*$", re.I)
_CLEAN_INN = re.compile(r"^[a-z][a-z \-]{2,59}(?:/[a-z][a-z \-]{2,59})?$")
_COMPANY = re.compile(r"(㈜|\(주\)|\(유\)|\(재\)|\(사\)|제약|약품|코리아|바이오|헬스|사이언스|파마|제약사|Pharma|Korea|Ltd|Inc|등\s*\d+개사|[가-힣]{2,}사$)", re.I)
_RE_STRENGTH_TOKEN = re.compile(r"\d\s*(?:mg|g|㎎|㎍|μg|µg|ug|mcg|iu|ml)\b", re.I)


def _is_company(c: str) -> bool:
    return bool(_COMPANY.search(c)) and not _RE_STRENGTH_TOKEN.search(c) and 2 <= len(c) <= 40


def _is_combination(value: str) -> bool:
    """복합제(성분 2개 이상)면 성분·함량을 한 칸에 담을 수 없다 — ', 영문성분' 또는 한글 성분 나열."""
    return bool(re.search(r",\s*[A-Za-z]{3}", value)) or bool(re.search(r"[가-힣]+\s*\d[\d.]*\s*mg\s*,\s*[가-힣]", value))


def _clean_lines(text: str) -> list[str]:
    text = unicodedata.normalize("NFC", text or "")
    return [ln.strip() for ln in text.split("\n") if ln.strip() and not _NOISE.search(ln)]


def extract_session_dates(text: str) -> list[dict]:
    """약제급여평가위원회 심의일 전부. [{year, ordinals:[int], date:'YYYY-MM-DD', tag}]. 유효하지 않은 날짜는 버린다."""
    out = []
    for m in _RE_SESSION.finditer(unicodedata.normalize("NFC", text or "")):
        y, ords, yy, mm, dd, tag = m.groups()
        try:
            d = date(int(yy), int(mm), int(dd))
        except ValueError:
            continue
        out.append({"year": int(y), "ordinals": [int(x) for x in re.split(r"\s*[,，·]\s*", ords.strip())],
                    "date": d.isoformat(), "tag": (tag or "").strip("[]") or None})
    return out


def _split_inn_strength(value: str) -> tuple[str | None, str | None]:
    """'Obinuzutumab 1g (25mg/mL)' → ('obinuzutumab', '1g (25mg/mL)'). 영문 성분이 없으면 (None, 값 전체)."""
    v = re.sub(r"^[-•·\s]+", "", value or "").strip()
    v = re.sub(r"^.{0,12}?\s중\s+", "", v)                      # '1병 중 ', '1정 중 ', '1 바이알 중 '
    if not v:
        return None, None
    m = _RE_STRENGTH_START.search(v)
    head, tail = (v[:m.start()], v[m.start() + 1:]) if m else (v, "")
    head = head.strip(" ,;")
    if _is_combination(v):
        return None, None
    inn = head.lower() if _CLEAN_INN.match(head.lower()) else None
    strength = tail.strip() if inn and _CLEAN_STRENGTH.match(tail.strip()) else None     # 성분을 확정 못 하면 용량도 쓰지 않는다
    return inn, strength


def extract_header_fields(text: str) -> dict:
    """1페이지 텍스트 → {manufacturer, brand_in_header, inn_en, strength, ingredient_text, session_dates}. 없으면 None/[]."""
    lines = _clean_lines(text)
    out: dict = {"manufacturer": None, "brand_in_header": None, "inn_en": None, "strength": None,
                 "ingredient_text": None, "session_dates": extract_session_dates(text)}

    # (브랜드, 제조사) 줄 — 상단 15줄 안에서 '(' 로 시작하고 ')' 로 끝나며 ',' 가 있는 첫 줄.
    # 예) '(가싸이바주, 한국로슈(주))' · '(욘델리스주사1.0밀리그램(트라벡테딘), ((주)메디팁)' (괄호가 짝이 안 맞는 경우도 있다)
    # 브랜드 줄이 2~3줄로 줄바꿈된 경우(복합제·긴 제품명)는 ')' 로 끝날 때까지 이어 붙인다.
    merged: list[tuple[int, str]] = []
    i = 0
    head_lines = lines[:18]
    while i < len(head_lines):
        ln = head_lines[i]
        j = i
        if ln.startswith("(") and not ln.endswith(")"):
            buf = ln
            while j + 1 < len(head_lines) and j - i < 3 and not buf.endswith(")") and not head_lines[j + 1].startswith(("▢", "□")):
                j += 1
                buf = f"{buf} {head_lines[j]}"
            ln = buf
        merged.append((i, ln))
        i = j + 1
    brand_idx = None
    for idx, ln in merged:
        if not (ln.startswith("(") and ln.endswith(")") and "," in ln):
            continue
        brand, _, company = ln[1:-1].rpartition(",")
        company = company.strip()
        while company.startswith("(") and company.count("(") > company.count(")"):
            company = company[1:].strip()
        if brand.strip() and _is_company(company):
            out["brand_in_header"], out["manufacturer"], brand_idx = brand.strip(), company, idx
            break

    # ① '▢ 제형, 성분․함량' 블록 (다음 ▢ 전까지)
    for i, ln in enumerate(lines):
        if re.search(r"제형\s*[,，、]?\s*성분", ln):
            val = re.sub(r"^.*?[:：]\s*", "", ln).strip()
            if not val and i + 1 < len(lines) and not lines[i + 1].startswith(("▢", "□")):
                val = lines[i + 1]
            if val and not re.search(r"효능|심의", val):
                out["ingredient_text"] = val
                out["inn_en"], out["strength"] = _split_inn_strength(val)
            break
    # ② 표 형식 '주성분 함량  Ceftazidime/Avibactam 2g/0.5g' (2018+)
    if not out["ingredient_text"]:
        for ln in lines[:40]:
            m = re.match(r"주성분\s*함량\s+(.+)", ln)
            if m:
                out["ingredient_text"] = m.group(1).strip()
                out["inn_en"], out["strength"] = _split_inn_strength(out["ingredient_text"])
                break
    # ③ 제목 아래, 브랜드 줄 바로 위 줄 — 'trabectedin 1.0mg', 'Human C1 esterase inhibitor, 500I.U'
    if not (out["inn_en"] and out["strength"]) and brand_idx:
        prev = lines[brand_idx - 1] if brand_idx >= 1 else ""
        if prev and not re.search(r"평가\s*결과|적정성|위원회", prev):
            inn, st = _split_inn_strength(prev)
            out["inn_en"] = out["inn_en"] or inn
            out["strength"] = out["strength"] or st
            out["ingredient_text"] = out["ingredient_text"] or prev
    return out


_NOT_INGREDIENT_EXACT = {"미분화", "유전자재조합", "사람단백질씨", "분화", "단클론항체"}
_NOT_INGREDIENT_RE = re.compile(
    r"(스미스클라인|아스트라|화이자|노바티스|로슈|얀센|사노피|릴리|엠에스디|다케다|아스텔라스|암젠|애브비|바이엘|베링거|길리어드|세엘진|"
    r"한국|지점|제약|약품|코리아|바이오|헬스|사이언스|파마|회사|그룹|암$|증$|질환|병$)")


def _not_an_ingredient(cand: str) -> bool:
    """파일명 괄호 안 한글이 성분명이 아닌 경우(회사명·질환명·조각)를 거른다. 틀린 성분명을 넣느니 비워 둔다."""
    c = re.sub(r"\s", "", cand or "")
    if len(c) < 3 or c in _NOT_INGREDIENT_EXACT:
        return True
    if re.search(r"(주|사)$", c) and len(c) <= 8:
        return True
    return bool(_NOT_INGREDIENT_RE.search(c))


def korean_ingredient_from_name(brand_name_raw: str | None, file_name: str | None = None) -> str | None:
    """'가니레버프리필드시린지주0.25mg-0.5ml(가니렐릭스아세트산염)_(0.25mg-0.5mL)' → '가니렐릭스아세트산염'.
    파일명 괄호 안 한글 성분명. 규격 괄호('(0.25mg-0.5mL)')·회사 괄호는 한글 성분이 아니므로 건너뛴다."""
    for src in (brand_name_raw, file_name):
        s = unicodedata.normalize("NFC", src or "")
        for m in re.finditer(r"\(([가-힣][가-힣,\s·\-]{1,40}?)\)", s):
            cand = m.group(1).strip()
            if _not_an_ingredient(cand):
                continue
            return cand
    return None


def looks_extractable(pages: list[str]) -> bool:
    """텍스트 PDF 인지(스캔이 아닌지) 판정. 이전 기준(첫 3페이지 300자 미만이면 스캔)은 2007~2012년의 짧은 비급여
    평가결과(중앙값 약 240자)를 전부 스캔으로 오판해 275건 중 273건이 실제 텍스트 PDF 인데도 화면에서 빠졌다."""
    full = "".join(pages[:3]).strip()
    if full.count("(cid:") > 10:
        return False
    if len(full) >= 300:
        return True
    return len(full) >= 40 and bool(re.search(r"제형\s*[,，]?\s*성분|효능\s*[·ㆍ]?\s*효과|심의\s*일|평가\s*결과", full))


_RE_EFFECT_HEAD = re.compile(r"^[▢□\s]*효능\s*[·ㆍ]?\s*효과\s*[:：]?\s*(.*)$")
_RE_NEXT_SECTION = re.compile(r"^[▢□]|심의|위원회|평가\s*(?:결과|내용)\s*$|^[나다라]\.\s|^※")
_RE_NOT_EFFECT = re.compile(r"위원회|심의|평가\s*내용|평가\s*결과")


def extract_effect_text(text: str, max_chars: int = 1200) -> str | None:
    """보고서 1페이지 '▢ 효능·효과' 단락 원문(다음 ▢ 단락 전까지). 식약처 허가 효능 텍스트와 별개의 평가위 보고서 원천.

    비어 있으면(2007~2009 공개 제외 보고서 등) None. 원문 그대로 이어 붙이며 요약·추정하지 않는다."""
    lines = _clean_lines(text)
    for i, ln in enumerate(lines):
        m = _RE_EFFECT_HEAD.match(ln)
        if not m:
            continue
        parts = [m.group(1).strip()] if m.group(1).strip() else []
        for nxt in lines[i + 1:i + 40]:
            if _RE_NEXT_SECTION.search(nxt):
                break
            parts.append(nxt)
        body = re.sub(r"[\uf09f\uf0a7\u2022•]+", " ", " ".join(parts))
        body = re.sub(r"\s+", " ", re.sub(r"(^|\s)-\s+", r"\1", body)).strip(" -:")
        if len(re.sub(r"[\s\-·,.]", "", body)) < 4 or _RE_NOT_EFFECT.search(body):
            return None      # 원문 단락이 비어 다음 섹션 제목을 잡은 경우
        return body[:max_chars]
    return None
