"""DashboardAgent.run() — 출력 디렉터리 부재 시 자동 생성 (2026-09-01 프로덕션 회귀).

프로덕션 볼륨에 config dashboard.output_dir(data/dashboard_v1_archive) 가 없어
`write_text` 가 FileNotFoundError 로 파이프라인 전체를 깨뜨렸다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.dashboard_agent import DashboardAgent  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def test_run_creates_missing_output_dir(tmp_path):
    cfg = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
    agent = DashboardAgent(cfg, tmp_path)
    assert not agent.output_html_path.parent.exists()

    out = agent.run()

    assert out == agent.output_html_path
    assert out.is_file()
    assert "<html" in out.read_text(encoding="utf-8").lower()
