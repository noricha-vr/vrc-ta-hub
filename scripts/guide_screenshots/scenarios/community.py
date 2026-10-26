"""集会を登録する画面。"""
from capture import scenario


@scenario('community/create', mobile=True)
def create(capture):
    capture.login('general')
    capture.goto('/account/settings/')
    capture.annotate('a[href="/community/create/"]', 1)
    capture.shot(selector='a[href="/community/create/"] >> xpath=ancestor::div[contains(concat(" ", normalize-space(@class), " "), " card ")][1]')
    capture.goto('/community/create/')
    capture.page.locator('input[name=name]').fill('ヒナの科学交流会')
    capture.page.locator('input[name=organizers]').fill('ヒナ')
    capture.annotate('#id_name', 1)
    capture.annotate('#id_start_time', 2)
    capture.shot(selector='form[enctype="multipart/form-data"] .card >> nth=0')
