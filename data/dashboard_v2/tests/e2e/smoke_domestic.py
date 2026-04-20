"""/domestic-pricing 페이지 smoke — 로그인 → 검색 → 선택 → 이력 렌더."""
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

        # /domestic-pricing 이동
        page.goto("http://localhost:3000/domestic-pricing", wait_until="networkidle")
        assert "/domestic-pricing" in page.url
        print(f"[1] /domestic-pricing 이동 ✓")

        # 초기 상태: 검색 프롬프트
        body = page.inner_text("body")
        assert "2자 이상" in body, f"empty prompt missing: {body[:200]}"
        print(f"[2] 빈 상태 프롬프트 렌더 ✓")

        # 검색: "자누비아"
        page.fill('input[placeholder*="제품명"]', "자누비아")
        # 디바운스 300ms + API 응답 대기
        page.wait_for_timeout(2500)

        # 결과 테이블 렌더 확인
        body = page.inner_text("body")
        assert "자누비아" in body, f"search result missing: {body[:500]}"
        # "총 N개 품목" 표시
        import re
        m = re.search(r"총\s*(\d+)\s*개 품목", body)
        assert m and int(m.group(1)) > 0, f"product count missing or zero: {body[:300]}"
        count = int(m.group(1))
        print(f"[3] 검색 결과 {count}개 렌더 ✓")

        # 첫 행 클릭 → detail panel
        page.click('tbody tr:first-child')
        page.wait_for_timeout(500)
        body = page.inner_text("body")
        assert "기본 정보" in body and "최초 약가 등재일" in body, "detail panel missing"
        assert "가격 변동 이력 테이블" in body, "history table missing"
        print(f"[4] detail + history 렌더 ✓")

        # 검색 초기화 버튼 존재 확인
        reset_btn = page.locator('input[placeholder*="제품명"] + button, button:has(i.ri-close-line)').first
        assert reset_btn.count() > 0, "reset button not rendered"
        print(f"[5] reset 버튼 존재 ✓")

        browser.close()

        if errors:
            print("\n⚠ 브라우저 에러:")
            for e in errors[:20]:
                print(f"  {e}")
        else:
            print("\n✅ 전체 pass")

if __name__ == "__main__":
    run()
