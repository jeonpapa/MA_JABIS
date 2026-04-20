"""/ (home) smoke — 로그인 → MSD 카드 + Keytruda 적응증 목록 렌더."""
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
        print(f"[1] 로그인 ✓")

        page.wait_for_timeout(2000)
        body = page.inner_text("body")

        # MSD 급여 카드 — 199 개 품목 (대략)
        assert "급여 등재 품목" in body, f"reimbursed card missing: {body[:300]}"
        import re
        m = re.search(r"(\d+)\s*개 품목", body)
        assert m, f"product count missing: {body[:300]}"
        count = int(m.group(1))
        assert count > 0, f"product count zero: {count}"
        print(f"[2] MSD 급여 품목 카드: {count}개 ✓")

        # Keytruda 카드
        assert "Keytruda 적응증 현황" in body, "keytruda card missing"
        assert "MFDS 허가 적응증" in body, "mfds label missing"
        print(f"[3] Keytruda 카드 ✓")

        # 적응증 목록 펼치기
        page.click('button:has-text("적응증 목록 보기")')
        page.wait_for_timeout(400)
        body = page.inner_text("body")
        # 최소 하나의 disease_kr 포함
        assert "비소세포폐암" in body or "흑색종" in body or "두경부암" in body, \
            f"no keytruda indications shown: {body[-500:]}"
        print(f"[4] Keytruda 적응증 목록 전개 ✓")

        browser.close()

        if errors:
            print("\n⚠ 브라우저 에러:")
            for e in errors[:10]:
                print(f"  {e}")
        else:
            print("\n✅ 전체 pass")

if __name__ == "__main__":
    run()
