"""フォーム共通 Mixin。

EventDetailForm と LTApplicationEditForm でスライドPDF・サムネ画像の
検証ロジックと保存後処理が完全に重複していたため抽出。
撮影の選択肢（recording_policy）は申請フォームを含む 3 フォームで共通に扱う。
記事化の同意（article_consent）は発表者の申請・編集フォームだけで選ぶ（主催者は表示のみ）。
"""

from django import forms

from ..form_validators import validate_and_sanitize_pdf, validate_thumbnail_image
from ..models import ARTICLE_BODY_FIELDS, EventDetail, article_body_hash

# 編集画面を開いた時点の記事のハッシュを持たせる hidden の項目
ARTICLE_SNAPSHOT_FIELD = 'article_snapshot'

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
    既存の発表の保存では、画面を開いている間に記事の自動生成が書いた値を、開いた時の古い値で戻さない。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 今の DB の記事（タイトルと本文）のハッシュ。画面には hidden で持たせ、送信時の DB の値と比べる
        self._article_hash_now = self._article_hash(self.instance)
        self.fields[ARTICLE_SNAPSHOT_FIELD] = forms.CharField(
            widget=forms.HiddenInput, required=False, initial=self._article_hash_now,
        )

    @staticmethod
    def _article_hash(instance) -> str:
        if instance is None or instance._state.adding:
            return ''
        return article_body_hash(instance.h1, instance.contents)

    def clean_slide_file(self):
        return validate_and_sanitize_pdf(self.cleaned_data.get('slide_file'))

    def clean_thumbnail_image(self):
        return validate_thumbnail_image(self.cleaned_data.get('thumbnail_image'))

    def _keeps_article_in_db(self) -> bool:
        """記事の 3 列を書かずに DB の値を残すか。

        画面を開いた後に DB の記事が変わり（キューが記事を作った等）、利用者が記事の欄を変えていない時。
        利用者が記事の欄を変えた時は、利用者の編集を優先して書く。hidden の無い古い画面は今までどおり書く。
        """
        opened = self.cleaned_data.get(ARTICLE_SNAPSHOT_FIELD) or ''
        if not opened:
            return False
        if self._article_hash(self.instance) != opened:
            # construct_instance の後なので、ここでの instance の記事は送信された値
            return False
        return self._article_hash_now != opened

    def _update_fields(self, keeps_article: bool) -> list[str]:
        """既存の発表の保存で書く列。生成管理の列は書かず、記事の列は必要な時だけ書く。"""
        fields = EventDetail.fields_without_article_control()
        if keeps_article:
            fields = [name for name in fields if name not in ARTICLE_BODY_FIELDS]
        return fields

    def save(self, commit=True):
        if commit and not self.instance._state.adding:
            instance = super().save(commit=False)
            keeps_article = self._keeps_article_in_db()
            if keeps_article:
                # 書かない記事の列は、保存の前に DB の値（画面を開いた後に作られた記事）へ揃える。古い値のままだと
                # 保存のシグナルが「記事を空にした」と誤って作り直しを頼み、保存と同時の生成も
                # 生成中に記事が書き換えられたと誤って判定する
                instance.refresh_from_db(fields=list(ARTICLE_BODY_FIELDS))
            instance.save(update_fields=self._update_fields(keeps_article))
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
