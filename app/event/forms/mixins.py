"""フォーム共通 Mixin。

EventDetailForm と LTApplicationEditForm でスライドPDF・サムネ画像の
検証ロジックと保存後処理が完全に重複していたため抽出。
撮影の選択肢（recording_policy）は申請フォームを含む 3 フォームで共通に扱う。
記事化の同意（article_consent）は発表者の申請・編集フォームだけで選ぶ（主催者は表示のみ）。
"""

from functools import partial
import logging

from django import forms
from django.db import transaction
from django.urls import reverse_lazy
from django.utils.functional import lazy
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from community.constants import RECORDING_POLICY_FORM_CHOICES

from ..form_validators import validate_and_sanitize_pdf, validate_thumbnail_image
from ..models import ARTICLE_BODY_FIELDS, SLIDE_FETCH_FIELDS, EventDetail, article_body_hash

logger = logging.getLogger(__name__)

# 編集画面を開いた時点の記事のハッシュを持たせる hidden の項目
ARTICLE_SNAPSHOT_FIELD = 'article_snapshot'

RECORDING_POLICY_LABEL = '撮影'
# 固定の文言と URL だけを HTML として扱い、テンプレートの autoescape は維持する。
RECORDING_POLICY_HELP_TEXT = mark_safe(
    '公開先は <a href="https://www.youtube.com/channel/UCNISeQgk2_lDF_4_UGWZ_Fw" '
    'target="_blank" rel="noopener">Hub の YouTube チャンネル</a> です。'
    '<a href="/news/2026-10-11-auto-recording-beta/" target="_blank" rel="noopener">撮影について詳しく</a>'
)

