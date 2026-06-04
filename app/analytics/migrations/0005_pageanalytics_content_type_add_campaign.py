# utm_campaign 経由の community 解決に必要な content_type を追加する。
# DB スキーマ変更なし（choices 追加と help_text 更新のみ）。

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('analytics', '0004_alter_campaign_utm_medium_alter_campaign_utm_source'),
    ]

    operations = [
        migrations.AlterField(
            model_name='pageanalytics',
            name='content_type',
            field=models.CharField(
                choices=[
                    ('community', '集会ページ'),
                    ('event_detail', 'イベント詳細ページ'),
                    ('global', 'サイト全体（紐付けなし）'),
                    ('campaign', 'キャンペーン経由（pagePath非依存）'),
                ],
                max_length=20,
                verbose_name='コンテンツ種別',
            ),
        ),
        migrations.AlterField(
            model_name='campaign',
            name='landing_path',
            field=models.CharField(
                default='/',
                help_text=(
                    '例: / または /community/123/。'
                    '/ などサイト全体トップに着地させた場合は utm_campaign 経由で集会に紐付ける'
                ),
                max_length=255,
                verbose_name='着地パス',
            ),
        ),
    ]
