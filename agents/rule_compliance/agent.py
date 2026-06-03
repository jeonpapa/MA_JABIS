"""RuleComplianceAgent — 메모리(합의 사항) ↔ 런타임 증거 일치 여부 감사.

설계 원칙:
- 사용자-Claude 대화에서 합의된 룰은 `~/.claude/projects/.../memory/MEMORY.md` 에 기록됨
- 이 에이전트는 MEMORY.md 를 소스 오브 트루스로 보고, 각 항목에 대한 **실행 증명**을 시도
- 증거 수집은 `checks.py` 의 함수 레지스트리로 구현 (신규 메모리 추가 시 함수 하나 추가)
- 검증 불가한 메모리(개발 관행·process state)는 SKIP 로 명시 — 묵시적 통과 금지

트리거:
- 매일 05:30 Asia/Seoul — `scheduler.compliance_audit_job` (QG 06:00 리뷰 직전)
- 수동: `python -m agents.rule_compliance [--write-report]`

출력:
- stdout: 요약 테이블
- `quality_guard/compliance_YYYY-MM-DD.md`: 전체 보고서
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from pathlib import Path

from .checks import CHECKS, SKIP_WITH_REASON, CheckResult

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[2]

# `~/.claude/projects/-Users-<user>-MA-AI-Dossier/memory/` 경로 자동 해석
# 로컬 경로는 시스템에 따라 다르므로 환경변수 > 자동탐지 > 기본값 순
_DEFAULT_MEMORY_DIR = (
    Path.home()
    / ".claude" / "projects"
    / "-Users-kimjeong-ae-MA-AI-Dossier" / "memory"
)


def _resolve_memory_dir() -> Path:
    override = os.environ.get("CLAUDE_MEMORY_DIR")
    if override:
        p = Path(override)
        if p.exists():
            return p
    if _DEFAULT_MEMORY_DIR.exists():
        return _DEFAULT_MEMORY_DIR
    # 프로젝트 상대 경로 fallback (개발 환경)
    for candidate in (
        Path.home() / ".claude" / "projects",
    ):
        if candidate.exists():
            for sub in candidate.iterdir():
                mem = sub / "memory"
                if mem.exists():
                    return mem
    raise FileNotFoundError("memory 디렉터리를 찾을 수 없음. CLAUDE_MEMORY_DIR 환경변수로 지정하세요.")


_INDEX_LINE_RE = re.compile(r"^- \[(?P<title>[^\]]+)\]\((?P<path>[^)]+\.md)\)\s*—\s*(?P<hook>.+)$")


class RuleComplianceAgent:
    """메모리 ↔ 런타임 일치 감사."""

    def __init__(self, memory_dir: Path | None = None, project_root: Path | None = None):
        self.memory_dir = memory_dir or _resolve_memory_dir()
        self.project_root = project_root or BASE_DIR
        self.report_dir = self.project_root / "quality_guard"
        self.report_dir.mkdir(parents=True, exist_ok=True)

    # ── 인덱스 파싱 ────────────────────────────────────────────────────────
    def load_index(self) -> list[dict]:
        """MEMORY.md 를 파싱하여 각 항목 `{name, title, hook, file}` 반환."""
        idx_path = self.memory_dir / "MEMORY.md"
        if not idx_path.exists():
            return []
        out: list[dict] = []
        for line in idx_path.read_text(encoding="utf-8").splitlines():
            m = _INDEX_LINE_RE.match(line.strip())
            if not m:
                continue
            name = Path(m.group("path")).stem
            out.append({
                "name": name,
                "title": m.group("title").strip(),
                "hook": m.group("hook").strip(),
                "file": m.group("path"),
            })
        return out

    # ── 감사 실행 ──────────────────────────────────────────────────────────
    def audit(self) -> list[CheckResult]:
        entries = self.load_index()
        results: list[CheckResult] = []
        for entry in entries:
            name = entry["name"]
            if name in CHECKS:
                try:
                    r = CHECKS[name](name, self.project_root)
                except Exception as e:
                    logger.exception("check 실패: %s", name)
                    r = CheckResult(name, "skip", f"check 예외: {e}")
            elif name in SKIP_WITH_REASON:
                r = CheckResult(name, "skip", SKIP_WITH_REASON[name])
            else:
                r = CheckResult(name, "skip", "신규 메모리 — rule_compliance/checks.py 에 체크 함수 추가 검토")
            r.__dict__["_title"] = entry["title"]
            r.__dict__["_file"] = entry["file"]
            results.append(r)
        return results

    # ── 리포트 ─────────────────────────────────────────────────────────────
    def render_markdown(self, results: list[CheckResult], ts: datetime) -> str:
        passes = [r for r in results if r.status == "pass"]
        fails  = [r for r in results if r.status == "fail"]
        skips  = [r for r in results if r.status == "skip"]
        lines = [
            f"# Rule Compliance 감사 — {ts.strftime('%Y-%m-%d %H:%M')}",
            "",
            "## 요약",
            f"- ✅ PASS: **{len(passes)}건**",
            f"- ❌ FAIL: **{len(fails)}건**",
            f"- ⏭ SKIP: **{len(skips)}건** (런타임 검증 불가)",
            f"- 전체 메모리: {len(results)}건",
            "",
        ]
        if fails:
            lines.append("## ❌ FAIL — 즉시 확인 필요")
            for r in fails:
                title = r.__dict__.get("_title", r.memory)
                lines.append(f"- **{title}** — {r.detail}")
                if r.metrics:
                    lines.append(f"  - 수치: `{r.metrics}`")
            lines.append("")
        if passes:
            lines.append("## ✅ PASS — 실행 증명 확보")
            for r in passes:
                title = r.__dict__.get("_title", r.memory)
                lines.append(f"- **{title}** — {r.detail}")
            lines.append("")
        if skips:
            lines.append("## ⏭ SKIP — 런타임 신호 없음")
            for r in skips:
                title = r.__dict__.get("_title", r.memory)
                lines.append(f"- _{title}_ — {r.detail}")
            lines.append("")

        # RSA 후보 — MI agent 변동사유 cache 에서 rsa_candidate 가 들어있는 항목 surface
        rsa_candidates = self._collect_rsa_candidates()
        if rsa_candidates:
            lines.append("## ⚠ RSA 후보 — 사용자 검증 필요")
            lines.append("")
            lines.append("MI agent 변동사유 분석에서 RSA 단서 검출. registry 미등록 상태이며 검증 후 등록 권장.")
            lines.append("")
            for c in rsa_candidates:
                lines.append(
                    f"- **{c['brand']}** — hint: `{c.get('hint_type') or '유형 불명'}`, "
                    f"signals: {c.get('signals_detected', [])}"
                )
                if c.get("cache_files"):
                    lines.append(f"  - 출처 cache: {', '.join(c['cache_files'][:3])}")
            lines.append("")
            lines.append("등록: `POST /api/admin/rsa-registry` 또는 대쉬보드 비교 카드의 [RSA 등록] 버튼 사용.")
            lines.append("")

        return "\n".join(lines)

    def _collect_rsa_candidates(self) -> list[dict]:
        """data/dashboard/reason_cache 에서 rsa_candidate 가 있는 cache 파일 수집."""
        import json as _json
        cache_dir = self.project_root / "data" / "dashboard" / "reason_cache"
        if not cache_dir.exists():
            return []
        seen: dict[str, dict] = {}
        for f in cache_dir.glob("MI_*.json"):
            try:
                d = _json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            cand = d.get("rsa_candidate")
            if not cand:
                continue
            brand = cand.get("brand") or ""
            if brand in seen:
                seen[brand]["cache_files"].append(f.name)
            else:
                cand_copy = dict(cand)
                cand_copy["cache_files"] = [f.name]
                seen[brand] = cand_copy
        return list(seen.values())

    def write_report(self, results: list[CheckResult]) -> Path:
        ts = datetime.now()
        body = self.render_markdown(results, ts)
        path = self.report_dir / f"compliance_{ts.strftime('%Y-%m-%d')}.md"
        path.write_text(body, encoding="utf-8")
        logger.info("Rule compliance 보고서 작성: %s", path)
        return path

    # ── 간단 요약 (stdout) ────────────────────────────────────────────────
    def summary_lines(self, results: list[CheckResult]) -> list[str]:
        icon = {"pass": "✅", "fail": "❌", "skip": "⏭"}
        out = []
        for r in results:
            title = r.__dict__.get("_title", r.memory)
            out.append(f"{icon[r.status]} {title[:50]:50} — {r.detail[:80]}")
        return out
