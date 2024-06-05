from django.contrib import admin
from .models import Event, EventDetail


class EventDetailInline(admin.TabularInline):
    model = EventDetail
    extra = 1  # 追加フォームの数


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('get_community_name', 'date', 'start_time', 'end_time')
    list_filter = ('date',)
    inlines = [EventDetailInline]

    def get_community_name(self, obj):
        return obj.community.name

    get_community_name.short_description = 'コミュニティ名'
