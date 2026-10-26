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
    c.page.locator('nav.mylist-nav a[href^="/analytics/dashboard/"]').scroll_into_view_if_needed()
    shot(c, ('header.mylist-header', 'nav.mylist-nav'), 'nav.mylist-nav a[href^="/analytics/dashboard/"]')
    c.goto('/account/settings/')
    shot(c, '.list-group:has(a[href="/analytics/dashboard/"])', 'a[href="/analytics/dashboard/"]')
    c.goto('/analytics/dashboard/')
    shot(c, 'form[aria-label="集会と期間の絞り込み"]', '#community-select', '[aria-label="集計期間"]')
    shot(c, '.card:has(.analytics-kpis)', '.analytics-kpis')
    detail_url = c.page.locator('a:text-is("小さなワールドの作り方")').get_attribute('href')
    shot(c, '.card:has(h2:has-text("ページ別ランキング"))', 'th a:has-text("閲覧")', 'th a:has-text("動画再生")')
    shot(c, 'main .col-lg-4', '.card:has(#weekly-chart) h2', '.analytics-source-legend', '.card a[href^="/analytics/dashboard/search-queries/"]')
    shot(c, '.card:has(#campaign-daily-chart)', '.card:has(#campaign-daily-chart) table')
    shot(c, 'form[aria-label="集会と期間の絞り込み"]', 'a[aria-label="CSV をダウンロード"]')
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
    shot(c, ('header.admin-page-header', 'nav[aria-label="集会管理"]'), 'nav[aria-label="集会管理"] a[href="/analytics/campaigns/"]')
    c.goto('/analytics/campaigns/')
    shot(c, 'header.admin-page-header', 'a[href="/analytics/campaigns/new/"]')
    c.goto('/analytics/campaigns/new/')
    c.page.locator('#id_community').select_option('1')
    c.page.locator('#id_name').fill('春の案内ポスター')
    c.page.locator('#id_utm_source').fill('poster')
    c.page.locator('#id_utm_campaign').fill('demo-spring-2030-b')
    shot(c, 'section[aria-labelledby="campaign-target-heading"]', '#id_community', '#id_name')
    shot(c, '#id_landing_path >> xpath=..', '#id_landing_path')
    shot(c, ('section[aria-labelledby="campaign-tracking-heading"]', 'section[aria-labelledby="campaign-schedule-heading"]', '[data-admin-action-bar]'), '#id_utm_source', '#id_utm_campaign', 'button[form="campaign-form"]')
    # 入力例は保存せず、一覧は保存済みのデモを使う。
    c.page.once('dialog', lambda dialog: dialog.accept())
    c.goto('/analytics/campaigns/')
    card = 'article.analytics-campaign:has(#campaign-1-heading)'
    shot(c, card, f'{card} .analytics-campaign-url', f'{card} .copy-url-btn')
    shot(c, f'{card} .analytics-campaign-heading', f'{card} a:has(img)')
    c.page.locator(f'{card} [data-bs-toggle="dropdown"]').click()
    c.page.locator(f'{card} .dropdown-menu.show').wait_for(state='visible')
    shot(c, (f'{card} .event-summary', f'{card} .dropdown-menu.show'), f'{card} a:has-text("編集")', f'{card} [data-bs-toggle="dropdown"]', f'{card} [data-confirm-panel="campaign-delete-1"]')
    c.page.locator(f'{card} [data-confirm-panel="campaign-delete-1"]').click()
    c.page.locator('#campaign-delete-1').wait_for(state='visible')
    shot(c, '#campaign-delete-1', '#campaign-delete-1 button[type=submit]', '#campaign-delete-1 [data-cancel-panel]')
    c.goto('/analytics/dashboard/')
    shot(c, '.card:has(#campaign-daily-chart)', '.card:has(#campaign-daily-chart) table')
    verify_guide(c)
