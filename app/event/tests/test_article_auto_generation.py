"""記事の自動生成（生成待ちの印・待ち行列の処理・通知・エンドポイント）のテスト。

外部 API（LLM・YouTube・PDF ワーカー）はすべてモックする。
"""

import json
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.db import DatabaseError, connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from event.forms import EventDetailForm, LTApplicationEditForm
from event.models import EventDetail, article_body_hash
from event.services import article_generation
from event.services.article_generation import MAX_ATTEMPTS, MAX_DEFERRALS, process_article_generation_queue
from event.services.content_generation_service import (
    ARTICLE_EDITED_MESSAGE,
    EDITED,
    REFUSED,
    SAVED,
    BlogOutput,
    BlogSources,
    _with_sources,
    generate_blog,
    save_generated_article,
)
from event.views.helpers import extract_video_id
from event.youtube_urls import youtube_video_id
from tests.factories import make_community, make_discord_linked_user, make_event, make_event_detail, make_user
from vket.models import VketCollaboration, VketParticipation, VketPresentation

ArticleConsent = EventDetail.ArticleConsent
ArticleState = EventDetail.ArticleState

VIDEO_ID = 'rrKl0s23E0M'
VIDEO_URL = f'https://www.youtube.com/watch?v={VIDEO_ID}'
OTHER_VIDEO_URL = 'https://www.youtube.com/watch?v=abcdefghijk'
SLIDE_NAME = 'slide/0123456789abcdef.pdf'
TRANSCRIPT = '字幕のテキスト。発表で話した内容です。'
PDF_TEXT = 'PDFのテキスト。スライドに書いた内容です。'
GENERATED = {'title': '生成した記事のタイトル', 'meta_description': '記事の要約', 'text': '## 見出し\n生成した本文'}


def _fake_transcript(video_id, language='ja'):
    """本物の get_transcript と同じく、動画 ID が無ければ空文字を返す。"""
    return TRANSCRIPT if video_id else ''


def _openrouter_client(payload=None):
    """関数呼び出しで記事を返す OpenRouter クライアントのモック。"""
    tool_call = MagicMock()
    tool_call.function.arguments = json.dumps(payload or GENERATED, ensure_ascii=False)
    completion = MagicMock()
    completion.choices = [MagicMock(message=MagicMock(tool_calls=[tool_call]))]
    client = MagicMock()
    client.chat.completions.create.return_value = completion
    return client


def _sent_prompts(openai_class) -> list[str]:
    """OpenRouter に送ったユーザープロンプトの一覧。"""
    client = openai_class.return_value
    return [
        call.kwargs['messages'][1]['content']
        for call in client.chat.completions.create.call_args_list
    ]


class ArticleBodyHashTest(TestCase):
    """手動編集の判定に使う本文のハッシュと、記事の状態。"""

    def setUp(self):
        self.detail = make_event_detail(make_event(make_community(name='ハッシュの集会')))

    def test_form_round_trip_does_not_change_hash(self):
        """textarea の CRLF とフォームの前後空白の除去では、ハッシュは変わらない。"""
        self.assertEqual(
            article_body_hash('タイトル', '一行目\n二行目'),
            article_body_hash(' タイトル ', '一行目\r\n二行目\r\n'),
        )

    def test_title_or_body_edit_changes_hash(self):
        """タイトルか本文を書き換えるとハッシュが変わる。"""
        original = article_body_hash('タイトル', '本文')
        self.assertNotEqual(original, article_body_hash('直したタイトル', '本文'))
        self.assertNotEqual(original, article_body_hash('タイトル', '直した本文'))

    def test_state_is_none_without_article(self):
        self.assertEqual(self.detail.article_state(), ArticleState.NONE)

    def test_state_is_auto_after_generation(self):
        self.detail.h1, self.detail.contents = 'タイトル', '本文'
        self.detail.record_generated_article()

        self.assertEqual(self.detail.article_state(), ArticleState.AUTO)

    def test_state_is_manual_after_edit(self):
        self.detail.h1, self.detail.contents = 'タイトル', '本文'
        self.detail.record_generated_article()
        self.detail.contents = '発表者が直した本文'

        self.assertEqual(self.detail.article_state(), ArticleState.MANUAL)

    def test_article_without_hash_is_treated_as_manual(self):
        """この機能より前の記事や手書きの記事（ハッシュ無し）は上書きしない。"""
        self.detail.contents = '前からある記事'

        self.assertEqual(self.detail.article_state(), ArticleState.MANUAL)

    def test_emptied_article_is_none_even_with_hash(self):
        """生成した記事のタイトルも本文も空にしたら未生成に戻る（固定されず、自動で作り直せる）。"""
        self.detail.h1, self.detail.contents = 'タイトル', '本文'
        self.detail.record_generated_article()
        self.detail.h1, self.detail.contents = '', ' \r\n'

        self.assertEqual(self.detail.article_state(), ArticleState.NONE)


class YouTubeVideoIdTest(SimpleTestCase):
    """動画 ID は YouTube の URL からだけ取り出す（Discord のメッセージリンクは動画なし）。"""

    VIDEO_URLS = (
        f'https://www.youtube.com/watch?v={VIDEO_ID}',
        f'https://www.youtube.com/watch?v={VIDEO_ID}?t=123',  # ? が 2 つある崩れた URL
        f'https://youtu.be/{VIDEO_ID}?t=30',
        f'https://youtu.be/{VIDEO_ID}&t=30',  # ? ではなく & で続く崩れた URL
        f'https://www.youtu.be/{VIDEO_ID}',
        f'https://www.youtube.com/v/{VIDEO_ID}&hl=ja',
        f'https://www.youtube.com/live/{VIDEO_ID}?si=share',
        f'https://m.youtube.com/watch?v={VIDEO_ID}&t=1m',
        f'https://www.youtube.com/shorts/{VIDEO_ID}',
        f'https://www.youtube.com/embed/{VIDEO_ID}',
    )
    NOT_VIDEO_URLS = (
        'https://discord.com/channels/123456789012345678/234567890123456789/345678901234567890',
        f'https://example.com/watch?v={VIDEO_ID}',
        'https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv',
        'https://www.youtube.com/@handle',
        '',
        None,
    )

    def test_youtube_urls(self):
        for url in self.VIDEO_URLS:
            with self.subTest(url=url):
                self.assertEqual(youtube_video_id(url), VIDEO_ID)

    def test_non_youtube_urls(self):
        for url in self.NOT_VIDEO_URLS:
            with self.subTest(url=url):
                self.assertIsNone(youtube_video_id(url))

    def test_detail_page_and_model_agree(self):
        """詳細ページの埋め込み（extract_video_id）と EventDetail.video_id の判定が同じ。"""
        for url in self.VIDEO_URLS + self.NOT_VIDEO_URLS:
            with self.subTest(url=url):
                self.assertEqual(extract_video_id(url), EventDetail(youtube_url=url).video_id)


class PreviousValuesTest(TestCase):
    """保存前の値は event / ta_hub / twitter のシグナルで共有し、1 回の SELECT で読む。"""

    def test_pre_save_reads_previous_values_once(self):
        event = make_event(make_community(name='旧値の集会'), event_date=date.today() - timedelta(days=1))
        detail = make_event_detail(
            event, status='approved', article_consent=ArticleConsent.OK, slide_file=SLIDE_NAME,
        )
        detail = EventDetail.objects.get(pk=detail.pk)
        detail.youtube_url = VIDEO_URL

        with CaptureQueriesContext(connection) as queries:
            detail.save()

        sqls = [query['sql'] for query in queries.captured_queries]
        update_at = next(i for i, sql in enumerate(sqls) if sql.startswith('UPDATE "event_detail"'))
        selects_before_update = [
            sql for sql in sqls[:update_at] if sql.startswith('SELECT') and '"event_detail"' in sql
        ]
        self.assertEqual(len(selects_before_update), 1)
        # 共有した旧値が ta_hub・twitter のシグナルにも渡っている
        self.assertEqual(detail._old_youtube_url, '')
        self.assertEqual(detail._old_slide_file, SLIDE_NAME)
        self.assertEqual(detail._old_status, 'approved')
        self.assertEqual(detail._old_event_date, event.date)
        self.assertEqual(detail._old_index_detail_type, 'LT')

    def test_previous_values_are_not_reused_across_saves(self):
        detail = make_event_detail(make_event(make_community(name='旧値の集会 2')), theme='最初のテーマ')
        detail.theme = '2 回目のテーマ'
        detail.save()
        detail.theme = '3 回目のテーマ'
        detail.save()

        self.assertEqual(detail._old_theme, '2 回目のテーマ')


