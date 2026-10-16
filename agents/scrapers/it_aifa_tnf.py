"""AIFA TrovaNorme: direct public ex-factory prices from determina PDFs.

Verified ricerca API uses pageNum/pageSize and dataPubblicazione descending.
Prices are per pack, IVA esclusa; pubblico is evidence only, never a fallback.
"""
import io
import json
import logging
import re
from datetime import date, datetime
from pathlib import Path

import requests

from .base import BaseScraper

logger = logging.getLogger(__name__)
TNF_SERVICE = "https://trovanorme.aifa.gov.it/tnf-service/"
TNF_PORTAL = "https://trovanorme.aifa.gov.it/"
SEARCH_PAGE_SIZE = 20
REQ_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


class TnfPdfParseError(RuntimeError):
    """Unreadable/scanned or structurally unparsed PDF, not zero results."""


def _date_value(value):
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except (ValueError, TypeError):
        return None


def _it_price(raw):
    try:
        value = float((raw or "").replace("\xa0", "").replace(" ", "").replace(".", "").replace(",", "."))
        return value if value > 0 else None
    except ValueError:
        return None


def _extract_pack_count(desc):
    match = re.search(r"(\d+)\s*(?:\([^)]*\)\s*)?(?:compresse|capsule|capsula|compressa|flaconcin[oi]|fiale|fiala|siringhe|siringa|penne|penna|bustine|bustina|cerotti)\b", desc or "", re.I)
    return int(match.group(1)) if match else None


