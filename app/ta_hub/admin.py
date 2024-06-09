from django.contrib import admin
from .models import ImageFile


@admin.register(ImageFile)
class ImageFileAdmin(admin.ModelAdmin):
    list_display = ('id', 'image', 'created_at')
