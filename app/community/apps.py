from django.apps import AppConfig


class CommunityConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'community'

    def ready(self):
        # models.pyを肥大化させず、同一communityアプリの監視モデルを登録する。
        from . import activity_models  # noqa: F401
