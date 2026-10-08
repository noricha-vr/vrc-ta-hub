from django.apps import AppConfig


class DiscordSchedulerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "discord_scheduler"
    verbose_name = "Discord予約投稿"
