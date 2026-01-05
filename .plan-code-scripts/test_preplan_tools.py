import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PREPLAN_RESEARCH = REPO_ROOT / ".plan-code-scripts" / "preplan_research.py"
PREPLAN_GEMINI = REPO_ROOT / ".plan-code-scripts" / "preplan_gemini_review.py"


def run_py(script: Path, *args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
    )


class PreplanToolsTests(unittest.TestCase):
    def test_research_dry_run_writes_plan(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            res = run_py(
                PREPLAN_RESEARCH,
                "--repo",
                str(repo),
                "--pr",
                "my-pr",
                "--goal",
                "Do the thing",
                "--dry-run",
                cwd=repo,
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            plan = repo / ".plans" / "my-pr" / "preplan" / "research.plan.json"
            self.assertTrue(plan.exists())
            payload = json.loads(plan.read_text(encoding="utf-8"))
            self.assertEqual(payload["tasks"][0]["id"], "task_000")
            expected = ".plans/my-pr/research/RESEARCH.md"
            self.assertIn(expected, payload["tasks"][0]["expected_files"][0])
            # Resilience: always writes a checkpoint RESEARCH.md even in dry-run mode.
            self.assertTrue((repo / ".plans" / "my-pr" / "research" / "RESEARCH.md").exists())

    def test_gemini_dry_run_writes_review(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            res = run_py(
                PREPLAN_GEMINI,
                "--repo",
                str(repo),
                "--pr",
                "my-pr",
                "--dry-run",
                cwd=repo,
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            out = repo / ".plans" / "my-pr" / "preplan" / "GEMINI_PLAN_REVIEW.md"
            self.assertTrue(out.exists())
            txt = out.read_text(encoding="utf-8")
            self.assertIn("gemini-3-pro-preview", txt)
