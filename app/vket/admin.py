from django.contrib import admin

from .models import (
    VketCollaboration,
    VketNotice,
    VketNoticeReceipt,
    VketParticipation,
    VketPresentation,
)


@admin.register(VketCollaboration)
class VketCollaborationAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "phase",
        "period_start",
        "period_end",
        "registration_deadline",
        "lt_deadline",
        "updated_at",
    )
    list_filter = ("phase",)
    search_fields = ("name",)
    prepopulated_fields = {"slug": ("name",)}


class VketPresentationInline(admin.TabularInline):
    model = VketPresentation
    extra = 0
    fields = ("order", "speaker", "theme", "duration", "status", "confirmed_start_time")


@admin.register(VketParticipation)
class VketParticipationAdmin(admin.ModelAdmin):
    list_display = (
        "collaboration",
        "community",
        "lifecycle",
        "progress",
        "requested_date",
        "confirmed_date",
        "updated_at",
    )
    list_filter = ("collaboration", "lifecycle", "progress")
    search_fields = ("community__name", "collaboration__name")
    autocomplete_fields = ("community",)
    inlines = [VketPresentationInline]
    readonly_fields = ("applied_at", "schedule_confirmed_at", "lt_submitted_at", "last_acknowledged_at")


@admin.register(VketPresentation)
class VketPresentationAdmin(admin.ModelAdmin):
    list_display = ("participation", "order", "speaker", "theme", "status")
    list_filter = ("status",)
    search_fields = ("speaker", "theme", "participation__community__name")


class VketNoticeReceiptInline(admin.TabularInline):
    model = VketNoticeReceipt
    extra = 0
    readonly_fields = ("ack_token", "acknowledged_at")
    fields = ("participation", "acknowledged_at", "ack_token")


@admin.register(VketNotice)
class VketNoticeAdmin(admin.ModelAdmin):
    list_display = ("title", "collaboration", "target_scope", "requires_ack", "sent_at", "created_at")
    list_filter = ("collaboration", "target_scope", "requires_ack")
    search_fields = ("title", "body")
    inlines = [VketNoticeReceiptInline]


@admin.register(VketNoticeReceipt)
class VketNoticeReceiptAdmin(admin.ModelAdmin):
    list_display = (
        "notice",
        "participation",
        "acknowledged_at",
    )
    readonly_fields = ("ack_token",)