class FormSaveKeepsGenerationColumnsTest(TestCase):
    """フォームの保存は、記事の自動生成が管理する列を古い値で上書きしない。"""

    def setUp(self):
        self.owner = make_user(user_name='keep_owner', email='keep_owner@example.com')
        self.detail = make_event_detail(
            make_event(make_community(name='上書きの集会', owner=self.owner)),
            status='approved',
            article_consent=ArticleConsent.OK,
        )

    def _write_generation_columns_meanwhile(self):
        """フォームを開いている間に自動生成が書いた値（フォームのインスタンスは古いまま）。"""
        now = timezone.now()
        EventDetail.objects.filter(pk=self.detail.pk).update(
            article_generation_requested_at=now,
            article_generation_attempts=2,
            article_body_hash='x' * 64,
            article_published_notified_at=now,
        )

    def _assert_generation_columns_kept(self):
        self.detail.refresh_from_db()
        self.assertIsNotNone(self.detail.article_generation_requested_at)
        self.assertEqual(self.detail.article_generation_attempts, 2)
        self.assertEqual(self.detail.article_body_hash, 'x' * 64)
        self.assertIsNotNone(self.detail.article_published_notified_at)

    def test_applicant_edit_form(self):
        form = LTApplicationEditForm(
            data={'theme': '直したテーマ', 'speaker': '発表者'},
            instance=EventDetail.objects.get(pk=self.detail.pk),
        )
        self.assertTrue(form.is_valid(), form.errors)
        self._write_generation_columns_meanwhile()

        form.save()

        self._assert_generation_columns_kept()
        self.assertEqual(self.detail.theme, '直したテーマ')

    def test_organizer_edit_form(self):
        form = EventDetailForm(
            data={
                'detail_type': 'LT', 'theme': '主催者が直したテーマ', 'speaker': '発表者',
                'start_time': '22:00', 'duration': 30,
            },
            instance=EventDetail.objects.get(pk=self.detail.pk),
        )
        self.assertTrue(form.is_valid(), form.errors)
        self._write_generation_columns_meanwhile()

        form.save()

        self._assert_generation_columns_kept()
        self.assertEqual(self.detail.theme, '主催者が直したテーマ')


class ArticleGenerationRequestTest(TestCase):
    """生成待ちの印（article_generation_requested_at）を付ける条件。"""

    def setUp(self):
        self.event = make_event(make_community(name='印の集会'), event_date=date.today() - timedelta(days=1))

    def _detail(self, **extra):
        defaults = {'status': 'approved', 'article_consent': ArticleConsent.OK}
        defaults.update(extra)
        return make_event_detail(self.event, **defaults)

    def _requested_at(self, detail):
        return EventDetail.objects.values_list('article_generation_requested_at', flat=True).get(pk=detail.pk)

    def _as_generated(self, detail):
        """自動生成のままの記事がある状態にする。"""
        detail.h1, detail.contents = '生成した記事', '## 見出し\n生成した本文\n'
        fields = detail.record_generated_article()
        detail.save(update_fields=['h1', 'contents', *fields])
        return detail

    def test_marks_when_slide_is_added(self):
        detail = self._detail()
        self.assertIsNone(self._requested_at(detail))

        detail.slide_file = SLIDE_NAME
        detail.save()

        self.assertIsNotNone(self._requested_at(detail))

    def test_marks_when_video_is_added_with_update_fields(self):
        """save(update_fields=...) で動画だけ保存しても印が残る。"""
        detail = self._detail()

        detail.youtube_url = VIDEO_URL
        detail.save(update_fields=['youtube_url'])

        self.assertIsNotNone(self._requested_at(detail))

    def test_marks_when_video_changes_on_auto_article(self):
        detail = self._as_generated(self._detail(youtube_url=VIDEO_URL))
        self.assertIsNone(self._requested_at(detail))

        detail.youtube_url = OTHER_VIDEO_URL
        detail.save()

        self.assertIsNotNone(self._requested_at(detail))

    def test_marks_when_consent_changes_to_ok(self):
        detail = self._detail(article_consent=ArticleConsent.UNANSWERED, youtube_url=VIDEO_URL)
        self.assertIsNone(self._requested_at(detail))

        detail.article_consent = ArticleConsent.OK
        detail.save()

        self.assertIsNotNone(self._requested_at(detail))

    def test_resets_attempts_and_error_when_marked(self):
        detail = self._detail(article_generation_attempts=3, article_generation_last_error='no_source_text')

        detail.youtube_url = VIDEO_URL
        detail.save()

        detail.refresh_from_db()
        self.assertEqual(detail.article_generation_attempts, 0)
        self.assertEqual(detail.article_generation_last_error, '')

    def test_does_not_mark_unanswered_or_ng(self):
        for consent in (ArticleConsent.UNANSWERED, ArticleConsent.NG):
            with self.subTest(consent=consent):
                detail = self._detail(article_consent=consent)

                detail.youtube_url = VIDEO_URL
                detail.save()

                self.assertIsNone(self._requested_at(detail))

    def test_does_not_mark_manually_written_article(self):
        detail = self._detail(contents='発表者が書いた記事')

        detail.youtube_url = VIDEO_URL
        detail.save()

        self.assertIsNone(self._requested_at(detail))

    def test_does_not_mark_edited_auto_article(self):
        detail = self._as_generated(self._detail(youtube_url=VIDEO_URL))
        detail.contents = '発表者が直した本文'
        detail.save()

        detail.youtube_url = OTHER_VIDEO_URL
        detail.save()

        self.assertIsNone(self._requested_at(detail))

    def test_does_not_mark_when_input_is_removed(self):
        detail = self._as_generated(self._detail(youtube_url=VIDEO_URL, slide_file=SLIDE_NAME))

        detail.youtube_url = ''
        detail.save()

        self.assertIsNone(self._requested_at(detail))

    def test_does_not_mark_rejected_or_non_presentation(self):
        for extra in ({'status': 'rejected'}, {'detail_type': 'BLOG'}):
            with self.subTest(extra=extra):
                detail = self._detail(**extra)

                detail.youtube_url = VIDEO_URL
                detail.save()

                self.assertIsNone(self._requested_at(detail))

    def test_marks_when_rejected_presentation_is_approved(self):
        """却下から承認に変わって対象になった時も印を付ける（承認画面と同じ update_fields）。"""
        detail = self._detail(status='rejected', youtube_url=VIDEO_URL)
        self.assertIsNone(self._requested_at(detail))

        detail.status = 'approved'
        detail.save(update_fields=['status', 'updated_at'])

        self.assertIsNotNone(self._requested_at(detail))

    def test_marks_when_restored_from_soft_delete(self):
        """論理削除から復元して対象に戻った時も印を付ける（restore の update_fields は deleted_at だけ）。"""
        detail = self._detail(youtube_url=VIDEO_URL)
        detail.soft_delete()
        EventDetail.all_objects.filter(pk=detail.pk).update(article_generation_requested_at=None)

        detail.restore()

        self.assertIsNotNone(self._requested_at(detail))

    def test_marks_when_detail_type_becomes_presentation(self):
        detail = self._detail(detail_type='SPECIAL', youtube_url=VIDEO_URL)
        self.assertIsNone(self._requested_at(detail))

        detail.detail_type = 'LT'
        detail.save()

        self.assertIsNotNone(self._requested_at(detail))

    def test_does_not_mark_when_target_saves_without_input_change(self):
        """対象のまま入力も変わらない保存（テーマの修正など）では印を付けない。"""
        detail = self._as_generated(self._detail(youtube_url=VIDEO_URL))

        detail.theme = '直したテーマ'
        detail.save()

        self.assertIsNone(self._requested_at(detail))

    def test_marks_emptied_article_when_input_changes(self):
        """記事を空にした後に入力が変われば、ハッシュが残っていても作り直す（手動扱いで固定しない）。"""
        detail = self._as_generated(self._detail(youtube_url=VIDEO_URL))
        detail.h1, detail.contents = '', ''
        detail.save()

        detail.slide_file = SLIDE_NAME
        detail.save()

        self.assertIsNotNone(self._requested_at(detail))

    def test_discord_link_is_not_a_video(self):
        """youtube_url が Discord のメッセージリンクだけなら動画なしとして扱い、印を付けない。"""
        detail = self._detail(youtube_url='https://discord.com/channels/123456789012345678/234567890123456789')

        self.assertIsNone(detail.video_id)
        self.assertFalse(detail.can_auto_generate_article)
        self.assertIsNone(self._requested_at(detail))

    def test_same_video_with_timestamp_does_not_mark(self):
        """同じ動画の再生位置（?t=）を付け替えただけでは作り直さない。"""
        detail = self._as_generated(self._detail(youtube_url=VIDEO_URL))

        detail.youtube_url = f'{VIDEO_URL}&t=120'
        detail.save()

        self.assertIsNone(self._requested_at(detail))

    def test_unchanged_resave_after_form_round_trip_keeps_auto(self):
        """テーマだけ直して保存しても（本文は CRLF になる）、自動生成のままとして扱う。"""
        detail = self._as_generated(self._detail(youtube_url=VIDEO_URL))
        detail.theme = '直したテーマ'
        detail.contents = detail.contents.replace('\n', '\r\n') + '\r\n'
        detail.save()

        detail.slide_file = SLIDE_NAME
        detail.save()

        self.assertEqual(detail.article_state(), ArticleState.AUTO)
        self.assertIsNotNone(self._requested_at(detail))

    def test_marks_when_auto_article_is_emptied(self):
        """自動生成した記事のタイトルと本文を両方空にしたら、入力が変わらなくても作り直す。"""
        detail = self._as_generated(self._detail(youtube_url=VIDEO_URL))

        detail.h1, detail.contents = '', ''
        detail.save()

        self.assertIsNotNone(self._requested_at(detail))

    def test_marks_when_article_is_emptied_in_edit_form(self):
        """発表者の編集画面で記事の欄を空にして保存した時も作り直す（空白・改行だけも空とみなす）。"""
        detail = self._as_generated(self._detail(youtube_url=VIDEO_URL))
        opened = LTApplicationEditForm(instance=EventDetail.objects.get(pk=detail.pk))
        data = {
            'theme': detail.theme, 'speaker': detail.speaker, 'youtube_url': VIDEO_URL,
            'h1': ' ', 'contents': '\r\n', 'article_snapshot': opened['article_snapshot'].value(),
        }

        form = LTApplicationEditForm(data=data, instance=EventDetail.objects.get(pk=detail.pk))
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        self.assertIsNotNone(self._requested_at(detail))

    def test_marks_when_manually_written_article_is_emptied(self):
        """手で書いた記事を空にした時も、未作成と同じに扱って作る（article_state の判定と揃える）。"""
        detail = self._detail(youtube_url=VIDEO_URL)
        EventDetail.objects.filter(pk=detail.pk).update(article_generation_requested_at=None)
        detail.refresh_from_db()
        detail.h1, detail.contents = '発表者が書いた記事', '発表者が書いた本文'
        detail.save()
        self.assertIsNone(self._requested_at(detail))

        detail.h1, detail.contents = '', ''
        detail.save()

        self.assertIsNotNone(self._requested_at(detail))

    def test_marks_when_contents_is_emptied_with_update_fields(self):
        """本文だけの保存（update_fields=['contents']）で記事が空になった時も拾う。"""
        detail = self._as_generated(self._detail(youtube_url=VIDEO_URL))
        detail.h1 = ''
        detail.save(update_fields=['h1'])
        self.assertIsNone(self._requested_at(detail))

        detail.contents = ''
        detail.save(update_fields=['contents'])

        self.assertIsNotNone(self._requested_at(detail))

    def test_does_not_mark_when_empty_article_is_saved_again(self):
        """空のままの記事を保存し直しただけでは印を付けない（諦めた生成を保存のたびに数え直さない）。"""
        detail = self._detail(youtube_url=VIDEO_URL)
        EventDetail.objects.filter(pk=detail.pk).update(
            article_generation_requested_at=None, article_generation_attempts=MAX_ATTEMPTS,
        )
        detail.refresh_from_db()

        detail.theme = '直したテーマ'
        detail.save()

        self.assertIsNone(self._requested_at(detail))

    def test_stale_full_save_does_not_treat_article_as_emptied(self):
        """記事が空の時に読み込んだ古いインスタンスを保存しても、その後に作られた記事を空にしたとはみなさない。"""
        detail = self._detail(youtube_url=VIDEO_URL)
        EventDetail.objects.filter(pk=detail.pk).update(article_generation_requested_at=None)
        stale = EventDetail.objects.get(pk=detail.pk)
        self._as_generated(EventDetail.objects.get(pk=detail.pk))

        stale.theme = '直したテーマ'
        stale.save()

        self.assertIsNone(self._requested_at(detail))
        detail.refresh_from_db()
        self.assertEqual(detail.h1, '生成した記事')


