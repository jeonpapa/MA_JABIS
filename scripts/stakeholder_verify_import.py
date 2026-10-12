#!/usr/bin/env python3
"""Stakeholder & Verbatim 이관 — 손실 없음 검증 (T10).

볼륨 root(소스 repo 와 같은 상대경로) + DB(sv_*) 를 읽기만 해서, 소스 repo 의 모든 산출물이 빠짐없이·바이트 그대로
옮겨졌는지 판정한다. DB 는 `mode=ro` 로 연다(쓰기 없음). 네트워크·LLM 없음.

    python scripts/stakeholder_verify_import.py --root <볼륨 root> --db <db> [--source-dir <SRC>] [--canonical-md <md>] [--json]

손실 없음의 정의 (각 항목이 failures 의 문구 접두와 대응한다)
  ① 소스 repo `scripts/verify_repository.py` 이식 — root 의 모든 *.json 파싱(`invalid JSON`), manifest sources 의
     local_path/text_path 존재(`missing manifest file`)·SHA-256(`SHA-256 mismatch`)·local_path 크기(`byte-size mismatch`),
     artifacts 존재(`missing artifact file`)·SHA-256(`artifact SHA-256 mismatch`)·크기(`artifact byte-size mismatch`).
  ② 미러 — `--source-dir` 의 허용목록(paths.is_sync_allowed + MAX_BLOB_BYTES) 파일 전부가 root 에 같은 바이트로 있다
     (`mirror 누락` / `mirror 바이트 불일치`). 허용목록 밖은 비교하지 않고 exclusions.rejected_paths 로 **열거**한다.
     sync 색인(sv_sync_file)의 모든 경로가 root 에 기록된 sha256 그대로 있다(`sync 색인 …`) — source-dir 없이도 돈다.
  ③ manifest ↔ DB — 모든 source/artifact 가 sv_source/sv_artifact 에 있고(`manifest … 미반영`) raw_json 이 원문 객체와
     같으며(`manifest … 필드 불일치`) integrity_status 가 지금 다시 잰 판정과 같다(`integrity verdict 불일치`).
     manifest 최상위 키는 전부 sv_meta 어딘가에 같은 값으로 있다(`sv_meta 불일치`). `no_local`(원문 파일이 없는 항목,
     예: video-only)은 손실이 아니라 소스 상태 — exclusions.no_local_sources 로 열거.
  ④ 레지스트리 — data/{people,source,audit_proposals} 의 모든 레코드가 DB 에 있고(`레지스트리 레코드 미반영`) 모든 원본
     필드가 대응 컬럼으로 왕복한다(`레지스트리 필드 불일치`; 대응 컬럼이 없는 필드는 `레지스트리 필드 매핑 없음`).
     운영자 결정(승인·반려·상태·편집)으로 바뀐 값은 손실이 아니다 — 운영자 소유 필드면 notes, 불변 필드면 실패.
  ⑤ 런 — monitoring-runs/*/daily_monitoring.json · audit-runs/*/coverage_audit.json 이 각각 DB 행으로 있고(`런 미반영`)
     payload_json 이 파일 바이트와 정확히 같다(`런 payload 바이트 불일치`, `런 payload_sha256 불일치`).
  ⑥ 정본 타임라인 MD — 파서와 독립된 구조 스캔으로 센다: 마스터 표 행 = sv_event(`정본 행사 수 불일치`), 주제 섹션의
     `### ` = sv_statement(`정본 발언블록 수 불일치`), 주제 섹션의 모든 `> ` 블록이 같은 행사·발언자의 sv_quote 에
     **바이트 그대로** 있다(`정본 인용 블록 미반영` / 정규화로만 같으면 `정본 인용 블록 원문 불일치`), 역으로 모든 sv_quote 가
     MD 의 블록과 바이트 그대로 같다(`DB 인용이 정본에 없음`). 주제 섹션 밖 블록은 편집 주석 — exclusions 로 열거.
     locator_match='none' 은 KNOWN_SOURCE_DISCREPANCIES 에 있으면 known_discrepancies(소스 측 결함), 아니면 실패.

출력: 텍스트(기본) 또는 `--json`. 종료코드 0 = failures 없음, 1 = 있음.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.stakeholder import canonical_parse, paths, store  # noqa: E402
from agents.stakeholder.sync import (  # noqa: E402
    CANONICAL_TIMELINE_ARTIFACT, CANONICAL_TIMELINE_GLOB, _MANIFEST_META_JSON, _MANIFEST_META_SCALARS, _SKIP_WALK_DIRS)

# 소스 측 결함으로 알려진 원문 대조 실패. (발언자, quote_text 의 SHA-256) 로 정확히 한 인용만 가리킨다 — 인용 문구는
# 저장소에 두지 않는다(저작권 하이진). 이 목록 밖의 locator_match='none' 은 실패다.
KNOWN_SOURCE_DISCREPANCIES: tuple[dict, ...] = (
    {"kind": "locator_match_none", "speaker_name": "홍승권",
     "quote_sha256": "9900fb1c963089baa98dd2a8b334228f59e3093da4e8a2dba2abb5d70d6a0d36",
     "reason": "2026-06-19 심평원 RWE 심포지엄 인용(정본 MD L296 무렵): 정본은 끝에 마침표('…기대한다.”')를 붙였으나 "
               "원문 추출 텍스트(sources/hira_20260619_rwe_event.txt:444)는 '…기대한다' 뒤 줄바꿈 후 '”' — "
               "소스 측 구두점 결함. 이관 손실 아님(인용 블록은 정본과 바이트 동일하게 적재됨)."},
)

_REPO_SKIP_PARTS = {".git", ".venv", "venv", paths.SYNC_SUBDIR}
_RE_H1 = re.compile(r"^# ")
_RE_TOPIC_H1 = re.compile(r"^# \d+\.\s*주제\s")
_RE_EVENT = re.compile(r"^## (\d{4}-\d{2}-\d{2})\s*[｜|]\s*(.+?)\s*$")
_RE_H2 = re.compile(r"^## ")
_RE_SPEAKER = re.compile(r"^### (.+?)(?: [—–] (.+?))?\s*$")
_RE_MASTER_ROW = re.compile(r"^\| \*\*(\d{4}-\d{2}-\d{2})\*\* \|")
_RE_QUOTE_LINE = re.compile(r"^>(?: (.*)|)$")

# ── 레지스트리 필드 대응 (원본 키 → (DB 컬럼, 형식)) ────────────────────────────────────
# 형식: None = 그대로, "json" = 컬럼이 JSON 텍스트(파싱해 비교).
PEOPLE_FIELDS: dict[str, tuple[str, str | None]] = {
    **{k: (k, None) for k in ("person_id", "name", "organization", "organization_code", "department", "role",
                              "status", "verification_status", "effective_from", "photo_url", "photo_path",
                              "discovered_from_proposal", "updated_by", "created_at", "updated_at")},
    "aliases": ("aliases_json", "json"), "topic_tags": ("topic_tags_json", "json"),
    "discovery_evidence": ("discovery_evidence_json", "json"),
}
DOMAIN_FIELDS: dict[str, tuple[str, str | None]] = {
    **{k: (k, None) for k in ("source_id", "name", "domain", "source_group", "status", "effective_from",
                              "approved_from_proposal", "created_at", "updated_at")},
    "evidence": ("evidence_json", "json"), "evidence_json": ("evidence_json", None),
}
PROPOSAL_FIELDS: dict[str, tuple[str, str | None]] = {
    **{k: (k, None) for k in ("proposal_id", "proposal_type", "dedupe_key", "priority", "summary", "rationale",
                              "audit_run_id", "created_by", "status", "effective_from", "reviewer", "reviewed_at",
                              "decision_reason", "applied_at", "created_at", "updated_at")},
    "proposed_change": ("proposed_change_json", "json"), "evidence": ("evidence_json", "json"),
    "conflicts": ("conflicts_json", "json"),
}
# 운영자 결정이 있어도 바뀌면 안 되는 필드(나머지는 운영자 소유 — 바뀌면 notes)
PEOPLE_IMMUTABLE = {"person_id", "organization_code", "photo_url", "created_at", "discovered_from_proposal",
                    "discovery_evidence"}
DOMAIN_IMMUTABLE = set(DOMAIN_FIELDS)            # 출처 도메인은 대쉬보드 편집 경로가 없다
PROPOSAL_IMMUTABLE = {"proposal_id", "proposal_type", "dedupe_key", "priority", "summary", "rationale",
                      "proposed_change", "evidence", "conflicts", "audit_run_id", "created_by", "created_at"}

REGISTRIES = (
    # (이름, 파일, 목록 키, 테이블, 키 컬럼, 필드표, 불변 필드)
    ("people_registry", "data/people_registry.json", "people", "sv_person", "person_id", PEOPLE_FIELDS,
     PEOPLE_IMMUTABLE),
    ("source_registry", "data/source_registry.json", "sources", "sv_source_domain", "source_id", DOMAIN_FIELDS,
     DOMAIN_IMMUTABLE),
    ("audit_proposals", "data/audit_proposals.json", "proposals", "sv_audit_proposal", "proposal_id",
     PROPOSAL_FIELDS, PROPOSAL_IMMUTABLE),
)
_SPECIAL_REGISTRY_KEYS = {"people_registry": {"pending_role_changes"}}

RUN_SPECS = (
    # (counts 키, 하위 디렉터리, 파일 stem, 테이블, 키 컬럼, payload 의 id 키)
    ("monitoring_runs", "monitoring-runs", "daily_monitoring", "sv_monitoring_run", "run_id", "run_id"),
    ("audit_runs", "audit-runs", "coverage_audit", "sv_audit_run", "audit_run_id", "audit_run_id"),
)


@dataclass
class Report:
    failures: list[str] = field(default_factory=list)
    counts: dict[str, Any] = field(default_factory=dict)
    basis: dict[str, str] = field(default_factory=dict)          # 수치마다 무엇과 무엇을 대조했는가
    exclusions: dict[str, list] = field(default_factory=lambda: {
        "rejected_paths": [], "excluded_blockquotes": [], "no_local_sources": [], "skipped_dirs": []})
    known_discrepancies: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures

    def to_dict(self) -> dict:
        return {"ok": self.ok, **asdict(self)}


# ── 공통 ─────────────────────────────────────────────────────────────────────────

def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _under(root: Path, rel: Any) -> Path | None:
    """manifest/색인 경로 → root 아래 실제 경로(안전하지 않으면 None)."""
    if not isinstance(rel, str) or not rel:
        return None
    try:
        return paths.resolve_under(root, rel)
    except ValueError:
        return None


def _jload(text: Any) -> Any:
    if text is None:
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return ("<JSON 아님>", text)


def _short(v: Any, n: int = 60) -> str:
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return s if len(s) <= n else s[: n - 1] + "…"


# ── ① verify_repository.py 이식 + 기대 무결성 판정 ─────────────────────────────────────

def _check_repository(root: Path, rep: Report) -> dict | None:
    json_count = 0
    for path in sorted(root.rglob("*.json")):
        rel_parts = path.relative_to(root).parts
        if any(part in _REPO_SKIP_PARTS for part in rel_parts) or rel_parts[0] == paths.PHOTOS_SUBDIR:
            continue
        rel = path.relative_to(root).as_posix()
        try:
            json.loads(path.read_text(encoding="utf-8"))
            json_count += 1
        except Exception as exc:  # noqa: BLE001 — verify_repository 와 같은 판정
            rep.failures.append(f"invalid JSON: {rel}: {exc}")
    rep.counts["json_files"] = json_count
    rep.basis["json_files"] = "root 아래 *.json 전부(.sync·photos 제외) json.loads 성공 수"

    mpath = root / "source_manifest.json"
    if not mpath.is_file():
        rep.failures.append("missing manifest file: source_manifest.json")
        return None
    raw = mpath.read_bytes()
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except ValueError:
        return None                                              # invalid JSON 으로 이미 실패
    manifest["__sha256__"] = _sha256_bytes(raw)

    hash_count = 0
    expected_sources: dict[str, str] = {}
    for src in manifest.get("sources", []) or []:
        sid = src.get("source_id") if isinstance(src, dict) else None
        pairs = [(k, h) for k, h in (("local_path", "sha256"), ("text_path", "text_sha256")) if src.get(k)]
        missing = mismatch = False
        compared = 0
        for path_key, hash_key in pairs:
            rel = src[path_key]
            path = _under(root, rel)
            if path is None or not path.is_file():
                rep.failures.append(f"missing manifest file: {rel}")
                missing = True
                continue
            expected = src.get(hash_key)
            if expected:
                actual = _sha256_file(path)
                hash_count += 1
                compared += 1
                if actual != expected:
                    rep.failures.append(f"SHA-256 mismatch: {rel}: expected {expected}, got {actual}")
                    mismatch = True
            if path_key == "local_path" and isinstance(src.get("bytes"), int):
                size = path.stat().st_size
                if size != src["bytes"]:
                    rep.failures.append(f"byte-size mismatch: {rel}: expected {src['bytes']}, got {size}")
                    mismatch = True
        if sid:
            expected_sources[sid] = ("no_local" if not pairs else "missing" if missing else "mismatch" if mismatch
                                     else "verified" if compared else "unchecked")

    expected_artifacts: dict[str, str] = {}
    for art in manifest.get("artifacts", []) or []:
        rel = art.get("path") if isinstance(art, dict) else None
        if not rel:
            rep.failures.append("artifact entry missing path")
            continue
        path = _under(root, rel)
        if path is None or not path.is_file():
            rep.failures.append(f"missing artifact file: {rel}")
            expected_artifacts[rel] = "missing"
            continue
        bad = False
        if art.get("sha256"):
            actual = _sha256_file(path)
            hash_count += 1
            if actual != art["sha256"]:
                rep.failures.append(f"artifact SHA-256 mismatch: {rel}: expected {art['sha256']}, got {actual}")
                bad = True
        if art.get("bytes") is not None:
            size = path.stat().st_size
            if size != art["bytes"]:
                rep.failures.append(f"artifact byte-size mismatch: {rel}: expected {art['bytes']}, got {size}")
                bad = True
        expected_artifacts[rel] = ("mismatch" if bad else "verified" if art.get("sha256") or
                                   isinstance(art.get("bytes"), int) else "unchecked")
    rep.counts["manifest_hashes_matched"] = hash_count - sum(
        1 for f in rep.failures if f.startswith(("SHA-256 mismatch", "artifact SHA-256 mismatch")))
    rep.basis["manifest_hashes_matched"] = "manifest sha256/text_sha256/artifact sha256 중 볼륨 파일과 일치한 수"
    manifest["__expected_sources__"] = expected_sources
    manifest["__expected_artifacts__"] = expected_artifacts
    return manifest


# ── ② 미러 ───────────────────────────────────────────────────────────────────────

def _check_mirror(root: Path, conn: sqlite3.Connection, source_dir: Path | None, rep: Report) -> None:
    index = {r["path"]: (r["sha256"], r["bytes"]) for r in conn.execute("SELECT path, sha256, bytes FROM sv_sync_file")}
    rep.counts["sync_index_files"] = len(index)
    rep.basis["sync_index_files"] = "sv_sync_file 행 수 — 각 경로가 root 에 기록된 sha256 그대로 있는지 확인"
    for rel, (sha, size) in sorted(index.items()):
        path = _under(root, rel)
        if path is None or not path.is_file():
            rep.failures.append(f"sync 색인 파일 없음: {rel}")
        elif sha and _sha256_file(path) != sha:
            rep.failures.append(f"sync 색인 불일치: {rel}: 색인 sha256 {sha[:12]} ≠ 볼륨")

    if source_dir is None:
        rep.counts.update(mirrored_files=None, mirror_identical=None, mirror_mismatch=None, mirror_missing=None)
        rep.basis["mirrored_files"] = "--source-dir 없음 — 소스↔볼륨 바이트 대조 생략(sync 색인 대조만)"
        return

    allowed: list[str] = []
    for dirpath, dirnames, filenames in os.walk(source_dir):
        skipped = [d for d in dirnames if d in _SKIP_WALK_DIRS]
        for d in skipped:
            rep.exclusions["skipped_dirs"].append(
                unicodedata.normalize("NFC", (Path(dirpath) / d).relative_to(source_dir).as_posix()))
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_WALK_DIRS)
        for fn in sorted(filenames):
            full = Path(dirpath) / fn
            rel = unicodedata.normalize("NFC", full.relative_to(source_dir).as_posix())
            if full.is_symlink() or not full.is_file():
                rep.exclusions["rejected_paths"].append({"path": rel, "reason": "심볼릭 링크·특수 파일"})
            elif not paths.is_sync_allowed(rel):
                rep.exclusions["rejected_paths"].append({"path": rel, "reason": "sync 허용목록 밖(설계상 이관 안 함)"})
            elif full.stat().st_size > paths.MAX_BLOB_BYTES:
                rep.exclusions["rejected_paths"].append({"path": rel, "reason": f"크기 상한 {paths.MAX_BLOB_BYTES} 초과"})
            else:
                allowed.append(rel)
                src_sha = _sha256_file(full)
                dest = _under(root, rel)
                if dest is None or not dest.is_file():
                    rep.failures.append(f"mirror 누락: {rel}")
                elif _sha256_file(dest) != src_sha:
                    rep.failures.append(f"mirror 바이트 불일치: {rel}")
                if rel not in index:
                    rep.failures.append(f"sync 색인 누락: {rel}")
                elif index[rel][0] and index[rel][0] != src_sha:
                    rep.failures.append(f"sync 색인 불일치(소스 대비): {rel}")
    missing = sum(1 for f in rep.failures if f.startswith("mirror 누락"))
    mismatch = sum(1 for f in rep.failures if f.startswith("mirror 바이트 불일치"))
    rep.counts.update(mirrored_files=len(allowed), mirror_identical=len(allowed) - missing - mismatch,
                      mirror_mismatch=mismatch, mirror_missing=missing)
    rep.basis["mirrored_files"] = (f"--source-dir 의 허용목록 파일 {len(allowed)}개 — 볼륨 같은 경로와 SHA-256 대조 "
                                   f"(일치 {len(allowed) - missing - mismatch}, 불일치 {mismatch}, 누락 {missing}); "
                                   f"허용목록 밖 {len(rep.exclusions['rejected_paths'])}개는 비교 대상 아님(exclusions)")
    allowed_set = set(allowed)
    extra = [p for p in index if p not in allowed_set]
    if extra:
        rep.notes.append(f"볼륨에만 있는 sync 파일 {len(extra)}건(원격에서 사라짐 — 기록 보존 설계, 손실 아님): "
                         + ", ".join(extra[:5]) + (" …" if len(extra) > 5 else ""))


# ── ③ manifest ↔ DB ──────────────────────────────────────────────────────────────

def _check_manifest_db(manifest: dict, conn: sqlite3.Connection, rep: Report) -> None:
    sources = [s for s in manifest.get("sources", []) or [] if isinstance(s, dict) and s.get("source_id")]
    artifacts = [a for a in manifest.get("artifacts", []) or [] if isinstance(a, dict) and a.get("path")]
    db_src = {r["source_id"]: r for r in conn.execute("SELECT * FROM sv_source")}
    db_art = {r["path"]: r for r in conn.execute("SELECT * FROM sv_artifact")}
    exp_s, exp_a = manifest["__expected_sources__"], manifest["__expected_artifacts__"]

    for s in sources:
        sid = s["source_id"]
        row = db_src.get(sid)
        if row is None:
            rep.failures.append(f"manifest source 미반영: {sid}")
            continue
        if _jload(row["raw_json"]) != s:
            rep.failures.append(f"manifest source 필드 불일치: {sid} (raw_json ≠ manifest 항목)")
        for k in store._SOURCE_SCALARS:
            want = store._scalar(s.get(k))
            if row[k] != want:
                rep.failures.append(f"manifest source 필드 불일치: {sid}.{k}: DB={_short(row[k])} 원본={_short(want)}")
        for k in store._SOURCE_JSONS:
            if _jload(row[f"{k}_json"]) != s.get(k):
                rep.failures.append(f"manifest source 필드 불일치: {sid}.{k}")
        if row["integrity_status"] != exp_s.get(sid):
            rep.failures.append(f"integrity verdict 불일치: source {sid}: DB={row['integrity_status']} "
                                f"지금 측정={exp_s.get(sid)}")
        if exp_s.get(sid) == "no_local":
            rep.exclusions["no_local_sources"].append({
                "source_id": sid, "source_type": s.get("source_type"),
                "reason": s.get("limitation") or s.get("video_limitation") or s.get("access_status")
                or "manifest 에 local_path·text_path 없음"})
    for a in artifacts:
        row = db_art.get(a["path"])
        if row is None:
            rep.failures.append(f"manifest artifact 미반영: {a['path']}")
            continue
        if _jload(row["raw_json"]) != a:
            rep.failures.append(f"manifest artifact 필드 불일치: {a['path']} (raw_json ≠ manifest 항목)")
        if row["integrity_status"] != exp_a.get(a["path"]):
            rep.failures.append(f"integrity verdict 불일치: artifact {a['path']}: DB={row['integrity_status']} "
                                f"지금 측정={exp_a.get(a['path'])}")
    extra_s = sorted(set(db_src) - {s["source_id"] for s in sources})
    extra_a = sorted(set(db_art) - {a["path"] for a in artifacts})
    if extra_s or extra_a:
        rep.notes.append(f"manifest 에 없는 DB 행(이전 manifest 미러 — 삭제하지 않는 설계): sources {extra_s} "
                         f"artifacts {extra_a}")

    keys = ("verified", "mismatch", "missing", "no_local", "unchecked")

    def dist(rows, keys):
        c = Counter(r["integrity_status"] for r in rows)
        return {k: c.get(k, 0) for k in keys}

    in_s = [db_src[s["source_id"]] for s in sources if s["source_id"] in db_src]
    in_a = [db_art[a["path"]] for a in artifacts if a["path"] in db_art]
    rep.counts["sources"] = len(sources)
    rep.counts["artifacts"] = len(artifacts)
    rep.counts["integrity"] = {"sources": dist(in_s, keys), "artifacts": dist(in_a, keys)}
    rep.basis["sources"] = (f"source_manifest.json sources[] {len(sources)}건 ↔ sv_source(id·raw_json·승격 컬럼·"
                            f"integrity 재측정) — DB 행 {len(db_src)}")
    rep.basis["artifacts"] = (f"source_manifest.json artifacts[] {len(artifacts)}건 ↔ sv_artifact(path·raw_json·"
                              f"integrity 재측정) — DB 행 {len(db_art)}")
    rep.basis["integrity"] = "sv_source/sv_artifact.integrity_status 분포 — 각 행이 지금 볼륨을 다시 잰 판정과 같음을 확인"

    # sv_meta — manifest 최상위 키 전부가 어딘가에 같은 값으로
    meta = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM sv_meta")}
    if meta.get("manifest_sha256") != manifest["__sha256__"]:
        rep.failures.append("sv_meta 불일치: manifest_sha256 ≠ 볼륨 source_manifest.json")
    top = {k: v for k, v in manifest.items() if not k.startswith("__")}
    covered = {"sources", "artifacts"}
    for k in _MANIFEST_META_SCALARS:
        if top.get(k) is not None:
            covered.add(k)
            if meta.get(k) != str(top[k]):
                rep.failures.append(f"sv_meta 불일치: {k}")
    for k in _MANIFEST_META_JSON:
        if k in top:
            covered.add(k)
            if _jload(meta.get(f"{k}_json")) != top[k]:
                rep.failures.append(f"sv_meta 불일치: {k}_json ≠ manifest.{k}")
    extra = {k: v for k, v in top.items() if k not in covered and not (k in _MANIFEST_META_SCALARS and v is None)}
    if _jload(meta.get("manifest_extra_json")) != extra and (extra or meta.get("manifest_extra_json")):
        rep.failures.append(f"sv_meta 불일치: manifest_extra_json ≠ manifest 미지 최상위 키 {sorted(extra)}")
    rep.counts["manifest_top_keys"] = len(top)
    rep.basis["manifest_top_keys"] = ("manifest 최상위 키 수 — sources/artifacts 는 테이블, 나머지는 sv_meta "
                                      "(스칼라·*_json·manifest_extra_json)에 같은 값")


# ── ④ 레지스트리 ─────────────────────────────────────────────────────────────────

def _operator_touched(conn: sqlite3.Connection, name: str, key: str) -> bool:
    if name == "people_registry":
        r = conn.execute("SELECT updated_by FROM sv_person WHERE person_id=?", (key,)).fetchone()
        if r is not None and r["updated_by"]:
            return True
        return conn.execute("SELECT 1 FROM sv_person_role_history WHERE person_id=? AND proposal_id IS NOT NULL",
                            (key,)).fetchone() is not None
    if name == "audit_proposals":
        return conn.execute("SELECT 1 FROM sv_audit_proposal_event WHERE proposal_id=? AND actor NOT LIKE 'system:sync%'",
                            (key,)).fetchone() is not None
    return False


def _check_registries(root: Path, conn: sqlite3.Connection, rep: Report) -> None:
    compared = 0
    for name, rel, list_key, table, key_col, fields, immutable in REGISTRIES:
        f = root / rel
        count_key = {"people_registry": "people", "source_registry": "source_domains",
                     "audit_proposals": "proposals"}[name]
        if not f.is_file():
            rep.failures.append(f"레지스트리 파일 없음: {rel}")
            rep.counts[count_key] = None
            continue
        try:
            items = json.loads(f.read_text(encoding="utf-8")).get(list_key)
        except (ValueError, AttributeError):
            items = None                                          # invalid JSON 으로 이미 실패
        if not isinstance(items, list):
            rep.failures.append(f"레지스트리 목록 없음: {rel}: '{list_key}'")
            rep.counts[count_key] = None
            continue
        db_rows = {r[key_col]: r for r in conn.execute(f"SELECT * FROM {table}")}
        n_present = 0
        for item in items:
            if not isinstance(item, dict) or not item.get(key_col):
                rep.failures.append(f"레지스트리 레코드 형식 오류: {name}: {_short(item)}")
                continue
            key = item[key_col]
            row = db_rows.get(key)
            if row is None:
                rep.failures.append(f"레지스트리 레코드 미반영: {name} {key}")
                continue
            n_present += 1
            diffs: list[str] = []
            for fk, fv in item.items():
                if fk in _SPECIAL_REGISTRY_KEYS.get(name, ()):
                    continue
                if fk not in fields:
                    rep.failures.append(f"레지스트리 필드 매핑 없음: {name} {key}.{fk} — 대응 컬럼이 없어 값이 사라진다")
                    continue
                col, kind = fields[fk]
                dbv = _jload(row[col]) if kind == "json" else row[col]
                compared += 1
                if dbv != fv:
                    diffs.append(fk)
            if diffs:
                touched = _operator_touched(conn, name, key)
                hard = [d for d in diffs if not touched or d in immutable]
                soft = [d for d in diffs if d not in hard]
                if hard:
                    rep.failures.append(f"레지스트리 필드 불일치: {name} {key}: " + ", ".join(
                        f"{d} DB={_short(_jload(row[fields[d][0]]) if fields[d][1] == 'json' else row[fields[d][0]])} "
                        f"원본={_short(item[d])}" for d in hard))
                if soft:
                    rep.notes.append(f"운영자 결정으로 바뀐 값(손실 아님): {name} {key}: " + ", ".join(
                        f"{d} DB={_short(row[fields[d][0]])} 원본={_short(item[d])}" for d in soft))
            if name == "people_registry":
                _check_pending_roles(conn, key, item.get("pending_role_changes") or [], rep)
        rep.counts[count_key] = len(items)
        rep.counts[f"{count_key}_db"] = len(db_rows)
        rep.basis[count_key] = (f"{rel} {list_key}[] {len(items)}건 ↔ {table}: 레코드 {n_present}건 존재, 원본 필드 전부 "
                                f"대응 컬럼과 값 대조 (DB 전체 {len(db_rows)}행 — 대쉬보드 등록분 포함)")
    rep.counts["registry_fields_compared"] = compared
    rep.basis["registry_fields_compared"] = "레지스트리 3종 원본 (레코드×필드) 값 대조 횟수"


def _check_pending_roles(conn: sqlite3.Connection, person_id: str, items: list, rep: Report) -> None:
    for it in items:
        pid = it.get("proposal_id") if isinstance(it, dict) else None
        if pid and conn.execute("SELECT 1 FROM sv_person_role_history WHERE proposal_id=?", (pid,)).fetchone():
            continue
        prop = conn.execute("SELECT status FROM sv_audit_proposal WHERE proposal_id=?", (pid,)).fetchone() if pid \
            else None
        if pid and prop is not None and prop["status"] == "scheduled":
            rep.failures.append(f"pending_role_change 미반영: {person_id} ← {pid}")
        else:
            rep.exclusions.setdefault("pending_role_changes_skipped", []).append(
                {"person_id": person_id, "proposal_id": pid,
                 "reason": "결합할 scheduled 제안이 DB 에 없음 — sync 설계상 대기 행을 만들지 않음"})


# ── ⑤ 런 ─────────────────────────────────────────────────────────────────────────

def _check_runs(root: Path, conn: sqlite3.Connection, rep: Report) -> None:
    for count_key, subdir, stem, table, key_col, id_key in RUN_SPECS:
        db_rows = {r[key_col]: r for r in conn.execute(f"SELECT * FROM {table}")}
        files = sorted((root / subdir).glob(f"*/{stem}.json")) if (root / subdir).is_dir() else []
        seen: set[str] = set()
        for f in files:
            rel = f.relative_to(root).as_posix()
            data_bytes = f.read_bytes()
            try:
                data = json.loads(data_bytes.decode("utf-8"))
                rid = data.get(id_key)
            except (ValueError, AttributeError):
                rep.failures.append(f"런 미반영: {rel} (JSON 파싱 불가)")
                continue
            row = db_rows.get(rid)
            if row is None:
                rep.failures.append(f"런 미반영: {rel} ({id_key}={rid})")
                continue
            seen.add(rid)
            if row["payload_json"].encode("utf-8") != data_bytes:
                same = _jload(row["payload_json"]) == data
                rep.failures.append(f"런 payload 바이트 불일치: {rel}" + (" (JSON 내용은 같음 — 원문 바이트 보존 위반)"
                                                                       if same else ""))
            if row["payload_sha256"] != _sha256_bytes(data_bytes):
                rep.failures.append(f"런 payload_sha256 불일치: {rel}")
            md = f.with_suffix(".md")
            if md.is_file() and row["md_path"] != md.relative_to(root).as_posix():
                rep.failures.append(f"런 md_path 불일치: {rel}: DB={row['md_path']}")
            if (f.parent / "raw").is_dir() and row["raw_dir"] != (f.parent / "raw").relative_to(root).as_posix():
                rep.failures.append(f"런 raw_dir 불일치: {rel}: DB={row['raw_dir']}")
        extra = sorted(set(db_rows) - seen)
        if extra:
            rep.notes.append(f"{table}: 볼륨 파일 없는 DB 행 {len(extra)}건: {extra[:5]}")
        rep.counts[count_key] = len(files)
        rep.basis[count_key] = (f"{subdir}/*/{stem}.json {len(files)}개 ↔ {table}: payload_json 이 파일 바이트와 동일 "
                                f"+ payload_sha256 (DB {len(db_rows)}행)")


# ── ⑥ 정본 타임라인 MD ─────────────────────────────────────────────────────────────

@dataclass
class _Block:
    line: int
    text: str
    in_topic: bool
    date: str | None = None
    title: str | None = None
    speaker: str | None = None


def scan_canonical(text: str) -> dict:
    """파서와 독립된 구조 스캔. 줄은 '\\n' 으로만 나눈다(끝 '\\r' 제거) — 인용은 `> ` 뒤 바이트 그대로."""
    lines = [ln[:-1] if ln.endswith("\r") else ln for ln in text.split("\n")]
    blocks: list[_Block] = []
    nonstandard: list[int] = []
    master_rows: list[str] = []
    speakers: list[str] = []
    section, date, title, speaker = None, None, None, None
    i = 0
    while i < len(lines):
        ln = lines[i]
        if _RE_H1.match(ln):
            section = "topic" if _RE_TOPIC_H1.match(ln) else "master" if "마스터" in ln else "other"
            date = title = speaker = None
        elif section == "master" and _RE_MASTER_ROW.match(ln):
            master_rows.append(_RE_MASTER_ROW.match(ln).group(1))
        elif section == "topic" and _RE_EVENT.match(ln):
            m = _RE_EVENT.match(ln)
            date, title, speaker = m.group(1), m.group(2), None
        elif section == "topic" and _RE_H2.match(ln):
            date = title = speaker = None
        elif section == "topic" and _RE_SPEAKER.match(ln):
            speaker = _RE_SPEAKER.match(ln).group(1).strip()
            speakers.append(speaker)
        if ln.startswith(">"):
            start = i
            buf: list[str] = []
            while i < len(lines) and lines[i].startswith(">"):
                m = _RE_QUOTE_LINE.match(lines[i])
                if m is None:
                    nonstandard.append(i + 1)
                    buf.append(lines[i][1:])
                else:
                    buf.append(m.group(1) or "")
                i += 1
            blocks.append(_Block(line=start + 1, text="\n".join(buf), in_topic=section == "topic", date=date,
                                 title=title, speaker=speaker))
            continue
        i += 1
    return {"blocks": blocks, "master_rows": master_rows, "speakers": speakers, "nonstandard": nonstandard}


def _find_canonical(root: Path, manifest: dict | None, meta: dict, canonical_md: Path | None) -> Path | None:
    if canonical_md is not None:
        return Path(canonical_md)
    cands = [meta.get("canonical_timeline_path")]
    for a in (manifest or {}).get("artifacts", []) or []:
        if isinstance(a, dict) and a.get("name") == CANONICAL_TIMELINE_ARTIFACT:
            cands.append(a.get("path"))
    for rel in cands:
        p = _under(root, rel)
        if p is not None and p.is_file():
            return p
    globbed = sorted(root.glob(CANONICAL_TIMELINE_GLOB))
    return globbed[-1] if globbed else None


def _speaker_ok(db_speaker: str, heading: str | None) -> bool:
    if not heading:
        return False
    return db_speaker == heading or db_speaker in [p.strip() for p in heading.split("·")]


def _check_canonical(root: Path, conn: sqlite3.Connection, manifest: dict | None, canonical_md: Path | None,
                     rep: Report) -> None:
    meta = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM sv_meta")}
    md = _find_canonical(root, manifest, meta, canonical_md)
    if md is None or not md.is_file():
        rep.failures.append("정본 MD 없음 — 정본 검사를 할 수 없음")
        return
    raw = md.read_bytes()
    if meta.get("canonical_timeline_sha256") != _sha256_bytes(raw):
        rep.failures.append(f"정본 MD 미반영(sha 불일치): {md.name} — DB 는 다른 판의 정본을 적재했다")
    text = raw.decode("utf-8-sig")
    scan = scan_canonical(text)
    topic_blocks = [b for b in scan["blocks"] if b.in_topic]
    excluded = [b for b in scan["blocks"] if not b.in_topic]
    for ln in scan["nonstandard"]:
        rep.failures.append(f"비표준 인용 줄: L{ln} ('>' 뒤 공백 없음 — 파서가 인용으로 보지 않는다)")
    for b in excluded:
        rep.exclusions["excluded_blockquotes"].append(
            {"line": b.line, "text": b.text, "reason": "주제 섹션(# N. 주제 …) 밖 `> ` — 편집 주석, 발언 인용 아님"})

    events = list(conn.execute("SELECT event_id, event_date FROM sv_event WHERE origin='canonical_md'"))
    statements = list(conn.execute("SELECT statement_id, speaker_name FROM sv_statement WHERE origin='canonical_md'"))
    quotes = list(conn.execute("SELECT * FROM sv_quote WHERE origin='canonical_md'"))
    event_date = {r["event_id"]: r["event_date"] for r in events}

    if len(events) != len(scan["master_rows"]):
        rep.failures.append(f"정본 행사 수 불일치: 마스터 표 {len(scan['master_rows'])}행 ≠ sv_event {len(events)}행")
    lost_dates = Counter(scan["master_rows"]) - Counter(event_date.values())
    for d in sorted(lost_dates):
        rep.failures.append(f"정본 행사 미반영: {d} (마스터 표 행 {lost_dates[d]}개가 sv_event 에 없음)")
    if len(statements) != len(scan["speakers"]):
        rep.failures.append(f"정본 발언블록 수 불일치: 주제 섹션 `### ` {len(scan['speakers'])}개 ≠ "
                            f"sv_statement {len(statements)}행")
    lost_sp = Counter(scan["speakers"]) - Counter(r["speaker_name"] for r in statements)
    for sp in sorted(lost_sp):
        rep.failures.append(f"정본 발언블록 미반영: {sp} ×{lost_sp[sp]}")

    # 인용: 블록 → (같은 행사일·발언자의) 바이트 동일 sv_quote
    by_text: dict[str, list] = {}
    by_norm: dict[str, list] = {}
    for q in quotes:
        by_text.setdefault(q["quote_text"], []).append(q)
        by_norm.setdefault(canonical_parse.norm(q["quote_text"]), []).append(q)

    def fits(q, b: _Block) -> bool:
        return _speaker_ok(q["speaker_name"], b.speaker) and event_date.get(q["event_id"]) == b.date

    matched: set[str] = set()
    lines_of: dict[str, list[int]] = {}
    missing: dict[tuple, list[int]] = {}
    inexact: dict[tuple, list[int]] = {}
    for b in topic_blocks:
        hit = [q for q in by_text.get(b.text, []) if fits(q, b)]
        if hit:
            for q in hit:
                matched.add(q["quote_id"])
                lines_of.setdefault(q["quote_id"], []).append(b.line)
            continue
        key = (b.speaker, b.date, b.text)
        if [q for q in by_norm.get(canonical_parse.norm(b.text), []) if fits(q, b)]:
            inexact.setdefault(key, []).append(b.line)
        else:
            missing.setdefault(key, []).append(b.line)
    for (sp, d, t), lns in missing.items():
        rep.failures.append(f"정본 인용 블록 미반영: {d} {sp} {','.join(f'L{n}' for n in lns)} — {_short(t, 40)}")
    for (sp, d, t), lns in inexact.items():
        rep.failures.append(f"정본 인용 블록 원문 불일치: {d} {sp} {','.join(f'L{n}' for n in lns)} — 정규화로만 같고 "
                            f"바이트가 다르다")
    for q in quotes:
        if q["quote_id"] not in matched:
            rep.failures.append(f"DB 인용이 정본에 없음: {q['quote_id']} {q['speaker_name']} — "
                                f"quote_text 가 MD 의 어떤 `> ` 블록과도 바이트 동일하지 않다")

    # 파서 교차확인 — 파서가 본 블록 목록이 구조 스캔과 같아야 한다(파서가 조용히 버린 블록이 없음)
    try:
        res = canonical_parse.parse_timeline(md, root=root)
        if res.quote_blocks != [b.text for b in topic_blocks]:
            rep.failures.append(f"파서·구조 스캔 불일치: 인용 블록 파서 {len(res.quote_blocks)} ≠ 스캔 {len(topic_blocks)}")
        if len(res.excluded_blockquotes) != len(excluded):
            rep.failures.append(f"파서·구조 스캔 불일치: 제외 블록 파서 {len(res.excluded_blockquotes)} ≠ 스캔 {len(excluded)}")
    except Exception as exc:  # noqa: BLE001
        rep.failures.append(f"파서·구조 스캔 불일치: 정본 파싱 실패: {exc}")

    # locator_match
    lm = Counter(q["locator_match"] for q in quotes)
    known_hit: set[int] = set()
    n_known = n_unknown = 0
    for q in quotes:
        if q["locator_match"] != "none":
            continue
        qsha = _sha256_bytes(q["quote_text"].encode("utf-8"))
        idx = next((i for i, k in enumerate(KNOWN_SOURCE_DISCREPANCIES)
                    if k.get("kind") == "locator_match_none" and k.get("speaker_name") == q["speaker_name"]
                    and k.get("quote_sha256") == qsha), None)
        if idx is None:
            n_unknown += 1
            rep.failures.append(f"locator_match=none: {q['quote_id']} {q['speaker_name']} "
                                f"{','.join(f'L{n}' for n in lines_of.get(q['quote_id'], []))} — 원문 대조 실패이며 "
                                f"알려진 소스 결함이 아니다")
        else:
            n_known += 1
            known_hit.add(idx)
            k = KNOWN_SOURCE_DISCREPANCIES[idx]
            rep.known_discrepancies.append({
                "kind": k["kind"], "speaker_name": q["speaker_name"], "quote_id": q["quote_id"],
                "event_id": q["event_id"], "md_lines": lines_of.get(q["quote_id"], []), "locator": q["locator"],
                "quote_sha256": qsha, "reason": k["reason"]})
    for i, k in enumerate(KNOWN_SOURCE_DISCREPANCIES):
        if i not in known_hit:
            rep.notes.append(f"알려진 소스 결함 항목이 이번 데이터에 없음(소스가 고쳐졌거나 다른 스냅샷): "
                             f"{k.get('kind')} {k.get('speaker_name')} {str(k.get('quote_sha256'))[:12]}")
    if lm.get("unchecked"):
        rep.notes.append(f"locator 없는 인용 {lm['unchecked']}건(locator_match=unchecked) — 원문 대조 대상 없음")

    n_rows = len(matched)
    rep.counts.update(
        events=len(scan["master_rows"]), statements=len(scan["speakers"]), quote_blocks_md=len(topic_blocks),
        quote_blocks_excluded=len(excluded), quote_blocks_distinct_texts=len({b.text for b in topic_blocks}),
        quotes_db=len(quotes), quotes_matched=n_rows,
        locator_match={k: lm.get(k, 0) for k in ("range", "file", "none", "unchecked")},
        quotes_locator_none_known=n_known, quotes_locator_none_unknown=n_unknown)
    rel = md.relative_to(root).as_posix() if md.is_relative_to(root) else str(md)
    rep.basis.update(
        events=f"{rel} '# … 마스터 …' 표의 '| **YYYY-MM-DD** |' 행 {len(scan['master_rows'])} ↔ sv_event {len(events)}행",
        statements=f"주제 섹션(# N. 주제 …)의 '### ' 헤딩 {len(scan['speakers'])} ↔ sv_statement {len(statements)}행 "
                   f"(서두 범례 '### ' 는 주제 섹션 밖이라 제외)",
        quote_blocks_md=f"주제 섹션의 `> ` 블록(연속 줄 = 1블록) {len(topic_blocks)} — 교차 표시 중복 포함; "
                        f"주제 섹션 밖 {len(excluded)}블록은 exclusions",
        quote_blocks_excluded=f"주제 섹션 밖 `> ` 블록 {len(excluded)} — 편집 주석(exclusions.excluded_blockquotes 에 줄·원문 열거)",
        quotes_db=f"sv_quote(origin=canonical_md) {len(quotes)}행 — (행사, 발언자, 정규화 문구) 유일; 블록 "
                  f"{len(topic_blocks)}개가 전부 같은 행사일·발언자의 바이트 동일 행으로 귀결({n_rows}행), 역방향도 전부",
        locator_match="sv_quote.locator_match 분포 — none 은 KNOWN_SOURCE_DISCREPANCIES 에 있어야만 허용")


# ── 진입점 ─────────────────────────────────────────────────────────────────────────

def verify(root: str | Path, db_path: str | Path, source_dir: str | Path | None = None,
           canonical_md: str | Path | None = None) -> Report:
    rep = Report()
    root, db_path = Path(root), Path(db_path)
    if not Path(db_path).is_file():
        rep.failures.append(f"DB 없음: {db_path}")
        return rep
    if not root.is_dir():
        rep.failures.append(f"볼륨 root 없음: {root}")
        return rep
    conn = _connect_ro(db_path)
    try:
        have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        lacking = [t for t in store.TABLES if t not in have]
        if lacking:
            rep.failures.append(f"DB 에 sv_* 테이블 없음: {lacking} — import 가 돌지 않았다")
            return rep
        manifest = _check_repository(root, rep)
        _check_mirror(root, conn, Path(source_dir) if source_dir else None, rep)
        if manifest is not None:
            _check_manifest_db(manifest, conn, rep)
        _check_registries(root, conn, rep)
        _check_runs(root, conn, rep)
        _check_canonical(root, conn, manifest, Path(canonical_md) if canonical_md else None, rep)
    finally:
        conn.close()
    return rep


_TEXT_COUNT_KEYS = ("people", "source_domains", "proposals", "sources", "artifacts", "monitoring_runs", "audit_runs",
                    "events", "statements", "quote_blocks_md", "quote_blocks_excluded", "quotes_db",
                    "mirrored_files", "sync_index_files", "json_files", "manifest_hashes_matched",
                    "registry_fields_compared")


def format_text(rep: Report, *, root: Path, db_path: Path, source_dir: Path | None) -> str:
    c = rep.counts
    out = [f"stakeholder verify: {'PASS' if rep.ok else 'FAIL'} failures={len(rep.failures)}",
           f"root={root} db={db_path} source_dir={source_dir or '-'}", "counts:"]
    for k in _TEXT_COUNT_KEYS:
        if k in c:
            out.append(f"  {k:<26}{'-' if c[k] is None else c[k]!s:>6}  ← {rep.basis.get(k, '')}")
    integ = c.get("integrity") or {}
    if integ:
        out.append("  integrity                     sources " + " ".join(f"{k}={v}" for k, v in integ["sources"].items())
                   + " | artifacts " + " ".join(f"{k}={v}" for k, v in integ["artifacts"].items()))
    if c.get("locator_match"):
        out.append("  locator_match                 " + " ".join(f"{k}={v}" for k, v in c["locator_match"].items())
                   + f" (none: known={c.get('quotes_locator_none_known')} unknown={c.get('quotes_locator_none_unknown')})")
    if c.get("mirrored_files") is not None:
        out.append(f"  mirror                        identical={c['mirror_identical']} mismatch={c['mirror_mismatch']} "
                   f"missing={c['mirror_missing']}")
    out.append("exclusions (손실 아님 — 명시적 열거):")
    for k, items in rep.exclusions.items():
        out.append(f"  {k} ({len(items)})")
        for it in items:
            if isinstance(it, dict):
                head = it.get("path") or it.get("source_id") or (f"L{it['line']}" if "line" in it else "")
                body = _short(it.get("text"), 50) if it.get("text") else ""
                out.append(f"    - {head} {body + ' ' if body else ''}[{it.get('reason', '')}]")
            else:
                out.append(f"    - {it}")
    out.append(f"known source discrepancies ({len(rep.known_discrepancies)}):")
    for k in rep.known_discrepancies:
        lines = ",".join(f"L{n}" for n in k["md_lines"])
        out.append(f"  - {k['kind']} {k['speaker_name']} {lines} quote_id={k['quote_id']} locator={k['locator']}")
        out.append(f"    {k['reason']}")
    out.append(f"notes ({len(rep.notes)}):")
    out += [f"  - {n}" for n in rep.notes]
    out.append(f"failures ({len(rep.failures)}):")
    out += [f"  - {f}" for f in rep.failures]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="scripts/stakeholder_verify_import.py",
                                 description="Stakeholder 이관 손실 없음 검증(볼륨 root + sv_* DB, 읽기 전용)")
    ap.add_argument("--root", required=True, help="볼륨 root (data/stakeholder 또는 임시 root)")
    ap.add_argument("--db", required=True, help="sv_* 가 있는 DB (mode=ro 로 연다)")
    ap.add_argument("--source-dir", help="소스 repo 체크아웃 — 있으면 허용목록 파일 전부를 볼륨과 바이트 대조")
    ap.add_argument("--canonical-md", help="정본 타임라인 MD 경로(기본: sv_meta·manifest·glob 으로 찾음)")
    ap.add_argument("--json", action="store_true", help="JSON 출력")
    args = ap.parse_args(argv)
    rep = verify(args.root, args.db, source_dir=args.source_dir, canonical_md=args.canonical_md)
    if args.json:
        print(json.dumps(rep.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(format_text(rep, root=Path(args.root), db_path=Path(args.db),
                          source_dir=Path(args.source_dir) if args.source_dir else None))
    return 0 if rep.ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
