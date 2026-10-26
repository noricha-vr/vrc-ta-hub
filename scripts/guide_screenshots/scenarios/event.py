"""イベントの入口・管理・日時変更・削除・公式告知を撮影する。"""

from capture import scenario


CARD = '[data-guide-event-card=first]'
QUICK = '.container.my-4 > .d-flex.flex-wrap'


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
    card = capture.page.locator('.card.shadow-sm:has(a[href*="/date/update/"])').first
    card.wait_for()
    card.evaluate(
        "e => e.dataset.guideEventCard = 'first'")


def event_link(capture, action):
    return capture.page.locator(CARD).locator(f'a[href*="{action}"]').get_attribute('href')


def verify_guide(capture, images=None):
    """共通の検証（capture.verify_guide）に任せる。"""
    capture.verify_guide()


@scenario('event/manage', mobile=True)
def manage(capture):
    menu(capture)
    management(capture)
    for number, href in enumerate((
        '/event/calendar/create/', '/account/lt-applications/', '/analytics/dashboard/',
        '/event/detail/history/', '/community/settings/', '/community/',
    ), 1):
        # 公開ページのリンクは ID を固定しない。
        selector = f'{QUICK} a[href^="{href}"]'
        if href == '/community/':
            selector += ':not([href="/community/settings/"])'
        capture.annotate(selector, number)
    capture.annotate(f'{CARD} a[title="開始時刻を編集"]', 7)
    capture.annotate(f'{CARD} a[href*="/date/update/"]', 8)
    capture.annotate(f'{CARD} a[href^="/twitter/"] >> nth=0', 9)
    capture.annotate(f'{CARD} form[action^="/event/delete/"]', 10)
    # 並び順ではなく、承認済み・未紐づけと承認待ちの操作で行を特定する。
    capture.annotate(
        f'{CARD} .col-md-7 > .mb-2:has(> a[href$="#speaker-account-heading"])', 11,
    )
    capture.annotate(
        f'{CARD} .col-md-7 > .mb-2:has(> button[data-bs-target^="#approveModal"])', 12,
    )
    capture.annotate(f'{CARD} a[href$="/detail/create/"]', 13)
    region(capture, f'{QUICK} a >> nth=0', f'{QUICK} a >> nth=5', CARD)
    capture.annotations.clear()
    capture.annotate('#more-future-events-toggle', 1)
    capture.shot(selector='#more-future-events-toggle')
    verify_guide(capture, 6)


@scenario('event/create', mobile=True)
def create(capture):
    menu(capture)
    management(capture)
    capture.annotate(f'{QUICK} a[href="/event/calendar/create/"]', 1)
    if capture.mobile:
        capture.shot(selector=QUICK)
    else:
        region(capture, f'{QUICK} a >> nth=0', f'{QUICK} a >> nth=5')
    capture.goto('/event/calendar/create/')
    capture.page.locator('#id_start_date').fill('2030-04-15')
    capture.page.locator('#id_start_time').fill('22:00')
    capture.page.locator('#id_duration').fill('60')
    for number, field in enumerate(('start_date', 'start_time', 'duration'), 1):
        capture.annotate(f'#id_{field}', number)
    region(capture, '#id_start_date >> xpath=..', '#id_duration >> xpath=..')
    capture.annotations.clear()
    capture.page.locator('input[name=recurrence_type][value=weekly]').check()
    capture.page.locator('#id_weekday').select_option('MO')
    capture.annotate('input[name=recurrence_type][value=weekly] >> xpath=ancestor::div[contains(@class,"form-check")][1]', 1)
    capture.annotate('#id_weekday', 2)
    region(capture, '.recurrence-options', '#weekday-field')
    capture.annotations.clear()
    capture.annotate('.card form button[type=submit]', 1)
    region(capture, '.card form .mt-4', top=20)
    verify_guide(capture, 10)


