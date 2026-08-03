from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import api.server as s


def test_default_mail_sources_defined():
    # canonical 고정 소스셋이 정의돼 있고 대표 매체를 포함
    assert isinstance(s.DEFAULT_MAIL_SOURCES, list) and s.DEFAULT_MAIL_SOURCES
    for mid in ("medi", "yakup", "hankyung"):
        assert mid in s.DEFAULT_MAIL_SOURCES


def test_create_without_media_injects_default(monkeypatch):
    # _coerce 결과에 media 가 없어도 create 경로가 DEFAULT 로 채우는지 (fields 빌더 단위 검증)
    body = {"name": "svc", "keywords": ["약가"], "schedule": "Daily",
            "time": "06:00", "emails": ["a@b.com"]}
    result = s._coerce_mail_sub_input(body)
    fields = result[0] if isinstance(result, tuple) else result
    fields = s._with_default_sources(fields)   # 신규 헬퍼
    import json
    assert json.loads(fields["media_json"]) == s.DEFAULT_MAIL_SOURCES
    assert json.loads(fields["custom_sources_json"]) == []


def test_create_with_media_kept(monkeypatch):
    fields = s._coerce_mail_sub_input({"media": ["medi"]})
    fields = s._with_default_sources(fields)
    import json
    assert json.loads(fields["media_json"]) == ["medi"]
