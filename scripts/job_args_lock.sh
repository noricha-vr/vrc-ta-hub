# shellcheck shell=bash
# Cloud Run Job の引数を一時的に差し替えるスクリプト（run_manage_command.sh /
# check_pending_migrations.sh）が共有する排他ロック。source して使う。
#
# Job の引数は 1 つしかなく、この環境では execute --args による実行ごとの上書きが使えない。
# 2 つの実行が同時に差し替えると、互いの引数で動いたり、復元が相手の差し替えを消したりする
# （2026-10-10 に dry-run のつもりの実行が本番の書き換えを行った）。そこで、引数を読む前から
# 復元し終えるまでを、このロックで 1 本に限る。
#
# ロックはこの端末の中だけで効く（mkdir の原子性を使う）。別の端末や CI から同じ Job の引数を
# 差し替える経路を作る時は、このロックでは守れないので、同時に動かさない運用で補う。

# 同じ Job を短い名前と完全な名前（projects/.../jobs/<name>）のどちらで指定しても同じロックになるよう、
# プロジェクト・リージョン・Job 名の末尾で決める
_job_args_lock_key="${PROJECT_ID:-vrc-ta-hub}-${REGION:-asia-northeast1}-${JOB_NAME##*/}"
JOB_ARGS_LOCK_DIR="${JOB_ARGS_LOCK_DIR:-${HOME}/.cache/vrc-ta-hub/job-args-${_job_args_lock_key}.lock}"
JOB_ARGS_LOCK_HELD=''

acquire_job_args_lock() {
  mkdir -p "$(dirname "$JOB_ARGS_LOCK_DIR")"
  if ! mkdir "$JOB_ARGS_LOCK_DIR" 2>/dev/null; then
    local holder
    holder="$(cat "$JOB_ARGS_LOCK_DIR/pid" 2>/dev/null || true)"
    # 残骸の自動回収はしない（2 本が同時に回収すると、相手の新しいロックを消しうる）。
    # 持ち主が居ないなら、Job の引数が既定に戻っていることを確かめてから人が消す。
    if [[ "$holder" =~ ^[0-9]+$ ]] && ! kill -0 "$holder" 2>/dev/null; then
      printf 'ERROR: stale lock %s (pid %s is gone). Check the job args are back to the default, then remove it.\n' \
        "$JOB_ARGS_LOCK_DIR" "$holder" >&2
    else
      printf 'ERROR: another run holds %s (pid %s). Retry after it finishes.\n' \
        "$JOB_ARGS_LOCK_DIR" "${holder:-unknown}" >&2
    fi
    return 1
  fi
  if ! printf '%s\n' "$$" > "$JOB_ARGS_LOCK_DIR/pid"; then
    rm -rf "$JOB_ARGS_LOCK_DIR"
    printf 'ERROR: could not write the owner of %s.\n' "$JOB_ARGS_LOCK_DIR" >&2
    return 1
  fi
  JOB_ARGS_LOCK_HELD=1
}

release_job_args_lock() {
  [[ -n "$JOB_ARGS_LOCK_HELD" ]] || return 0
  [[ "$(cat "$JOB_ARGS_LOCK_DIR/pid" 2>/dev/null || true)" == "$$" ]] && rm -rf "$JOB_ARGS_LOCK_DIR"
  JOB_ARGS_LOCK_HELD=''
}
