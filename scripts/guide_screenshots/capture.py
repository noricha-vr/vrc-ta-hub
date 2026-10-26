"""ページ別シナリオが使う撮影 API と個人情報ガード。"""
import importlib
import io
import json
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image

from guide.demo import DEMO_USERS, demo_password

HERE = Path(__file__).resolve().parent
OUTPUT = HERE.parents[1] / 'app/guide/static/guide/images'
SCENARIOS: dict[str, Callable[['Capture'], None]] = {}
# スマホ幅（390px）でも撮るページ。フォームやメニューなど操作の多い画面だけ指定する。
MOBILE_PATHS: set[str] = set()


def scenario(path, *, mobile=False):
    """公開ガイドの path ごとに一つの関数を登録する。mobile=True ならスマホ幅でも同じ手順で撮る。"""
    def register(function):
        if path in SCENARIOS:
            raise ValueError(f'重複シナリオ: {path}')
        SCENARIOS[path] = function
        if mobile:
            MOBILE_PATHS.add(path)
        return function
    return register


def assert_safe(text):
    """値を例外に含めず、検知した種類だけ報告する。伏せ字は先に適用する。"""
    for email in re.findall(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', text):
        if email.rsplit('@', 1)[1].lower() != 'example.com':
            raise ValueError('撮影ガード: 許可されていないメールアドレス')
    if re.search(r'(?<!\d)\d{17,19}(?!\d)', text):
        raise ValueError('撮影ガード: Discord ID に似た数字')
    for webhook in re.findall(r'(?:https?://)?(?:\w+\.)?discord(?:app)?\.com/api/webhooks/[^\s<>"\']+', text):
        if not re.fullmatch(r'https://discord\.com/api/webhooks/0+/DUMMY', webhook):
            raise ValueError('撮影ガード: ダミー以外の Webhook URL')
    patterns = [r'\bsk-[\w-]+', r'\b(?:gh[pousr]_|github_pat_)[\w]+', r'\bAIza[\w-]+',
                r'\bAKIA[A-Z0-9]{16}\b', r'-----BEGIN .*PRIVATE KEY-----',
                r'\b(?:xox[baprs]-|gsk_)[\w-]+', r'\beyJ[\w-]+\.[\w-]+\.[\w-]+']
    if any(re.search(pattern, text) for pattern in patterns):
        raise ValueError('撮影ガード: 鍵・トークン形式の文字列')


class Capture:
    def __init__(self, page, base_url, path, mobile=False):
        self.page = page
        self.base_url = base_url
        self.path = path
        self.mobile = mobile
        self.annotations = []
        self.masks = []
        self.count = 0

    def goto(self, path):
        self.annotations.clear()
        self.masks.clear()
        response = self.page.goto(self.base_url + path, wait_until='networkidle')
        if not response or response.status >= 400:
            raise RuntimeError('撮影先ページの表示に失敗しました')
        self.page.evaluate('document.fonts.ready')
        if not self.page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--bs-blue').trim()"):
            raise RuntimeError('画面の CSS を読み込めませんでした。CDN への接続を確認してください')

    def verify_guide(self):
        """撮った画像を載せたガイドを同じ幅で開き、全画像の読み込み・スマホ版への差し替え・横のはみ出しを確かめる。"""
        self.goto(f'/guide/{self.path}/')
        images = self.page.locator('.guide-content img.guide-screenshot')
        if not images.count():
            raise RuntimeError(f'{self.path}: ガイドに画像がありません')
        images.evaluate_all("elements => elements.forEach(e => e.loading = 'eager')")
        self.page.wait_for_function("""() => [...document.querySelectorAll('.guide-content img.guide-screenshot')]
            .every(e => e.complete && e.naturalWidth > 0)""")
        if self.mobile:
            # -sp 版がある画像は、スマホ幅ではそちらが表示される
            wrong = self.page.evaluate("""() => [...document.querySelectorAll('.guide-content picture')]
                .filter(p => !p.querySelector('img').currentSrc.includes('-sp.webp')).length""")
            if wrong:
                raise RuntimeError(f'{self.path}: スマホ幅で -sp 版に差し替わらない画像があります')
        if self.page.evaluate('document.documentElement.scrollWidth > innerWidth'):
            raise RuntimeError(f'{self.path}: ガイドに横方向のはみ出しがあります')
        print(json.dumps({'event': 'guide_page_verified', 'path': self.path, 'mobile': self.mobile,
                          'images': images.count()}))

    def login(self, role='owner'):
        self.page.context.clear_cookies()
        self.goto('/account/login/')
        username = self.page.locator('input[name=username]')
        username.fill(DEMO_USERS[role][1])
        form = username.locator('xpath=ancestor::form')
        form.locator('input[name=password]').fill(demo_password())
        form.locator('button[type=submit]').click()
        self.page.wait_for_load_state('networkidle')
        if '/account/login/' in self.page.url:
            raise RuntimeError('デモユーザーのログインに失敗しました')

    def mock(self, name):
        file = (HERE / 'mocks' / name).resolve()
        if not file.is_relative_to((HERE / 'mocks').resolve()) or file.suffix != '.html':
            raise ValueError('モックは mocks/ 内の HTML を指定してください')
        self.annotations.clear()
        self.masks.clear()
        self.page.goto(file.as_uri(), wait_until='load')

    def annotate(self, selector, number=1):
        if not 1 <= number <= 20:
            raise ValueError('注釈番号は 1〜20 です')
        self.annotations.append((selector, number))

    def mask(self, selector):
        self.masks.append(selector)

    def _prepare(self):
        self.page.locator('[data-guide-overlay]').evaluate_all('(elements) => elements.forEach(e => e.remove())')
        for selector in self.masks:
            self.page.locator(selector).evaluate_all('''elements => elements.forEach(e => {
                if ('value' in e) {e.value = ''; e.placeholder = '';} 
                else {
                    // 伏せ字で要素が縮み、表や注釈の位置が変わらないようにする。
                    const rect = e.getBoundingClientRect();
                    if (getComputedStyle(e).display === 'inline') e.style.display = 'inline-block';
                    e.style.minWidth = `${rect.width}px`; e.style.minHeight = `${rect.height}px`;
                    e.textContent = '非表示';
                }
                e.style.backgroundColor = '#9ca3af'; e.style.color = 'transparent';
                e.style.textShadow = 'none';
            })''')
        # 表示テキストに加え、innerText に出ない input/select/textarea の値と placeholder も検査する。
        text = self.page.locator('body').inner_text()
        values = self.page.locator('input:not([type=hidden]), textarea, select').evaluate_all(r'''elements =>
            elements.map(e => [e.value, e.placeholder || ''].join('\n')).join('\n')''')
        if self.page.locator('input[type=password]').evaluate_all('elements => elements.some(e => e.value)'):
            raise ValueError('撮影ガード: パスワード欄に値が残っています')
        assert_safe(text + '\n' + values)
        for selector, number in self.annotations:
            target = self.page.locator(selector)
            if target.count() != 1 or not target.is_visible():
                raise ValueError(f'注釈対象は表示中の一要素にしてください: {selector}（{target.count()} 件）')
            target.evaluate('''(element, number) => {
                const r = element.getBoundingClientRect();
                const frame = document.createElement('div');
                frame.dataset.guideOverlay = '1';
                Object.assign(frame.style, {position:'absolute', pointerEvents:'none', zIndex:'2147483647',
                    left:`${r.left + scrollX}px`, top:`${r.top + scrollY}px`, width:`${r.width}px`,
                    height:`${r.height}px`, border:'3px solid #dc2626', borderRadius:'5px', boxSizing:'border-box'});
                const label = document.createElement('span');
                label.textContent = String(number);
                // 項目名は左寄せが多いので、番号は枠の右上の角に重ねて文字を隠さないようにする。
                Object.assign(label.style, {position:'absolute', top:'-13px', right:'-13px', width:'26px',
                    height:'26px', color:'white', background:'#dc2626', borderRadius:'50%',
                    border:'2px solid white', boxShadow:'0 1px 3px rgba(0,0,0,.35)',
                    font:'bold 15px/22px sans-serif', textAlign:'center'});
                frame.append(label); document.body.append(frame);
            }''', number)

    def shot(self, *, selector=None, clip=None):
        """全体・要素・矩形のいずれかを撮影。枠の上側に番号分の余白を付ける。"""
        if selector and clip:
            raise ValueError('selector と clip は同時に指定できません')
        if selector:
            self.page.locator(selector).scroll_into_view_if_needed()
        self.page.evaluate('document.fonts.ready')
        self._prepare()
        if selector:
            box = self.page.locator(selector).bounding_box()
            if not box:
                raise ValueError('切り出し対象がありません')
            scroll = self.page.evaluate('({x: scrollX, y: scrollY})')
            top = max(0, box['y'] + scroll['y'] - 32)
            clip = {'x': max(0, box['x'] + scroll['x'] - 16), 'y': top,
                    'width': min(box['width'] + 32, self.page.viewport_size['width'] - max(0, box['x'] - 16)),
                    'height': box['y'] + scroll['y'] + box['height'] + 6 - top}
        # clip はページ先頭からの座標なので、範囲指定の時も full_page で撮る（画面内の座標にしない）。
        png = self.page.screenshot(type='png', full_page=True, clip=clip, animations='disabled')
        self.count += 1
        section, name = self.path.rsplit('/', 1)
        suffix = '-sp' if self.mobile else ''
        file = OUTPUT / section / f'{name}-{self.count:02d}{suffix}.webp'
        file.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(io.BytesIO(png)) as image:
            image.convert('RGB').save(file, 'WEBP', quality=82, method=6)
        print(json.dumps({'event': 'guide_screenshot', 'path': str(file.relative_to(OUTPUT)),
                          'width': image.width, 'height': image.height}, ensure_ascii=False))


def main():
    from playwright.sync_api import sync_playwright

    # CLI 実行中も scenarios から同じレジストリを参照させる。
    sys.modules['capture'] = sys.modules[__name__]
    for file in sorted((HERE / 'scenarios').glob('*.py')):
        if not file.name.startswith('_'):
            importlib.import_module(f'scenarios.{file.stem}')
    requested = sys.argv[2:] or sorted(SCENARIOS)
    unknown = set(requested) - SCENARIOS.keys()
    if unknown:
        raise ValueError(f'未登録のページ: {sorted(unknown)}')
    started = time.monotonic()
    count = 0
    with sync_playwright() as playwright:
        # ファイル選択ボタンなどブラウザ自身の表記も日本語にする。
        browser = playwright.chromium.launch(args=['--lang=ja-JP'])
        try:
            for path in requested:
                widths = ((False, 1280), (True, 390)) if path in MOBILE_PATHS else ((False, 1280),)
                for mobile, width in widths:
                    context = browser.new_context(viewport={'width': width, 'height': 900},
                                                  locale='ja-JP', timezone_id='Asia/Tokyo', reduced_motion='reduce')
                    try:
                        # 計測・外部APIは送らず、既存 UI の CSS とフォントだけ読み込む。
                        def route_request(route):
                            url = urlsplit(route.request.url)
                            if url.hostname == '127.0.0.1' or url.scheme == 'file':
                                route.continue_()
                            elif url.hostname in {'cdn.jsdelivr.net', 'cdnjs.cloudflare.com', 'fonts.googleapis.com', 'fonts.gstatic.com'} and route.request.resource_type in {'stylesheet', 'font', 'script'}:
                                route.continue_()
                            else:
                                route.abort()
                        context.route('**/*', route_request)
                        capture = Capture(context.new_page(), sys.argv[1], path, mobile)
                        SCENARIOS[path](capture)
                        count += capture.count
                    finally:
                        context.close()
        finally:
            browser.close()
    print(json.dumps({'event': 'guide_capture_complete', 'images': count, 'seconds': round(time.monotonic() - started, 1)}))


if __name__ == '__main__':
    main()
