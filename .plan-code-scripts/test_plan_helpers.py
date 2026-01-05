import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MARK_VERIFIED = REPO_ROOT / ".plan-code-scripts" / "mark_verified.py"
SYNC_PLAN = REPO_ROOT / ".plan-code-scripts" / "sync_plan.py"


def run_py(script: Path, *args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
    )


class PlanHelpersTests(unittest.TestCase):
    def test_mark_verified_updates_plan_and_master(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            plan_dir = repo / ".plans" / "p"
            plan_dir.mkdir(parents=True)
            (plan_dir / "plan.json").write_text(
                json.dumps(
                    {
                        "goal": "x",
                        "tasks": [
                            {
                                "id": "task_009",
                                "title": "t",
                                "description": "d",
                                "expected_files": [],
                                "verify": [],
                                "done_criteria": [],
                                "depends_on": [],
                                "status": "agent_done",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            res = run_py(MARK_VERIFIED, "--repo", str(repo), "--pr", "p", "--task", "task_009", "--note", "device ok", cwd=repo)
            self.assertEqual(res.returncode, 0, res.stderr)
            payload = json.loads((plan_dir / "plan.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["tasks"][0]["status"], "done")
            self.assertTrue((plan_dir / "MASTER.md").exists())

    def test_sync_plan_marks_agent_done_from_results(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            pr = "p"
            plan_dir = repo / ".plans" / pr
            plan_dir.mkdir(parents=True)
            (plan_dir / "plan.json").write_text(
                json.dumps(
                    {
                        "goal": "x",
                        "tasks": [
                            {
                                "id": "task_001",
                                "title": "t",
                                "description": "d",
                                "expected_files": [],
                                "verify": [],
                                "done_criteria": [],
                                "depends_on": [],
                                "status": "pending",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            runs_root = repo / "runs" / pr / "r1"
            (runs_root / "results").mkdir(parents=True)
            (runs_root / "meta.json").write_text(json.dumps({"created_at": "2026-01-01T00:00:00+00:00", "invocation_run_id": "rid"}), encoding="utf-8")
            (runs_root / "results" / "task_001.json").write_text(
                json.dumps({"task_id": "task_001", "run_id": "rid", "status": "completed"}),
                encoding="utf-8",
            )
            res = run_py(SYNC_PLAN, "--repo", str(repo), "--pr", pr, "--run-root", str(repo / "runs"), cwd=repo)
            self.assertEqual(res.returncode, 0, res.stderr)
            payload = json.loads((plan_dir / "plan.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["tasks"][0]["status"], "agent_done")

