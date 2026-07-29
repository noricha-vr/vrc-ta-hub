"""共有factoryへ移行中のcore model直接生成件数をratchetする。"""

import ast
from unittest import TestCase

from tests._factory_usage_guard import (
    count_direct_core_creates,
    find_direct_core_creates,
)


EXPECTED_DIRECT_CREATES_BY_MODEL = {
    'Community': 258,
    'CustomUser': 119,
    'Event': 160,
    'EventDetail': 157,
    'User': 107,
}


class FactoryUsageRatchetTest(TestCase):
    """core model fixtureの直接生成負債が増えないことを検証する。"""

    def test_explicit_import_matcher_ignores_unrelated_and_reexported_models(self):
        """明示的なcanonical importだけを解決しre-exportは数えない。"""
        source = """
from community.models import Community
from event.models import Event as CalendarEvent
from user_account.models import CustomUser as AccountUser
import event.models as event_models
import community.models
from django.contrib.auth import get_user_model as resolve_user_model
from unrelated.models import Community as UnrelatedCommunity
from twitter.tests._auto_tweet_test_base import CustomUser as ReexportedUser

Account = resolve_user_model()

Community.objects.create(name='counted')
CalendarEvent.objects.create(name='counted')
AccountUser.objects.create_user(user_name='counted')
event_models.EventDetail.objects.create(name='counted')
community.models.Community.objects.create(name='counted')
Account.objects.create_user(user_name='counted')

UnrelatedCommunity.objects.create(name='ignored')
ReexportedUser.objects.create_user(user_name='ignored')

def use_local_shadow():
    Community = object()
    Community.objects.create(name='ignored')

def use_unimported_local_model():
    class EventDetail:
        pass
    EventDetail.objects.create(name='ignored')

CommunityFactory.create(name='ignored')
CommunityFactory.objects.create(name='ignored')
"""
        identities = find_direct_core_creates(ast.parse(source))

        self.assertCountEqual(
            identities,
            [
                ('Community', 'create'),
                ('Event', 'create'),
                ('CustomUser', 'create_user'),
                ('EventDetail', 'create'),
                ('Community', 'create'),
                ('User', 'create_user'),
            ],
        )

    def test_relative_import_resolves_canonical_model_from_file_package(self):
        """file packageを基準にcanonical modelsの相対importを解決する。"""
        source = """
from ..models import Community as RelativeCommunity

RelativeCommunity.objects.create(name='counted')
"""
        identities = find_direct_core_creates(
            ast.parse(source),
            package_path=('community', 'tests'),
        )

        self.assertEqual(identities, [('Community', 'create')])

    def test_lambda_and_comprehension_targets_shadow_module_model(self):
        """内包表記とlambdaのローカル束縛をcore modelとして数えない。"""
        source = """
from community.models import Community

values = []
(lambda Community: Community.objects.create(name='ignored'))(object())
[Community.objects.create(name='ignored') for Community in values]
{Community.objects.create(name='ignored') for Community in values}
{Community: Community.objects.create(name='ignored') for Community in values}
(Community.objects.create(name='ignored') for Community in values)

[value for value in Community.objects.create(name='outer-iter-counted')]
Community.objects.create(name='global-counted')

Community = object()
Community.objects.create(name='module-shadow-ignored')
"""
        identities = find_direct_core_creates(ast.parse(source))

        self.assertEqual(
            identities,
            [
                ('Community', 'create'),
                ('Community', 'create'),
            ],
        )

    def test_direct_core_model_fixture_snapshot_matches_exactly(self):
        """共有factory外の直接生成件数が意図したsnapshotと完全一致する。"""
        counts = count_direct_core_creates()
        expected_total = sum(EXPECTED_DIRECT_CREATES_BY_MODEL.values())
        snapshot_guidance = (
            'Direct fixture snapshot changed. Both increases and reductions require '
            'intentional fixture review and EXPECTED_DIRECT_CREATES_BY_MODEL update.'
        )

        self.assertEqual(set(counts), set(EXPECTED_DIRECT_CREATES_BY_MODEL), snapshot_guidance)
        for model_name, expected in EXPECTED_DIRECT_CREATES_BY_MODEL.items():
            with self.subTest(model=model_name):
                self.assertEqual(
                    counts[model_name],
                    expected,
                    f'{model_name}: expected {expected}, observed {counts[model_name]}. {snapshot_guidance}',
                )
        self.assertEqual(
            counts.total(),
            expected_total,
            f'total: expected {expected_total}, observed {counts.total()}. {snapshot_guidance}',
        )
