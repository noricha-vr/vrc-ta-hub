"""イベントの入口・管理・日時変更・削除・公式告知を撮影する。"""

from capture import scenario


CARD = '[data-guide-event-card=first]'
HEADER = '.mylist-header'
NAV = 'nav.mylist-nav'
EVENT_MENU = '.event-actions button[aria-label$="の開催のその他の操作"]'


def region(capture, *selectors, top=28, bottom=12):
    """関連する項目の範囲だけ切り出し、赤丸用の余白を残す。"""
    page = capture.page
    boxes = []
    for selector in selectors:
        boxes.append(page.locator(selector).evaluate('''e => {
            const r = e.getBoundingClientRect();
            return {x:r.x + scrollX, y:r.y + scrollY, width:r.width, height:r.height};
        }'''))
    x = max(0, min(box['x'] for box in boxes) - 18)
    y = max(0, min(box['y'] for box in boxes) - top)
    right = min(page.viewport_size['width'], max(box['x'] + box['width'] for box in boxes) + 18)
    bottom = max(box['y'] + box['height'] for box in boxes) + bottom
    capture.shot(clip={'x': x, 'y': y, 'width': right - x, 'height': bottom - y})


def menu(capture):
    capture.login('owner')
    capture.goto('/event/my_list/')
    page = capture.page
    if capture.mobile:
        page.locator('.navbar-toggler').click()
    page.locator('[aria-label="アカウントメニュー"]').click()
    capture.annotate('[aria-label="アカウントメニュー"]', 1)
    capture.annotate('.account-menu a[href^="/event/my_list/?community="]:has-text("さくら技術交流会")', 2)
    region(capture, '[aria-label="アカウントメニュー"]', '.account-menu')


def management(capture):
    capture.goto('/event/my_list/')
    # Vket のお知らせなど開催回以外のカードを除き、最初の開催回のカードを使う。
    card = capture.page.locator('article.event-card:has(a[href*="/date/update/"])').first
    card.wait_for()
    card.evaluate(
        "e => e.dataset.guideEventCard = 'first'")


def event_link(capture, action):
    return capture.page.locator(CARD).locator(f'a[href*="{action}"]').get_attribute('href')


def verify_guide(capture, images=None):
    """共通の検証（capture.verify_guide）に任せる。"""
    capture.verify_guide()


def open_event_menu(capture, card=CARD):
    """開催回のメニューを開き、表示された項目を撮れる状態にする。"""
    capture.page.locator(f'{card} {EVENT_MENU}').click()
    capture.page.locator(f'{card} .event-actions .dropdown-menu.show').wait_for()


@scenario('event/manage', mobile=True)
def manage(capture):
    menu(capture)
    management(capture)
    for number, selector in enumerate((
        '.community-selector', '.mylist-header-actions', NAV, '#action-required',
        f'{CARD} .event-date', f'{CARD} .event-actions > a',
        f'{CARD} .event-actions .dropdown-toggle', f'{CARD} {EVENT_MENU}',
        '#presentation-1 .presentation-actions', '#presentation-2 .presentation-actions',
        f'{CARD} .event-footer',
    ), 1):
        capture.annotate(selector, number)
    region(capture, HEADER, NAV, '#action-required', CARD)
    capture.annotations.clear()
    capture.annotate('#more-future-events-toggle', 1)
    capture.shot(selector='#more-future-events-toggle')
    verify_guide(capture)


@scenario('event/create', mobile=True)
def create(capture):
    menu(capture)
    management(capture)
    capture.annotate('.mylist-header-actions a[href="/event/calendar/create/"]', 1)
    capture.shot(selector=HEADER)
    capture.goto('/event/calendar/create/')
    capture.page.locator('#id_start_date').fill('2030-04-15')
    capture.page.locator('#id_start_time').fill('22:00')
    capture.page.locator('#id_duration').fill('60')
    for number, field in enumerate(('start_date', 'start_time', 'duration'), 1):
        capture.annotate(f'#id_{field}', number)
    region(capture, '#calendar-form .admin-card:has(#id_start_date)')
    capture.annotations.clear()
    capture.page.locator('input[name=recurrence_type][value=weekly]').check()
    capture.page.locator('#id_weekday').select_option('MO')
    capture.annotate('input[name=recurrence_type][value=weekly] >> xpath=ancestor::div[contains(@class,"form-check")][1]', 1)
    capture.annotate('#id_weekday', 2)
    region(capture, '#calendar-form .admin-card:has(input[name="recurrence_type"])')
    capture.annotations.clear()
    capture.annotate('button[form="calendar-form"]', 1)
    region(capture, '[data-admin-action-bar]', top=20)
    verify_guide(capture, 10)


