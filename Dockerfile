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
RUN pip3 install -r /app/requirements.txt

# アプリケーションのコピー
COPY ./app /app
COPY uwsgi.ini /uwsgi.ini
COPY uwsgi_params /uwsgi_params

# PYTHON_PATH
ENV PYTHON_PATH=/app

# ポートの公開
EXPOSE 8080

# Supervisorの実行
CMD ["supervisord", "-c", "/etc/supervisor/supervisord.conf", "-n"]
