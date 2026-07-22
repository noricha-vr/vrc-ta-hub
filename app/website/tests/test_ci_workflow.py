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

    def test_all_head_code_jobs_need_isolation_gate(self) -> None:
        jobs = _load_workflow()["jobs"]
        expected_needs = {
            "lint": {"isolation-gate"},
            "test": {"isolation-gate", "lint"},
            "e2e": {"isolation-gate", "lint"},
            "mypy": {"isolation-gate", "lint"},
        }
        for job_name, expected in expected_needs.items():
            job = jobs[job_name]
            needs = job["needs"]
            actual = {needs} if isinstance(needs, str) else set(needs)
            self.assertEqual(actual, expected)
            self.assertTrue(any(step.get("uses") == "actions/checkout@v5" for step in job["steps"]))
