"""ログインなしで集会・発表・予定を見つける。外部カレンダーには接続しない。"""
from capture import scenario

from .analytics import shot, verify_guide


def navigation(c):
    if c.mobile:
        c.page.locator('.navbar-toggler').click()
        c.page.locator('#navbarNav.show').wait_for(state='visible')


@scenario('browse/find', mobile=True)
def find(c):
    c.goto('/')
    navigation(c)
    shot(c, ('header', '#navbarNav .navbar-nav') if c.mobile else 'header', '#navbarNav a[href="/"]', '#navbarNav a[href="/community/list/"]', '#navbarNav a[href="/event/detail/history/"]')
    if c.mobile:
        c.goto('/')  # 固定表示のメニューを閉じてから、ホーム下部を撮る。
    shot(c, 'a[href="/event/list/"]:has-text("すべてのイベントを見る") >> xpath=..', 'a[href="/event/list/"]:has-text("すべてのイベントを見る")')
    c.goto('/event/list/')
    shot(c, 'main form', 'input[name=name]', 'button:has-text("検索")')
    c.goto('/community/list/?query=さくら')
    shot(c, 'main form', 'input[name=query]', 'button:has-text("検索")', 'a:has-text("アーカイブ")')
    shot(c, 'main .card:has(h2) >> nth=0', 'main .card h2 >> nth=0')
    c.goto('/community/archive/')
    shot(c, 'main .card:has(h2) >> nth=0', 'main .card h2 >> nth=0')
    c.goto('/event/detail/history/?view=all')
    shot(c, ('form[role=search]', '.presentation-row >> nth=0'), 'input[name=q]', 'a:has-text("すべて (")', 'a:has-text("特別企画・ブログ")')
    c.goto('/event/detail/history/?type=special')
    shot(c, 'article.card >> nth=0', 'article.card h2 >> nth=0')
    c.goto('/news/')
    shot(c, 'a.card[href="/news/guide-demo-news/"]', 'a.card[href="/news/guide-demo-news/"] h5')
    shot(c, 'footer a[href="/community/criteria/"] >> xpath=ancestor::ul', 'footer a[href="/community/criteria/"]')
    c.goto('/community/1/')
    shot(c, 'main .col-lg-5', 'a[href="/community/1/poster/download/"]')
    verify_guide(c)


@scenario('browse/calendar', mobile=True)
def calendar(c):
    c.goto('/')
    shot(c, '.card:has(#calendarSyncDetails)', 'a[href="#calendarSyncDetails"]')
    c.page.locator('a[href="#calendarSyncDetails"]').click()
    c.page.locator('#calendarSyncDetails.show').wait_for(state='visible')
    shot(c, '.card:has(#calendarSyncDetails)', '#calendarSyncDetails a:has-text("Googleカレンダーに追加")')
    shot(c, '.card-hover-container:has(.calendar-add-button) >> nth=0', '.calendar-add-button >> nth=0')
    verify_guide(c)
