from django.contrib import admin
from .models import Event


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('get_community_name', 'date', 'theme', 'speakers')
    list_filter = ('date',)
    search_fields = ('theme', 'speakers', 'overview')

    def get_community_name(self, obj):
        return obj.community.name

    get_community_name.short_description = 'コミュニティ名'