ARTICLE_CONSENT_LABEL = '発表の記事化'
ARTICLE_CONSENT_HELP_TEXT = lazy(format_html, str)(
    '動画か PDF を登録すると記事を自動で作り、<a href="{}" target="_blank" rel="noopener">'
    '発表一覧</a>に載せます。あとから編集できます。',
    reverse_lazy('event:detail_history'),
)
ARTICLE_CONSENT_REQUIRED_MESSAGE = '発表の記事化の OK / NG を選んでください。'
# 発表者が選べるのは 2 択。「未回答」は機能の追加前からある発表だけが持つ値
ARTICLE_CONSENT_CHOICES = [
    (EventDetail.ArticleConsent.OK.value, 'OK（スライド画像も載せる）'),
    (EventDetail.ArticleConsent.NG.value, 'NG（記事にしない）'),
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
        self._original_slide_file = self.instance.slide_file
        if self.is_bound:
            entry = self.data.get('slide_entry') or ('link' if self.data.get('slide_url') else 'pdf')
            # 選んでいない入口に残った入力は保存・検証の対象にしない。
            self.files = self.files.copy()
            if entry == 'pdf':
                self.data = self.data.copy()
                self.data['slide_url'] = ''
                self.files.pop('slide_source_file', None)
            elif entry == 'link':
                self.files.pop('slide_file', None)
                self.data = self.data.copy()
                self.data.pop('slide_file-clear', None)
        self.fields['slide_entry'] = forms.ChoiceField(
            choices=[('pdf', 'PDF をアップロード'), ('link', 'リンクから読み込む')],
            required=False, widget=forms.RadioSelect,
            # リンクを空にしても記事用の PDF が残る。PDF 側で開くと次の保存で消えるのでリンク側で開く。
            initial='link' if self.instance.slide_url or self.instance.slide_source_file else (
                'pdf' if self.instance.slide_file else ''
            ),
        )
        # FileInput は保存済みファイルの URL を描画しない。
        self.fields['slide_source_file'].widget = forms.FileInput(attrs={'class': 'form-control', 'accept': '.pdf'})
        self.fields['slide_file'].label = 'スライドの PDF'
        self.fields['slide_url'].label = 'スライドのリンク'
        self.fields['slide_url'].widget.attrs.update({
            'placeholder': 'https://speakerdeck.com/...',
            'aria-describedby': 'slide-url-help slide-status',
        })
        # 今の DB の記事のハッシュ。画面には hidden で持たせ、送信時の DB の値と比べる
        self._article_hash_now = self._article_hash(self.instance)
        self.fields[ARTICLE_SNAPSHOT_FIELD] = forms.CharField(
            widget=forms.HiddenInput, required=False, initial=self._article_hash_now,
        )

    @property
    def _shows_meta_description(self) -> bool:
        """この画面に要約（meta_description）の欄があるか。"""
        return 'meta_description' in self.fields

    def _article_hash(self, instance) -> str:
        """この画面で扱う記事の列のハッシュ（モデルと同じ article_body_hash で計算する）。

        要約の欄が無い画面では、要約の枠を空にして計算する。利用者が変えられない要約まで比べると、
        画面を開いた後にキューが要約ごと記事を作り直した時に「利用者が記事を変えた」と誤って判定し、
        古い画面の値で記事を消してしまう。その画面の保存では要約を書かない（``_update_fields``）。
        """
        if instance is None or instance._state.adding:
            return ''
        meta_description = instance.meta_description if self._shows_meta_description else ''
        return article_body_hash(instance.h1, instance.contents, meta_description)

    def clean_slide_file(self):
        entry = self.data.get('slide_entry') or ('link' if self.data.get('slide_url') else 'pdf')
        if entry == 'link':
            return self._original_slide_file
        return validate_and_sanitize_pdf(self.cleaned_data.get('slide_file'))

    def clean_slide_source_file(self):
        entry = self.data.get('slide_entry') or ('link' if self.data.get('slide_url') else 'pdf')
        if entry == 'pdf':
            return None
        return validate_and_sanitize_pdf(self.cleaned_data.get('slide_source_file'))

    def clean(self):
        cleaned = super().clean()
        entry = cleaned.get('slide_entry') or ('link' if cleaned.get('slide_url') else 'pdf')
        cleaned['slide_entry'] = entry
        if entry == 'pdf':
            cleaned['slide_url'] = ''
        else:
            # 非表示の PDF 欄の入力やクリア操作で、保持中の公開 PDF を変えない。
            cleaned['slide_file'] = self._original_slide_file
        return cleaned

    @property
    def slide_state(self):
        from event.services.slide_source import FETCH_ERROR_MESSAGES, is_importable
        detail = self.instance
        if detail.is_article_ng:
            return {'chip': '記事化しない', 'message': 'PDF は読み込みません。リンクは公開ページに載せます。'}
        if detail.slide_fetch_status in ('pending', 'fetching'):
            return {'chip': '読み込み中', 'message': '画面を閉じても続きます。'}
        if detail.slide_source_file and not detail.slide_fetch_url:
            return {
                'chip': 'PDF 登録済み', 'tone': 'success', 'fallback': True,
                'message': '公開ページはリンク先へ案内します。' if detail.slide_url else '',
            }
        if detail.slide_source_file:
            return {'chip': 'PDF 読み込み済み', 'tone': 'success', 'fallback': not is_importable(detail.slide_url)}
        if detail.slide_fetch_status == 'failed':
            reason = FETCH_ERROR_MESSAGES.get(detail.slide_fetch_error, 'PDF を読み込めませんでした。')
            return {'chip': '読み込み失敗', 'tone': 'danger', 'message': 'リンクは保存済みです。' + reason, 'fallback': True}
        if detail.slide_url:
            return {
                'chip': 'リンクのみ', 'fallback': not is_importable(detail.slide_url),
                'message': '公開ページはリンク先へ案内します。記事を作るには PDF をアップロードするか、動画を登録してください。',
            }
        return {}

    @property
    def slide_article_note(self):
        detail = self.instance
        if detail.is_article_ng:
            return '発表者が記事化を希望していないため、記事は作りません。'
        if detail.article_pdf:
            return 'この PDF を記事に使います。'
        if detail.slide_fetch_status in ('pending', 'fetching'):
            return 'PDF を読み込んだら記事を作ります。'
        if detail.slide_url:
            return 'PDF か動画を登録すると、記事を作ります。'
        return 'スライドか動画を登録すると、記事を自動で作ります。'

    def _apply_slide_entry(self, instance, *, commit=True):
        from event.services.slide_source import is_importable, mark_slide_fetch_pending, reset_slide_fetch
        old_file = instance.slide_source_file
        old_name = old_file.name if old_file else ''
        if self.cleaned_data['slide_entry'] == 'pdf':
            instance.slide_url = ''
            instance.slide_source_file = None
            reset_slide_fetch(instance)
        else:
            instance.slide_file = self._original_slide_file
            uploaded = self.files.get('slide_source_file')
            source_changed = bool(instance.slide_fetch_url and instance.slide_fetch_url != (instance.slide_url or ''))
            if uploaded:
                instance.slide_source_file = self.cleaned_data['slide_source_file']
                instance.slide_fetch_url = ''
                reset_slide_fetch(instance)
            elif source_changed:
                # slide_fetch_url の無い記事用 PDF は手動登録なので残す。
                instance.slide_source_file = None
                reset_slide_fetch(instance)
            if instance.is_article_ng or instance.status == 'rejected' or instance.deleted_at:
                reset_slide_fetch(instance)
            elif is_importable(instance.slide_url) and not uploaded and not instance.slide_source_file:
                # 同じ URL の処理中・成功・失敗を、関係ない編集で巻き戻さない。
                if source_changed or not instance.slide_fetch_status or instance.slide_fetch_url != instance.slide_url:
                    mark_slide_fetch_pending(instance)
            elif not is_importable(instance.slide_url):
                reset_slide_fetch(instance)
            if self.cleaned_data.get('generate_blog_article') and instance.slide_fetch_status in ('pending', 'fetching'):
                instance.slide_generate_requested = True
        new_name = instance.slide_source_file.name if instance.slide_source_file else ''
        if commit and old_name and old_name != new_name:
            transaction.on_commit(partial(old_file.storage.delete, old_name))

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
        """既存の発表の保存で書く列。生成管理の列は書かず、記事の列は必要な時だけ書く。

        要約の欄が無い画面は要約を書かず、DB の要約をそのまま残す（管理画面や API で直した要約を戻さない）。
        """
        skipped = set(ARTICLE_BODY_FIELDS) if keeps_article else set()
        if not self._shows_meta_description:
            skipped.add('meta_description')
        return [name for name in EventDetail.fields_without_article_control() if name not in skipped]

    def save(self, commit=True):
        instance = super().save(commit=False)
        with transaction.atomic():
            if not instance._state.adding:
                # Vket 同期・承認と同じく、関連する行を発表より先にロックする。
                from vket.application_link import lock_linked_detail
                lock_linked_detail(instance)
                # キューが保存した PDF・状態を、開いていたフォームの古い値で戻さない。
                current = EventDetail.all_objects.select_for_update().get(pk=instance.pk)
                for name in ('article_consent', 'status', 'deleted_at'):
                    if name not in self.fields:
                        setattr(instance, name, getattr(current, name))
                for name in SLIDE_FETCH_FIELDS:
                    setattr(instance, name, getattr(current, name))
                if self.cleaned_data['slide_entry'] == 'link':
                    instance.slide_file = current.slide_file
                    self._original_slide_file = current.slide_file
            self._apply_slide_entry(instance, commit=commit)
            if commit:
                if instance._state.adding:
                    instance.save()
                else:
                    keeps_article = self._keeps_article_in_db()
                    if keeps_article:
                        instance.refresh_from_db(fields=list(ARTICLE_BODY_FIELDS))
                    instance.save(update_fields=self._update_fields(keeps_article))
                self._save_m2m()
                logger.info('slide_entry_saved', extra={'event_type': 'slide_entry_saved', 'event_detail_id': instance.pk, 'entry': self.cleaned_data['slide_entry'], 'service': instance.slide_service or 'other'})
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
        self.fields['recording_policy'].choices = RECORDING_POLICY_FORM_CHOICES

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
