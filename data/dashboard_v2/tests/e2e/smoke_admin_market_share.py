"""/admin/market-share smoke — admin 로그인 → 업로드 이력 + 파일 업로드 검증."""
from playwright.sync_api import sync_playwright

def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: errors.append(f"console:{m.type}:{m.text}") if m.type == "error" else None)

        page.goto("http://localhost:3000/login", wait_until="networkidle")
        page.fill('input[type="email"]', "admin@marketintel.kr")
        page.fill('input[type="password"]', "admin1234")
        page.click('button[type="submit"]')
        page.wait_for_url("http://localhost:3000/", timeout=5000)
        print("[1] 로그인 ✓")

        page.goto("http://localhost:3000/admin/market-share", wait_until="networkidle")
        page.wait_for_timeout(1500)
        body = page.inner_text("body")
        assert "데이터 관리" in body, f"page title missing: {body[:300]}"
        assert "35,381" in body or "35381" in body, f"product count missing: {body[:400]}"
        assert "20" in body, "quarters count missing"
        print("[2] 페이지 + totals 카드 ✓")

        # 업로드 이력
        assert "업로드 이력" in body, "history section missing"
        assert "NSA_E_Master_2025Q4" in body, f"prior upload not listed: {body[:600]}"
        print("[3] 업로드 이력 표시 ✓")

        # 실제 업로드
        page.set_input_files('input[type="file"]', '/Users/kimjeong-ae/MA_AI_Dossier/_resource/NSA_E_Master_2025Q4.xlsx')
        page.click('button:has-text("업로드 + 적재")')
        # 업로드는 파싱 시간 필요
        page.wait_for_selector('text=적재 완료', timeout=180000)
        body = page.inner_text("body")
        assert "55,154" in body or "55154" in body, f"row count missing in result: {body[:600]}"
        print("[4] 파일 업로드 → 적재 완료 ✓")

        # non-admin 차단 검증 (별도 user 계정 필요. skip if not)
        browser.close()

        if errors:
            print("\n⚠ 에러:")
            for e in errors[:20]: print(f"  {e}")
        else:
            print("\n✅ 전체 pass")

if __name__ == "__main__":
    run()
