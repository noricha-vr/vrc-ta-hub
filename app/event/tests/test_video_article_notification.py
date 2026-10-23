"""動画と記事の告知。通信はモックし、保存・生成・コミット後の条件を確かめる。"""
from datetime import timedelta
from unittest.mock import patch

from django.db import transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from event.models import EventDetail
from event.notifications import notify_video_article_published
from event.services.article_generation import _notify_first_time, process_article_generation_queue
from event.services.content_generation_service import (
    SAVED,
    BlogOutput,
    BlogSources,
    _with_sources,
    save_generated_article,
    set_generated_article,
)
from api_v1.serializers import EventDetailWriteSerializer
from tests.factories import make_community, make_event, make_event_detail, make_user
from website.constants import build_site_url

VIDEO_ID = 'rrKl0s23E0M'
OTHER_VIDEO_ID = 'abcdefghijk'
VIDEO_URL = f'https://www.youtube.com/watch?v={VIDEO_ID}'


class VideoArticleNotificationTest(TestCase):
    """キュー・ボタン・手動編集が共通の保存シグナルを通ることを確かめる。"""

    def setUp(self):
        self.post = self.enterContext(patch('event.notifications.post_discord_webhook'))
        self.send_mail = self.enterContext(patch('event.notifications.send_mail', return_value=1))
        self.enterContext(patch('event.services.article_generation.ensure_pdf_thumbnail', return_value=False))
        self.enterContext(patch('event.services.content_generation_service.ensure_pdf_thumbnail', return_value=False))
        self.applicant = make_user(user_name='video_speaker', email='speaker@example.com')
        self.community = make_community(name='動画告知の集会', webhook_url='offline-webhook')
        self.event = make_event(self.community, event_date=timezone.localdate() + timedelta(days=1))

    def _detail(self, **changes):
        values = {'status': 'approved', 'applicant': self.applicant,
                  'article_consent': EventDetail.ArticleConsent.OK, 'youtube_url': VIDEO_URL}
        values.update(changes)
        return make_event_detail(self.event, **values)

    def _output(self, video_id=VIDEO_ID, slide_name=''):
        return _with_sources(
            BlogOutput(title='動画の記事', text='生成した本文', meta_description='動画の要約'),
            BlogSources(transcript='動画の字幕' if video_id else '', pdf_content='資料の文章',
                        pdf_url='', video_id=video_id, slide_name=slide_name),
        )

    def _generate(self, detail, video_id=VIDEO_ID):
        output = self._output(video_id)
        detail.save(update_fields=set_generated_article(detail, output, output.sources.used_sources))

    def _queue(self, detail, video_id=VIDEO_ID):
        EventDetail.objects.filter(pk=detail.pk).update(
            article_generation_requested_at=timezone.now() - timedelta(minutes=1),
        )
        output = self._output(video_id)
        with patch('event.services.article_generation._generate', return_value=(output, output.sources)):
            with self.captureOnCommitCallbacks(execute=True):
                result = process_article_generation_queue()
        self.assertEqual(result['generated'], 1)

    def test_queue_first_video_article_notifies_once_and_keeps_email(self):
        detail = self._detail()
        self._queue(detail)
        self.post.assert_called_once()
        self.assertIn('動画と記事を公開しました', self.post.call_args.args[1]['content'])
        self.send_mail.assert_called_once()
        self.assertIn('発表の記事を公開しました', self.send_mail.call_args.kwargs['subject'])
        self.assertIn(reverse('account:lt_application_edit', kwargs={'pk': detail.pk}),
                      self.send_mail.call_args.kwargs['html_message'])
        detail.refresh_from_db()
        self.assertIsNotNone(detail.article_published_notified_at)

    def _video_posts(self):
        return [call for call in self.post.call_args_list
                if call.args[1].get('content', '').endswith('の動画と記事を公開しました')]

    def _set_video(self, detail, url):
        with self.captureOnCommitCallbacks(execute=True):
            detail.youtube_url = url
            detail.save(update_fields=['youtube_url'])

    def test_transcript_reset_and_same_video_regeneration_do_not_notify_again(self):
        """字幕の保存で生成元の動画 ID が空に戻り、同じ動画で作り直しても告知は 1 回だけ。"""
        detail = self._detail()
        self._queue(detail)
        self.assertEqual(len(self._video_posts()), 1)
        detail.refresh_from_db()
        EventDetailWriteSerializer._store_transcript(detail, '新しい字幕')
        detail.refresh_from_db()
        self.assertEqual(detail.article_source_video_id, '')
        self.assertIsNotNone(detail.article_generation_requested_at)
        self._queue(detail)
        detail.refresh_from_db()
        self.assertEqual(detail.article_source_video_id, VIDEO_ID)
        self.assertEqual(len(self._video_posts()), 1)
        self.assertEqual(self.post.call_count, 1)

    def test_old_event_manual_article_edit_does_not_announce(self):
        """告知の列が空の昔の発表は、記事を直しても告知しない（開催日から 30 日を過ぎたもの）。"""
        old_event = make_event(self.community, event_date=timezone.localdate() - timedelta(days=60))
        detail = make_event_detail(old_event, status='approved', applicant=self.applicant,
                                   article_consent=EventDetail.ArticleConsent.OK, youtube_url=VIDEO_URL,
                                   h1='昔の記事', contents='昔の本文')
        with self.captureOnCommitCallbacks(execute=True):
            detail.h1 = '直したタイトル'
            detail.save(update_fields=['h1'])
        self.assertEqual(self._video_posts(), [])
        detail.refresh_from_db()
        self.assertEqual(detail.video_article_announced_id, '')

    def test_manual_video_reattached_is_announced_once_and_new_video_again(self):
        """手動の記事で動画を A → 空 → A と付け直しても 1 回、別の動画 B は 1 回だけ告知する。"""
        detail = self._detail(youtube_url='', h1='手書きの記事', contents='手で書いた本文')
        for url in (VIDEO_URL, '', VIDEO_URL):
            self._set_video(detail, url)
        self.assertEqual(len(self._video_posts()), 1)
        other_url = f'https://youtu.be/{OTHER_VIDEO_ID}'
        self._set_video(detail, other_url)
        self._set_video(detail, other_url)
        posts = self._video_posts()
        self.assertEqual(len(posts), 2)
        self.assertEqual(posts[1].args[1]['embeds'][0]['fields'][2]['value'], other_url)
        detail.refresh_from_db()
        self.assertEqual(detail.video_article_announced_id, OTHER_VIDEO_ID)

    def test_only_lt_and_special_are_announced(self):
        for detail_type, expected in (('LT', 1), ('SPECIAL', 1), ('BLOG', 0)):
            with self.subTest(detail_type=detail_type):
                detail = self._detail(youtube_url='', detail_type=detail_type, h1='手書きの記事')
                self._set_video(detail, VIDEO_URL)
                self.assertEqual(len(self._video_posts()), expected)
                self.post.reset_mock()

    def test_stale_source_video_posts_existing_article_discord(self):
        """元の動画が今の動画と違う自動生成の記事は告知せず、既存の記事公開の Discord を送る。"""
        detail = self._detail()
        self._queue(detail, video_id=OTHER_VIDEO_ID)
        self.assertEqual(self._video_posts(), [])
        self.post.assert_called_once()
        self.assertEqual(self.post.call_args.args[1]['embeds'][0]['title'], '📝 発表の記事を公開しました')
        self.send_mail.assert_called_once()

    def test_slide_article_then_video_regeneration_notifies_once(self):
        detail = self._detail(youtube_url='', slide_file='slide/presentation.pdf')
        self._queue(detail, video_id='')
        self.assertEqual(self.post.call_args.args[1]['embeds'][0]['title'], '📝 発表の記事を公開しました')
        self.post.reset_mock()
        detail.refresh_from_db()
        with self.captureOnCommitCallbacks(execute=True):
            detail.youtube_url = VIDEO_URL
            detail.save(update_fields=['youtube_url'])
        self.post.assert_not_called()
        self._queue(detail)
        self.post.assert_called_once()
        self.assertIn('動画と記事を公開しました', self.post.call_args.args[1]['content'])
        self.send_mail.assert_called_once()

    def test_email_failure_does_not_block_or_repeat_video_notice(self):
        detail = self._detail()
        self.send_mail.return_value = 0
        self._queue(detail)
        self.post.assert_called_once()
        detail.refresh_from_db()
        self.assertIsNone(detail.article_published_notified_at)
        self.send_mail.return_value = 1
        _notify_first_time(detail.pk)
        self.assertEqual(self.send_mail.call_count, 2)
        self.post.assert_called_once()

    def test_button_save_generated_article_notifies_once(self):
        detail = self._detail()
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(save_generated_article(detail, self._output()), SAVED)
        self.post.assert_called_once()
        self.send_mail.assert_not_called()

    def test_same_video_regeneration_and_unrelated_save_do_not_notify(self):
        detail = self._detail()
        with self.captureOnCommitCallbacks(execute=True):
            self._generate(detail)
        self.post.reset_mock()
        detail.refresh_from_db()
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(save_generated_article(detail, self._output()), SAVED)
            detail.refresh_from_db()
            detail.additional_info = '補足'
            detail.save(update_fields=['additional_info'])
            detail.save()
        self.post.assert_not_called()

    def test_different_source_video_notifies_again_for_new_video(self):
        detail = self._detail()
        with self.captureOnCommitCallbacks(execute=True):
            self._generate(detail)
        self.post.reset_mock()
        detail.refresh_from_db()
        with self.captureOnCommitCallbacks(execute=True):
            detail.youtube_url = f'https://youtu.be/{OTHER_VIDEO_ID}'
            detail.save(update_fields=['youtube_url'])
            self._generate(detail, video_id=OTHER_VIDEO_ID)
        self.post.assert_called_once()
        self.assertEqual(self.post.call_args.args[1]['embeds'][0]['fields'][2]['value'], detail.youtube_url)

    def test_manual_article_gets_video_without_regeneration(self):
        for generated_before in (False, True):
            with self.subTest(generated_before=generated_before):
                detail = self._detail(youtube_url='', h1='手書きの記事', contents='手で書いた本文')
                if generated_before:
                    self._generate(detail, video_id='')
                    detail.contents = '手で修正した本文'
                    detail.save(update_fields=['contents'])
                with self.captureOnCommitCallbacks(execute=True):
                    detail.youtube_url = VIDEO_URL
                    detail.save(update_fields=['youtube_url'])
                self.post.assert_called_once()
                detail.refresh_from_db()
                self.assertEqual(detail.article_state(), EventDetail.ArticleState.MANUAL)
                self.assertIsNone(detail.article_generation_requested_at)
                self.post.reset_mock()

    def test_manual_video_url_format_change_does_not_notify_again(self):
        detail = self._detail(youtube_url='', h1='手動記事')
        with self.captureOnCommitCallbacks(execute=True):
            detail.youtube_url = VIDEO_URL
            detail.save(update_fields=['youtube_url'])
            detail.youtube_url = f'https://youtu.be/{VIDEO_ID}?t=30'
            detail.save(update_fields=['youtube_url'])
        self.post.assert_called_once()

    def test_source_and_manual_video_in_same_save_only_notify_once(self):
        detail = self._detail(youtube_url='', h1='手動記事')
        with self.captureOnCommitCallbacks(execute=True):
            detail.youtube_url = VIDEO_URL
            detail.article_source_video_id = VIDEO_ID
            detail.save(update_fields=['youtube_url', 'article_source_video_id'])
        self.post.assert_called_once()

    def test_pending_rejected_ng_unanswered_deleted_and_no_video_are_silent(self):
        for changes in (
            {'status': 'pending'}, {'status': 'rejected'},
            {'article_consent': EventDetail.ArticleConsent.NG},
            {'article_consent': EventDetail.ArticleConsent.UNANSWERED},
            {'deleted_at': timezone.now()}, {'youtube_url': ''},
            {'youtube_url': 'https://example.com/video'},
        ):
            with self.subTest(changes=changes):
                detail = self._detail(**changes)
                with self.captureOnCommitCallbacks(execute=True):
                    self._generate(detail)
                self.post.assert_not_called()

    def test_empty_article_is_silent(self):
        detail = self._detail()
        with self.captureOnCommitCallbacks(execute=True):
            detail.article_source_video_id = VIDEO_ID
            detail.h1 = ' \n'
            detail.contents = ' '
            detail.save(update_fields=['article_source_video_id', 'h1', 'contents'])
        self.post.assert_not_called()

    def test_no_webhook_is_success_without_http(self):
        self.community.notification_webhook_url = ''
        self.community.save(update_fields=['notification_webhook_url'])
        detail = self._detail()
        with self.captureOnCommitCallbacks(execute=True):
            self._generate(detail)
        self.assertTrue(notify_video_article_published(detail))
        self.post.assert_not_called()

    def test_missing_transcript_does_not_announce_video_article(self):
        detail = self._detail(slide_file='slide/presentation.pdf')
        self._queue(detail, video_id='')
        self.post.assert_called_once()
        self.assertNotIn('content', self.post.call_args.args[1])
        self.assertEqual(self.post.call_args.args[1]['embeds'][0]['title'], '📝 発表の記事を公開しました')

    def test_source_only_save_is_watched_and_twitter_old_values_are_preserved(self):
        detail = self._detail(h1='記事のタイトル')
        with self.captureOnCommitCallbacks(execute=True):
            detail.article_source_video_id = VIDEO_ID
            detail.save(update_fields=['article_source_video_id'])
        self.post.assert_called_once()
        self.assertEqual(detail._old_youtube_url, VIDEO_URL)
        self.assertEqual(detail._old_status, 'approved')
        self.assertEqual(detail._old_theme, detail.theme)
        with self.captureOnCommitCallbacks(execute=True):
            detail.save(update_fields=['article_source_video_id'])
        self.post.assert_called_once()

    def test_unsaved_source_change_does_not_trigger_notice(self):
        detail = self._detail(youtube_url='', h1='手動記事')
        detail.article_source_video_id = VIDEO_ID
        with self.captureOnCommitCallbacks(execute=True):
            detail.h1 = '編集した記事'
            detail.save(update_fields=['h1'])
        self.post.assert_not_called()

    def test_notification_is_deferred_until_commit(self):
        detail = self._detail()
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            self._generate(detail)
            self.post.assert_not_called()
        self.assertTrue(callbacks)
        for callback in callbacks:
            callback()
        self.post.assert_called_once()

    def test_rollback_discards_notification(self):
        detail = self._detail()
        with self.captureOnCommitCallbacks(execute=True):
            with self.assertRaises(RuntimeError), transaction.atomic():
                self._generate(detail)
                raise RuntimeError('rollback')
        self.post.assert_not_called()
        detail.refresh_from_db()
        self.assertEqual(detail.article_source_video_id, '')

    def test_callback_rechecks_current_publication_article_and_video(self):
        for changes in (
            {'status': 'pending'}, {'status': 'rejected'},
            {'article_consent': EventDetail.ArticleConsent.NG}, {'deleted_at': timezone.now()},
            {'h1': '', 'contents': ''}, {'youtube_url': ''},
            {'youtube_url': f'https://youtu.be/{OTHER_VIDEO_ID}'}, {'article_source_video_id': ''},
        ):
            with self.subTest(changes=changes):
                detail = self._detail()
                with self.captureOnCommitCallbacks(execute=False) as callbacks:
                    self._generate(detail)
                EventDetail.all_objects.filter(pk=detail.pk).update(**changes)
                for callback in callbacks:
                    callback()
                self.post.assert_not_called()

    def test_manual_callback_skips_article_that_became_automatic(self):
        detail = self._detail(youtube_url='', h1='手動記事')
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            detail.youtube_url = VIDEO_URL
            detail.save(update_fields=['youtube_url'])
        EventDetail.objects.filter(pk=detail.pk).update(article_body_hash=detail.current_article_hash())
        for callback in callbacks:
            callback()
        self.post.assert_not_called()

    def test_callback_uses_fresh_message_values(self):
        detail = self._detail()
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            self._generate(detail)
        EventDetail.objects.filter(pk=detail.pk).update(h1='送る直前のタイトル', speaker='送る直前の発表者')
        for callback in callbacks:
            callback()
        payload = self.post.call_args.args[1]
        self.assertEqual(payload['embeds'][0]['title'], '送る直前のタイトル')
        self.assertIn('送る直前の発表者', payload['content'])

    def test_http_failure_restores_column_and_retries_on_next_save(self):
        detail = self._detail()
        self.post.side_effect = RuntimeError('offline failure')
        with self.assertLogs('event.notifications', level='ERROR') as logs:
            with self.captureOnCommitCallbacks(execute=True):
                self._generate(detail)
        self.assertIn(f'community_id={self.community.pk}', logs.output[0])
        self.assertIn(f'event_detail_id={detail.pk}', logs.output[0])
        self.assertNotIn('offline failure', logs.output[0])
        detail.refresh_from_db()
        self.assertEqual(detail.article_source_video_id, VIDEO_ID)
        self.assertEqual(detail.video_article_announced_id, '')
        self.post.side_effect = None
        self._set_video(detail, '')
        self._set_video(detail, VIDEO_URL)
        self.assertEqual(self.post.call_count, 2)
        detail.refresh_from_db()
        self.assertEqual(detail.video_article_announced_id, VIDEO_ID)

    def test_unrelated_full_save_of_recent_video_article_does_not_announce(self):
        """直近の動画付き・記事付きの行で、関係しない列だけを変えたフル保存では告知しない。"""
        detail = self._detail(h1='手書きの記事', contents='手で書いた本文')
        detail.refresh_from_db()
        with self.captureOnCommitCallbacks(execute=True):
            detail.additional_info = '補足を追記'
            detail.save()
        self.post.assert_not_called()
        detail.refresh_from_db()
        self.assertEqual(detail.video_article_announced_id, '')

    def test_manual_article_written_later_announces(self):
        """動画だけの発表に後から手で記事を書いた時（記事の空から非空）に告知する。"""
        detail = self._detail()
        detail.refresh_from_db()
        with self.captureOnCommitCallbacks(execute=True):
            detail.h1 = '手書きの記事'
            detail.contents = '手で書いた本文'
            detail.save()
        self.assertEqual(len(self._video_posts()), 1)

    def test_queue_webhook_failure_falls_back_to_existing_article_discord(self):
        """生成後に告知の Webhook が失敗したら、既存の記事公開の Discord を送る。"""
        detail = self._detail()

        def fail_video_notice(_url, payload):
            if payload.get('content', '').endswith('の動画と記事を公開しました'):
                raise RuntimeError('offline failure')

        self.post.side_effect = fail_video_notice
        with self.assertLogs('event.notifications', level='ERROR'):
            self._queue(detail)
        titles = [call.args[1]['embeds'][0]['title'] for call in self.post.call_args_list
                  if 'content' not in call.args[1]]
        self.assertEqual(titles, ['📝 発表の記事を公開しました'])
        self.send_mail.assert_called_once()
        detail.refresh_from_db()
        self.assertEqual(detail.video_article_announced_id, '')

    def test_template_has_edit_url_only_for_auto_article(self):
        detail = self._detail()
        with self.captureOnCommitCallbacks(execute=True):
            self._generate(detail)
        edit_url = build_site_url(reverse('account:lt_application_edit', kwargs={'pk': detail.pk}))
        fields = self.post.call_args.args[1]['embeds'][0]['fields']
        self.assertEqual(fields[-1], {'name': '✏️ 確認・修正（発表者向け）', 'value': edit_url, 'inline': False})
        detail.refresh_from_db()
        detail.contents = '手で直した本文'
        notify_video_article_published(detail)
        self.assertNotIn(edit_url, str(self.post.call_args.args[1]))

    def test_callback_exception_does_not_fail_save(self):
        detail = self._detail()
        with patch('event.notifications.notify_video_article_published', side_effect=RuntimeError('offline failure')):
            with self.assertLogs('event.notifications', level='ERROR') as logs:
                with self.captureOnCommitCallbacks(execute=True):
                    self._generate(detail)
        self.assertIn('Traceback', logs.output[0])
        detail.refresh_from_db()
        self.assertEqual(detail.h1, '動画の記事')
        self.assertEqual(detail.video_article_announced_id, '')

    def test_sender_returns_false_only_on_failure(self):
        detail = self._detail(h1='記事')
        self.post.side_effect = RuntimeError('offline failure')
        with self.assertLogs('event.notifications', level='ERROR'):
            self.assertFalse(notify_video_article_published(detail))

    def test_template_has_article_url_and_no_edit_url(self):
        detail = self._detail(h1='記事タイトル', contents='本文', meta_description='要約')
        image_url = '/test-og.jpg'
        with patch('event.notifications._get_event_detail_og_image_url', return_value=image_url):
            self.assertTrue(notify_video_article_published(detail))
        self.post.assert_called_once()
        payload = self.post.call_args.args[1]
        article_url = build_site_url(reverse('event:detail', kwargs={'pk': detail.pk}))
        self.assertEqual(payload, {
            'content': f'🎬 {detail.speaker} さんの発表「{detail.theme}」の動画と記事を公開しました',
            'allowed_mentions': {'parse': []},
            'embeds': [{
                'title': '記事タイトル', 'url': article_url, 'description': '要約', 'color': 3447003,
                'fields': [
                    {'name': '👤 発表者', 'value': detail.speaker, 'inline': True},
                    {'name': '📅 開催日', 'value': str(self.event.date), 'inline': True},
                    {'name': '🎥 動画', 'value': VIDEO_URL, 'inline': False},
                    {'name': '📝 記事', 'value': article_url, 'inline': False},
                ],
                'image': {'url': image_url}, 'footer': {'text': self.community.name},
            }],
        })
        self.assertNotIn(reverse('account:lt_application_edit', kwargs={'pk': detail.pk}), str(payload))

    def test_template_theme_fallback_and_empty_optional_fields(self):
        detail = self._detail(contents='本文')
        with patch('event.notifications._get_event_detail_og_image_url', return_value=''):
            notify_video_article_published(detail)
        embed = self.post.call_args.args[1]['embeds'][0]
        self.assertEqual(embed['title'], detail.theme)
        self.assertNotIn('description', embed)
        self.assertNotIn('image', embed)

    def test_template_description_truncates_after_200_characters(self):
        detail = self._detail(h1='記事')
        for length in (199, 200, 201):
            with self.subTest(length=length):
                detail.meta_description = 'あ' * length
                notify_video_article_published(detail)
                description = self.post.call_args.args[1]['embeds'][0]['description']
                self.assertEqual(description, 'あ' * min(length, 200) + ('…' if length > 200 else ''))
