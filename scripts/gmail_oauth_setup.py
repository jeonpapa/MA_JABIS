"""개인 Gmail 발송 토큰 생성 (사용자가 로컬에서 1회 실행).

준비 (Google Cloud Console):
  1) OAuth 동의 화면 — 게시 상태를 '프로덕션'으로 (테스트 상태는 토큰이 7일 만에 만료)
  2) 사용자 인증 정보 → OAuth 클라이언트 ID(데스크톱 앱) → client_secret JSON 다운로드
실행:
  .venv/bin/python scripts/gmail_oauth_setup.py --client-secret ~/Downloads/client_secret.json
결과: data/secrets/google_token.json (0600) + fly secret 등록 명령 출력.
      (토큰 내용은 터미널에 찍지 않는다 — 명령이 파일을 읽는 형태라 셸 히스토리에도 남지 않음)
"""
from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
REPO_ROOT = Path(__file__).resolve().parent.parent
FLY_APP = "ma-ai-dossier"


def fly_secret_command(token_path: str | Path) -> str:
    """fly secret 등록 명령 — 토큰 파일을 셸이 읽어 인코딩한다. base64 전문을 출력/복사하지 않도록."""
    return f'flyctl secrets set GOOGLE_TOKEN_JSON="$(base64 < {Path(token_path).resolve()})" -a {FLY_APP}'


def _is_git_ignored(path: str | Path) -> bool:
    """`git check-ignore -q <path>` (cwd=REPO_ROOT) 가 0 이면 True. git 부재/오류는 False(=보수적으로 거부)."""
    try:
        result = subprocess.run(["git", "check-ignore", "-q", str(path)], cwd=REPO_ROOT,
                                capture_output=True, check=False)
    except OSError:
        return False
    return result.returncode == 0


def refuse_if_committable(out: str | Path) -> None:
    """토큰 출력 경로가 레포 안인데 .gitignore 에 걸리지 않으면 중단 — 토큰은 절대 커밋 대상이면 안 된다."""
    resolved = Path(out).resolve()
    try:
        resolved.relative_to(REPO_ROOT)
    except ValueError:
        return  # 레포 밖 — git 커밋 위험 없음
    if not _is_git_ignored(resolved):
        raise SystemExit(
            f"중단: {resolved} 는 git 에 커밋될 수 있는 경로입니다 (.gitignore 미적용). "
            "data/secrets/ 를 .gitignore 에 추가하거나 --out 을 레포 밖 경로로 지정하세요."
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Gmail gmail.send OAuth 토큰 생성")
    parser.add_argument("--client-secret", required=True)
    parser.add_argument("--out", default=str(REPO_ROOT / "data" / "secrets" / "google_token.json"))
    args = parser.parse_args()

    out = Path(args.out)
    refuse_if_committable(out)  # OAuth 흐름·파일 쓰기 전에 검사

    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = InstalledAppFlow.from_client_secrets_file(args.client_secret, SCOPES).run_local_server(port=0)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(creds.to_json(), encoding="utf-8")
    os.chmod(out, 0o600)
    print(f"토큰 저장: {out}")
    print("프로덕션 등록(사용자 직접 실행 — 토큰 내용은 출력하지 않습니다):")
    print(f"  {fly_secret_command(out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
