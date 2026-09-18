"""개인 Gmail 발송 토큰 생성 (사용자가 로컬에서 1회 실행).

준비 (Google Cloud Console):
  1) OAuth 동의 화면 — 게시 상태를 '프로덕션'으로 (테스트 상태는 토큰이 7일 만에 만료)
  2) 사용자 인증 정보 → OAuth 클라이언트 ID(데스크톱 앱) → client_secret JSON 다운로드
실행:
  .venv/bin/python scripts/gmail_oauth_setup.py --client-secret ~/Downloads/client_secret.json
결과: data/secrets/google_token.json (0600) + fly secret 등록 명령 출력.
"""
from __future__ import annotations

import argparse
import base64
import os
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description="Gmail gmail.send OAuth 토큰 생성")
    parser.add_argument("--client-secret", required=True)
    parser.add_argument("--out", default=str(REPO_ROOT / "data" / "secrets" / "google_token.json"))
    args = parser.parse_args()

    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = InstalledAppFlow.from_client_secrets_file(args.client_secret, SCOPES).run_local_server(port=0)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(creds.to_json(), encoding="utf-8")
    os.chmod(out, 0o600)
    encoded = base64.b64encode(out.read_bytes()).decode("ascii")
    print(f"토큰 저장: {out}")
    print("프로덕션 등록(사용자 직접 실행):")
    print(f"  flyctl secrets set GOOGLE_TOKEN_JSON={encoded} -a ma-ai-dossier")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
