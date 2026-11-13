"""集会の掲載設定と公開範囲のテスト。"""

from datetime import timedelta

from django.contrib import admin
from django.db import connection, models
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from community.forms import CommunityCreateForm, CommunityUpdateForm
from community.models import Community
from tests.factories import make_community, make_user


class CommunityListingTest(TestCase):
    def setUp(self):
        self.listed = make_community(name='掲載する集会', poster_image='poster/listed.jpg', tags=['tech'])
        self.unlisted = make_community(
            name='掲載しない集会', poster_image='poster/unlisted.jpg', tags=['tech'], is_listed=False,
        )

    def test_is_listed_defaults_to_true(self):
        self.listed.refresh_from_db()
        field = Community._meta.get_field('is_listed')
        self.assertIsInstance(field, models.BooleanField)
        self.assertIs(self.listed.is_listed, True)
        self.assertIs(field.default, True)
        self.assertIs(field.db_default, True)
        self.assertEqual(field.verbose_name, '集会一覧に掲載する')

    def test_legacy_insert_without_listing_column_uses_db_default(self):
        columns = ', '.join(
            connection.ops.quote_name(field.column)
            for field in Community._meta.local_concrete_fields
            if field.name not in {'id', 'is_listed'}
        )
        table = connection.ops.quote_name(Community._meta.db_table)
        with connection.cursor() as cursor:
            cursor.execute(
                f'INSERT INTO {table} ({columns}) SELECT {columns} FROM {table} WHERE id = %s',
                [self.listed.pk],
            )

        inserted = Community.objects.exclude(pk__in=[self.listed.pk, self.unlisted.pk]).get()
        self.assertIs(inserted.is_listed, True)

    def test_list_excludes_unlisted_community(self):
        response = self.client.get(reverse('community:list'))

        self.assertEqual(response.status_code, 200)
        self.assertQuerySetEqual(response.context['communities'], [self.listed])

    def test_archive_excludes_unlisted_community(self):
        Community.objects.filter(pk__in=[self.listed.pk, self.unlisted.pk]).update(
            end_at=timezone.localdate() - timedelta(days=1),
        )

        response = self.client.get(reverse('community:archive_list'))

        self.assertEqual(response.status_code, 200)
        self.assertQuerySetEqual(response.context['communities'], [self.listed])

    def test_unlisted_community_detail_remains_public(self):
        response = self.client.get(reverse('community:detail', args=[self.unlisted.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['community'], self.unlisted)

    def test_admin_can_edit_and_filter_listing_setting(self):
        model_admin = admin.site._registry[Community]
        request = RequestFactory().get('/admin/community/community/')
        request.user = make_user(is_staff=True, is_superuser=True)

        self.assertIn('is_listed', model_admin.get_list_display(request))
        self.assertIn('is_listed', model_admin.get_list_filter(request))
        form = model_admin.get_form(request, obj=self.unlisted)(instance=self.unlisted)
        self.assertIn('is_listed', form.fields)
        self.assertIs(form.initial['is_listed'], False)

    def test_organizer_forms_do_not_expose_listing_setting(self):
        for form_class in (CommunityCreateForm, CommunityUpdateForm):
            with self.subTest(form=form_class.__name__):
                self.assertNotIn('is_listed', form_class().fields)
