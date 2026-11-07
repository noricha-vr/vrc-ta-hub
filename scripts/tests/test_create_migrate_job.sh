#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
export MOCK_DIR="$TMP_DIR"

cat > "$TMP_DIR/gcloud" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$@" >> "$MOCK_DIR/calls"
case "$*" in
  'run services describe '*'--format=value(spec.template.spec.containers[0].image)')
    printf 'example.invalid/test/service:current\n'
    ;;
  'run services describe '*'--format=value(spec.template.spec.serviceAccountName)')
    printf 'test-service-account@example.invalid\n'
    ;;
  'run services describe '*'--format=json(spec.template.spec.containers[0].env)')
    cat "$MOCK_DIR/service.json"
    ;;
  'run jobs describe '*'--format=json(spec.template.spec.template.spec.containers[0].env)')
    [[ "$MOCK_CASE" != create ]] || exit 1
    cat "$MOCK_DIR/job.json"
    ;;
  'run jobs update '*|'run jobs create '*)
    printf '%s\n' "$@" > "$MOCK_DIR/args"
    for arg in "$@"; do
      if [[ "$arg" == --env-vars-file=* ]]; then
        cp "${arg#--env-vars-file=}" "$MOCK_DIR/env"
      fi
    done
    ;;
  *) exit 99 ;;
esac
EOF
chmod +x "$TMP_DIR/gcloud"
export PATH="$TMP_DIR:$PATH"
# 呼出元の設定に依存せず、必ず偽の gcloud から設定を読む。
unset IMAGE KEEP_JOB_ENV
export PROJECT_ID=test-project REGION=test-region SERVICE_NAME=test-service JOB_NAME=test-migrate

