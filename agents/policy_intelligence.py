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


from agents.policy_analysis import (
    default_root as _default_root,
    remap_private_path as _remap_private_path,
    resolve_curation as _resolve_curation,
)

DEFAULT_ROOT = _default_root()
DEFAULT_MANIFEST_NAME = "pilot_krpia_20260629.json"
DEFAULT_REPORT_NAME = "krpia_policy_intelligence_pilot_timeline_implications_20260629.md"
IMPACT_DRAFT_NAME = "impact_assessment_draft_기등재_약제_재평가_20260630.md"
IMPACT_TEMPLATE_NAME = "impact_assessment_template_기등재_약제_재평가_20260702.xlsx"
REPORT_ARTIFACT_SPECS = [
    {
        "filename": DEFAULT_REPORT_NAME,
        "topic": "전체 KRPIA Policy Intelligence",
        "kind": "pilot_timeline_report",
        "title": "KRPIA Policy Intelligence 파일럿 1차 정리",
        "format": "markdown",
    },
    {
        "filename": IMPACT_DRAFT_NAME,
        "topic": "기등재 약제 재평가·약가조정",
        "kind": "impact_assessment_draft",
        "title": "Impact Assessment Draft — 기등재 약제 재평가",
        "format": "markdown",
    },
    {
        "filename": IMPACT_TEMPLATE_NAME,
        "topic": "기등재 약제 재평가·약가조정",
        "kind": "impact_assessment_template",
        "title": "MSD Product Impact Simulation Template",
        "format": "xlsx",
    },
]


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


def _topic_id(topic: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in topic).strip("-")
    return slug or "topic"


def _artifact_id(filename: str) -> str:
    stem = Path(filename).stem
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in stem).strip("-")
    return slug[:90] or "artifact"


def _report_artifacts(root: Path) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for spec in REPORT_ARTIFACT_SPECS:
        path = root / "reports" / spec["filename"]
        stat = path.stat() if path.exists() else None
        artifacts.append(
            {
                "id": _artifact_id(spec["filename"]),
                "topic": spec["topic"],
                "kind": spec["kind"],
                "title": spec["title"],
                "filename": spec["filename"],
                "format": spec["format"],
                "available": path.exists(),
                "file_size": stat.st_size if stat else 0,
                "updated_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat() if stat else None,
                "download_url": f"/api/policy-intelligence/reports/{_artifact_id(spec['filename'])}/download" if stat else None,
            }
        )
    return artifacts


def resolve_report_artifact_path(artifact_id: str, root: str | Path | None = None) -> Path:
    """Resolve a sanitized report/template artifact ID to a private report path."""
    root_path = Path(root) if root is not None else _default_root()
    for spec in REPORT_ARTIFACT_SPECS:
        if _artifact_id(spec["filename"]) == artifact_id:
            path = root_path / "reports" / spec["filename"]
            if not path.exists():
                raise FileNotFoundError(f"Policy intelligence report artifact not found: {artifact_id}")
            return path
    raise FileNotFoundError(f"Unknown policy intelligence report artifact: {artifact_id}")


def _event_summary(subject: str) -> str:
    cleaned = subject.replace("Fw:", "").replace("FW:", "").strip()
    return cleaned[:180]


GENERAL_MEDIA_LANE_SUBJECT_MARKERS = (
    "prain_keytruda",
    "daily mailing draft",
    "주요 뉴스 &amp; market insight",
    "주요 뉴스 & market insight",
)


def _is_policy_intelligence_event(event: dict[str, Any]) -> bool:
    """Keep the KRPIA/government consultation lane separate from news mailings."""
    subject = (event.get("subject") or "").casefold()
    if any(marker in subject for marker in GENERAL_MEDIA_LANE_SUBJECT_MARKERS):
        return False
    return True


