#!/usr/bin/env bash
set -euo pipefail

APP_SERVICE="${APP_SERVICE:-vrc-ta-hub}"
DB_SERVICE="${DB_SERVICE:-db}"
DB_PULL_VERIFY_TABLE="${DB_PULL_VERIFY_TABLE:-vket_collaboration}"
DB_PULL_VERIFY_MIN_ROWS="${DB_PULL_VERIFY_MIN_ROWS:-1}"

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

compose() {
  docker compose "$@"
}

validate_identifier() {
  local label="$1"
  local value="$2"

  [[ "$value" =~ ^[A-Za-z0-9_]+$ ]] || die "$label must contain only letters, numbers, and underscores: $value"
}

get_service_env() {
  local service="$1"
  local name="$2"

  compose exec -T "$service" env \
    | awk -F= -v key="$name" '
      $1 == key {
        sub(/^[^=]*=/, "")
        print
        found = 1
        exit
      }
      END {
        if (!found) {
          exit 1
        }
      }
    '
}

parse_last_numeric_line() {
  awk '
    /^[[:space:]]*[0-9]+[[:space:]]*$/ {
      value = $1
    }
    END {
      if (value == "") {
        exit 1
      }
      print value
    }
  '
}

restore_dump_to_compose_db() {
  local dump_path="$1"
  local db_name="$2"
  local db_user="$3"
  local db_password="$4"

  compose exec -T -e "MYSQL_PWD=$db_password" "$DB_SERVICE" \
    mysql -u "$db_user" \
    -e "DROP DATABASE IF EXISTS \`$db_name\`; CREATE DATABASE \`$db_name\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"

  gunzip -c "$dump_path" \
    | compose exec -T -e "MYSQL_PWD=$db_password" "$DB_SERVICE" \
      mysql -u "$db_user" "$db_name"
}

verify_app_table_count() {
  local db_name="$1"
  local table_name="$2"
  local min_rows="$3"
  local min_rows_int
  local verify_output
  local count

  min_rows_int=$((10#$min_rows))

  verify_output="$(
    compose exec -T "$APP_SERVICE" python manage.py shell -c \
      "from django.db import connection
cursor = connection.cursor()
cursor.execute('SELECT COUNT(*) FROM \`$table_name\`')
print(cursor.fetchone()[0])"
  )"

  count="$(printf '%s\n' "$verify_output" | parse_last_numeric_line)" \
    || die "Could not parse verification count for $table_name from app container output."

  printf 'Verified via app container: %s.%s has %s rows.\n' "$db_name" "$table_name" "$count"

  if (( count < min_rows_int )); then
    die "Verification failed: $table_name has $count rows, expected at least $min_rows_int."
  fi
}

main() {
  local dump_path="${1:-dumps/production.sql.gz}"
  local app_db_name
  local app_db_host
  local app_db_user
  local app_db_password

  [[ -f "$dump_path" ]] || die "Dump file does not exist: $dump_path"
  [[ "$DB_PULL_VERIFY_MIN_ROWS" =~ ^[0-9]+$ ]] || die "DB_PULL_VERIFY_MIN_ROWS must be a non-negative integer."

  app_db_name="$(get_service_env "$APP_SERVICE" DB_NAME)" \
    || die "Could not read DB_NAME from app service: $APP_SERVICE"
  app_db_host="$(get_service_env "$APP_SERVICE" DB_HOST)" \
    || die "Could not read DB_HOST from app service: $APP_SERVICE"
  app_db_user="$(get_service_env "$APP_SERVICE" DB_USER)" \
    || die "Could not read DB_USER from app service: $APP_SERVICE"
  app_db_password="$(get_service_env "$APP_SERVICE" DB_PASSWORD)" \
    || die "Could not read DB_PASSWORD from app service: $APP_SERVICE"

  [[ -n "$app_db_name" ]] || die "DB_NAME is empty in app service: $APP_SERVICE"
  [[ -n "$app_db_host" ]] || die "DB_HOST is empty in app service: $APP_SERVICE"
  [[ -n "$app_db_user" ]] || die "DB_USER is empty in app service: $APP_SERVICE"
  [[ -n "$app_db_password" ]] || die "DB_PASSWORD is empty in app service: $APP_SERVICE"
  [[ "$app_db_host" == "$DB_SERVICE" ]] \
    || die "App DB_HOST is '$app_db_host', but db-pull restores to Docker Compose service '$DB_SERVICE'."

  validate_identifier DB_NAME "$app_db_name"
  validate_identifier DB_PULL_VERIFY_TABLE "$DB_PULL_VERIFY_TABLE"

  printf 'Restoring %s to Compose service %s database %s as %s.\n' "$dump_path" "$DB_SERVICE" "$app_db_name" "$app_db_user"
  restore_dump_to_compose_db "$dump_path" "$app_db_name" "$app_db_user" "$app_db_password"
  verify_app_table_count "$app_db_name" "$DB_PULL_VERIFY_TABLE" "$DB_PULL_VERIFY_MIN_ROWS"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
