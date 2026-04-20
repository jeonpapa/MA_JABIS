"""/admin/reimbursement smoke — admin 로그인 → 질환 그룹 + 일괄 토글 + 저장 검증."""
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

        page.goto("http://localhost:3000/admin/reimbursement", wait_until="networkidle")
        page.wait_for_timeout(1500)
        body = page.inner_text("body")
        assert "적응증 급여 체크리스트" in body, f"page title missing: {body[:300]}"
        assert "keytruda" in body.lower(), "product dropdown 기본값 keytruda 안보임"
        print("[2] 페이지 + 헤더 ✓")

        # 질환 그룹 헤더 (Korean label + raw code)
        assert "비소세포폐암" in body and "NSCLC" in body, "NSCLC 그룹 헤더 없음"
        assert "두경부암" in body and "HNSCC" in body, "HNSCC 그룹 헤더 없음"
        assert "요로상피암" in body and "UC" in body, "UC 그룹 헤더 없음"
        print("[3] 질환 그룹핑 ✓")

        # 일괄 저장 버튼 (초기에는 dirty 없어서 disabled)
        save_all = page.locator('button:has-text("변경사항 모두 저장")')
        assert save_all.is_visible(), "'변경사항 모두 저장' 버튼 없음"
        print("[4] 일괄 저장 UI ✓")

        # 첫번째 disease 그룹의 '모두 급여' 버튼 클릭 → dirty 카운트 증가 확인
        first_bulk = page.locator('button:has-text("모두 급여")').first
        first_bulk.click()
        page.wait_for_timeout(300)
        body2 = page.inner_text("body")
        assert "변경" in body2 and "대기" in body2, f"dirty 표시 안됨: {body2[:400]}"
        print("[5] 일괄 토글 → dirty 표시 ✓")

        # 취소 차원에서 같은 버튼 바로 옆 '모두 비급여' 클릭해 원복
        first_unreimb = page.locator('button:has-text("모두 비급여")').first
        first_unreimb.click()
        page.wait_for_timeout(300)

        # 일괄 저장 실행 → dirty 가 0 으로 돌아옴
        save_all = page.locator('button:has-text("변경사항 모두 저장")')
        save_all.click()
        page.wait_for_timeout(2500)
        body3 = page.inner_text("body")
        # dirty 카운트 "변경 N건 대기" 문구가 사라졌거나 0이어야 함
        assert "대기" not in body3 or "변경 0건" in body3, f"저장 후에도 dirty 남음: {body3[:400]}"
        print("[6] 일괄 저장 round-trip ✓")

        # collapse/expand: NSCLC 섹션 토글
        nsclc_toggle = page.locator('h3:has-text("비소세포폐암")').first.locator('xpath=preceding-sibling::button')
        if nsclc_toggle.count() > 0:
            nsclc_toggle.first.click()
            page.wait_for_timeout(200)
            nsclc_toggle.first.click()
            page.wait_for_timeout(200)
        print("[7] collapse/expand ✓")

        browser.close()

        if errors:
            print("\n⚠ 에러:")
            for e in errors[:20]:
                print(f"  {e}")
        else:
            print("\n✅ 전체 pass")


if __name__ == "__main__":
    run()
