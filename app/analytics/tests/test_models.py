from datetime import date

from django.db import IntegrityError, transaction
from django.test import TestCase

from community.models import Community

from analytics.models import PageAnalytics


class PageAnalyticsModelTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.community = Community.objects.create(
            name='テスト集会',
            frequency='毎週',
            organizers='主催A',
        )

    def test_create(self):
        record = PageAnalytics.objects.create(
            page_path=f'/community/{self.community.pk}/',
            date=date(2026, 5, 1),
            content_type=PageAnalytics.ContentType.COMMUNITY,
            community=self.community,
            object_id=self.community.pk,
            pv=10,
            users=8,
            sessions=9,
            source_medium='google / organic',
        )
        self.assertEqual(record.pv, 10)
        self.assertEqual(PageAnalytics.objects.count(), 1)

    def test_unique_together_violation_raises_integrity_error(self):
        common = dict(
            page_path=f'/community/{self.community.pk}/',
            date=date(2026, 5, 1),
            content_type=PageAnalytics.ContentType.COMMUNITY,
            community=self.community,
            object_id=self.community.pk,
            source_medium='google / organic',
        )
        PageAnalytics.objects.create(pv=1, users=1, sessions=1, **common)
        # 同じ (page_path, date, source_medium) は unique_together 違反
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PageAnalytics.objects.create(pv=2, users=2, sessions=2, **common)
