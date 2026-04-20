"""/international-pricing smoke — 로그인 → 이력 → 선택 → 3탭 렌더."""
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

        # /international-pricing 이동
        page.goto("http://localhost:3000/international-pricing", wait_until="networkidle")
        assert "/international-pricing" in page.url
        print(f"[1] /international-pricing 이동 ✓")

        # 이력 로드 대기
        page.wait_for_timeout(1500)
        body = page.inner_text("body")
        assert "기존 검색 이력" in body, f"history section missing"
        # 최소 하나 이상의 이력 카드
        assert "keytruda" in body.lower() or "pembrolizumab" in body.lower(), \
            f"expected history items not visible: {body[:500]}"
        print(f"[2] 이력 카드 렌더 ✓")

        # keytruda / pembrolizumab 카드 클릭
        cards = page.query_selector_all('div.grid.grid-cols-4 button')
        assert len(cards) > 0, "no history cards"
        # keytruda 포함 카드 찾기
        for c in cards:
            txt = c.inner_text().lower()
            if "keytruda" in txt or "pembrolizumab" in txt:
                c.click()
                break
        print(f"[3] 이력 카드 클릭 ✓")

        # detail 로드 대기
        page.wait_for_timeout(3500)
        body = page.inner_text("body")
        assert "A8 국가 급여 약가" in body, f"pricing tab not rendered: {body[-500:]}"
        print(f"[4] A8 Pricing 탭 렌더 ✓")

        # HTA 탭 클릭
        page.click('button:has-text("HTA 현황")')
        page.wait_for_timeout(500)
        body = page.inner_text("body")
        assert "HTA 중심 국가" in body, "hta header missing"
        assert "NICE" in body and "CADTH" in body, "hta bodies missing"
        print(f"[5] HTA 탭 렌더 ✓")

        # 허가 탭 클릭
        page.click('button:has-text("허가 현황")')
        page.wait_for_timeout(500)
        body = page.inner_text("body")
        assert "제외국 허가" in body, "approval header missing"
        # FDA 있으면 USA 허가 표시
        assert "미국" in body and "일본" in body, "approval countries missing"
        print(f"[6] 허가 탭 렌더 ✓")

        browser.close()

        if errors:
            print("\n⚠ 브라우저 에러:")
            for e in errors[:20]:
                print(f"  {e}")
        else:
            print("\n✅ 전체 pass")

if __name__ == "__main__":
    run()
