"""発表の記事化の同意（EventDetail.article_consent）のテスト。

申請・編集フォームでの保存、主催者画面での表示だけの扱い、NG の発表の非表示と生成の拒否を確かめる。
"""

import re
from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase
from django.urls import reverse

from event.forms import EventDetailForm, LTApplicationEditForm
from event.models import EventDetail, related_event_details_cache_key
from event.services.content_generation_service import BlogOutput
from ta_hub.index_cache import build_index_database_context, get_index_view_cache_key
from tests.factories import (
    make_community,
    make_discord_linked_user,
    make_event,
    make_event_detail,
    make_user,
)

ArticleConsent = EventDetail.ArticleConsent
CONSENT_OK_LABEL = '記事化 OK（スライド画像の掲載を含む）'
VIDEO_URL = 'https://www.youtube.com/watch?v=rrKl0s23E0M'
OTHER_VIDEO_URL = 'https://www.youtube.com/watch?v=abcdefghijk'


class ArticleConsentModelTest(TestCase):
    """選択肢と、既存の発表の値。"""

    def test_default_is_unanswered(self):
        detail = make_event_detail(make_event(make_community(name='既定値の集会')))

        detail.refresh_from_db()
        self.assertEqual(detail.article_consent, ArticleConsent.UNANSWERED)

    def test_choices_and_labels(self):
        self.assertEqual(
            list(ArticleConsent.choices),
            [('unanswered', '未回答'), ('ok', CONSENT_OK_LABEL), ('ng', '記事化 NG')],
        )


@patch('event.notifications.send_mail', return_value=1)
class LTApplicationArticleConsentTest(TestCase):
    """申請フォームは必須の 2 択で、初期値を置かない。"""

    def setUp(self):
        self.user = make_discord_linked_user(user_name='consent_applicant', email='consent@example.com')
        self.community = make_community(name='申請の集会')
        self.event = make_event(self.community)
        self.url = reverse('event:lt_application_create', kwargs={'community_pk': self.community.pk})
        self.client.force_login(self.user)

    def _post(self, theme, **extra):
        data = {'event': self.event.pk, 'theme': theme, 'speaker': '発表者'}
        data.update(extra)
        return self.client.post(self.url, data)

    def test_form_shows_two_choices_without_selection(self, _mock_send):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, CONSENT_OK_LABEL)
        self.assertContains(response, '記事化 NG')
        self.assertNotContains(response, '未回答')
        html = response.content.decode()
        self.assertEqual(len(re.findall(r'<input type="radio" name="article_consent"', html)), 2)
        self.assertNotRegex(html, r'<input type="radio" name="article_consent"[^>]*checked')

    def test_missing_consent_is_rejected(self, _mock_send):
        response = self._post('未選択の発表')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '発表の記事化の OK / NG を選んでください。')
        self.assertFalse(EventDetail.objects.filter(theme='未選択の発表').exists())

    def test_unanswered_cannot_be_submitted(self, _mock_send):
        response = self._post('未回答を送った発表', article_consent='unanswered')

        self.assertEqual(response.status_code, 200)
        self.assertFalse(EventDetail.objects.filter(theme='未回答を送った発表').exists())

    def test_selected_consent_is_saved(self, _mock_send):
        for consent in (ArticleConsent.OK, ArticleConsent.NG):
            with self.subTest(consent=consent):
                theme = f'{consent}の発表'
                response = self._post(theme, article_consent=consent)

                self.assertEqual(response.status_code, 302)
                self.assertEqual(EventDetail.objects.get(theme=theme).article_consent, consent)


