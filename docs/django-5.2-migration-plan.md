# Django 5.2 移行計画

## 概要

| 項目 | 値 |
|------|-----|
| 現行バージョン | Django 4.2.x |
| 目標バージョン | Django 5.2.x |
| リスクレベル | 中 |
| 推定工数 | 4時間 |

## 破壊的変更と対応

### 1. STATICFILES_STORAGE と DEFAULT_FILE_STORAGE の移行

**ファイル**: `app/website/settings.py`
**行番号**: 236-237 (DEBUG=True), 241-242 (DEBUG=False)

Django 4.2 で非推奨、Django 5.1 で削除。`STORAGES` 設定に移行が必要です。

**現状**:
```python
if DEBUG:
    # ローカル開発環境の設定
    MEDIA_URL = '/media/'
    MEDIA_ROOT = os.path.join(BASE_DIR, 'media')
    STATICFILES_STORAGE = 'django.contrib.staticfiles.storage.StaticFilesStorage'
    DEFAULT_FILE_STORAGE = 'django.core.files.storage.FileSystemStorage'
else:
    # 本番環境の設定 (Cloudflare R2)
    MEDIA_URL = f'{AWS_S3_ENDPOINT_URL}/{AWS_STORAGE_BUCKET_NAME}/'
    STATICFILES_STORAGE = 'storages.backends.s3boto3.S3Boto3Storage'
    DEFAULT_FILE_STORAGE = 'storages.backends.s3boto3.S3Boto3Storage'
```

**修正後**:
```python
if DEBUG:
    # ローカル開発環境の設定
    MEDIA_URL = '/media/'
    MEDIA_ROOT = os.path.join(BASE_DIR, 'media')
    STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }
else:
    # 本番環境の設定 (Cloudflare R2)
    MEDIA_URL = f'{AWS_S3_ENDPOINT_URL}/{AWS_STORAGE_BUCKET_NAME}/'
    STORAGES = {
        "default": {
            "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
            "OPTIONS": {
                "access_key": AWS_ACCESS_KEY_ID,
                "secret_key": AWS_SECRET_ACCESS_KEY,
                "bucket_name": AWS_STORAGE_BUCKET_NAME,
                "endpoint_url": AWS_S3_ENDPOINT_URL,
                "custom_domain": AWS_S3_CUSTOM_DOMAIN,
                "file_overwrite": False,
                "querystring_auth": False,
            },
        },
        "staticfiles": {
            "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
            "OPTIONS": {
                "access_key": AWS_ACCESS_KEY_ID,
                "secret_key": AWS_SECRET_ACCESS_KEY,
                "bucket_name": AWS_STORAGE_BUCKET_NAME,
                "endpoint_url": AWS_S3_ENDPOINT_URL,
                "custom_domain": AWS_S3_CUSTOM_DOMAIN,
            },
        },
    }
```

### 2. ログアウト GET リクエストの無効化

Django 5.0 で `LogoutView` は GET リクエストをサポートしなくなりました。POST リクエストでフォームを送信する必要があります。

#### 2.1 settings.html

**ファイル**: `app/user_account/templates/account/settings.html`
**行番号**: 128

**現状**:
```html
<a href="{% url 'account:logout' %}" class="list-group-item list-group-item-action text-danger">
    <i class="fa-solid fa-right-from-bracket me-2"></i>ログアウト
</a>
```

**修正後**:
```html
<form method="post" action="{% url 'account:logout' %}" class="list-group-flush">
    {% csrf_token %}
    <button type="submit" class="list-group-item list-group-item-action text-danger border-0 bg-transparent text-start w-100">
        <i class="fa-solid fa-right-from-bracket me-2"></i>ログアウト
    </button>
</form>
```

#### 2.2 discord_required.html

**ファイル**: `app/user_account/templates/account/discord_required.html`
**行番号**: 28

**現状**:
```html
<a href="{% url 'account:logout' %}" class="text-muted">
    <i class="fas fa-sign-out-alt me-1"></i>ログアウト
</a>
```

**修正後**:
```html
<form method="post" action="{% url 'account:logout' %}" class="d-inline">
    {% csrf_token %}
    <button type="submit" class="btn btn-link text-muted p-0 text-decoration-none">
        <i class="fas fa-sign-out-alt me-1"></i>ログアウト
    </button>
</form>
```

#### 2.3 base.html

**ファイル**: `app/ta_hub/templates/ta_hub/base.html`
**行番号**: 405

**現状**:
```html
<li><a class="dropdown-item" href="{% url 'account:logout' %}">
    <i class="bi bi-box-arrow-right me-2"></i>ログアウト
</a></li>
```

**修正後**:
```html
<li>
    <form method="post" action="{% url 'account:logout' %}">
        {% csrf_token %}
        <button type="submit" class="dropdown-item">
            <i class="bi bi-box-arrow-right me-2"></i>ログアウト
        </button>
    </form>
</li>
```

## 依存パッケージ更新

| パッケージ | 現行 | 目標 | 備考 |
|-----------|------|------|------|
| Django | 4.2.* | 5.2.* | メジャーアップグレード |
| django-storages | 1.11.1 | 1.14.* | STORAGES 対応版 |
| djangorestframework | 3.15.2 | 3.15.* | Django 5.x 対応済み |
| django-allauth | 65.3.1 | 最新 | Django 5.x 対応版 |
| django-bootstrap5 | 24.2 | 24.* | Django 5.x 対応済み |
| mysqlclient | 2.2.4 | 2.2.* | 互換性あり |

## テスト計画

1. ローカル環境で Django 5.2 にアップグレード
2. `python manage.py check` で警告確認
3. `python manage.py migrate` で移行確認
4. 以下の機能を重点的に確認:
   - ファイルアップロード機能（STORAGES 移行確認、Cloudflare R2）
   - Discord 認証・ログイン/ログアウト機能
   - イベント登録・編集機能
   - API エンドポイント

## 実行手順

1. requirements.txt を更新
   ```
   django==5.2.*
   django-storages==1.14.*
   ```

2. settings.py の修正
   - STATICFILES_STORAGE と DEFAULT_FILE_STORAGE を STORAGES に移行

3. テンプレートのログアウトリンクを POST フォームに変更
   - `app/user_account/templates/account/settings.html`
   - `app/user_account/templates/account/discord_required.html`
   - `app/ta_hub/templates/ta_hub/base.html`

4. テスト実行
   ```bash
   python manage.py check
   python manage.py test
   ```

5. 動作確認後、本番環境にデプロイ

## 注意事項

- Python 3.10 以上が必要（Django 5.x の要件）
- Cloudflare R2 ストレージの設定が STORAGES 形式に変わるため、環境変数の確認を忘れずに
- allauth の Discord 連携が Django 5.x で正しく動作するか事前に確認すること