@override_settings(GEMINI_MODEL='test-model')
@patch.dict('os.environ', {'OPENROUTER_API_KEY': 'test-key'}, clear=False)
@patch('event.services.article_generation.ensure_pdf_thumbnail', return_value=False)
@patch('event.notifications.send_mail', return_value=1)
@patch('event.services.content_generation_service._copy_uploaded_file_to_temp_path', return_value='/tmp/none.pdf')
@patch('event.services.content_generation_service._extract_pdf_text', return_value=PDF_TEXT)
@patch('event.services.content_generation_service.get_transcript', side_effect=_fake_transcript)
@patch('event.services.content_generation_service.OpenAI')
class ArticleGenerationQueueTest(TestCase):
    """待ち行列の処理（生成・手動編集の保護・再試行）。"""

    def setUp(self):
        self.applicant = make_user(user_name='speaker', email='speaker@example.com')
        self.event = make_event(make_community(name='生成の集会'), event_date=date.today() - timedelta(days=1))

    def _due_detail(self, **extra):
        """記事化 OK で、生成待ちの期限が来ている発表を作る。"""
        defaults = {
            'status': 'approved',
            'applicant': self.applicant,
            'article_consent': ArticleConsent.OK,
            'youtube_url': VIDEO_URL,
            'slide_file': SLIDE_NAME,
        }
        defaults.update(extra)
        detail = make_event_detail(self.event, **defaults)
        self._make_due(detail)
        return detail

    def _make_due(self, detail, minutes_ago=1):
        EventDetail.objects.filter(pk=detail.pk).update(
            article_generation_requested_at=timezone.now() - timedelta(minutes=minutes_ago),
        )

    def test_generates_article_from_video_and_pdf(self, openai_class, *_mocks):
        openai_class.return_value = _openrouter_client()
        send_mail, ensure_thumbnail = _mocks[3], _mocks[4]
        detail = self._due_detail()

        def thumbnail_after_commit(target, save=False):
            # サムネイル（ストレージへの書き込み）は記事の保存が確定した後に作る
            self.assertEqual(EventDetail.objects.get(pk=target.pk).h1, GENERATED['title'])
            self.assertTrue(save)
            return False

        ensure_thumbnail.side_effect = thumbnail_after_commit

        result = process_article_generation_queue()
        ensure_thumbnail.assert_called_once()

        self.assertEqual(result['generated'], 1)
        self.assertEqual(result['processed'], 1)
        self.assertEqual(result['pending'], 0)
        self.assertEqual(result['results'][0]['outcome'], 'generated')
        prompt = _sent_prompts(openai_class)[0]
        self.assertIn(TRANSCRIPT, prompt)
        self.assertIn(PDF_TEXT, prompt)

        detail.refresh_from_db()
        self.assertEqual(detail.h1, GENERATED['title'])
        self.assertEqual(detail.contents, GENERATED['text'])
        self.assertEqual(detail.meta_description, GENERATED['meta_description'])
        self.assertEqual(detail.article_source_video_id, VIDEO_ID)
        self.assertEqual(detail.article_source_slide_name, SLIDE_NAME)
        self.assertEqual(detail.article_body_hash, article_body_hash(detail.h1, detail.contents))
        self.assertIsNotNone(detail.article_generated_at)
        self.assertIsNone(detail.article_generation_requested_at)
        self.assertEqual(detail.article_state(), ArticleState.AUTO)

        send_mail.assert_called_once()
        self.assertEqual(send_mail.call_args.kwargs['recipient_list'], ['speaker@example.com'])
        self.assertIn('公開しました', send_mail.call_args.kwargs['subject'])
        edit_path = reverse('account:lt_application_edit', kwargs={'pk': detail.pk})
        self.assertIn(edit_path, send_mail.call_args.kwargs['html_message'])
        self.assertIn('確認・修正はこちら', send_mail.call_args.kwargs['html_message'])

    def test_regenerates_with_both_inputs_when_second_input_arrives(self, openai_class, *_mocks):
        """PDF だけで作った後に動画が来たら、字幕と PDF を合わせて作り直す。通知は最初の 1 回だけ。"""
        send_mail = _mocks[3]
        openai_class.return_value = _openrouter_client()
        detail = self._due_detail(youtube_url='')
        process_article_generation_queue()
        first_prompt = _sent_prompts(openai_class)[0]
        self.assertIn(PDF_TEXT, first_prompt)
        self.assertNotIn(TRANSCRIPT, first_prompt)

        detail.refresh_from_db()
        detail.youtube_url = VIDEO_URL
        detail.save()
        self._make_due(detail)
        openai_class.return_value = _openrouter_client({**GENERATED, 'text': '字幕も使って作り直した本文'})
        result = process_article_generation_queue()

        self.assertEqual(result['generated'], 1)
        second_prompt = _sent_prompts(openai_class)[0]
        self.assertIn(TRANSCRIPT, second_prompt)
        self.assertIn(PDF_TEXT, second_prompt)
        detail.refresh_from_db()
        self.assertEqual(detail.contents, '字幕も使って作り直した本文')
        self.assertEqual(detail.article_source_video_id, VIDEO_ID)
        send_mail.assert_called_once()
        self.assertIsNotNone(detail.article_published_notified_at)

    def test_waits_for_transcript_when_video_is_added(self, openai_class, get_transcript, extract_pdf_text,
                                                      copy_to_temp, *_mocks):
        """PDF で作った後に動画が来ても、字幕がまだ無ければ作り直さずに待つ（待つ時は PDF も読まない）。"""
        get_transcript.side_effect = lambda video_id, language='ja': None
        detail = self._due_detail(h1='PDF の記事', contents='PDF から作った本文', youtube_url='')
        fields = detail.record_generated_article(used_sources=('', SLIDE_NAME))
        detail.save(update_fields=fields)
        detail.youtube_url = VIDEO_URL
        detail.save()
        self._make_due(detail)

        result = process_article_generation_queue()

        self.assertEqual(result['deferred'], 1)
        self.assertEqual(result['results'][0]['reason'], 'waiting_for_transcript')
        openai_class.return_value.chat.completions.create.assert_not_called()
        copy_to_temp.assert_not_called()
        extract_pdf_text.assert_not_called()
        detail.refresh_from_db()
        self.assertEqual(detail.contents, 'PDF から作った本文')
        self.assertEqual(detail.article_generation_last_error, 'waiting_for_transcript')
        self.assertGreater(detail.article_generation_requested_at, timezone.now())

    def test_last_attempt_uses_pdf_only_and_does_not_record_video(self, openai_class, get_transcript, *_mocks):
        """字幕を上限まで待っても無ければ PDF だけで作り、字幕を使っていない動画は生成元に記録しない。"""
        get_transcript.side_effect = lambda video_id, language='ja': None
        openai_class.return_value = _openrouter_client()
        detail = self._due_detail()
        EventDetail.objects.filter(pk=detail.pk).update(article_generation_deferrals=MAX_DEFERRALS)

        result = process_article_generation_queue()

        self.assertEqual(result['generated'], 1)
        self.assertNotIn(TRANSCRIPT, _sent_prompts(openai_class)[0])
        detail.refresh_from_db()
        self.assertEqual(detail.article_source_video_id, '')
        self.assertEqual(detail.article_source_slide_name, SLIDE_NAME)

    def test_regenerates_when_transcript_appears_after_pdf_only_article(self, openai_class, get_transcript, *_mocks):
        """字幕なしで作った記事は「作り直し不要」扱いにせず、次の印で字幕も使って作り直す。"""
        openai_class.return_value = _openrouter_client()
        detail = self._due_detail(h1='PDF の記事', contents='PDF から作った本文')
        fields = detail.record_generated_article(used_sources=('', SLIDE_NAME))
        detail.save(update_fields=fields)
        self._make_due(detail)

        result = process_article_generation_queue()

        self.assertEqual(result['generated'], 1)
        self.assertIn(TRANSCRIPT, _sent_prompts(openai_class)[0])
        detail.refresh_from_db()
        self.assertEqual(detail.article_source_video_id, VIDEO_ID)

    def test_does_not_overwrite_manually_edited_article(self, openai_class, *_mocks):
        openai_class.return_value = _openrouter_client()
        detail = self._due_detail(h1='生成した記事', contents='生成した本文')
        fields = detail.record_generated_article()
        detail.save(update_fields=fields)
        detail.contents = '発表者が直した本文'
        detail.save()
        self._make_due(detail)

        result = process_article_generation_queue()

        self.assertEqual(result['skipped_manual'], 1)
        self.assertEqual(result['results'][0]['reason'], 'manual_edit')
        openai_class.return_value.chat.completions.create.assert_not_called()
        detail.refresh_from_db()
        self.assertEqual(detail.contents, '発表者が直した本文')
        self.assertIsNone(detail.article_generation_requested_at)

    def test_edit_during_generation_is_not_overwritten(self, openai_class, *_mocks):
        """生成中に発表者が本文を書いたら、生成結果で上書きしない。"""
        client = _openrouter_client()
        detail = self._due_detail()
        completion = client.chat.completions.create.return_value

        def edit_then_respond(*args, **kwargs):
            EventDetail.objects.filter(pk=detail.pk).update(contents='生成中に書いた本文')
            return completion

        client.chat.completions.create.side_effect = edit_then_respond
        openai_class.return_value = client

        result = process_article_generation_queue()

        self.assertEqual(result['skipped_manual'], 1)
        detail.refresh_from_db()
        self.assertEqual(detail.contents, '生成中に書いた本文')
        self.assertEqual(detail.h1, '')
        # 書き込まなかった時はサムネイルも作らない（ストレージに孤児を残さない）
        _mocks[4].assert_not_called()
        # 通知も送らない
        _mocks[3].assert_not_called()

    def test_new_input_during_generation_is_processed_next_time(self, openai_class, *_mocks):
        """生成中に新しい入力が来たら書き込まず、新しい印を残す。"""
        client = _openrouter_client()
        detail = self._due_detail(youtube_url='')
        completion = client.chat.completions.create.return_value

        def upload_then_respond(*args, **kwargs):
            current = EventDetail.objects.get(pk=detail.pk)
            current.youtube_url = VIDEO_URL
            current.save()
            return completion

        client.chat.completions.create.side_effect = upload_then_respond
        openai_class.return_value = client

        result = process_article_generation_queue()

        self.assertEqual(result['skipped'], 1)
        self.assertEqual(result['results'][0]['reason'], 'superseded')
        detail.refresh_from_db()
        self.assertEqual(detail.h1, '')
        self.assertIsNotNone(detail.article_generation_requested_at)
        _mocks[4].assert_not_called()

    def test_input_removed_during_generation_is_not_stored(self, openai_class, *_mocks):
        """生成中に PDF が外されたら、外した PDF で作った記事は書かず、生成待ちに戻して残った入力で作り直す。"""
        client = _openrouter_client()
        detail = self._due_detail()
        completion = client.chat.completions.create.return_value

        def remove_slide_then_respond(*args, **kwargs):
            current = EventDetail.objects.get(pk=detail.pk)
            current.slide_file = ''
            current.save()
            return completion

        client.chat.completions.create.side_effect = remove_slide_then_respond
        openai_class.return_value = client

        result = process_article_generation_queue()

        self.assertEqual(result['skipped'], 1)
        self.assertEqual(result['results'][0]['reason'], 'inputs_changed')
        detail.refresh_from_db()
        self.assertEqual(detail.h1, '')
        self.assertIsNotNone(detail.article_generation_requested_at)
        self.assertLessEqual(detail.article_generation_requested_at, timezone.now())
        self.assertEqual(detail.article_generation_attempts, 0)
        _mocks[4].assert_not_called()
        _mocks[3].assert_not_called()

        client.chat.completions.create.side_effect = None
        result = process_article_generation_queue()

        self.assertEqual(result['generated'], 1)
        self.assertNotIn(PDF_TEXT, _sent_prompts(openai_class)[-1])
        detail.refresh_from_db()
        self.assertEqual((detail.article_source_video_id, detail.article_source_slide_name), (VIDEO_ID, ''))

    def test_claimed_then_deleted_before_reload_is_skipped(self, openai_class, *_mocks):
        """取った直後に論理削除されても 500 にせず、対象外としてスキップする。"""
        detail = self._due_detail()
        real_try_claim = article_generation._try_claim

        def claim_then_delete(pk, requested_at, lease_until):
            claimed = real_try_claim(pk, requested_at, lease_until)
            EventDetail.objects.filter(pk=pk).update(deleted_at=timezone.now())
            return claimed

        with patch.object(article_generation, '_try_claim', side_effect=claim_then_delete):
            result = process_article_generation_queue()

        self.assertEqual(result['skipped'], 1)
        self.assertEqual(result['results'][0]['reason'], 'not_eligible')
        openai_class.return_value.chat.completions.create.assert_not_called()
        self.assertIsNone(EventDetail.all_objects.get(pk=detail.pk).article_generation_requested_at)

    def test_deleted_during_generation_is_skipped(self, openai_class, *_mocks):
        """生成中に発表が論理削除されたら書き込まず、印を外して呼び出しを正常に終える。"""
        client = _openrouter_client()
        detail = self._due_detail()
        completion = client.chat.completions.create.return_value

        def delete_then_respond(*args, **kwargs):
            EventDetail.objects.get(pk=detail.pk).soft_delete()
            return completion

        client.chat.completions.create.side_effect = delete_then_respond
        openai_class.return_value = client

        result = process_article_generation_queue()

        self.assertEqual(result['skipped'], 1)
        self.assertEqual(result['results'][0]['reason'], 'not_eligible')
        deleted = EventDetail.all_objects.get(pk=detail.pk)
        self.assertEqual(deleted.h1, '')
        self.assertIsNone(deleted.article_generation_requested_at)

    def test_skips_when_already_generated_from_same_inputs(self, openai_class, *_mocks):
        openai_class.return_value = _openrouter_client()
        detail = self._due_detail(h1='生成した記事', contents='生成した本文')
        fields = detail.record_generated_article()
        detail.save(update_fields=fields)
        self._make_due(detail)

        result = process_article_generation_queue()

        self.assertEqual(result['skipped'], 1)
        self.assertEqual(result['results'][0]['reason'], 'unchanged')
        openai_class.return_value.chat.completions.create.assert_not_called()

    def test_skips_and_clears_when_no_longer_eligible(self, openai_class, *_mocks):
        detail = self._due_detail()
        EventDetail.objects.filter(pk=detail.pk).update(article_consent=ArticleConsent.NG)

        result = process_article_generation_queue()

        self.assertEqual(result['skipped'], 1)
        self.assertEqual(result['results'][0]['reason'], 'not_eligible')
        openai_class.return_value.chat.completions.create.assert_not_called()
        detail.refresh_from_db()
        self.assertIsNone(detail.article_generation_requested_at)

    def test_failure_records_error_and_retries_later(self, openai_class, get_transcript, extract_pdf_text, *_mocks):
        """入力のテキストが取れない時（文字の無い PDF など）は記事を作らず、間を空けて再試行する。"""
        extract_pdf_text.return_value = ''
        detail = self._due_detail(youtube_url='')

        result = process_article_generation_queue()

        self.assertEqual(result['failed'], 1)
        self.assertFalse(result['results'][0]['gave_up'])
        openai_class.return_value.chat.completions.create.assert_not_called()
        detail.refresh_from_db()
        self.assertEqual(detail.article_generation_attempts, 1)
        self.assertEqual(detail.article_generation_last_error, 'no_source_text')
        self.assertGreater(detail.article_generation_requested_at, timezone.now())
        self.assertEqual(process_article_generation_queue()['processed'], 0)

    def test_empty_llm_output_is_a_failure(self, openai_class, *_mocks):
        client = MagicMock()
        client.chat.completions.create.return_value.choices = [
            MagicMock(message=MagicMock(content=None, tool_calls=None))
        ]
        openai_class.return_value = client
        detail = self._due_detail()

        result = process_article_generation_queue()

        self.assertEqual(result['failed'], 1)
        detail.refresh_from_db()
        self.assertEqual(detail.article_generation_last_error, 'empty_output')
        self.assertEqual(detail.h1, '')

    def test_gives_up_after_max_attempts(self, openai_class, get_transcript, *_mocks):
        """字幕を待ちきった後、入力が無いまま失敗が上限に達したら諦めて印を外す。"""
        get_transcript.side_effect = lambda video_id, language='ja': None
        detail = self._due_detail(slide_file='')
        EventDetail.objects.filter(pk=detail.pk).update(
            article_generation_attempts=MAX_ATTEMPTS - 1, article_generation_deferrals=MAX_DEFERRALS,
        )

        result = process_article_generation_queue()

        self.assertTrue(result['results'][0]['gave_up'])
        detail.refresh_from_db()
        self.assertEqual(detail.article_generation_attempts, MAX_ATTEMPTS)
        self.assertEqual(detail.article_generation_last_error, 'no_source_text')
        self.assertIsNone(detail.article_generation_requested_at)

    def test_processes_oldest_first_up_to_limit(self, openai_class, *_mocks):
        openai_class.return_value = _openrouter_client()
        details = [self._due_detail(theme=f'発表{i}') for i in range(3)]
        for minutes_ago, detail in zip((30, 20, 10), details):
            self._make_due(detail, minutes_ago=minutes_ago)

        result = process_article_generation_queue(limit=2)

        self.assertEqual(result['processed'], 2)
        self.assertEqual(result['pending'], 1)
        self.assertEqual(
            [r['event_detail_id'] for r in result['results']],
            [details[0].pk, details[1].pk],
        )

    def test_claimed_item_is_not_picked_by_another_run(self, openai_class, *_mocks):
        """処理中（締切が未来）のものは、重なった別の呼び出しで拾わない。"""
        self._due_detail()

        claimed = article_generation._claim_next(timezone.now())

        self.assertIsNotNone(claimed)
        self.assertIsNone(article_generation._claim_next(timezone.now()))

    def test_long_llm_title_and_summary_are_truncated(self, openai_class, *_mocks):
        """LLM が列より長いタイトル・要約を返しても、切り詰めて保存する（MySQL の DataError を防ぐ）。"""
        openai_class.return_value = _openrouter_client(
            {**GENERATED, 'title': 'タ' * 300, 'meta_description': '要' * 300},
        )
        detail = self._due_detail()

        result = process_article_generation_queue()

        self.assertEqual(result['generated'], 1)
        detail.refresh_from_db()
        self.assertEqual(len(detail.h1), 255)
        self.assertEqual(len(detail.meta_description), 255)
        self.assertEqual(detail.article_state(), ArticleState.AUTO)

    def test_error_in_one_item_does_not_stop_the_batch(self, openai_class, *_mocks):
        """1 件目の保存で DB エラーが出ても失敗として記録し、2 件目へ進む（リースのまま残さない）。"""
        openai_class.return_value = _openrouter_client()
        first = self._due_detail(theme='1 件目')
        second = self._due_detail(theme='2 件目')
        self._make_due(first, minutes_ago=20)
        self._make_due(second, minutes_ago=10)
        real_store = article_generation._store_article

        def fail_first(pk, *args):
            if pk == first.pk:
                raise DatabaseError('boom')
            return real_store(pk, *args)

        with patch.object(article_generation, '_store_article', side_effect=fail_first):
            result = process_article_generation_queue(limit=2)

        self.assertEqual([r['outcome'] for r in result['results']], ['failed', 'generated'])
        self.assertEqual(result['results'][0]['reason'], 'error:DatabaseError')
        first.refresh_from_db()
        self.assertEqual(first.article_generation_last_error, 'error:DatabaseError')
        # 処理中の締切（15 分後）ではなく、再試行の時刻（10 分後）が入っている
        self.assertLess(first.article_generation_requested_at, timezone.now() + timedelta(minutes=12))

    def test_unexpected_error_is_recorded_as_failure(self, openai_class, *_mocks):
        detail = self._due_detail()

        with patch.object(article_generation, '_process_claimed', side_effect=DatabaseError('boom')):
            result = process_article_generation_queue()

        self.assertEqual(result['failed'], 1)
        detail.refresh_from_db()
        self.assertEqual(detail.article_generation_last_error, 'error:DatabaseError')
        self.assertLess(detail.article_generation_requested_at, timezone.now() + timedelta(minutes=12))

    def test_vket_presentation_notifies_applied_by_user(self, openai_class, *_mocks):
        """applicant の無い Vket 由来の発表は、申し込んだ人に知らせる（資料リマインドと同じ宛先）。"""
        send_mail = _mocks[3]
        openai_class.return_value = _openrouter_client()
        organizer = make_user(user_name='vket_owner', email='vket_owner@example.com')
        detail = self._due_detail(applicant=None)
        _link_vket_presentation(detail, organizer)

        process_article_generation_queue()

        self.assertEqual(send_mail.call_args.kwargs['recipient_list'], ['vket_owner@example.com'])
        detail.refresh_from_db()
        self.assertIsNotNone(detail.article_published_notified_at)

    def test_without_recipient_notified_at_stays_empty(self, openai_class, *_mocks):
        """宛先が無い時は送らず、通知日時も入れない（後で宛先ができた時に知らせられる）。"""
        send_mail = _mocks[3]
        openai_class.return_value = _openrouter_client()
        detail = self._due_detail(applicant=None)

        result = process_article_generation_queue()

        self.assertEqual(result['generated'], 1)
        send_mail.assert_not_called()
        detail.refresh_from_db()
        self.assertIsNone(detail.article_published_notified_at)

    def test_second_item_is_not_started_after_time_budget(self, openai_class, *_mocks):
        """1 件目を終えた時点で時間の予算を過ぎていたら、limit=2 でも 2 件目は始めない。"""
        openai_class.return_value = _openrouter_client()
        self._due_detail(theme='1 件目')
        self._due_detail(theme='2 件目')

        with patch.object(article_generation, '_monotonic', side_effect=[0.0, 31.0]):
            result = process_article_generation_queue(limit=2)

        self.assertEqual(result['processed'], 1)
        self.assertEqual(result['pending'], 1)

    def test_second_item_runs_within_time_budget(self, openai_class, *_mocks):
        openai_class.return_value = _openrouter_client()
        self._due_detail(theme='1 件目')
        self._due_detail(theme='2 件目')

        with patch.object(article_generation, '_monotonic', side_effect=[0.0, 10.0]):
            result = process_article_generation_queue(limit=2)

        self.assertEqual(result['processed'], 2)

    def test_deferral_is_not_counted_as_a_failed_attempt(self, openai_class, get_transcript, *_mocks):
        """字幕待ちの見送りは失敗の回数に数えず、見送りの回数として数える。"""
        get_transcript.side_effect = lambda video_id, language='ja': None
        detail = self._due_detail(slide_file='')

        result = process_article_generation_queue()

        self.assertEqual(result['deferred'], 1)
        self.assertEqual(result['results'][0]['deferrals'], 1)
        detail.refresh_from_db()
        self.assertEqual(detail.article_generation_attempts, 0)
        self.assertEqual(detail.article_generation_deferrals, 1)

    def test_failure_after_waiting_for_transcript_is_retried(self, openai_class, get_transcript, *_mocks):
        """字幕を待ちきった後の本当の生成で 1 回失敗しても、すぐには諦めずに再試行する。"""
        get_transcript.side_effect = lambda video_id, language='ja': None
        client = MagicMock()
        client.chat.completions.create.return_value.choices = [
            MagicMock(message=MagicMock(content=None, tool_calls=None))
        ]
        openai_class.return_value = client
        detail = self._due_detail()
        EventDetail.objects.filter(pk=detail.pk).update(article_generation_deferrals=MAX_DEFERRALS)

        result = process_article_generation_queue()

        self.assertEqual(result['failed'], 1)
        self.assertFalse(result['results'][0]['gave_up'])
        detail.refresh_from_db()
        self.assertEqual(detail.article_generation_attempts, 1)
        self.assertGreater(detail.article_generation_requested_at, timezone.now())

    def test_transcript_is_fetched_once_on_the_last_wait(self, openai_class, get_transcript, *_mocks):
        """字幕を待ちきって PDF だけで作る回でも、字幕は 1 回しか取りに行かない。"""
        get_transcript.side_effect = lambda video_id, language='ja': None
        openai_class.return_value = _openrouter_client()
        detail = self._due_detail()
        EventDetail.objects.filter(pk=detail.pk).update(article_generation_deferrals=MAX_DEFERRALS)

        result = process_article_generation_queue()

        self.assertEqual(result['generated'], 1)
        self.assertEqual(get_transcript.call_count, 1)

    def test_failed_email_is_sent_again_next_time(self, openai_class, *_mocks):
        """メールを送れなかったら通知日時を戻し、次に記事を作った時に送り直す。Discord も送れた時だけ。"""
        send_mail = _mocks[3]
        send_mail.return_value = 0
        openai_class.return_value = _openrouter_client()
        detail = self._due_detail(youtube_url='')

        process_article_generation_queue()

        detail.refresh_from_db()
        self.assertIsNone(detail.article_published_notified_at)

        send_mail.return_value = 1
        detail.youtube_url = VIDEO_URL
        detail.save()
        self._make_due(detail)
        process_article_generation_queue()

        self.assertEqual(send_mail.call_count, 2)
        detail.refresh_from_db()
        self.assertIsNotNone(detail.article_published_notified_at)

    def test_does_not_notify_when_no_longer_published_before_sending(self, openai_class, *_mocks):
        """記事を書いた後、知らせる前に NG・却下・論理削除へ変わったら知らせず、通知日時も入れない。"""
        send_mail, thumbnail = _mocks[3], _mocks[4]
        openai_class.return_value = _openrouter_client()
        changes = {
            'ng': {'article_consent': ArticleConsent.NG},
            'rejected': {'status': 'rejected'},
            'deleted': {'deleted_at': timezone.now()},
        }
        for label, change in changes.items():
            with self.subTest(label):
                detail = self._due_detail(theme=label)

                def change_while_making_thumbnail(*args, pk=detail.pk, change=change, **kwargs):
                    EventDetail.all_objects.filter(pk=pk).update(**change)
                    return False

                thumbnail.side_effect = change_while_making_thumbnail

                result = process_article_generation_queue()

                self.assertEqual(result['generated'], 1)
                send_mail.assert_not_called()
                notified_at = EventDetail.all_objects.get(pk=detail.pk).article_published_notified_at
                self.assertIsNone(notified_at)

    def test_notification_uses_row_read_right_before_sending(self, openai_class, *_mocks):
        """宛先は知らせる直前に読み直した行で決める（サムネイルを作る間に変わったメールアドレスへ送る）。"""
        send_mail, thumbnail = _mocks[3], _mocks[4]
        openai_class.return_value = _openrouter_client()
        self._due_detail()

        def change_email_while_making_thumbnail(*args, **kwargs):
            get_user_model().objects.filter(pk=self.applicant.pk).update(email='changed@example.com')
            return False

        thumbnail.side_effect = change_email_while_making_thumbnail

        process_article_generation_queue()

        self.assertEqual(send_mail.call_args.kwargs['recipient_list'], ['changed@example.com'])