class ItAifaTnfScraper(BaseScraper):
    COUNTRY = "IT"
    CURRENCY = "EUR"
    SOURCE_LABEL = "AIFA TrovaNorme (Prezzo ex-factory IVA esclusa)"
    SOURCE_TYPE = "aifa_exfactory"
    REQUIRES_LOGIN = False

    def __init__(self, cache_dir: Path = None, msd_only: bool = False, **kwargs):
        super().__init__(**kwargs)
        self.cache_dir = cache_dir or Path("data/foreign/it")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.msd_only = msd_only
        self._session = requests.Session()
        self._session.headers.update(REQ_HEADERS)
        self._session.verify = False
        try:
            requests.packages.urllib3.disable_warnings()
        except Exception:
            pass

    def _api_json(self, path, **params):
        response = self._session.get(TNF_SERVICE + path, params={"determinaTNFSource": "true", **params}, timeout=30)
        response.raise_for_status()
        return response.json()

    def _search_determine(self, query):
        rows, seen, page_num = [], set(), 0
        while True:
            payload = self._api_json("ricerca/", parola=query, pageNum=page_num,
                                     pageSize=SEARCH_PAGE_SIZE, sortColumn="dataPubblicazione", sortOrder="desc")
            batch = payload.get("resourceList")
            if not isinstance(batch, list):
                raise RuntimeError("IT-TNF search: missing resourceList")
            if not batch:
                break
            fresh = [r for r in batch if r.get("id") not in seen]
            if not fresh:
                raise RuntimeError("IT-TNF search: pagination did not advance")
            rows.extend(fresh)
            seen.update(r.get("id") for r in fresh)
            total = payload.get("totalElementNum")
            if (total is not None and len(rows) >= int(total)) or len(batch) < SEARCH_PAGE_SIZE:
                break
            page_num += 1
        return sorted(rows, key=lambda r: (r.get("dataPubblicazione") or "", r.get("data") or ""), reverse=True)

    def _detail(self, det_id):
        return self._api_json(f"determina/tnf/pubblicate/{det_id}")

    def _download_allegato(self, allegato_id):
        response = self._session.get(TNF_SERVICE + f"determina/tnf/pubblicate/allegato/{allegato_id}",
                                     headers={"Accept": "application/pdf,application/octet-stream,*/*"}, timeout=45)
        response.raise_for_status()
        return response.content

    @staticmethod
    def _matching_farms(detail, query):
        q = (query or "").strip()
        if not q:
            return []
        pattern = re.compile(r"(?<!\w)" + re.escape(q) + r"(?!\w)", re.I)
        return [f for f in detail.get("datiFarmaco", []) or []
                if any(pattern.search(str(n or "")) for n in
                       [f.get("denominazione"), *(f.get("principiAttivi") or [])])]

    @staticmethod
    def _matches_drug(detail, query):
        return bool(ItAifaTnfScraper._matching_farms(detail, query))

    @staticmethod
    def _parse_text_pages(pages):
        if not any((p or "").strip() for p in pages):
            raise TnfPdfParseError("IT-TNF PDF has no extractable text (scanned/OCR required)")
        txt, ranges = "", []
        for number, text in enumerate(pages, 1):
            start = len(txt)
            txt += (text or "") + "\n"
            ranges.append((number, start, len(txt)))
        starts = list(re.finditer(r"\bConfezione\b", txt, re.I))
        rows = []
        for index, start in enumerate(starts):
            end = starts[index + 1].start() if index + 1 < len(starts) else len(txt)
            block = txt[start.start():end]
            exf = re.search(r"Prezzo\s+ex[\s–-]*factory\s*\(\s*IVA\s+esclusa\s*\)\s*[:=]?\s*€\s*([\d.]+,\d{2})", block, re.I)
            if not exf:
                continue
            aic = re.search(r"\bA\.?I\.?C\.?\s*(?:n\.?\s*)?[:]?\s*(\d{9})\b", block, re.I)
            if not aic:
                raise TnfPdfParseError("IT-TNF priced confezione has no valid AIC")
            desc = re.sub(r"\s+", " ", block[len(start.group()):aic.start()]).strip(' :"“”')
            cls = re.search(r"Classe di rimborsabilit[aà]\s*[:]?\s*([A-CH]{1,2})\b", block, re.I)
            pub = re.search(r"Prezzo\s+al\s+pubblico\s*\(\s*IVA\s+inclusa\s*\)\s*[:=]?\s*€\s*([\d.]+,\d{2})", block, re.I)
            price = _it_price(exf.group(1))
            if price is None:
                continue
            evidence_end = pub.end() if pub else exf.end()
            rows.append({"desc": desc, "aic": aic.group(1),
                         "classe": cls.group(1).upper() if cls else "",
                         "ex_factory": price, "pubblico": _it_price(pub.group(1)) if pub else None,
                         "pack": _extract_pack_count(desc),
                         "pages": [n for n, lo, hi in ranges if lo < start.start() + evidence_end and hi > start.start()],
                         "evidence_text": block[:evidence_end].strip()})
        if not rows and re.search(r"Prezzo\s+ex[\s–-]*factory", txt, re.I):
            raise TnfPdfParseError("IT-TNF ex-factory label found but price/pack could not be parsed")
        return rows

    @staticmethod
    def _parse_confezioni(pdf_bytes):
        try:
            import pdfplumber
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as doc:
                pages = [p.extract_text() or "" for p in doc.pages]
        except Exception as exc:
            raise TnfPdfParseError(f"IT-TNF PDF extraction failed: {exc}") from exc
        return ItAifaTnfScraper._parse_text_pages(pages)

    async def search(self, query, page=None):
        self.warnings = []
        determine = self._search_determine(query)
        today = date.today().isoformat()
        details, failures = [], []
        for item in determine:
            try:
                detail = self._detail(item["id"])
            except Exception as exc:
                failures.append(exc)
                continue
            if self._matches_drug(detail, query):
                # Rectifications may have non-price tipiAtto; inspect all matching PDFs.
                details.append((item, detail))
        details.sort(key=lambda pair: (pair[1].get("dataPubblicazione") or pair[0].get("dataPubblicazione") or "",
                                       pair[1].get("data") or pair[0].get("data") or ""), reverse=True)
        results, seen = [], set()
        for item, detail in details:
            publication = _date_value(detail.get("dataPubblicazione") or item.get("dataPubblicazione"))
            effective = _date_value(detail.get("dataEfficacia"))
            document = _date_value(detail.get("data") or item.get("data"))
            if (effective and effective > today) or (publication and publication > today):
                continue
            farms = self._matching_farms(detail, query)
            attachments = detail.get("allegati") or []
            if not attachments:
                failures.append(RuntimeError(f"IT-TNF missing PDF: {item['id']}"))
                continue
            for attachment in sorted(attachments, key=lambda a: not a.get("principale", False)):
                pdf_url = TNF_SERVICE + f"determina/tnf/pubblicate/allegato/{attachment['id']}"
                try:
                    confs = self._parse_confezioni(self._download_allegato(attachment["id"]))
                except Exception as exc:
                    failures.append(exc)
                    continue
                for conf in confs:
                    if conf.get("ex_factory") is None or conf["ex_factory"] <= 0:
                        continue
                    owners = [f for f in farms if (f.get("aic9") and conf["aic"] == str(f["aic9"]))
                              or (f.get("aic6") and conf["aic"].startswith(str(f["aic6"])))]
                    if not owners:
                        if len(detail.get("datiFarmaco") or []) != 1:
                            continue
                        owner = farms[0]
                        if owner.get("aic6") or owner.get("aic9"):
                            continue
                    else:
                        owner = owners[0]
                    brand = owner.get("denominazione") or query
                    key = (brand.casefold(), conf["aic"])
                    if key in seen:
                        continue
                    seen.add(key)
                    results.append({
                        "product_name": brand, "ingredient": ", ".join(owner.get("principiAttivi") or []),
                        "dosage_strength": conf["desc"], "dosage_form": conf["desc"],
                        "package_unit": str(conf["pack"]) if conf["pack"] else "",
                        "local_price": conf["ex_factory"], "pack_count": conf["pack"],
                        "source_type": self.SOURCE_TYPE, "source_url": pdf_url,
                        "extra": {"channel": "tnf_determina", "aic": conf["aic"], "classe": conf["classe"],
                                  "price_kind": "Prezzo ex-factory (IVA esclusa)",
                                  "prezzo_ex_factory": conf["ex_factory"], "prezzo_pubblico": conf["pubblico"],
                                  "determina": detail.get("numeroRepertorio") or item.get("titolo", ""),
                                  "determina_id": item["id"], "determina_date": document,
                                  "document_date": document, "publication_date": publication,
                                  "effective_date": effective,
                                  "effective_date_status": "reported" if effective else "not_reported",
                                  "pdf_url": pdf_url, "attachment_id": attachment["id"],
                                  "evidence_pages": conf.get("pages", []), "evidence_text": conf.get("evidence_text", ""),
                                  "detail_url": f"{TNF_PORTAL}#/dettaglio/tnf/{item['id']}",
                                  "atc": owner.get("atcs") or [], "source_type": self.SOURCE_TYPE}})
        if failures:
            if not results:
                raise failures[0]
            self.warnings.append({"warning_class": "channel_failed", "channel": "tnf_determina",
                                  "message": "; ".join(str(e) for e in failures)})
        return results

    async def refresh(self, _page=None):
        logger.info("[IT-TNF] Public API, no cache")

    async def run(self, query):
        searched_at = datetime.now().isoformat()
        results = []
        for item in await self.search(query):
            result = {key: item.get(key) for key in ("product_name", "ingredient", "dosage_strength", "dosage_form",
                                                     "package_unit", "local_price", "pack_count", "source_url")}
            result.update(searched_at=searched_at, query_name=query, country=self.COUNTRY, currency=self.CURRENCY,
                          source_label=self.SOURCE_LABEL, source_type=self.SOURCE_TYPE,
                          raw_data=json.dumps(item.get("extra", {}), ensure_ascii=False),
                          form_type=self._resolve_form_type(item))
            result.update({key: None for key in ("exchange_rate", "exchange_rate_from", "exchange_rate_to", "factory_price_krw",
                                                 "vat_rate", "distribution_margin", "adjusted_price_krw")})
            results.append(result)
        return results
