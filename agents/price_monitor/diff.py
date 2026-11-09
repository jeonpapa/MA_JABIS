"""모니터링 회차 비교 — 순수 함수.

비교 기준은 **현지 통화 원가격(local_price)** 이다. 원화 조정가는 환율·산식 변경이 섞이므로 가격 변동 판정에 쓰지 않고,
원가격이 같은데 원화만 움직인 경우는 '환율 영향'으로 따로 보여 준다(변경 이력 화면 classify_price_sessions 와 같은 원칙).

국가 변경 유형(kind):
  first     — 직전 회차 없음(첫 회차)
  same      — 원가격·출처 같음
  price     — 같은 출처·단계에서 원가격 변동 (실제 가격 변동 → 하이라이트)
  basis     — 출처/가격 단계 표기가 바뀌고 값도 다름 → 직접 비교 불가 (인하·인상으로 읽지 않음)
  packs     — 겹치는 포장 값은 같고 포장 구성만 추가·제외
  now_priced— 직전엔 가격 없음 → 이번에 확인
  lost      — 직전엔 가격 있음 → 이번에 미확인/실패 (판정 사유 함께)
  unpriced  — 직전·이번 모두 가격 없음
"""
from __future__ import annotations

import re

HIGHLIGHT_KINDS = ("price", "basis", "packs", "now_priced", "lost")
FX_NOTE_THRESHOLD = 0.005   # 원화 조정가 0.5% 이상 움직이면 환율 영향 표시


def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def row_key(r: dict) -> str:
    fk = r.get("formulation_key") or _norm(r.get("dosage_strength")) or _norm(r.get("product_name"))
    return f"{fk}|{r.get('pack_count') or _norm(r.get('package_unit'))}"


def _sig(rows: list[dict]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for r in rows:
        if r.get("local_price") is None:
            continue
        out.setdefault(row_key(r), []).append(round(float(r["local_price"]), 2))
    return {k: sorted(v) for k, v in out.items()}


def _labels(rows: list[dict]) -> frozenset:
    return frozenset(_norm(r.get("source_label")) for r in rows if r.get("local_price") is not None)


def _krw(rows: list[dict]) -> float | None:
    vals = [float(r["adjusted_price_krw"]) for r in rows if r.get("adjusted_price_krw")]
    return min(vals) if vals else None


def _desc(rows: list[dict], key: str) -> str:
    for r in rows:
        if row_key(r) == key:
            return " ".join(x for x in (r.get("product_name"), r.get("package_unit")) if x)[:120]
    return key


def compare_country(prev: dict | None, cur: dict) -> dict:
    cur_rows = [r for r in (cur.get("rows") or []) if r.get("local_price") is not None]
    out = {"kind": None, "changes": [], "fx": None}
    if prev is None:
        out["kind"] = "first" if cur_rows else "unpriced"
        return out
    prev_rows = [r for r in (prev.get("rows") or []) if r.get("local_price") is not None]
    if not cur_rows:
        out["kind"] = "lost" if prev_rows else "unpriced"
        return out
    if not prev_rows:
        out["kind"] = "now_priced"
        return out
    ps, cs = _sig(prev_rows), _sig(cur_rows)
    common = set(ps) & set(cs)
    changed = sorted(k for k in common if ps[k] != cs[k])
    currency = (cur_rows[0].get("currency") or "")
    for k in changed:
        old, new = ps[k], cs[k]
        pct = (new[0] - old[0]) / old[0] if old and new and old[0] else None
        out["changes"].append({"key": k, "label": _desc(cur_rows, k), "old": old, "new": new,
                               "pct": round(pct, 4) if pct is not None else None, "currency": currency})
    if changed:
        out["kind"] = "basis" if _labels(prev_rows) != _labels(cur_rows) else "price"
    elif set(ps) ^ set(cs):
        out["kind"] = "packs"
        out["changes"] = [{"key": k, "label": _desc(cur_rows if k in cs else prev_rows, k),
                           "old": ps.get(k), "new": cs.get(k), "pct": None, "currency": currency}
                          for k in sorted(set(ps) ^ set(cs))]
    else:
        out["kind"] = "same"
    a, b = _krw(prev_rows), _krw(cur_rows)
    if out["kind"] == "same" and a and b and abs(b - a) / a >= FX_NOTE_THRESHOLD:
        out["fx"] = {"old_krw": round(a), "new_krw": round(b), "pct": round((b - a) / a, 4)}
    return out


def compare_runs(prev_snapshot: dict | None, cur_snapshot: dict) -> dict:
    """회차 비교. 약제는 query 로 맞춘다(약제를 빼고 넣어도 남은 약제끼리 비교)."""
    prev_items = {it["query"]: it for it in (prev_snapshot or {}).get("items", [])}
    items, totals = [], {k: 0 for k in ("first", "same", "price", "basis", "packs", "now_priced", "lost", "unpriced")}
    totals["fx"] = 0
    for it in cur_snapshot.get("items", []):
        p = prev_items.get(it["query"])
        countries = {}
        for cc, cur in (it.get("countries") or {}).items():
            pc = (p or {}).get("countries", {}).get(cc) if p else None
            d = compare_country(pc if p else None, cur)
            d.update({"status": cur.get("status"), "verdict": cur.get("verdict"), "reason": cur.get("reason")})
            countries[cc] = d
            totals[d["kind"]] += 1
            if d.get("fx"):
                totals["fx"] += 1
        items.append({"query": it["query"], "label": it.get("label") or it["query"], "countries": countries,
                      "highlight": any(c["kind"] in HIGHLIGHT_KINDS for c in countries.values()),
                      "is_new_item": p is None and prev_snapshot is not None})
    return {"items": items, "totals": totals, "has_previous": prev_snapshot is not None,
            "highlight_count": sum(totals[k] for k in HIGHLIGHT_KINDS)}
