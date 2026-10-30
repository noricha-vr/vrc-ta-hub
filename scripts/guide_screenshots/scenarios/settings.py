"""外部連携設定。外部への通知・投稿は実行しない。"""
from capture import scenario


SETTINGS = '/community/settings/'
WEBHOOK = '#webhook'


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
    shot(capture, f'#integrations > .settings-integration:has(a[href="{href}"])',
         f'#integrations a[href="{href}"]')


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
    capture.page.locator('#webhook > summary').click()
    capture.page.locator('#notification-form [data-admin-secret-edit] > summary').click()
    capture.page.locator('input[name=notification_webhook_url]:not([type=hidden])').fill('https://discord.com/api/webhooks/000000000000/DUMMY')
    shot(capture, '#notification-form', 'input[name=notification_webhook_url]:not([type=hidden])', 'button[form=notification-form]')
    # 保存はせず、未保存の変更を破棄して保存済みのデモ状態へ戻る。
    capture.page.once('dialog', lambda dialog: dialog.accept())
    capture.goto(SETTINGS)
    capture.mask('input[name=notification_webhook_url]')
    capture.page.locator('#webhook > summary').click()
    shot(capture, WEBHOOK, 'form[action$="/webhook/test/"] button')
    capture.mock('discord-webhook-notification.html')
    shot(capture, '.diagram', '#notification')
    verify_guide(capture)


@scenario('settings/event-calendar', mobile=True)
def event_calendar(capture):
    entry(capture, '/community/calendar_update/')
    capture.goto('/community/calendar_update/')
    shot(capture, '.admin-form-field:has(#id_event_detail)', '#id_event_detail')
    shot(capture, '.admin-form-field:has(#id_event_genres)', '#id_event_genres')
    for field in ['join_condition', 'how_to_join']:
        shot(capture, f'.admin-form-field:has(#id_{field})', f'#id_{field}')
    shot(capture, '.admin-form-field:has(#id_note)', '#id_note', 'label[for=id_is_overseas_user]')
    shot(capture, '.admin-form-field:has(#id_x_post_text)', '#id_x_post_text')
    shot(capture, '[data-admin-action-bar]', 'button[form="calendar-entry-form"]')
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
    shot(capture, '#twitter-template-form', '#id_name', '#id_template', 'button[form="twitter-template-form"]')
    capture.page.once('dialog', lambda dialog: dialog.accept())
    capture.goto('/twitter/template/list/')
    edit = capture.page.locator('a:has-text("編集")').first
    edit_href = edit.get_attribute('href')
    card = 'article[data-template-delete]:has(a[href="' + edit_href + '"])'
    shot(capture, f'{card} >> xpath=..', f'{card} a:has-text("編集")', f'{card} [data-confirm-panel]')
    capture.goto(edit_href)
    shot(capture, '#twitter-template-form', '#id_template', 'button[form="twitter-template-form"]')
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
    # 生成APIは offline runner が遮断する。成功時だけ出る本文と投稿リンクを、
    # tweet_preview.html の成功時の構造に合わせ、架空の生成結果で補う。
    capture.page.locator('.admin-content').evaluate('''(content, text) => {
        const section = content.querySelector('section[aria-labelledby="tweet-heading"]');
        let body = section.querySelector('#tweet-text');
        if (!body) {
            body = document.createElement('p');
            body.id = 'tweet-text';
            body.className = 'text-break mb-0';
            section.querySelector('.admin-empty-state').replaceWith(body);
        }
        body.innerText = text;
        content.querySelector('#copy-button').disabled = false;
        const post = content.querySelector('section[aria-labelledby="post-heading"]');
        let link = post.querySelector('a');
        if (!link) {
            link = document.createElement('a');
            link.className = 'btn btn-primary';
            link.target = '_blank';
            link.rel = 'noopener noreferrer';
            link.innerHTML = '<i class="fa-brands fa-x-twitter me-2" aria-hidden="true"></i>X の投稿画面を開く ↗';
            post.append(link);
        }
        link.href = 'https://twitter.com/intent/tweet?text=' + encodeURIComponent(text);
    }''', '4月8日(月) 22:00からさくら技術交流会を開催します。\nはじめてのワールド制作（アオイ）\n初めての方も歓迎します。')
    shot(capture, '.admin-content', '#tweet-text', '#copy-button', 'a[href="https://x.com"]')
    capture.goto('/twitter/template/list/')
    trigger = capture.page.locator(f'{card} [data-confirm-panel]')
    panel = '#' + trigger.get_attribute('data-confirm-panel')
    trigger.click()
    capture.page.locator(panel).wait_for(state='visible')
    shot(capture, panel, f'{panel} button[type=submit]')
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
    shot(capture, 'section[aria-labelledby="new-key-heading"]', '#copy-new-key', '#reveal-new-key')
    capture.goto('/account/api-keys/')
    # 撮影用に作ったキーだけを無効化し、既存のキーで確認欄を説明する。
    created = 'article.presentation-row:has(h3:text-is("記録ツール用"))'
    capture.page.locator(f'{created} [data-confirm-panel]').click()
    capture.page.locator(f'{created} .confirm-panel button[type=submit]').click()
    capture.page.wait_for_load_state('networkidle')
    row = 'article.presentation-row:has(h3:text-is("資料連携用"))'
    capture.page.locator(f'{row} [data-confirm-panel]').click()
    capture.page.locator(f'{row} .confirm-panel').wait_for(state='visible')
    capture.mask(f'{row} code')
    shot(capture, row, f'{row} [data-confirm-panel]', f'{row} .confirm-panel button[type=submit]')
    verify_guide(capture)
