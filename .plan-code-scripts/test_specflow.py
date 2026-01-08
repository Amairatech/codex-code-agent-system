import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SPECFLOW = REPO_ROOT / ".plan-code-scripts" / "specflow.py"


def run_specflow(*args: str, repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SPECFLOW), "--repo", str(repo), *args],
        text=True,
        capture_output=True,
        check=False,
    )


class SpecflowTests(unittest.TestCase):
    def test_init_creates_structure(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            res = run_specflow("init", repo=repo)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertTrue((repo / "openspec" / "project.md").exists())
            self.assertTrue((repo / "openspec" / "changes" / ".gitkeep").exists())
            self.assertTrue((repo / "openspec" / "specs" / ".gitkeep").exists())
            self.assertTrue((repo / "openspec" / "archive" / ".gitkeep").exists())

    def test_proposal_validate_plan_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            self.assertEqual(run_specflow("init", repo=repo).returncode, 0)

            res = run_specflow("proposal", "add-profile-filters", repo=repo)
            self.assertEqual(res.returncode, 0, res.stderr)

            res = run_specflow("validate", "add-profile-filters", repo=repo)
            self.assertEqual(res.returncode, 0, res.stderr)

            res = run_specflow("plan", "add-profile-filters", "--pr", "my-pr", repo=repo)
            self.assertEqual(res.returncode, 0, res.stderr)

            plan_path = repo / ".plans" / "my-pr" / "plan.json"
            payload = json.loads(plan_path.read_text(encoding="utf-8"))
            self.assertIn("tasks", payload)
            self.assertGreaterEqual(len(payload["tasks"]), 1)
            self.assertEqual(payload["tasks"][0]["id"], "task_001")
            self.assertEqual(payload["tasks"][0]["status"], "pending")

            context_path = repo / ".plans" / "my-pr" / "CONTEXT.md"
            self.assertTrue(context_path.exists())

            # Validate-plan succeeds.
            res = run_specflow("validate-plan", "--pr", "my-pr", repo=repo)
            self.assertEqual(res.returncode, 0, res.stderr)

    def test_plan_applies_verify_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            self.assertEqual(run_specflow("init", repo=repo).returncode, 0)
            self.assertEqual(run_specflow("proposal", "x", repo=repo).returncode, 0)

            overrides = repo / "verify_overrides.json"
            overrides.write_text(
                json.dumps(
                    {
                        "default_verify": ["echo default"],
                        "by_task_id": {"task_001": {"verify": ["echo t1"]}},
                    }
                ),
                encoding="utf-8",
            )
            res = run_specflow("plan", "x", "--pr", "p", "--verify-overrides", str(overrides), repo=repo)
            self.assertEqual(res.returncode, 0, res.stderr)
            payload = json.loads((repo / ".plans" / "p" / "plan.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["tasks"][0]["verify"], ["echo t1"])

    def test_plan_suggests_verify_from_package_json(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            self.assertEqual(run_specflow("init", repo=repo).returncode, 0)
            self.assertEqual(run_specflow("proposal", "x", repo=repo).returncode, 0)
            (repo / "package.json").write_text(json.dumps({"scripts": {"test": "echo ok"}}), encoding="utf-8")

            res = run_specflow("plan", "x", "--pr", "p", repo=repo)
            self.assertEqual(res.returncode, 0, res.stderr)
            payload = json.loads((repo / ".plans" / "p" / "plan.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["tasks"][0]["verify"], ["npm test"])

    def test_plan_refuses_overwrite_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            self.assertEqual(run_specflow("init", repo=repo).returncode, 0)
            self.assertEqual(run_specflow("proposal", "x", repo=repo).returncode, 0)
            self.assertEqual(run_specflow("plan", "x", "--pr", "p", repo=repo).returncode, 0)

            res = run_specflow("plan", "x", "--pr", "p", repo=repo)
            self.assertNotEqual(res.returncode, 0)
            self.assertIn("Refusing to overwrite", res.stderr)
