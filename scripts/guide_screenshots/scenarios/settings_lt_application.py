"""主催者・スタッフが使う発表申請設定。"""
from capture import scenario
from scenarios.community import select, settings_entry
from scenarios.settings import shot
from scenarios.speaker import region


@scenario('settings/lt-application', mobile=True)
def lt_application(capture):
    capture.login('owner')
    settings_entry(capture)
    select(capture)
    region(capture, ['form[action$="/lt-settings/update/"] > .mb-3:has(#accepts_lt_application)'],
           marks=['label[for=accepts_lt_application]'])
    region(capture, ['form[action$="/lt-settings/update/"] > .mb-3:has(#recording_allowed_true)',
                     '#default-recording-policy-section'],
           marks=['[aria-label="撮影許可"]', '[aria-label="撮影ステータスの初期値"]'])
    region(capture, ['#lt-settings-detail .mb-3:has(#lt_application_template)'],
           marks=['#lt_application_template'])
    # 変更のあるまとまりだけ保存ボタンが出るため、送信せず値を変更する。
    capture.page.locator('#default_lt_duration').fill('25')
    shot(capture, '#lt-settings-detail .mb-3:has(#default_lt_duration)',
         '#default_lt_duration', '#lt_start_offset_minutes', 'button[form=lt-form]')
