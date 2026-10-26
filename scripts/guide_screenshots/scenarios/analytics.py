"""アクセス解析と配布リンクの実画面。保存や外部の計測通信は行わない。"""
from capture import scenario


def shot(capture, region, *marks):
    """手順ごとに注釈を入れ替え、操作に関係する要素だけを切り出す。"""
    capture.page.mouse.move(0, 0)
    # グラフの JavaScript アニメーションは screenshot(animations='disabled') の対象外。
    capture.page.evaluate("() => Object.values(window.Chart?.instances || {}).forEach(chart => {chart.stop(); chart.update('none');})")
    capture.page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    capture.annotations.clear()
    for number, selector in enumerate(marks, 1):
        capture.annotate(selector, number)
    if isinstance(region, tuple):
        boxes = []
        for selector in region:
            target = capture.page.locator(selector)
            target.scroll_into_view_if_needed()
            box = target.bounding_box()
            scroll = capture.page.evaluate('({x: scrollX, y: scrollY})')
            boxes.append({**box, 'x': box['x'] + scroll['x'], 'y': box['y'] + scroll['y']})
        x = max(0, min(box['x'] for box in boxes) - 16)
        y = max(0, min(box['y'] for box in boxes) - 32)
        right = max(box['x'] + box['width'] for box in boxes) + 16
        bottom = max(box['y'] + box['height'] for box in boxes) + 8
        capture.shot(clip={'x': x, 'y': y, 'width': min(right, capture.page.viewport_size['width']) - x, 'height': bottom - y})
    else:
        capture.shot(selector=region)


def table_row(capture, row, *marks):
    """名前から操作までを一枚に収める。スマホは横スクロールの全範囲を示す。"""
    capture.annotations.clear()
    container = capture.page.locator('.table-responsive')
    original_style = container.get_attribute('style')
    container.evaluate("e => { e.scrollLeft = 0; e.style.overflow = 'visible'; }")
    try:
        target = capture.page.locator(row)
        target.scroll_into_view_if_needed()
        box = target.evaluate("e => { const r = e.getBoundingClientRect(); return {x: r.x + scrollX, y: r.y + scrollY, width: r.width, height: r.height}; }")
        for number, mark in enumerate(marks, 1):
            capture.annotate(mark, number)
        capture.shot(clip={'x': max(0, box['x'] - 16), 'y': max(0, box['y'] - 32),
                           'width': box['width'] + 32, 'height': box['height'] + 48})
    finally:
        container.evaluate("(e, style) => { if (style === null) e.removeAttribute('style'); else e.setAttribute('style', style); }", original_style)


def verify_guide(capture):
    """共通の検証（capture.verify_guide）に任せる。"""
    capture.verify_guide()


@scenario('analytics/dashboard', mobile=True)
def dashboard(c):
    c.login('owner')
    c.goto('/event/my_list/')
    shot(c, 'a:has-text("アクセス") >> xpath=..', 'a[href^="/analytics/dashboard/"]')
    c.goto('/account/settings/')
    shot(c, '.list-group:has(a[href="/analytics/dashboard/"])', 'a[href="/analytics/dashboard/"]')
    c.goto('/analytics/dashboard/')
    shot(c, 'form.card', '#community-select', '[aria-label="集計期間"]')
    shot(c, '.card:has(.analytics-kpis)', '.analytics-kpis')
    detail_url = c.page.locator('a:text-is("小さなワールドの作り方")').get_attribute('href')
    shot(c, '.card:has(h2:has-text("ページ別ランキング"))', 'th a:has-text("閲覧")', 'th a:has-text("動画再生")')
    shot(c, 'main .col-lg-4', '.card:has(#weekly-chart) h2', '.analytics-source-legend', 'a[href^="/analytics/dashboard/search-queries/"]')
    shot(c, '.card:has(#campaign-daily-chart)', '.card:has(#campaign-daily-chart) table')
    shot(c, 'form.card', 'a[aria-label="CSV をダウンロード"]')
    c.goto('/analytics/dashboard/search-queries/')
    shot(c, '.card:has(#query-filter)', '#query-filter', 'button:has-text("絞り込む")', 'th a:has-text("表示")')
    c.login('speaker')
    c.goto(detail_url)
    shot(c, '#analytics-detail-card', '#analytics-detail-heading')
    c.login('owner')
    c.goto(detail_url)
    shot(c, '#analytics-detail-card', '#analytics-detail-heading')
    verify_guide(c)


@scenario('analytics/distribution-link', mobile=True)
def distribution(c):
    c.login('owner')
    c.goto('/analytics/dashboard/')
    shot(c, '.card:has(#campaign-daily-chart) .card-body > div:first-child', 'a[href="/analytics/campaigns/"]')
    c.goto('/analytics/campaigns/')
    shot(c, 'h1 >> xpath=..', 'a[href="/analytics/campaigns/new/"]')
    c.goto('/analytics/campaigns/new/')
    c.page.locator('#id_community').select_option('1')
    c.page.locator('#id_name').fill('春の案内ポスター')
    c.page.locator('#id_utm_source').fill('poster')
    c.page.locator('#id_utm_campaign').fill('demo-spring-2030-b')
    shot(c, ('#id_community >> xpath=..', '#id_utm_campaign >> xpath=..'), '#id_community', '#id_name', '#id_utm_source')
    shot(c, '#id_landing_path >> xpath=..', '#id_landing_path')
    shot(c, 'form > .d-flex', 'main button[type=submit]')
    c.goto('/analytics/campaigns/')
    shot(c, 'tbody td:has(.campaign-url-input)', '.campaign-url-input', '.copy-url-btn')
    shot(c, 'tbody td:first-child', 'tbody td:first-child img')
    table_row(c, 'tbody tr:has(.campaign-url-input)', 'a:has-text("編集")', 'a:has-text("削除")')
    c.goto('/analytics/campaigns/1/delete/')
    shot(c, 'main .container', 'button:has-text("削除する")', 'a:has-text("キャンセル")')
    c.goto('/analytics/dashboard/')
    shot(c, '.card:has(#campaign-daily-chart)', '.card:has(#campaign-daily-chart) table')
    verify_guide(c)
