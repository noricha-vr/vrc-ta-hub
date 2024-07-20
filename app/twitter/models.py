from django.db import models

from community.models import Community


class TwitterTemplate(models.Model):
    name = models.CharField('テンプレート名', max_length=255, default="")
    community = models.ForeignKey(Community, on_delete=models.CASCADE, related_name='twitter_template')
    template = models.TextField('テンプレート',
                                help_text="利用可能な変数: {event_name}, {date}, {time}, {speaker}, {theme}")

    def __str__(self):
        return f"Twitter Template for {self.community.name}"

    class Meta:
        db_table = 'twitter_template'
