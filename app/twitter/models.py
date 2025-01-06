from django.db import models

from community.models import Community


class TwitterTemplate(models.Model):
    name = models.CharField('テンプレート名', max_length=255, default="")
    community = models.ForeignKey(Community, on_delete=models.CASCADE, related_name='twitter_template')
    template = models.TextField('テンプレート',
                                help_text="利用可能な変数: {event_name}, {date}, {time}, {speaker}, {theme}")

    def __str__(self):
        return f"Twitter Template for {self.community.name}"

    def generate_tweet_text(self, event):
        """イベント情報をテンプレートに適用してツイートテキストを生成する"""
        try:
            event_info = {
                "event_name": event.community.name,
                "date": event.date.strftime("%Y年%m月%d日"),
                "time": event.start_time.strftime("%H:%M"),
                "speaker": event.details.first().speaker if event.details.exists() else "",
                "theme": event.details.first().theme if event.details.exists() else "",
            }
            return self.template.format(**event_info)
        except Exception as e:
            return None

    class Meta:
        db_table = 'twitter_template'
