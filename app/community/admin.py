from django.contrib import admin
from .models import Community, CommunityMember
from .forms import CommunityForm


@admin.register(Community)
class CommunityAdmin(admin.ModelAdmin):
    form = CommunityForm
    list_display = ('name', 'get_weekdays', 'start_time', 'frequency', 'organizers', 'get_tags', 'status', 'get_closed_status')
    list_filter = ('weekdays', 'frequency', 'tags', 'status', 'end_at')  # end_atをフィルタに追加
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
        if type(obj.tags) == str:
            return obj.tags
        return ", ".join(obj.tags)

    get_tags.short_description = 'タグ'

@admin.register(CommunityMember)
class CommunityMemberAdmin(admin.ModelAdmin):
    list_display = ('community', 'user', 'role', 'created_at')
    list_filter = ('role',)
    search_fields = ('community__name', 'user__user_name')
