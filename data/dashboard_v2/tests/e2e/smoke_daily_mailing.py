"""Daily Mailing CRUD + 테스트 발송 UI 검증."""
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

        # /daily-mailing 이동
        page.goto("http://localhost:3000/daily-mailing", wait_until="networkidle")
        page.wait_for_timeout(1500)
        body = page.inner_text("body")
        assert "Daily Mailing Setting" in body, "page title missing"
        assert "SMTP 미설정" in body, "SMTP 경고 배너 누락 (dry-run 표시 필요)"
        print("[2] 페이지 로드 + SMTP 경고 배너 ✓")

        # 설정 이름 입력
        page.fill('input[name="settingName"]', 'E2E 테스트 설정')

        # 키워드 '약가 인하' 확인 (기본 선택) — 추가로 '임상시험' 선택
        page.click('button:has-text("임상시험")')
        page.wait_for_timeout(200)

        # 이메일 추가
        page.fill('input[type="email"]', 'e2e-test@example.com')
        page.click('button:has(span):has-text("추가"):near(input[type="email"])')
        page.wait_for_timeout(200)

        # 저장
        page.click('button[type="submit"]:has-text("설정 저장")')
        page.wait_for_timeout(1200)
        body = page.inner_text("body")
        assert "메일링 설정이 저장" in body or "저장되었습니다" in body, f"save banner missing: {body[:800]}"
        print("[3] 신규 설정 저장 ✓")

        # '저장된 설정' 탭 이동
        page.click('button:has-text("저장된 설정")')
        page.wait_for_timeout(600)
        body = page.inner_text("body")
        assert "E2E 테스트 설정" in body, f"saved setting not visible: {body[:800]}"
        print("[4] 저장된 설정 표시 ✓")

        # 테스트 발송 (dry-run 다이얼로그)
        dialogs = []
        page.on("dialog", lambda d: (dialogs.append(d.message), d.accept()))
        page.click('button:has-text("테스트 발송")')
        page.wait_for_timeout(1500)
        assert any("Dry-run" in m or "dry" in m.lower() or "SMTP" in m for m in dialogs), f"dry-run alert not seen: {dialogs}"
        print(f"[5] 테스트 발송 (dry-run) ✓ — dialog={dialogs[0][:80]}…")

        # 비활성화
        page.click('button:has-text("비활성화")')
        page.wait_for_timeout(600)
        body = page.inner_text("body")
        assert "활성화" in body, "toggle 후 활성화 버튼 노출 실패"
        print("[6] 비활성화 ✓")

        # 삭제
        page.on("dialog", lambda d: d.accept())
        page.click('button:has(i.ri-delete-bin-line)')
        page.wait_for_timeout(800)
        body = page.inner_text("body")
        assert "E2E 테스트 설정" not in body, "delete failed"
        print("[7] 삭제 ✓")

        browser.close()

        if errors:
            print("\n⚠ 에러:")
            for e in errors[:20]:
                print(f"  {e}")
        else:
            print("\n✅ 전체 pass")


if __name__ == "__main__":
    run()
