from django.contrib import admin
from .models import Event, EventDetail


class EventDetailInline(admin.TabularInline):
    model = EventDetail
    extra = 1  # 追加フォームの数


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('get_community_name', 'date', 'start_time', 'end_time')
    list_filter = ('date',)
    search_fields = ('community__name', 'date')
    inlines = [EventDetailInline]

    def get_community_name(self, obj):
        return obj.community.name

    get_community_name.short_description = '集会名'


@admin.register(EventDetail)
class EventDetailAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'updated_at', 'theme', 'speaker')
    list_filter = ('theme', 'speaker')
    readonly_fields = ('event',)
