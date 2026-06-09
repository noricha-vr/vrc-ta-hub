"""テスト共有 factory ヘルパー.

各テストの setUp で User/Community/Event/EventDetail を毎回手書きするコストを
下げるための薄い factory 群。

Django 標準の ``TestCase`` 前提（pytest-django は導入しない）。新規テスト追加時は
まずこのモジュールを確認し、足りないキーワード引数があれば ``**extra`` 経由で
渡すか、本モジュールを拡張する。

使い方::

    from app.tests.factories import (
        make_user,
        make_community,
        make_event,
        make_event_detail,
    )

    class SomeTest(TestCase):
        def setUp(self):
            self.owner = make_user(user_name="owner1", email="owner1@example.com")
            self.community = make_community(owner=self.owner)
            self.event = make_event(self.community)
            self.detail = make_event_detail(self.event, applicant=self.owner)
"""
from __future__ import annotations

from datetime import date, time, timedelta
from typing import Optional

from django.contrib.auth import get_user_model

from community.models import Community, CommunityMember
from event.models import Event, EventDetail

# Discord 連携付きユーザーは ``user_account.tests.utils`` 側を正本にして再利用する。
# import 時の循環や allauth 未ロード状態を避けるため、関数内 import で遅延読み込みする。

User = get_user_model()


def make_user(
    user_name: str = "testuser",
    email: str = "test@example.com",
    password: str = "testpass123",
    **extra,
):
    """通常ユーザー（Discord 未連携）を作成する.

    ``DiscordAuthRequiredMiddleware`` を通る画面テストでは
    :func:`make_discord_linked_user` を使うこと。本関数はモデル層・通知・
    フォーム単体テストのように middleware を経由しないテスト向け。

    Args:
        user_name: ``CustomUser.user_name`` に入る値（``username`` ではない）。
        email: メールアドレス。空文字を渡すと email 未設定ユーザーになる。
        password: 生パスワード（``create_user`` 側でハッシュ化される）。
        **extra: ``CustomUser`` への追加フィールド。

    Returns:
        作成された ``CustomUser`` インスタンス。
    """
    return User.objects.create_user(
        user_name=user_name,
        email=email,
        password=password,
        **extra,
    )


def make_discord_linked_user(
    user_name: str = "discorduser",
    email: str = "discord@example.com",
    password: str = "testpass123",
    discord_uid: Optional[str] = None,
    **extra,
):
    """Discord 連携済みユーザーを作成する.

    ``user_account.tests.utils.create_discord_linked_user`` への薄い wrapper。
    既存テストとの互換性を保つため、引数名・挙動はそちらに揃えてある。
    middleware (``DiscordAuthRequiredMiddleware``) によりリダイレクトされる
    画面テストではこちらを使う。

    Args:
        user_name: ``CustomUser.user_name``。
        email: メールアドレス。
        password: 生パスワード。
        discord_uid: Discord UID。未指定なら ``f"discord_{user_name}"``。
        **extra: ``CustomUser`` への追加フィールド。

    Returns:
        Discord ``SocialAccount`` を持つ ``CustomUser`` インスタンス。
    """
    # 遅延 import: allauth の app loading 順を尊重するため
    from user_account.tests.utils import create_discord_linked_user

    return create_discord_linked_user(
        user_name=user_name,
        email=email,
        password=password,
        discord_uid=discord_uid,
        **extra,
    )


def make_community(
    name: str = "Test Community",
    owner=None,
    status: str = "approved",
    webhook_url: str = "",
    **extra,
):
    """テスト用 Community を作成する.

    必須フィールドはデフォルト値で埋める。``owner`` を渡すと OWNER ロールの
    ``CommunityMember`` を同時に作る（権限判定は ``CommunityMember`` 経由のため、
    ``organizers`` 文字列だけでは ``is_owner`` 等が True にならない）。

    Args:
        name: 集会名。
        owner: ``CustomUser``。指定時は OWNER として CommunityMember を作成。
        status: ``'pending' | 'approved' | 'rejected' | 'closed'`` 等。
        webhook_url: Discord 通知 webhook URL。空なら通知 OFF 相当。
        **extra: ``Community`` への追加フィールド
            （``start_time`` / ``duration`` / ``weekdays`` 等を上書きしたい時に使う）。

    Returns:
        作成された ``Community`` インスタンス。
    """
    defaults = {
        "start_time": time(22, 0),
        "duration": 60,
        "weekdays": ["Mon"],
        "frequency": "Every week",
        "organizers": "Test Organizer",
        "notification_webhook_url": webhook_url,
    }
    defaults.update(extra)

    community = Community.objects.create(
        name=name,
        status=status,
        **defaults,
    )
    if owner is not None:
        CommunityMember.objects.create(
            community=community,
            user=owner,
            role=CommunityMember.Role.OWNER,
        )
    return community


def make_event(
    community,
    event_date: Optional[date] = None,
    accepts_lt_application: bool = True,
    **extra,
):
    """テスト用 Event を作成する.

    ``date`` は組み込み型と衝突するため、引数名を ``event_date`` にしている。
    未指定なら ``today + 7 days``（未来イベント）になる。

    Args:
        community: 紐づく ``Community``。
        event_date: 開催日。未指定なら 7 日後。
        accepts_lt_application: 発表申請受付可否。
        **extra: ``Event`` への追加フィールド
            （``start_time`` / ``duration`` / ``weekday`` 等を上書きしたい時）。

    Returns:
        作成された ``Event`` インスタンス。
    """
    if event_date is None:
        event_date = date.today() + timedelta(days=7)

    defaults = {
        "start_time": time(22, 0),
        "duration": 60,
        "weekday": "Mon",
    }
    defaults.update(extra)

    return Event.objects.create(
        community=community,
        date=event_date,
        accepts_lt_application=accepts_lt_application,
        **defaults,
    )


def make_event_detail(
    event,
    applicant=None,
    status: str = "pending",
    speaker: str = "Speaker A",
    theme: str = "サンプル発表",
    detail_type: str = "LT",
    duration: int = 30,
    additional_info: str = "",
    **extra,
):
    """テスト用 EventDetail（発表詳細）を作成する.

    ``start_time`` 未指定時は親 ``event.start_time`` を引き継ぐ。発表枠の重複や
    時刻ロジックを検証したい場合は ``start_time=time(...)`` を明示する。

    Args:
        event: 紐づく ``Event``。
        applicant: 申請ユーザー。None なら無申請（管理者直接作成扱い）。
        status: ``'pending' | 'approved' | 'rejected'``。
        speaker: 発表者表示名。
        theme: 発表テーマ。
        detail_type: ``'LT'`` 等。
        duration: 発表時間（分）。
        additional_info: 追加情報フリーテキスト。
        **extra: ``EventDetail`` への追加フィールド
            （``start_time`` / ``rejection_reason`` 等を上書きしたい時）。

    Returns:
        作成された ``EventDetail`` インスタンス。
    """
    defaults = {
        "start_time": event.start_time,
    }
    defaults.update(extra)

    return EventDetail.objects.create(
        event=event,
        speaker=speaker,
        theme=theme,
        detail_type=detail_type,
        duration=duration,
        applicant=applicant,
        status=status,
        additional_info=additional_info,
        **defaults,
    )
