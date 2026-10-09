"""フォーム共通 Mixin。

EventDetailForm と LTApplicationEditForm でスライドPDF・サムネ画像の
検証ロジックと保存後処理が完全に重複していたため抽出。
撮影の選択肢（recording_policy）は申請フォームを含む 3 フォームで共通に扱う。
記事化の同意（article_consent）は発表者の申請・編集フォームだけで選ぶ（主催者は表示のみ）。
"""

from django import forms

from ..form_validators import validate_and_sanitize_pdf, validate_thumbnail_image
from ..models import EventDetail

RECORDING_POLICY_LABEL = '撮影'
RECORDING_POLICY_HELP_TEXT = 'ハブの自動撮影で、この発表をどう扱うかを選んでください。'

ARTICLE_CONSENT_LABEL = '発表の記事化'
ARTICLE_CONSENT_HELP_TEXT = (
    '発表の動画やスライドから内容を記事にまとめ、ハブで公開してよいかを選んでください。'
    'OK の場合は、動画（YouTube URL）かスライドPDFを登録した時点で記事を自動で作ります。'
    '記事はあとから編集でき、編集した記事は自動では書き換えません。'
    'NG の場合は記事を作らず、表示もしません。'
)
ARTICLE_CONSENT_REQUIRED_MESSAGE = '発表の記事化の OK / NG を選んでください。'
# 発表者が選べるのは 2 択。「未回答」は機能の追加前からある発表だけが持つ値
ARTICLE_CONSENT_CHOICES = [
    (EventDetail.ArticleConsent.OK.value, EventDetail.ArticleConsent.OK.label),
    (EventDetail.ArticleConsent.NG.value, EventDetail.ArticleConsent.NG.label),
]


def recording_policy_widget() -> forms.RadioSelect:
    """撮影の選択肢に使うラジオボタン。"""
    return forms.RadioSelect(attrs={'class': 'form-check-input'})


def article_consent_widget() -> forms.RadioSelect:
    """記事化の同意に使うラジオボタン。"""
    return forms.RadioSelect(attrs={'class': 'form-check-input'})


def configure_article_generation_field(form: forms.BaseForm, instance) -> None:
    """記事を生成するチェックボックスの出し分け。

    記事化 NG の発表は生成しないので出さない。記事化 OK の発表は、動画か PDF が入ると
    Cloud Scheduler のキューが記事を作るので、保存時の生成と二重にならないよう出さない
    （画面には ``form.article_auto_generation`` を見て自動で作る旨を出す）。
    """
    existing = instance is not None and instance.pk
    form.article_auto_generation = bool(
        existing and instance.article_consent == EventDetail.ArticleConsent.OK
    )
    if existing and (instance.is_article_ng or form.article_auto_generation):
        form.fields.pop('generate_blog_article', None)


class ArticleConsentFormMixin:
    """発表者の編集フォームで記事化の同意（article_consent）を OK / NG から選ばせる Mixin。

    「未回答」の既存の発表はどちらも選ばれていない状態で表示する。
    未送信・空のときは今の値を保ち、同意を勝手に変えない。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        field = self.fields['article_consent']
        field.choices = ARTICLE_CONSENT_CHOICES
        field.required = False

    def clean_article_consent(self):
        value = self.cleaned_data.get('article_consent')
        if value:
            return value
        return self.instance.article_consent


class EventDetailMediaFormMixin:
    """スライドPDF・サムネ画像の検証と保存後処理を共通化するMixin。

    EventDetailForm と LTApplicationEditForm の両方で同一実装が重複していたため抽出。
    """

    def clean_slide_file(self):
        return validate_and_sanitize_pdf(self.cleaned_data.get('slide_file'))

    def clean_thumbnail_image(self):
        return validate_thumbnail_image(self.cleaned_data.get('thumbnail_image'))

    def save(self, commit=True):
        if commit and not self.instance._state.adding:
            instance = super().save(commit=False)
            # 記事の自動生成が管理する列は書き戻さない。画面を開いている間に自動生成が書いた値を、
            # 開いた時の古い値で消さないため（新規作成は通常どおり全列を書く）
            instance.save(update_fields=EventDetail.fields_without_article_control())
            self._save_m2m()
        else:
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
