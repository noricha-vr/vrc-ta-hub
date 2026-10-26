"""集会管理の実画面。保存・外部送信は行わず、固定デモ状態を撮る。"""
from io import BytesIO

from PIL import Image

from capture import scenario
from scenarios.settings import shot


MAIN = 'さくら技術交流会'
TRANSFER = 'こもれび技術勉強会'


def snap(capture, selector, *marks):
    capture.page.evaluate('document.activeElement.blur()')
    capture.annotations.clear()
    for number, mark in enumerate(marks, 1):
        capture.annotate(mark, number)
    capture.shot(selector=selector)


def fields(capture, first, last, *marks):
    """フォームの項目群だけを、ラベルと説明を含めて切り出す。"""
    capture.page.evaluate('document.activeElement.blur()')
    capture.annotations.clear()
    for number, mark in enumerate(marks, 1):
        capture.annotate(mark, number)
    a = capture.page.locator(first).locator('xpath=ancestor-or-self::*[contains(@class,"admin-form-field") or self::fieldset][1]')
    b = capture.page.locator(last).locator('xpath=ancestor-or-self::*[contains(@class,"admin-form-field") or self::fieldset][1]')
    a.scroll_into_view_if_needed()
    boxes = [element.bounding_box() for element in (a, b)]
    scroll = capture.page.evaluate('({x: scrollX, y: scrollY})')
    left = max(0, boxes[0]['x'] - 16)
    top = max(0, boxes[0]['y'] + scroll['y'] - 20)
    capture.shot(clip={'x': left, 'y': top,
                       'width': min(boxes[0]['width'] + 32, capture.page.viewport_size['width'] - left),
                       'height': boxes[1]['y'] + scroll['y'] + boxes[1]['height'] + 10 - top})


def account_menu(capture):
    capture.goto('/account/settings/')
    if capture.mobile:
        capture.page.locator('.navbar-toggler').click()
    capture.page.locator('[aria-label="アカウントメニュー"]').click()
    snap(capture, '.account-menu', '.account-menu-identity')


def account_row(capture, name):
    return capture.page.locator('section[aria-labelledby="communities-heading"] .list-group-item').filter(has_text=name).filter(
        has=capture.page.locator('input[name=community_id]')).first


def select(capture, name=MAIN, *, settings=True):
    capture.goto('/account/settings/')
    row = account_row(capture, name)
    row.locator('button').nth(1 if settings else 0).click()
    capture.page.wait_for_load_state('networkidle')
    if settings:
        capture.mask('input[name=notification_webhook_url]')


def settings_entry(capture, name=MAIN):
    capture.goto('/account/settings/')
    row = account_row(capture, name)
    row.locator('button').nth(1).evaluate("e => e.dataset.guideSettings = '1'")
    row.evaluate("e => e.dataset.guideRow = '1'")
    snap(capture, '[data-guide-row]', '[data-guide-settings]')


def card(capture, title):
    return f'.admin-card:has(> .admin-section-heading > h2:text-is("{title}"))'


@scenario('community/create', mobile=True)
def create(capture):
    capture.login('general')
    account_menu(capture)
    capture.goto('/account/settings/')
    snap(capture, 'a[href="/community/create/"]', 'a[href="/community/create/"]')
    capture.goto('/community/create/')
    capture.page.locator('#id_name').fill('ヒナの科学交流会')
    capture.page.locator('#id_frequency').fill('毎週月曜日')
    capture.page.locator('#id_organizers').fill('ヒナ')
    fields(capture, '#id_name', '#id_frequency', '#id_name', '#id_frequency')
    fields(capture, '#id_organizers', '#id_discord', '#id_organizers', '#id_group_url', '#id_discord')
    poster = BytesIO()
    Image.new('RGB', (420, 594), '#e9f5f0').save(poster, 'PNG')
    capture.page.locator('#id_poster_image').set_input_files(
        {'name': 'demo-poster.png', 'mimeType': 'image/png', 'buffer': poster.getvalue()})
    capture.page.locator('#id_tags_1').check()
    fields(capture, '#id_poster_image', '#id_tags', '#id_poster_image', '#id_tags')
    # 申請は送信せず、同意欄と画面下の保存バーを撮る。
    capture.page.locator('#id_guidelines_agreed').check()
    shot(capture, card(capture, '登録前の確認'), 'label[for=id_guidelines_agreed]',
         'button[form=community-form]')
    capture.goto('/account/settings/')
    snap(capture, '.alert-warning:has-text("この集会は現在承認待ち")', '.alert-warning:has-text("この集会は現在承認待ち")')
    capture.login('owner')
    select(capture, settings=False)
    snap(capture, 'a:has-text("イベント登録") >> xpath=..', 'a:has-text("イベント登録")')


