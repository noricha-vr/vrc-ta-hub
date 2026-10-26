"""架空のコラボを使った参加登録・お知らせ・ステージ準備。"""
from capture import scenario

from .analytics import shot, verify_guide


@scenario('vket/apply', mobile=True)
def apply(c):
    c.login('owner')
    c.goto('/event/my_list/')
    shot(c, '.vket-banner', '.vket-banner-actions a')
    c.goto('/vket/1/')
    shot(c, 'main .container > .d-flex:has(h1)', 'a[href="/vket/1/apply/"]')
    c.goto('/vket/1/apply/')
    shot(c, '#id_requested_date >> xpath=ancestor::div[contains(@class,"mb-3")][1]', '#id_requested_date')
    shot(c, '.row:has(#id_requested_start_time)', '#id_requested_start_time', '#id_requested_duration')
    shot(c, '#lt-formset-container', '#id_lt-0-speaker', '#id_lt-0-theme', '#id_lt-0-lt_start_time')
    shot(c, '#organizer-note', '#id_organizer_note')
    shot(c, '.admin-action-bar', 'button[form="vket-apply-form"]')
    c.goto('/vket/1/status/')
    shot(c, '.admin-card:has(h2:has-text("日程情報"))', '.admin-card:has(h2:has-text("日程情報")) a:has-text("編集")')
    verify_guide(c)


@scenario('vket/stage', mobile=True)
def stage(c):
    c.login('owner')
    c.goto('/vket/1/status/')
    shot(c, '.admin-card:has(h2:has-text("Vketステージ登録"))', 'a:has-text("登録用の入力値・サムネイルを用意する")')
    c.goto('/vket/1/stage-copy/1/')
    shot(c, '.admin-card:has(#stage-title)', 'button[data-copy-target="stage-title"]')
    shot(c, '.admin-card:has(#stage-body)', '#stage-body', 'button[data-copy-target="stage-body"]')
    shot(c, '.admin-card:has(#stage-start)', 'button[data-copy-target="stage-start"]')
    # 「未生成」の状態は別の架空参加で用意済み。生成 API は呼ばない。
    c.login('staff')
    c.goto('/vket/1/stage-copy/2/')
    shot(c, '#stage-thumbnail', '#stage-thumbnail-form button')
    c.goto('/vket/1/stage-copy/1/')
    shot(c, '#stage-thumbnail', 'a:has-text("JPEGをダウンロード")')
    c.mock('vket-stage.html')
    shot(c, 'main', '#fields', '#thumbnail', '#complete')
    c.login('owner')
    c.goto('/vket/1/status/')
    c.page.locator('[data-confirm-panel="stage-register-confirm"]').click()
    c.page.locator('#stage-register-confirm').wait_for(state='visible')
    shot(c, '#stage-registration', '[data-confirm-panel="stage-register-confirm"]',
         '#stage-register-confirm button[type="submit"]')
    verify_guide(c)


@scenario('vket/notices', mobile=True)
def notices(c):
    c.login('owner')
    c.goto('/vket/1/status/')
    shot(c, '#member-attention', '#member-attention a[href^="/vket/1/notices/?open="]')
    c.goto('/vket/1/notices/?open=1')
    shot(c, '#unackedAccordion', '#unackedAccordion button[type="submit"]')
    c.goto('/vket/1/notices/?open=2')
    shot(c, '.notice-done', '.notice-done .accordion-button:has-text("資料の準備ができました")')
    c.page.context.clear_cookies()
    c.goto('/vket/ack/00000000-0000-4000-8000-000000000001/')
    shot(c, 'main .container', 'main button[type="submit"]')
    verify_guide(c)
