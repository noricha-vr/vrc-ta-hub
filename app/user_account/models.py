from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
import hashlib
import ipaddress
import secrets
import string


class CustomUserManager(BaseUserManager):
    def create_user(self, user_name, email=None, password=None, **extra_fields):
        if not user_name:
            raise ValueError('ユーザー名は必須項目です。')
        email = self.normalize_email(email)
        extra_fields.setdefault('display_name', user_name)
        user = self.model(user_name=user_name, email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, user_name, email=None, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)

        if extra_fields.get('is_staff') is not True:
            raise ValueError('スーパーユーザーはstaffである必要があります。')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('スーパーユーザーはsuperuserである必要があります。')

        return self.create_user(user_name, email, password, **extra_fields)


class CustomUser(AbstractBaseUser, PermissionsMixin):
    user_name = models.CharField(
        'ユーザー名',
        max_length=150,
        unique=True,
        help_text='必須。150文字以下。文字、数字、@/./+/-/_のみ使用可能です。',
        error_messages={
            'unique': "そのユーザー名はすでに使用されています。",
        },
    )
    display_name = models.CharField(
        '表示名',
        max_length=200,
        blank=True,
        default='',
        help_text='VRChat等で使う表示名。同じ表示名を複数ユーザーが使用できます。',
    )
    email = models.EmailField(
        'メールアドレス',
        unique=True,
        blank=False,
        null=False,
        help_text='必須。有効なメールアドレスを入力してください。',
    )
    is_staff = models.BooleanField(
        'スタッフ権限',
        default=False,
        help_text='このユーザーが管理サイトにログインできるかどうかを指定します。',
    )
    is_active = models.BooleanField(
        '有効',
        default=True,
        help_text='このユーザーを有効として扱うかどうかを指定します。'
                  '無効にする代わりに、これを選択解除してください。',
    )
    groups = models.ManyToManyField(
        'auth.Group',
        verbose_name=_('groups'),
        blank=True,
        help_text=_(
            'The groups this custom_user belongs to. A custom_user will get all permissions '
            'granted to each of their groups.'
        ),
        related_name='customuser_set',  # related_name を追加
    )
    user_permissions = models.ManyToManyField(
        'auth.Permission',
        verbose_name=_('custom_user permissions'),
        blank=True,
        help_text=_('Specific permissions for this custom_user.'),
        related_name='customuser_set',  # related_name を追加
    )

    date_joined = models.DateTimeField('登録日', default=timezone.now)
    icon = models.ImageField('アイコン', upload_to='icon/', blank=True, null=True)
    x_account = models.CharField(
        'X (Twitter) アカウント',
        max_length=15,
        blank=True,
        default='',
        help_text='@ なしのハンドル名（例: noricha_vr）。英数字とアンダースコア、1〜15文字。',
        validators=[
            RegexValidator(
                r'^[A-Za-z0-9_]{1,15}\Z',
                'X のハンドル名は英数字とアンダースコアで1〜15文字です。',
            ),
        ],
    )
    vrchat_user_id = models.CharField(
        'VRChatユーザーID',
        max_length=40,
        blank=True,
        default='',
        help_text='VRChatのユーザーID（usr_から始まるID）を保存します。',
        validators=[
            RegexValidator(
                r'^usr_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z',
                'VRChatユーザーIDは usr_ から始まるIDです。',
                flags=0,
            ),
        ],
    )

    objects = CustomUserManager()

    EMAIL_FIELD = 'email'
    USERNAME_FIELD = 'user_name'
    REQUIRED_FIELDS = ['email']

    class Meta:
        verbose_name = 'ユーザー'
        verbose_name_plural = 'ユーザー'
        db_table = 'account_customuser'

    def __str__(self):
        return self.user_name

    @property
    def display_label(self):
        """人間向けの表示名を返す。未設定ならログインユーザー名を使う。"""
        return self.display_name or self.user_name

    def clean(self):
        super().clean()
        self.email = self.__class__.objects.normalize_email(self.email)


