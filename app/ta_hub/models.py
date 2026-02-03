from django.db import models

from ta_hub.libs import resize_and_convert_image


class ImageFile(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    image = models.ImageField(upload_to='images/')
    max_size = models.IntegerField(default=720)

    class Meta:
        verbose_name = '画像ファイル'
        verbose_name_plural = '画像ファイル'

    def save(self, *args, **kwargs):
        # 新しいファイルがアップロードされた場合のみリサイズ
        # _committed が False = 新しいファイルがまだストレージに保存されていない
        if self.image and not getattr(self.image, '_committed', True):
            resize_and_convert_image(self.image, max_size=self.max_size)
        super().save(*args, **kwargs)