def _public_event(event: dict[str, Any], root: Path) -> dict[str, Any]:
    topic = event.get("topic") or "기타"
    rule = _rule(topic)
    cur = _resolve_curation(event, root)
    is_hermes = cur["curation_source"] == "hermes"
    return {
        "id": event.get("event_id"),
        "date": event.get("received_utc"),
        "subject": event.get("subject"),
        "summary": cur["summary"] if is_hermes and cur.get("summary") else _event_summary(event.get("subject") or ""),
        "topic": topic,
        "agencies": event.get("agencies") or [],
        "deadline": event.get("deadline_hint_from_subject"),
        "status": cur["status"] if is_hermes and cur.get("status") else rule["status"],
        "severity": cur["severity"] if is_hermes and cur.get("severity") else rule["severity"],
        "curation_source": cur["curation_source"],
        "msd_implication": cur.get("msd_implication") or {"rationale": rule["rationale"], "next_action": rule["next_action"]},
        "evidence_quotes": cur.get("evidence_quotes") or [],
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
    all_raw_events = manifest.get("events") or []
    excluded_event_count = sum(1 for event in all_raw_events if not _is_policy_intelligence_event(event))
    # 정책 lane 중에서도 위원회(Monthly/TF) 운영 이벤트는 별도 탭(committee)에서 관리 → 토픽 뷰 제외
    policy_lane = [event for event in all_raw_events if _is_policy_intelligence_event(event)]
    committee_event_count = sum(1 for event in policy_lane if is_committee_event(event))
    raw_events = [event for event in policy_lane if not is_committee_event(event)]
    raw_events = sorted(raw_events, key=lambda e: _safe_dt(e.get("received_utc")), reverse=True)

    events = [_public_event(event, root) for event in raw_events]

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
    topic_ledgers = []
    change_records: list[dict[str, Any]] = []
    for topic, topic_events in by_topic.items():
        rule = _rule(topic)
        chronological_events = sorted(topic_events, key=lambda e: _safe_dt(e.get("date")))
        latest = chronological_events[-1]
        first = chronological_events[0]
        topic_change_records: list[dict[str, Any]] = []
        previous_summary: str | None = None
        for index, event in enumerate(chronological_events):
            after = event.get("summary") or event.get("subject") or ""
            hermes_quotes = [q.get("quote") for q in (event.get("evidence_quotes") or []) if q.get("quote")]
            hermes_rationale = (event.get("msd_implication") or {}).get("rationale")
            change_record = {
                "change_id": f"{_topic_id(topic)}:{event.get('id')}",
                "topic_id": _topic_id(topic),
                "topic_name": topic,
                "event_id": event.get("id"),
                "date": event.get("date"),
                "change_type": "new_topic" if index == 0 else "updated",
                "before": previous_summary,
                "after": after,
                "evidence_quotes": hermes_quotes or ([after] if after else []),
                "why_it_matters": hermes_rationale or rule["rationale"],
                "confidence": "medium",
            }
            topic_change_records.append(change_record)
            change_records.append(change_record)
            previous_summary = after
        latest_change = topic_change_records[-1] if topic_change_records else None
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
        topic_ledgers.append(
            {
                "topic_id": _topic_id(topic),
                "topic_name": topic,
                "first_seen_at": first.get("date"),
                "latest_seen_at": latest.get("date"),
                "current_status": latest.get("status") or rule["status"],
                "current_summary": latest.get("summary"),
                "latest_change": latest_change,
                "severity": latest.get("severity") or rule["severity"],
                "curation_source": latest.get("curation_source", "rule_fallback"),
                "msd_implication_latest": latest.get("msd_implication") or {
                    "rationale": rule["rationale"],
                    "next_action": rule["next_action"],
                },
                "events": [event.get("id") for event in chronological_events],
                "impact_assessment_ready": rule["priority"] == 1,
                "data_gaps": [
                    "MSD 내부 품목·가격·매출·계약/인하 이력 필요"
                ] if rule["priority"] <= 2 else [],
            }
        )
    topics.sort(key=lambda t: (_rule(t["topic"])["priority"], t["topic"]))
    topic_ledgers.sort(key=lambda t: (_rule(t["topic_name"])["priority"], t["topic_name"]))

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
    curated_event_count = sum(1 for event in events if event.get("curation_source") == "hermes")
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
        "excluded_general_media_event_count": excluded_event_count,
        "committee_event_count": committee_event_count,
        "curated_event_count": curated_event_count,
        "pending_analysis_count": len(events) - curated_event_count,
        "report_available": report_path.exists(),
    }
    report_artifacts = _report_artifacts(root)

    return {
        "overview": overview,
        "events": events,
        "topics": topics,
        "topic_ledgers": topic_ledgers,
        "documents": documents,
        "impact_candidates": impact_candidates,
        "change_records": change_records,
        "report_artifacts": report_artifacts,
    }


