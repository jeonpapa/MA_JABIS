"""약제 등재 아날로그 검색 — 코퍼스 파서.

data/hira_pipeline/HIRA_보도자료/*.md (537개 개별 약제 평가 보고서)를 파싱해
단일 JSON(agents/ingest/analog_corpus.json, 비볼륨)으로 외부화한다.
앱은 이 JSON 을 ingest 한다 (committee_results.json 과 동일 git-sync 패턴).

.md 는 Obsidian authoring 소스 유지, JSON 은 앱 ingest 소스.
PyYAML 없이 경량 frontmatter 파서 사용 (frontmatter 형식이 규칙적 — key: "val" | ["a"] | null | int).

실행: python -m agents.analog.corpus   (→ agents/ingest/analog_corpus.json 생성)
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[2]
CORPUS_DIR = BASE_DIR / "data" / "hira_pipeline" / "HIRA_보도자료"
OUT_PATH = BASE_DIR / "agents" / "ingest" / "analog_corpus.json"

# 패싯(단일값/리스트) 키
_SCALAR_KEYS = {
    "title", "brand_name", "generic_name", "manufacturer",
    "disease_category", "disease_name", "cancer_type", "line_of_therapy",
    "committee", "session_date", "review_result", "reimbursement_track",
    "mfds_approval_date", "application_date", "amjilsim_date",
}
_LIST_KEYS = {"rsa_types", "policy_drivers"}
_INT_KEYS = {"ordinal", "lag_days_approval_to_reimb"}

_NULLISH = {"", "null", "none", "None", "미상", "N/A"}


def _clean_scalar(v: str):
    v = v.strip()
    if v.startswith('"') and v.endswith('"'):
        v = v[1:-1]
    if v.startswith("'") and v.endswith("'"):
        v = v[1:-1]
    return None if v in _NULLISH else v


def _parse_list(v: str) -> list[str]:
    v = v.strip()
    if v in _NULLISH or v == "[]":
        return []
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1]
        out = []
        for item in inner.split(","):
            item = item.strip().strip('"').strip("'")
            if item and item not in _NULLISH:
                out.append(item)
        return out
    s = _clean_scalar(v)
    return [s] if s else []


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """--- ... --- frontmatter + 본문 분리. 반환 (meta, body)."""
    meta: dict = {}
    body = text
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.DOTALL)
    if not m:
        return meta, text
    fm, body = m.group(1), m.group(2)
    for line in fm.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        if key in _LIST_KEYS:
            meta[key] = _parse_list(val)
        elif key in _INT_KEYS:
            sv = _clean_scalar(val)
            try:
                meta[key] = int(sv) if sv is not None else None
            except (ValueError, TypeError):
                meta[key] = None
        elif key in _SCALAR_KEYS:
            meta[key] = _clean_scalar(val)
        # 그 외 키(weekday/source_url 등)는 무시
    return meta, body


def _normalize_committee(v) -> str | None:
    if not v:
        return None
    s = str(v)
    if "약평위" in s or "YAKPYUNGWI" in s.upper():
        return "YAKPYUNGWI"
    if "암질심" in s or "AMJILSIM" in s.upper():
        return "AMJILSIM"
    return None


def _extract_wikilinks(body: str) -> list[str]:
    return sorted(set(re.findall(r"\[\[([^\]]+)\]\]", body)))


def build_analog_corpus(out: Path = OUT_PATH) -> dict:
    """537 .md → analog_corpus.json. 반환 {path, reports, sha256}."""
    files = sorted(CORPUS_DIR.glob("*.md"))
    reports = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("[analog.corpus] 읽기 실패 %s: %s", f.name, e)
            continue
        meta, body = parse_frontmatter(text)
        committee = _normalize_committee(meta.get("committee"))
        # brand 없는 보도자료형(9개)·committee 미상은 검색 코어에서 제외하되 보존
        rec = {
            "file_name": f.name,
            "file_hash": hashlib.sha1(text.encode("utf-8")).hexdigest(),
            "title": meta.get("title"),
            "brand_name": meta.get("brand_name"),
            "generic_name": meta.get("generic_name"),
            "manufacturer": meta.get("manufacturer"),
            "disease_category": meta.get("disease_category"),
            "disease_name": meta.get("disease_name"),
            "cancer_type": meta.get("cancer_type"),
            "line_of_therapy": meta.get("line_of_therapy"),
            "committee": committee,
            "session_date": meta.get("session_date"),
            "ordinal": meta.get("ordinal"),
            "review_result": meta.get("review_result"),
            "reimbursement_track": meta.get("reimbursement_track"),
            "rsa_types": meta.get("rsa_types") or [],
            "policy_drivers": meta.get("policy_drivers") or [],
            "mfds_approval_date": meta.get("mfds_approval_date"),
            "application_date": meta.get("application_date"),
            "amjilsim_date": meta.get("amjilsim_date"),
            "lag_days_approval_to_reimb": meta.get("lag_days_approval_to_reimb"),
            "wikilinks": _extract_wikilinks(body),
            "body_text": body.strip(),
        }
        reports.append(rec)

    payload = {"schema_version": 1, "reports": reports}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    sha = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return {"path": str(out), "reports": len(reports), "sha256": sha,
            "with_brand": sum(1 for r in reports if r["brand_name"]),
            "with_generic": sum(1 for r in reports if r["generic_name"])}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print(json.dumps(build_analog_corpus(), ensure_ascii=False, indent=2))
