#!/bin/sh
set -eu

ENV_FILE="${COMMUNITY_ACTIVITY_ENV_FILE:-$HOME/.config/vrc-ta-hub/community-activity.env}"
if [ ! -f "$ENV_FILE" ]; then
  echo "environment file not found: $ENV_FILE" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPOSITORY_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
PYTHON_BIN=${PYTHON_BIN:-python3}

cd "$REPOSITORY_DIR"
exec "$PYTHON_BIN" scripts/community_activity_monitor.py --apply "$@"
