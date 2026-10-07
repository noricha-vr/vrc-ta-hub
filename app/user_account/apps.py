from django.apps import AppConfig


class UserAccountConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'user_account'
    label = 'user_account'
    verbose_name = 'アカウント管理'

    def ready(self):
        """アプリケーション起動時の処理."""
        # シグナルハンドラーを登録するためにadaptersをインポート
        from user_account import adapters  # noqa: F401
        # EmailAddress の保存・削除で、メールアドレスの持ち主の記録を合わせるシグナルを登録する
        from user_account import email_ownership  # noqa: F401
