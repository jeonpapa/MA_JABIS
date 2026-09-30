"""Gmail OAuth 토큰은 절대 git 에 커밋되면 안 된다 — .gitignore + 설정 스크립트 가드."""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "gmail_oauth_setup.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("gmail_oauth_setup_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_default_token_path_is_git_ignored():
    r = subprocess.run(["git", "check-ignore", "-q", "data/secrets/google_token.json"], cwd=REPO_ROOT)
    assert r.returncode == 0


def test_is_git_ignored_helper_uses_repo_gitignore():
    setup = _load_script()
    assert setup._is_git_ignored(REPO_ROOT / "data" / "secrets" / "google_token.json") is True
    assert setup._is_git_ignored(REPO_ROOT / "requirements.txt") is False


def test_guard_refuses_committable_path_inside_repo(monkeypatch):
    setup = _load_script()
    monkeypatch.setattr(setup, "_is_git_ignored", lambda p: False)
    with pytest.raises(SystemExit) as exc:
        setup.refuse_if_committable(setup.REPO_ROOT / "data" / "secrets" / "google_token.json")
    assert exc.value.code != 0
    assert "git" in str(exc.value.code)


def test_guard_allows_ignored_path_or_path_outside_repo(monkeypatch, tmp_path):
    setup = _load_script()
    monkeypatch.setattr(setup, "_is_git_ignored", lambda p: True)
    setup.refuse_if_committable(setup.REPO_ROOT / "data" / "secrets" / "google_token.json")  # no raise
    monkeypatch.setattr(setup, "_is_git_ignored", lambda p: False)
    setup.refuse_if_committable(tmp_path / "outside.json")  # 레포 밖 — 가드 대상 아님


def test_fly_secret_command_never_prints_token_contents(tmp_path):
    """터미널·셸 히스토리에 토큰 base64 전문이 남지 않도록 파일을 읽는 명령 형태만 출력한다."""
    setup = _load_script()
    out = tmp_path / "google_token.json"
    out.write_text('{"token": "ya29.SECRET", "scopes": ["https://www.googleapis.com/auth/gmail.send"]}')
    cmd = setup.fly_secret_command(out)
    assert cmd == f'flyctl secrets set GOOGLE_TOKEN_JSON="$(base64 < {out})" -a ma-ai-dossier'
    assert "SECRET" not in cmd and "ya29" not in cmd
    assert "b64encode" not in SCRIPT.read_text(encoding="utf-8")   # 스크립트 어디서도 토큰을 인코딩해 찍지 않는다
