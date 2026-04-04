from django.contrib import admin

from .models import TweetQueue, TwitterTemplate


@admin.register(TwitterTemplate)
class TwitterTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "community")
    list_filter = ("community",)


@admin.register(TweetQueue)
class TweetQueueAdmin(admin.ModelAdmin):
    list_display = ("tweet_type", "community", "status", "created_at", "posted_at")
    list_filter = ("tweet_type", "status", "created_at")
    search_fields = ("community__name", "generated_text")
    readonly_fields = (
        "tweet_type",
        "community",
        "event",
        "event_detail",
        "tweet_id",
        "created_at",
        "posted_at",
        "error_message",
    )
    ordering = ("-created_at",)
