from django.contrib import admin
from .models import Community, Event


class EventInline(admin.TabularInline):
    model = Event
    extra = 1


@admin.register(Community)
class CommunityAdmin(admin.ModelAdmin):
    list_display = ('name', 'weekday', 'start_time', 'frequency', 'organizers')
    list_filter = ('weekday', 'frequency')
    search_fields = ('name', 'organizers', 'description')
    inlines = [EventInline]


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('get_community_name', 'date', 'theme', 'speakers')
    list_filter = ('date',)
    search_fields = ('theme', 'speakers', 'overview')

    def get_community_name(self, obj):
        return obj.community.name

    get_community_name.short_description = 'コミュニティ名'