class FullSaveProtectionTest(TestCase):
    """承認・管理画面・API などのフル保存は、生成管理の列と、読み込み時から変えていない記事の列を書かない。"""

    def setUp(self):
        self.detail = make_event_detail(
            make_event(make_community(name='フル保存の集会')), status='pending', theme='テーマ',
        )

    def test_full_save_does_not_write_back_generation_columns(self):
        stale = EventDetail.objects.get(pk=self.detail.pk)
        notified = timezone.now()
        EventDetail.objects.filter(pk=self.detail.pk).update(
            article_generation_requested_at=notified, article_generation_attempts=2,
            article_body_hash='x' * 64, article_published_notified_at=notified,
        )

        stale.status = 'approved'
        stale.save()

        self.detail.refresh_from_db()
        self.assertEqual(self.detail.status, 'approved')
        self.assertIsNotNone(self.detail.article_generation_requested_at)
        self.assertEqual(self.detail.article_generation_attempts, 2)
        self.assertEqual(self.detail.article_body_hash, 'x' * 64)
        self.assertIsNotNone(self.detail.article_published_notified_at)

    def test_full_save_does_not_write_back_unchanged_article(self):
        """読み込んだ後にキューが作った記事を、記事を変えていない古いインスタンスの保存で戻さない。"""
        stale = EventDetail.objects.get(pk=self.detail.pk)
        EventDetail.objects.filter(pk=self.detail.pk).update(
            h1='キューが作った記事', contents='キューが作った本文', meta_description='キューが作った要約',
        )

        stale.theme = '直したテーマ'
        stale.save()

        self.detail.refresh_from_db()
        self.assertEqual(self.detail.theme, '直したテーマ')
        self.assertEqual(self.detail.h1, 'キューが作った記事')
        self.assertEqual(self.detail.contents, 'キューが作った本文')
        self.assertEqual(self.detail.meta_description, 'キューが作った要約')

    def test_full_save_writes_article_changed_in_memory(self):
        stale = EventDetail.objects.get(pk=self.detail.pk)

        stale.contents = '手で直した本文'
        stale.save()
        stale.theme = 'もう一度保存'
        stale.save()

        self.detail.refresh_from_db()
        self.assertEqual(self.detail.contents, '手で直した本文')
        self.assertEqual(self.detail.theme, 'もう一度保存')

    def test_insert_writes_every_column(self):
        detail = make_event_detail(
            make_event(make_community(name='新規の集会')),
            h1='新規の記事', article_generation_attempts=3,
        )

        detail.refresh_from_db()
        self.assertEqual(detail.h1, '新規の記事')
        self.assertEqual(detail.article_generation_attempts, 3)


