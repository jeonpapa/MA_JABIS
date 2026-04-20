"""Daily Mailing 디지스트 렌더러 — 허가/약가/미디어/MSD 요약을 실데이터로 묶어 HTML 생성.

사용처:
- `/api/mailing/preview` — 프리뷰 표시
- `/api/mail-subscriptions/<id>/test-send` — 실제 발송
"""
from __future__ import annotations

import html as _html
import logging
from datetime import datetime, timedelta
from pathlib import Path

from agents.db import DrugPriceDB
from agents import media_intelligence as _media_intel

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[2]
_db_singleton: DrugPriceDB | None = None


def _get_db() -> DrugPriceDB:
    global _db_singleton
    if _db_singleton is None:
        _db_singleton = DrugPriceDB(BASE_DIR / "data" / "db" / "drug_prices.db")
    return _db_singleton


def _fetch_msd_summary() -> dict:
    """MSD 최신 고시일 기준 품목수 + 고시일."""
    try:
        with _get_db()._connect() as conn:
            row = conn.execute("SELECT MAX(apply_date) FROM drug_prices").fetchone()
            latest = row[0] if row and row[0] else None
            if not latest:
                return {"count": 0, "latest_apply_date": None}
            cnt_row = conn.execute(
                "SELECT COUNT(DISTINCT product_name_kr) FROM drug_prices "
                "WHERE company LIKE ? AND apply_date = ? AND max_price > 0",
                ("%엠에스디%", latest),
            ).fetchone()
            return {"count": int(cnt_row[0]) if cnt_row else 0, "latest_apply_date": latest}
    except Exception as e:
        logger.warning("[digest] MSD summary 실패: %s", e)
        return {"count": 0, "latest_apply_date": None}


def _fetch_top_price_changes(limit: int = 5) -> tuple[str | None, str | None, list[dict]]:
    try:
        with _get_db()._connect() as conn:
            dates = [r[0] for r in conn.execute(
                "SELECT DISTINCT apply_date FROM drug_prices "
                "ORDER BY apply_date DESC LIMIT 2"
            ).fetchall()]
            if len(dates) < 2:
                return (None, None, [])
            latest, prev = dates[0], dates[1]
            rows = conn.execute(
                """
                SELECT c.product_name_kr, c.ingredient, c.company,
                       c.max_price, p.max_price
                FROM drug_prices c
                JOIN drug_prices p
                  ON p.insurance_code = c.insurance_code AND p.apply_date = ?
                WHERE c.apply_date = ? AND c.max_price IS NOT NULL
                  AND p.max_price IS NOT NULL AND p.max_price > 0
                  AND c.max_price != p.max_price
                """,
                (prev, latest),
            ).fetchall()
        items = []
        for r in rows:
            curr, prev_p = int(r[3]), int(r[4])
            delta = curr - prev_p
            pct = (delta / prev_p) * 100
            items.append({
                "product": r[0] or "",
                "ingredient": r[1] or "",
                "company": r[2] or "",
                "prev_price": prev_p,
                "curr_price": curr,
                "delta": delta,
                "delta_pct": round(pct, 2),
            })
        items.sort(key=lambda x: abs(x["delta_pct"]), reverse=True)
        return (latest, prev, items[:limit])
    except Exception as e:
        logger.warning("[digest] top price changes 실패: %s", e)
        return (None, None, [])


def _fetch_recent_approvals(days: int = 30, limit: int = 8) -> list[dict]:
    """최근 N일 내 MFDS 허가 (적응증 단위)."""
    try:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        with _get_db()._connect() as conn:
            rows = conn.execute(
                """
                SELECT m.product, m.disease, m.line_of_therapy, m.stage,
                       m.biomarker_class, a.approval_date
                FROM indications_by_agency a
                JOIN indications_master m ON m.indication_id = a.indication_id
                WHERE a.agency = 'MFDS' AND a.approval_date >= ?
                ORDER BY a.approval_date DESC
                LIMIT ?
                """,
                (cutoff, limit),
            ).fetchall()
        return [{
            "product": r[0] or "",
            "disease": r[1] or "",
            "line_of_therapy": r[2] or "",
            "stage": r[3] or "",
            "biomarker_class": r[4] or "",
            "approval_date": r[5],
        } for r in rows]
    except Exception as e:
        logger.warning("[digest] recent approvals 실패: %s", e)
        return []


