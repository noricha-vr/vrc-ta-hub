import secrets
from datetime import timedelta

from django.db import models
from django.utils import timezone

from ta_hub.libs import resize_and_convert_image

# Create your models here.
WEEKDAY_CHOICES = (
    ('Sun', '日曜日'),
    ('Mon', '月曜日'),
    ('Tue', '火曜日'),
    ('Wed', '水曜日'),
    ('Thu', '木曜日'),
    ('Fri', '金曜日'),
    ('Sat', '土曜日'),
    ('Other', 'その他')
)

PLATFORM_CHOICES = (
    ('PC', 'PC'),
    ('All', 'PC / モバイル (Meta Quest 単体)'),
    ('Android', 'モバイル (Meta Quest 単体)'),
)
FORM_TAGS = (
    ('tech', '技術系集会'),
    ('academic', '学術系集会'),
)
TAGS = (
    ('tech', '技術系集会'),
    ('academic', '学術系集会'),
    ('partner', '協力団体'),
)

STATUS_CHOICES = (
    ('pending', '承認待ち'),
    ('approved', '承認済み'),
    ('rejected', '非承認'),
)


class Community(models.Model):
    custom_user = models.ForeignKey('user_account.CustomUser', on_delete=models.CASCADE, verbose_name='ユーザー',
                                    default=None, blank=True, null=True)
    name = models.CharField('集会名', max_length=100, db_index=True)
    created_at = models.DateField('開始日', default=timezone.now, blank=True, null=True, db_index=True)
    updated_at = models.DateField('更新日', auto_now=True, db_index=True)
    end_at = models.DateField('終了日', default=None, blank=True, null=True)
    start_time = models.TimeField('開始時刻', default='22:00', db_index=True)
    duration = models.IntegerField('開催時間', default=60, help_text='単位は分')
    weekdays = models.JSONField('曜日', default=list, blank=True)  # JSONFieldに変更
    frequency = models.CharField('開催周期', max_length=100)
    organizers = models.CharField('主催・副主催', max_length=200)
    group_url = models.URLField('VRChatグループURL', blank=True)
    organizer_url = models.URLField('主催プロフィールURL', blank=True)
    sns_url = models.URLField('SNS', blank=True)
    discord = models.URLField('Discord', blank=True)
    twitter_hashtag = models.CharField('Twitterハッシュタグ', max_length=100, blank=True)
    poster_image = models.ImageField('ポスター', upload_to='poster/', blank=True)
    description = models.TextField('集会紹介', default='', blank=True)
    platform = models.CharField('対応プラットフォーム', max_length=10, choices=PLATFORM_CHOICES, default='All')
    tags = models.JSONField('タグ', max_length=10, default=list)
    status = models.CharField('承認状態', max_length=20, choices=STATUS_CHOICES, default='pending', db_index=True)
    allow_poster_repost = models.BooleanField(
        '集会を紹介するためのポスター転載を許可する',
        default=False,
    )
    notification_webhook_url = models.URLField('Discord Webhook URL', blank=True, default='')

    class Meta:
        verbose_name = '集会'
        verbose_name_plural = '集会'
        db_table = 'community'

    def __str__(self):
        return self.name

    @property
    def end_time(self):
        return (timezone.datetime.combine(timezone.datetime.today(), self.start_time) + timedelta(
            minutes=self.duration)).time()

    @property
    def get_sns_display(self):
        if self.sns_url:
            sns_parts = self.sns_url.split('/')
            return f"@{sns_parts[-1]}"
        return None

    @property
    def is_accepted(self):
        return self.status == 'approved'

    def save(self, *args, **kwargs):
        update_fields = kwargs.get('update_fields')
        # update_fieldsが指定されていない、またはposter_imageが含まれている場合のみリサイズ
        if update_fields is None or 'poster_image' in update_fields:
            resize_and_convert_image(self.poster_image, max_size=1000, output_format='JPEG')
        super().save(*args, **kwargs)

    def get_owners(self):
        """主催者ユーザーのリストを返す"""
        return [m.user for m in self.members.filter(role=CommunityMember.Role.OWNER)]

    def get_staff(self):
        """スタッフユーザーのリストを返す"""
        return [m.user for m in self.members.filter(role=CommunityMember.Role.STAFF)]

    def get_all_managers(self):
        """全管理者ユーザーのリストを返す"""
        return [m.user for m in self.members.all()]

    def is_manager(self, user):
        """指定ユーザーが管理者かどうかを判定する"""
        # メンバーシップチェック
        if self.members.filter(user=user).exists():
            return True
        # 後方互換: custom_userチェック（CommunityMember未作成の集会用）
        return self.custom_user == user

    def is_owner(self, user):
        """指定ユーザーが主催者かどうかを判定する"""
        # メンバーシップチェック
        if self.members.filter(user=user, role=CommunityMember.Role.OWNER).exists():
            return True
        # 後方互換: custom_userチェック（CommunityMember未作成の集会用）
        return self.custom_user == user

    def can_edit(self, user):
        """指定ユーザーが編集可能かどうかを判定する"""
        return self.is_manager(user)

    def can_delete(self, user):
        """指定ユーザーが削除可能かどうかを判定する"""
        return self.is_owner(user)


