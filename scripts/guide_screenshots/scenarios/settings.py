"""外部連携設定。外部への通知・投稿は実行しない。"""
from capture import scenario
from scenarios.analytics import table_row


SETTINGS = '/community/settings/'
WEBHOOK = 'div.border-top:has(input[name=notification_webhook_url])'


def shot(capture, selector, *targets):
    capture.annotations.clear()
    for number, target in enumerate(targets, 1):
        capture.annotate(target, number)
    capture.page.locator(selector).scroll_into_view_if_needed()
    # full_page の撮影は表示の高さをページ全体に広げ、vh 基準の余白で中身が下にずれる。
    # 先に同じ高さへ広げてから測り、撮り終えたら戻す（下端の番号やボタンが切れないように）。
    viewport = capture.page.viewport_size
    height = capture.page.evaluate('document.documentElement.scrollHeight')
    capture.page.set_viewport_size({'width': viewport['width'], 'height': max(viewport['height'], height)})
    capture.page.evaluate('document.fonts.ready')
    try:
        box = capture.page.locator(selector).bounding_box()
        left, top = box['x'], box['y'] - 4
        right, bottom = box['x'] + box['width'], box['y'] + box['height'] + 16
        for target in targets:
            mark = capture.page.locator(target).bounding_box()
            left, top = min(left, mark['x'] - 3), min(top, mark['y'] - 16)
            right, bottom = max(right, mark['x'] + mark['width'] + 16), max(bottom, mark['y'] + mark['height'] + 16)
        scroll_y = capture.page.evaluate('scrollY')
        capture.shot(clip={'x': max(0, left), 'y': max(0, top + scroll_y),
                           'width': min(right, viewport['width']) - max(0, left),
                           'height': bottom - top})
    finally:
        capture.page.set_viewport_size(viewport)


def verify_guide(capture):
    """共通の検証（capture.verify_guide）に任せる。"""
    capture.verify_guide()


def entry(capture, href):
    capture.login('owner')
    capture.goto(SETTINGS)
    capture.mask('input[name=notification_webhook_url]')
    shot(capture, f'div.col-md-4:has(a[href="{href}"])', f'a[href="{href}"]')


@scenario('settings/discord', mobile=True)
def discord(capture):
    for stage, target in [
        ('server', '#server-settings'), ('integrations', '#integrations'),
        ('webhooks', '#webhooks'), ('new', '#new-webhook'),
        ('channel', '#channel'), ('copy', '#copy-url'),
    ]:
        capture.mock('discord-webhook.html')
        capture.page.evaluate('(stage) => showStage(stage)', stage)
        shot(capture, '.diagram', target)
    capture.login('owner')
    capture.goto(SETTINGS)
    capture.mask('input[name=notification_webhook_url]')
    shot(capture, WEBHOOK, 'input[name=notification_webhook_url]', 'form[action$="/webhook/update/"] button')
    shot(capture, WEBHOOK, 'form[action$="/webhook/test/"] button')
    capture.mock('discord-webhook-notification.html')
    shot(capture, '.diagram', '#notification')
    verify_guide(capture)


@scenario('settings/event-calendar', mobile=True)
def event_calendar(capture):
    entry(capture, '/community/calendar_update/')
    capture.goto('/community/calendar_update/')
    shot(capture, '.card-body > div:has(#id_event_detail)', '#id_event_detail')
    shot(capture, '.card-body > div:has(#id_event_genres)', '#id_event_genres')
    for field in ['join_condition', 'how_to_join']:
        shot(capture, f'.card-body > div:has(#id_{field})', f'#id_{field}')
    capture.annotations.clear()
    capture.annotate('#id_note', 1)
    capture.annotate('label[for=id_is_overseas_user]', 2)
    top = capture.page.locator('.card-body > div:has(#id_note)').bounding_box()
    bottom = capture.page.locator('.card-body > div:has(#id_is_overseas_user)').bounding_box()
    scroll_y = capture.page.evaluate('scrollY')
    capture.shot(clip={'x': max(0, top['x'] - 16), 'y': top['y'] + scroll_y - 32,
                       'width': top['width'] + 32, 'height': bottom['y'] + bottom['height'] - top['y'] + 48})
    shot(capture, 'form[enctype] .card-body > div:has(#id_x_post_text)', '#id_x_post_text')
    shot(capture, 'form[enctype] > .mt-4', 'form[enctype] button[type=submit]')
    capture.goto('/event/my_list/')
    target = 'a:has-text("VRCイベントカレンダーに投稿")'
    shot(capture, f'{target} >> nth=0 >> xpath=..', f'{target} >> nth=0')
    verify_guide(capture)


