"""フォーム共通 Mixin。

EventDetailForm と LTApplicationEditForm でスライドPDF・サムネ画像の
検証ロジックと保存後処理が完全に重複していたため抽出。
撮影の選択肢（recording_policy）は申請フォームを含む 3 フォームで共通に扱う。
"""

from django import forms

from ..form_validators import validate_and_sanitize_pdf, validate_thumbnail_image
from ..models import EventDetail

RECORDING_POLICY_LABEL = '撮影'
RECORDING_POLICY_HELP_TEXT = 'ハブの自動撮影で、この発表をどう扱うかを選んでください。'


def recording_policy_widget() -> forms.RadioSelect:
    """撮影の選択肢に使うラジオボタン。"""
    return forms.RadioSelect(attrs={'class': 'form-check-input'})


class EventDetailMediaFormMixin:
    """スライドPDF・サムネ画像の検証と保存後処理を共通化するMixin。

    EventDetailForm と LTApplicationEditForm の両方で同一実装が重複していたため抽出。
    """

    def clean_slide_file(self):
        return validate_and_sanitize_pdf(self.cleaned_data.get('slide_file'))

    def clean_thumbnail_image(self):
        return validate_thumbnail_image(self.cleaned_data.get('thumbnail_image'))

    def save(self, commit=True):
        instance = super().save(commit=commit)
        if commit:
            from event.services.media_service import ensure_pdf_thumbnail
            from twitter.services.tweet_generation import sync_slide_share_queue_image

            ensure_pdf_thumbnail(instance, save=True)
            sync_slide_share_queue_image(instance)
        return instance


class RecordingPolicyFormMixin:
    """撮影の選択肢（recording_policy）を任意入力として扱うMixin。

    ラジオボタンは既定値が選択済みで表示されるため通常は必ず送られるが、
    未送信・空のときは既存の値を保ち、同意を勝手に変えない。
    集会への新規の申請で未送信なのは撮影の選択肢を見ていない時（表示後に集会が撮影を許可した等）なので「禁止」にする。
    集会も既存の値も無い時（主催者の新規登録）は「公開」。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['recording_policy'].required = False

    def clean_recording_policy(self):
        value = self.cleaned_data.get('recording_policy')
        if value:
            return value
        instance = getattr(self, 'instance', None)
        if instance is not None and instance.recording_policy:
            return instance.recording_policy
        if getattr(self, 'community', None) is not None:
            return EventDetail.RecordingPolicy.FORBIDDEN
        return EventDetail.RecordingPolicy.PUBLIC

    def remove_recording_policy_unless_allowed(self, community) -> bool:
        """集会が撮影を許可していなければ撮影の選択肢を消す。消したら True。

        登壇者には選ばせず、保存する値は呼び出し側が決める（新規は「禁止」、既存は今の値のまま）。
        """
        if community is None or community.recording_allowed:
            return False
        self.fields.pop('recording_policy', None)
        return True
