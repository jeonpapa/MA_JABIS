"""HTA 스크레이퍼 베이스.

가격이 아닌 '허가/등재 평가' 데이터(적응증·결정·평가 PDF)를 다룬다.
- 동기 requests + BeautifulSoup (대부분 정적 HTML)
- 결과는 HTAResult dataclass 리스트로 반환
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class HTAResult:
    drug_query:  str           # 검색어 (belzutifan 등)
    country:     str           # AU / CA / UK / SCT
    body:        str           # PBAC / CADTH / NICE / SMC
    title:       str           # 문서 제목
    indication:  str           # 적응증
    decision:    str           # 결정/권고 상태
    decision_date: Optional[str] = None     # ISO YYYY-MM-DD
    detail_url:  Optional[str] = None       # SMC/PBAC 등 상세 페이지
    pdf_url:     Optional[str] = None       # 평가 PDF 직링크
    pdf_local:   Optional[str] = None       # 다운로드된 로컬 경로
    extra:       dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ReimbursementResult:
    """급여 결정 결과 — reimbursement_xnational 테이블 행과 1:1 매핑.

    HTAResult 와 분리: HTAResult 는 권고(결정 status) 만, ReimbursementResult 는
    실제 보험 수재 + 효력일 + 가격까지 포함 (DB 영구 저장 대상).
    """
    drug_query:     str             # 검색어 (brand or INN)
    indication_id:  Optional[str]   # indications_master FK (못 잡으면 None)
    country:        str             # UK / AU / US / JP / EU
    body:           str             # NICE / PBAC / CMS / CHUIKYO
    decision_type:  str             # recommend / restrict / reject / optimised / not_applicable / not_listed
    decision_id:    Optional[str] = None   # TA1014 / PBAC item / NCD-110.x
    decision_date:  Optional[str] = None   # ISO YYYY-MM-DD
    effective_date: Optional[str] = None   # 보험 수재일
    criteria_text:  Optional[str] = None
    pbs_code:       Optional[str] = None   # 호주 PBS item code
    nhs_list_price: Optional[float] = None
    currency:       Optional[str] = None
    source_url:     Optional[str] = None
    raw_payload:    Optional[dict] = None

    def to_db_record(self) -> dict:
        """save_xnational_reimbursement() 에 그대로 넘길 dict."""
        return {
            "indication_id":  self.indication_id,
            "country":        self.country,
            "body":           self.body,
            "decision_type":  self.decision_type,
            "decision_id":    self.decision_id,
            "decision_date":  self.decision_date,
            "effective_date": self.effective_date,
            "criteria_text":  self.criteria_text,
            "pbs_code":       self.pbs_code,
            "nhs_list_price": self.nhs_list_price,
            "currency":       self.currency,
            "source_url":     self.source_url,
            "raw_payload":    self.raw_payload,
        }

    def to_dict(self) -> dict:
        return asdict(self)


class HTABaseScraper(ABC):
    COUNTRY: str = ""
    BODY:    str = ""
    BASE_URL: str = ""

    def __init__(self, cache_dir: Optional[Path] = None, timeout: int = 30):
        self.timeout   = timeout
        self.cache_dir = cache_dir or Path(__file__).parent.parent.parent / "data" / "hta_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @abstractmethod
    def search(self, drug: str) -> list[HTAResult]:
        """약제명으로 검색해 평가 결과 리스트 반환."""
        raise NotImplementedError

    # ──────────────────────────────────────────────
    @staticmethod
    def _keep_latest(results: list[HTAResult]) -> list[HTAResult]:
        """같은 약제에 대해 적응증별 최신 결정만 유지.

        그룹 키: (indication 앞 60자 정규화). 날짜 없으면 마지막 등장 우선.
        """
        import re as _re
        def _norm(s: str) -> str:
            return _re.sub(r"\s+", " ", (s or "").strip().lower())[:60]

        groups: dict[str, HTAResult] = {}
        for r in results:
            key = _norm(r.indication) or _norm(r.title)
            prev = groups.get(key)
            if prev is None:
                groups[key] = r
            else:
                # 날짜 비교: 더 최신 것 우선
                if (r.decision_date or "") >= (prev.decision_date or ""):
                    groups[key] = r
        return list(groups.values())

    def download_pdf(self, url: str, filename: str) -> Optional[Path]:
        import requests
        out = self.cache_dir / self.BODY / filename
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.exists() and out.stat().st_size > 0:
            return out
        try:
            r = requests.get(url, timeout=self.timeout)
            r.raise_for_status()
            out.write_bytes(r.content)
            logger.info("[%s] PDF 다운로드: %s (%d bytes)", self.BODY, filename, len(r.content))
            return out
        except Exception as e:
            logger.warning("[%s] PDF 다운로드 실패 %s: %s", self.BODY, url, e)
            return None
