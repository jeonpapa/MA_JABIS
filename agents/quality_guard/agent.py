"""QualityGuardAgent — 감시 · 기록 · 보완 · 리뷰 · 제안.

규칙: agents/rules/quality_guard_rules.md
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from .checks import (
    check_code_pattern,
    check_foreign_price_data,
    check_scraper_output,
    summarize_unverified_pack_rows,
)
from .deviations import BASE_DIR, DEVIATION_LOG, GUARD_DIR, _write_deviation

logger = logging.getLogger(__name__)


class QualityGuardAgent:
    """전체 개발·운영 과정을 모니터링하고 편차를 기록·보완하는 에이전트.

    사용 예시:
        guard = QualityGuardAgent()
        results = guard.validate_scraper_results(raw_results, "UK", "uk_mims.py")
        guard.scan_codebase()
        guard.review_codebase()        # 매일 06:00 잡이 부르는 것 — 코드 스캔 + 데이터 검사 + 회귀 + 제안
        guard.generate_daily_report()

    2026-10: check_db_records / validate_keytruda(run_keytruda_validation) 는 삭제 — 어디서도 호출되지 않던 죽은
    안전장치였다. 저장 행의 국가/통화 코드 검사는 `checks.check_foreign_price_data` 가 매일 리뷰에서 수행하고,
    Keytruda 검증은 `tests/test_keytruda_injection_baseline.py` + 같은 데이터 검사가 맡는다.
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
        # 데이터 검사·회귀·제안이 읽는 DB — 테스트/수동 실행은 임시 DB 로 바꿔 끼운다. QG 는 DB 를 읽기만 한다.
        self.db_path: Path = BASE_DIR / "data" / "db" / "drug_prices.db"

    @staticmethod
    def _connect_ro(db_path: Path) -> sqlite3.Connection:
        """QG 의 모든 DB 접근은 read-only — 감시자가 데이터를 바꾸는 일은 없다."""
        return sqlite3.connect(f"{Path(db_path).resolve().as_uri()}?mode=ro", uri=True)

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

    # ── 데이터 검사 (저장 행) ─────────────────────────────────────────────

    def check_foreign_price_data(self) -> list[dict]:
        """해외약가 저장 행 검사 — injection 분모가 농도인 행, 잘못된 국가/통화 코드. review_codebase 가 매일 호출."""
        findings = check_foreign_price_data(self.db_path)
        if findings:
            logger.error("[QualityGuard] 해외약가 데이터 검사: %d건 (%s)", len(findings),
                         ", ".join(sorted({f["check"] for f in findings})))
        return findings

    def check_data_contracts(self) -> list[dict]:
        """보호 행·불변식(data_contracts.md) 검사 — competitor badge, MFDS 병용 허가일, 해외약가 route/조각, 체크리스트 고아."""
        from .data_contracts import check_data_contracts
        findings = check_data_contracts(self.db_path)
        if findings:
            logger.error("[QualityGuard] 데이터 계약 위반: %s", ", ".join(f"{f['check']}={f['count']}" for f in findings))
        return findings

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
        db_path = self.db_path
        if not db_path.exists():
            return []

        mismatches: list[dict] = []
        with self._connect_ro(db_path) as conn:
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

        # data_contracts.md §2 — indications_by_agency.approval_date 를 직접 UPDATE 하는 자동 경로는 manual_correction 행을
        # 건너뛰어야 한다 (2026-10-17: apply_mfds_official_dates 가 가드 없이 EV 병용 정정일을 덮을 수 있었다).
        for root in (agents_dir, BASE_DIR / "scripts"):
            for py in root.rglob("*.py"):
                if "__pycache__" in str(py) or py.parent.name == "quality_guard":
                    continue
                text = py.read_text(encoding="utf-8", errors="ignore")
                if self._MFDS_UPDATE_PATTERN.search(text) and "manual_correction" not in text:
                    drifts.append({
                        "rule": "data_contracts.md §2 — approval_date 자동 UPDATE 는 raw_source='manual_correction' 행 제외",
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
            db_path = self.db_path
            if db_path.exists():
                from agents.hta_scrapers.kr_mfds_indication_mapper import DISEASE_KR
                with self._connect_ro(db_path) as conn:
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

        # 5) drug_enrichment 커버리지 — 활성 약제 대비
        try:
            db_path = self.db_path
            if db_path.exists():
                with self._connect_ro(db_path) as conn:
                    def _table_exists(name: str) -> bool:
                        r = conn.execute(
                            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                            (name,),
                        ).fetchone()
                        return r is not None
                    enriched = conn.execute(
                        "SELECT COUNT(*) FROM drug_enrichment"
                    ).fetchone()[0] if _table_exists("drug_enrichment") else 0
                    # drug_latest 전체 row (활성 약제의 대략적 upper bound)
                    active = conn.execute(
                        "SELECT COUNT(*) FROM drug_latest"
                    ).fetchone()[0] if _table_exists("drug_latest") else 0
                if active > 0 and enriched < 30:
                    ratio = enriched / active * 100
                    suggestions.append(
                        f"💊 drug_enrichment 커버리지 {enriched}/{active} ({ratio:.3f}%) — "
                        f"비교약제 추가 시 대부분 일일투약비/허가일 공백. "
                        f"동일 generic 상속은 보완책일 뿐. 주요 ATC TOP-100 drug enrich 배치 권장"
                    )
        except Exception as e:
            logger.debug("drug_enrichment 커버리지 점검 생략: %s", e)

        # 6a) DE DocCheck 자격증명 점검 — 로그인 실패 시 Rote Liste 가격 벽 통과 불가
        #     (실제 로그인 시도 = 네트워크. 오프라인 리뷰/테스트는 QG_SKIP_NETWORK_CHECKS=1 로 건너뛴다)
        try:
            verify_script = BASE_DIR / "scripts" / "verify_docchecker_login.py"
            if os.environ.get("QG_SKIP_NETWORK_CHECKS"):
                logger.info("[QualityGuard] DocCheck 로그인 점검 생략 (QG_SKIP_NETWORK_CHECKS)")
            elif verify_script.exists():
                import subprocess
                proc = subprocess.run(
                    ["python3", str(verify_script)],
                    capture_output=True, text=True, timeout=60,
                )
                if proc.returncode == 1:
                    suggestions.append(
                        "🔐 DocCheck 자격증명 미설정 (ROTE_LISTE_DE_USERNAME/PASSWORD) — "
                        "DE Rote Liste 가격 수집 불가"
                    )
                elif proc.returncode == 2:
                    suggestions.append(
                        "🔐 DocCheck 로그인 실패 — 자격증명 만료/변경 가능성. "
                        "config/.env 갱신 필요"
                    )
        except Exception as e:
            logger.debug("DocCheck 점검 생략: %s", e)

        # 6c) MFDS 공공데이터 API 통합 헬스 점검 — permit/patent cache 비어있으면 회귀 신호
        try:
            db_path = self.db_path
            if db_path.exists():
                with self._connect_ro(db_path) as conn:
                    permit_n = patent_n = None
                    for tbl, key in [("mfds_permit_cache", "permit"), ("mfds_patent_cache", "patent")]:
                        r = conn.execute(
                            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (tbl,)
                        ).fetchone()
                        if r is None:
                            suggestions.append(
                                f"🚨 `{tbl}` 테이블 부재 — MFDS API 모듈이 로드되지 않았거나 "
                                f"agents/db/schema.py 적용 안됨. `python -c \"from agents.db.base import DrugPriceDB; DrugPriceDB()\"` 실행"
                            )
                            continue
                        n = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
                        if key == "permit": permit_n = n
                        else: patent_n = n
                    if permit_n is not None and patent_n is not None:
                        if permit_n == 0 and patent_n == 0:
                            suggestions.append(
                                "💤 MFDS API 캐시 모두 비어있음 — 첫 enrichment 미실행. "
                                "대쉬보드에서 약 1건 검색하면 자동 캐시 누적 시작 (mfds_api_integration_rules.md)"
                            )
                    # API 키 점검
                    env = BASE_DIR / "config" / ".env"
                    if env.exists():
                        env_text = env.read_text(encoding="utf-8")
                        if "MFDS_PATENT_API_KEY=" not in env_text:
                            suggestions.append(
                                "🔑 `MFDS_PATENT_API_KEY` 가 config/.env 에 없음 — "
                                "data.go.kr 공공데이터 API 인증 실패 → enrichment fallback 으로만 동작"
                            )
        except Exception as e:
            logger.debug("MFDS API 헬스 점검 생략: %s", e)

        # 6) price-change-reason evidence 품질 — 최근 캐시에서 n_refs=0 비율
        try:
            cache_dir = BASE_DIR / "data" / "dashboard" / "reason_cache"
            if cache_dir.exists():
                import json as _json
                files = sorted(cache_dir.glob("MI_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:30]
                zero_refs = 0
                low_confidence = 0
                for f in files:
                    try:
                        d = _json.loads(f.read_text(encoding="utf-8"))
                    except Exception:
                        continue
                    if len(d.get("references") or []) == 0:
                        zero_refs += 1
                    if (d.get("confidence") or "").lower() == "low":
                        low_confidence += 1
                total = len(files)
                if total >= 5 and (zero_refs / total) > 0.5:
                    suggestions.append(
                        f"📰 price-change-reason 최근 {total}건 중 n_refs=0 {zero_refs}건 "
                        f"({zero_refs/total:.0%}) — 근거 수집 실패 과반. "
                        f"perplexity `citations` → references 변환 + enforce_rules "
                        f"URL-date 추출 경로 점검 (agents/market_intelligence/rules_engine.py)"
                    )
                elif total >= 5 and (low_confidence / total) > 0.7:
                    suggestions.append(
                        f"📰 price-change-reason 최근 {total}건 중 confidence=low {low_confidence}건 "
                        f"({low_confidence/total:.0%}) — 근거 부족 또는 ReviewAgent 연속 거부. "
                        f"refresh=1 로 재분석 또는 perplexity 프롬프트 보강 검토"
                    )
        except Exception as e:
            logger.debug("reason_cache 품질 점검 생략: %s", e)

        return suggestions

    # ── 종합 리뷰 ──────────────────────────────────────────────────────────

    def review_codebase(self) -> dict:
        """상시 감시 · 회귀 탐지 · 개선 제안을 모두 수행하고 리뷰 보고서 생성."""
        today = date.today().isoformat()
        code_issues = self.scan_codebase()
        rule_drifts = self.scan_rule_drift()
        mfds_regressions = self.check_mfds_baseline()
        # 해외약가 저장 행 데이터 검사 — 결과는 record_quality_review 가 읽는 rule_drifts 형태({rule, file, severity})
        # 로 합친다 (ERROR → ops_incident priority 2). 코드 grep 이 못 잡는 간접 fallback 을 값으로 잡는다.
        data_issues = self.check_foreign_price_data()
        # 데이터 계약(2026-10-17 일회성 정정이 잡·재빌드로 되돌아오지 않는지) — 같은 data 형태, check 당 1건
        contract_issues = self.check_data_contracts()
        data_issues = list(data_issues) + contract_issues
        rule_drifts = list(rule_drifts) + data_issues
        suggestions = self.generate_suggestions()
        try:
            suggestions = list(suggestions) + summarize_unverified_pack_rows(self.db_path)
        except Exception as e:
            logger.debug("pack_count 미확인 요약 생략: %s", e)

        lines: list[str] = [
            f"# QualityGuard 코드베이스 리뷰 — {today}",
            "",
            "## 요약",
            f"- 코드 패턴 위반: **{len(code_issues)}건**",
            f"- 규칙↔코드 drift: **{len(rule_drifts) - len(data_issues)}건**",
            f"- 해외약가 데이터 검사: **{len(data_issues) - len(contract_issues)}건**",
            f"- 데이터 계약 위반: **{len(contract_issues)}건**",
            f"- MFDS baseline 회귀: **{len(mfds_regressions)}건**",
            f"- 개선 제안: **{len(suggestions)}건**",
            "",
        ]

        foreign_issues = [d for d in data_issues if not d.get("contract")]
        if foreign_issues:
            lines.append("## ❌ 해외약가 데이터 검사 (저장 행이 규칙과 어긋남)")
            for d in foreign_issues:
                lines.append(f"- **{d['check']}** `{d['file']}` — {d.get('detail', '')}")
            lines.append("")
            lines.append("→ 해당 행 재수집(스크레이퍼가 volume/포장수량 포함) 또는 값 정정. "
                         "`agents/rules/foreign_agent_rules.md` §최소단위 · §pack_count")
            lines.append("")

        if contract_issues:
            lines.append("## ❌ 데이터 계약 위반 (정정값 회귀·재발 — agents/rules/data_contracts.md)")
            for d in contract_issues:
                lines.append(f"- **{d['check']}** ({d['severity']}) — {d.get('detail', '')}")
            lines.append("")

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

        code_drifts = [d for d in rule_drifts if d.get("kind") != "data"]
        if code_drifts:
            lines.append("## ⚠️ 규칙 ↔ 코드 Drift")
            for d in code_drifts:
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
            "rule_drifts": rule_drifts,          # 코드 drift + 데이터 검사(kind="data") — 원장 계약
            "data_issues": data_issues,          # 같은 항목, 이름 있는 키 (보고서·테스트용)
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
