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
        resize_and_convert_image(self.image, self.max_size, 'JPEG')
        super().save(*args, **kwargs)
