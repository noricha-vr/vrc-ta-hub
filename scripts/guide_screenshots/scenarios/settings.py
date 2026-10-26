"""集会設定の Discord 通知と Webhook 作成のイメージ図。"""
from capture import scenario


@scenario('settings/discord', mobile=True)
def discord(capture):
    capture.login('owner')
    capture.goto('/community/settings/')
    capture.mask('input[name=notification_webhook_url]')
    capture.annotate('input[name=notification_webhook_url]', 1)
    capture.annotate('form[action$="/webhook/update/"] button[type=submit]', 2)
    capture.shot(selector='div.border-top:has(input[name=notification_webhook_url])')
    capture.mock('discord-webhook.html')
    capture.annotate('#integrations', 1)
    capture.annotate('#webhooks', 2)
    capture.annotate('#new-webhook', 3)
    capture.shot(selector='.diagram')
