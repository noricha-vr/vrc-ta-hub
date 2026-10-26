"""発表申し込み、本人の資料更新、VRChat での表示準備。"""
from capture import scenario


def region(capture, selectors, *, marks=None):
    """隣り合う項目だけを切り出し、PC の余白とスマホの縦長画像を避ける。"""
    capture.annotations.clear()
    page = capture.page
    page.locator(selectors[0]).scroll_into_view_if_needed()
    boxes = [page.locator(selector).bounding_box() for selector in selectors]
    scroll = page.evaluate('({x: scrollX, y: scrollY})')
    left = max(0, min(box['x'] for box in boxes) + scroll['x'] - 18)
    top = max(0, min(box['y'] for box in boxes) + scroll['y'] - 16)
    right = min(page.viewport_size['width'], max(box['x'] + box['width'] for box in boxes) + 18)
    bottom = max(box['y'] + box['height'] for box in boxes) + scroll['y'] + 12
    for number, selector in enumerate(marks or selectors, 1):
        capture.annotate(selector, number)
    capture.shot(clip={'x': left, 'y': top, 'width': right - left, 'height': bottom - top})


APPLICATION_CARD = 'main article.admin-card'
APPROVED_CARD = f'{APPLICATION_CARD}:has(.bg-success-subtle)'


def field(name):
    return f'.field-wrapper[data-field="{name}"]'


def edit_url(capture):
    capture.goto('/account/lt-applications/')
    return capture.page.locator(f'{APPROVED_CARD} a:has-text("編集")').first.get_attribute('href')


@scenario('speaker/apply', mobile=True)
def apply(capture):
    capture.login('speaker')
    capture.goto('/community/1/')
    region(capture, ['section:has(a[href="/event/apply/1/"])'],
           marks=['a[href="/event/apply/1/"]'])
    capture.goto('/event/apply/1/')
    page = capture.page
    # 既存フォームの実在ユーザー名を含む placeholder は架空の入力値に置き換える。
    page.locator('#id_x_account').fill('guide_aoi')
    page.locator('#id_x_account').evaluate("e => e.placeholder = 'guide_aoi'")
    page.locator('#id_theme').fill('はじめてのワールド制作')
    region(capture, ['form .mb-4:has(#id_event)', 'form .mb-4:has(#id_theme)',
                     'form .mb-4:has(#id_speaker)', 'form .mb-4:has(#id_x_account)'],
           marks=['#id_event', '#id_theme', '#id_speaker'])
    region(capture, ['form .mb-4:has([aria-label="撮影"])', 'form .mb-4:has([aria-label="発表の記事化"])'])
    page.locator('#id_additional_info').fill('【発表概要】\nワールドを作る手順を紹介します。\n【希望する時間】\n質疑応答を含めて30分です。')
    region(capture, ['#application-form .admin-card:has(#id_additional_info)', '[data-admin-action-bar]'],
           marks=['#id_additional_info', 'button[form="application-form"]'])
    # 保存・通知は実行せず、seed 済みの承認待ち状態と実際の完了テンプレートを撮る。
    capture.goto('/event/apply/1/complete/')
    region(capture, ['main h1', 'main .alert-success', 'a[href="/account/lt-applications/"]'],
           marks=['a[href="/account/lt-applications/"]'])


@scenario('speaker/manage', mobile=True)
def manage(capture):
    capture.login('speaker')
    capture.goto('/event/apply/1/complete/')
    region(capture, ['a[href="/account/lt-applications/"]'],
           marks=['a[href="/account/lt-applications/"]'])
    capture.goto('/account/lt-applications/')
    region(capture, [f'{APPLICATION_CARD} >> nth=0', f'{APPLICATION_CARD} >> nth=-1'],
           marks=[f'{APPLICATION_CARD} .bg-success-subtle >> nth=0', f'{APPLICATION_CARD} .bg-warning-subtle >> nth=0',
                  f'{APPROVED_CARD} a:has-text("編集") >> nth=0'])
    url = edit_url(capture)
    capture.goto(url)
    region(capture, [field('theme'), field('speaker')], marks=['#id_theme', '#id_speaker'])
    region(capture, [field('h1'), field('contents')], marks=['#id_h1', '#id_contents'])
    region(capture, [field('slide_file'), field('slide_url'), field('youtube_url')],
           marks=['#id_slide_file', '#id_slide_url', '#id_youtube_url'])
    region(capture, [field('recording_policy'), field('article_consent')])
    region(capture, ['#application-form [data-admin-action-bar]'],
           marks=['#application-form button[type=submit]'])


@scenario('speaker/slide-video', mobile=True)
def slide_video(capture):
    capture.login('speaker')
    capture.mock('speaker-pdf-export.html')
    capture.annotate('#format', 1)
    capture.annotate('#export', 2)
    capture.shot(selector='.diagram')
    capture.goto(edit_url(capture))
    region(capture, [field('slide_file')], marks=['#id_slide_file'])
    region(capture, ['#application-form [data-admin-action-bar]'],
           marks=['#application-form button[type=submit]'])
    for name, selectors in [
        ('speaker-webscreen-upload.html', ['#login', '#upload']),
        ('speaker-webscreen-result.html', ['#video-url', '#copy']),
        ('speaker-vrchat-player.html', ['#url', '#load', '#seek']),
    ]:
        capture.mock(name)
        for number, selector in enumerate(selectors, 1):
            capture.annotate(selector, number)
        capture.shot(selector='.diagram')