def _fetch_media_top(limit: int = 5) -> tuple[str | None, list[dict]]:
    """Naver 뉴스 1개월 트래픽 Top N 브랜드 (캐시 우선)."""
    try:
        data = _media_intel.get_brand_traffic(days=30, refresh=False)
        brands = data.get("brands", [])[:limit]
        return (data.get("updated_at"), brands)
    except Exception as e:
        logger.warning("[digest] media top 실패: %s", e)
        return (None, [])


def _sparkline_svg(values: list[int], color: str = "#00E5CC", width: int = 120, height: int = 28) -> str:
    if not values or len(values) < 2:
        return ""
    v_min = min(values)
    v_max = max(values)
    rng = v_max - v_min or 1
    pts = []
    for i, v in enumerate(values):
        x = (i / (len(values) - 1)) * width
        y = height - ((v - v_min) / rng) * height
        pts.append(f"{x:.1f},{y:.1f}")
    poly = " ".join(pts)
    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'xmlns="http://www.w3.org/2000/svg" style="display:inline-block;vertical-align:middle;">'
        f'<polyline points="{poly}" fill="none" stroke="{color}" stroke-width="1.8" '
        f'stroke-linejoin="round" stroke-linecap="round" />'
        f'</svg>'
    )


def _format_price(v: int) -> str:
    return f"{v:,}원"


def _section_style() -> str:
    return "margin:0 0 20px;padding:16px 20px;background:#161B27;border:1px solid #1E2530;border-radius:12px;"


def _h3_style(color: str = "#00E5CC") -> str:
    return f"margin:0 0 12px;padding:0;color:{color};font-size:14px;font-weight:700;letter-spacing:-0.01em;"