@scenario('event/edit', mobile=True)
def edit(capture):
    capture.login('owner')
    management(capture)
    date_url = event_link(capture, '/date/update/')
    time_url = capture.page.locator(f'{CARD} a[title="開始時刻を編集"]').get_attribute('href')
    capture.annotate(f'{CARD} a[href*="/date/update/"]', 1)
    region(capture, f'{CARD} .col-md-5 > .card-text >> nth=0',
           f'{CARD} a[href*="/date/update/"]', top=18, bottom=2)
    capture.goto(date_url)
    capture.page.locator('#id_date').fill('2030-04-09')
    capture.annotate('#id_date', 1)
    capture.annotate('.card form button[type=submit]', 2)
    capture.shot(selector='.col-12.col-md-7.col-lg-5')
    management(capture)
    capture.annotate(f'{CARD} a[title="開始時刻を編集"]', 1)
    region(capture, f'{CARD} .col-md-5 > .card-text >> nth=0',
           f'{CARD} a[title="開始時刻を編集"]', top=18)
    capture.goto(time_url)
    capture.page.locator('#id_start_time').fill('22:30')
    capture.annotate('#id_start_time', 1)
    capture.annotate('.card form button[type=submit]', 2)
    capture.shot(selector='.card')
    verify_guide(capture, 8)


@scenario('event/delete', mobile=True)
def delete(capture):
    capture.login('owner')
    management(capture)
    # 親の削除による連鎖と区別できるよう、子開催回のカードを使う。
    # 先頭の .card.shadow-sm は Vket の案内や親（4月8日）なので、開催日で子開催回のカードに絞る。
    capture.page.locator('.card.shadow-sm:has(form[action^="/event/delete/"])',
                         has_text='2030年4月15日').first.evaluate(
        "e => e.dataset.guideDeleteCard = 'selected'")
    card = '[data-guide-delete-card=selected]'
    capture.annotate(f'{card} .card-text >> nth=0', 1)
    region(capture, f'{card} .col-md-5 > .card-text >> nth=0',
           f'{card} a[title="開始時刻を編集"]', top=18)
    capture.annotations.clear()
    capture.annotate(f'{card} .form-check', 1)
    capture.annotate(f'{card} form[action^="/event/delete/"] button', 2)
    region(capture, f'{card} .col-md-5 > .card-text >> nth=0',
           f'{card} form[action^="/event/delete/"]', top=18)
    # ブラウザの確認文を実装から取得し、キャンセルして外部処理を起動しない。
    messages = []

    def dismiss(dialog):
        messages.append(dialog.message)
        dialog.dismiss()

    capture.page.once('dialog', dismiss)
    capture.page.locator(f'{card} form[action^="/event/delete/"] button').click()
    if len(messages) != 1:
        raise AssertionError('削除確認が表示されませんでした')
    capture.mock('event-delete-confirm.html')
    capture.page.locator('#message').evaluate('(e, message) => e.textContent = message', messages[0])
    capture.annotate('#confirm', 1)
    capture.annotate('#cancel', 2)
    capture.shot(selector='main')
    verify_guide(capture, 6)


@scenario('event/auto-post', mobile=True)
def auto_post(capture):
    capture.login('owner')
    capture.goto('/community/update/')
    capture.page.locator('#id_twitter_hashtag').fill('#さくら技術交流会')
    capture.annotate('#id_twitter_hashtag', 1)
    region(capture, '#id_twitter_hashtag >> xpath=..', top=4)
    capture.goto('/twitter/queue/?status=ready')
    capture.annotate('tbody tr', 1)
    region(capture, '.container.my-5 > .mb-3', '.table-responsive', top=4)
    onclick = capture.page.locator('tbody tr').get_attribute('onclick')
    capture.goto(onclick.split("'")[1])
    capture.annotate('.card-body', 1)
    capture.annotate('#id_generated_text', 2)
    region(capture, '.card', '#id_generated_text >> xpath=..')
    capture.mock('event-x-post.html')
    capture.annotate('#post-text', 1)
    capture.shot(selector='main')
    verify_guide(capture, 8)
