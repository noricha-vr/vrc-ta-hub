"""発表管理。保存・承認・削除・生成・外部送信は行わず、各状態を撮る。"""
from capture import scenario


FORM = 'form[enctype="multipart/form-data"]'


def link_state_pk(linked):
    """紐づけの例に使う発表の ID。ほかのセクションのデモ投入で番号がずれるので、テーマと紐づけの有無で引く。"""
    import os
    from unittest.mock import patch

    import django
    django.setup()
    from event.models import EventDetail
    # Playwright の同期 API がイベントループを持つため、読み取りだけ許可する（一時 SQLite のみ）。
    with patch.dict(os.environ, DJANGO_ALLOW_ASYNC_UNSAFE='true'):
        return EventDetail.objects.get(theme='小さな実験を始める', applicant__isnull=not linked).pk


def region(capture, *selectors):
    """関連する項目だけを、番号の余白を含めて切り出す。"""
    boxes = []
    for selector in selectors:
        locator = capture.page.locator(selector)
        locator.scroll_into_view_if_needed()
        box = locator.bounding_box()
        scroll = capture.page.evaluate('({x: scrollX, y: scrollY})')
        boxes.append({**box, 'x': box['x'] + scroll['x'], 'y': box['y'] + scroll['y']})
    x = max(0, min(b['x'] for b in boxes) - 16)
    y = max(0, min(b['y'] for b in boxes) - 16)
    right = min(capture.page.viewport_size['width'], max(b['x'] + b['width'] for b in boxes) + 20)
    bottom = max(b['y'] + b['height'] for b in boxes) + 12
    capture.shot(clip={'x': x, 'y': y, 'width': right - x, 'height': bottom - y})
    capture.annotations.clear()


def entry(capture, pk, *, with_menu=False):
    row = f'#presentation-{pk}'
    selectors = [row]
    if with_menu:
        selectors.append(f'{row} .dropdown-menu.show')
    region(capture, *selectors)


def open_menu(capture, pk):
    row = f'#presentation-{pk}'
    capture.page.locator(f'{row} button[data-bs-toggle="dropdown"]').click()
    capture.page.locator(f'{row} .dropdown-menu.show').wait_for()


def field(name):
    return f'[data-field="{name}"]'


def manage(capture):
    capture.login('owner')
    capture.goto('/event/my_list/?community=1')


def menu(capture, destination='community'):
    capture.goto('/account/settings/')
    if capture.mobile:
        capture.page.locator('.navbar-toggler').click()
    capture.page.locator('[aria-label="アカウントメニュー"]').click()
    target = '.account-menu a[href*="community=1"]' if destination == 'community' else '.account-menu-identity'
    capture.annotate(target, 1)
    capture.shot(selector='.account-menu')


def expand(capture):
    button = capture.page.locator('#detail-settings-toggle button')
    if button.is_visible():
        button.click()


@scenario('lt/create', mobile=True)
def create(capture):
    capture.login('owner')
    menu(capture)
    capture.goto('/event/my_list/?community=1')
    capture.annotate('a[href="/event/1/detail/create/"]', 1)
    region(capture, 'a[href="/event/1/detail/create/"]')
    capture.goto('/event/1/detail/create/')
    capture.page.locator('#id_theme').fill('身近な道具で実験する')
    capture.page.locator('#id_speaker').fill('アオイ')
    capture.annotate('.form-check:has(#id_detail_type_0)', 1)
    capture.annotate('#id_theme', 2)
    capture.annotate('#id_speaker', 3)
    capture.annotate('[aria-label="撮影"]', 4)
    region(capture, f'{FORM} .admin-card:has(#id_theme)')
    capture.annotate('#id_start_time', 1)
    capture.annotate('#id_duration', 2)
    region(capture, f'{FORM} .admin-card:has(#id_start_time)')
    capture.annotate('#detail-settings-toggle button', 1)
    region(capture, '#detail-settings-toggle')
    expand(capture)
    capture.annotate('#id_slide_file', 1)
    capture.annotate('#id_slide_url', 2)
    capture.annotate('#id_thumbnail_image', 3)
    region(capture, field('slide_file'), field('slide_url'), field('thumbnail_image'))
    capture.page.locator('#id_detail_type_1').check()
    capture.page.locator('#id_h1').fill('みんなで試す実験会')
    capture.annotate('.form-check:has(#id_detail_type_1)', 1)
    capture.annotate('#id_start_time', 2)
    capture.annotate('#id_h1', 3)
    region(capture, f'{FORM} .admin-card:has(#id_detail_type_1)', field('start_time'), field('h1'))
    capture.page.locator('#id_detail_type_2').check()
    capture.page.locator('#id_h1').fill('実験会の振り返り')
    capture.annotate('.form-check:has(#id_detail_type_2)', 1)
    capture.annotate('#id_h1', 2)
    capture.annotate('#id_contents', 3)
    region(capture, f'{FORM} .admin-card:has(#id_detail_type_2)', field('h1'), field('contents'))
    capture.annotate(f'{FORM} button[type=submit]', 1)
    region(capture, f'{FORM} button[type=submit]')