def render_daily_digest(
    name: str = "Daily Dossier",
    dashboard_url: str = "http://localhost:3000",
    keywords: list[str] | None = None,
    media: list[str] | None = None,
) -> tuple[str, str, str]:
    """실데이터 기반 HTML 디지스트.

    Returns:
        (subject, html, text)
    """
    today = datetime.now().strftime("%Y-%m-%d")
    msd = _fetch_msd_summary()
    latest, prev, price_changes = _fetch_top_price_changes(limit=5)
    approvals = _fetch_recent_approvals(days=30, limit=8)
    media_updated, media_top = _fetch_media_top(limit=5)

    subject = f"[MA Dossier] {name} · {today}"

    # ── 1. 허가 변동 ────────────────────────────────────────────
    if approvals:
        app_rows = []
        for a in approvals:
            parts = [_html.escape(a["disease"])]
            if a["line_of_therapy"]:
                parts.append(_html.escape(a["line_of_therapy"]))
            if a["stage"]:
                parts.append(_html.escape(a["stage"]))
            if a["biomarker_class"] and a["biomarker_class"] != "all_comers":
                parts.append(_html.escape(a["biomarker_class"].replace("_", " ")))
            title = " · ".join(parts)
            app_rows.append(
                f'<tr><td style="padding:8px 10px;border-bottom:1px solid #1E2530;color:#C9D1D9;font-size:13px;">'
                f'<span style="color:#F59E0B;font-weight:600;">{_html.escape(a["product"])}</span>'
                f'</td><td style="padding:8px 10px;border-bottom:1px solid #1E2530;color:#8B9BB4;font-size:12px;">{title}</td>'
                f'<td style="padding:8px 10px;border-bottom:1px solid #1E2530;color:#00E5CC;font-size:12px;text-align:right;white-space:nowrap;">{a["approval_date"]}</td></tr>'
            )
        approvals_html = (
            f'<table cellpadding="0" cellspacing="0" width="100%" style="border-collapse:collapse;">'
            + "".join(app_rows) + "</table>"
        )
    else:
        approvals_html = '<p style="color:#4A5568;font-size:12px;margin:0;">지난 30일간 신규 MFDS 허가 없음</p>'

    # ── 2. 약가 변동 ────────────────────────────────────────────
    if price_changes:
        rows = []
        for p in price_changes:
            up = p["delta_pct"] >= 0
            color = "#EF4444" if not up else "#10B981"  # 하락 적색, 상승 녹색
            arrow = "▼" if p["delta"] < 0 else "▲"
            rows.append(
                f'<tr><td style="padding:8px 10px;border-bottom:1px solid #1E2530;color:#C9D1D9;font-size:13px;">'
                f'{_html.escape(p["product"][:40])}</td>'
                f'<td style="padding:8px 10px;border-bottom:1px solid #1E2530;color:#8B9BB4;font-size:11px;">{_html.escape(p["company"])}</td>'
                f'<td style="padding:8px 10px;border-bottom:1px solid #1E2530;color:#8B9BB4;font-size:12px;text-align:right;white-space:nowrap;">'
                f'{_format_price(p["prev_price"])} → {_format_price(p["curr_price"])}</td>'
                f'<td style="padding:8px 10px;border-bottom:1px solid #1E2530;color:{color};font-size:12px;text-align:right;font-weight:700;white-space:nowrap;">'
                f'{arrow} {abs(p["delta_pct"]):.1f}%</td></tr>'
            )
        price_html = (
            f'<p style="color:#8B9BB4;font-size:11px;margin:0 0 8px;">{_html.escape(prev or "—")} → {_html.escape(latest or "—")}</p>'
            f'<table cellpadding="0" cellspacing="0" width="100%" style="border-collapse:collapse;">'
            + "".join(rows) + "</table>"
        )
    else:
        price_html = '<p style="color:#4A5568;font-size:12px;margin:0;">약가 변동 없음</p>'

    # ── 3. 미디어 인텔리전스 ───────────────────────────────────
    if media_top:
        media_rows = []
        max_count = max((b["total_count"] for b in media_top), default=1) or 1
        for b in media_top:
            ratio = min(1.0, b["total_count"] / max_count)
            bar_w = int(ratio * 100)
            spark = _sparkline_svg(b["sparkline"], color="#00E5CC", width=110, height=22)
            first_news = (b.get("latest_news") or [{}])[0]
            news_title = first_news.get("title", "")
            news_url = first_news.get("url", "")
            news_html = (
                f'<a href="{_html.escape(news_url)}" style="color:#8B9BB4;font-size:11px;text-decoration:none;">· {_html.escape(news_title[:60])}</a>'
                if news_url else ""
            )
            media_rows.append(
                f'<tr><td style="padding:8px 10px;border-bottom:1px solid #1E2530;width:25%;">'
                f'<span style="color:#F59E0B;font-weight:700;font-size:13px;">{_html.escape(b["brand"])}</span><br/>'
                f'<span style="color:#4A5568;font-size:10px;">{b["total_count"]}건</span>'
                f'</td><td style="padding:8px 10px;border-bottom:1px solid #1E2530;width:30%;">'
                f'<div style="background:#0D1117;border-radius:2px;overflow:hidden;height:4px;width:100%;">'
                f'<div style="height:4px;width:{bar_w}%;background:#00E5CC;"></div></div>'
                f'</td><td style="padding:8px 10px;border-bottom:1px solid #1E2530;width:20%;text-align:center;">{spark}</td>'
                f'<td style="padding:8px 10px;border-bottom:1px solid #1E2530;">{news_html}</td></tr>'
            )
        media_html = (
            f'<p style="color:#8B9BB4;font-size:11px;margin:0 0 8px;">Naver 뉴스 · 30일 누적 · 업데이트 {(media_updated or "")[:10]}</p>'
            f'<table cellpadding="0" cellspacing="0" width="100%" style="border-collapse:collapse;">'
            + "".join(media_rows) + "</table>"
        )
    else:
        media_html = '<p style="color:#4A5568;font-size:12px;margin:0;">미디어 데이터 없음</p>'

    # ── 4. MSD 요약 ───────────────────────────────────────────
    msd_html = (
        f'<table cellpadding="0" cellspacing="0" width="100%" style="border-collapse:collapse;"><tr>'
        f'<td style="width:50%;text-align:center;">'
        f'<div style="color:#00E5CC;font-size:28px;font-weight:800;line-height:1;">{msd["count"]}</div>'
        f'<div style="color:#8B9BB4;font-size:11px;margin-top:4px;">급여 등재 품목</div></td>'
        f'<td style="width:50%;text-align:center;">'
        f'<div style="color:#F59E0B;font-size:15px;font-weight:700;line-height:1;">{_html.escape(msd.get("latest_apply_date") or "—")}</div>'
        f'<div style="color:#8B9BB4;font-size:11px;margin-top:4px;">최근 고시일</div></td>'
        f'</tr></table>'
    )

    # ── 구독 조건 요약 ────────────────────────────────────────
    kws = keywords or []
    meds = media or []
    meta_html = ""
    if kws or meds:
        kw_html = ("<span style=\"color:#00E5CC;\">" + " · ".join(_html.escape(k) for k in kws[:8]) + "</span>") if kws else "—"
        m_html = _html.escape(", ".join(meds[:8])) if meds else "—"
        meta_html = (
            f'<div style="margin:0 0 16px;padding:10px 14px;background:#0D1117;border:1px solid #1E2530;border-radius:8px;">'
            f'<div style="color:#4A5568;font-size:10px;margin-bottom:4px;">모니터링 조건</div>'
            f'<div style="color:#8B9BB4;font-size:12px;line-height:1.5;">'
            f'🔎 {kw_html}<br/>📰 <span style="color:#8B9BB4;">{m_html}</span>'
            f'</div></div>'
        )

    dashboard_safe = _html.escape(dashboard_url)
    name_safe = _html.escape(name)

    body_html = f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{subject}</title></head>
