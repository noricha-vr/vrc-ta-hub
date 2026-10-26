"""主催者と、招待を受け取る側の実画面。招待 URL は必ず伏せる。"""
from capture import scenario
from scenarios.community import TRANSFER, card, confirm, open_danger, select, snap


def members(capture):
    select(capture)
    capture.page.locator('#members a').click()
    capture.page.wait_for_load_state('networkidle')


def member_entry(capture):
    select(capture)
    snap(capture, '#members', '#members a')


def reveal_link(capture):
    """明示的に共有表示へ進み、共有リンクの入力値は撮影前に伏せる。"""
    capture.page.locator('[data-reveal-invitation]').click()
    capture.page.locator('[data-copy-invitation]').wait_for(state='visible')
    capture.mask('input[id^="shared-link-"]')


@scenario('member/invite', mobile=True)
def invite(capture):
    capture.login('owner')
    member_entry(capture)
    members(capture)
    snap(capture, card(capture, '招待リンク'), 'form[action$="/invite/create/"] button')
    # 生成はせず、seed 済みの招待リンクで共有操作を撮る。
    snap(capture, '.admin-card [id^="invitation-"]', '[data-reveal-invitation]')
    reveal_link(capture)
    trigger = capture.page.locator('[data-confirm-panel^="revoke-invitation-"]')
    panel_id = trigger.get_attribute('data-confirm-panel')
    confirm(capture, panel_id)
    snap(capture, '.admin-card [id^="invitation-"]',
         '[data-copy-invitation]', '[data-hide-invitation]',
         '[data-confirm-panel^="revoke-invitation-"]', f'#{panel_id} button[type=submit]')


@scenario('member/accept', mobile=True)
def accept(capture):
    capture.page.context.clear_cookies()
    capture.goto('/community/invite/guide-demo-staff-invitation/')
    snap(capture, '.admin-card', '.admin-card a:has-text("ログイン")')
    capture.login('general')
    capture.goto('/community/invite/guide-demo-staff-invitation/')
    snap(capture, '.admin-card', '#accept-invitation button[type=submit]')


@scenario('member/remove', mobile=True)
def remove(capture):
    capture.login('owner')
    member_entry(capture)
    members(capture)
    row = f'{card(capture, "メンバー一覧")} li:has(strong:text-is("レン"))'
    trigger = capture.page.locator(f'{row} [data-confirm-panel]')
    panel_id = trigger.get_attribute('data-confirm-panel')
    confirm(capture, panel_id)
    snap(capture, row, f'{row} .community-row > div', f'{row} [data-confirm-panel]',
         f'#{panel_id} button[type=submit]')


@scenario('member/transfer', mobile=True)
def transfer(capture):
    capture.login('owner')
    # リンク未発行の集会で、画面内の確認欄を開く。
    select(capture)
    open_danger(capture)
    snap(capture, '#danger', '[data-confirm-panel="create-transfer"]')
    confirm(capture, 'create-transfer')
    snap(capture, '#create-transfer', '#create-transfer button[type=submit]')
    # 作成はせず、固定トークン・固定期限の集会へ移る。
    select(capture, TRANSFER)
    open_danger(capture)
    reveal_link(capture)
    # 共有表示はリンクの部分だけが差し替わり、「危険な操作」は開いたまま。
    confirm(capture, 'revoke-transfer')
    snap(capture, '#danger', '[data-copy-invitation]', '[data-hide-invitation]',
         '[data-confirm-panel="revoke-transfer"]', '#revoke-transfer button[type=submit]')
    capture.page.context.clear_cookies()
    capture.goto('/community/transfer/guide-demo-ownership-transfer/')
    snap(capture, '.admin-card', '.admin-card a:has-text("ログイン")')
    capture.login('general')
    capture.goto('/community/transfer/guide-demo-ownership-transfer/')
    snap(capture, '.admin-card', '#accept-transfer button[type=submit]')
