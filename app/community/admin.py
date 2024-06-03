from django.contrib import admin
from .models import Community


@admin.register(Community)
class CommunityAdmin(admin.ModelAdmin):
    list_display = ('name', 'weekday', 'start_time', 'frequency', 'organizers')
    list_filter = ('weekday', 'frequency')
    search_fields = ('name', 'organizers', 'description')