<body style="margin:0;padding:24px 16px;background:#0D1117;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Malgun Gothic',sans-serif;color:#C9D1D9;">
<div style="max-width:680px;margin:0 auto;">
  <div style="margin:0 0 20px;padding:20px 24px;background:linear-gradient(135deg,#161B27 0%,#0D1117 100%);border:1px solid #1E2530;border-radius:14px;">
    <div style="color:#00E5CC;font-size:11px;font-weight:600;letter-spacing:0.08em;margin-bottom:6px;">MA AI DOSSIER · DAILY DIGEST</div>
    <h1 style="margin:0 0 6px;color:#FFFFFF;font-size:22px;font-weight:800;letter-spacing:-0.02em;">{name_safe}</h1>
    <div style="color:#8B9BB4;font-size:12px;">{today}</div>
  </div>

  {meta_html}

  <div style="{_section_style()}">
    <h3 style="{_h3_style('#F59E0B')}">🩺 최근 MFDS 허가 (30일)</h3>
    {approvals_html}
  </div>

  <div style="{_section_style()}">
    <h3 style="{_h3_style('#EF4444')}">💊 약가 변동 Top 5</h3>
    {price_html}
  </div>

  <div style="{_section_style()}">
    <h3 style="{_h3_style('#00E5CC')}">📊 미디어 인텔리전스 Top 5</h3>
    {media_html}
  </div>

  <div style="{_section_style()}">
    <h3 style="{_h3_style('#A78BFA')}">🏢 MSD 급여 요약</h3>
    {msd_html}
  </div>

  <div style="text-align:center;margin:24px 0 16px;">
    <a href="{dashboard_safe}" style="display:inline-block;padding:10px 24px;background:#00E5CC;color:#0A0E1A;text-decoration:none;font-size:13px;font-weight:700;border-radius:8px;">대쉬보드에서 자세히 보기 →</a>
  </div>

  <div style="margin-top:24px;padding-top:16px;border-top:1px solid #1E2530;color:#4A5568;font-size:11px;text-align:center;line-height:1.6;">
    본 메일은 MA AI Dossier 의 Daily Mailing 설정에 따라 자동 발송됩니다.<br/>
    수신 거부 또는 설정 변경은 대쉬보드 → Daily Mailing 메뉴에서 가능합니다.
  </div>
</div></body></html>"""

    # Plain-text fallback
    lines = [f"[MA Dossier] {name} · {today}", ""]
    lines.append("■ 최근 MFDS 허가 (30일)")
    if approvals:
        for a in approvals:
            lines.append(f"  - {a['product']} · {a['disease']} {a['line_of_therapy']} {a['stage']} ({a['approval_date']})")
    else:
        lines.append("  - 없음")
    lines.append("")
    lines.append("■ 약가 변동 Top 5")
    if price_changes:
        lines.append(f"  ({prev} → {latest})")
        for p in price_changes:
            arrow = "▼" if p["delta"] < 0 else "▲"
            lines.append(f"  - {p['product'][:40]}: {_format_price(p['prev_price'])} → {_format_price(p['curr_price'])} {arrow}{abs(p['delta_pct']):.1f}%")
    else:
        lines.append("  - 없음")
    lines.append("")
    lines.append("■ 미디어 인텔리전스 Top 5 (Naver, 30일)")
    for b in media_top:
        lines.append(f"  - {b['brand']}: {b['total_count']}건")
    lines.append("")
    lines.append(f"■ MSD 급여 요약")
    lines.append(f"  - 품목: {msd['count']}개 (고시일 {msd.get('latest_apply_date') or '—'})")
    lines.append("")
    lines.append(f"대쉬보드: {dashboard_url}")
    body_text = "\n".join(lines)

    return subject, body_html, body_text
