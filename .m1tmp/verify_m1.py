"""M1 verification: international-pricing page wired to real backend data."""
from playwright.sync_api import sync_playwright

BASE = 'http://localhost:3000'
console_msgs = []

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width': 1600, 'height': 1000})
    page.on('console', lambda m: console_msgs.append(f'[{m.type}] {m.text}'))
    page.on('pageerror', lambda e: console_msgs.append(f'[pageerror] {e}'))

    # 1. Login
    page.goto(f'{BASE}/login')
    page.wait_for_load_state('networkidle')
    page.fill('input[type="email"]', 'admin@marketintel.kr')
    page.fill('input[type="password"]', 'admin1234')
    page.click('button[type="submit"]') if page.locator('button[type="submit"]').count() else page.get_by_role('button', name='로그인').first.click()
    page.wait_for_timeout(1500)
    print('after login url:', page.url)

    # 2. Go to international-pricing
    page.goto(f'{BASE}/international-pricing')
    page.wait_for_load_state('networkidle')
    page.wait_for_timeout(1200)

    # history cards
    cards = page.locator('button:has-text("개국 가격 캐시")')
    print('history cards:', cards.count())
    for i in range(cards.count()):
        print('  card:', cards.nth(i).inner_text().replace('\n', ' | ')[:80])

    # 3. Click Keytruda card
    page.locator('button', has_text='Keytruda').first.click()
    page.wait_for_timeout(2500)  # pricing tab fetch

    body = page.inner_text('body')
    print('\n--- KEYTRUDA A8 checks ---')
    for needle in ['€3,428', '조정가', '일일', '가격 미공개', '급여정보 없음', '정보 없음', 'AIFA']:
        print(f'  {needle!r:25} →', needle in body)

    page.screenshot(path='/tmp/m1_foreign.png', full_page=True)

    # 4. HTA tab
    page.get_by_role('button', name='HTA 현황').click()
    page.wait_for_timeout(3000)
    body = page.inner_text('body')
    print('\n--- KEYTRUDA HTA checks ---')
    for needle in ['NICE', 'CADTH', 'PBAC', 'SMC', '평가일', '총 ']:
        print(f'  {needle!r:25} →', needle in body)
    # expand SMC (scotland)
    smc_btn = page.locator('button:has-text("SMC")').first
    smc_btn.click()
    page.wait_for_timeout(600)
    body = page.inner_text('body')
    print('  SMC expand → Appraisals listed:', 'Appraisals' in body)
    page.screenshot(path='/tmp/m1_foreign_hta.png', full_page=True)

    # 5. Approval tab
    page.get_by_role('button', name='허가 현황').click()
    page.wait_for_timeout(3000)
    body = page.inner_text('body')
    print('\n--- KEYTRUDA 허가 checks ---')
    for needle in ['FDA', 'EMA', 'MHRA', 'PMDA', 'MFDS', 'TGA', '최초 허가일', '개 적응증']:
        print(f'  {needle!r:25} →', needle in body)
    # expand MFDS card
    page.locator('button:has-text("MFDS")').first.click()
    page.wait_for_timeout(800)
    body = page.inner_text('body')
    print('  MFDS expand → 승인/원문:', ('승인:' in body), ('라벨 원문' in body))
    page.screenshot(path='/tmp/m1_foreign_approval.png', full_page=True)

    # 6. Welireg spot-check (A8 prices: CA 213.33 CAD, JP 21,916.8 JPY, UK 11,936.7 GBP)
    page.locator('button', has_text='Welireg').first.click()
    page.wait_for_timeout(2500)
    body = page.inner_text('body')
    print('\n--- WELIREG A8 checks ---')
    for needle in ['CA$213.33', '¥21,916.8', '£11,936.7', '가격 미공개', 'belzutifan', 'Ontario EAP', 'MIMS']:
        print(f'  {needle!r:25} →', needle in body)
    page.screenshot(path='/tmp/m1_foreign_welireg.png', full_page=True)

    browser.close()

print('\n--- CONSOLE ---')
errs = [m for m in console_msgs if m.startswith('[error]') or m.startswith('[pageerror]')]
warns = [m for m in console_msgs if m.startswith('[warning]')]
print(f'total={len(console_msgs)} errors={len(errs)} warnings={len(warns)}')
for m in errs[:10]:
    print(' ', m[:200])
