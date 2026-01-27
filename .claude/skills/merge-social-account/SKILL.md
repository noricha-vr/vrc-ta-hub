---
name: "merge-social-account"
description: "Discord認証で重複したアカウントのSocialAccountを既存ユーザーに移動し、不要ユーザーを削除する"
---

# SocialAccount マージスキル

<trigger_conditions>

## 使用するタイミング

- Discord認証でアカウントが重複した場合
- 「アカウントをマージしたい」「重複ユーザーを整理」などの依頼
- SocialAccountを別ユーザーに移動したい場合

</trigger_conditions>

<instructions>

## 1. 本番DBコンテナを起動

```bash
docker compose -f docker-compose-production.yaml up -d
```

## 2. ユーザーに必要情報を質問

AskUserQuestion で以下を確認:

- **移動元ユーザー**（新しく作成された不要なユーザー）のID または メールアドレス
- **移動先ユーザー**（SocialAccountを紐付けたい既存ユーザー）のID または ユーザー名

## 3. 対象データを確認・表示

```bash
docker compose -f docker-compose-production.yaml exec vrc-ta-hub python manage.py shell -c "
from django.contrib.auth import get_user_model
from allauth.socialaccount.models import SocialAccount

User = get_user_model()

# 移動元ユーザー（IDまたはメールで検索）
source_user = User.objects.get(id=<SOURCE_ID>)  # または email='...'
print('=== 移動元ユーザー ===')
print(f'ID: {source_user.id}')
print(f'Username: {source_user.username}')
print(f'Email: {source_user.email}')
print(f'Date Joined: {source_user.date_joined}')

# 移動先ユーザー
target_user = User.objects.get(id=<TARGET_ID>)  # または username='...'
print('\n=== 移動先ユーザー ===')
print(f'ID: {target_user.id}')
print(f'Username: {target_user.username}')
print(f'Email: {target_user.email}')
print(f'Date Joined: {target_user.date_joined}')

# SocialAccount情報
print('\n=== SocialAccount ===')
for sa in SocialAccount.objects.filter(user=source_user):
    print(f'Provider: {sa.provider}, UID: {sa.uid}, User: {sa.user_id}')
"
```

## 4. ユーザーに確認（必須）

以下の内容を表示して確認を取る:

```
以下の操作を実行してよいですか？

1. SocialAccount（Discord）を ID:XX から ID:YY に移動
2. 移動元ユーザー（ID:XX, username: ...）を削除

[はい] [いいえ]
```

**確認なしに実行しないこと**

## 5. マージ実行

```bash
docker compose -f docker-compose-production.yaml exec vrc-ta-hub python manage.py shell -c "
from django.contrib.auth import get_user_model
from allauth.socialaccount.models import SocialAccount

User = get_user_model()

source_user = User.objects.get(id=<SOURCE_ID>)
target_user = User.objects.get(id=<TARGET_ID>)

# SocialAccountの移動
updated = SocialAccount.objects.filter(user=source_user).update(user=target_user)
print(f'SocialAccount {updated}件を移動しました')

# 移動元ユーザーの削除
username = source_user.username
source_user.delete()
print(f'ユーザー {username} を削除しました')
"
```

## 6. 結果を確認

```bash
docker compose -f docker-compose-production.yaml exec vrc-ta-hub python manage.py shell -c "
from django.contrib.auth import get_user_model
from allauth.socialaccount.models import SocialAccount

User = get_user_model()
target_user = User.objects.get(id=<TARGET_ID>)

print('=== 移動先ユーザーの現在の状態 ===')
print(f'ID: {target_user.id}')
print(f'Username: {target_user.username}')
print(f'Email: {target_user.email}')

print('\n=== 紐付いたSocialAccount ===')
for sa in SocialAccount.objects.filter(user=target_user):
    print(f'Provider: {sa.provider}, UID: {sa.uid}')
"
```

## 7. 結果を報告

- 移動したSocialAccountの数
- 削除したユーザー情報
- 移動先ユーザーの現在の状態

</instructions>

## トラブルシューティング

### 移動先ユーザーに既に同じProviderのSocialAccountがある場合

```bash
# 既存のSocialAccountを確認
docker compose -f docker-compose-production.yaml exec vrc-ta-hub python manage.py shell -c "
from allauth.socialaccount.models import SocialAccount
for sa in SocialAccount.objects.filter(user_id=<TARGET_ID>):
    print(f'{sa.provider}: {sa.uid}')
"
```

この場合は移動元のSocialAccountを削除してから移動元ユーザーを削除する。

### ユーザーに関連データがある場合

移動元ユーザーに関連データ（投稿、コメント等）がある場合は、先に確認:

```bash
docker compose -f docker-compose-production.yaml exec vrc-ta-hub python manage.py shell -c "
from django.contrib.auth import get_user_model
User = get_user_model()
user = User.objects.get(id=<SOURCE_ID>)

# 関連オブジェクトを確認
for rel in user._meta.related_objects:
    related_name = rel.get_accessor_name()
    count = getattr(user, related_name).count() if hasattr(getattr(user, related_name), 'count') else 'N/A'
    print(f'{related_name}: {count}')
"
```

## チェックリスト

- [ ] 本番DBコンテナが起動している
- [ ] 移動元・移動先ユーザーを正しく特定した
- [ ] ユーザーに確認を取った（必須）
- [ ] マージ実行後に結果を確認した
- [ ] 結果をユーザーに報告した