class LTApplicationEditFormArticleConsentTest(TestCase):
    """発表者の編集フォームで同意を変えられる。"""

    def setUp(self):
        self.event = make_event(make_community(name='編集の集会'))

    def _form(self, instance, **extra):
        data = {'theme': 'テーマ', 'speaker': '発表者'}
        data.update(extra)
        return LTApplicationEditForm(data=data, instance=instance)

    def test_choices_are_ok_and_ng_only(self):
        form = LTApplicationEditForm(instance=make_event_detail(self.event))

        self.assertEqual(
            [value for value, _label in form.fields['article_consent'].choices],
            ['ok', 'ng'],
        )

    def test_unanswered_is_shown_without_selection(self):
        form = LTApplicationEditForm(instance=make_event_detail(self.event))

        self.assertNotIn('checked', str(form['article_consent']))

    def test_consent_can_be_changed(self):
        detail = make_event_detail(self.event, article_consent=ArticleConsent.OK)
        form = self._form(detail, article_consent='ng')

        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        detail.refresh_from_db()
        self.assertEqual(detail.article_consent, ArticleConsent.NG)

    def test_blank_keeps_current_value(self):
        detail = make_event_detail(self.event, article_consent=ArticleConsent.OK)
        form = self._form(detail)

        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        detail.refresh_from_db()
        self.assertEqual(detail.article_consent, ArticleConsent.OK)

    def test_generation_checkbox_by_consent(self):
        """NG は生成しないので出さない。OK は自動生成に任せるので出さない。未回答は今までどおり出す。"""
        ng_form = LTApplicationEditForm(instance=make_event_detail(self.event, article_consent=ArticleConsent.NG))
        ok_form = LTApplicationEditForm(instance=make_event_detail(self.event, article_consent=ArticleConsent.OK))
        unanswered_form = LTApplicationEditForm(instance=make_event_detail(self.event))

        self.assertNotIn('generate_blog_article', ng_form.fields)
        self.assertNotIn('generate_blog_article', ok_form.fields)
        self.assertTrue(ok_form.article_auto_generation)
        self.assertIn('generate_blog_article', unanswered_form.fields)
        self.assertFalse(unanswered_form.article_auto_generation)


class LTApplicationEditViewArticleConsentTest(TestCase):
    """申請の編集画面（発表者）。"""

    def setUp(self):
        self.user = make_discord_linked_user(user_name='edit_speaker', email='edit_speaker@example.com')
        self.detail = make_event_detail(
            make_event(make_community(name='編集画面の集会')),
            applicant=self.user,
            status='approved',
            article_consent=ArticleConsent.OK,
            youtube_url=VIDEO_URL,
        )
        # 作成時に付いた生成待ちの印は外し、各テストの送信で付くかを見る
        EventDetail.objects.filter(pk=self.detail.pk).update(article_generation_requested_at=None)
        self.url = reverse('account:lt_application_edit', kwargs={'pk': self.detail.pk})
        self.client.force_login(self.user)

    def _post(self, **extra):
        data = {'theme': 'テーマ', 'speaker': '発表者', 'youtube_url': VIDEO_URL}
        data.update(extra)
        return self.client.post(self.url, data)

    def test_edit_page_shows_consent_choices(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '発表の記事化')
        self.assertRegex(
            response.content.decode(),
            r'<input type="radio" name="article_consent" value="ok"[^>]*checked',
        )

    def test_ok_edit_page_says_article_is_generated_automatically(self):
        response = self.client.get(self.url)

        self.assertContains(response, 'id="article-auto-generation-note"')
        self.assertNotContains(response, 'name="generate_blog_article"')

    @patch('event.services.content_generation_service.generate_blog')
    def test_switching_to_ng_with_checkbox_does_not_generate(self, mock_generate_blog):
        """同じ送信で NG に変えたら、チェックボックスが ON でも生成しない。"""
        EventDetail.objects.filter(pk=self.detail.pk).update(article_consent=ArticleConsent.UNANSWERED)

        response = self._post(article_consent='ng', generate_blog_article='on')

        self.assertEqual(response.status_code, 302)
        mock_generate_blog.assert_not_called()
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.article_consent, ArticleConsent.NG)

    @patch('event.services.content_generation_service.generate_blog')
    def test_ok_leaves_generation_to_queue(self, mock_generate_blog):
        """記事化 OK の発表は保存時に生成せず、キューに任せる（同期生成と二重にしない）。"""
        response = self._post(youtube_url=OTHER_VIDEO_URL, generate_blog_article='on')

        self.assertEqual(response.status_code, 302)
        mock_generate_blog.assert_not_called()
        self.detail.refresh_from_db()
        self.assertIsNotNone(self.detail.article_generation_requested_at)
        sent = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertIn('記事は自動で作成し、できあがったらメールでお知らせします', sent[0])

    @patch('event.services.content_generation_service.ensure_pdf_thumbnail', return_value=False)
    @patch('event.services.content_generation_service.generate_blog')
    def test_switching_unanswered_to_ok_leaves_generation_to_queue(self, mock_generate_blog, _thumbnail):
        """未回答のまま開いた画面で OK に変えて送っても、保存時には生成しない（キューが作る）。"""
        EventDetail.objects.filter(pk=self.detail.pk).update(article_consent=ArticleConsent.UNANSWERED)

        self._post(article_consent='ok', generate_blog_article='on')

        mock_generate_blog.assert_not_called()
        self.detail.refresh_from_db()
        self.assertIsNotNone(self.detail.article_generation_requested_at)

    @patch('event.services.content_generation_service.ensure_pdf_thumbnail', return_value=False)
    @patch('event.services.content_generation_service.generate_blog')
    def test_unanswered_still_generates_on_save(self, mock_generate_blog, _thumbnail):
        """未回答の発表はこれまでどおり、チェックボックスで保存と同時に生成する。"""
        EventDetail.objects.filter(pk=self.detail.pk).update(article_consent=ArticleConsent.UNANSWERED)
        mock_generate_blog.return_value = BlogOutput(title='生成した記事', meta_description='要約', text='本文')

        self._post(generate_blog_article='on')

        mock_generate_blog.assert_called_once()
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.h1, '生成した記事')


