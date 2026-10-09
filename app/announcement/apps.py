from django.apps import AppConfig


class AnnouncementConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'announcement'
    verbose_name = 'Discord 告知の予約送信'
