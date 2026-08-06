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

PROD_ENV_FILE ?= .env.production.local

# 本番 dump は RDS(MySQL 8) 向け。SSL を無効化する指定は付けない（RDS は SSL 接続を受け付ける）。
# --set-gtid-purged=OFF は GTID を持たない Compose DB へ流し込むために必須。
PROD_DUMP_OPTS := --single-transaction --routines --triggers --no-tablespaces --set-gtid-purged=OFF

# .env.production.local の値は 1Password 参照（op://...）なので生値を読んではいけない。
# `op run --env-file` で実値へ解決し、コマンド内でシェル変数として参照する。
# また本番 MySQL 8 の認証プラグインにホストの MariaDB クライアントは非対応なため、
# 本番への mysqldump / mysql は Compose の db サービス（MySQL 8 正規クライアント）経由で叩く。
OP_RUN := op run --env-file=$(PROD_ENV_FILE) --
# MYSQL_PWD の値は呼び出し側で `-e MYSQL_PWD="$$DB_PASSWORD"` の形に展開される
PROD_MYSQL_EXEC := docker compose exec -T -e MYSQL_PWD

define require_op
	@command -v op >/dev/null 2>&1 || \
		(echo "ERROR: 1Password CLI (op) not found. $(PROD_ENV_FILE) stores op:// references and requires 'op run'." >&2; exit 1)
	@test -f "$(PROD_ENV_FILE)" || (echo "ERROR: $(PROD_ENV_FILE) not found." >&2; exit 1)
endef

# op run 配下で必須 DB 変数が解決できたかを検証する（値そのものは出力しない）
define assert_prod_db_env
	test -n "$$DB_NAME" -a -n "$$DB_USER" -a -n "$$DB_PASSWORD" -a -n "$$DB_HOST" || \
		{ echo "ERROR: $(PROD_ENV_FILE) must define DB_NAME, DB_USER, DB_PASSWORD, and DB_HOST." >&2; exit 1; }
endef

db-backup: ## 本番DBバックアップ → dumps/
	$(require_op)
	@mkdir -p $(DUMPS_DIR)
	@$(OP_RUN) sh -c '$(assert_prod_db_env); \
		echo "Backing up production DB ($$DB_NAME)..."; \
		$(PROD_MYSQL_EXEC)="$$DB_PASSWORD" $(COMPOSE_DB_SERVICE) mysqldump -h "$$DB_HOST" -u "$$DB_USER" $(PROD_DUMP_OPTS) "$$DB_NAME"' \
		| gzip > $(DUMPS_DIR)/production_$(DATE).sql.gz
	@echo "Done: $(DUMPS_DIR)/production_$(DATE).sql.gz"

db-backup-local: ## ローカルDBバックアップ → dumps/
	@mkdir -p $(DUMPS_DIR)
	@echo "Backing up Docker Compose local DB ($(LOCAL_DB_NAME))..."
	@docker compose exec -T -e MYSQL_PWD="$(LOCAL_DB_AUTH)" db mysqldump -u "$(LOCAL_DB_USER)" --single-transaction --routines --triggers --no-tablespaces "$(LOCAL_DB_NAME)" \
		| gzip > $(DUMPS_DIR)/local_$(DATE).sql.gz
	@echo "Done: $(DUMPS_DIR)/local_$(DATE).sql.gz"

db-pull: ## 本番DB → ローカルDB
	$(require_op)
	@mkdir -p $(DUMPS_DIR)
	@$(OP_RUN) sh -c '$(assert_prod_db_env); \
		echo "Dumping production DB ($$DB_NAME)..."; \
		$(PROD_MYSQL_EXEC)="$$DB_PASSWORD" $(COMPOSE_DB_SERVICE) mysqldump -h "$$DB_HOST" -u "$$DB_USER" $(PROD_DUMP_OPTS) "$$DB_NAME"' \
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
	$(require_op)
	@echo "WARNING: This will OVERWRITE the production database with local data."
	@mkdir -p $(DUMPS_DIR)
	@echo "Dumping Docker Compose local DB ($(LOCAL_DB_NAME))..."
	@docker compose exec -T -e MYSQL_PWD="$(LOCAL_DB_AUTH)" db mysqldump -u "$(LOCAL_DB_USER)" --single-transaction --routines --triggers --no-tablespaces "$(LOCAL_DB_NAME)" \
		| gzip > $(DUMPS_DIR)/local.sql.gz
	@$(OP_RUN) sh -c '$(assert_prod_db_env); \
		printf "Type the production DB name (%s) to continue: " "$$DB_NAME"; \
		read confirm </dev/tty; \
		[ "$$confirm" = "$$DB_NAME" ] || { echo "Aborted."; exit 1; }; \
		echo "Auto-backup: production DB..."; \
		$(PROD_MYSQL_EXEC)="$$DB_PASSWORD" $(COMPOSE_DB_SERVICE) mysqldump -h "$$DB_HOST" -u "$$DB_USER" $(PROD_DUMP_OPTS) "$$DB_NAME" \
			| gzip > $(DUMPS_DIR)/production_before_push_$(DATE).sql.gz; \
		echo "Saved: $(DUMPS_DIR)/production_before_push_$(DATE).sql.gz"; \
		echo "Restoring to production DB ($$DB_NAME)..."; \
		$(PROD_MYSQL_EXEC)="$$DB_PASSWORD" $(COMPOSE_DB_SERVICE) mysql -h "$$DB_HOST" -u "$$DB_USER" \
			-e "DROP DATABASE IF EXISTS \`$$DB_NAME\`; CREATE DATABASE \`$$DB_NAME\`;"; \
		gunzip -c $(DUMPS_DIR)/local.sql.gz \
			| $(PROD_MYSQL_EXEC)="$$DB_PASSWORD" $(COMPOSE_DB_SERVICE) mysql -h "$$DB_HOST" -u "$$DB_USER" "$$DB_NAME"'
	@echo "Done: $(LOCAL_DB_NAME) → production"
