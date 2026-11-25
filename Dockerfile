# ベースイメージの指定
FROM python:3.12-slim

# 環境変数の設定
ENV PYTHONUNBUFFERED 1
ENV LC_CTYPE='C.UTF-8'
ENV TZ=Asia/Tokyo
# Install dependencies
RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y \
    locales \
    nginx \
    supervisor \
    build-essential \
    libmariadb-dev-compat \
    libmariadb-dev \
    libsqlite3-dev \
    pkg-config



# uWSGIのインストール
RUN pip3 install uwsgi

# Nginxの設定
RUN echo "daemon off;" >> /etc/nginx/nginx.conf
COPY nginx-app.conf /etc/nginx/sites-available/default

# Supervisorの設定
COPY supervisor-app.conf /etc/supervisor/conf.d/

# アプリケーションディレクトリの作成と設定
WORKDIR /app
COPY /requirements.txt /app/requirements.txt

# Python依存関係のインストール
# requirements.txt は requirements.in（人が編集する直接依存）から uv pip compile で生成した、
# 全 transitive deps をピンしたロックファイル（Dependabot は両方を書き換える）。
# 本番ビルドの再現性確保とサプライチェーン攻撃防止のため lock を使ってインストールする。
# Cloud Run でのメモリ消費を避けるため uv ではなく pip を使う (CLAUDE.md 規約)。
RUN pip3 install --no-deps -r /app/requirements.txt

# アプリケーションのコピー
COPY ./app /app
COPY ./docs /docs
COPY uwsgi.ini /uwsgi.ini
COPY uwsgi_params /uwsgi_params
COPY scripts/docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# PYTHON_PATH
ENV PYTHON_PATH=/app

# JS / CSS 等の静的ファイルをコンテナ内 /app/static へ収集する（起動時 R2 同期の正本）
RUN SECRET_KEY=build-only \
    DEBUG=True \
    TESTING=1 \
    GOOGLE_API_KEY=build-only \
    GOOGLE_CALENDAR_ID=build-only@group.calendar.google.com \
    GEMINI_API_KEY=build-only \
    REQUEST_TOKEN=build-only \
    python3 manage.py collectstatic --noinput

# ポートの公開
EXPOSE 8080

# 起動時 R2 同期 + Supervisor の実行
CMD ["/docker-entrypoint.sh"]
