"""
이탈리아 AIFA — TrovaNormeFarmaco (TNF) 결정문 채널

기존 `it_aifa.py`(AIFA Class A/H CSV) 와 **병행**하는 신규 IT 가격 소스.
CSV 는 등재 리스트 스냅샷인 반면, TNF 는 약가·급여 **결정문(determina) 원문**에서
성분·제형·가격을 직접 확인한다 ("art. 8, comma 10, legge 537/1993" 분류·급여 결정).

파이프라인 (전량 공개 HTTP JSON API — 브라우저·로그인 불필요):
  1. 검색:  GET tnf-service/determina/tnf/parametrata?determinaTNFSource=true&parola={약제}
            → 결정문 목록 (parola = 자유텍스트 필터. dF/pA 축약은 서버가 무시함)
  2. 상세:  GET tnf-service/determina/tnf/pubblicate/{id}
            → datiFarmaco[{denominazione, principiAttivi, atcs, aic6}] + allegati[{id, principale}]
            + tipiAtto[{codice:"A_H"=급여분류(A-H) 결정}]  ← 가격 결정문 식별
  3. 첨부:  GET tnf-service/determina/tnf/pubblicate/allegato/{allegatoId}  → 결정문 PDF
  4. 파싱:  confezione 블록별 {제형·함량·경로, AIC, Classe, Prezzo ex-factory, Prezzo al pubblico}

가격 기준: **Prezzo ex-factory (IVA esclusa)** 를 사용 → SOURCE_TYPE="aifa_exfactory"
           (factory_ratio=1.0, 이미 공장도가). 결정문은 Classe 무관 ex-factory 를 명시하므로
           기존 CSV Class H 로직과 동일 A8 기준으로 정합.

로그인 불필요. AIFA 서버 인증서 체인이 불완전해 requests(verify=False) 사용
 (기존 it_aifa.py 도 동일 도메인, 공개 gov 문서).
"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path

import requests

from .base import BaseScraper

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────────────────
# 상수
# ────────────────────────────────────────────────────────────────────────────

TNF_SERVICE = "https://trovanorme.aifa.gov.it/tnf-service/"
TNF_PORTAL  = "https://trovanorme.aifa.gov.it/"

REQ_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

# tipiAtto.codice — 가격/급여 결정문 (art.8 c.10)
PRICE_ATTO_CODES = {"A_H"}  # "Modifica Classe di Rimborsabilità (classi A-H)"
# titolo 보조 필터 (결정문 유형 문구)
PRICE_TITLE_KW = ("rimborsabilit", "prezzo", "classificazione", "rinegoziazione")

# 검색당 확인할 결정문 상세 최대 건수 (parola 필터가 이미 좁혀줌)
MAX_DETERMINE_CHECK = 8


def _it_price(raw: str):
    """이탈리아 통화 표기 → float. '16.066,49' → 16066.49 (dot=천, comma=소수)."""
    s = (raw or "").strip().replace("\xa0", "").replace(" ", "")
    if not s:
        return None
    s = s.replace(".", "").replace(",", ".")
    try:
        v = float(s)
        return v if v > 0 else None
    except ValueError:
        return None


def _extract_pack_count(desc: str):
    """confezione 설명에서 포장 수량 추출.
    '... Blister 30 compresse' → 30, '3 flaconcini' → 3. 없으면 None."""
    if not desc:
        return None
    # "90 (3 x 30) compresse" → 90 (총량), "30 compresse" → 30, "1 flaconcino" → 1.
    # 수량 뒤 멀티팩 괄호(옵션)를 건너뛰고 제형 단위어와 매칭.
    m = re.search(
        r"(\d+)\s*(?:\([^)]*\)\s*)?"
        r"(?:compresse|capsule|capsula|compressa|flaconcin[oi]|"
        r"fiale|fiala|siringhe|siringa|penne|penna|bustine|bustina|cerotti)",
        desc, re.I,
    )
    return int(m.group(1)) if m else None


class ItAifaTnfScraper(BaseScraper):
    COUNTRY        = "IT"
    CURRENCY       = "EUR"
    SOURCE_TYPE    = "aifa_exfactory"   # ex-factory → factory_ratio 1.0
    SOURCE_LABEL   = "AIFA TrovaNorme (Determina prezzo, ex-factory)"
    REQUIRES_LOGIN = False

    def __init__(self, cache_dir: Path = None, msd_only: bool = False, **kwargs):
        super().__init__(**kwargs)
        self.cache_dir = cache_dir or Path("data/foreign/it")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.msd_only = msd_only
        self._session = requests.Session()
        self._session.headers.update(REQ_HEADERS)
        self._session.verify = False
        try:  # AIFA cert 체인 경고 억제
            requests.packages.urllib3.disable_warnings()
        except Exception:
            pass

    async def login(self, page=None) -> None:
        pass

    async def logout(self, page=None) -> None:
        pass

    # ── API helpers ─────────────────────────────────────────────────────────

    def _api_json(self, path: str, **params) -> dict:
        p = {"determinaTNFSource": "true", **params}
        r = self._session.get(TNF_SERVICE + path, params=p, timeout=30)
        r.raise_for_status()
        return r.json()

    def _search_determine(self, query: str) -> list[dict]:
        """parola 자유텍스트로 결정문 목록 조회 (최신순)."""
        try:
            j = self._api_json(
                "determina/tnf/parametrata",
                parola=query, page=0, size=MAX_DETERMINE_CHECK,
            )
            return j.get("resourceList", []) or []
        except Exception as e:
            logger.warning("[IT-TNF] 검색 실패 '%s': %s", query, e)
            return []

    def _detail(self, det_id: str) -> dict:
        return self._api_json(f"determina/tnf/pubblicate/{det_id}")

    def _download_allegato(self, allegato_id) -> bytes:
        # 세션 기본 Accept=application/json 이면 PDF 엔드포인트가 500 → binary Accept 로 override
        r = self._session.get(
            TNF_SERVICE + f"determina/tnf/pubblicate/allegato/{allegato_id}",
            headers={"Accept": "application/pdf,application/octet-stream,*/*"},
            timeout=45,
        )
        r.raise_for_status()
        return r.content

    # ── 결정문 선택 ───────────────────────────────────────────────────────────

    @staticmethod
    def _is_price_determina(detail: dict) -> bool:
        codes = {a.get("codice") for a in (detail.get("tipiAtto") or [])}
        if codes & PRICE_ATTO_CODES:
            return True
        title = (detail.get("titolo") or "").lower()
        return any(k in title for k in PRICE_TITLE_KW)

    @staticmethod
    def _matches_drug(detail: dict, query: str) -> bool:
        q = (query or "").strip().lower()
        for f in (detail.get("datiFarmaco") or []):
            if q in (f.get("denominazione") or "").lower():
                return True
            for pa in (f.get("principiAttivi") or []):
                if q in (pa or "").lower():
                    return True
        return False

    # ── PDF 파싱 ──────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_confezioni(pdf_bytes: bytes) -> list[dict]:
        """결정문 PDF → confezione 별 {desc, aic, classe, ex_factory, pubblico, pack}."""
        import io
        try:
            import pdfplumber
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as doc:
                txt = "\n".join((p.extract_text() or "") for p in doc.pages)
        except Exception as e:
            logger.warning("[IT-TNF] PDF 파싱 실패: %s", e)
            return []

        rows = []
        # "Confezione" 로 블록 분할 후 순차 필드 추출
        for block in re.split(r"(?=Confezione)", txt):
            if not block.startswith("Confezione"):
                continue
            exf = re.search(r"ex.?factory[^€]*€\s?([\d\.]+,\d+)", block, re.I)
            if not exf:
                continue  # 가격 없는 confezione (Classe C 등) skip
            # desc = "Confezione" ~ "AIC" 사이 전체 (포장 수량 포함)
            desc = re.search(r'Confezione\s*[:“"]?\s*(.+?)\s+AIC\s*n\.', block, re.S | re.I)
            aic = re.search(r"AIC\s*n\.\s*([\d]+/?\w?)", block)
            cls = re.search(r"Classe di rimborsabilit[aà]\s*([A-CH]{1,2})", block, re.I)
            pub = re.search(r"pubblico[^€]*€\s?([\d\.]+,\d+)", block, re.I)
            desc_s = re.sub(r"\s+", " ", desc.group(1)).strip('"“” ') if desc else ""
            rows.append({
                "desc":       desc_s[:120],
                "aic":        aic.group(1) if aic else "",
                "classe":     (cls.group(1).upper() if cls else ""),
                "ex_factory": _it_price(exf.group(1)),
                "pubblico":   _it_price(pub.group(1)) if pub else None,
                "pack":       _extract_pack_count(desc_s),
            })
        return rows

    # ── 메인 검색 ─────────────────────────────────────────────────────────────

    async def search(self, query: str, page=None) -> list[dict]:
        determine = self._search_determine(query)
        if not determine:
            logger.info("[IT-TNF] '%s' 결정문 없음", query)
            return []

        # 상세 확인 → 가격 결정문 + 약제 매칭. 최신순으로 첫 매칭 채택.
        chosen = None
        chosen_detail = None
        for it in determine:
            try:
                d = self._detail(it["id"])
            except Exception as e:
                logger.debug("[IT-TNF] 상세 실패 %s: %s", it.get("id"), e)
                continue
            if not self._is_price_determina(d):
                continue
            if self._matches_drug(d, query):
                chosen, chosen_detail = it, d
                break
            if chosen is None:  # 매칭 없으면 최신 가격결정문 fallback 후보
                chosen, chosen_detail = it, d

        if not chosen_detail:
            logger.info("[IT-TNF] '%s' 가격 결정문 없음", query)
            return []

        # datiFarmaco 에서 성분·브랜드
        farms = chosen_detail.get("datiFarmaco") or []
        brand = farms[0].get("denominazione") if farms else query
        ings = sorted({pa for f in farms for pa in (f.get("principiAttivi") or [])})
        ingredient = ", ".join(ings) if ings else ""
        det_no = chosen_detail.get("numeroRepertorio") or chosen.get("titolo", "")
        det_date = (chosen.get("dataPubblicazione") or "")[:10]

        allegati = [a for a in (chosen_detail.get("allegati") or [])]
        principale = next((a for a in allegati if a.get("principale")), allegati[0] if allegati else None)
        if not principale:
            logger.info("[IT-TNF] '%s' 첨부 PDF 없음 (%s)", query, det_no)
            return []

        try:
            pdf = self._download_allegato(principale["id"])
        except Exception as e:
            logger.warning("[IT-TNF] '%s' 첨부 다운로드 실패: %s", query, e)
            return []

        confs = self._parse_confezioni(pdf)
        if not confs:
            logger.info("[IT-TNF] '%s' confezione 가격 없음 (%s)", query, det_no)
            return []

        src_url = f"{TNF_PORTAL}#/dettaglio/tnf/{chosen['id']}"
        results = []
        for c in confs:
            results.append({
                "product_name":    brand,
                "ingredient":      ingredient,
                "dosage_strength": c["desc"],
                "dosage_form":     c["desc"],
                "package_unit":    (f"{c['pack']}" if c["pack"] else ""),
                "local_price":     c["ex_factory"],
                "pack_count":      c["pack"],       # 명시 → 에이전트 per-unit 환산 최우선
                "source_url":      src_url,
                "extra": {
                    "channel":        "tnf_determina",
                    "aic":            c["aic"],
                    "classe":         c["classe"],
                    "price_kind":     "ex-factory",
                    "prezzo_pubblico": c["pubblico"],
                    "determina":      det_no,
                    "determina_date": det_date,
                    "atc":            sorted({a for f in farms for a in (f.get("atcs") or [])}),
                    "source_type":    self.SOURCE_TYPE,
                },
            })
        logger.info("[IT-TNF] '%s' → %s (%s): confezione %d건",
                    query, brand, det_no, len(results))
        return results

    async def refresh(self, _page=None) -> None:
        logger.info("[IT-TNF] 공개 API 기반 — 캐시 없음")

    # ── run() 오버라이드 — Playwright 없이 requests ──────────────────────────

    async def run(self, query: str) -> list[dict]:
        logger.info("[IT-TNF] TrovaNorme 검색 시작: '%s'", query)
        searched_at = datetime.now().isoformat()
        raw = await self.search(query)
        results = []
        for item in raw:
            form_type = self._resolve_form_type(item)
            results.append({
                "searched_at":         searched_at,
                "query_name":          query,
                "country":             self.COUNTRY,
                "product_name":        item.get("product_name", ""),
                "ingredient":          item.get("ingredient", ""),
                "dosage_strength":     item.get("dosage_strength", ""),
                "dosage_form":         item.get("dosage_form", ""),
                "package_unit":        item.get("package_unit", ""),
                "local_price":         item.get("local_price"),
                "currency":            self.CURRENCY,
                "exchange_rate":       None,
                "exchange_rate_from":  None,
                "exchange_rate_to":    None,
                "factory_price_krw":   None,
                "vat_rate":            None,
                "distribution_margin": None,
                "adjusted_price_krw":  None,
                "pack_count":          item.get("pack_count"),
                "source_url":          item.get("source_url", ""),
                "source_label":        self.SOURCE_LABEL,
                "raw_data":            json.dumps(item.get("extra", {}), ensure_ascii=False),
                "form_type":           form_type,
            })
        logger.info("[IT-TNF] 결과: %d건", len(results))
        return results