@override_settings(GEMINI_MODEL='test-model')
@patch.dict('os.environ', {'OPENROUTER_API_KEY': 'test-key'}, clear=False)
@patch('event.services.content_generation_service.ensure_pdf_thumbnail', return_value=False)
@patch('event.services.content_generation_service._copy_uploaded_file_to_temp_path', return_value='/tmp/none.pdf')
@patch('event.services.content_generation_service._extract_pdf_text', return_value='')
@patch('event.services.content_generation_service.get_transcript', side_effect=_fake_transcript)
@patch('event.services.content_generation_service.OpenAI')
class GeneratedSourcesTest(TestCase):
    """生成ボタン等の保存も、generate_blog が実際に中身を使えた入力だけを生成元に記録する。"""

    def test_slide_whose_text_could_not_be_read_is_not_recorded(self, openai_class, *_mocks):
        openai_class.return_value = _openrouter_client()
        detail = make_event_detail(
            make_event(make_community(name='生成元の集会')), status='approved',
            youtube_url=VIDEO_URL, slide_file=SLIDE_NAME,
        )

        blog_output = generate_blog(detail, model='test-model')
        save_generated_article(detail, blog_output)

        self.assertIsNotNone(blog_output.sources)
        detail.refresh_from_db()
        self.assertEqual(detail.article_source_video_id, VIDEO_ID)
        self.assertEqual(detail.article_source_slide_name, '')

    def test_sources_are_not_part_of_the_llm_schema(self, *_mocks):
        """生成に使った入力は BlogOutput に持たせるが、LLM に渡す関数のスキーマには出さない。"""
        self.assertEqual(
            set(BlogOutput.model_json_schema()['properties']), {'title', 'meta_description', 'text'},
        )