class CommunityMember(models.Model):
    """集会メンバー（管理者）モデル"""

    class Role(models.TextChoices):
        OWNER = 'owner', '主催者'
        STAFF = 'staff', 'スタッフ'

    community = models.ForeignKey(
        'Community',
        on_delete=models.CASCADE,
        related_name='members',
        verbose_name='集会'
    )
    user = models.ForeignKey(
        'user_account.CustomUser',
        on_delete=models.CASCADE,
        related_name='community_memberships',
        verbose_name='ユーザー'
    )
    role = models.CharField(
        max_length=10,
        choices=Role.choices,
        default=Role.STAFF,
        verbose_name='役割'
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='追加日時')

    class Meta:
        unique_together = ('community', 'user')
        verbose_name = '集会メンバー'
        verbose_name_plural = '集会メンバー'

    def __str__(self):
        return f'{self.user.user_name} - {self.community.name} ({self.get_role_display()})'

    @property
    def is_owner(self):
        """このメンバーが主催者かどうか"""
        return self.role == self.Role.OWNER

    @property
    def can_delete(self):
        """このメンバーが集会を削除できるかどうか"""
        return self.role == self.Role.OWNER

    @property
    def can_edit(self):
        """このメンバーが集会を編集できるかどうか"""
        return self.role in [self.Role.OWNER, self.Role.STAFF]


# 招待リンクの有効期限（日数）
INVITATION_EXPIRATION_DAYS = 7


class CommunityInvitation(models.Model):
    """集会招待リンクモデル"""

    class InvitationType(models.TextChoices):
        STAFF = 'staff', 'スタッフ招待'
        OWNERSHIP_TRANSFER = 'ownership_transfer', '主催者引き継ぎ'

    community = models.ForeignKey(
        'Community',
        on_delete=models.CASCADE,
        related_name='invitations',
        verbose_name='集会'
    )
    created_by = models.ForeignKey(
        'user_account.CustomUser',
        on_delete=models.CASCADE,
        related_name='created_invitations',
        verbose_name='作成者'
    )
    token = models.CharField(
        max_length=64,
        unique=True,
        verbose_name='トークン'
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='作成日時'
    )
    expires_at = models.DateTimeField(
        verbose_name='有効期限'
    )
    invitation_type = models.CharField(
        max_length=20,
        choices=InvitationType.choices,
        default=InvitationType.STAFF,
        verbose_name='招待タイプ'
    )

    class Meta:
        verbose_name = '集会招待リンク'
        verbose_name_plural = '集会招待リンク'

    def __str__(self):
        return f'{self.community.name} - {self.token[:8]}...'

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = secrets.token_urlsafe(48)
        if not self.expires_at:
            self.expires_at = timezone.now() + timedelta(days=INVITATION_EXPIRATION_DAYS)
        super().save(*args, **kwargs)

    @property
    def is_valid(self):
        """招待が有効かどうか"""
        return timezone.now() < self.expires_at

    @classmethod
    def create_invitation(cls, community, user):
        """新しい招待リンクを作成する"""
        return cls.objects.create(
            community=community,
            created_by=user,
            token=secrets.token_urlsafe(48),
            expires_at=timezone.now() + timedelta(days=INVITATION_EXPIRATION_DAYS)
        )
