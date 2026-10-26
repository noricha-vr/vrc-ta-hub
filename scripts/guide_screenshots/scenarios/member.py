"""主催者と、招待を受け取る側の実画面。招待 URL は必ず伏せる。"""
from capture import scenario
from scenarios.community import TRANSFER, card, select, snap


def members(capture):
    select(capture)
    link = capture.page.locator('a:has-text("メンバーを管理")')
    link.click()
    capture.page.wait_for_load_state('networkidle')
    capture.mask('input[id^="invitation-"]')


def member_entry(capture):
    select(capture)
    snap(capture, card(capture, 'メンバー管理'), 'a:has-text("メンバーを管理")')


@scenario('member/invite', mobile=True)
def invite(capture):
    capture.login('owner')
    member_entry(capture)
    members(capture)
    snap(capture, 'form[action$="/invite/create/"]', 'form[action$="/invite/create/"] button')
    snap(capture, '.card:has(form[action$="/invite/create/"]) li.list-group-item',
         'button[onclick^="copyInvitationLink"]')
    capture.page.context.clear_cookies()
    capture.goto('/community/invite/guide-demo-staff-invitation/')
    snap(capture, '.card', '.card a:has-text("ログイン")')
    capture.login('general')
    capture.goto('/community/invite/guide-demo-staff-invitation/')
    snap(capture, '.card', '.card button:has-text("招待を受ける")')
    capture.login('owner')
    members(capture)
    snap(capture, '.card:has(form[action$="/invite/create/"]) li.list-group-item',
         'form[action*="/revoke/"] button')


@scenario('member/remove', mobile=True)
def remove(capture):
    capture.login('owner')
    member_entry(capture)
    members(capture)
    row = '.card:has(> .card-header:text-is("メンバー一覧")) li:has(strong:text-is("レン"))'
    snap(capture, row, f'{row} > div', f'{row} button')


@scenario('member/transfer', mobile=True)
def transfer(capture):
    capture.login('owner')
    # リンク未発行の集会で作成前の操作を撮る。モーダルには集会名を表示しない。
    select(capture)
    snap(capture, card(capture, '危険な操作'), '[data-bs-target="#createTransferModal"]')
    capture.page.locator('[data-bs-target="#createTransferModal"]').click()
    capture.page.locator('#createTransferModal.show').wait_for()
    capture.page.wait_for_timeout(300)
    snap(capture, '#createTransferModal .modal-content', '#createTransferModal button[type=submit]')
    # 発行後は固定トークン・固定期限の状態へ。リンク自体は表示しない。
    select(capture, TRANSFER)
    capture.mask('#transferLinkInput')
    snap(capture, card(capture, '危険な操作'), 'button[onclick="copyTransferLink()"]',
         'form[action*="/transfer/"][action$="/revoke/"] button')
    capture.page.context.clear_cookies()
    capture.goto('/community/transfer/guide-demo-ownership-transfer/')
    snap(capture, '.card', '.card a:has-text("ログイン")')
    capture.login('general')
    capture.goto('/community/transfer/guide-demo-ownership-transfer/')
    snap(capture, '.card', '.card button:has-text("主催者を引き継ぐ")')
