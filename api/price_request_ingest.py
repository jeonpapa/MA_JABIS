"""Validate observed prices and prepare DB records, never run a remote scraper.
Client-supplied KRW/adjusted numbers are ignored. A8 conversion uses server's
existing calculator and valid KEB cache; OECD native tiers stay separate.
"""
from __future__ import annotations
import json
import math
from datetime import datetime, timezone, date
from urllib.parse import urlsplit
from agents.price_request_catalog import CURRENCIES


def positive(value, name, optional=False):
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite positive number")
    return value


def text(row, key, required=True, limit=4000):
    v = row.get(key)
    if v is None and not required:
        return None
    if not isinstance(v, str) or (required and not v.strip()) or len(v) > limit:
        raise ValueError(f"invalid {key}")
    return v.strip()


def day(value, field):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"invalid {field}")
    d = date.fromisoformat(value)
    if d > datetime.now(timezone.utc).date():
        raise ValueError(f"future {field} is not a current price")
    return d.isoformat()


def common(raw, cc):
    if not isinstance(raw, dict):
        raise ValueError("price row must be an object")
    r = {k: text(raw, k) for k in ("product_name", "ingredient", "dosage_strength", "dosage_form", "package_unit", "source_url", "source_label", "evidence_ref", "price_tier_original")}
    u = urlsplit(r["source_url"])
    if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password:
        raise ValueError("source_url must be a public source link without credentials")
    if raw.get("currency") != CURRENCIES[cc]:
        raise ValueError("wrong country currency")
    r.update(country=cc, currency=CURRENCIES[cc], local_price=positive(raw.get("local_price"), "local_price"))
    count = positive(raw.get("pack_count"), "pack_count")
    if int(count) != count or count > 100000:
        raise ValueError("pack_count must be a verified integer unit count")
    r["pack_count"] = int(count)
    observed = text(raw, "observed_at")
    try:
        dt = datetime.fromisoformat(observed.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("observation timezone required")
        if dt > datetime.now(timezone.utc):
            raise ValueError("future observation")
        # Preserve actual source observation time, not upload time.
        r["observed_at"] = dt.isoformat()
    except (TypeError, ValueError) as e:
        raise ValueError("observed_at must be a nonfuture ISO timestamp with timezone") from e
    r["effective_date"] = day(raw.get("effective_date"), "effective_date")
    if raw.get("vat_status") not in ("included", "excluded", "unknown"):
        raise ValueError("VAT status required")
    if raw.get("price_tier") not in ("ex_factory", "pharmacy_purchase", "pharmacy_retail", "reimbursement", "other"):
        raise ValueError("price tier required")
    r["price_tier"], r["vat_status"] = raw["price_tier"], raw["vat_status"]
    return r


def prepare_oecd(raw, cc):
    r = common(raw, cc)
    for key in ("ex_factory_price", "pharmacy_purchase_price", "total_pkg_mg", "reference_mg"):
        r[key] = positive(raw.get(key), key, optional=True)
    r["derivation_note"] = text(raw, "derivation_note", required=False)
    r["grade"] = raw.get("grade", "-")
    if r["grade"] not in ("A", "B", "C", "-"):
        raise ValueError("invalid derivation grade")
    if r["grade"] in ("B", "C") and not r["derivation_note"]:
        raise ValueError("derived prices require regulatory/formula evidence")
    # Direct native-tier equality is mandatory, preventing misleading alternate columns.
    if r["price_tier"] == "ex_factory" and r["vat_status"] == "excluded":
        if r["ex_factory_price"] is not None and abs(r["ex_factory_price"] - r["local_price"]) > 0.005:
            raise ValueError("direct ex-factory price mismatch")
        r["ex_factory_price"] = r["local_price"]
    if r["price_tier"] == "pharmacy_purchase" and r["vat_status"] == "excluded":
        if r["pharmacy_purchase_price"] is not None and abs(r["pharmacy_purchase_price"] - r["local_price"]) > 0.005:
            raise ValueError("direct pharmacy purchase price mismatch")
        r["pharmacy_purchase_price"] = r["local_price"]
    direct = {"ex_factory": "ex_factory_price", "pharmacy_purchase": "pharmacy_purchase_price"}.get(r["price_tier"]) if r["vat_status"] == "excluded" else None
    if any(r[k] is not None and k != direct for k in ("ex_factory_price", "pharmacy_purchase_price")) and not r["derivation_note"]:
        raise ValueError("alternate tier requires separate source or legal calculation evidence")
    r["normalized_price"] = (r["local_price"] * r["reference_mg"] / r["total_pkg_mg"] if r["reference_mg"] and r["total_pkg_mg"] else None)
    r["form_type"] = raw.get("form_type", "unknown")
    r["route"] = text(raw, "route", required=False)
    return r


def prepare_a8(raw, cc, query, rid, agent):
    r = common(raw, cc)
    source_type = raw.get("source_type")
    # Italy must use the direct manufacturer price; public-price entries are not accepted here.
    if cc == "IT" and (r["price_tier"] != "ex_factory" or r["vat_status"] != "excluded"):
        raise ValueError("IT requests require Prezzo ex-factory (IVA esclusa)")
    if cc == "US" and (text(raw, "price_tier_original").upper() != "WAC" or source_type != "redbook_wac"):
        raise ValueError("US accepts WAC only, never AWP")
    expected_sources = {"IT": "aifa_exfactory", "US": "redbook_wac", "CH": "compendium", "FR": "vidal"}
    if source_type not in (None, expected_sources.get(cc)):
        raise ValueError("unsupported source_type for country")
    if cc == "IT":
        source_type = "aifa_exfactory"
    # ── 별첨5 국가별 기준 가격 단계 (docs/foreign_price/README.md, 2026-10-27) ──────────────────────────────
    tier, vat, tier_text = r["price_tier"], r["vat_status"], (r.get("price_tier_original") or "")
    extra_in = raw.get("extra") or {}
    if cc == "DE":
        # 2026-11-02 사용자 결정: 기준 = Rote Liste **AVP (EB)/FB**. EB/FB 칸이 비어 있을 때만 AVP/UVP 를 쓰고 대체로 표시한다.
        from agents.foreign_price_agent import de_tier_is_uvp
        up = tier_text.upper()
        uses_uvp = de_tier_is_uvp(tier_text)
        uses_eb = not uses_uvp and ("EB" in up or "FB" in up)
        if not (uses_eb or uses_uvp):
            raise ValueError("DE requires Rote Liste AVP (EB)/FB (or AVP/UVP only when EB/FB is not shown)")
        if uses_uvp:
            eb_ref = extra_in.get("avp_eb_fb_reference") if isinstance(extra_in, dict) else None
            if isinstance(eb_ref, (int, float)) and not isinstance(eb_ref, bool) and eb_ref > 0:
                raise ValueError("DE: AVP (EB)/FB is shown for this pack — upload the EB/FB price, not AVP/UVP")
        source_type = None                     # 계산기: 별첨5 독일 산식(포장 약국판매가 기준)
    elif cc == "FR":
        if tier == "ex_factory":
            if vat != "excluded":
                raise ValueError("FR ex-factory (PFHT / UCD tarif HT) must be VAT excluded")
            source_type = None                 # PFHT × 0.77
        else:
            source_type = "vidal"              # 공장도가 미확인 대체: 공중가 × 0.65 (별첨5 각주1)
    elif cc == "CH":
        has_fap = isinstance(extra_in, dict) and isinstance(extra_in.get("ex_factory_price"), (int, float)) \
            and not isinstance(extra_in.get("ex_factory_price"), bool) and extra_in["ex_factory_price"] > 0
        if tier == "ex_factory":
            if vat != "excluded":
                raise ValueError("CH SL ex-factory (FAP) must be VAT excluded")
            source_type = None                 # SL FAP × 0.73
        elif not has_fap:
            source_type = "compendium"         # SL 약가 미확인 대체: Compendium × 0.65 (별첨5 각주2)
        else:
            source_type = "compendium"         # 판매가 + extra.ex_factory_price → 계산기가 FAP × 0.73 사용
    if raw.get("form_type") not in ("oral", "injection", "unknown"):
        raise ValueError("form_type required")
    observed = r.pop("observed_at")
    metadata = {k: r.pop(k) for k in ("price_tier", "price_tier_original", "vat_status", "effective_date", "evidence_ref")}
    extra = raw.get("extra") or {}
    if not isinstance(extra, dict):
        raise ValueError("extra must be an object")
    extra = {**extra, **metadata, "observed_at": observed, "price_request_id": rid, "source_type": source_type}
    if cc == "DE":
        from agents.foreign_price_agent import de_price_basis_warning
        extra["price_column"] = "AVP/UVP" if uses_uvp else "AVP (EB)/FB"
        warn = de_price_basis_warning({"extra": extra})
        if warn:
            extra["price_basis_warning"] = warn
    r.update(query_name=query, searched_at=datetime.now().isoformat(timespec="microseconds"),
             raw_data=json.dumps(extra, ensure_ascii=False, allow_nan=False),
             form_type=raw["form_type"], pack_count_source="scraper", source_type=source_type)
    # Never trust upload-supplied FX or adjusted figures.
    r.update({k: None for k in ("exchange_rate", "exchange_rate_from", "exchange_rate_to", "factory_price_krw", "vat_rate", "distribution_margin", "adjusted_price_krw", "daily_cost_krw", "daily_cost_note", "total_pkg_mg", "daily_dose_mg")})
    try:
        rate = agent.rate_fetcher.get_36m_average(CURRENCIES[cc], None)
    except Exception as exc:
        # Missing FX is not missing native price. Never substitute an invented rate.
        rate = None
        extra["fx_status"] = "unavailable"
        extra["fx_error_class"] = type(exc).__name__
        r["raw_data"] = json.dumps(extra, ensure_ascii=False, allow_nan=False)
    if rate:
        from agents.foreign_price_agent import official_exfactory_price
        calc = agent.calculator.calculate_adjusted_price(country=cc, listed_price=r["local_price"], exchange_rate=rate["rate"], pack_count=r["pack_count"], source_type=source_type,
                                                         exfactory_price=official_exfactory_price(cc, {"extra": extra}))
        for key in ("factory_price_krw", "vat_rate", "distribution_margin", "adjusted_price_krw", "pack_count"):
            r[key] = calc[key]
        r.update(exchange_rate=rate["rate"], exchange_rate_from=rate["from_month"], exchange_rate_to=rate["to_month"])
    r["per_unit_local"] = r["local_price"] / r["pack_count"]
    r["unit_strength_mg"] = agent._extract_per_unit_mg(r["form_type"], r["dosage_strength"], r["package_unit"], product_name=r["product_name"], dosage_form=r["dosage_form"])
    # Same guard as the scraper pipeline: an implausible vial volume (e.g. "Inj Sol-240 mL Vial") is not a per-vial mg.
    if r["form_type"] == "injection" and hasattr(agent, "_injection_unit_ambiguity"):
        ambiguity = agent._injection_unit_ambiguity(r["dosage_strength"], r["package_unit"], product_name=r["product_name"], dosage_form=r["dosage_form"])
        if ambiguity:
            r["unit_strength_ambiguous"] = ambiguity
            r["unit_strength_mg"] = None
    # These functions consume the same form-aware denominator as the native scraper pipeline.
    dosing_map = agent._load_dosing_map(query, [r["ingredient"]])
    dosing = agent._match_dosing(query, r["ingredient"], dosing_map)
    agent._populate_daily_cost(r, dosing)
    # Deterministic no-LLM formulation key, keeping route/strength distinct.
    from agents.foreign_dose_normalize import process_formulations
    process_formulations(query, [r])
    return r


def de_rows_from_scraper_items(items):
    """서버 Rote Liste 수집기 포장 행 → 워커 upload 형식(2026-11-06). 가격·포장 수량이 없는 포장은 skipped 로."""
    from datetime import timezone as _tz
    observed = datetime.now(_tz.utc).isoformat()
    rows, skipped = [], []
    for it in items:
        ex = it.get("extra") or {}
        price, pc = it.get("local_price"), it.get("pack_count")
        if not price or not pc:
            skipped.append({"package_unit": it.get("package_unit"), "reason": "가격 또는 포장 수량 미확인"})
            continue
        fallback = ex.get("price_column") == "AVP/UVP"
        extra = {"pzn": ex.get("pzn"), "eb_label": ex.get("eb_label"), "avp_uvp_reference": ex.get("avp_uvp_reference"),
                 "price_as_of": ex.get("price_as_of"), "server_collected": True}
        if not fallback:
            extra["avp_eb_fb_reference"] = price
        import re as _re
        # 수집기 표기 정리 — 업로드 검증(필수 텍스트 칸) 통과용. 가격·포장·단계는 손대지 않는다.
        pname = _re.sub(r"\s+", " ", _re.sub(r"[▼®™]", "", it.get("product_name") or "")).strip()
        ingr = _re.split(r"\s+Rote Liste|\s+Fachinformation|&#", it.get("ingredient") or "")[0].strip()
        m_str = _re.search(r"\d+(?:[.,]\d+)?\s*(?:mg|g|µg|mcg)\b", pname, _re.I)
        strength = m_str.group(0) if m_str else (it.get("dosage_strength") or "")
        form = (it.get("dosage_form") or "").strip()
        if not form:
            m_form = _re.search(r"\d\s*(?:mg|g|µg|mcg)\s+(.+)$", pname, _re.I)
            form = (m_form.group(1).strip() if m_form else "") or "Rote Liste Packungsangaben"
        rows.append({
            "product_name": pname, "ingredient": ingr or pname,
            "dosage_strength": strength, "dosage_form": form,
            "package_unit": it.get("package_unit") or "", "source_url": it.get("source_url") or "",
            "source_label": "Rote Liste (AVP (EB)/FB; DocCheck, 서버 수집)" if not fallback
                            else "Rote Liste (AVP/UVP 대체; DocCheck, 서버 수집)",
            "evidence_ref": f"server-scrape:rote_liste:PZN {ex.get('pzn') or '-'}",
            "price_tier_original": ex.get("price_tier_original") or "", "price_tier": ex.get("price_tier") or "pharmacy_retail",
            "vat_status": ex.get("vat_status") or "included", "currency": "EUR", "local_price": price,
            "pack_count": int(pc), "observed_at": observed, "effective_date": None, "extra": extra,
        })
    return rows, skipped


# ── 서버 수집기 결과 → 업로드 행 (2026-11-10 사용자 결정: JP·US·CA 서버 수집) ─────────────────────────────
# 수집기 run() 레코드를 워커 upload 형식으로 바꾼다. 같은 prepare_a8 검증을 거친다(저장은 워커 업로드 때).
# 포장 수는 ForeignPriceAgent._resolve_pack_count(per_unit·label·parsed)로 정하고, 근거 없는 경구·미상 행은 넣지 않는다.
SERVER_TIERS = {
    "JP": ("pharmacy_retail", "薬価 (1単位, 厚生労働省 薬価基準)", "unknown", None),
    "CA": ("reimbursement", "Drug Benefit Price (DBP) per unit, Ontario", "unknown", None),
    "US": ("ex_factory", "WAC", "unknown", "redbook_wac"),
}


def rows_from_scraper_records(cc, records, agent):
    import json as _json
    from datetime import timezone as _tz
    tier, tier_text, vat, stype = SERVER_TIERS[cc]
    observed = datetime.now(_tz.utc).isoformat()
    rows, skipped = [], []
    for rec in records or []:
        price = rec.get("local_price")
        if not price or price <= 0:
            skipped.append({"product_name": rec.get("product_name"), "reason": "가격 없음"})
            continue
        try:
            extra = _json.loads(rec.get("raw_data") or "{}") if isinstance(rec.get("raw_data"), str) else (rec.get("raw_data") or {})
        except ValueError:
            extra = {}
        item = dict(rec)
        item.pop("pack_count", None)
        item.pop("id", None)
        pc = agent._resolve_pack_count(cc, item, {})
        src = item.get("pack_count_source")
        form = (rec.get("form_type") or "unknown").lower()
        if src == "default" and form != "injection":
            skipped.append({"product_name": rec.get("product_name"), "package_unit": rec.get("package_unit"),
                            "reason": "포장 수 근거 없음(경구·미상) — 브라우저 확인"})
            continue
        if cc == "US" and stype and (rec.get("source_type") or extra.get("source_type") or stype) != stype:
            skipped.append({"product_name": rec.get("product_name"), "reason": "WAC 아님"})
            continue
        unit_note = {"per_unit": "1 unit (단가 체계)", "label": "라벨 확인 포장", "parsed": "", "default": "1 vial (주사 단일 바이알 가정)"}.get(src, "")
        url = rec.get("source_url") or ""
        if not url.startswith(("http://", "https://")):
            url = {"JP": "https://www.mhlw.go.jp/topics/2024/04/tp20240401-01.html", "CA": "https://www.formulary.health.gov.on.ca/formulary/",
                   "US": "https://www.micromedexsolutions.com/"}[cc]
        rows.append({
            "product_name": rec.get("product_name") or "-", "ingredient": rec.get("ingredient") or rec.get("product_name") or "-",
            "dosage_strength": rec.get("dosage_strength") or "-", "dosage_form": rec.get("dosage_form") or "-",
            "package_unit": rec.get("package_unit") or unit_note or "-", "pack_count": int(pc),
            "local_price": float(price), "currency": rec.get("currency") or CURRENCIES[cc],
            "price_tier": tier, "price_tier_original": tier_text, "vat_status": vat,
            "effective_date": extra.get("effective_date") if isinstance(extra.get("effective_date"), str)
                              and __import__("re").fullmatch(r"\d{4}-\d{2}-\d{2}", extra.get("effective_date"))
                              and extra.get("effective_date") <= observed[:10] else None,
            "observed_at": observed, "source_url": url, "source_label": rec.get("source_label") or cc,
            "evidence_ref": f"server-scrape:{cc}:{rec.get('source_label') or ''}"[:300], "form_type": form if form in ("oral", "injection") else "unknown",
            **({"source_type": stype} if stype else {}),
            "extra": {**{k: v for k, v in extra.items() if k not in ("price_tier", "price_tier_original", "vat_status", "evidence_ref")},
                      "pack_count_source": src, "server_collected": True},
        })
    return rows, skipped