@scenario('community/edit', mobile=True)
def edit(capture):
    capture.login('owner')
    settings_entry(capture)
    select(capture)
    snap(capture, card(capture, '基本情報'), '#id_name')
    capture.page.locator('#id_name').fill('さくら技術交流会（紹介を更新）')
    fields(capture, '#id_name', '#id_frequency', '#id_name', '#id_frequency')
    snap(capture, card(capture, '掲載内容'), '#id_poster_image', '#id_description', '#id_tags')
    snap(capture, '[data-admin-action-bar]', 'button[form=community-form]')


@scenario('community/settings', mobile=True)
def settings(capture):
    capture.login('owner')
    settings_entry(capture)
    select(capture)
    snap(capture, card(capture, '基本情報'), '#id_name')
    snap(capture, '#applications', 'label[for=accepts_lt_application]', 'label[for=recording_allowed_true]',
         '#lt_application_template')
    snap(capture, '#integrations', 'a[href="/community/calendar_update/"]',
         'a[href="/twitter/template/list/"]', 'a[href="/account/api-keys/"]')
    snap(capture, '#members', '#members a')
    open_danger(capture)
    snap(capture, '#danger', '[data-confirm-panel="close-community"]',
         '[data-confirm-panel="create-transfer"]')


def open_danger(capture):
    capture.page.locator('#danger > details > summary').click()


def confirm(capture, panel_id):
    capture.page.locator(f'[data-confirm-panel="{panel_id}"]').click()
    capture.page.locator(f'#{panel_id}').wait_for(state='visible')


@scenario('community/switch', mobile=True)
def switch(capture):
    capture.login('owner')
    account_menu(capture)
    capture.goto('/account/settings/')
    row = account_row(capture, TRANSFER)
    row.evaluate("e => e.dataset.guideRow = '1'")
    row.locator('button').nth(0).evaluate("e => e.dataset.guideChoose = '1'")
    row.locator('button').nth(1).evaluate("e => e.dataset.guideSettings = '1'")
    snap(capture, '[data-guide-row]', '[data-guide-choose]', '[data-guide-settings]')
    select(capture, TRANSFER, settings=False)
    capture.page.locator('[aria-label="管理中の集会を切り替え"]').click()
    capture.page.locator('.community-selector .dropdown-menu.show').wait_for()
    capture.annotations.clear()
    capture.annotate('[aria-label="管理中の集会を切り替え"]', 1)
    capture.annotate('.community-selector .dropdown-menu.show button.dropdown-item:has-text("さくら技術交流会")', 2)
    heading = capture.page.locator('.community-selector').bounding_box()
    menu = capture.page.locator('.community-selector .dropdown-menu.show').bounding_box()
    scroll = capture.page.evaluate('scrollY')
    left = max(0, min(heading['x'], menu['x']) - 16)
    right = max(heading['x'] + heading['width'], menu['x'] + menu['width'])
    capture.shot(clip={'x': left, 'y': heading['y'] + scroll - 28,
                       'width': min(right - left + 16, capture.page.viewport_size['width'] - left),
                       'height': menu['y'] + menu['height'] - heading['y'] + 40})


@scenario('community/close', mobile=True)
def close(capture):
    capture.login('owner')
    select(capture)
    open_danger(capture)
    snap(capture, '#danger', '[data-confirm-panel="close-community"]')
    confirm(capture, 'close-community')
    snap(capture, '#close-community', '#close-community button[type=submit]')
    capture.goto('/account/settings/')
    target = capture.page.locator('section[aria-labelledby="communities-heading"] .list-group-item').filter(has_text='ひだまり学習会')
    target.evaluate("e => e.dataset.guideEnded = '1'")
    snap(capture, '[data-guide-ended]', '[data-guide-ended] a')
    target.locator('a').click()
    capture.page.wait_for_load_state('networkidle')
    fields(capture, '#id_name', '#id_duration', '#id_name', '#id_start_time')
    snap(capture, card(capture, '掲載内容'), '#id_poster_image', '#id_description')
    snap(capture, '[data-admin-action-bar]', 'button[form=community-form]')
    # 再開後と同じ、終了日のない固定集会の完了画面を表示する。
    select(capture)
    community_id = capture.page.locator('form[action*="/close/"]').get_attribute('action').split('/')[-2]
    capture.goto(f'/community/reopen/{community_id}/complete/')
    snap(capture, '.admin-card', 'button:has-text("開催予定・開催周期を登録する")')


@scenario('community/report', mobile=True)
def report(capture):
    capture.goto('/community/list/')
    capture.page.locator('a').filter(has_text=MAIN).first.click()
    capture.page.wait_for_load_state('networkidle')
    snap(capture, '[data-bs-target="#reportModal"]', '[data-bs-target="#reportModal"]')
    capture.page.locator('[data-bs-target="#reportModal"]').click()
    capture.page.locator('#reportModal.show').wait_for()
    capture.page.wait_for_timeout(300)
    snap(capture, '#reportModal .modal-content', '#reportModal button[type=submit]')
