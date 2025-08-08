from django.contrib import admin

from .models import Category, Post


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "order")
    list_editable = ("order",)
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "is_published", "published_at", "created_at")
    list_filter = ("category", "is_published")
    search_fields = ("title", "body_markdown")
    date_hierarchy = "published_at"
    prepopulated_fields = {"slug": ("title",)}