class StaleEditFormTest(TestCase):
    """編集画面を開いた後にキューが記事を作っても、テーマを直して保存しただけで記事を消さない。"""

    def setUp(self):
        self.detail = make_event_detail(
            make_event(make_community(name='古い画面の集会')), status='approved',
            article_consent=ArticleConsent.OK,
        )

    def _open_then_generate(self):
        """画面を開き（hidden の値を控え）、その後にキューが記事を作る。"""
        opened = LTApplicationEditForm(instance=EventDetail.objects.get(pk=self.detail.pk))
        snapshot = opened['article_snapshot'].value()
        EventDetail.objects.filter(pk=self.detail.pk).update(
            h1='キューが作った記事', contents='キューが作った本文', meta_description='キューが作った要約',
        )
        return snapshot

    def _submit(self, snapshot, **extra):
        data = {'theme': '直したテーマ', 'speaker': '発表者', 'h1': '', 'contents': '', 'article_snapshot': snapshot}
        data.update(extra)
        form = LTApplicationEditForm(data=data, instance=EventDetail.objects.get(pk=self.detail.pk))
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.detail.refresh_from_db()

    def test_article_generated_after_opening_is_kept(self):
        self._submit(self._open_then_generate())

        self.assertEqual(self.detail.theme, '直したテーマ')
        self.assertEqual(self.detail.h1, 'キューが作った記事')
        self.assertEqual(self.detail.contents, 'キューが作った本文')
        self.assertEqual(self.detail.meta_description, 'キューが作った要約')

    def test_user_edit_of_article_wins(self):
        """利用者が記事の欄を実際に変えた時は、利用者の編集を優先して書く。"""
        self._submit(self._open_then_generate(), contents='利用者が書いた本文')

        self.assertEqual(self.detail.contents, '利用者が書いた本文')

    def test_form_without_snapshot_writes_as_before(self):
        """hidden の無い古い画面（デプロイ前に開いた画面）は今までどおり書く。"""
        self._open_then_generate()

        self._submit('', contents='古い画面の本文')

        self.assertEqual(self.detail.contents, '古い画面の本文')

    def test_snapshot_is_rendered_as_hidden_input(self):
        rendered = str(LTApplicationEditForm(instance=self.detail)['article_snapshot'])

        self.assertIn('type="hidden"', rendered)
        self.assertIn(article_body_hash('', ''), rendered)


