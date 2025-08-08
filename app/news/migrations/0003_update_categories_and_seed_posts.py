from django.db import migrations
from django.utils import timezone


def update_categories_and_seed_posts(apps, schema_editor):
    Category = apps.get_model('news', 'Category')
    Post = apps.get_model('news', 'Post')

    # Ensure categories: イベント, 記録, アップデート
    desired = [
        {"name": "イベント", "slug": "event", "order": 0},
        {"name": "記録", "slug": "record", "order": 1},
        {"name": "アップデート", "slug": "update", "order": 2},
    ]

    # Remove old "お知らせ" if exists
    Category.objects.filter(slug="oshirase").delete()

    slug_to_cat = {}
    for item in desired:
        cat, _ = Category.objects.get_or_create(slug=item["slug"], defaults=item)
        # sync name/order in case they changed
        changed = False
        if cat.name != item["name"]:
            cat.name = item["name"]
            changed = True
        if cat.order != item["order"]:
            cat.order = item["order"]
            changed = True
        if changed:
            cat.save(update_fields=["name", "order"]) 
        slug_to_cat[item["slug"]] = cat

    # Seed posts (category: アップデート)
    update_cat = slug_to_cat["update"]

    now = timezone.now()

    # 1) Googleカレンダー同期バグ修正
    Post.objects.get_or_create(
        slug="fix-google-calendar-sync-bug",
        defaults={
            "title": "Googleカレンダー同期の不具合を修正しました",
            "body_markdown": (
                "最近の更新で、Googleカレンダー連携における重複/不整合の不具合を修正しました。\n\n"
                "- 連携時の重複生成を防止\n"
                "- 同期処理の安定化と整理\n\n"
                "今後も日程の正確な反映に向けて改善を継続します。"
            ),
            "category": update_cat,
            "is_published": True,
            "published_at": now,
        },
    )

    # 2) 日付切り替えを午前4時に変更
    Post.objects.get_or_create(
        slug="change-day-cutoff-to-4am",
        defaults={
            "title": "開催日程とトップページの切替時刻を午前4時に変更",
            "body_markdown": (
                "VRChatユーザーの活動時間に合わせ、開催日程やトップページのLT/イベント情報の切替時刻を\n"
                "午前4時に変更しました。これにより深夜帯の表示が実態に即した形になります。"
            ),
            "category": update_cat,
            "is_published": True,
            "published_at": now,
        },
    )


def reverse_func(apps, schema_editor):
    # Keep categories; remove seeded posts only
    Post = apps.get_model('news', 'Post')
    Post.objects.filter(slug__in=["fix-google-calendar-sync-bug", "change-day-cutoff-to-4am"]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('news', '0002_initial_categories'),
    ]

    operations = [
        migrations.RunPython(update_categories_and_seed_posts, reverse_func),
    ]
