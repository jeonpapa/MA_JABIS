"""Workbench smoke — 검색 → 매칭 → 시나리오 → 조정가 → HTA & xlsx export."""
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

        # Sidebar Negotiation Workbench 링크
        page.wait_for_timeout(500)
        side = page.inner_text('nav')
        assert "Negotiation Workbench" in side, f"sidebar link missing: {side[:300]}"
        print("[2] Sidebar Negotiation Workbench 링크 ✓")

        # /workbench 진입
        page.goto("http://localhost:3000/workbench", wait_until="networkidle")
        page.wait_for_timeout(1000)
        body = page.inner_text("body")
        assert "Negotiation Workbench" in body, "title missing"
        assert "제품 검색" in body, "step 1 header missing"
        print("[3] /workbench 진입 + Stepper 렌더 ✓")

        # 검색: keytruda (캐시에 6개국 존재)
        page.fill('input[placeholder*="제품명 또는 성분명"]', "keytruda")
        page.click('button:has-text("검색")')
        # compute 까지 끝나길 대기 (API 2-3초)
        page.wait_for_timeout(4000)
        body = page.inner_text("body")
        assert "해석됨" in body or "pembrolizumab" in body.lower(), f"resolve not rendered: {body[:500]}"
        print("[4] 검색 + ingredient 해석 ✓")

        # 매칭 테이블 — 국가 표시
        assert "JP" in body and "DE" in body, f"매칭 국가 코드 누락: {body[500:1500]}"
        print("[5] 매칭 테이블 국가 렌더 ✓")

        # 시나리오 카드 — A안/B안/C안
        assert "A안" in body and "B안" in body and "C안" in body, f"시나리오 카드 누락: {body[500:2000]}"
        print("[6] 기본 시나리오 3개 생성 ✓")

        # 조정가 상세 — "조정가 상세" + "제안 상한가"
        assert "조정가 상세" in body, "step 4 제목 누락"
        assert "제안 상한가" in body, "제안 상한가 라벨 누락"
        print("[7] 조정가 상세 테이블 + 제안 상한가 렌더 ✓")

        # HTA 로드 버튼 클릭 — Keytruda 캐시가 있을 가능성 있음
        page.click('button:has-text("HTA 데이터 로드")')
        page.wait_for_timeout(2500)
        # 성공해도 실패해도 UI 가 crash 하지 않으면 OK
        body = page.inner_text("body")
        assert "HTA Matrix" in body, "HTA section 사라짐"
        print("[8] HTA 로드 버튼 클릭 → UI 안정 ✓")

        # Export bar
        assert "xlsx Export" in body, "export 버튼 없음"
        print("[9] Export bar 렌더 ✓")

        # 시나리오 추가 모달 열기 → 바로 닫기 (빠른 검증)
        page.click('button:has-text("+ 시나리오 추가")')
        page.wait_for_timeout(300)
        modal_body = page.inner_text("body")
        assert "시나리오 추가" in modal_body, "모달 title 누락"
        assert "포함 국가" in modal_body, "국가 선택 라벨 누락"
        # 취소 버튼
        page.click('button:has-text("취소")')
        page.wait_for_timeout(300)
        print("[10] 시나리오 추가 모달 open/close ✓")

        # 가정치 설정 페이지 진입
        page.click('a:has-text("가정치 설정")')
        page.wait_for_url("http://localhost:3000/admin/workbench-settings", timeout=3000)
        page.wait_for_timeout(1000)
        body = page.inner_text("body")
        assert "Workbench Settings" in body, "settings title 누락"
        assert "공장도비율" in body, "공장도비율 필드 누락"
        assert "Japan" in body and "Germany" in body, "국가 카드 렌더 실패"
        print("[11] 가정치 설정 페이지 진입 + 국가 카드 렌더 ✓")

        # 값 수정 → 저장 → 재로드 확인
        # 국가 카드 input 은 문서 순서 JP/IT/FR/CH/UK/DE/US — 글로벌 input 2개 이후 JP 첫 필드가 factory_ratio
        ratio_inputs = page.locator('input[type="number"]').all()
        assert len(ratio_inputs) >= 4, f"예상보다 input 개수 적음: {len(ratio_inputs)}"
        # 글로벌 2개 + JP 4개 → 3번째 input 이 JP factory_ratio
        jp_ratio = ratio_inputs[2]
        original = jp_ratio.input_value()
        new_val = str(round(float(original) + 1.0, 2))
        jp_ratio.fill(new_val)
        page.wait_for_timeout(300)
        assert "저장하지 않은 변경사항" in page.inner_text("body"), "dirty 상태 표시 안 됨"
        page.click('button:has-text("저장")')
        page.wait_for_timeout(1500)
        body = page.inner_text("body")
        assert "변경 없음" in body, f"저장 후 dirty clear 실패: {body[-500:]}"
        print(f"[12] 가정치 수정 ({original}→{new_val}) + 저장 ✓")

        # 원복 — HIRA 기본값 복원
        page.on("dialog", lambda d: d.accept())
        page.click('button:has-text("HIRA 기본값 복원")')
        page.wait_for_timeout(800)
        # 저장해서 defaults 값으로 돌려놓기
        page.click('button:has-text("저장")')
        page.wait_for_timeout(1500)
        print("[13] HIRA 기본값 복원 + 저장 ✓")

        browser.close()

        if errors:
            print("\n⚠ 에러:")
            for e in errors[:20]:
                print(f"  {e}")
        else:
            print("\n✅ 전체 pass")


if __name__ == "__main__":
    run()
