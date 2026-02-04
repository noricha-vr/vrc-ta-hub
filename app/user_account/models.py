from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
import hashlib
import secrets
import string


class CustomUserManager(BaseUserManager):
    def create_user(self, user_name, email=None, password=None, **extra_fields):
        if not user_name:
            raise ValueError('ユーザー名は必須項目です。')
        email = self.normalize_email(email)
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

    def clean(self):
        super().clean()
        self.email = self.__class__.objects.normalize_email(self.email)


class APIKey(models.Model):
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='api_keys')
    # NOTE: このフィールドには平文キーではなく、平文キーのSHA-256ハッシュ（hex）を保存する
    key = models.CharField('APIキー', max_length=64, unique=True)
    name = models.CharField('キー名', max_length=100, blank=True)
    created_at = models.DateTimeField('作成日時', auto_now_add=True)
    last_used = models.DateTimeField('最終使用日時', blank=True, null=True)
    is_active = models.BooleanField('有効', default=True)
    
    class Meta:
        verbose_name = 'APIキー'
        verbose_name_plural = 'APIキー'
        db_table = 'account_apikey'

    def __str__(self):
        return f"{self.user.user_name} - {self.name or 'API Key'}"
    
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
