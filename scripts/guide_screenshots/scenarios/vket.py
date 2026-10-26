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
    shot(c, '#id_organizer_note >> xpath=..', '#id_organizer_note')
    shot(c, 'form.card .d-flex.justify-content-end', 'button:has-text("保存する")')
    c.goto('/vket/1/status/')
    shot(c, '.card:has(h2:has-text("日程情報"))', '.card:has(h2:has-text("日程情報")) a:has-text("編集")')
    verify_guide(c)


@scenario('vket/stage', mobile=True)
def stage(c):
    c.login('owner')
    c.goto('/vket/1/status/')
    shot(c, '.card:has(h2:has-text("Vketステージ登録"))', 'a:has-text("登録用の入力値・サムネイルを用意する")')
    c.goto('/vket/1/stage-copy/1/')
    shot(c, '.card:has(#stage-title)', 'button[data-copy-target="stage-title"]')
    shot(c, '.card:has(#stage-body)', '#stage-body', 'button[data-copy-target="stage-body"]')
    shot(c, '.card:has(#stage-start)', 'button[data-copy-target="stage-start"]')
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
    shot(c, '.card:has(h2:has-text("Vketステージ登録"))', 'button:has-text("Vketで登録したことを記録する")')
    verify_guide(c)


@scenario('vket/notices', mobile=True)
def notices(c):
    c.login('owner')
    c.goto('/vket/1/status/')
    shot(c, '.alert-warning', '.alert-warning a:has-text("確認する")')
    c.goto('/vket/1/notices/?open=1')
    shot(c, '#unackedAccordion', 'button:has-text("確認しました")')
    c.goto('/vket/1/notices/?open=2')
    shot(c, '.notice-done', '.notice-done button:has-text("資料の準備ができました")')
    c.page.context.clear_cookies()
    c.goto('/vket/ack/00000000-0000-4000-8000-000000000001/')
    shot(c, 'main .container', 'button:has-text("確認しました")')
    verify_guide(c)
