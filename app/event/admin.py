from django.contrib import admin
from .models import Event, EventDetail, RecurrenceRule


class EventDetailInline(admin.TabularInline):
    model = EventDetail
    extra = 1  # 追加フォームの数


@admin.register(RecurrenceRule)
class RecurrenceRuleAdmin(admin.ModelAdmin):
    list_display = ('frequency', 'interval', 'week_of_month', 'end_date', 'created_at')
    list_filter = ('frequency', 'created_at')
    search_fields = ('custom_rule',)
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('get_community_name', 'date', 'start_time', 'end_time', 'is_recurring_master', 'recurring_master')
    list_filter = ('date', 'is_recurring_master')
    search_fields = ('community__name', 'date')
    inlines = [EventDetailInline]
    raw_id_fields = ('recurring_master', 'recurrence_rule')

    def get_community_name(self, obj):
        return obj.community.name

    get_community_name.short_description = '集会名'


@admin.register(EventDetail)
class EventDetailAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'updated_at', 'detail_type', 'theme', 'speaker')
    list_filter = ('detail_type', 'theme', 'speaker')
    readonly_fields = ('event',)
    
    def formfield_for_dbfield(self, db_field, **kwargs):
        field = super().formfield_for_dbfield(db_field, **kwargs)
        if db_field.name == 'slide_file':
            field.help_text = 'PDFファイルのみアップロード可能です（最大30MB）'
            field.widget.attrs['accept'] = '.pdf'
        return field
