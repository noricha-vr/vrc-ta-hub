"""共有factoryへ移行中のcore model直接生成件数をratchetする。"""

import ast
from unittest import TestCase

from tests._factory_usage_guard import (
    count_direct_core_creates,
    find_direct_core_creates,
)


MAX_DIRECT_CREATES_BY_MODEL = {
    'Community': 258,
    'CustomUser': 119,
    'Event': 160,
    'EventDetail': 157,
    'User': 107,
}
MAX_DIRECT_CORE_CREATES = 801


class FactoryUsageRatchetTest(TestCase):
    """core model fixtureの直接生成負債が増えないことを検証する。"""

    def test_ast_matcher_identifies_only_core_model_manager_calls(self):
        """import解決できるcore model manager callだけを数える。"""
        source = """
from community.models import Community
from event.models import Event as CalendarEvent
from user_account.models import CustomUser as AccountUser
import event.models as event_models
import community.models
from django.contrib.auth import get_user_model as resolve_user_model
from unrelated.models import Community as UnrelatedCommunity

Account = resolve_user_model()

Community.objects.create(name='counted')
CalendarEvent.objects.create(name='counted')
AccountUser.objects.create_user(user_name='counted')
event_models.EventDetail.objects.create(name='counted')
community.models.Community.objects.create(name='counted')
Account.objects.create_user(user_name='counted')

UnrelatedCommunity.objects.create(name='ignored')

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

    def test_direct_core_model_fixture_debt_does_not_increase(self):
        """共有factory外の直接生成は今回のpost値以下に保つ。"""
        counts = count_direct_core_creates()

        for model_name, maximum in MAX_DIRECT_CREATES_BY_MODEL.items():
            with self.subTest(model=model_name):
                self.assertLessEqual(
                    counts[model_name],
                    maximum,
                    f'{model_name}.objects direct create debt increased: {counts[model_name]} > {maximum}',
                )
        self.assertLessEqual(
            counts.total(),
            MAX_DIRECT_CORE_CREATES,
            f'repo test direct core create debt increased: {counts.total()} > {MAX_DIRECT_CORE_CREATES}',
        )
