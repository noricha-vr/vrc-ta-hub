"""メンバー管理ガイドの固定期限・固定トークン。"""
from datetime import datetime, timedelta, timezone

from community.models import Community, CommunityInvitation, CommunityMember


def seed(context):
    fixed = datetime(2030, 4, 1, 12, tzinfo=timezone.utc)
    target = Community.objects.get(name='こもれび技術勉強会')
    CommunityInvitation.objects.create(
        community=target, created_by=context['users']['owner'],
        token='guide-demo-ownership-transfer',
        invitation_type=CommunityInvitation.InvitationType.OWNERSHIP_TRANSFER,
        expires_at=fixed + timedelta(days=7),
    )
    CommunityInvitation.objects.all().update(created_at=fixed, expires_at=fixed + timedelta(days=7))
    CommunityMember.objects.all().update(created_at=fixed)
