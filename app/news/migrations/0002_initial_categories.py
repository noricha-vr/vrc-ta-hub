from django.db import migrations


def create_initial_categories(apps, schema_editor):
    Category = apps.get_model('news', 'Category')
    data = [
        {"name": "お知らせ", "slug": "oshirase", "order": 0},
        {"name": "アップデート", "slug": "update", "order": 1},
    ]
    for item in data:
        Category.objects.get_or_create(slug=item["slug"], defaults=item)


def delete_initial_categories(apps, schema_editor):
    Category = apps.get_model('news', 'Category')
    Category.objects.filter(slug__in=["oshirase", "update"]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('news', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(create_initial_categories, delete_initial_categories),
    ]
