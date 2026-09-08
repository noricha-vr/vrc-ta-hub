"""DBやopに接続せず、dotenvの受け渡しとmake db-backupを検証する。"""

import gzip
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts/production_db_env.py"


class ProductionDbEnvTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env_file = self.root / "production settings.env"
        self.values = dict(DB_HOST="example.invalid", DB_NAME="fixture_db", DB_USER="fixture_user", DB_PASSWORD="fixture-password")
        self.base = {"PATH": os.path.dirname(sys.executable) + ":/usr/bin:/bin", "HOME": self.tmp.name}
        self.write_env()

    def write_env(self, changes=None, extra=""):
        values = {**self.values, **(changes or {})}
        self.env_file.write_text("".join(f"{k}='{v}'\n" for k, v in values.items()) + extra)

    def run_command(self, *command, extra_env=None):
        return subprocess.run([sys.executable, str(RUNNER), str(self.env_file), *command],
                              env={**self.base, **(extra_env or {})}, capture_output=True, text=True)

    def test_exec_preserves_literal_values_without_shell_evaluation(self):
        value = f"$dollar $$two #hash space `touch {self.root}/bad` $(touch {self.root}/bad2) \\literal"
        self.write_env({"DB_PASSWORD": value}, "UNUSED_API_KEY=must-not-be-exported\n")
        code = "import os,json; print(json.dumps({k:os.environ.get(k) for k in ['DB_HOST','DB_NAME','DB_USER','DB_PASSWORD','UNUSED_API_KEY','OP_SERVICE_ACCOUNT_TOKEN']}))"
        result = self.run_command("--", sys.executable, "-c", code,
                                  extra_env={"DB_HOST": "stale.invalid", "OP_SERVICE_ACCOUNT_TOKEN": "fixture-token"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {**self.values, "DB_PASSWORD": value, "UNUSED_API_KEY": None, "OP_SERVICE_ACCOUNT_TOKEN": None})
        self.assertFalse((self.root / "bad").exists())
        self.assertFalse((self.root / "bad2").exists())

    def test_compose_double_quotes(self):
        value = 'dollar$ quote" slash\\'
        encoded = json.dumps(value).replace("$", "$$")
        self.env_file.write_text("".join(f"{k}={encoded if k == 'DB_PASSWORD' else v}\n" for k, v in self.values.items()))
        result = self.run_command("--", sys.executable, "-c", "import os,json; print(json.dumps(os.environ['DB_PASSWORD']))")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), value)

    def test_invalid_input_never_runs_child_or_prints_value(self):
        for raw in ["'op://fixture/item/private'", "'private\x00value'", '"private\\nvalue"', "'private", "''"]:
            with self.subTest(raw=raw):
                self.env_file.write_text("".join(f"{k}={raw if k == 'DB_PASSWORD' else v}\n" for k, v in self.values.items()))
                result = self.run_command("--", sys.executable, "-c", "print('CHILD_EXECUTED')")
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertNotIn("private", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_missing_or_duplicate_key_and_missing_file(self):
        self.env_file.write_text("DB_HOST=example.invalid\n")
        self.assertNotEqual(self.run_command("--check", extra_env=self.values).returncode, 0)
        self.write_env(extra="DB_HOST=duplicate.invalid\n")
        self.assertNotEqual(self.run_command("--check").returncode, 0)
        self.env_file.unlink()
        self.assertNotEqual(self.run_command("--check").returncode, 0)

    def test_check_is_quiet_and_child_exit_is_preserved(self):
        result = self.run_command("--check")
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        self.assertEqual(self.run_command("--", sys.executable, "-c", "raise SystemExit(7)").returncode, 7)

    def test_make_backup_uses_env_not_password_argument(self):
        shutil.copy(ROOT / "Makefile", self.root / "Makefile")
        (self.root / "scripts").symlink_to(ROOT / "scripts", target_is_directory=True)
        binary = self.root / "bin"
        binary.mkdir()
        docker = binary / "docker"
        docker.write_text(f"#!{sys.executable}\n" + "import os,sys,json\nfrom pathlib import Path\nPath('docker-call.json').write_text(json.dumps({'args':sys.argv[1:],'password':os.environ.get('MYSQL_PWD')}))\nprint('CREATE TABLE fixture (id int);')\n")
        docker.chmod(0o755)
        op = binary / "op"
        op.write_text("#!/bin/sh\nexit 99\n")
        op.chmod(0o755)
        result = subprocess.run(["make", "db-backup", f"PROD_ENV_FILE={self.env_file}", "DATE=fixture"],
                                cwd=self.root, env={**self.base, "PATH": str(binary) + ":" + self.base["PATH"]},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        call = json.loads((self.root / "docker-call.json").read_text())
        self.assertEqual(call["password"], self.values["DB_PASSWORD"])
        self.assertNotIn(self.values["DB_PASSWORD"], " ".join(call["args"]))
        self.assertIn("mysqldump", call["args"])
        self.assertNotIn("printenv", call["args"])
        self.assertNotIn(self.values["DB_PASSWORD"], result.stdout + result.stderr)
        with gzip.open(self.root / "dumps/production_fixture.sql.gz", "rt") as source:
            self.assertIn("CREATE TABLE", source.read())


if __name__ == "__main__":
    unittest.main()
