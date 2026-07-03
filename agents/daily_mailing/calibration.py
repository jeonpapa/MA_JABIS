from __future__ import annotations

from pathlib import Path

CALIBRATION_SYSTEM_PROMPT = """
You are the MA Daily Intelligence Writer Agent for Korean pharma Market Access.
Use forwarded monitoring emails and BioSpectator newsletters only as calibration references: tone, density,
section rhythm, and implication style. Do not copy their prose, and do not treat newsletter links as live
sendable candidates unless independently verified from publisher/official sources.

For each article card, write in Korean for a senior Market Access audience. The output must be article-specific:
- 2-3 fact bullets grounded in the source title/body
- Market Access Insight: payer/access/pricing/HTA/reimbursement consequence
- Korea MA Implication only when defensible; omit when weak
- MSD/Product Watchpoint only when directly relevant to MSD asset, competitor, disease area, or access strategy
- Source caveat and confidence
Avoid generic boilerplate. Mention concrete nouns: product, patient group, committee step, price/claim amount,
comparator, milestone, or access criterion.
""".strip()

ARTICLE_CARD_SCHEMA = {
    "title": "string",
    "source": "string",
    "published_at": "ISO-8601",
    "url": "string",
    "category": "Top Signal|Watchlist",
    "summary_bullets": ["2-3 source-grounded facts"],
    "market_access_insight": "article-specific MA interpretation",
    "korea_ma_implication": "optional; omit/empty if not defensible",
    "msd_watchpoint": "optional",
    "confidence": "high|medium|low",
    "verification_status": "official_verified|publisher_verified|needs_review",
}

def write_calibration_template(path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "# MA Daily Mailing Writer Agent Calibration\n\n"
        "## System prompt\n\n" + CALIBRATION_SYSTEM_PROMPT + "\n\n"
        "## Article card schema\n\n```json\n" + str(ARTICLE_CARD_SCHEMA).replace("'", '"') + "\n```\n\n"
        "## Calibration inputs to paste/save\n\n"
        "- BioSpectator newsletter examples: tone and implication style only\n"
        "- Current monitoring scope emails: topic/source/coverage calibration\n"
        "- Do not copy proprietary wording into final outputs.\n",
        encoding="utf-8",
    )
    return out
