# Django パターン

## save() オーバーライド時の注意点

### update_fields を尊重する

`save(update_fields=[...])` を使っても、オーバーライドした `save()` メソッド内の処理は常に実行される。

```python
# ❌ 問題のあるコード
def save(self, *args, **kwargs):
    resize_image(self.image)  # 毎回実行される
    super().save(*args, **kwargs)

# ✅ 正しいコード
def save(self, *args, **kwargs):
    update_fields = kwargs.get('update_fields')
    if update_fields is None or 'image' in update_fields:
        resize_image(self.image)
    super().save(*args, **kwargs)
```

### ImageField の save() でパスが二重になる問題

`upload_to='poster/'` と設定したフィールドで、`image_field.save(filename, ...)` を呼ぶとき、
filename にディレクトリパスが含まれていると二重になる。

```python
# ❌ 問題のあるコード
file_name = image_field.name  # 'poster/image.jpg'
image_field.save(file_name, content)  # 結果: 'poster/poster/image.jpg'

# ✅ 正しいコード
file_name = os.path.basename(image_field.name)  # 'image.jpg'
image_field.save(file_name, content)  # 結果: 'poster/image.jpg'
```

---

*初出: [2026-01-28](../log/2026-01.md#django-save-の-update_fields-と画像処理の落とし穴)*
