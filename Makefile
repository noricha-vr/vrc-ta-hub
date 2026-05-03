.PHONY: up down build restart logs shell test migrate makemigrations superuser tunnel collectstatic sync db-backup db-backup-local db-pull db-restore-production-local db-verify-local db-push

# Docker
up:
	docker compose up -d

down:
	docker compose down

build:
	docker compose up -d --build

restart:
	docker compose down && docker compose up -d

logs:
	docker compose logs -f

shell:
	docker compose exec vrc-ta-hub bash

# Django
test:
	docker compose exec vrc-ta-hub python manage.py test

migrate:
	docker compose exec vrc-ta-hub python manage.py migrate

makemigrations:
	docker exec -it vrc-ta-hub bash -c "python manage.py makemigrations && python manage.py migrate"

superuser:
	docker compose exec vrc-ta-hub python manage.py createsuperuser

collectstatic:
	docker compose exec -e DEBUG=False vrc-ta-hub python manage.py collectstatic --noinput

# カレンダー同期
sync:
	docker compose exec vrc-ta-hub python manage.py generate_recurring_events

# Cloudflare Tunnel
tunnel:
	cloudflared tunnel run vrc-ta-hub-local

# ─── DB Sync ───────────────────────────────────────
SHELL := /bin/bash
.SHELLFLAGS := -o pipefail -c

DATE      := $(shell date +%Y%m%d_%H%M%S)
DUMPS_DIR := dumps

DB_SECRET_SUFFIX := PASSWORD
LOCAL_DB_NAME     := $(shell docker compose exec -T vrc-ta-hub printenv DB_NAME 2>/dev/null || grep '^DB_NAME=' .env.local | cut -d= -f2-)
LOCAL_DB_USER     := $(shell docker compose exec -T vrc-ta-hub printenv DB_USER 2>/dev/null || grep '^DB_USER=' .env.local | cut -d= -f2-)
LOCAL_DB_AUTH     := $(shell docker compose exec -T vrc-ta-hub printenv DB_$(DB_SECRET_SUFFIX) 2>/dev/null || grep '^DB_$(DB_SECRET_SUFFIX)=' .env.local | cut -d= -f2-)

COMPOSE_APP_SERVICE      ?= vrc-ta-hub
COMPOSE_DB_SERVICE       ?= db
DB_PULL_VERIFY_TABLE     ?= vket_collaboration
DB_PULL_VERIFY_MIN_ROWS  ?= 1

DUMP_OPTS  := --single-transaction --routines --triggers --no-tablespaces --skip-ssl
MYSQL_OPTS := --skip-ssl
PROD_ENV_FILE ?= .env.production.local

# .env.production.local から DB_ 変数を安全に読み込む
PROD_DB_NAME := $(shell [ ! -f "$(PROD_ENV_FILE)" ] || grep '^DB_NAME=' "$(PROD_ENV_FILE)" | cut -d= -f2-)
PROD_DB_USER := $(shell [ ! -f "$(PROD_ENV_FILE)" ] || grep '^DB_USER=' "$(PROD_ENV_FILE)" | cut -d= -f2-)
PROD_DB_PASSWORD := $(shell [ ! -f "$(PROD_ENV_FILE)" ] || grep '^DB_PASSWORD=' "$(PROD_ENV_FILE)" | cut -d= -f2-)
PROD_DB_HOST := $(shell [ ! -f "$(PROD_ENV_FILE)" ] || grep '^DB_HOST=' "$(PROD_ENV_FILE)" | cut -d= -f2-)

db-backup: ## 本番DBバックアップ → dumps/
	@mkdir -p $(DUMPS_DIR)
	@echo "Backing up production DB ($(PROD_DB_HOST)/$(PROD_DB_NAME))..."
	@MYSQL_PWD="$(PROD_DB_PASSWORD)" mysqldump -h "$(PROD_DB_HOST)" -u "$(PROD_DB_USER)" $(DUMP_OPTS) "$(PROD_DB_NAME)" \
		| gzip > $(DUMPS_DIR)/production_$(DATE).sql.gz
	@echo "Done: $(DUMPS_DIR)/production_$(DATE).sql.gz"

db-backup-local: ## ローカルDBバックアップ → dumps/
	@mkdir -p $(DUMPS_DIR)
	@echo "Backing up Docker Compose local DB ($(LOCAL_DB_NAME))..."
	@docker compose exec -T -e MYSQL_PWD="$(LOCAL_DB_AUTH)" db mysqldump -u "$(LOCAL_DB_USER)" --single-transaction --routines --triggers --no-tablespaces "$(LOCAL_DB_NAME)" \
		| gzip > $(DUMPS_DIR)/local_$(DATE).sql.gz
	@echo "Done: $(DUMPS_DIR)/local_$(DATE).sql.gz"