def _attachment_path(folder: Path | None, doc: dict[str, Any], root: Path) -> Path | None:
    """raw_folder/attachments.json 에서 문서(sha256 우선, filename fallback)의 원본 파일 경로 remap.
    원본은 attachments/original/<name>_<hash>.<ext> 형태라 saved_path 로만 정확히 찾을 수 있음."""
    if folder is None:
        return None
    aj = folder / "attachments.json"
    if not aj.exists():
        return None
    try:
        entries = json.loads(aj.read_text(encoding="utf-8"))
    except Exception:
        return None
    sha, fn = doc.get("sha256"), doc.get("filename")
    match = next((e for e in entries if sha and e.get("sha256") == sha), None)
    if match is None:
        match = next((e for e in entries if fn and e.get("filename") == fn), None)
    if match is None:
        return None
    return _remap_private_path(match.get("saved_path"), root)


def _raw_event_by_id(event_id: str, root: Path, manifest_path: str | Path | None = None) -> dict[str, Any] | None:
    manifest = _load_json(_resolve_manifest_file(root, manifest_path))
    for event in manifest.get("events") or []:
        if event.get("event_id") == event_id and _is_policy_intelligence_event(event):
            return event
    return None


def load_event_detail(event_id: str, root: str | Path | None = None,
                      manifest_path: str | Path | None = None) -> dict[str, Any]:
    """단일 이벤트의 메일 본문 + 첨부 문서(추출텍스트/원본 다운로드 가용성)."""
    root = Path(root) if root is not None else _default_root()
    raw = _raw_event_by_id(event_id, root, manifest_path)
    if raw is None:
        raise FileNotFoundError(f"policy event not found: {event_id}")
    folder = _remap_private_path(raw.get("raw_folder"), root)
    email_body = ""
    if folder is not None:
        body_txt = folder / "body.txt"
        if body_txt.exists():
            email_body = body_txt.read_text(encoding="utf-8", errors="replace")
    documents: list[dict[str, Any]] = []
    for index, doc in enumerate(raw.get("documents") or [], start=1):
        did = _doc_id(event_id, doc.get("filename", "document"), index)
        text_available = _remap_private_path(doc.get("text_path"), root) is not None
        file_available = _attachment_path(folder, doc, root) is not None
        documents.append({
            "id": did,
            "filename": doc.get("filename"),
            "char_count": doc.get("chars", 0),
            "status": doc.get("status"),
            "text_available": text_available,
            "file_available": file_available,
            "text_url": f"/api/policy-intelligence/documents/{did}/text" if text_available else None,
            "download_url": f"/api/policy-intelligence/documents/{did}/download" if file_available else None,
        })
    rule = _rule(raw.get("topic") or "기타")
    return {
        "id": event_id,
        "subject": raw.get("subject"),
        "date": raw.get("received_utc"),
        "from": raw.get("from"),
        "topic": raw.get("topic") or "기타",
        "agencies": raw.get("agencies") or [],
        "severity": rule["severity"],
        "status": rule["status"],
        "deadline": raw.get("deadline_hint_from_subject"),
        "email_body": email_body,
        "email_body_chars": raw.get("email_body_chars", 0),
        "documents": documents,
    }


