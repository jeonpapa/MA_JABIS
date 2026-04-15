"""
DrugEnrichmentAgent — 국내 약제 부가정보 수집 에이전트

책임:
  - RSA(위험분담제) 여부, 허가일, 용법용량 수집 (Perplexity sonar-pro)
  - drug_enrichment 테이블에 캐싱 (TTL 30일) → 토큰 절감
  - 현재가 + 용법용량 → 일/월/연 치료비용 계산

규칙 단일 공급원: agents/rules/drug_enrichment_rules.md (프롬프트에 원문 주입)
"""

import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

BASE_DIR   = Path(__file__).parent.parent
RULES_PATH = BASE_DIR / "agents" / "rules" / "drug_enrichment_rules.md"


def _load_env() -> None:
    env_path = BASE_DIR / "config" / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def _load_rules() -> str:
    try:
        return RULES_PATH.read_text(encoding="utf-8")
    except Exception:
        return ""


RULES_TEXT = _load_rules()

SYSTEM_PROMPT = (
    "당신은 한국 MA(Market Access) 전문 분석가로서, 약제의 RSA·허가·용법용량을\n"
    "공식 출처에서 조사하여 아래 룰 원문을 그대로 준수하는 JSON으로 응답합니다.\n\n"
    "=== drug_enrichment_rules.md (원문 주입) ===\n"
    f"{RULES_TEXT}\n"
    "=== 룰 끝 ===\n\n"
    "응답 형식: 룰의 '출력 JSON 스키마' 블록을 글자 그대로 따를 것. 다른 텍스트 금지."
)


