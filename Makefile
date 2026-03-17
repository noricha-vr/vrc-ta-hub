.PHONY: up down build restart logs shell test migrate makemigrations superuser tunnel collectstatic sync db-backup db-backup-local db-pull db-push

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

LOCAL_DB_HOST     := 127.0.0.1
LOCAL_DB_PORT     := 3306
LOCAL_DB_USER     := root
LOCAL_DB_PASSWORD := root
LOCAL_DB_NAME     := local_vrc_ta_hub

DUMP_OPTS  := --single-transaction --routines --triggers --no-tablespaces --skip-ssl
MYSQL_OPTS := --skip-ssl

# .env.production.local から DB_ 変数を安全に読み込む
PROD_DB_NAME := $(shell grep '^DB_NAME=' .env.production.local | cut -d= -f2-)
PROD_DB_USER := $(shell grep '^DB_USER=' .env.production.local | cut -d= -f2-)
PROD_DB_PASSWORD := $(shell grep '^DB_PASSWORD=' .env.production.local | cut -d= -f2-)
PROD_DB_HOST := $(shell grep '^DB_HOST=' .env.production.local | cut -d= -f2-)

db-backup: ## 本番DBバックアップ → dumps/
	@mkdir -p $(DUMPS_DIR)
	@echo "Backing up production DB ($(PROD_DB_HOST)/$(PROD_DB_NAME))..."
	@MYSQL_PWD="$(PROD_DB_PASSWORD)" mysqldump -h "$(PROD_DB_HOST)" -u "$(PROD_DB_USER)" $(DUMP_OPTS) "$(PROD_DB_NAME)" \
		| gzip > $(DUMPS_DIR)/production_$(DATE).sql.gz
	@echo "Done: $(DUMPS_DIR)/production_$(DATE).sql.gz"

db-backup-local: ## ローカルDBバックアップ → dumps/
	@mkdir -p $(DUMPS_DIR)
	@echo "Backing up local DB ($(LOCAL_DB_NAME))..."
	@MYSQL_PWD=$(LOCAL_DB_PASSWORD) mysqldump -h $(LOCAL_DB_HOST) -P $(LOCAL_DB_PORT) -u $(LOCAL_DB_USER) $(DUMP_OPTS) "$(LOCAL_DB_NAME)" \
		| gzip > $(DUMPS_DIR)/local_$(DATE).sql.gz
	@echo "Done: $(DUMPS_DIR)/local_$(DATE).sql.gz"

db-pull: ## 本番DB → ローカルDB
	@mkdir -p $(DUMPS_DIR)
	@echo "Dumping production DB ($(PROD_DB_HOST)/$(PROD_DB_NAME))..."
	@MYSQL_PWD="$(PROD_DB_PASSWORD)" mysqldump -h "$(PROD_DB_HOST)" -u "$(PROD_DB_USER)" $(DUMP_OPTS) "$(PROD_DB_NAME)" \
		| gzip > $(DUMPS_DIR)/production.sql.gz
	@echo "Restoring to local DB ($(LOCAL_DB_NAME))..."
	@MYSQL_PWD=$(LOCAL_DB_PASSWORD) mysql -h $(LOCAL_DB_HOST) -P $(LOCAL_DB_PORT) -u $(LOCAL_DB_USER) $(MYSQL_OPTS) \
		-e "DROP DATABASE IF EXISTS \`$(LOCAL_DB_NAME)\`; CREATE DATABASE \`$(LOCAL_DB_NAME)\`;"
	@gunzip -c $(DUMPS_DIR)/production.sql.gz \
		| MYSQL_PWD=$(LOCAL_DB_PASSWORD) mysql -h $(LOCAL_DB_HOST) -P $(LOCAL_DB_PORT) -u $(LOCAL_DB_USER) $(MYSQL_OPTS) "$(LOCAL_DB_NAME)"
	@echo "Done: production → $(LOCAL_DB_NAME)"

db-push: ## ローカルDB → 本番DB（確認プロンプト + 自動backup）
	@echo "WARNING: This will OVERWRITE the production database with local data."
	@read -p "Type the production DB name '$(PROD_DB_NAME)' to continue: " confirm && \
		[ "$$confirm" = "$(PROD_DB_NAME)" ] || (echo "Aborted." && exit 1)
	@mkdir -p $(DUMPS_DIR)
	@echo "Auto-backup: production DB..."
	@MYSQL_PWD="$(PROD_DB_PASSWORD)" mysqldump -h "$(PROD_DB_HOST)" -u "$(PROD_DB_USER)" $(DUMP_OPTS) "$(PROD_DB_NAME)" \
		| gzip > $(DUMPS_DIR)/production_before_push_$(DATE).sql.gz
	@echo "Saved: $(DUMPS_DIR)/production_before_push_$(DATE).sql.gz"
	@echo "Dumping local DB ($(LOCAL_DB_NAME))..."
	@MYSQL_PWD=$(LOCAL_DB_PASSWORD) mysqldump -h $(LOCAL_DB_HOST) -P $(LOCAL_DB_PORT) -u $(LOCAL_DB_USER) $(DUMP_OPTS) "$(LOCAL_DB_NAME)" \
		| gzip > $(DUMPS_DIR)/local.sql.gz
	@echo "Restoring to production DB ($(PROD_DB_HOST)/$(PROD_DB_NAME))..."
	@MYSQL_PWD="$(PROD_DB_PASSWORD)" mysql -h "$(PROD_DB_HOST)" -u "$(PROD_DB_USER)" $(MYSQL_OPTS) \
		-e "DROP DATABASE IF EXISTS \`$(PROD_DB_NAME)\`; CREATE DATABASE \`$(PROD_DB_NAME)\`;"
	@gunzip -c $(DUMPS_DIR)/local.sql.gz \
		| MYSQL_PWD="$(PROD_DB_PASSWORD)" mysql -h "$(PROD_DB_HOST)" -u "$(PROD_DB_USER)" $(MYSQL_OPTS) "$(PROD_DB_NAME)"
	@echo "Done: $(LOCAL_DB_NAME) → production"