def _find_document(doc_id: str, root: Path, manifest_path: str | Path | None = None):
    """(raw_event, doc, remapped_folder) 반환 — doc_id 로 역탐색."""
    manifest = _load_json(_resolve_manifest_file(root, manifest_path))
    for event in manifest.get("events") or []:
        if not _is_policy_intelligence_event(event):
            continue
        eid = event.get("event_id", "event")
        for index, doc in enumerate(event.get("documents") or [], start=1):
            if _doc_id(eid, doc.get("filename", "document"), index) == doc_id:
                return event, doc, _remap_private_path(event.get("raw_folder"), root)
    return None, None, None


def resolve_document_text(doc_id: str, root: str | Path | None = None) -> tuple[str, str]:
    """(filename, 추출텍스트). 없으면 FileNotFoundError."""
    root = Path(root) if root is not None else _default_root()
    _event, doc, _folder = _find_document(doc_id, root)
    if not doc:
        raise FileNotFoundError(f"document not found: {doc_id}")
    text_path = _remap_private_path(doc.get("text_path"), root)
    if text_path is None:
        raise FileNotFoundError(f"document text not available: {doc_id}")
    return doc.get("filename") or "document.txt", text_path.read_text(encoding="utf-8", errors="replace")


def resolve_document_file(doc_id: str, root: str | Path | None = None) -> tuple[str, Path]:
    """(filename, 원본첨부 경로). 없으면 FileNotFoundError. traversal 가드."""
    root = Path(root) if root is not None else _default_root()
    _event, doc, folder = _find_document(doc_id, root)
    if not doc or folder is None:
        raise FileNotFoundError(f"document not found: {doc_id}")
    fp = _attachment_path(folder, doc, root)  # remap + traversal 가드 내장
    if fp is None or not fp.exists():
        raise FileNotFoundError(f"document file not available: {doc_id}")
    return doc.get("filename") or fp.name, fp


# ── KRPIA 위원회(Monthly Meeting + TF) 분류 ───────────────────────────────────
import re as _re

TF_DEFS: list[dict[str, Any]] = [
    {"id": "icer", "name": "ICER TF", "markers": ("icer tf", "icer tft"),
     "description": "경제성평가(ICER/QALY) 방법론 및 임계값 대응 TF"},
    {"id": "dpe", "name": "DPE TF", "markers": ("dpe tf", "dpe tft"),
     "description": "약물경제성평가(Drug/Pharmacoeconomics) TF"},
    {"id": "process", "name": "Process Improvement TF", "markers": ("process improvement tf", "process improvement", "pi tf"),
     "description": "등재·평가 프로세스 개선 TF"},
    {"id": "value", "name": "Value Pricing TF", "markers": ("value pricing tf", "value pricing", "value-based"),
     "description": "가치기반 약가(Value-based Pricing) TF"},
]
_MONTHLY_MARKERS = ("ma committee monthly meeting", "monthly committee meeting", "ma monthly committee")

# 월별 회의에서 다뤄진 주제 매칭용 키워드(첨부/제목 텍스트 스캔, LLM 불필요)
TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "기등재 약제 재평가·약가조정": ("기등재", "재평가", "특허만료", "약가조정", "약가 조정", "오리지널"),
    "약가 유연계약제": ("유연계약", "유연 계약", "flexible"),
    "RWE·약제성과평가": ("rwe", "성과평가", "real world", "리얼월드"),
    "희귀질환 치료제 신속등재 / 100일 신속등재": ("희귀질환", "신속등재", "100일"),
    "사용량-약가 연동 협상": ("사용량-약가", "사용량 약가", "pva", "연동 협상", "연동협상"),
    "급여기준 고시 개정 의견조회": ("급여기준", "고시 개정", "세부사항", "적용기준"),
    "KRPIA 정책제안": ("정책제안", "policy proposal", "정책 제안"),
}


