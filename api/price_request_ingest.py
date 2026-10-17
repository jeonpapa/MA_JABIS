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
    if raw.get("form_type") not in ("oral", "injection", "unknown"):
        raise ValueError("form_type required")
    observed = r.pop("observed_at")
    metadata = {k: r.pop(k) for k in ("price_tier", "price_tier_original", "vat_status", "effective_date", "evidence_ref")}
    extra = raw.get("extra") or {}
    if not isinstance(extra, dict):
        raise ValueError("extra must be an object")
    extra = {**extra, **metadata, "observed_at": observed, "price_request_id": rid, "source_type": source_type}
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
        calc = agent.calculator.calculate_adjusted_price(country=cc, listed_price=r["local_price"], exchange_rate=rate["rate"], pack_count=r["pack_count"], source_type=source_type)
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
