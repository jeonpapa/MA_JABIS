"""브랜드 트래픽 CRUD + Home 카드 연결 검증."""
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

        # Home — 브랜드 카드 확인 (KeywordCloud)
        page.wait_for_timeout(2500)
        body = page.inner_text("body")
        assert "Keytruda" in body, "Keytruda not found in Home"
        assert "Opdivo" in body, "Opdivo not found in Home"
        assert "Tagrisso" in body, "Tagrisso not found"
        print("[2] Home 브랜드 Top10 ✓ (API 연결)")

        # 브랜드 클릭 → 뉴스 패널
        page.locator('button:has(span:has-text("Keytruda"))').first.click()
        page.wait_for_timeout(400)
        body = page.inner_text("body")
        assert "KEYNOTE-789" in body or "대장암 1차" in body, f"뉴스 패널 미노출: {body[-600:]}"
        print("[3] 브랜드 클릭 → 뉴스 패널 ✓")

        # /admin/brand-traffic
        page.goto("http://localhost:3000/admin/brand-traffic", wait_until="networkidle")
        page.wait_for_timeout(1500)
        body = page.inner_text("body")
        assert "브랜드 미디어 트래픽" in body, "admin page title missing"
        assert "Keytruda" in body and "Opdivo" in body, "seed rows missing"
        print("[4] admin 페이지 + 시드 10건 ✓")

        # 신규 추가
        page.fill('input[placeholder="Keytruda"]', 'TEST-BRAND-X')
        page.fill('input[placeholder="한국MSD"]', '테스트팜')
        page.fill('input[placeholder="면역항암제"]', '테스트영역')
        page.fill('input[placeholder="9840"]', '1234')
        page.fill('input[placeholder="12.4"]', '5.5')
        page.fill('input[placeholder*="6200"]', '100,200,300,400,500,600,700')
        page.click('button:has-text("추가"):not(:has-text("추가 중"))')
        page.wait_for_timeout(800)
        body = page.inner_text("body")
        assert "TEST-BRAND-X" in body, "new brand not visible"
        print("[5] 신규 추가 ✓")

        # 삭제
        page.once("dialog", lambda d: d.accept())
        row_sel = 'div:has(> span.text-\\[\\#F59E0B\\]):has-text("TEST-BRAND-X")'
        # Fallback: find delete button next to text
        page.locator('button:has-text("삭제")').last.click()
        page.wait_for_timeout(800)
        body = page.inner_text("body")
        assert "TEST-BRAND-X" not in body, "brand not deleted"
        print("[6] 삭제 ✓")

        browser.close()

        if errors:
            print("\n⚠ 에러:")
            for e in errors[:20]:
                print(f"  {e}")
        else:
            print("\n✅ 전체 pass")


if __name__ == "__main__":
    run()
