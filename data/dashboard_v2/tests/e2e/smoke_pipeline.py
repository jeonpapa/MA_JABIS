"""MSD 파이프라인 CRUD + Home 카드 연결 검증."""
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

        # Home — 파이프라인 카드 (2027=2개, 2028=4개)
        page.wait_for_timeout(2000)
        body = page.inner_text("body")
        assert "New Pipeline" in body, "pipeline card missing"
        # 파이프라인 리스트 펼치기
        page.click('button:has-text("파이프라인 목록 보기")')
        page.wait_for_timeout(500)
        body = page.inner_text("body")
        print(f"[2] Home 파이프라인 카드 ✓")

        # +1년 카드 클릭 → 2027 (enlicitide, Dor/Ist)
        page.locator('button:has(p:has-text("+1"))').first.click()
        page.wait_for_timeout(400)
        body = page.inner_text("body")
        assert "enlicitide" in body or "Dor/Ist" in body, f"2027 seed missing: {body[-800:]}"
        print("[3] 2027 (+1) 2개 ✓")

        # +2년 카드 클릭 → 2028 (Sac-TMT, Tulisokibart, MK-8581B, MK-8591C)
        page.locator('button:has(p:has-text("+2"))').first.click()
        page.wait_for_timeout(400)
        body = page.inner_text("body")
        found = sum(1 for k in ("Sac-TMT","Tulisokibart","MK-8581B","MK-8591C") if k in body)
        assert found >= 3, f"2028 seed missing: found={found}, tail={body[-800:]}"
        print(f"[4] 2028 (+2) {found}/4 ✓")

        # /admin/msd-pipeline 페이지
        page.goto("http://localhost:3000/admin/msd-pipeline", wait_until="networkidle")
        page.wait_for_timeout(1500)
        body = page.inner_text("body")
        assert "파이프라인 — 관리" in body, "admin page title missing"
        assert "enlicitide" in body and "MK-8591C" in body, "seed rows missing"
        print("[5] admin 페이지 + 시드 6건 ✓")

        # 신규 추가
        page.fill('input[placeholder*="MK-1234"]', 'TEST-PIPELINE-X')
        page.fill('input[placeholder*="Phase 2"]', 'Phase 3')
        page.fill('input[placeholder="비소세포폐암"]', '테스트적응증')
        page.fill('input[placeholder="2027"]', '2029')
        page.click('button:has-text("추가")')
        page.wait_for_timeout(800)
        body = page.inner_text("body")
        assert "TEST-PIPELINE-X" in body, "new row not visible"
        print("[6] 신규 추가 ✓")

        # 삭제 (dialog accept)
        page.once("dialog", lambda d: d.accept())
        # find the row with TEST-PIPELINE-X and click its 삭제 button
        row_sel = 'tr:has-text("TEST-PIPELINE-X")'
        page.locator(row_sel).locator('button:has-text("삭제")').click()
        page.wait_for_timeout(800)
        body = page.inner_text("body")
        assert "TEST-PIPELINE-X" not in body, "row not deleted"
        print("[7] 삭제 ✓")

        # 편집: enlicitide phase 추가
        page.once("dialog", lambda d: d.accept())
        row_sel = 'tr:has-text("enlicitide")'
        page.locator(row_sel).locator('button:has-text("편집")').click()
        page.wait_for_timeout(300)
        # 편집 행의 phase 인풋 (두 번째 input)
        inputs = page.locator('tr.bg-\\[\\#00E5CC\\]\\/5 input').all()
        if inputs:
            inputs[1].fill('Phase 3')  # phase 칸
        page.locator('button:has-text("저장")').click()
        page.wait_for_timeout(800)
        body = page.inner_text("body")
        # 저장 후 enlicitide 행에 Phase 3 존재
        assert "Phase 3" in body, "edit save missing Phase 3"
        print("[8] 편집/저장 ✓")

        browser.close()

        if errors:
            print("\n⚠ 에러:")
            for e in errors[:20]: print(f"  {e}")
        else:
            print("\n✅ 전체 pass")

if __name__ == "__main__":
    run()
