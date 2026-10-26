"""主催者・スタッフが使う発表申請設定。"""
from capture import scenario
from scenarios.speaker import region


@scenario('settings/lt-application', mobile=True)
def lt_application(capture):
    capture.login('owner')
    capture.goto('/account/settings/')
    region(capture, ['h6:has-text("集会一覧")',
                     '.list-group-item:has(input[value="/community/settings/"]):has-text("さくら技術交流会")'],
           marks=['.list-group-item:has-text("さくら技術交流会") form:has(input[value="/community/settings/"]) button'])
    capture.goto('/community/settings/')
    capture.mask('input[name=notification_webhook_url]')
    region(capture, ['form[action$="/lt-settings/update/"] > .mb-3:has(#accepts_lt_application)'],
           marks=['label[for=accepts_lt_application]'])
    region(capture, ['form[action$="/lt-settings/update/"] > .mb-3:has(#recording_allowed_true)',
                     '#default-recording-policy-section'],
           marks=['[aria-label="撮影許可"]', '[aria-label="撮影ステータスの初期値"]'])
    region(capture, ['#lt-settings-detail .mb-3:has(#lt_application_template)'],
           marks=['#lt_application_template'])
    region(capture, ['#lt-settings-detail .mb-3:has(#default_lt_duration)',
                     '#lt-settings-detail .mb-3:has(#lt_start_offset_minutes)',
                     'form[action$="/lt-settings/update/"] button[type=submit]'],
           marks=['#default_lt_duration', '#lt_start_offset_minutes',
                  'form[action$="/lt-settings/update/"] button[type=submit]'])
