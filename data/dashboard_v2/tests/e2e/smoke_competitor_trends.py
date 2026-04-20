"""Competitor Trends + Keyword Cloud smoke — API 연결 + admin CRUD."""
from playwright.sync_api import sync_playwright


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: errors.append(f"console:{m.type}:{m.text}") if m.type == "error" else None)

        # 로그인
        page.goto("http://localhost:3000/login", wait_until="networkidle")
        page.fill('input[type="email"]', "admin@marketintel.kr")
        page.fill('input[type="password"]', "admin1234")
        page.click('button[type="submit"]')
        page.wait_for_url("http://localhost:3000/", timeout=5000)
        print("[1] 로그인 ✓")

        # Sidebar 에 Competitor Trends 링크
        page.wait_for_timeout(500)
        side = page.inner_text('nav')
        assert "Competitor Trends" in side, f"sidebar link missing: {side[:200]}"
        print("[2] Sidebar Competitor Trends 링크 ✓")

        # /competitor-trends 페이지
        page.goto("http://localhost:3000/competitor-trends", wait_until="networkidle")
        page.wait_for_timeout(1500)
        body = page.inner_text("body")
        assert "Competitor Trends" in body, "title missing"
        assert "AstraZeneca" in body or "MSD" in body or "Roche" in body, f"no seeded trend visible: {body[:400]}"
        print("[3] API 연결된 동향 목록 렌더 ✓")

        # admin CRUD 페이지 이동
        page.goto("http://localhost:3000/admin/competitor-trends", wait_until="networkidle")
        page.wait_for_timeout(1500)
        body = page.inner_text("body")
        assert "Competitor Trends — 관리" in body, f"admin title missing: {body[:400]}"
        assert "AstraZeneca" in body, "seeded item not in admin list"
        print("[4] admin 페이지 + 시드 6건 ✓")

        # 신규 추가
        page.fill('input[placeholder="회사명 *"]', 'E2E Test Co')
        page.fill('input[placeholder="헤드라인 *"]', 'E2E 자동 테스트 동향')
        page.fill('textarea[placeholder="상세 *"]', 'E2E Playwright 테스트로 자동 생성된 경쟁사 동향 항목.')
        page.click('button:has-text("+ 추가")')
        page.wait_for_timeout(1500)
        body = page.inner_text("body")
        assert "E2E Test Co" in body, f"new item not visible: {body[-500:]}"
        print("[5] 신규 동향 추가 ✓")

        # 삭제
        page.on("dialog", lambda d: d.accept())
        page.click('div:has-text("E2E Test Co") button.bg-\\[\\#EF4444\\]\\/20')
        page.wait_for_timeout(1500)
        body = page.inner_text("body")
        assert "E2E Test Co" not in body, "delete failed"
        print("[6] 삭제 ✓")

        # Keyword Cloud admin
        page.goto("http://localhost:3000/admin/keyword-cloud", wait_until="networkidle")
        page.wait_for_timeout(1500)
        body = page.inner_text("body")
        assert "Keyword Cloud — 관리" in body, "kw admin title missing"
        assert "약가 재평가" in body, "seeded keyword missing"
        print("[7] Keyword Cloud admin + 시드 20건 ✓")

        # 신규 키워드 추가
        page.fill('input[placeholder="키워드 *"]', 'E2E-키워드')
        page.click('button:has-text("+ 추가")')
        page.wait_for_timeout(1200)
        body = page.inner_text("body")
        assert "E2E-키워드" in body, "new keyword not visible"
        print("[8] 키워드 추가 ✓")

        # 삭제 — Locator filter 로 정확한 row 선택
        kw_row = page.locator('div.bg-\\[\\#161B27\\]').filter(has_text="E2E-키워드").first
        kw_row.locator('button.bg-\\[\\#EF4444\\]\\/20').click()
        page.wait_for_timeout(1500)
        body = page.inner_text("body")
        assert "E2E-키워드" not in body, "keyword delete failed"
        print("[9] 키워드 삭제 ✓")

        # Home 워드클라우드 API 연결 확인
        page.goto("http://localhost:3000/", wait_until="networkidle")
        page.wait_for_timeout(2000)
        body = page.inner_text("body")
        assert "약가 재평가" in body, "home keyword cloud not rendering API data"
        print("[10] Home 워드클라우드 API 연결 ✓")

        browser.close()

        if errors:
            print("\n⚠ 에러:")
            for e in errors[:20]:
                print(f"  {e}")
        else:
            print("\n✅ 전체 pass")


if __name__ == "__main__":
    run()
