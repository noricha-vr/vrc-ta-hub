from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
import secrets
import string


class CustomUserManager(BaseUserManager):
    def create_user(self, user_name, email=None, password=None, **extra_fields):
        if not user_name:
            raise ValueError('集会名は必須項目です。')
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
        '集会名',
        max_length=150,
        unique=True,
        help_text='必須。150文字以下。文字、数字、@/./+/-/_のみ使用可能です。',
        error_messages={
            'unique': "その集会名はすでに使用されています。",
        },
    )
    email = models.EmailField(
        'メールアドレス',
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
    discord_id = models.CharField('Discord ID', max_length=50, blank=True, null=True)

    class Meta:
        verbose_name = 'ユーザー'
        verbose_name_plural = 'ユーザー'

    def __str__(self):
        return self.user_name

    def clean(self):
        super().clean()
        self.email = self.__class__.objects.normalize_email(self.email)


class APIKey(models.Model):
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='api_keys')
    key = models.CharField('APIキー', max_length=64, unique=True)
    name = models.CharField('キー名', max_length=100, blank=True)
    created_at = models.DateTimeField('作成日時', auto_now_add=True)
    last_used = models.DateTimeField('最終使用日時', blank=True, null=True)
    is_active = models.BooleanField('有効', default=True)
    
    class Meta:
        verbose_name = 'APIキー'
        verbose_name_plural = 'APIキー'
        
    def __str__(self):
        return f"{self.user.user_name} - {self.name or 'API Key'}"
    
    @classmethod
    def generate_key(cls):
        """ランダムなAPIキーを生成"""
        alphabet = string.ascii_letters + string.digits
        return ''.join(secrets.choice(alphabet) for _ in range(64))
    
    def save(self, *args, **kwargs):
        if not self.key:
            self.key = self.generate_key()
        super().save(*args, **kwargs)
