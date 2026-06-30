"""Policy Intelligence Hub data adapter.

This module converts private KRPIA Gmail/document ingest manifests into the
sanitized JSON shape consumed by the MA dashboard. Raw paths stay server-side;
API payloads expose metadata, summaries, and internal document identifiers only.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any


def _default_root() -> Path:
    configured = os.environ.get("POLICY_INTELLIGENCE_ROOT")
    if configured:
        return Path(configured)
    fly_volume_root = Path("/app/data/policy_intelligence")
    if Path("/app/data").exists():
        return fly_volume_root
    return Path("/opt/data/policy_intelligence")


DEFAULT_ROOT = _default_root()
DEFAULT_MANIFEST_NAME = "pilot_krpia_20260629.json"
DEFAULT_REPORT_NAME = "krpia_policy_intelligence_pilot_timeline_implications_20260629.md"


def _resolve_manifest_file(root: Path, manifest_path: str | Path | None = None) -> Path:
    if manifest_path:
        return Path(manifest_path)
    env_manifest = os.environ.get("POLICY_INTELLIGENCE_MANIFEST")
    if env_manifest:
        return Path(env_manifest) if env_manifest.startswith("/") else root / "manifests" / env_manifest
    latest_status = root / "manifests" / "latest_ingest_status.json"
    if latest_status.exists():
        try:
            status = json.loads(latest_status.read_text(encoding="utf-8"))
            name = status.get("manifest_name")
            if name:
                candidate = root / "manifests" / name
                if candidate.exists():
                    return candidate
        except Exception:
            pass
    gmail_manifests = sorted((root / "manifests").glob("gmail_krpia_*.json"))
    if gmail_manifests:
        return gmail_manifests[-1]
    return root / "manifests" / DEFAULT_MANIFEST_NAME

TOPIC_RULES: dict[str, dict[str, Any]] = {
    "기등재 약제 재평가·약가조정": {
        "severity": "Very High",
        "status": "high-risk pending",
        "priority": 1,
        "impact_title": "기등재 약제 재평가·특허만료 오리지널 45% 조정",
        "rationale": "직접 약가 인하 가능성이 높고 MSD legacy/originator portfolio exposure 확인이 필요함.",
        "next_action": "MSD 품목별 특허/제네릭/2012 가격/현재가/누적 인하 이력 inventory와 price impact simulation 작성",
    },
    "약가 유연계약제": {
        "severity": "High",
        "status": "implementation",
        "priority": 2,
        "impact_title": "약가 유연계약제 운영 및 실제가 정보관리",
        "rationale": "global reference price 방어 기회와 실제가 노출·유통/청구 혼선 리스크가 동시에 존재함.",
        "next_action": "후보 품목 mapping 및 도매/유통 실제가 커뮤니케이션 SOP 점검",
    },
    "RWE·약제성과평가": {
        "severity": "High",
        "status": "submitted",
        "priority": 3,
        "impact_title": "RWE 성과평가 가이드라인",
        "rationale": "oncology/rare/high-cost assets의 launch 및 사후평가 부담으로 이어질 수 있음.",
        "next_action": "자산별 RWE feasibility matrix와 PMS/RMP 중복 검토표 작성",
    },
    "희귀질환 치료제 신속등재 / 100일 신속등재": {
        "severity": "High",
        "status": "consultation requested",
        "priority": 4,
        "impact_title": "희귀질환 100일 신속등재",
        "rationale": "early access opportunity이나 A8/pricing/RWE gate가 존재함.",
        "next_action": "희귀질환 후보 자산의 A8 등재국/가격 및 RWE 계획 가능성 mapping",
    },
    "사용량-약가 연동 협상": {
        "severity": "High",
        "status": "submitted",
        "priority": 5,
        "impact_title": "PVA 지침 개정 — 자진인하 반영",
        "rationale": "자진인하/계약상 인하 반영 여부가 추가 인하 방어 논리와 연결됨.",
        "next_action": "PVA exposure 품목과 자진인하·계약상 인하 evidence trail 정리",
    },
    "급여기준 고시 개정 의견조회": {
        "severity": "Medium",
        "status": "consultation requested",
        "priority": 6,
        "impact_title": "급여기준 고시 개정",
        "rationale": "제품별 영향 가능성이 있어 MSD/competitor 성분 mapping 후 우선순위 재판단 필요.",
        "next_action": "개정 성분과 MSD/competitor 품목 mapping",
    },
    "KRPIA 정책제안": {
        "severity": "Medium-High",
        "status": "monitoring",
        "priority": 7,
        "impact_title": "KRPIA 2026 정책제안 agenda alignment",
        "rationale": "vaccine, oncology, rare/severe disease, access/pricing 제도 개선 아젠다와 MSD priority 연결 가능.",
        "next_action": "MSD priority TA별 정책제안서 문구/근거 재사용 가능성 검토",
    },
}

DEFAULT_TOPIC_RULE = {
    "severity": "Medium",
    "status": "monitoring",
    "priority": 99,
    "impact_title": "정책 동향 모니터링",
    "rationale": "KRPIA/정부 커뮤니케이션의 간접 영향 모니터링 필요.",
    "next_action": "후속 커뮤니케이션 수집 및 관련 MSD 제품/TA mapping",
}


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Policy intelligence manifest not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_dt(value: str | None) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def _rule(topic: str) -> dict[str, Any]:
    return TOPIC_RULES.get(topic, DEFAULT_TOPIC_RULE)


def _doc_id(event_id: str, filename: str, index: int) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in filename)[:60].strip("-")
    return f"{event_id}-{index}-{slug or 'document'}"


def _event_summary(subject: str) -> str:
    cleaned = subject.replace("Fw:", "").replace("FW:", "").strip()
    return cleaned[:180]


def _public_event(event: dict[str, Any]) -> dict[str, Any]:
    topic = event.get("topic") or "기타"
    rule = _rule(topic)
    return {
        "id": event.get("event_id"),
        "date": event.get("received_utc"),
        "subject": event.get("subject"),
        "summary": _event_summary(event.get("subject") or ""),
        "topic": topic,
        "agencies": event.get("agencies") or [],
        "deadline": event.get("deadline_hint_from_subject"),
        "status": rule["status"],
        "severity": rule["severity"],
        "email_body_chars": event.get("email_body_chars", 0),
        "attachment_count": event.get("attachment_count_total", len(event.get("documents") or [])),
        "document_count": len(event.get("documents") or []),
    }


def load_policy_intelligence(
    root: str | Path | None = None,
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    """Return sanitized dashboard data for the Policy Intelligence Hub."""
    root = Path(root) if root is not None else _default_root()
    manifest_file = _resolve_manifest_file(root, manifest_path)
    manifest = _load_json(manifest_file)
    source_batch_id = manifest_file.stem
    raw_events = manifest.get("events") or []
    raw_events = sorted(raw_events, key=lambda e: _safe_dt(e.get("received_utc")), reverse=True)

    events = [_public_event(event) for event in raw_events]

    documents: list[dict[str, Any]] = []
    for event in raw_events:
        for index, doc in enumerate(event.get("documents") or [], start=1):
            documents.append(
                {
                    "id": _doc_id(event.get("event_id", "event"), doc.get("filename", "document"), index),
                    "event_id": event.get("event_id"),
                    "subject": event.get("subject"),
                    "topic": event.get("topic"),
                    "filename": doc.get("filename"),
                    "status": doc.get("status"),
                    "char_count": doc.get("chars", 0),
                    "source_kind": "attachment",
                    "text_available": bool(doc.get("text_path")),
                }
            )

    by_topic: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_topic[event["topic"]].append(event)

    topics = []
    for topic, topic_events in by_topic.items():
        rule = _rule(topic)
        latest = max(topic_events, key=lambda e: _safe_dt(e.get("date")))
        topics.append(
            {
                "topic": topic,
                "event_count": len(topic_events),
                "latest_date": latest.get("date"),
                "latest_subject": latest.get("subject"),
                "severity": rule["severity"],
                "status": rule["status"],
                "next_action": rule["next_action"],
            }
        )
    topics.sort(key=lambda t: (_rule(t["topic"])["priority"], t["topic"]))

    impact_candidates = []
    for topic in by_topic:
        rule = _rule(topic)
        if rule["priority"] >= 99:
            continue
        impact_candidates.append(
            {
                "topic": topic,
                "title": rule["impact_title"],
                "priority": rule["priority"],
                "severity": rule["severity"],
                "rationale": rule["rationale"],
                "next_action": rule["next_action"],
                "event_count": len(by_topic[topic]),
            }
        )
    impact_candidates.sort(key=lambda c: c["priority"])

    severity_counts = Counter(event["severity"] for event in events)
    report_path = root / "reports" / DEFAULT_REPORT_NAME
    overview = {
        "created_at": manifest.get("created_at"),
        "source_batch_id": source_batch_id,
        "event_count": len(events),
        "topic_count": len(topics),
        "document_count": len(documents),
        "high_impact_count": sum(severity_counts[s] for s in ("High", "Very High")),
        "latest_event_date": events[0]["date"] if events else None,
        "severity_counts": dict(severity_counts),
        "report_available": report_path.exists(),
    }

    return {
        "overview": overview,
        "events": events,
        "topics": topics,
        "documents": documents,
        "impact_candidates": impact_candidates,
    }


def write_dashboard_json(root: str | Path | None = None, manifest_path: str | Path | None = None) -> Path:
    root = Path(root) if root is not None else _default_root()
    data = load_policy_intelligence(root=root, manifest_path=manifest_path)
    out = root / "manifests" / "policy_intelligence_dashboard.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


if __name__ == "__main__":
    path = write_dashboard_json()
    print(path)
