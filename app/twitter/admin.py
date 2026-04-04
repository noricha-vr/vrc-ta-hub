from django.contrib import admin

from .models import TweetQueue, TwitterTemplate


@admin.register(TwitterTemplate)
class TwitterTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "community")
    list_filter = ("community",)


@admin.register(TweetQueue)
class TweetQueueAdmin(admin.ModelAdmin):
    list_display = ("tweet_type", "community", "status", "has_image", "created_at", "posted_at")
    list_filter = ("tweet_type", "status", "created_at")
    search_fields = ("community__name", "generated_text")
    readonly_fields = (
        "tweet_type",
        "community",
        "event",
        "event_detail",
        "tweet_id",
        "image_url",
        "created_at",
        "posted_at",
        "error_message",
    )
    ordering = ("-created_at",)

    @admin.display(boolean=True, description="画像")
    def has_image(self, obj):
        return bool(obj.image_url)