db-pull: ## 本番DB → ローカルDB
	@test -n "$(PROD_DB_NAME)" -a -n "$(PROD_DB_USER)" -a -n "$(PROD_DB_PASSWORD)" -a -n "$(PROD_DB_HOST)" || \
		(echo "ERROR: $(PROD_ENV_FILE) must define DB_NAME, DB_USER, DB_PASSWORD, and DB_HOST." >&2; exit 1)
	@mkdir -p $(DUMPS_DIR)
	@echo "Dumping production DB ($(PROD_DB_HOST)/$(PROD_DB_NAME))..."
	@MYSQL_PWD="$(PROD_DB_PASSWORD)" mysqldump -h "$(PROD_DB_HOST)" -u "$(PROD_DB_USER)" $(DUMP_OPTS) "$(PROD_DB_NAME)" \
		| gzip > $(DUMPS_DIR)/production.sql.gz
	@echo "Restoring to Docker Compose DB service ($(COMPOSE_DB_SERVICE))..."
	@APP_SERVICE="$(COMPOSE_APP_SERVICE)" \
		DB_SERVICE="$(COMPOSE_DB_SERVICE)" \
		DB_PULL_VERIFY_TABLE="$(DB_PULL_VERIFY_TABLE)" \
		DB_PULL_VERIFY_MIN_ROWS="$(DB_PULL_VERIFY_MIN_ROWS)" \
		scripts/db_pull_restore.sh "$(DUMPS_DIR)/production.sql.gz"
	@echo "Done: production → Docker Compose DB"

db-restore-production-local: db-backup-local db-pull ## ローカルDBを退避してから本番DBをローカルDBへ完全復元

db-verify-local: ## アプリコンテナ経由でローカルDB復元結果を検証
	@docker compose exec -T vrc-ta-hub python manage.py shell -c "from community.models import Community; from event.models import Event; from vket.models import VketCollaboration; print('local DB verify:', {'communities': Community.objects.count(), 'events': Event.objects.count(), 'vket_collaborations': VketCollaboration.objects.count()}); assert Community.objects.exists(); assert Event.objects.exists();"

db-push: ## ローカルDB → 本番DB（確認プロンプト + 自動backup）
	@echo "WARNING: This will OVERWRITE the production database with local data."
	@read -p "Type the production DB name '$(PROD_DB_NAME)' to continue: " confirm && \
		[ "$$confirm" = "$(PROD_DB_NAME)" ] || (echo "Aborted." && exit 1)
	@mkdir -p $(DUMPS_DIR)
	@echo "Auto-backup: production DB..."
	@MYSQL_PWD="$(PROD_DB_PASSWORD)" mysqldump -h "$(PROD_DB_HOST)" -u "$(PROD_DB_USER)" $(DUMP_OPTS) "$(PROD_DB_NAME)" \
		| gzip > $(DUMPS_DIR)/production_before_push_$(DATE).sql.gz
	@echo "Saved: $(DUMPS_DIR)/production_before_push_$(DATE).sql.gz"
	@echo "Dumping Docker Compose local DB ($(LOCAL_DB_NAME))..."
	@docker compose exec -T -e MYSQL_PWD="$(LOCAL_DB_AUTH)" db mysqldump -u "$(LOCAL_DB_USER)" --single-transaction --routines --triggers --no-tablespaces "$(LOCAL_DB_NAME)" \
		| gzip > $(DUMPS_DIR)/local.sql.gz
	@echo "Restoring to production DB ($(PROD_DB_HOST)/$(PROD_DB_NAME))..."
	@MYSQL_PWD="$(PROD_DB_PASSWORD)" mysql -h "$(PROD_DB_HOST)" -u "$(PROD_DB_USER)" $(MYSQL_OPTS) \
		-e "DROP DATABASE IF EXISTS \`$(PROD_DB_NAME)\`; CREATE DATABASE \`$(PROD_DB_NAME)\`;"
	@gunzip -c $(DUMPS_DIR)/local.sql.gz \
		| MYSQL_PWD="$(PROD_DB_PASSWORD)" mysql -h "$(PROD_DB_HOST)" -u "$(PROD_DB_USER)" $(MYSQL_OPTS) "$(PROD_DB_NAME)"
	@echo "Done: $(LOCAL_DB_NAME) → production"