@patch('event.services.content_generation_service.ensure_pdf_thumbnail', return_value=False)
class SaveGeneratedArticleTest(TestCase):
    """生成ボタン・保存と同時の生成の保存は、記事の列だけを書き、NG に変わっていたら書かない。"""

    OUTPUT = BlogOutput(title='生成した記事', meta_description='要約', text='本文')

    def setUp(self):
        self.owner = make_user(user_name='save_owner', email='save_owner@example.com')
        self.detail = make_event_detail(
            make_event(make_community(name='保存の集会', owner=self.owner)),
            status='approved',
            youtube_url=VIDEO_URL,
        )

    def test_does_not_write_back_columns_changed_while_generating(self, _thumbnail):
        stale = EventDetail.objects.get(pk=self.detail.pk)
        EventDetail.objects.filter(pk=self.detail.pk).update(
            theme='生成中に直したテーマ', article_published_notified_at=timezone.now(),
        )

        self.assertEqual(save_generated_article(stale, self.OUTPUT), SAVED)

        self.detail.refresh_from_db()
        self.assertEqual(self.detail.h1, '生成した記事')
        self.assertEqual(self.detail.theme, '生成中に直したテーマ')
        self.assertIsNotNone(self.detail.article_published_notified_at)

    def test_refuses_when_consent_became_ng_while_generating(self, _thumbnail):
        stale = EventDetail.objects.get(pk=self.detail.pk)
        EventDetail.objects.filter(pk=self.detail.pk).update(article_consent=ArticleConsent.NG)

        self.assertEqual(save_generated_article(stale, self.OUTPUT), REFUSED)

        self.detail.refresh_from_db()
        self.assertEqual(self.detail.h1, '')
        self.assertEqual(self.detail.article_consent, ArticleConsent.NG)

    def test_truncates_long_title_and_summary(self, _thumbnail):
        output = BlogOutput(title='タ' * 300, meta_description='要' * 300, text='本文')

        save_generated_article(self.detail, output)

        self.detail.refresh_from_db()
        self.assertEqual(len(self.detail.h1), 255)
        self.assertEqual(len(self.detail.meta_description), 255)
        self.assertEqual(self.detail.article_state(), ArticleState.AUTO)

    @patch('event.views.blog.generate_blog')
    def test_generate_button_does_not_publish_when_consent_became_ng(self, mock_generate_blog, _thumbnail):
        """生成ボタンを押した後、生成を待つ間に NG へ変えられたら、同意を戻さず記事も書かない。"""
        def flip_to_ng(*args, **kwargs):
            EventDetail.objects.filter(pk=self.detail.pk).update(article_consent=ArticleConsent.NG)
            return self.OUTPUT

        mock_generate_blog.side_effect = flip_to_ng
        self.client.force_login(self.owner)

        self.client.post(reverse('event:generate_blog', kwargs={'pk': self.detail.pk}))

        self.detail.refresh_from_db()
        self.assertEqual(self.detail.article_consent, ArticleConsent.NG)
        self.assertEqual(self.detail.h1, '')

    def _output_from(self, video_id):
        """generate_blog が video_id の字幕で作った結果（生成に使った入力を持つ）。"""
        sources = BlogSources(transcript=TRANSCRIPT, pdf_content='', pdf_url='', video_id=video_id)
        return _with_sources(BlogOutput(title='生成した記事', meta_description='要約', text='本文'), sources)

    def _make_auto_target(self):
        """記事化 OK で生成待ちの印が無い発表にする。"""
        EventDetail.objects.filter(pk=self.detail.pk).update(
            article_consent=ArticleConsent.OK, article_generation_requested_at=None,
        )

    def test_does_not_overwrite_article_edited_while_generating(self, _thumbnail):
        """生成を待つ間に記事が書き換えられたら、書き換えた内容を優先して保存しない。"""
        started = EventDetail.objects.get(pk=self.detail.pk)
        EventDetail.objects.filter(pk=self.detail.pk).update(contents='生成中に書いた本文')

        self.assertEqual(save_generated_article(started, self.OUTPUT), EDITED)

        self.detail.refresh_from_db()
        self.assertEqual(self.detail.contents, '生成中に書いた本文')
        self.assertEqual(self.detail.h1, '')
        self.assertIsNone(self.detail.article_generated_at)
        _thumbnail.assert_not_called()

    def test_inputs_changed_while_generating_leaves_regeneration_to_queue(self, _thumbnail):
        """生成を待つ間に動画が差し替わったら、記事は書くが生成元は記録せず、生成待ちの印を付ける。"""
        self._make_auto_target()
        started = EventDetail.objects.get(pk=self.detail.pk)
        EventDetail.objects.filter(pk=self.detail.pk).update(youtube_url=OTHER_VIDEO_URL)

        self.assertEqual(save_generated_article(started, self._output_from(VIDEO_ID)), SAVED)

        self.detail.refresh_from_db()
        self.assertEqual(self.detail.h1, '生成した記事')
        # 自動生成のままとして扱う（手動扱いにするとキューが作り直さない）
        self.assertEqual(self.detail.article_state(), ArticleState.AUTO)
        self.assertEqual((self.detail.article_source_video_id, self.detail.article_source_slide_name), ('', ''))
        self.assertIsNotNone(self.detail.article_generation_requested_at)
        self.assertEqual(article_generation._skip_reason(self.detail), '')

    def test_inputs_changed_while_generating_keeps_existing_mark(self, _thumbnail):
        """入力が変わった時の生成待ちの印（処理中の締切を含む）は外さず、そのまま残す。"""
        self._make_auto_target()
        started = EventDetail.objects.get(pk=self.detail.pk)
        lease_until = timezone.now() + timedelta(minutes=15)
        EventDetail.objects.filter(pk=self.detail.pk).update(
            youtube_url=OTHER_VIDEO_URL, article_generation_requested_at=lease_until,
        )

        self.assertEqual(save_generated_article(started, self._output_from(VIDEO_ID)), SAVED)

        self.detail.refresh_from_db()
        self.assertEqual(self.detail.article_generation_requested_at, lease_until)

    def test_same_inputs_record_sources_and_clear_mark(self, _thumbnail):
        """入力が変わっていなければ、これまでどおり生成元を記録して印を外す。"""
        self._make_auto_target()
        EventDetail.objects.filter(pk=self.detail.pk).update(article_generation_requested_at=timezone.now())
        started = EventDetail.objects.get(pk=self.detail.pk)

        self.assertEqual(save_generated_article(started, self._output_from(VIDEO_ID)), SAVED)

        self.detail.refresh_from_db()
        self.assertEqual(self.detail.article_source_video_id, VIDEO_ID)
        self.assertIsNone(self.detail.article_generation_requested_at)

    @patch('event.views.blog.generate_blog')
    def test_generate_button_keeps_article_edited_while_generating(self, mock_generate_blog, _thumbnail):
        """生成ボタンを押した後、生成を待つ間に記事が編集されたら、編集を残して理由を伝える。"""
        def edit_then_respond(*args, **kwargs):
            EventDetail.objects.filter(pk=self.detail.pk).update(contents='生成中に書いた本文')
            return self.OUTPUT

        mock_generate_blog.side_effect = edit_then_respond
        self.client.force_login(self.owner)

        response = self.client.post(reverse('event:generate_blog', kwargs={'pk': self.detail.pk}))

        self.detail.refresh_from_db()
        self.assertEqual(self.detail.contents, '生成中に書いた本文')
        sent = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertEqual(sent, [ARTICLE_EDITED_MESSAGE])


