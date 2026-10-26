"""管理画面の継承と、後続の画面が利用する部品の契約。"""
from types import SimpleNamespace

from bs4 import BeautifulSoup
from django import forms
from django.contrib.auth.models import AnonymousUser
from django.template import Context, Template
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase

from website.constants import SITE_NAME_SEO


class AdminUITemplateTest(SimpleTestCase):
    def render(self, name, **context):
        return render_to_string(name, context)

    def soup(self, name, **context):
        return BeautifulSoup(self.render(name, **context), 'html.parser')

    def test_existing_settings_title_is_used_once(self):
        html = self.render('community/settings.html', community=SimpleNamespace(
            pk=1, name='テスト集会', weekdays=[], members=SimpleNamespace(all=lambda: []),
        ))
        titles = BeautifulSoup(html, 'html.parser').find_all('title')
        self.assertEqual([title.text for title in titles], ['集会設定 - テスト集会'])

    def test_default_title_and_public_css_are_unchanged(self):
        html = self.render('ta_hub/base.html')
        soup = BeautifulSoup(html, 'html.parser')
        self.assertEqual(soup.title.text, f'{SITE_NAME_SEO} - VRChat技術学術系集会の世界へ！ようこそ！')
        self.assertNotIn('admin-ui.css', html)
        self.assertNotIn('admin-ui.js', html)

    def test_meta_override_does_not_duplicate_title(self):
        html = Template("{% extends 'ta_hub/base.html' %}{% block meta %}<title>独自タイトル</title>"
                        "{% endblock %}{% block title %}未使用{% endblock %}").render(Context())
        self.assertEqual([t.text for t in BeautifulSoup(html, 'html.parser').find_all('title')], ['独自タイトル'])

    def test_admin_skeleton_order_and_assets(self):
        html = Template("{% extends 'ta_hub/admin_base.html' %}"
                        "{% block admin_breadcrumbs %}順序1{% endblock %}"
                        "{% block admin_header %}順序2{% endblock %}"
                        "{% block admin_subnav %}順序3{% endblock %}"
                        "{% block messages %}順序4{% endblock %}"
                        "{% block admin_attention %}順序5{% endblock %}"
                        "{% block admin_toc %}<aside class='admin-toc'>順序6</aside>{% endblock %}"
                        "{% block admin_content %}順序7{% endblock %}"
                        "{% block admin_action_bar %}順序8{% endblock %}").render(Context())
        soup = BeautifulSoup(html, 'html.parser')
        self.assertIsNotNone(soup.select_one('main > .hub-admin .admin-content'))
        self.assertEqual(sorted(range(1, 9), key=lambda n: html.index(f'順序{n}')), list(range(1, 9)))
        self.assertIn('admin-ui.css', html)
        self.assertIn('admin-ui.js', html)
        self.assertGreater(html.index('admin-ui.css'), html.index('</style>'))

    def test_page_header_escapes_target_and_actions(self):
        soup = self.soup('ta_hub/admin/includes/page_header.html', title='集会設定', target='<script>対象</script>',
                         actions=[{'url': '/public/', 'label': '公開ページ'},
                                  {'url': '/new/', 'label': '登録', 'primary': True}])
        self.assertEqual(soup.h1.text, '集会設定: <script>対象</script>')
        self.assertIsNone(soup.script)
        self.assertEqual(soup.select_one('.btn-primary')['href'], '/new/')

    def test_subnav_only_renders_supplied_links(self):
        soup = self.soup('ta_hub/admin/includes/subnav.html', items=[
            {'url': '/events/', 'label': 'イベント', 'current': True},
            {'url': '/manage/', 'label': '管理', 'operator': True},
        ])
        self.assertEqual(soup.select_one('[aria-current="page"]').text, 'イベント')
        self.assertEqual(soup.select_one('.badge').text, '運営')
        self.assertEqual(len(soup.find_all('a')), 2)

    def test_confirm_panel_keeps_post_target_csrf_and_escaped_message(self):
        request = RequestFactory().get('/')
        request.user = AnonymousUser()
        html = render_to_string('ta_hub/admin/includes/confirm_panel.html', {
            'panel_id': 'delete-1', 'label': '削除の確認', 'message': '<b>対象</b>を削除しますか？',
            'action_url': '/delete/1/', 'submit_label': '対象を削除する',
            'submit_name': 'action', 'submit_value': 'delete',
        }, request=request)
        soup = BeautifulSoup(html, 'html.parser')
        self.assertTrue(soup.select_one('#delete-1').has_attr('hidden'))
        self.assertIsNone(soup.b)
        self.assertEqual(soup.form['method'], 'post')
        self.assertEqual(soup.form['action'], '/delete/1/')
        self.assertTrue(soup.select_one('[name="csrfmiddlewaretoken"]')['value'])
        self.assertEqual(soup.select_one('[type="submit"]')['name'], 'action')
        self.assertEqual(soup.select_one('[data-cancel-panel]')['data-cancel-panel'], 'delete-1')

    def test_confirm_panel_direct_page_is_visible_and_has_cancel_link(self):
        soup = self.soup('ta_hub/admin/includes/confirm_panel.html', panel_id='delete-1', open=True,
                         label='削除の確認', message='対象を削除しますか？', action_url='/delete/1/',
                         submit_label='削除する', cancel_url='/list/')
        self.assertFalse(soup.select_one('#delete-1').has_attr('hidden'))
        self.assertEqual(soup.a['href'], '/list/')

    def test_action_bar_submits_each_existing_form_separately(self):
        soup = self.soup('ta_hub/admin/includes/action_bar.html', cancel_url='/list/', actions=[
            {'form_id': 'community-form', 'scope': '集会情報', 'label': '集会情報を保存'},
            {'form_id': 'notification-form', 'scope': '通知', 'label': '通知を保存',
             'name': 'action', 'value': 'save_notifications'},
        ])
        self.assertEqual([button['form'] for button in soup.find_all('button')],
                         ['community-form', 'notification-form'])
        self.assertEqual(len(soup.select('[data-admin-form-status]')), 2)
        self.assertIsNone(soup.form)
        self.assertEqual(soup.select_one('[name="action"]')['value'], 'save_notifications')
        inline = self.soup('ta_hub/admin/includes/action_bar.html', inline=True, actions=[])
        self.assertIsNone(inline.select_one('[data-admin-action-bar]'))
        self.assertIsNone(inline.select_one('.stickybar'))

    def test_secret_field_never_renders_saved_or_bound_value(self):
        class SecretForm(forms.Form):
            webhook = forms.CharField(label='通知先', required=False)

        secret = 'private-dummy-value-12345678'
        for form in (SecretForm(initial={'webhook': secret}), SecretForm(data={'webhook': secret})):
            html = self.render('ta_hub/admin/includes/secret_field.html', field=form['webhook'],
                               label='通知先', configured=True, suffix=secret)
            soup = BeautifulSoup(html, 'html.parser')
            self.assertNotIn(secret, html)
            self.assertNotIn('12345678', html)
            self.assertIn('5678', html)
            self.assertFalse(soup.input.has_attr('value'))
            self.assertTrue(soup.input.has_attr('disabled'))
            self.assertEqual(soup.input['type'], 'password')

    def test_secret_field_shows_errors_without_echoing_input(self):
        class SecretForm(forms.Form):
            api_key = forms.CharField(min_length=20)

        form = SecretForm(data={'api_key': 'dummy-short-secret'})
        soup = self.soup('ta_hub/admin/includes/secret_field.html', field=form['api_key'], label='キー', configured=True)
        self.assertTrue(soup.details.has_attr('open'))
        self.assertEqual(soup.input['aria-invalid'], 'true')
        self.assertFalse(soup.input.has_attr('value'))
        self.assertFalse(soup.input.has_attr('disabled'))
        self.assertIsNotNone(soup.select_one('.invalid-feedback'))

    def test_setup_link_supports_url_and_existing_post_form(self):
        soup = self.soup('ta_hub/admin/includes/setup_link.html', url='/setup/', label='設定する',
                         setup_type='calendar', community_id=12)
        self.assertEqual(soup.a['href'], '/setup/')
        self.assertIn('setup-placeholder', soup.a['class'])
        self.assertEqual(soup.a['data-mylist-setup'], 'calendar')
        soup = self.soup('ta_hub/admin/includes/setup_link.html', form_id='switch-form', label='設定する')
        self.assertEqual(soup.button['form'], 'switch-form')
        self.assertEqual(soup.button['type'], 'submit')
        self.assertIsNone(soup.form)

    def test_field_errors_section_empty_state_and_status_chip(self):
        class ExampleForm(forms.Form):
            name = forms.CharField(label='名前')

        form = ExampleForm(data={'name': ''})
        soup = self.soup('ta_hub/admin/includes/form_field.html', field=form['name'])
        self.assertEqual(soup.label['for'], 'id_name')
        self.assertIn('is-invalid', soup.input['class'])
        soup = self.soup('ta_hub/admin/includes/form_errors.html', form=form)
        self.assertEqual(soup.a['href'], '#id_name')
        soup = self.soup('ta_hub/admin/includes/section_heading.html', title='通知', save_scope='通知', form_id='notify')
        self.assertEqual(soup.h2.text, '通知')
        self.assertIn('このカードだけ保存: 通知', soup.text)
        soup = self.soup('ta_hub/admin/includes/empty_state.html', title='未登録', action_url='/new/', action_label='登録する')
        self.assertEqual(soup.a['href'], '/new/')
        soup = self.soup('ta_hub/admin/includes/status_chip.html', label='承認待ち', tone='warning')
        self.assertEqual(soup.span.text, '承認待ち')
        self.assertIn('bg-warning-subtle', soup.span['class'])
