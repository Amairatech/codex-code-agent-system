#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now_rfc3339() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _mkdirp(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _write_json(path: Path, payload: Any) -> None:
    _mkdirp(path.parent)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _slugify(name: str) -> str:
    s = name.strip()
    if not s:
        raise ValueError("Empty PR name")
    # Keep it conservative: planner/orchestrator doesn't require strict slugs,
    # but using a git-branch-safe name avoids surprises.
    safe = []
    for ch in s:
        if ch.isalnum() or ch in ("-", "_", "/"):
            safe.append(ch)
        else:
            safe.append("-")
    out = "".join(safe).strip("-")
    return out or "pr"


@dataclass(frozen=True)
class ResearchPlanPaths:
    plan_dir: Path
    plan_file: Path
    output_md: Path


def _default_output_md(*, plan_dir: Path) -> Path:
    return plan_dir / "research" / "RESEARCH.md"


def create_research_plan(*, repo: Path, pr_name: str, goal: str, output_md: Path | None) -> ResearchPlanPaths:
    repo = repo.resolve()
    plan_dir = repo / ".plans" / _slugify(pr_name)
    plan_file = plan_dir / "preplan" / "research.plan.json"
    out_md = output_md.resolve() if output_md else _default_output_md(plan_dir=plan_dir)

    payload: dict[str, Any] = {
        "created_at": _utc_now_rfc3339(),
        "goal": goal,
        "tasks": [
            {
                "id": "task_000",
                "title": "Research (MCP) + repo discovery",
                "description": (
                    "You are the Research Agent. Your job is to collect the minimum research needed so the planner can "
                    "draft 3 high-quality plan options without polluting its context.\n\n"
                    "1) Repo discovery: briefly summarize the current codebase (stack, structure, key entrypoints).\n"
                    "2) MCP research: use context7-mcp first, then perplexity-server as needed. Focus on best practices, "
                    "edge cases, and verification approaches relevant to the goal.\n"
                    "3) Write a concise research brief to the required output file.\n\n"
                    "Constraints:\n"
                    "- Do not implement product code.\n"
                    "- Do not edit existing files except writing the research output.\n"
                    "- Keep the research brief actionable: include suggested plan phases and concrete verify commands.\n"
                ),
                "expected_files": [
                    str(out_md.relative_to(repo)),
                ],
                "verify": [
                    f"test -f {shlex.quote(str(out_md.relative_to(repo)))}",
                ],
                "done_criteria": [
                    f"Research brief exists at {out_md.relative_to(repo)}",
                    "Includes: repo summary, external research notes, risks/edge cases, and suggested verification commands.",
                ],
                "depends_on": [],
                "status": "pending",
            }
        ],
    }

    _write_json(plan_file, payload)
    return ResearchPlanPaths(plan_dir=plan_dir, plan_file=plan_file, output_md=out_md)


def _detect_orchestrator() -> list[str]:
    # Prefer a repo-local orchestrator if the user copied `.plan-code-scripts/`.
    local = Path(__file__).resolve().parent / "codex-orchestrate"
    if local.is_file() and os.access(local, os.X_OK):
        return [str(local)]
    candidates = [
        Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser() / "bin" / "codex-orchestrate",
        Path("codex-orchestrate"),
    ]
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return [str(c)]
    return ["codex-orchestrate"]


def _run(cmd: list[str], *, cwd: Path) -> int:
    proc = subprocess.run(cmd, cwd=str(cwd), text=True, check=False)
    return int(proc.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a Codex research agent before planning.")
    parser.add_argument("--repo", default=".", help="Repo root (default: .)")
    parser.add_argument("--pr", required=True, help="PR name (used for .plans/<PR>/)")
    parser.add_argument("--goal", required=True, help="User goal/feature request")
    parser.add_argument(
        "--out",
        default=None,
        help="Path to write the research markdown (default: .plans/<PR>/research/RESEARCH.md)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Only write the preplan plan file; do not run Codex.")
    parser.add_argument("--ghostty", action="store_true", help="Run the research agent in a new Ghostty window.")
    parser.add_argument("--keep-open", action="store_true", help="Keep the Ghostty window open after exit.")
    parser.add_argument(
        "--run-root",
        default=os.environ.get("CODEX_RUN_ROOT") or str(Path("~/.codex/runs").expanduser()),
        help="Run artifacts root (default: ~/.codex/runs; can set CODEX_RUN_ROOT).",
    )
    parser.add_argument(
        "--sandbox",
        default="danger-full-access",
        choices=["read-only", "workspace-write", "danger-full-access"],
        help="Sandbox mode for spawned Codex session.",
    )
    parser.add_argument(
        "--approval",
        default="never",
        choices=["untrusted", "on-failure", "on-request", "never"],
        help="Approval policy for spawned Codex session.",
    )
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    out_md = Path(args.out).resolve() if args.out else None
    paths = create_research_plan(repo=repo, pr_name=args.pr, goal=args.goal, output_md=out_md)

    if args.dry_run:
        print(f"Wrote research plan file: {paths.plan_file}")
        print(f"Research output path: {paths.output_md}")
        return 0

    orch = _detect_orchestrator()
    cmd = [
        *orch,
        "--plan-dir",
        str(paths.plan_dir),
        "--plan-file",
        str(paths.plan_file),
        "--code-dir",
        str(repo),
        "--run-root",
        str(Path(args.run_root).expanduser()),
        "--no-reuse-run",
        "--compact-run",
        "--handshake",
        "results-json",
        "--ensure-git",
        "--baseline-commit",
        "--git-branch",
        f"pr/{_slugify(args.pr)}",
        "--sandbox",
        args.sandbox,
        "--approval",
        args.approval,
        "--max-tasks",
        "1",
    ]
    if args.ghostty:
        cmd += ["--ghostty"]
    if args.keep_open:
        cmd += ["--keep-open"]
    cmd += ["--", args.goal]

    return _run(cmd, cwd=repo)


if __name__ == "__main__":
    raise SystemExit(main())
