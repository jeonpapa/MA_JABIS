"""잡 래퍼·신호가 실제 원장(data/db/drug_prices.db)에 쓰지 않게 매 테스트마다 임시 DB 를 지정한다.

agents/agent_tasks/jobs.resolve_db_path() 와 scheduler 의 _agent_* 경계 함수가 이 환경변수를 읽는다.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_agent_task_db(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_TASK_DB", str(tmp_path / "agent_tasks.db"))
