"""QualityGuardAgent — 감시 · 기록 · 보완 · 리뷰 · 제안.

규칙: agents/rules/quality_guard_rules.md
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from .checks import (
    check_code_pattern,
    check_db_records,
    check_scraper_output,
    validate_keytruda,
)
from .deviations import BASE_DIR, DEVIATION_LOG, GUARD_DIR, _write_deviation

logger = logging.getLogger(__name__)


class QualityGuardAgent:
    """전체 개발·운영 과정을 모니터링하고 편차를 기록·보완하는 에이전트.

    사용 예시:
        guard = QualityGuardAgent()
        results = guard.validate_scraper_results(raw_results, "UK", "uk_mims.py")
        guard.run_keytruda_validation("UK", results)
        guard.scan_codebase()
        guard.generate_daily_report()
    """

    # kr_mfds_approval_agent_rules.md §9 verification baseline
    # 변경 시 해당 룰 파일도 동기화할 것
    MFDS_OFFICIAL_BASELINE: dict[str, str] = {
        "keytruda_nsclc_adjuvant_resectable_mono":            "2024-05-14",
        "keytruda_nsclc_perioperative_resectable_mono":       "2023-12-19",
        "keytruda_tnbc_perioperative_mono":                   "2022-07-13",
        "keytruda_hnscc_perioperative_resectable_pdl1_1_mono": "2025-10-02",
        "keytruda_mel_adjuvant_adjuvant_mono":                "2019-05-13",
        "keytruda_rcc_adjuvant_adjuvant_mono":                "2022-08-22",
        "lynparza_bc_adjuvant_adjuvant_brca_mut_mono":        "2023-02-23",
        "welireg_vhl_mono":                                   "2023-05-23",
    }

    def __init__(self):
        GUARD_DIR.mkdir(parents=True, exist_ok=True)

    # ── 스크레이퍼 결과 검증 ───────────────────────────────────────────────

    def validate_scraper_results(
        self,
        results: list[dict],
        country: str,
        scraper_file: str = "",
    ) -> list[dict]:
        """스크레이퍼 결과 검증 + 자동 보완 후 반환."""
        logger.info("[QualityGuard] [%s] 스크레이퍼 결과 검증: %d건", country, len(results))
        return check_scraper_output(results, country, scraper_file)

    def validate_db_records(self, records: list[dict], country: str) -> None:
        """DB 저장 전 레코드 검증."""
        check_db_records(records, country)

    # ── Keytruda Validation ────────────────────────────────────────────────

    def run_keytruda_validation(self, country: str, results: list[dict]) -> bool:
        """Keytruda 기준으로 스크레이퍼 정상 동작 검증."""
        return validate_keytruda(results, country)

    # ── 코드 스캔 ─────────────────────────────────────────────────────────

    def scan_codebase(self, target_dir: Optional[Path] = None) -> list[str]:
        """전체 코드베이스에서 금지 패턴 스캔. quality_guard 패키지 자체는 제외."""
        scan_dir = target_dir or (BASE_DIR / "agents")
        all_issues: list[str] = []

        for py_file in scan_dir.rglob("*.py"):
            if "__pycache__" in str(py_file):
                continue
            if py_file.parent.name == "quality_guard":
                continue  # 자기 자신(패키지) 은 스캔 제외
            issues = check_code_pattern(py_file)
            all_issues.extend(issues)

        if all_issues:
            logger.warning("[QualityGuard] 코드 스캔 결과: %d건 문제 발견", len(all_issues))
        else:
            logger.info("[QualityGuard] 코드 스캔 완료: 문제 없음 ✅")

        return all_issues

    # ── 회귀 탐지: MFDS 공식일 baseline ────────────────────────────────────

    def check_mfds_baseline(self) -> list[dict]:
        """DB 의 MFDS 공식 승인일이 baseline 과 일치하는지 점검.

        kr_mfds_approval_agent_rules.md §9 의 8개 indication 은 peri/adj/neo
        분류 로직의 회귀 지시자. 한 건이라도 불일치 시 ERROR 로 기록한다.
        """
        db_path = BASE_DIR / "data" / "db" / "drug_prices.db"
        if not db_path.exists():
            return []

        mismatches: list[dict] = []
        with sqlite3.connect(db_path) as conn:
            for ind_id, expected in self.MFDS_OFFICIAL_BASELINE.items():
                row = conn.execute(
                    "SELECT approval_date, date_source FROM indications_by_agency "
                    "WHERE agency='MFDS' AND indication_id=?",
                    (ind_id,),
                ).fetchone()
                if row is None:
                    mismatches.append({
                        "indication_id": ind_id,
                        "expected": expected,
                        "actual": None,
                        "reason": "DB 에 row 없음",
                    })
                    continue
                actual_date, src = row
                if actual_date != expected:
                    mismatches.append({
                        "indication_id": ind_id,
                        "expected": expected,
                        "actual": actual_date,
                        "date_source": src,
                        "reason": "baseline 불일치 (peri/adj/neo 매칭 회귀 가능)",
                    })

        for m in mismatches:
            _write_deviation({
                "severity": "ERROR",
                "agent": "QualityGuard",
                "deviation_type": "mfds_baseline_regression",
                "description": f"MFDS baseline 불일치: {m['indication_id']} "
                               f"expected={m['expected']} actual={m['actual']}",
                "expected": m["expected"],
                "actual": str(m.get("actual")),
                "corrective_action": "kr_mfds_indication_mapper.LOT_KR 확인 + "
                                     "python -m scripts.apply_mfds_official_dates --all --apply",
            })
        return mismatches

    # ── 규칙 ↔ 코드 drift 탐지 ─────────────────────────────────────────────

    _MFDS_UPDATE_PATTERN = re.compile(
        r"UPDATE\s+indications_by_agency[\s\S]{0,300}?approval_date",
        re.IGNORECASE,
    )
    _DRIFT_SKIP_FILES = {
        "db.py", "builders.py", "kr_mfds.py",
        "apply_mfds_official_dates.py",
    }

    def scan_rule_drift(self) -> list[dict]:
        """CLAUDE.md 핵심 원칙이 구현에서 어긋나는지 스캔.

        현재 점검 항목:
          - indications_by_agency 에 MFDS approval_date 를 UPDATE 하면서
            date_source 를 함께 설정하지 않는 파일
        """
        drifts: list[dict] = []
        agents_dir = BASE_DIR / "agents"

        for py in agents_dir.rglob("*.py"):
            if "__pycache__" in str(py) or py.name in self._DRIFT_SKIP_FILES:
                continue
            if py.parent.name == "quality_guard":
                continue  # 패키지 자체 제외
            text = py.read_text(encoding="utf-8", errors="ignore")
            if not self._MFDS_UPDATE_PATTERN.search(text):
                continue
            if "'MFDS'" not in text and '"MFDS"' not in text:
                continue
            if "date_source" in text:
                continue
            drifts.append({
                "rule": "foreign_approval_agent_rules.md §5 — MFDS approval_date 는 date_source 확인 필수",
                "file": str(py.relative_to(BASE_DIR)),
                "severity": "WARNING",
            })

        for d in drifts:
            _write_deviation({
                "severity": d["severity"],
                "agent": "QualityGuard",
                "file": d["file"],
                "deviation_type": "rule_code_drift",
                "description": f"{d['rule']} → {d['file']}",
                "corrective_action": "해당 파일에서 룰 준수 여부 재확인",
            })
        return drifts

    # ── 개선 제안 생성 ─────────────────────────────────────────────────────

    def generate_suggestions(self) -> list[str]:
        """경량 휴리스틱 기반 개선 제안 (LLM 없이).

        - 700 줄 초과 파일: 분리 제안
        - 미해결 편차 20건 초과: 트리아지 제안
        - MFDS 자동화 gap 점검
        - DISEASE_KR 미커버 disease 탐지
        """
        suggestions: list[str] = []

        # 1) 큰 파일
        for py in (BASE_DIR / "agents").rglob("*.py"):
            if "__pycache__" in str(py):
                continue
            try:
                n = len(py.read_text(encoding="utf-8", errors="ignore").splitlines())
            except Exception:
                continue
            if n > 700:
                rel = py.relative_to(BASE_DIR)
                suggestions.append(
                    f"📦 `{rel}` ({n}줄) — 모듈 분리 검토 "
                    f"(scraper/parser/business logic 레이어 구분)"
                )

        # 2) 미해결 편차 누적
        unresolved = self.get_unresolved_deviations()
        if len(unresolved) > 20:
            suggestions.append(
                f"🗂 미해결 편차 {len(unresolved)}건 누적 — `mark_resolved` 로 "
                f"삭제/정리 후 현황 재파악 권장"
            )

        # 3) MFDS 자동화 gap
        orch = BASE_DIR / "agents" / "foreign_approval" / "builders.py"
        if orch.exists():
            text = orch.read_text(encoding="utf-8", errors="ignore")
            if "apply_mfds_official_dates" not in text:
                suggestions.append(
                    "🔗 `ForeignApprovalAgent._build_mfds` 후 "
                    "`scripts.apply_mfds_official_dates.map_indications` 를 "
                    "자동 호출하도록 통합하면 MFDS 공식일 수동 단계가 제거됨 "
                    "(kr_mfds_approval_agent_rules.md §7 TODO)"
                )

        # 4) DISEASE_KR 커버리지 점검
        try:
            db_path = BASE_DIR / "data" / "db" / "drug_prices.db"
            if db_path.exists():
                from agents.hta_scrapers.kr_mfds_indication_mapper import DISEASE_KR
                with sqlite3.connect(db_path) as conn:
                    diseases = {
                        r[0] for r in conn.execute(
                            "SELECT DISTINCT disease FROM indications_master "
                            "WHERE disease IS NOT NULL"
                        ).fetchall()
                    }
                missing = sorted(d for d in diseases if d and d not in DISEASE_KR)
                if missing:
                    suggestions.append(
                        f"🔑 `DISEASE_KR` 미커버 disease: {', '.join(missing)} — "
                        f"해당 indication 은 MFDS 매칭 0 이므로 키워드 추가 필요"
                    )
        except Exception as e:
            logger.debug("DISEASE_KR 점검 생략: %s", e)

        return suggestions

    # ── 종합 리뷰 ──────────────────────────────────────────────────────────

    def review_codebase(self) -> dict:
        """상시 감시 · 회귀 탐지 · 개선 제안을 모두 수행하고 리뷰 보고서 생성."""
        today = date.today().isoformat()
        code_issues = self.scan_codebase()
        rule_drifts = self.scan_rule_drift()
        mfds_regressions = self.check_mfds_baseline()
        suggestions = self.generate_suggestions()

        lines: list[str] = [
            f"# QualityGuard 코드베이스 리뷰 — {today}",
            "",
            "## 요약",
            f"- 코드 패턴 위반: **{len(code_issues)}건**",
            f"- 규칙↔코드 drift: **{len(rule_drifts)}건**",
            f"- MFDS baseline 회귀: **{len(mfds_regressions)}건**",
            f"- 개선 제안: **{len(suggestions)}건**",
            "",
        ]

        if mfds_regressions:
            lines.append("## ❌ MFDS 공식일 회귀 (peri/adj/neo 매칭 의심)")
            for m in mfds_regressions:
                lines.append(
                    f"- `{m['indication_id']}`: expected **{m['expected']}**, "
                    f"actual **{m.get('actual')}** "
                    f"({m.get('reason', '')})"
                )
            lines.append("")
            lines.append("→ `python -m scripts.apply_mfds_official_dates --all --apply` "
                         "+ `agents/rules/kr_mfds_approval_agent_rules.md` §2 LayerSpec 확인")
            lines.append("")

        if rule_drifts:
            lines.append("## ⚠️ 규칙 ↔ 코드 Drift")
            for d in rule_drifts:
                icon = "❌" if d["severity"] == "ERROR" else "⚠️"
                lines.append(f"- {icon} **{d['rule']}** — `{d['file']}`")
            lines.append("")

        if code_issues:
            lines.append("## 🔎 코드 패턴 위반")
            for i in code_issues[:30]:
                lines.append(f"- {i}")
            if len(code_issues) > 30:
                lines.append(f"- ... 외 {len(code_issues) - 30}건")
            lines.append("")

        if suggestions:
            lines.append("## 💡 개선 제안")
            for s in suggestions:
                lines.append(f"- {s}")
            lines.append("")

        if not (code_issues or rule_drifts or mfds_regressions or suggestions):
            lines.append("## ✅ 전 영역 정상 — 별다른 이슈 없음")

        report_path = GUARD_DIR / f"review_{today}.md"
        report_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("[QualityGuard] 리뷰 보고서 생성: %s", report_path)

        return {
            "date": today,
            "code_issues": code_issues,
            "rule_drifts": rule_drifts,
            "mfds_regressions": mfds_regressions,
            "suggestions": suggestions,
            "report_path": str(report_path),
        }

    # ── 편차 로그 조회 ─────────────────────────────────────────────────────

    def get_unresolved_deviations(self) -> list[dict]:
        """미해결 편차 목록 반환."""
        if not DEVIATION_LOG.exists():
            return []

        unresolved: list[dict] = []
        with open(DEVIATION_LOG, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    if not entry.get("resolved", False):
                        unresolved.append(entry)
                except json.JSONDecodeError:
                    continue
        return unresolved

    def mark_resolved(self, deviation_type: str, file: str = "") -> int:
        """특정 편차 유형을 해결로 표시. 처리 건수 반환."""
        if not DEVIATION_LOG.exists():
            return 0

        lines = DEVIATION_LOG.read_text(encoding="utf-8").splitlines()
        updated = 0
        new_lines: list[str] = []

        for line in lines:
            try:
                entry = json.loads(line)
                if (entry.get("deviation_type") == deviation_type
                        and (not file or entry.get("file", "") == file)
                        and not entry.get("resolved", False)):
                    entry["resolved"] = True
                    entry["resolved_at"] = datetime.now().isoformat()
                    updated += 1
                new_lines.append(json.dumps(entry, ensure_ascii=False))
            except json.JSONDecodeError:
                new_lines.append(line)

        DEVIATION_LOG.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        return updated

    # ── 일일 보고서 생성 ───────────────────────────────────────────────────

    def generate_daily_report(self) -> Path:
        """미해결 편차를 요약한 일일 보고서 생성."""
        unresolved = self.get_unresolved_deviations()
        today = date.today().isoformat()

        errors   = [d for d in unresolved if d.get("severity") == "ERROR"]
        warnings = [d for d in unresolved if d.get("severity") == "WARNING"]

        lines = [
            f"# QualityGuard 일일 보고 — {today}",
            "",
            "## 요약",
            f"- 미해결 편차 총계: **{len(unresolved)}건**",
            f"- ERROR: {len(errors)}건 / WARNING: {len(warnings)}건",
            "",
        ]

        if errors:
            lines.append("## ❌ ERROR")
            for e in errors:
                lines.append(
                    f"- [{e.get('file', e.get('agent', ''))}] "
                    f"{e.get('description', '')} "
                    f"→ *{e.get('corrective_action', '')}*"
                )
            lines.append("")

        if warnings:
            lines.append("## ⚠️ WARNING")
            for w in warnings:
                lines.append(
                    f"- [{w.get('file', w.get('agent', ''))}] "
                    f"{w.get('description', '')} "
                    f"→ *{w.get('corrective_action', '')}*"
                )
            lines.append("")

        if not unresolved:
            lines.append("## ✅ 모든 항목 정상 — 편차 없음")

        report_path = GUARD_DIR / f"daily_report_{today}.md"
        report_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("[QualityGuard] 일일 보고서 생성: %s", report_path)
        return report_path

    def print_summary(self) -> None:
        """현재 미해결 편차 요약을 콘솔 출력."""
        unresolved = self.get_unresolved_deviations()
        errors   = [d for d in unresolved if d.get("severity") == "ERROR"]
        warnings = [d for d in unresolved if d.get("severity") == "WARNING"]

        print(f"\n[QualityGuard 현황] 미해결: {len(unresolved)}건 "
              f"(ERROR={len(errors)}, WARNING={len(warnings)})")
        for d in unresolved[:10]:
            icon = "❌" if d.get("severity") == "ERROR" else "⚠️"
            print(f"  {icon} {d.get('description', '')}")
        if len(unresolved) > 10:
            print(f"  ... 외 {len(unresolved)-10}건")