def _committee_classify(subject: str | None, doc_filenames: list[str]) -> dict[str, Any]:
    """이벤트를 위원회 lane 으로 분류: monthly | tf(<id>) | None."""
    hay = " ".join([subject or ""] + doc_filenames).casefold()
    for tf in TF_DEFS:
        if any(mk in hay for mk in tf["markers"]):
            return {"lane": "tf", "tf_id": tf["id"], "tf_name": tf["name"]}
    if any(mk in hay for mk in _MONTHLY_MARKERS):
        return {"lane": "monthly", "tf_id": None, "tf_name": None}
    return {"lane": None, "tf_id": None, "tf_name": None}


def _event_committee_class(event: dict[str, Any]) -> dict[str, Any]:
    fns = [d.get("filename") or "" for d in (event.get("documents") or [])]
    return _committee_classify(event.get("subject"), fns)


def is_committee_event(event: dict[str, Any]) -> bool:
    return _event_committee_class(event)["lane"] is not None


def _meeting_month_and_no(event: dict[str, Any]) -> tuple[str, str | None]:
    """첨부/제목에서 회의 날짜(YYYY-MM)·회차를 추출. 실패 시 received_utc 월."""
    blob = (event.get("subject") or "") + " " + " ".join(d.get("filename") or "" for d in (event.get("documents") or []))
    ym, no = None, None
    m8 = _re.search(r"(20\d{2})(\d{2})(\d{2})", blob)  # 20260421
    if m8:
        ym = f"{m8.group(1)}-{m8.group(2)}"
    mno = _re.search(r"(\d+)\s*(?:st|nd|rd|th)\s+MA", blob, _re.I)
    if mno:
        no = mno.group(1)
    if ym is None:
        rec = event.get("received_utc") or ""
        ym = rec[:7] if len(rec) >= 7 else "unknown"
    return ym, no