@scenario('settings/x-template', mobile=True)
def x_template(capture):
    entry(capture, '/twitter/template/list/')
    capture.goto('/twitter/template/list/')
    shot(capture, 'a:has-text("新規作成")', 'a:has-text("新規作成")')
    capture.page.locator('a:has-text("新規作成")').click()
    capture.page.locator('#id_name').fill('いつもの告知')
    capture.page.locator('#id_template').fill('{date} {time}から{event_name}を開催します。\n{theme}\n初めての方も歓迎します。\n{group_url}\n{hashtag}')
    shot(capture, 'form:has(#id_template)', '#id_name', '#id_template', 'form:has(#id_template) button[type=submit]')
    capture.goto('/twitter/template/list/')
    edit = capture.page.locator('a:has-text("編集")').first
    edit_href = edit.get_attribute('href')
    shot(capture, '.card:has(.delete-template)', 'a:has-text("編集")', '.delete-template')
    capture.goto(edit_href)
    shot(capture, 'form:has(#id_template)', '#id_template', 'form:has(#id_template) button[type=submit]')
    capture.goto('/event/my_list/')
    event = 'article.event-card:has(a[href="/event/1/detail/create/"])'
    capture.page.locator(f'{event} .event-actions .dropdown-toggle').click()
    capture.page.locator(f'{event} .event-actions .dropdown-menu.show').wait_for()
    preview = capture.page.locator(f'{event} .dropdown-menu.show a:text-is("いつもの告知")')
    preview_href = preview.get_attribute('href')
    shot(capture, f'{event} .event-actions',
         f'{event} .event-actions .dropdown-toggle',
         f'{event} .dropdown-menu.show a:text-is("いつもの告知")')
    capture.goto(preview_href)
    # 生成APIは offline runner が遮断する。生成本文だけを架空の結果で置く。
    capture.page.locator('#tweet-text').evaluate("e => e.innerText = '4月8日(月) 22:00からさくら技術交流会を開催します。\\nはじめてのワールド制作（アオイ）\\n初めての方も歓迎します。'")
    shot(capture, '.container > .card', '#tweet-text', '#copy-button', 'a:has-text("Xを開く")')
    capture.goto('/twitter/template/list/')
    capture.page.locator('.delete-template').first.click()
    capture.page.locator('#deleteConfirmModal').wait_for(state='visible')
    shot(capture, '#deleteConfirmModal .modal-content', '#confirmDelete')
    verify_guide(capture)


@scenario('settings/api-key', mobile=True)
def api_key(capture):
    entry(capture, '/account/api-keys/')
    capture.goto('/account/api-keys/')
    capture.page.locator('#keyName').fill('記録ツール用')
    shot(capture, 'form:has(#keyName)', '#keyName', 'form:has(#keyName) button')
    # 作成は一時SQLite内だけ。外部サービスには送らない。
    capture.page.locator('form:has(#keyName) button').click()
    capture.page.wait_for_load_state('networkidle')
    capture.mask('.alert-warning code')
    shot(capture, '.alert-warning:has(code)', '.alert-warning code')
    capture.goto('/account/api-keys/')
    capture.page.once('dialog', lambda dialog: dialog.accept())
    capture.page.locator('tr:has-text("記録ツール用") button').click()
    capture.page.wait_for_load_state('networkidle')
    capture.mask('td code')
    row = 'tr:has-text("資料連携用")'
    table_row(capture, row, f'{row} button')
    verify_guide(capture)
