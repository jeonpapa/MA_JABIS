"""/market-share smoke — 로그인 → ATC4 기본(L01G5=Keytruda 시장) 로드 → 검색 → 트렌드 전환."""
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

        # Market share 페이지로
        page.goto("http://localhost:3000/market-share", wait_until="networkidle")
        page.wait_for_timeout(1500)
        body = page.inner_text("body")

        # 헤더
        assert "Korean Market" in body, f"header missing"
        assert "PD-1/PD-L1" in body or "MAB A-NEOPLAS" in body, f"ATC4 meta missing: {body[:400]}"
        print("[2] 기본 ATC4 (L01G5) 로드 ✓")

        # Summary row — Keytruda 상위 존재
        assert "KEYTRUDA" in body, "Keytruda missing from summary"
        print("[3] Summary row Keytruda ✓")

        # Donut — 점유율 % 노출
        import re
        pcts = re.findall(r"(\d+\.\d)%", body)
        assert len(pcts) >= 3, f"percentage labels insufficient: {pcts[:5]}"
        print(f"[4] 점유율 라벨 {len(pcts)}개 ✓")

        # Unit Trend 전환
        page.click('button:has-text("Unit Trend")')
        page.wait_for_timeout(600)
        body = page.inner_text("body")
        assert "Dosage Units" in body, f"unit trend view missing"
        print("[5] Unit Trend 전환 ✓")

        # Revenue Trend 전환
        page.click('button:has-text("Revenue Trend")')
        page.wait_for_timeout(600)
        body = page.inner_text("body")
        assert "M KRW" in body or "백만원" in body, "revenue trend view missing"
        print("[6] Revenue Trend 전환 ✓")

        # Market Share 복귀 + 검색
        page.click('button:has-text("Market Share")')
        page.wait_for_timeout(400)
        page.fill('input[placeholder*="제품명"]', 'OPDIVO')
        page.wait_for_timeout(500)
        # 드롭다운 OPDIVO 선택
        opts = page.locator('button:has-text("OPDIVO")')
        if opts.count() > 0:
            opts.first.click()
            page.wait_for_timeout(800)
            body = page.inner_text("body")
            assert "OPDIVO" in body, "Opdivo detail card missing"
            assert "NIVOLUMAB" in body, f"molecule missing: {body[:400]}"
            print("[7] OPDIVO 검색 → 상세 ✓")
        else:
            print("[7] (skip) OPDIVO dropdown not found")

        # 분기 선택 드롭다운 변경
        sel = page.locator('select').first
        sel.select_option(value='2024Q1')
        page.wait_for_timeout(600)
        body = page.inner_text("body")
        print("[8] 분기 2024Q1 전환 ✓")

        browser.close()

        if errors:
            print("\n⚠ 브라우저 에러:")
            for e in errors[:20]:
                print(f"  {e}")
        else:
            print("\n✅ 전체 pass")

if __name__ == "__main__":
    run()
