#!/usr/bin/env bash
# 本番の migration を適用する（deploy-check.toml の apply_command の正本）。
#
# Job vrc-ta-hub-migrate を新しいイメージに更新して（create_migrate_job.sh）、既定の引数
# （manage.py migrate --noinput）で実行する。run_manage_command.sh / check_pending_migrations.sh と
# 同じロックを持ち、Job の引数が既定でない時（ほかの実行が差し替えている最中）は何も変えずに断る。
# execute は --args 無しだと Job の今の引数で動くため、差し替えの最中に流すと migration のつもりで
# 別のコマンドを動かしてしまう。
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-vrc-ta-hub}"
REGION="${REGION:-asia-northeast1}"
JOB_NAME="${JOB_NAME:-vrc-ta-hub-migrate}"
IDLE_ARGS="${IDLE_ARGS:-manage.py|migrate|--noinput}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CREATE_JOB_SCRIPT="${CREATE_JOB_SCRIPT:-$SCRIPT_DIR/create_migrate_job.sh}"

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 2
}

command -v gcloud >/dev/null 2>&1 || die 'gcloud CLI not found.'
# shellcheck source=scripts/job_args_lock.sh
source "$SCRIPT_DIR/job_args_lock.sh"
acquire_job_args_lock || exit 2
trap release_job_args_lock EXIT

# Job がまだ無い時（初回）は create_migrate_job.sh が作る。あれば、引数が既定であることを確かめる。
if gcloud run jobs describe "$JOB_NAME" --project="$PROJECT_ID" --region="$REGION" >/dev/null 2>&1; then
  CURRENT_ARGS="$(
    gcloud run jobs describe "$JOB_NAME" \
      --project="$PROJECT_ID" --region="$REGION" \
      --format='value[delimiter="|"](spec.template.spec.template.spec.containers[0].args)'
  )" || die "Could not describe $JOB_NAME."
  [[ "$CURRENT_ARGS" == "$IDLE_ARGS" ]] \
    || die "Args of $JOB_NAME are \"$CURRENT_ARGS\", not \"$IDLE_ARGS\". Another run may be in progress; retry after it finishes."
fi

"$CREATE_JOB_SCRIPT"

gcloud run jobs execute "$JOB_NAME" --project="$PROJECT_ID" --region="$REGION" --wait