@scenario('event/edit', mobile=True)
def edit(capture):
    capture.login('owner')
    management(capture)
    date_url = event_link(capture, '/date/update/')
    time_url = event_link(capture, '/event/update/')
    open_event_menu(capture)
    capture.annotate(f'{CARD} {EVENT_MENU}', 1)
    capture.annotate(f'{CARD} .dropdown-menu.show a[href*="/date/update/"]', 2)
    region(capture, f'{CARD} .event-summary', f'{CARD} .event-actions .dropdown-menu.show')
    capture.goto(date_url)
    capture.page.locator('#id_date').fill('2030-04-09')
    capture.page.evaluate('document.activeElement.blur()')
    capture.annotate('#id_date', 1)
    capture.annotate('button[form="schedule-form"]', 2)
    region(capture, '#schedule-form', '[data-admin-action-bar]')
    management(capture)
    open_event_menu(capture)
    capture.annotate(f'{CARD} {EVENT_MENU}', 1)
    capture.annotate(f'{CARD} .dropdown-menu.show a[href^="/event/update/"]', 2)
    region(capture, f'{CARD} .event-summary', f'{CARD} .event-actions .dropdown-menu.show')
    capture.goto(time_url)
    capture.page.locator('#id_start_time').fill('22:30')
    capture.page.evaluate('document.activeElement.blur()')
    capture.annotate('#id_start_time', 1)
    capture.annotate('button[form="schedule-form"]', 2)
    region(capture, '#schedule-form', '[data-admin-action-bar]')
    verify_guide(capture, 8)


@scenario('event/delete', mobile=True)
def delete(capture):
    capture.login('owner')
    management(capture)
    # 親の削除による連鎖と区別するため、子開催回を使う。
    card = 'article.event-card[aria-label="4月15日の開催"]'
    capture.annotate(f'{card} .event-date', 1)
    capture.shot(selector=f'{card} .event-summary')
    capture.annotations.clear()
    open_event_menu(capture, card)
    capture.annotate(f'{card} {EVENT_MENU}', 1)
    capture.annotate(f'{card} .dropdown-menu.show [data-confirm-panel^="event-delete-"]', 2)
    region(capture, f'{card} .event-summary', f'{card} .event-actions .dropdown-menu.show')
    capture.page.locator(f'{card} [data-confirm-panel^="event-delete-"]').click()
    panel = f'{card} .confirm-panel[id^="event-delete-"]'
    capture.page.locator(panel).wait_for(state='visible')
    capture.annotations.clear()
    capture.annotate(f'{panel} .form-check', 1)
    capture.annotate(f'{panel} button[type=submit]', 2)
    capture.annotate(f'{panel} [data-cancel-panel]', 3)
    capture.shot(selector=panel)
    # 確定ボタンは押さず、削除や外部処理を起動しない。
    verify_guide(capture)


@scenario('event/auto-post', mobile=True)
def auto_post(capture):
    capture.login('owner')
    capture.goto('/community/update/')
    capture.page.locator('#id_twitter_hashtag').fill('#さくら技術交流会')
    capture.annotate('#id_twitter_hashtag', 1)
    region(capture, '#id_twitter_hashtag >> xpath=..', top=4)
    capture.page.once('dialog', lambda dialog: dialog.accept())
    capture.goto('/twitter/queue/?status=ready')
    capture.page.locator('article.queue-row').first.evaluate("e => e.dataset.guideQueue = 'first'")
    card = 'article.queue-row[data-guide-queue="first"]'
    capture.annotate(f'{card} .event-summary', 1)
    capture.annotate(f'{card} .event-actions a', 2)
    region(capture, 'nav[aria-label="ポストの状態で絞り込み"]', card, top=4)
    detail_url = capture.page.locator(f'{card} .event-actions a').get_attribute('href')
    capture.goto(detail_url)
    capture.annotate('section[aria-labelledby="queue-info-heading"]', 1)
    capture.annotate('#id_generated_text', 2)
    region(capture, 'section[aria-labelledby="queue-info-heading"]', 'section[aria-labelledby="queue-text-heading"]')
    capture.mock('event-x-post.html')
    capture.annotate('#post-text', 1)
    capture.shot(selector='main')
    verify_guide(capture, 8)
