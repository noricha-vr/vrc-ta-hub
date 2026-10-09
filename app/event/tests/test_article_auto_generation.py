"""記事の自動生成（生成待ちの印・待ち行列の処理・通知・エンドポイント）のテスト。

外部 API（LLM・YouTube・PDF ワーカー）はすべてモックする。
"""

import json
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from event.models import EventDetail, article_body_hash
from event.services import article_generation
from event.services.article_generation import MAX_ATTEMPTS, process_article_generation_queue
from tests.factories import make_community, make_event, make_event_detail, make_user

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
        send_mail = _mocks[3]
        detail = self._due_detail()

        result = process_article_generation_queue()

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
        """PDF だけで作った後に動画が来たら、字幕と PDF を合わせて作り直す。"""
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

    def test_does_not_overwrite_manually_edited_article(self, openai_class, *_mocks):
        openai_class.return_value = _openrouter_client()
        detail = self._due_detail(h1='生成した記事', contents='生成した本文')
        detail.record_generated_article()
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

    def test_failure_records_error_and_retries_later(self, openai_class, get_transcript, *_mocks):
        """入力のテキストが取れない時は記事を作らず、間を空けて再試行する。"""
        get_transcript.side_effect = lambda video_id, language='ja': None
        detail = self._due_detail(slide_file='')

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
        get_transcript.side_effect = lambda video_id, language='ja': None
        detail = self._due_detail(slide_file='')
        EventDetail.objects.filter(pk=detail.pk).update(article_generation_attempts=MAX_ATTEMPTS - 1)

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

        notify_applicant_of_article_published(detail)

        self.assertIn('発表の記事を公開しました', send_mail.call_args.kwargs['subject'])
        html = send_mail.call_args.kwargs['html_message']
        self.assertIn(reverse('account:lt_application_edit', kwargs={'pk': detail.pk}), html)
        self.assertIn(reverse('event:detail', kwargs={'pk': detail.pk}), html)
        post_webhook.assert_called_once()
        embed = post_webhook.call_args.args[1]['embeds'][0]
        self.assertEqual(embed['title'], '📝 発表の記事を公開しました')

    def test_pending_article_says_created_and_skips_discord(self, send_mail, post_webhook):
        from event.notifications import notify_applicant_of_article_published

        notify_applicant_of_article_published(self._detail('pending'))

        self.assertIn('発表の記事を作成しました', send_mail.call_args.kwargs['subject'])
        self.assertIn('承認されると公開されます', send_mail.call_args.kwargs['html_message'])
        post_webhook.assert_not_called()


class RunArticleGenerationViewTest(TestCase):
    """Cloud Scheduler から呼ぶエンドポイントの認証と応答。"""

    TOKEN_ENV = {'REQUEST_TOKEN': 'test-token'}

    def setUp(self):
        self.url = reverse('event:run_article_generation')

    def test_rejects_missing_or_wrong_token(self):
        with patch.dict('os.environ', self.TOKEN_ENV):
            self.assertEqual(self.client.get(self.url).status_code, 401)
            self.assertEqual(self.client.get(self.url, HTTP_REQUEST_TOKEN='wrong').status_code, 401)

    def test_rejects_when_server_token_is_not_set(self):
        with patch.dict('os.environ', {'REQUEST_TOKEN': ''}):
            self.assertEqual(self.client.get(self.url, HTTP_REQUEST_TOKEN='').status_code, 401)

    def test_returns_counts_as_json(self):
        with patch.dict('os.environ', self.TOKEN_ENV):
            response = self.client.get(self.url, HTTP_REQUEST_TOKEN='test-token')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            'generated': 0,
            'failed': 0,
            'skipped_manual': 0,
            'skipped': 0,
            'processed': 0,
            'pending': 0,
            'results': [],
        })

    @patch('event.views.article_generation.process_article_generation_queue', return_value={})
    def test_limit_is_between_one_and_two(self, process_queue):
        with patch.dict('os.environ', self.TOKEN_ENV):
            ok = self.client.get(self.url, {'limit': '2'}, HTTP_REQUEST_TOKEN='test-token')
            too_many = self.client.get(self.url, {'limit': '3'}, HTTP_REQUEST_TOKEN='test-token')
            not_number = self.client.get(self.url, {'limit': 'x'}, HTTP_REQUEST_TOKEN='test-token')

        self.assertEqual(ok.status_code, 200)
        process_queue.assert_called_once_with(limit=2)
        self.assertEqual(too_many.status_code, 400)
        self.assertEqual(not_number.status_code, 400)

    def test_post_is_not_allowed(self):
        with patch.dict('os.environ', self.TOKEN_ENV):
            response = self.client.post(self.url, HTTP_REQUEST_TOKEN='test-token')

        self.assertEqual(response.status_code, 405)
