"""일반약제 항목 '최신' 검증 — 책자(예: 2026년 7월판) 이후 개정 고시 반영.

1) 보험인정기준 '고시' 탭 전체 목록에서 관련근거가 '(약제)' 인 행을 모은다(목록만, 상세는 필요한 것만).
2) 제목 정규화(분류번호 제외: 성분+제형, 품명 괄호 제거)로 책자 항목과 매칭.
3) 매칭된 고시 중 책자 항목의 고시보다 '새로운'(연도·번호가 큰) 것이 있으면 상세 팝업 본문으로 교체
   (latest_basis='gosi_newer'). 책자 최대 고시번호보다 새로운 고시가 책자에 없는 항목이면 신설로 추가.
   상세 본문 섹션이 '삭제' 이면 행 제거.
4) 매칭 실패·판단 불가는 report 에 남긴다. 요청 간 0.3초 이상.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
import unicodedata
from typing import Callable, Optional

from . import sources

SLEEP = 0.35


def gosi_tuple(g: Optional[str]) -> tuple[int, int]:
    m = re.search(r"(\d{4})\s*-\s*(\d+)", g or "")
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def basis_gosi(basis: str) -> Optional[str]:
    m = re.search(r"고시\s*제?\s*(\d{4})\s*-\s*(\d+)\s*호", basis or "")
    return f"고시 제{m.group(1)}-{int(m.group(2))}호" if m else None


def match_key(title: str) -> str:
    """제목 → 매칭 키: [분류] 제거, 품명 괄호 제거, 소문자·영숫자·한글만."""
    s = unicodedata.normalize("NFKC", title or "").lower()
    s = re.sub(r"\[[^\]]*\]", " ", s)
    s = re.sub(r"\(\s*품\s*명[^)]*\)?", " ", s)
    s = re.sub(r"\(\s*성장호르몬제\s*\)", " ", s)
    s = s.replace("+", " + ")
    return re.sub(r"[^0-9a-z가-힣α-ω+]+", "", s)


def ingredient_key(title: str) -> str:
    k = match_key(title)
    k = re.split(r"(경구제|주사제|외용제|흡입제|흡입액|점안액|시럽제|패취제|복합경구제|복합주사제|제제|내복액)", k)[0]
    return k


def collect_drug_gosi(max_pages: int = 60, fetch_list: Callable = sources.insu_list) -> list[dict]:
    rows, page = [], 1
    while page <= max_pages:
        res = fetch_list("01", page, 100)
        if not res["rows"]:
            break
        for r in res["rows"]:
            if "약제" in (r["basis"] or "") and r["key"]:
                g = basis_gosi(r["basis"])
                if g:
                    rows.append({**r, "gosi_no": g})
        total = res.get("total") or 0
        if page * 100 >= total:
            break
        page += 1
        time.sleep(SLEEP)
    return rows


def _action(detail: dict) -> str:
    names = " ".join(detail.get("sections", {}).keys())
    if "삭제" in names and "전체내용" in names and "신설" not in names and "개정" not in names:
        return "delete"
    if "신설" in names:
        return "new"
    return "amend"


def _full_text(detail: dict) -> str:
    for k, v in detail.get("sections", {}).items():
        if "전체내용" in k:
            return v
    return ""


def _effective(detail: dict, gosi_no: str) -> Optional[str]:
    """상세의 '고시번호(시행일자)' 에서 목록의 고시번호와 같은 번호의 시행일. 없으면 None(호출측이 게시일 사용)."""
    y, n = gosi_tuple(gosi_no)
    for k, v in detail.get("sections", {}).items():
        if "고시번호(시행일자)" in k.replace(" ", "") and "변경전" not in k.replace(" ", ""):
            m = re.search(rf"{y}\s*-\s*{n}\s*호\s*\((\d{{4}})\.\s*(\d{{1,2}})\.\s*(\d{{1,2}})", v)
            if m:
                return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return None


def form_of(title: str) -> str:
    from .drug_booklet import parse_title
    return parse_title(re.sub(r"\[[^\]]*\]", "", title or "").strip())[1]


def class_from_attachment(detail: dict) -> Optional[str]:
    """상세 첨부(HWP)를 kordoc 으로 변환해 '[421]' 분류번호를 읽는다(신설 항목용)."""
    from .http import download
    from .kordoc_runner import convert
    from .store import RAW_DIR
    for a in detail.get("attachments", []):
        try:
            f = download(a["url"], RAW_DIR / "drug_gosi", expect=("ole", "zip", "pdf"), name_hint=a["fnm"])
            ext = Path(a["fnm"]).suffix.lower() or ".hwp"
            src = f["path"] if f["path"].suffix.lower() == ext else f["path"]
            md = convert(src, timeout=300).read_text(encoding="utf-8")
            m = re.search(r"\[(\d{3})\]", md)
            if m:
                return m.group(1)
        except Exception:  # noqa: BLE001
            continue
    return None


def _brand(title: str) -> str:
    m = re.search(r"품\s*[명목]\s*[:：]?\s*([^)]+)", title or "")
    return re.sub(r"[^0-9a-z가-힣]", "", unicodedata.normalize("NFKC", m.group(1)).lower())[:5] if m else ""


def _by_brand(gosi_title: str, cands: list[dict]) -> list[dict]:
    b = _brand(gosi_title)
    if not b:
        return []
    return [c for c in cands if b and b in re.sub(r"[^0-9a-z가-힣]", "", unicodedata.normalize("NFKC", c["title"]).lower())]


def apply_latest(items: list[dict], *, gosi_rows: list[dict] | None = None,
                 fetch_detail: Callable = sources.insu_detail) -> dict:
    """items: drug_criteria_item 딕셔너리 목록(제자리 수정). 반환: 보고서."""
    gosi_rows = gosi_rows if gosi_rows is not None else collect_drug_gosi()
    booklet_max = max((gosi_tuple(i["gosi_no"]) for i in items if i.get("gosi_no")), default=(0, 0))
    by_key: dict[str, list[dict]] = {}
    for it in items:
        for k in {match_key(it["title"]), ingredient_key(it["title"])}:
            if k:
                by_key.setdefault(k, []).append(it)

    report = {"booklet_max_gosi": f"{booklet_max[0]}-{booklet_max[1]}", "drug_gosi_rows": len(gosi_rows),
              "replaced": [], "added": [], "deleted": [], "unmatched_newer": [], "already_latest": 0,
              "detail_fetches": 0, "errors": []}
    # 오래된 것부터 → 최신이 마지막에 이긴다
    for g in sorted(gosi_rows, key=lambda r: gosi_tuple(r["gosi_no"])):
        gt = gosi_tuple(g["gosi_no"])
        # 매칭: 제목 키 완전일치, 아니면 성분키 일치 + 제형 일치(제형 다르면 별개 항목 — 예: Sirolimus 경구제 vs 시럽제)
        cands = list(by_key.get(match_key(g["title"])) or [])
        fuzzy = False
        if not cands:
            gf = form_of(g["title"])
            cands = [c for c in by_key.get(ingredient_key(g["title"]), []) if form_of(c["title"]) == gf]
            fuzzy = bool(cands)
        if len(cands) > 1:                       # 같은 성분·제형 항목이 여럿(텍피데라 vs 스킬라렌스) → 품명으로 고른다
            cands = _by_brand(g["title"], cands) or cands
        if cands:
            target = cands[0]
            if gt <= gosi_tuple(target.get("gosi_no")) and (target.get("criteria_text") or "").strip():   # 본문이 빈 항목은 같은 고시라도 채운다
                target.setdefault("_insu", []).append(g["gosi_no"])
                if gt == gosi_tuple(target.get("gosi_no")):
                    report["already_latest"] += 1
                continue
        elif gt <= booklet_max:
            continue  # 책자 시점 이전 고시인데 매칭 안 됨 → 제목 표기 차이(책자에 수록된 것으로 간주)
        # 새 고시 → 상세
        try:
            time.sleep(SLEEP)
            d = fetch_detail(*g["key"])
            report["detail_fetches"] += 1
        except Exception as e:  # noqa: BLE001
            report["errors"].append({"gosi": g["gosi_no"], "title": g["title"], "error": str(e)[:200]})
            continue
        act, text = _action(d), _full_text(d)
        eff = _effective(d, g["gosi_no"])
        if not eff:
            report.setdefault("detail_gosi_mismatch", []).append({"title": g["title"], "list_gosi": g["gosi_no"]})
            eff = g["posted_date"]
        if act == "new" and cands and fuzzy:
            cands = []  # 신설 고시는 제목이 완전히 같지 않으면 새 항목
        if cands and act == "delete":
            items.remove(cands[0])
            report["deleted"].append({"title": cands[0]["title"], "gosi": g["gosi_no"], "url": d["url"]})
            continue
        if not text:
            report["errors"].append({"gosi": g["gosi_no"], "title": g["title"], "error": "전체내용 섹션 없음"})
            continue
        if cands:
            t = cands[0]
            prev = t.get("gosi_no")
            t.update(criteria_text=text, gosi_no=g["gosi_no"], effective_date=eff, latest_basis="gosi_newer",
                     latest_url=d["url"])
            t.setdefault("_insu", []).append(g["gosi_no"])
            report["replaced"].append({"title": t["title"], "gosi_title": g["title"], "from": prev, "to": g["gosi_no"],
                                       "effective": eff, "url": d["url"], "match": "fuzzy" if fuzzy else "exact"})
        elif gt > booklet_max:
            cls = _class_from_text(text) or class_from_attachment(d)
            new = {"class_no": cls or "미상", "title": g["title"], "criteria_text": text,
                   "gosi_no": g["gosi_no"], "effective_date": eff, "latest_basis": "gosi_new", "latest_url": d["url"],
                   "parse_flags": "added_from_gosi"}
            items.append(new)
            for k in {match_key(new["title"]), ingredient_key(new["title"])}:
                by_key.setdefault(k, []).append(new)
            report["added"].append({"title": g["title"], "gosi": g["gosi_no"], "action": act, "url": d["url"]})
        else:
            report["unmatched_newer"].append({"title": g["title"], "gosi": g["gosi_no"]})
    # 항목별 최신 상태: 매칭된 고시 목록의 최댓값과 항목 고시 비교
    status = {"verified_by_gosi_list": 0, "replaced_newer": 0, "booklet_newer_than_list": 0, "no_gosi_list_match": 0,
              "newer_gosi_unapplied": 0}
    for it in items:
        ins = it.pop("_insu", [])
        if it.get("latest_basis") in ("gosi_newer", "gosi_new"):
            st = "replaced_newer" if it["latest_basis"] == "gosi_newer" else "verified_by_gosi_list"
        elif not ins:
            st = "no_gosi_list_match"
        else:
            mx = max(gosi_tuple(x) for x in ins)
            st = "verified_by_gosi_list" if mx == gosi_tuple(it.get("gosi_no")) else (
                "booklet_newer_than_list" if mx < gosi_tuple(it.get("gosi_no")) else "newer_gosi_unapplied")
            if st == "newer_gosi_unapplied":
                report.setdefault("newer_gosi_unapplied", []).append({"title": it["title"], "item_gosi": it.get("gosi_no"),
                                                                       "list_max": f"{mx[0]}-{mx[1]}"})
        it["_latest_status"] = st
        status[st] += 1
    report["item_status"] = status
    return report


def _class_from_text(text: str) -> Optional[str]:
    m = re.search(r"\[(\d{3})\]", text or "")
    return m.group(1) if m else None