@scenario('lt/edit-delete', mobile=True)
def edit_delete(capture):
    manage(capture)
    open_menu(capture, 1)
    capture.annotate('#presentation-1 .presentation-actions > a[href="/event/detail/1/update/"]', 1)
    capture.annotate('#presentation-1 button[data-bs-toggle="dropdown"]', 2)
    capture.annotate('#presentation-1 .dropdown-menu.show [data-confirm-panel="detail-delete-1"]', 3)
    entry(capture, 1, with_menu=True)
    capture.goto('/event/detail/1/update/')
    expand(capture)
    capture.annotate('#id_slide_file', 1)
    capture.annotate('#id_thumbnail_image', 2)
    region(capture, field('slide_file'), field('slide_url'), field('thumbnail_image'))
    capture.annotate('#id_h1', 1)
    capture.annotate('#id_contents', 2)
    capture.annotate(f'{FORM} button[type=submit]', 3)
    region(capture, field('h1'), field('contents'), f'{FORM} button[type=submit]')
    capture.goto('/event/my_list/?community=1')
    open_menu(capture, 1)
    capture.page.locator('#presentation-1 [data-confirm-panel="detail-delete-1"]').click()
    panel = '#detail-delete-1'
    capture.page.locator(panel).wait_for(state='visible')
    capture.annotate(f'{panel} button[type=submit]', 1)
    capture.annotate(f'{panel} [data-cancel-panel]', 2)
    capture.shot(selector=panel)


@scenario('lt/approve', mobile=True)
def approve(capture):
    capture.mock('lt-application-email.html')
    capture.annotate('#review', 1)
    capture.shot(selector='main')
    capture.mock('lt-application-discord.html')
    capture.annotate('#review', 1)
    capture.shot(selector='main')
    manage(capture)
    capture.annotate('a[href="/event/application/2/review/"]', 1)
    entry(capture, 2)
    capture.goto('/event/application/2/review/')
    capture.annotate('.application-information', 1)
    region(capture, '.application-information', '.application-information + .admin-card')
    capture.annotations.clear()
    capture.annotate('#id_event', 1)
    capture.annotate('#id_start_time', 2)
    capture.annotate('#id_duration', 3)
    region(capture, '#review-form .row')
    capture.annotate('button[value=approve]', 1)
    capture.annotate('#reject-button', 2)
    region(capture, 'button[value=approve]', '#reject-button')
    capture.page.locator('#reject-button').click()
    capture.page.locator('#id_rejection_reason').fill('今回は発表枠が埋まっています。次回の開催をご相談ください。')
    capture.annotate('#id_rejection_reason', 1)
    capture.annotate('#reject-button', 2)
    region(capture, '#rejection-reason-field', '#reject-button')