@patch('event.services.content_generation_service.ensure_pdf_thumbnail', return_value=False)
@patch('event.services.content_generation_service.generate_blog')
class StaleFormGenerateTest(TestCase):
    """画面を開いた後に記事が作られた発表でも、保存と同時の生成は「生成中に編集された」と誤判定しない。"""

    def setUp(self):
        self.user = make_discord_linked_user(user_name='stale_speaker', email='stale_speaker@example.com')
        self.detail = make_event_detail(
            make_event(make_community(name='古い画面で生成する集会')),
            applicant=self.user,
            status='approved',
            youtube_url=VIDEO_URL,
        )
        self.client.force_login(self.user)

    def test_generate_checkbox_after_article_was_made_elsewhere(self, mock_generate_blog, _thumbnail):
        url = reverse('account:lt_application_edit', kwargs={'pk': self.detail.pk})
        snapshot = LTApplicationEditForm(instance=EventDetail.objects.get(pk=self.detail.pk))['article_snapshot']
        EventDetail.objects.filter(pk=self.detail.pk).update(h1='別の画面で作った記事', contents='別の画面で作った本文')
        mock_generate_blog.return_value = BlogOutput(title='生成した記事', meta_description='要約', text='本文')

        response = self.client.post(url, {
            'theme': '直したテーマ', 'speaker': '発表者', 'youtube_url': VIDEO_URL,
            'h1': '', 'contents': '', 'article_snapshot': snapshot.value(), 'generate_blog_article': 'on',
        })

        self.assertEqual(response.status_code, 302)
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.theme, '直したテーマ')
        self.assertEqual(self.detail.h1, '生成した記事')
        sent = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertNotIn(ARTICLE_EDITED_MESSAGE, ''.join(sent))


def _link_vket_presentation(detail: EventDetail, applied_by) -> None:
    """applicant の無い Vket 由来の発表にする（申し込んだ人が発表の持ち主になる）。"""
    collaboration = VketCollaboration.objects.create(
        slug=f'article-vket-{detail.pk}',
        name='記事の Vket',
        period_start=date.today() - timedelta(days=2),
        period_end=date.today() + timedelta(days=2),
        registration_deadline=date.today() - timedelta(days=30),
        lt_deadline=date.today() - timedelta(days=10),
    )
    participation = VketParticipation.objects.create(
        collaboration=collaboration, community=detail.event.community, applied_by=applied_by,
    )
    VketPresentation.objects.create(
        participation=participation,
        published_event_detail=detail,
        status=VketPresentation.Status.CONFIRMED,
    )


@patch('event.notifications.post_discord_webhook')
@patch('event.notifications.send_mail', return_value=1)
class ArticlePublishedNotificationTest(TestCase):
    """記事を作った後の発表者への通知（申請結果の通知と同じメールと集会の Discord）。"""

    def setUp(self):
        self.applicant = make_user(user_name='notify_speaker', email='notify@example.com')
        self.community = make_community(
            name='通知の集会', webhook_url='https://discord.com/api/webhooks/123/token',
        )
        self.event = make_event(self.community, event_date=date.today() - timedelta(days=1))

    def _detail(self, status):
        return make_event_detail(
            self.event, applicant=self.applicant, status=status, h1='記事のタイトル',
        )

    def test_published_article_notifies_by_email_and_discord(self, send_mail, post_webhook):
        from event.notifications import notify_applicant_of_article_published

        detail = self._detail('approved')

        notify_applicant_of_article_published(detail, self.applicant)

        self.assertIn('発表の記事を公開しました', send_mail.call_args.kwargs['subject'])
        self.assertEqual(send_mail.call_args.kwargs['recipient_list'], ['notify@example.com'])
        html = send_mail.call_args.kwargs['html_message']
        self.assertIn(reverse('account:lt_application_edit', kwargs={'pk': detail.pk}), html)
        self.assertIn(reverse('event:detail', kwargs={'pk': detail.pk}), html)
        post_webhook.assert_called_once()
        embed = post_webhook.call_args.args[1]['embeds'][0]
        self.assertEqual(embed['title'], '📝 発表の記事を公開しました')

    def test_pending_article_says_created_and_skips_discord(self, send_mail, post_webhook):
        from event.notifications import notify_applicant_of_article_published

        notify_applicant_of_article_published(self._detail('pending'), self.applicant)

        self.assertIn('発表の記事を作成しました', send_mail.call_args.kwargs['subject'])
        self.assertIn('承認されると公開されます', send_mail.call_args.kwargs['html_message'])
        post_webhook.assert_not_called()


@override_settings(REQUEST_TOKEN='test-token')
class RunArticleGenerationViewTest(TestCase):
    """Cloud Scheduler から呼ぶエンドポイントの認証と応答。"""

    def setUp(self):
        self.url = reverse('event:run_article_generation')

    def test_rejects_missing_or_wrong_token(self):
        self.assertEqual(self.client.get(self.url).status_code, 401)
        self.assertEqual(self.client.get(self.url, HTTP_REQUEST_TOKEN='wrong').status_code, 401)

    @override_settings(REQUEST_TOKEN='')
    def test_rejects_when_server_token_is_not_set(self):
        self.assertEqual(self.client.get(self.url, HTTP_REQUEST_TOKEN='').status_code, 401)

    @patch.dict('os.environ', {'REQUEST_TOKEN': 'env-token'})
    def test_token_is_read_from_settings(self):
        """隣の Scheduler 用エンドポイントと同じく settings.REQUEST_TOKEN と照合する（環境変数は直接見ない）。"""
        self.assertEqual(self.client.get(self.url, HTTP_REQUEST_TOKEN='env-token').status_code, 401)
        self.assertEqual(self.client.get(self.url, HTTP_REQUEST_TOKEN='test-token').status_code, 200)

    def test_returns_counts_as_json(self):
        response = self.client.get(self.url, HTTP_REQUEST_TOKEN='test-token')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            'generated': 0,
            'deferred': 0,
            'failed': 0,
            'skipped_manual': 0,
            'skipped': 0,
            'processed': 0,
            'pending': 0,
            'results': [],
        })

    @patch('event.views.article_generation.process_article_generation_queue', return_value={})
    def test_limit_is_between_one_and_two(self, process_queue):
        ok = self.client.get(self.url, {'limit': '2'}, HTTP_REQUEST_TOKEN='test-token')
        too_many = self.client.get(self.url, {'limit': '3'}, HTTP_REQUEST_TOKEN='test-token')
        not_number = self.client.get(self.url, {'limit': 'x'}, HTTP_REQUEST_TOKEN='test-token')

        self.assertEqual(ok.status_code, 200)
        process_queue.assert_called_once_with(limit=2)
        self.assertEqual(too_many.status_code, 400)
        self.assertEqual(not_number.status_code, 400)

    def test_post_is_not_allowed(self):
        response = self.client.post(self.url, HTTP_REQUEST_TOKEN='test-token')

        self.assertEqual(response.status_code, 405)
