"""GitHub Actions CIの隔離branch approval gateを静的検証する."""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CI_WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
CONCURRENCY_CANCEL_CONDITION = "${{ github.event_name == 'pull_request' }}"
CONCURRENCY_GROUP = (
    "${{ github.workflow }}-"
    "${{ github.event.pull_request.number || github.run_id }}"
)


def _load_workflow() -> dict[str, object]:
    """YAML key ``on`` を文字列のまま保持してCI workflowを読み込む."""
    payload = yaml.load(CI_WORKFLOW_PATH.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    if not isinstance(payload, dict):
        raise AssertionError("CI workflow must be a mapping")
    return payload


def _normalize_expression(value: object) -> str:
    """YAMLのfolded expressionを空白差に依存せず比較可能にする."""
    if not isinstance(value, str):
        raise AssertionError("workflow expression must be a string")
    return " ".join(value.split())


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


def _jobs_with_job_condition(jobs: dict[str, object]) -> set[str]:
    """gate後の実行をskipできるjob-level条件を持つjob名を返す."""
    conditional_jobs: set[str] = set()
    for job_name, raw_job in jobs.items():
        if job_name == "isolation-gate":
            continue
        if isinstance(raw_job, dict) and "if" in raw_job:
            conditional_jobs.add(job_name)
    return conditional_jobs


class IsolationCiGateTests(unittest.TestCase):
    """隔離PRのhead codeがhuman approval前に実行されないことを保証する."""

    def test_workflow_name_and_triggers_are_stable(self) -> None:
        workflow = _load_workflow()
        self.assertEqual(workflow["name"], "CI")
        triggers = workflow["on"]
        self.assertEqual(triggers["push"]["branches"], ["main"])
        self.assertEqual(triggers["pull_request"]["branches"], ["main"])
        self.assertEqual(
            triggers["pull_request"]["types"],
            ["opened", "synchronize", "reopened", "labeled", "unlabeled"],
        )

    def test_isolation_gate_has_no_checkout_and_fails_without_label(self) -> None:
        workflow = _load_workflow()
        jobs = workflow["jobs"]
        self.assertIsInstance(jobs, dict)
        gate = jobs["isolation-gate"]
        self.assertEqual(gate["name"], "isolation-gate")
        self.assertNotIn("continue-on-error", gate)
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

    def test_all_label_events_restart_full_ci_through_gate(self) -> None:
        workflow = _load_workflow()
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        pull_request = workflow["on"]["pull_request"]
        self.assertIn("labeled", pull_request["types"])
        self.assertIn("unlabeled", pull_request["types"])
        concurrency = workflow["concurrency"]
        self.assertEqual(
            _normalize_expression(concurrency["group"]),
            _normalize_expression(CONCURRENCY_GROUP),
        )
        self.assertEqual(
            _normalize_expression(concurrency["cancel-in-progress"]),
            _normalize_expression(CONCURRENCY_CANCEL_CONDITION),
        )
        self.assertEqual(_jobs_with_job_condition(workflow["jobs"]), set())

    def test_all_non_gate_jobs_directly_need_isolation_gate(self) -> None:
        jobs = _load_workflow()["jobs"]
        self.assertEqual(_jobs_without_direct_isolation_gate(jobs), set())
        self.assertEqual(_jobs_with_job_condition(jobs), set())

    def test_future_job_without_required_guards_is_rejected(self) -> None:
        jobs = dict(_load_workflow()["jobs"])
        jobs["future-job-without-gate"] = {
            "needs": "lint",
            "runs-on": "ubuntu-latest",
            "steps": [{"uses": "actions/checkout@v5"}],
        }
        jobs["future-job-with-skip-condition"] = {
            "needs": "isolation-gate",
            "if": "github.event.action != 'labeled'",
            "runs-on": "ubuntu-latest",
            "steps": [{"uses": "actions/checkout@v5"}],
        }

        self.assertEqual(
            _jobs_without_direct_isolation_gate(jobs),
            {"future-job-without-gate"},
        )
        self.assertEqual(
            _jobs_with_job_condition(jobs),
            {"future-job-with-skip-condition"},
        )