class APIKey(models.Model):
    SCOPE_READ = "read"
    SCOPE_WRITE = "write"
    SCOPE_CHOICES = [
        (SCOPE_READ, "読み取りのみ"),
        (SCOPE_WRITE, "読み書き"),
    ]

    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='api_keys')
    # NOTE: このフィールドには平文キーではなく、平文キーのSHA-256ハッシュ（hex）を保存する
    key = models.CharField('APIキー', max_length=64, unique=True)
    name = models.CharField('キー名', max_length=100, blank=True)
    created_at = models.DateTimeField('作成日時', auto_now_add=True)
    last_used = models.DateTimeField('最終使用日時', blank=True, null=True)
    is_active = models.BooleanField('有効', default=True)
    # null=True で既存キーを invalidate しない（永続キーとして扱う）
    expires_at = models.DateTimeField('有効期限', blank=True, null=True)
    scope = models.CharField(
        '権限スコープ',
        max_length=16,
        choices=SCOPE_CHOICES,
        default=SCOPE_WRITE,
    )
    # TEXT 型は MySQL で DEFAULT 制約が扱えないケースがあるため CharField(255) を採用。
    # プロキシ配下では REMOTE_ADDR がプロキシ IP になる点に注意（運用者向け）。
    allowed_ips = models.CharField(
        '許可IP',
        max_length=255,
        blank=True,
        default='',
        help_text='カンマ区切りで IP アドレスまたは CIDR を指定。空ならすべての IP を許可。'
                  'プロキシ配下では REMOTE_ADDR がプロキシ IP になるため、運用環境に応じて要検証。',
    )

    class Meta:
        verbose_name = 'APIキー'
        verbose_name_plural = 'APIキー'
        db_table = 'account_apikey'

    def __str__(self):
        return f"{self.user.user_name} - {self.name or 'API Key'}"

    def is_expired(self) -> bool:
        """有効期限切れかを判定。expires_at が None なら無期限。"""
        if self.expires_at is None:
            return False
        return self.expires_at < timezone.now()

    def _parse_allowed_networks(self) -> list:
        """allowed_ips をパースして network オブジェクトのリストを返す。

        想定外に広い allowlist を防ぐため、`192.168.1.10/24` のようにホストビットを
        含む CIDR は ValueError を投げる (strict=True)。単体 IP は ip_address() で
        受けてから /32 (IPv4) または /128 (IPv6) のネットワークに変換する。
        """
        networks = []
        for raw in self.allowed_ips.split(','):
            token = raw.strip()
            if not token:
                continue
            if '/' in token:
                # CIDR 表記。host bit があれば fail-closed（ValueError 経由）
                networks.append(ipaddress.ip_network(token, strict=True))
            else:
                # 単体 IP は /32 or /128 として扱う
                addr = ipaddress.ip_address(token)
                networks.append(ipaddress.ip_network(f'{addr}/{addr.max_prefixlen}', strict=True))
        return networks

    def is_ip_allowed(self, client_ip: str) -> bool:
        """client_ip が allowed_ips の範囲内かを判定。allowed_ips が空なら常に True。"""
        if not self.allowed_ips.strip():
            return True
        if not client_ip:
            # ホワイトリスト設定済みかつクライアントIP不明なら fail-closed
            return False
        try:
            client = ipaddress.ip_address(client_ip)
            networks = self._parse_allowed_networks()
        except ValueError:
            # 不正なIP/CIDRは fail-closed
            return False
        return any(client in network for network in networks)

    def is_valid(self, request=None) -> bool:
        """有効期限・IP ホワイトリストを総合チェックする。

        request が None の場合は IP チェックをスキップ（モデル単体での有効期限確認に利用）。
        """
        if self.is_expired():
            return False
        if request is not None and self.allowed_ips.strip():
            client_ip = request.META.get('REMOTE_ADDR', '') if hasattr(request, 'META') else ''
            if not self.is_ip_allowed(client_ip):
                return False
        return True
    
    @classmethod
    def generate_key(cls):
        """ランダムなAPIキー（平文）を生成"""
        alphabet = string.ascii_letters + string.digits
        return ''.join(secrets.choice(alphabet) for _ in range(64))

    @staticmethod
    def hash_raw_key(raw_key: str) -> str:
        """平文APIキーをSHA-256でハッシュ化して返す（hex, 64文字）。"""
        return hashlib.sha256(raw_key.encode('utf-8')).hexdigest()

    @classmethod
    def create_with_raw_key(cls, *, user: CustomUser, name: str = "") -> tuple["APIKey", str]:
        """平文キーを生成し、ハッシュのみを保存して作成する。

        Returns:
            (api_key, raw_key): raw_key は作成直後にのみ利用し、永続化しないこと。
        """
        raw_key = cls.generate_key()
        api_key = cls.objects.create(
            user=user,
            name=name,
            key=cls.hash_raw_key(raw_key),
        )
        return api_key, raw_key
    
    def save(self, *args, **kwargs):
        if not self.key:
            # 管理画面などでkey未指定のまま作成された場合は、復元不可能な形で保存される。
            raw_key = self.generate_key()
            self.key = self.hash_raw_key(raw_key)
        super().save(*args, **kwargs)
