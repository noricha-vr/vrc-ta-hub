from django.contrib import admin

from .models import VketCollaboration, VketParticipation


@admin.register(VketCollaboration)
class VketCollaborationAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'status',
        'period_start',
        'period_end',
        'registration_deadline',
        'lt_deadline',
        'updated_at',
    )
    list_filter = ('status',)
    search_fields = ('name',)


@admin.register(VketParticipation)
class VketParticipationAdmin(admin.ModelAdmin):
    list_display = ('collaboration', 'community', 'event', 'updated_at')
    list_filter = ('collaboration',)
    search_fields = ('community__name', 'collaboration__name')
    autocomplete_fields = ('community', 'event')

