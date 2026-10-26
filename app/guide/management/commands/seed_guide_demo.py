"""使い方ガイドの撮影専用データ。外部通知は送らない。"""
import importlib
import pkgutil
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from community.models import CommunityInvitation
from event.models import RecurrenceRule
from tests.factories import (
    make_community, make_community_member, make_discord_linked_user,
    make_event, make_event_detail,
)

from guide.demo import DEMO_USERS, demo_password

# 撮り直しで日付が変わらない固定基準日。説明用の架空イベントとして扱う。
BASE_DATE = date(2030, 4, 1)


def seed_users():
    password = demo_password()
    return {role: make_discord_linked_user(user_name=name, email=email, password=password,
                                          discord_uid=f'guide-demo-{role}')
            for role, (name, email) in DEMO_USERS.items()}


def seed_communities(users):
    community = make_community(name='さくら技術交流会', owner=users['owner'], organizers='さくら',
                               description='VRChat で技術を学ぶ架空の集会です。',
                               created_at=BASE_DATE, tags=['tech'],
                               webhook_url='https://discord.com/api/webhooks/000000000000/DUMMY')
    make_community_member(community, users['staff'])
    make_community(name='レンの学びの会', owner=users['staff'], organizers='レン',
                   status='pending', created_at=BASE_DATE)
    return community


def seed_events(community, users):
    rule = RecurrenceRule.objects.create(community=community, frequency='WEEKLY', start_date=BASE_DATE)
    event = make_event(community, event_date=BASE_DATE + timedelta(days=7), recurrence_rule=rule,
                       is_recurring_master=True)
    make_event_detail(event, applicant=users['speaker'], speaker='アオイ', theme='はじめてのワールド制作',
                      status='approved', start_time=time(22))
    make_event_detail(event, applicant=users['general'], speaker='ヒナ', theme='身近な科学を楽しむ',
                      status='pending', start_time=time(22, 30))


def seed_invitations(community, users):
    CommunityInvitation.objects.create(community=community, created_by=users['owner'],
                                       token='guide-demo-staff-invitation',
                                       expires_at=datetime(2030, 4, 30, tzinfo=timezone.utc))


def seed_sections(context):
    """guide/demo_seeds/<セクション>.py の seed(context) を名前順に呼ぶ。

    ページの撮影に要る状態はセクションごとのファイルに足す（並行して書いても衝突しないように）。
    context は users（役割→ユーザー）・community（主催者の集会）・base_date を持つ辞書。
    """
    for module in _section_modules():
        module.seed(context)


def finalize_sections(context):
    """各セクションの finalize(context) を名前順に呼ぶ。

    seed のトランザクションを閉じた後に呼ぶ。発表の保存などで on_commit に積まれた同期処理（告知キューの
    作り直しなど）が先に終わるので、その後の表示用の状態をここで整える。
    """
    for module in _section_modules():
        if hasattr(module, 'finalize'):
            module.finalize(context)


def _section_modules():
    from guide import demo_seeds
    return [importlib.import_module(f'guide.demo_seeds.{module.name}')
            for module in sorted(pkgutil.iter_modules(demo_seeds.__path__), key=lambda m: m.name)
            if not module.name.startswith('_')]


class Command(BaseCommand):
    help = '撮影専用の空の一時 SQLite DB に架空のデータを作成します'

    def handle(self, *args, **options):
        database = settings.DATABASES['default']
        # 誤操作で通常の開発 DB を触らない。run.sh が発行した専用設定だけを許可する。
        demo_root = getattr(settings, 'GUIDE_DEMO_ROOT', None)
        if (not settings.DEBUG or database['ENGINE'] != 'django.db.backends.sqlite3' or not demo_root
                or Path(database['NAME']).resolve().parent != Path(demo_root).resolve()):
            raise CommandError('run.sh の一時 SQLite 環境でのみ実行できます')
        with transaction.atomic():
            from django.contrib.auth import get_user_model
            if get_user_model().objects.exists():
                self.stdout.write('guide_demo: 既存のデモデータを保持しました')
                return
            users = seed_users()
            community = seed_communities(users)
            seed_events(community, users)
            seed_invitations(community, users)
            context = {'users': users, 'community': community, 'base_date': BASE_DATE}
            seed_sections(context)
        with transaction.atomic():
            finalize_sections(context)
        self.stdout.write('guide_demo: users=4 communities=2 events=1 presentations=2 invitations=1')
