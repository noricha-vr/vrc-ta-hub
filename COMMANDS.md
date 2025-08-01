# コピペ用コマンド集

### イメージ作成

```shell script
docker compose build
```

### データベーステーブルを更新

```shell script
docker exec -it vrc-ta-hub bash -c "python manage.py makemigrations && python manage.py migrate"
```

### カレンダー更新用ページにリクエストを送る

```shell script
 curl -X GET -H "Request-Token: YOUR_REQUEST_TOKEN" https://vrc-ta-hub.com/event/sync/
```
