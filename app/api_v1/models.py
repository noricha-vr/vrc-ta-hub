from django.conf import settings
from django.db import models


class APIRequestLog(models.Model):
    """/api/v1/* のリクエストログ。

    利用者集計のため middleware で毎リクエスト INSERT する。
    件数が多くなるため created_at と user にインデックスを張り、
    定期的な古いレコード削除は運用側の判断に委ねる（当面は保持）。
    """

    AUTH_ANONYMOUS = 'anonymous'
    AUTH_SESSION = 'session'
    AUTH_API_KEY = 'api_key'
    AUTH_METHOD_CHOICES = [
        (AUTH_ANONYMOUS, '未認証'),
        (AUTH_SESSION, 'セッション'),
        (AUTH_API_KEY, 'APIキー'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='api_request_logs',
    )
    api_key = models.ForeignKey(
        'user_account.APIKey',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='request_logs',
    )
    auth_method = models.CharField(
        max_length=16,
        choices=AUTH_METHOD_CHOICES,
        default=AUTH_ANONYMOUS,
    )
    path = models.CharField(max_length=500)
    method = models.CharField(max_length=8)
    status_code = models.PositiveSmallIntegerField()
    remote_ip = models.CharField(max_length=64, blank=True, default='')
    user_agent = models.CharField(max_length=500, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['-created_at']),
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['api_key', '-created_at']),
        ]

    def __str__(self) -> str:
        return f'{self.method} {self.path} → {self.status_code} ({self.created_at:%Y-%m-%d %H:%M:%S})'