class OrganizerArticleConsentTest(TestCase):
    """主催者の画面では表示だけで、同意を変えられない。"""

    def setUp(self):
        self.owner = make_user(user_name='consent_owner', email='consent_owner@example.com')
        self.community = make_community(name='主催者の集会', owner=self.owner)
        self.detail = make_event_detail(
            make_event(self.community),
            status='approved',
            article_consent=ArticleConsent.NG,
            youtube_url=VIDEO_URL,
        )
        self.client.force_login(self.owner)

    def _update_data(self, **extra):
        data = {
            'detail_type': 'LT', 'theme': 'テーマ', 'speaker': '発表者',
            'start_time': '22:00', 'duration': 30, 'youtube_url': VIDEO_URL,
        }
        data.update(extra)
        return data

    def test_organizer_form_has_no_consent_field(self):
        form = EventDetailForm(instance=self.detail)

        self.assertNotIn('article_consent', form.fields)
        self.assertNotIn('generate_blog_article', form.fields)

    def test_edit_page_shows_consent(self):
        response = self.client.get(reverse('event:detail_update', kwargs={'pk': self.detail.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="article-consent-status"')
        self.assertContains(response, '記事化 NG')
        self.assertNotContains(response, 'name="article_consent"')

    @patch('event.views.crud_event_detail.generate_blog')
    def test_organizer_post_cannot_change_consent_or_generate(self, mock_generate_blog):
        url = reverse('event:detail_update', kwargs={'pk': self.detail.pk})
        response = self.client.post(url, self._update_data(article_consent='ok', generate_blog_article='on'))

        self.assertEqual(response.status_code, 302)
        mock_generate_blog.assert_not_called()
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.article_consent, ArticleConsent.NG)

    @patch('event.notifications.send_mail', return_value=1)
    def test_review_page_shows_consent(self, _mock_send):
        pending = make_event_detail(
            make_event(self.community, event_date=date.today() + timedelta(days=14)),
            applicant=make_user(user_name='review_speaker', email='review_speaker@example.com'),
            article_consent=ArticleConsent.OK,
        )

        response = self.client.get(reverse('event:lt_application_review', kwargs={'pk': pending.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '発表の記事化')
        self.assertContains(response, CONSENT_OK_LABEL)


class OrganizerOkArticleTest(TestCase):
    """記事化 OK の発表は、主催者の編集でも保存時に生成せず、キューに任せる。"""

    def setUp(self):
        self.owner = make_user(user_name='ok_owner', email='ok_owner@example.com')
        self.detail = make_event_detail(
            make_event(make_community(name='OK の集会', owner=self.owner)),
            status='approved',
            article_consent=ArticleConsent.OK,
            youtube_url=VIDEO_URL,
        )
        EventDetail.objects.filter(pk=self.detail.pk).update(article_generation_requested_at=None)
        self.url = reverse('event:detail_update', kwargs={'pk': self.detail.pk})
        self.client.force_login(self.owner)

    def test_form_hides_checkbox_and_says_generated_automatically(self):
        response = self.client.get(self.url)

        self.assertNotContains(response, 'name="generate_blog_article"')
        self.assertContains(response, 'id="article-auto-generation-note"')

    @patch('event.views.crud_event_detail.generate_blog')
    def test_update_does_not_generate_on_save(self, mock_generate_blog):
        response = self.client.post(self.url, {
            'detail_type': 'LT', 'theme': 'テーマ', 'speaker': '発表者',
            'start_time': '22:00', 'duration': 30,
            'youtube_url': OTHER_VIDEO_URL, 'generate_blog_article': 'on',
        })

        self.assertEqual(response.status_code, 302)
        mock_generate_blog.assert_not_called()
        self.detail.refresh_from_db()
        self.assertIsNotNone(self.detail.article_generation_requested_at)


class ArticleNgOtherScreensTest(TestCase):
    """NG の記事は、発表一覧のカード・構造化データ・他の発表ページの関連一覧でも出さない。"""

    H1 = '一覧に出してはいけない記事のタイトル'
    SUMMARY = '一覧に出してはいけない記事の要約'
    BODY = '一覧に出してはいけない記事の本文'

    def setUp(self):
        cache.clear()
        self.community = make_community(name='一覧の集会')
        self.event = make_event(self.community, event_date=date.today() - timedelta(days=3))
        self.ng = make_event_detail(
            self.event,
            status='approved',
            theme='NG の発表のテーマ',
            h1=self.H1,
            meta_description=self.SUMMARY,
            contents=f'## 見出し\n{self.BODY}',
            article_consent=ArticleConsent.NG,
            youtube_url=VIDEO_URL,
        )

    def test_model_hides_article_for_ng(self):
        self.assertEqual(self.ng.title, 'NG の発表のテーマ')
        self.assertEqual(self.ng.get_excerpt(), '')
        self.assertFalse(self.ng.has_article)

    def test_ng_article_alone_is_not_a_material(self):
        article_only = make_event_detail(
            self.event, status='approved', contents='本文だけの発表', article_consent=ArticleConsent.NG,
        )

        self.assertFalse(article_only.has_materials)
        self.assertFalse(EventDetail.objects.filter(EventDetail.materials_q(), pk=article_only.pk).exists())
        self.assertTrue(EventDetail.objects.filter(EventDetail.materials_q(), pk=self.ng.pk).exists())

    def test_presentation_list_hides_article(self):
        response = self.client.get(reverse('event:detail_history'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'NG の発表のテーマ')
        # カードのタイトル・抜粋・「記事」バッジ、構造化データ（JSON-LD）のどれにも出さない
        self.assertNotContains(response, self.H1)
        self.assertNotContains(response, self.SUMMARY)
        self.assertNotContains(response, self.BODY)
        self.assertNotContains(response, '</i>記事</span>')

    def _set_ng_consent(self, consent):
        """発表者の編集と同じく save() で同意を変える（保存のシグナルが関連一覧のキャッシュを消す）。"""
        detail = EventDetail.objects.get(pk=self.ng.pk)
        detail.article_consent = consent
        detail.save()

    def test_related_list_hides_ng_title_even_when_cached(self):
        """関連一覧のキャッシュを作った後に NG に変わっても、その h1 を出さない。"""
        other = make_event_detail(self.event, status='approved', theme='別の発表', h1='別の発表の記事')
        url = reverse('event:detail', kwargs={'pk': other.pk})
        self._set_ng_consent(ArticleConsent.OK)
        self.assertContains(self.client.get(url), self.H1)

        self._set_ng_consent(ArticleConsent.NG)

        self.assertNotContains(self.client.get(url), self.H1)

    def test_related_list_excludes_ng_when_built(self):
        other = make_event_detail(self.event, status='approved', theme='別の発表', h1='別の発表の記事')

        response = self.client.get(reverse('event:detail', kwargs={'pk': other.pk}))

        self.assertNotContains(response, self.H1)

    def test_related_list_cache_does_not_hide_the_first_viewed_detail(self):
        """最初に見た発表を除いた結果をキャッシュしない（同じ集会の他のページでは、その発表も出す）。"""
        first = make_event_detail(self.event, status='approved', theme='最初の発表', h1='最初に見た発表の記事')
        second = make_event_detail(self.event, status='approved', theme='次の発表', h1='次に見た発表の記事')

        first_page = self.client.get(reverse('event:detail', kwargs={'pk': first.pk}))
        second_page = self.client.get(reverse('event:detail', kwargs={'pk': second.pk}))

        self.assertContains(first_page, '次に見た発表の記事')
        self.assertContains(second_page, '最初に見た発表の記事')
        related_ids = [item['id'] for item in second_page.context['related_event_details']]
        self.assertNotIn(second.pk, related_ids)

    def test_related_cache_is_cleared_only_when_title_or_consent_changes(self):
        """h1 か記事化の同意が変わった時だけ、集会の関連一覧のキャッシュを消す（読むたびに問い合わせない）。"""
        other = make_event_detail(self.event, status='approved', theme='別の発表', h1='別の発表の記事')
        key = related_event_details_cache_key(self.community.pk)
        self.client.get(reverse('event:detail', kwargs={'pk': other.pk}))
        self.assertIsNotNone(cache.get(key))

        other.theme = 'テーマだけ直した'
        other.save()
        self.assertIsNotNone(cache.get(key))

        other.h1 = '直した記事のタイトル'
        other.save()
        self.assertIsNone(cache.get(key))

        self.client.get(reverse('event:detail', kwargs={'pk': other.pk}))
        self._set_ng_consent(ArticleConsent.OK)
        self.assertIsNone(cache.get(key))

    def test_search_does_not_match_ng_title(self):
        """発表一覧の検索は、記事化 NG の発表の記事のタイトル（h1）では当てない。"""
        by_title = self.client.get(reverse('event:detail_history'), {'q': '一覧に出してはいけない記事'})
        by_theme = self.client.get(reverse('event:detail_history'), {'q': 'NG の発表のテーマ'})

        self.assertNotIn(self.ng, list(by_title.context['event_details']))
        self.assertIn(self.ng, list(by_theme.context['event_details']))
        self.assertNotContains(by_theme, self.H1)

    def test_community_page_hides_ng_article(self):
        """集会ページの「記事・特別企画」も、記事化 NG ならタイトル（h1）と要約を出さない。"""
        make_event_detail(
            self.event, detail_type='SPECIAL', status='approved', theme='特別企画のテーマ',
            h1='出してはいけない特別企画の記事', meta_description='出してはいけない特別企画の要約',
            article_consent=ArticleConsent.NG,
        )

        response = self.client.get(reverse('community:detail', kwargs={'pk': self.community.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '特別企画のテーマ')
        self.assertNotContains(response, '出してはいけない特別企画の記事')
        self.assertNotContains(response, '出してはいけない特別企画の要約')


class IndexSpecialArticleNgTest(TestCase):
    """トップページの特別企画も、記事化 NG ならタイトル（h1）と要約を出さない。"""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.day = date.today() + timedelta(days=1)
        community = make_community(name='トップの集会', poster_image='community/poster.png')
        self.special = make_event_detail(
            make_event(community, event_date=self.day), detail_type='SPECIAL', status='approved',
            theme='トップの特別企画のテーマ', h1='トップに出してはいけない記事',
            meta_description='トップに出してはいけない要約', article_consent=ArticleConsent.NG,
        )

    def test_index_hides_ng_special_article(self):
        request = RequestFactory().get('/')
        request.user = AnonymousUser()
        context = build_index_database_context(request, self.day, get_index_view_cache_key(self.day))

        special = context['special_events'][0]
        self.assertEqual(special['title'], 'トップの特別企画のテーマ')
        self.assertEqual(special['meta_description'], '')
        html = render_to_string('ta_hub/index.html', context, request=request)
        self.assertIn('トップの特別企画のテーマ', html)
        self.assertNotIn('トップに出してはいけない記事', html)
        self.assertNotIn('トップに出してはいけない要約', html)


class EventDetailPageArticleConsentTest(TestCase):
    """NG の発表は詳細ページで記事の本文（h1 / contents）と要約を出さない。"""

    H1 = '生成した記事のタイトル'
    BODY = '記事の本文テキスト'
    SUMMARY = '記事の要約テキスト'

    def setUp(self):
        cache.clear()
        self.owner = make_user(user_name='page_owner', email='page_owner@example.com')
        self.detail = make_event_detail(
            make_event(make_community(name='詳細の集会', owner=self.owner), event_date=date.today()),
            status='approved',
            theme='発表のテーマ',
            h1=self.H1,
            contents=f'## 見出し\n{self.BODY}',
            meta_description=self.SUMMARY,
            youtube_url=VIDEO_URL,
        )
        self.url = reverse('event:detail', kwargs={'pk': self.detail.pk})

    def _set_consent(self, consent):
        EventDetail.objects.filter(pk=self.detail.pk).update(article_consent=consent)

    def test_ng_hides_article_body_and_summary(self):
        self._set_consent(ArticleConsent.NG)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.H1)
        self.assertNotContains(response, self.BODY)
        self.assertNotContains(response, self.SUMMARY)
        self.assertContains(response, '<h1 class="mb-3">発表のテーマ</h1>')
        self.assertContains(response, '<title>発表のテーマ - VRChat 技術・学術系イベントHub</title>')

    def test_ng_keeps_article_data_and_ok_shows_it_again(self):
        self._set_consent(ArticleConsent.NG)
        self.client.get(self.url)
        self._set_consent(ArticleConsent.OK)

        response = self.client.get(self.url)

        self.assertContains(response, self.H1)
        self.assertContains(response, self.BODY)
        self.assertContains(response, self.SUMMARY)

    def test_unanswered_keeps_showing_article(self):
        response = self.client.get(self.url)

        self.assertContains(response, self.H1)
        self.assertContains(response, self.BODY)
        self.assertContains(response, self.SUMMARY)

    def test_ng_hides_generate_button_for_organizer(self):
        self.client.force_login(self.owner)
        self._set_consent(ArticleConsent.NG)

        response = self.client.get(self.url)

        self.assertNotContains(response, 'id="generate-button"')
        self.assertContains(response, '発表者が記事化を NG にしているため')

    def test_ok_shows_generate_button_for_organizer(self):
        self.client.force_login(self.owner)
        self._set_consent(ArticleConsent.OK)

        response = self.client.get(self.url)

        self.assertContains(response, 'id="generate-button"')
        self.assertContains(response, CONSENT_OK_LABEL)

    @patch('event.views.blog.generate_blog')
    def test_generate_view_rejects_ng(self, mock_generate_blog):
        self.client.force_login(self.owner)
        self._set_consent(ArticleConsent.NG)

        response = self.client.post(reverse('event:generate_blog', kwargs={'pk': self.detail.pk}))

        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        mock_generate_blog.assert_not_called()
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.h1, self.H1)