@scenario('lt/auto-generate', mobile=True)
def auto_generate(capture):
    manage(capture)
    capture.annotate('a[href="/event/detail/1/"]', 1)
    entry(capture, 1)
    capture.goto('/event/detail/1/update/')
    expand(capture)
    capture.annotate('#id_slide_file', 1)
    capture.annotate('#id_youtube_url', 2)
    region(capture, field('slide_file'), field('slide_url'), field('thumbnail_image'), field('youtube_url'))
    capture.annotate('#article-consent-status', 1)
    capture.shot(selector='#article-consent-status')
    capture.annotations.clear()
    capture.annotate(f'{FORM} button[type=submit]', 1)
    region(capture, f'{FORM} button[type=submit]')
    capture.goto('/event/detail/1/')
    capture.annotate('#generate-button', 1)
    capture.annotate('a[href="/event/detail/1/update/"]', 2)
    region(capture, '#admin-actions')
    article = 'div:has(> h2:text-is("小さく作って試す"))'
    capture.annotate(article, 1)
    capture.shot(selector=article)


@scenario('lt/link-speaker', mobile=True)
def link_speaker(capture):
    unlinked, linked = link_state_pk(False), link_state_pk(True)
    manage(capture)
    open_menu(capture, unlinked)
    capture.annotate(f'#presentation-{unlinked} button[data-bs-toggle="dropdown"]', 1)
    capture.annotate(f'#presentation-{unlinked} .dropdown-menu.show a[href$="#speaker-account-heading"]', 2)
    entry(capture, unlinked, with_menu=True)
    capture.goto(f'/event/detail/{unlinked}/')
    capture.annotate('#speaker-invite-issue-button', 1)
    capture.shot(selector='.speaker-account-card')
    capture.annotations.clear()
    capture.page.locator('#speaker-invite-issue-button').click()
    capture.page.locator('#speaker-invite-result').wait_for(state='visible')
    invite = capture.page.locator('#speaker-invite-url').input_value()
    capture.mask('#speaker-invite-url')
    capture.annotate('#speaker-invite-copy-button', 1)
    capture.shot(selector='.speaker-account-card')
    capture.mock('lt-speaker-message.html')
    capture.annotate('#message', 1)
    capture.shot(selector='main')
    capture.login('speaker')
    capture.goto(invite.removeprefix(capture.base_url))
    capture.page.locator('#speaker-link-confirm-button').wait_for(state='visible')
    capture.annotate('#speaker-link-confirm-button', 1)
    capture.shot(selector='.speaker-link .admin-card')
    # 完了後の例は seed の紐づけ済み発表。確定POSTや通知は実行しない。
    menu(capture, 'account')
    capture.goto('/account/settings/')
    capture.annotate('a[href="/event/detail/history/?mine=1"]', 1)
    region(capture, 'a[href="/event/detail/history/?mine=1"]')
    capture.login('owner')
    capture.goto(f'/event/detail/{linked}/')
    capture.annotate('[data-bs-target="#speaker-unlink-modal"]', 1)
    capture.shot(selector='.speaker-account-card')
    capture.annotations.clear()
    capture.page.locator('[data-bs-target="#speaker-unlink-modal"]').click()
    capture.page.locator('#speaker-unlink-modal').wait_for(state='visible')
    capture.page.locator('#speaker-unlink-modal').evaluate(
        'async element => { await Promise.all(element.getAnimations({subtree: true}).map(a => a.finished)); }'
    )
    capture.annotate('#speaker-unlink-modal button[type=submit]', 1)
    # 確認画面だけを切り出し、背後のページのスクロール位置を画像に含めない。
    dialog = capture.page.locator('#speaker-unlink-modal .modal-content').bounding_box()
    scroll = capture.page.evaluate('({x: scrollX, y: scrollY})')
    capture.shot(clip={**dialog, 'x': dialog['x'] + scroll['x'], 'y': dialog['y'] + scroll['y']})
    capture.login('speaker')
    capture.goto('/event/detail/history/?mine=1')
    capture.annotate(f'a[href="/event/detail/{linked}/update/"]', 1)
    capture.shot(selector=f'article:has(a[href="/event/detail/{linked}/update/"])')
