from django.contrib import admin

from .activity_models import CommunityActivityCheck, CommunityActivityState
from .forms import CommunityForm
from .models import Community, CommunityMember, CommunityReport


@admin.register(Community)
class CommunityAdmin(admin.ModelAdmin):
    form = CommunityForm
    list_display = (
        'name',
        'get_weekdays',
        'start_time',
        'frequency',
        'organizers',
        'get_tags',
        'status',
        'get_closed_status',
        'get_report_count',
    )
    list_filter = ('weekdays', 'frequency', 'tags', 'status', 'end_at')
    search_fields = ('name', 'organizers')

    def get_closed_status(self, obj):
        if obj.end_at:
            return f"閉鎖済み ({obj.end_at})"
        return "開催中"

    get_closed_status.short_description = '開催状態'

    def get_weekdays(self, obj):
        return ", ".join(obj.weekdays)

    get_weekdays.short_description = '曜日'

    def get_tags(self, obj):
        if isinstance(obj.tags, str):
            return obj.tags
        return ", ".join(obj.tags)

    get_tags.short_description = 'タグ'

    def get_report_count(self, obj):
        return obj.reports.count()

    get_report_count.short_description = '通報数'


@admin.register(CommunityMember)
class CommunityMemberAdmin(admin.ModelAdmin):
    list_display = ('community', 'user', 'role', 'created_at')
    list_filter = ('role',)
    search_fields = ('community__name', 'user__user_name')


@admin.register(CommunityReport)
class CommunityReportAdmin(admin.ModelAdmin):
    list_display = ('community', 'ip_address', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('community__name',)
    readonly_fields = ('community', 'ip_address', 'created_at')


@admin.register(CommunityActivityState)
class CommunityActivityStateAdmin(admin.ModelAdmin):
    list_display = (
        'community',
        'status',
        'monitoring_enabled',
        'consecutive_inactive_checks',
        'last_checked_at',
        'warning_sent_at',
        'auto_hidden_at',
    )
    list_filter = ('monitoring_enabled', 'status', 'last_checked_at', 'auto_hidden_at')
    search_fields = ('community__name', 'last_reason')
    list_select_related = ('community',)
    readonly_fields = (
        'community',
        'status',
        'consecutive_inactive_checks',
        'inactive_detected_at',
        'warning_sent_at',
        'auto_hidden_at',
        'last_checked_at',
        'check_started_at',
        'last_activity_at',
        'last_signal',
        'last_confidence',
        'last_reason',
        'last_evidence',
        'last_response_id',
        'last_model_name',
        'last_cost_in_usd_ticks',
        'created_at',
        'updated_at',
    )

    def has_add_permission(self, request):
        return False


@admin.register(CommunityActivityCheck)
class CommunityActivityCheckAdmin(admin.ModelAdmin):
    list_display = (
        'community',
        'result',
        'signal',
        'confidence',
        'last_activity_at',
        'action',
        'cost_in_usd_ticks',
        'created_at',
    )
    list_filter = ('result', 'action', 'created_at')
    search_fields = ('community__name', 'reason', 'response_id')
    list_select_related = ('community',)
    readonly_fields = (
        'community',
        'result',
        'signal',
        'confidence',
        'last_activity_at',
        'reason',
        'evidence',
        'response_id',
        'model_name',
        'cost_in_usd_ticks',
        'action',
        'created_at',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