class DrugEnrichmentAgent:
    def __init__(self, db, model: str = "sonar-pro"):
        self.db = db
        self.model = model
        _load_env()

    # ─────────────────────────────────────────────────────────────
    # 공개 진입점
    # ─────────────────────────────────────────────────────────────
    def get(
        self,
        normalized_name: str,
        *,
        representative_code: str = "",
        insurance_codes: Optional[list] = None,
        product_name: str = "",
        ingredient: str = "",
        current_price: Optional[float] = None,
        force_refresh: bool = False,
    ) -> dict:
        """
        캐시 히트면 DB에서 반환, 미스면 Perplexity 호출 후 저장.
        current_price 가 제공되면 일/월/연 치료비도 포함해 반환.
        """
        cached = self.db.get_enrichment(normalized_name) if not force_refresh else None
        if cached and self._cache_valid(cached):
            logger.info("[Enrichment] cache HIT: %s", normalized_name)
            return self._shape_response(cached, current_price, source="cache")

        # cache miss → 외부 조회
        logger.info("[Enrichment] cache MISS: %s — Perplexity 호출", normalized_name)
        rec = self._fetch_remote(normalized_name, product_name, ingredient)
        if not rec:
            # 실패 폴백 — 빈 레코드 반환 (에러로 블록하지 않음)
            rec = self._empty_record()

        # 저장용 dict 구성
        rec["normalized_name"]      = normalized_name
        rec["representative_code"]  = representative_code
        rec["insurance_codes_json"] = insurance_codes or []
        self.db.save_enrichment(rec)

        persisted = self.db.get_enrichment(normalized_name) or rec
        return self._shape_response(persisted, current_price, source="fresh")

    # ─────────────────────────────────────────────────────────────
    def _cache_valid(self, rec: dict) -> bool:
        try:
            fetched = datetime.fromisoformat(rec["fetched_at"].replace("Z", ""))
            ttl     = int(rec.get("ttl_days") or 30)
            return datetime.utcnow() < fetched + timedelta(days=ttl)
        except Exception:
            return False

    def _empty_record(self) -> dict:
        return {
            "is_rsa": None, "rsa_type": None, "rsa_note": "",
            "approval_date": None, "usage_text": "",
            "daily_dose_units": None, "dose_schedule": "as_needed",
            "cycle_days": None, "doses_per_cycle": None,
            "sources_json": [], "confidence": "low",
            "notes": "외부 조회 실패 — 재시도 필요",
        }

    # ─────────────────────────────────────────────────────────────
    # Perplexity 호출
    # ─────────────────────────────────────────────────────────────
    def _fetch_remote(self, normalized_name: str, product_name: str, ingredient: str) -> Optional[dict]:
        api_key = os.environ.get("PERPLEXITY_API_KEY", "")
        if not api_key:
            logger.warning("[Enrichment] PERPLEXITY_API_KEY 미설정")
            return None
        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key, base_url="https://api.perplexity.ai")

            user = (
                f"[조사 대상]\n- 제품명(정규화): {normalized_name}\n"
                f"- 원 제품명: {product_name}\n- 주성분: {ingredient}\n- 국가: 대한민국\n\n"
                f"다음을 조사하여 룰의 JSON 스키마로만 응답하세요:\n"
                f"1) RSA(위험분담제) 해당 여부 및 유형 — HIRA 공식 목록/복지부 고시 근거\n"
                f"2) 최초 품목허가일 (MFDS 의약품안전나라)\n"
                f"3) 성인 표준 용법용량 (식약처 허가사항) 및 계산용 수치 추출\n"
                f"4) 출처 URL 1~5개 (공식 소스 우선)"
            )
            resp = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": SYSTEM_PROMPT},
                          {"role": "user",   "content": user}],
                temperature=0.1,
                max_tokens=1400,
            )
            raw = resp.choices[0].message.content.strip()
            if "```" in raw:
                raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
            m = re.search(r"\{[\s\S]+\}", raw)
            if m:
                raw = m.group(0)
            data = json.loads(raw)

            # citations 보강
            sources = data.get("sources", []) or []
            if hasattr(resp, "citations") and resp.citations:
                existing = {s.get("url") for s in sources}
                for url in resp.citations:
                    if url not in existing:
                        sources.append({"url": url, "title": "", "media": self._guess_media(url)})

            return {
                "is_rsa":          self._coerce_bool(data.get("is_rsa")),
                "rsa_type":        data.get("rsa_type"),
                "rsa_note":        data.get("rsa_note") or "",
                "approval_date":   data.get("approval_date"),
                "usage_text":      data.get("usage_text") or "",
                "daily_dose_units": self._coerce_float(data.get("daily_dose_units")),
                "dose_schedule":   data.get("dose_schedule") or "as_needed",
                "cycle_days":      self._coerce_int(data.get("cycle_days")),
                "doses_per_cycle": self._coerce_float(data.get("doses_per_cycle")),
                "sources_json":    sources,
                "confidence":      data.get("confidence") or "medium",
                "notes":           data.get("notes") or "",
            }
        except Exception as e:
            logger.warning("[Enrichment] Perplexity 조회 실패: %s", e)
            return None

    @staticmethod
    def _coerce_bool(v):
        if v is None: return None
        if isinstance(v, bool): return 1 if v else 0
        s = str(v).lower()
        if s in ("true", "1", "yes", "해당"): return 1
        if s in ("false", "0", "no", "해당없음"): return 0
        return None

    @staticmethod
    def _coerce_float(v):
        try: return float(v) if v is not None else None
        except Exception: return None

    @staticmethod
    def _coerce_int(v):
        try: return int(v) if v is not None else None
        except Exception: return None

    @staticmethod
    def _guess_media(url: str) -> str:
        mp = {"hira.or.kr": "건강보험심사평가원", "mfds.go.kr": "MFDS", "nedrug.mfds.go.kr": "의약품안전나라",
              "health.kr": "약학정보원", "mohw.go.kr": "보건복지부", "law.go.kr": "법제처",
              "dailypharm.com": "데일리팜", "yakup.com": "약업신문", "medipana.com": "메디파나뉴스",
              "hitnews.co.kr": "히트뉴스"}
        for k, v in mp.items():
            if k in url: return v
        return "기타"

    # ─────────────────────────────────────────────────────────────
    # 응답 정형화 + 치료비 계산
    # ─────────────────────────────────────────────────────────────
    def _shape_response(self, rec: dict, current_price: Optional[float], source: str) -> dict:
        sources = []
        try:
            raw = rec.get("sources_json")
            if isinstance(raw, str) and raw:
                sources = json.loads(raw)
            elif isinstance(raw, list):
                sources = raw
        except Exception:
            sources = []

        codes = []
        try:
            raw = rec.get("insurance_codes_json")
            if isinstance(raw, str) and raw:
                codes = json.loads(raw)
            elif isinstance(raw, list):
                codes = raw
        except Exception:
            pass

        cost = self._calc_cost(
            current_price,
            dose_schedule=rec.get("dose_schedule"),
            daily_dose_units=rec.get("daily_dose_units"),
            cycle_days=rec.get("cycle_days"),
            doses_per_cycle=rec.get("doses_per_cycle"),
        )

        return {
            "normalized_name":      rec.get("normalized_name"),
            "representative_code":  rec.get("representative_code"),
            "insurance_codes":      codes,
            "is_rsa":               rec.get("is_rsa"),
            "rsa_type":             rec.get("rsa_type"),
            "rsa_note":             rec.get("rsa_note") or "",
            "approval_date":        rec.get("approval_date"),
            "usage_text":           rec.get("usage_text") or "",
            "daily_dose_units":     rec.get("daily_dose_units"),
            "dose_schedule":        rec.get("dose_schedule") or "as_needed",
            "cycle_days":           rec.get("cycle_days"),
            "doses_per_cycle":      rec.get("doses_per_cycle"),
            "sources":              sources,
            "confidence":           rec.get("confidence") or "low",
            "notes":                rec.get("notes") or "",
            "fetched_at":           rec.get("fetched_at"),
            "cache_source":         source,
            "current_price":        current_price,
            "treatment_cost":       cost,
        }

    @staticmethod
    def _calc_cost(price, *, dose_schedule, daily_dose_units, cycle_days, doses_per_cycle):
        if price in (None, 0):
            return {"daily": None, "monthly": None, "annual": None, "note": "약가 정보 없음"}
        if dose_schedule == "continuous" and daily_dose_units:
            daily = price * daily_dose_units
            return {"daily": round(daily), "monthly": round(daily * 30),
                    "annual": round(daily * 365), "note": ""}
        if dose_schedule == "cycle" and cycle_days and doses_per_cycle:
            cycle_cost = price * doses_per_cycle
            annual = cycle_cost * (365 / cycle_days)
            return {"daily": round(annual / 365), "monthly": round(annual / 12),
                    "annual": round(annual),
                    "note": f"{cycle_days}일 주기 × {doses_per_cycle}단위/주기"}
        return {"daily": None, "monthly": None, "annual": None,
                "note": "용법용량 미확정 — 필요시 투여(as_needed) 또는 데이터 부족"}
