"""GitHub Actions CIの隔離branch approval gateを静的検証する."""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CI_WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"


def _load_workflow() -> dict[str, object]:
    """YAML key ``on`` を文字列のまま保持してCI workflowを読み込む."""
    payload = yaml.load(CI_WORKFLOW_PATH.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    if not isinstance(payload, dict):
        raise AssertionError("CI workflow must be a mapping")
    return payload


def _jobs_without_direct_isolation_gate(jobs: dict[str, object]) -> set[str]:
    """隔離gateへの直接依存がないjob名を返す."""
    missing_gate: set[str] = set()
    for job_name, raw_job in jobs.items():
        if job_name == "isolation-gate":
            continue
        if not isinstance(raw_job, dict):
            missing_gate.add(job_name)
            continue

        needs = raw_job.get("needs", [])
        if isinstance(needs, str):
            dependencies = {needs}
        elif isinstance(needs, list):
            dependencies = set(needs)
        else:
            dependencies = set()
        if "isolation-gate" not in dependencies:
            missing_gate.add(job_name)
    return missing_gate


class IsolationCiGateTests(unittest.TestCase):
    """隔離PRのhead codeがhuman approval前に実行されないことを保証する."""

    def test_isolation_gate_has_no_checkout_and_fails_without_label(self) -> None:
        workflow = _load_workflow()
        jobs = workflow["jobs"]
        self.assertIsInstance(jobs, dict)
        gate = jobs["isolation-gate"]
        self.assertEqual(gate["name"], "isolation-gate")
        steps = gate["steps"]
        self.assertTrue(all("uses" not in step for step in steps))

        blocking_step = steps[0]
        condition = blocking_step["if"]
        self.assertIn("github.event_name == 'pull_request'", condition)
        self.assertIn(
            "startsWith(github.event.pull_request.head.ref, "
            "'fix-flow/isolation-task-')",
            condition,
        )
        self.assertIn(
            "!contains(github.event.pull_request.labels.*.name, 'safe-to-test')",
            condition,
        )
        self.assertIn("exit 1", blocking_step["run"])
        self.assertTrue(all("${{" not in step.get("run", "") for step in steps))

    def test_label_changes_rerun_gate_with_read_only_permissions(self) -> None:
        workflow = _load_workflow()
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        pull_request = workflow["on"]["pull_request"]
        self.assertEqual(
            set(pull_request["types"]),
            {"opened", "synchronize", "reopened", "labeled", "unlabeled"},
        )
        self.assertEqual(
            workflow["concurrency"],
            {
                "group": (
                    "${{ github.workflow }}-"
                    "${{ github.event.pull_request.number || github.run_id }}"
                ),
                "cancel-in-progress": "${{ github.event_name == 'pull_request' }}",
            },
        )

    def test_all_non_gate_jobs_directly_need_isolation_gate(self) -> None:
        jobs = _load_workflow()["jobs"]
        self.assertEqual(_jobs_without_direct_isolation_gate(jobs), set())

    def test_future_job_without_direct_gate_is_rejected(self) -> None:
        jobs = dict(_load_workflow()["jobs"])
        jobs["future-head-code-job"] = {
            "needs": "lint",
            "runs-on": "ubuntu-latest",
            "steps": [{"uses": "actions/checkout@v5"}],
        }

        self.assertEqual(
            _jobs_without_direct_isolation_gate(jobs),
            {"future-head-code-job"},
        )
