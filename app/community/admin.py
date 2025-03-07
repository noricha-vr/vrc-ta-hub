from django.contrib import admin
from .models import Community
from .forms import CommunityForm


@admin.register(Community)
class CommunityAdmin(admin.ModelAdmin):
    form = CommunityForm
    list_display = ('name', 'get_weekdays', 'start_time', 'frequency', 'organizers', 'get_tags', 'status')
    list_filter = ('weekdays', 'frequency', 'tags', 'status')  # statusをフィルタに追加
    search_fields = ('name', 'organizers')

    def get_weekdays(self, obj):
        return ", ".join(obj.weekdays)

    get_weekdays.short_description = '曜日'

    def get_tags(self, obj):
        if type(obj.tags) == str:
            return obj.tags
        return ", ".join(obj.tags)

    get_tags.short_description = 'タグ'

#
# class Community(models.Model):
#     name = models.CharField('イベント名', max_length=100, db_index=True)
#     start_from = models.DateField('イベント開始日', default=None, blank=True, null=True)
#     end_at = models.DateField('イベント終了日', default=None, blank=True, null=True)
#     start_time = models.TimeField('開始時刻', default='22:00')
#     duration = models.IntegerField('開催時間', default=60, help_text='単位は分')
#     weekday = models.CharField('曜日', max_length=5, choices=WEEKDAY_CHOICES)
#     frequency = models.CharField('開催周期', max_length=100)
#     organizers = models.CharField('主催・副主催', max_length=200)
#     group_url = models.URLField('VRChatグループURL', blank=True)
#     organizer_url = models.URLField('VRChat主催プロフィールURL', blank=True)
#     discord = models.URLField('Discord', blank=True)
#     twitter_hashtag = models.CharField('Twitterハッシュタグ', max_length=100, blank=True)
#     poster_image = models.ImageField('ポスター', upload_to='poster/', blank=True)
#     description = models.TextField('イベント紹介')
#     platform = models.CharField('対応プラットフォーム', max_length=10, choices=PLATFORM_CHOICES, default='All')
#     tags = models.JSONField('タグ', max_length=10, choices=TAGS, default=list)