run_case() {
  export MOCK_CASE="$1"
  : > "$TMP_DIR/calls"
  rm -f "$TMP_DIR/args" "$TMP_DIR/env"
  python3 - <<'PY'
import json
import os
from pathlib import Path

root = Path(os.environ["MOCK_DIR"])


def plain(name, value):
    return {"name": name, "value": value}


def secret(name, ref, version="latest"):
    return {"name": name, "valueFrom": {"secretKeyRef": {"name": ref, "key": version}}}


service_env = [
    secret("DB_USER", "svc-db-user-secret"),
    secret("DB_PASSWORD", "svc-db-password-secret"),
    secret("DB_HOST", "svc-db-host-secret"),
    plain("APP_LABEL", 'service-label: "test"\nsecond line'),
    secret("SHARED_SECRET", "svc-shared-secret"),
]
job_env = [
    plain("DB_USER", "job-db-user"),
    secret("DB_PASSWORD", "job-db-password-secret", "7"),
    plain("DB_HOST", "job-db-host.example.invalid"),
    plain("APP_LABEL", "old-job-label"),
    plain("OLD_ENV", "old-job-value"),
    secret("OLD_SECRET", "old-job-secret"),
]
if os.environ["MOCK_CASE"] == "asymmetric":
    # Job にない保持項目は足さず、サービスにない保持項目も残す。
    job_env = [entry for entry in job_env if entry["name"] != "DB_USER"]
    service_env = [entry for entry in service_env if entry["name"] != "DB_HOST"]
elif os.environ["MOCK_CASE"] == "custom":
    # 任意の保持対象と、サービスの平文から Job のシークレット参照への切替。
    service_env[0] = plain("DB_USER", "svc-db-user")
    job_env[0] = secret("DB_USER", "job-db-user-secret", "3")
    job_env[3] = secret("APP_LABEL", "job-label-secret", "5")

service = {"spec": {"template": {"spec": {"containers": [{"env": service_env}]}}}}
job = {"spec": {"template": {"spec": {"template": {"spec": {"containers": [{"env": job_env}]}}}}}}
(root / "service.json").write_text(json.dumps(service), encoding="utf-8")
(root / "job.json").write_text(json.dumps(job), encoding="utf-8")
PY

  bash "$REPO_ROOT/scripts/create_migrate_job.sh" > "$TMP_DIR/stdout" 2> "$TMP_DIR/stderr"
  python3 - <<'PY'
import json
import os
from pathlib import Path

root = Path(os.environ["MOCK_DIR"])
case = os.environ["MOCK_CASE"]
args = (root / "args").read_text().splitlines()
calls = (root / "calls").read_text()
logs = (root / "stdout").read_text() + (root / "stderr").read_text()
service = json.loads((root / "service.json").read_text())["spec"]["template"]["spec"]["containers"][0]["env"]
job = json.loads((root / "job.json").read_text())["spec"]["template"]["spec"]["template"]["spec"]["containers"][0]["env"]

assert args[:4] == ["run", "jobs", "create" if case == "create" else "update", "test-migrate"]
for expected in (
    "--project=test-project", "--region=test-region",
    "--image=example.invalid/test/service:current",
    "--service-account=test-service-account@example.invalid",
    "--command=python", "--args=^|^manage.py|migrate|--noinput",
):
    assert expected in args, "Missing inherited setting or migration argument"
assert "--format=json(spec.template.spec.template.spec.containers[0].env)" in calls

expected_plain = {"APP_LABEL": 'service-label: "test"\nsecond line'}
expected_secrets = {"SHARED_SECRET=svc-shared-secret:latest"}
expected_log = None
if case in ("create", "disabled"):
    expected_secrets.update({
        "DB_USER=svc-db-user-secret:latest",
        "DB_PASSWORD=svc-db-password-secret:latest",
        "DB_HOST=svc-db-host-secret:latest",
    })
elif case == "custom":
    expected_plain = {}
    expected_secrets.update({
        "DB_USER=job-db-user-secret:3",
        "APP_LABEL=job-label-secret:5",
        "DB_PASSWORD=svc-db-password-secret:latest",
        "DB_HOST=svc-db-host-secret:latest",
    })
    expected_log = "Kept from job: DB_USER APP_LABEL"
else:
    expected_plain["DB_HOST"] = "job-db-host.example.invalid"
    expected_secrets.add("DB_PASSWORD=job-db-password-secret:7")
    if case == "update":
        expected_plain["DB_USER"] = "job-db-user"
        expected_log = "Kept from job: DB_USER DB_PASSWORD DB_HOST"
    else:
        assert case == "asymmetric"
        expected_log = "Kept from job: DB_PASSWORD DB_HOST"
# JSON 文字列でクォートされた YAML を読み、改行・引用符を含む値も比較する。
actual_plain = {}
for line in (root / "env").read_text().splitlines():
    if line:
        name, value = line.split(": ", 1)
        actual_plain[json.loads(name)] = json.loads(value)
assert actual_plain == expected_plain, "Plain environment differs"
secret_args = [arg for arg in args if arg.startswith("--set-secrets=")]
assert len(secret_args) == 1, "Expected full secret replacement"
assert set(secret_args[0].removeprefix("--set-secrets=").split(",")) == expected_secrets, "Secret references differ"
assert not any(arg.startswith(("--update-env-vars=", "--update-secrets=")) for arg in args)
if expected_log:
    assert [line for line in logs.splitlines() if line.startswith("Kept from job:")] == [expected_log]
else:
    assert "Kept from job:" not in logs

# ケース 4: すべてのケースで平文値が引数・標準出力・ログに漏れないことを確認する。
for entry in service + job:
    if "value" in entry:
        assert entry["value"] not in calls + logs, "Plain value leaked"
        for line in entry["value"].splitlines():
            assert line not in calls + logs, "Plain value line leaked"
env_path = next(arg.removeprefix("--env-vars-file=") for arg in args if arg.startswith("--env-vars-file="))
assert not Path(env_path).exists(), "Temporary environment file was not removed"
PY
  printf 'PASS: create_migrate_job %s\n' "$MOCK_CASE"
}

# ケース 1: 既存 Job の平文・シークレット参照を残し、それ以外をサービスから引き継ぐ。
run_case update
# ケース 2: Job がなければ、保持対象もサービスから引き継いで新規作成する。
run_case create
# ケース 3: 明示した空文字列では、更新時もサービスの値に置き換える。
KEEP_JOB_ENV="" run_case disabled
run_case asymmetric
KEEP_JOB_ENV="DB_USER APP_LABEL" run_case custom
printf 'PASS: no plaintext values in gcloud arguments or logs (all cases)\n'
