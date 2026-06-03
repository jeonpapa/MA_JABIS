"""DocCheck 자격증명 점검 — Rote Liste DE 로그인 테스트.

정기 실행으로 자격증명 만료·변경 감지. QG 일일 리뷰 또는 수동 실행.

사용법:
    python scripts/verify_docchecker_login.py            # 기본: 환경변수 기반
    python scripts/verify_docchecker_login.py --verbose  # 요청/응답 상세

종료 코드:
    0 — 로그인 성공
    1 — 자격증명 누락
    2 — 로그인 실패 (인증 오류)
    3 — 예외 (네트워크/SSL)
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    logger = logging.getLogger("docchecker_verify")

    # config/.env 로드
    env_path = BASE_DIR / "config" / ".env"
    if env_path.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_path, override=False)
        except ImportError:
            logger.warning("python-dotenv 미설치 — 환경변수만 사용")

    user = os.environ.get("ROTE_LISTE_DE_USERNAME", "")
    pw = os.environ.get("ROTE_LISTE_DE_PASSWORD", "")
    if not user or not pw:
        logger.error(
            "ROTE_LISTE_DE_USERNAME/PASSWORD 미설정 — config/.env 확인. "
            "DocCheck 계정 등록: https://www.doccheck.com/"
        )
        return 1

    logger.info("[docchecker] 자격증명 로드 성공 (username=%s***)", user[:4])

    try:
        from agents.scrapers.de_rote_liste import DeRoteListeScraper
        scraper = DeRoteListeScraper()
        ok = scraper._login_requests()
    except Exception as e:
        logger.exception("[docchecker] 로그인 시도 중 예외: %s", e)
        return 3

    if ok:
        logger.info("✓ DocCheck 로그인 성공")
        # 추가 검증: 간단한 Keytruda 페이지 접근 테스트
        try:
            import requests  # type: ignore
            r = scraper._session.get(
                "https://www.rote-liste.de/suche?q=keytruda",
                timeout=15,
            )
            if "login" in r.url or r.status_code == 401:
                logger.warning("[docchecker] 로그인은 되지만 Keytruda 검색 페이지에 가격벽 존재")
                return 2
            logger.info("[docchecker] ✓ Rote Liste 페이지 접근 확인 (status=%d)", r.status_code)
        except Exception as e:
            logger.warning("[docchecker] 검색 테스트 스킵 (%s)", e)
        return 0

    logger.error("✗ DocCheck 로그인 실패 — 자격증명 만료 또는 변경 가능성")
    return 2


if __name__ == "__main__":
    sys.exit(main())
