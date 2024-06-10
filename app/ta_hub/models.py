import os
from django.db import models
from django.core.files.base import ContentFile
from PIL import Image
from io import BytesIO


class ImageFile(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    image = models.ImageField(upload_to='images/')
    max_size = models.IntegerField(default=720)

    class Meta:
        verbose_name = '画像ファイル'
        verbose_name_plural = '画像ファイル'

    def save(self, *args, **kwargs):
        if self.image:
            img = Image.open(self.image.file)

            # 画像のサイズを取得
            width, height = img.size

            # 最大サイズを720pxに設定
            max_size = self.max_size

            # 縦横比を維持しながらリサイズ
            if width > max_size or height > max_size:
                if width > height:
                    new_width = max_size
                    new_height = int(height * (max_size / width))
                else:
                    new_width = int(width * (max_size / height))
                    new_height = max_size

                img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

                # リサイズした画像で置き換え
                buffer = BytesIO()
                img.save(buffer, format='PNG', optimize=True, quality=85)
                buffer.seek(0)

                # ファイル名の拡張子を.pngに変更
                file_name, _ = os.path.splitext(self.image.name)
                file_name = f"{file_name}-{self.max_size}.png"

                self.image.save(file_name, ContentFile(buffer.read()), save=False)

        super().save(*args, **kwargs)
