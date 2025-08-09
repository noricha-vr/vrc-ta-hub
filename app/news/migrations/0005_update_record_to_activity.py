from django.db import migrations


def update_record_to_activity(apps, schema_editor):
    Category = apps.get_model('news', 'Category')
    try:
        record_category = Category.objects.get(slug='record')
        record_category.name = '活動'
        record_category.slug = 'activity'
        record_category.save()
    except Category.DoesNotExist:
        pass


def revert_activity_to_record(apps, schema_editor):
    Category = apps.get_model('news', 'Category')
    try:
        activity_category = Category.objects.get(slug='activity')
        activity_category.name = '記録'
        activity_category.slug = 'record'
        activity_category.save()
    except Category.DoesNotExist:
        pass


class Migration(migrations.Migration):

    dependencies = [
        ('news', '0004_post_meta_description'),
    ]

    operations = [
        migrations.RunPython(update_record_to_activity, revert_activity_to_record),
    ]