def _read_text_file(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _topics_in_text(text: str) -> list[str]:
    low = text.casefold()
    hits = []
    for topic, kws in TOPIC_KEYWORDS.items():
        if any(kw.casefold() in low for kw in kws):
            hits.append(topic)
    return hits


def load_committee_workspace(root: str | Path | None = None,
                             manifest_path: str | Path | None = None) -> dict[str, Any]:
    """KRPIA Monthly Meeting(월별 다뤄진 주제 implication) + 4개 TF lane."""
    root = Path(root) if root is not None else _default_root()
    manifest = _load_json(_resolve_manifest_file(root, manifest_path))
    raw_events = [e for e in (manifest.get("events") or []) if _is_policy_intelligence_event(e)]

    def _docs_public(event) -> list[dict[str, Any]]:
        eid = event.get("event_id", "event")
        folder = _remap_private_path(event.get("raw_folder"), root)
        out = []
        for index, doc in enumerate(event.get("documents") or [], start=1):
            did = _doc_id(eid, doc.get("filename", "document"), index)
            out.append({
                "id": did, "filename": doc.get("filename"), "char_count": doc.get("chars", 0),
                "text_available": _remap_private_path(doc.get("text_path"), root) is not None,
                "file_available": _attachment_path(folder, doc, root) is not None,
                "text_url": f"/api/policy-intelligence/documents/{did}/text",
                "download_url": f"/api/policy-intelligence/documents/{did}/download",
            })
        return out

    monthly: list[dict[str, Any]] = []
    tf_buckets: dict[str, list[dict[str, Any]]] = {tf["id"]: [] for tf in TF_DEFS}

    for event in raw_events:
        klass = _event_committee_class(event)
        if klass["lane"] is None:
            continue
        eid = event.get("event_id")
        base = {
            "event_id": eid,
            "subject": event.get("subject"),
            "received_utc": event.get("received_utc"),
            "agencies": event.get("agencies") or [],
            "documents": _docs_public(event),
        }
        if klass["lane"] == "tf":
            tf_buckets[klass["tf_id"]].append(base)
        else:
            ym, no = _meeting_month_and_no(event)
            # 첨부(회의자료) 텍스트에서 그 달 다뤄진 주제 스캔
            combined_text = event.get("subject") or ""
            for doc in event.get("documents") or []:
                tp = _remap_private_path(doc.get("text_path"), root)
                combined_text += "\n" + _read_text_file(tp)
            discussed = []
            for topic in _topics_in_text(combined_text):
                rule = _rule(topic)
                discussed.append({
                    "topic": topic, "severity": rule["severity"],
                    "rationale": rule["rationale"], "next_action": rule["next_action"],
                })
            monthly.append({**base, "month": ym, "meeting_no": no, "discussed_topics": discussed})

    monthly.sort(key=lambda m: (m.get("month") or "", m.get("received_utc") or ""), reverse=True)
    for bucket in tf_buckets.values():
        bucket.sort(key=lambda x: x.get("received_utc") or "", reverse=True)

    tfs = []
    for tf in TF_DEFS:
        items = tf_buckets[tf["id"]]
        tfs.append({
            "id": tf["id"], "name": tf["name"], "description": tf["description"],
            "materials": items, "material_count": len(items),
            "latest_date": items[0]["received_utc"] if items else None,
            "document_count": sum(len(i["documents"]) for i in items),
        })

    return {
        "summary": {
            "monthly_count": len(monthly),
            "tf_count": len(TF_DEFS),
            "tf_with_materials": sum(1 for t in tfs if t["material_count"] > 0),
        },
        "monthly_meetings": monthly,
        "tfs": tfs,
    }


def search_policy(query: str, root: str | Path | None = None,
                  manifest_path: str | Path | None = None, limit: int = 40) -> dict[str, Any]:
    """제목·주제·첨부 파일명·첨부 추출텍스트 전반 키워드 검색."""
    q = (query or "").strip()
    if not q:
        return {"query": q, "count": 0, "results": []}
    root = Path(root) if root is not None else _default_root()
    manifest = _load_json(_resolve_manifest_file(root, manifest_path))
    low = q.casefold()
    results: list[dict[str, Any]] = []
    for event in manifest.get("events") or []:
        if not _is_policy_intelligence_event(event):
            continue
        eid = event.get("event_id", "event")
        subject = event.get("subject") or ""
        topic = event.get("topic") or "기타"
        klass = _event_committee_class(event)
        lane = klass["lane"] or "policy"
        # 이벤트 레벨 매치(제목/주제/기관)
        event_hay = " ".join([subject, topic, " ".join(event.get("agencies") or [])]).casefold()
        if low in event_hay:
            results.append({
                "type": "event", "event_id": eid, "subject": subject, "topic": topic,
                "lane": lane, "tf_name": klass["tf_name"], "date": event.get("received_utc"),
                "snippet": _snippet(subject, q),
            })
        # 문서 레벨 매치(파일명 + 추출텍스트)
        folder = _remap_private_path(event.get("raw_folder"), root)
        for index, doc in enumerate(event.get("documents") or [], start=1):
            fn = doc.get("filename") or ""
            did = _doc_id(eid, fn, index)
            text = _read_text_file(_remap_private_path(doc.get("text_path"), root))
            hay = (fn + "\n" + text).casefold()
            if low in hay:
                results.append({
                    "type": "document", "event_id": eid, "doc_id": did, "filename": fn,
                    "subject": subject, "topic": topic, "lane": lane, "tf_name": klass["tf_name"],
                    "date": event.get("received_utc"),
                    "snippet": _snippet(text or fn, q),
                })
            if len(results) >= limit:
                break
        if len(results) >= limit:
            break
    return {"query": q, "count": len(results), "results": results[:limit]}


def _snippet(text: str, query: str, width: int = 90) -> str:
    if not text:
        return ""
    idx = text.casefold().find(query.casefold())
    if idx == -1:
        return text[:width].strip()
    start = max(0, idx - width // 2)
    end = min(len(text), idx + len(query) + width // 2)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return prefix + text[start:end].replace("\n", " ").strip() + suffix


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
