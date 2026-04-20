"""Dashboard v2 auth flow smoke test — login → AuthGuard → admin popup → listUsers."""
from playwright.sync_api import sync_playwright

def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda msg: errors.append(f"console:{msg.type}:{msg.text}") if msg.type == "error" else None)

        # 1) /login 으로 리다이렉트
        page.goto("http://localhost:3000/", wait_until="networkidle")
        assert "/login" in page.url, f"expected /login, got {page.url}"
        print(f"[1] unauthed redirect → {page.url} ✓")

        # 2) 로그인
        page.fill('input[type="email"]', "admin@marketintel.kr")
        page.fill('input[type="password"]', "admin1234")
        page.click('button[type="submit"]')
        page.wait_for_url("http://localhost:3000/", timeout=5000)
        print(f"[2] login succeeded → {page.url} ✓")

        # 3) localStorage 에 JWT 저장됐는지
        token = page.evaluate("() => localStorage.getItem('app_jwt')")
        assert token and token.startswith("eyJ"), f"JWT missing: {token}"
        print(f"[3] JWT stored (len={len(token)}) ✓")

        # 4) /api/auth/me 가 AuthGuard 에서 통과됐는지 — 리로드해서 재검증
        page.reload(wait_until="networkidle")
        assert page.url == "http://localhost:3000/", f"reload kicked to {page.url}"
        print(f"[4] AuthGuard /api/auth/me 통과 ✓")

        # 5) Sidebar admin 설정 버튼 클릭 → users 탭 → /api/admin/users 조회
        page.click('button[title="관리자 설정"]')
        page.click('button:has-text("접속 관리")')
        # 로딩 끝날 때까지 대기
        page.wait_for_timeout(1000)
        # "접속 가능 계정 (N명)" 이 렌더링돼야 함
        count_text = page.inner_text('.relative p.text-\\[\\#4A5568\\].text-xs.font-semibold.mb-2, p.text-\\[\\#4A5568\\]:has-text("접속 가능")').strip()
        assert "접속 가능 계정" in count_text, f"users tab not rendered: {count_text!r}"
        print(f"[5] admin users 탭 렌더 → {count_text[:40]!r} ✓")

        # 6) 유저 추가
        page.fill('input[placeholder="이메일 주소"]', "smoke-test@example.com")
        page.fill('input[placeholder="비밀번호 (4자 이상)"]', "smoke1234")
        page.click('button:has-text("계정 추가")')
        page.wait_for_timeout(1500)
        body = page.inner_text("body")
        assert "smoke-test@example.com" in body, "added user not visible"
        print(f"[6] 유저 추가 + 목록 반영 ✓")

        # 7) 유저 삭제 — smoke-test 옆의 휴지통 버튼
        page.click('li:has-text("smoke-test@example.com") button[title="삭제"]')
        page.wait_for_timeout(1500)
        body = page.inner_text("body")
        assert "smoke-test@example.com" not in body, "deleted user still visible"
        print(f"[7] 유저 삭제 반영 ✓")

        # 8) 로그아웃
        page.keyboard.press("Escape")
        page.click('button[title="로그아웃"]')
        page.wait_for_url("**/login", timeout=3000)
        token_after = page.evaluate("() => localStorage.getItem('app_jwt')")
        assert not token_after, f"JWT still present after logout: {token_after}"
        print(f"[8] 로그아웃 → JWT 클리어 ✓")

        browser.close()

        if errors:
            print("\n⚠ 브라우저 에러:")
            for e in errors:
                print(f"  {e}")
        else:
            print("\n✅ 전체 8단계 pass")

if __name__ == "__main__":
    run()
