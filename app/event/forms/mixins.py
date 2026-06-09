"""フォーム共通 Mixin。

EventDetailForm と LTApplicationEditForm でスライドPDF・サムネ画像の
検証ロジックと保存後処理が完全に重複していたため抽出。
"""

from ..form_validators import validate_and_sanitize_pdf, validate_thumbnail_image


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
            from twitter.signals import sync_slide_share_queue_image

            ensure_pdf_thumbnail(instance, save=True)
            sync_slide_share_queue_image(instance)
        return instance
