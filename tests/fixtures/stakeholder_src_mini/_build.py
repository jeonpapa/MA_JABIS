#!/usr/bin/env python3
"""합성 픽스처의 파생값(해시·바이트·1x1 PNG)을 실제 파일에서 다시 계산한다.

픽스처 파일(소스 텍스트, 정본 MD, 레지스트리 등)을 고쳤을 때 이 스크립트를 재실행한다:

    python -I tests/fixtures/stakeholder_src_mini/_build.py

계산 대상:
  * sources/user_input/list.png          — 1x1 PNG (실제 사진 아님)
  * source_manifest.json                 — sha256 / text_sha256 / artifacts sha256·bytes
  * monitoring-runs/*/daily_monitoring.json 의 scope — 레지스트리 해시·활성 인물·승인 출처·스냅샷 id

테스트는 이 스크립트를 부르지 않는다(결과물만 커밋). 표준 라이브러리만 쓴다.
"""
from __future__ import annotations

import hashlib
import json
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(rel: str) -> str:
    return sha256_bytes((ROOT / rel).read_bytes())


def dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


def build_png() -> None:
    """1x1 회색 PNG — 실제 인물 사진·사용자 제공 이미지가 아닌 placeholder."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    raw = b"\x00\x80"  # filter 0 + 1 gray pixel
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9))
           + chunk(b"IEND", b""))
    path = ROOT / "sources/user_input/list.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


def build_manifest() -> None:
    def src(source_id, title, url, source_type, grade, event_date, pub_date, stem):
        return {
            "source_id": source_id, "title": title, "url": url, "source_type": source_type,
            "evidence_grade": grade, "access_status": "acquired_and_text_extracted",
            "local_path": f"sources/raw/{stem}.html", "sha256": sha256_file(f"sources/raw/{stem}.html"),
            "text_path": f"sources/{stem}.txt", "text_sha256": sha256_file(f"sources/{stem}.txt"),
            "event_date": event_date, "publication_date": pub_date,
            "last_live_check": {"checked_at": "2026-10-08T19:50:34+09:00", "http_status": 200,
                                "content_type": "text/html; charset=utf-8"},
        }

    def art(rel, description):
        return {"path": rel, "description": description, "sha256": sha256_file(rel),
                "bytes": (ROOT / rel).stat().st_size}

    manifest = {
        "project": "합성-Stakeholder-픽스처",
        "scope_start_kst": "2025-10-08",
        "scope_end_kst": "2026-10-08",
        "retrieved_at_kst": "2026-10-08T19:31:12+09:00",
        "sources": [
            src("SRC-FAKE-20260903-HONG", "가상 협회 약가 토론 가상 기사", "https://example-news.test/news/1",
                "trade_press_full_article", "C-quote", "2026-09-03", "2026-09-03", "fake_20260903_hong"),
            src("SRC-FAKE-20260619-RWE", "가상 RWE 심포지엄 가상 보도자료", "https://gov.example-news.test/press/rwe-1",
                "official_press_release", "B-summary", "2026-06-19", "2026-06-19", "fake_20260619_rwe"),
            {"source_id": "USER-INPUT-FAKE-LIST", "title": "가상 모니터링 리스트 이미지 (1x1 placeholder)", "url": None,
             "source_type": "user_provided_image", "evidence_grade": "Candidate-only",
             "access_status": "local_preserved", "local_path": "sources/user_input/list.png",
             "sha256": sha256_file("sources/user_input/list.png"),
             "scope_note": "공식 직제·직책·발언 근거로 단독 사용하지 않음"},
        ],
        "withheld_claims": [
            {"claim": "가상 정책으로 연간 약 100억원 절감", "reason": "공식 1차 출처와 정책 범위가 확정되지 않음"},
        ],
        "artifacts": [
            {"name": "topic_timeline_canonical", "path": "2026-10-08_주제별_인물발언_타임라인_정본.md",
             "sha256": sha256_file("2026-10-08_주제별_인물발언_타임라인_정본.md"),
             "bytes": (ROOT / "2026-10-08_주제별_인물발언_타임라인_정본.md").stat().st_size,
             "description": "주제별·날짜별 인물 발언, 직접인용, 내용, 해석, 증거등급 및 출처 검증대장 (합성)"},
            {"path": "scripts/verify_repository.py", "sha256": sha256_file("scripts/verify_repository.py"),
             "bytes": (ROOT / "scripts/verify_repository.py").stat().st_size,
             "description": "JSON, source provenance, artifact SHA-256 및 바이트 크기 검증 스크립트"},
            art("data/policy_signals.json", "확인 근거·분석 추론·미확인 범위를 분리한 정책 시그널 데이터 (합성)"),
            art("2026-10-15_정책시그널_합성.md", "정책 시그널 분석 MD (합성 — 정본 아님, 파싱 대상 아님)"),
            # 아래 둘은 sync 허용목록 밖 — integrity_status='excluded' 픽스처(서비스 코드·대시보드 스케치)
            art("stakeholder_service.py", "합성 서비스 코드 스텁 (허용목록 밖)"),
            art("dashboard-sketches/001-leadership-timeline/index.html", "합성 리더십 스케치 (허용목록 밖)"),
        ],
        "evidence_grade_vocabulary": ["A", "B-quote", "B-summary", "C-quote", "C-summary", "Role-only", "Candidate-only"],
        "topic_timeline_update": {
            "updated_at": "2026-10-12T10:26:10+09:00", "date_window": "2025-10-08/2026-10-08",
            "live_urls_checked": 2, "live_urls_http_200": 2,
            "quote_validation": "합성 픽스처 — 정확 부분문자열 검사 대상 인용 1건",
            "gapfill": "합성", "red_team": "합성 픽스처이며 실제 검토 아님",
        },
        "updated_at_kst": "2026-10-12T10:26:10+09:00",
    }
    (ROOT / "source_manifest.json").write_text(dump(manifest), encoding="utf-8")


def build_run_scopes() -> None:
    people_path = ROOT / "data/people_registry.json"
    source_path = ROOT / "data/source_registry.json"
    people_sha = sha256_bytes(people_path.read_bytes())
    source_sha = sha256_bytes(source_path.read_bytes())
    people = json.loads(people_path.read_text(encoding="utf-8"))["people"]
    sources = json.loads(source_path.read_text(encoding="utf-8"))["sources"]
    snapshot = sha256_bytes(f"{people_sha}:{source_sha}".encode())
    for path in sorted((ROOT / "monitoring-runs").glob("*/daily_monitoring.json")):
        run = json.loads(path.read_text(encoding="utf-8"))
        date = run["run_date_kst"]
        scope = run["scope"]
        scope["scope_snapshot_id"] = snapshot
        scope["people_registry_sha256"] = people_sha
        scope["source_registry_sha256"] = source_sha
        scope["active_people"] = [p for p in people if p["status"] == "active" and p["effective_from"] <= date]
        scope["approved_sources"] = [s for s in sources if s["status"] == "approved" and s["effective_from"] <= date]
        path.write_text(dump(run), encoding="utf-8")


def main() -> None:
    build_png()
    build_manifest()
    build_run_scopes()
    print("fixture derived values rebuilt")


if __name__ == "__main__":
    main()
