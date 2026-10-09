"""発表の記事化の同意（EventDetail.article_consent）のテスト。

申請・編集フォームでの保存、主催者画面での表示だけの扱い、NG の発表の非表示と生成の拒否を確かめる。
"""

import re
from datetime import date, timedelta
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from event.forms import EventDetailForm, LTApplicationEditForm
from event.models import EventDetail
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

    def test_generation_checkbox_is_hidden_for_ng(self):
        ng_form = LTApplicationEditForm(instance=make_event_detail(self.event, article_consent=ArticleConsent.NG))
        ok_form = LTApplicationEditForm(instance=make_event_detail(self.event, article_consent=ArticleConsent.OK))

        self.assertNotIn('generate_blog_article', ng_form.fields)
        self.assertIn('generate_blog_article', ok_form.fields)


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
        self.url = reverse('account:lt_application_edit', kwargs={'pk': self.detail.pk})
        self.client.force_login(self.user)

    def test_edit_page_shows_consent_choices(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '発表の記事化')
        self.assertRegex(
            response.content.decode(),
            r'<input type="radio" name="article_consent" value="ok"[^>]*checked',
        )

    @patch('event.services.content_generation_service.generate_blog')
    def test_switching_to_ng_with_checkbox_does_not_generate(self, mock_generate_blog):
        """同じ送信で NG に変えたら、チェックボックスが ON でも生成しない。"""
        response = self.client.post(self.url, {
            'theme': 'テーマ',
            'speaker': '発表者',
            'youtube_url': VIDEO_URL,
            'article_consent': 'ng',
            'generate_blog_article': 'on',
        })

        self.assertEqual(response.status_code, 302)
        mock_generate_blog.assert_not_called()
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.article_consent, ArticleConsent.NG)


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


class EventDetailPageArticleConsentTest(TestCase):
    """NG の発表は詳細ページで記事の本文（h1 / contents）と要約を出さない。"""

    H1 = '生成した記事のタイトル'
    BODY = '記事の本文テキスト'
    SUMMARY = '記事の要約テキスト'

    def setUp(self):
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
