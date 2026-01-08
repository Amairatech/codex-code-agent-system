#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


ALLOWED_TASK_STATUSES = {"pending", "in_progress", "agent_done", "done", "deferred", "blocked"}


def _utc_now_rfc3339() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _mkdirp(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _write_text(path: Path, content: str) -> None:
    _mkdirp(path.parent)
    path.write_text(content, encoding="utf-8")


def _write_json(path: Path, payload: object) -> None:
    _mkdirp(path.parent)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _repo_root(cwd: Path) -> Path:
    # Treat current working directory as repo root for this lightweight tool.
    return cwd.resolve()


def _slugify(name: str) -> str:
    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    if not s:
        raise ValueError("Empty change name")
    return s


@dataclass(frozen=True)
class ParsedTask:
    raw: str
    text: str
    checked: bool


_TASK_LINE_RE = re.compile(r"^\s*-\s*\[(?P<mark>[ xX])\]\s*(?P<body>.+?)\s*$")


def _parse_tasks_md(tasks_md: str) -> list[ParsedTask]:
    tasks: list[ParsedTask] = []
    for line in tasks_md.splitlines():
        m = _TASK_LINE_RE.match(line)
        if not m:
            continue
        checked = m.group("mark").strip().lower() == "x"
        body = m.group("body").strip()
        tasks.append(ParsedTask(raw=line.rstrip("\n"), text=body, checked=checked))
    return tasks


def _default_project_md() -> str:
    return """# Project Context

## Overview
Describe what this project is, who uses it, and what “done” looks like.

## Tech stack
- Language(s):
- Framework(s):
- Build/test commands:

## Conventions
- Code style:
- Folder structure:
- Naming:

## Architecture constraints (invariants)
- (Example) No circular deps between packages A and B.
- (Example) All DB access goes through <module>.

## Verification
- Unit tests:
- Integration/E2E:
"""


def _default_proposal_md(change: str) -> str:
    return f"""# Change Proposal: {change}

## Summary
What is the feature/change?

## Motivation
Why are we doing this now?

## Scope
### In scope
- ...

### Out of scope
- ...

## Acceptance criteria
- ...

## Risks
- ...
"""


def _default_tasks_md() -> str:
    return """# Tasks

## 1. Implementation
- [ ] 1.1 First small step
- [ ] 1.2 Second small step

## 2. Verification
- [ ] 2.1 Add/adjust tests (if applicable)
- [ ] 2.2 Run build/tests and fix issues
"""


def _default_design_md() -> str:
    return """# Design Notes

## Decisions
- ...

## Alternatives considered
- ...
"""


def cmd_init(args: argparse.Namespace) -> int:
    root = _repo_root(Path(args.repo))
    openspec = root / "openspec"
    _mkdirp(openspec / "specs")
    _mkdirp(openspec / "changes")
    _mkdirp(openspec / "archive")
    if not (openspec / "project.md").exists() or args.force:
        _write_text(openspec / "project.md", _default_project_md())
    # Keep directories in git even if empty.
    for p in (openspec / "specs", openspec / "changes", openspec / "archive"):
        keep = p / ".gitkeep"
        if not keep.exists():
            _write_text(keep, "")
    return 0


def cmd_proposal(args: argparse.Namespace) -> int:
    root = _repo_root(Path(args.repo))
    change = _slugify(args.change)
    base = root / "openspec" / "changes" / change
    _mkdirp(base / "specs")
    if not (root / "openspec" / "project.md").exists():
        raise SystemExit("Missing openspec/project.md. Run: python3 .plan-code-scripts/specflow.py init")
    for name, content in (
        ("proposal.md", _default_proposal_md(change)),
        ("tasks.md", _default_tasks_md()),
        ("design.md", _default_design_md()),
    ):
        path = base / name
        if path.exists() and not args.force:
            continue
        _write_text(path, content)
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    root = _repo_root(Path(args.repo))
    change = _slugify(args.change)
    base = root / "openspec" / "changes" / change
    missing: list[str] = []
    for required in ("proposal.md", "tasks.md"):
        if not (base / required).exists():
            missing.append(str(base / required))
    if missing:
        raise SystemExit("Missing required files:\n" + "\n".join(f"- {p}" for p in missing))
    tasks = _parse_tasks_md((base / "tasks.md").read_text(encoding="utf-8"))
    if not tasks:
        raise SystemExit(f"No tasks found in {base / 'tasks.md'} (expected '- [ ] ...' lines).")
    return 0


def _load_verify_overrides(path: Path | None) -> dict[str, object]:
    if not path:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return {}
    except json.JSONDecodeError as e:
        raise SystemExit(f"Invalid JSON in overrides file {path}: {e}")
    if not isinstance(data, dict):
        raise SystemExit(f"Overrides file must be a JSON object: {path}")
    return data


def _apply_task_override(task: dict[str, object], override: dict[str, object]) -> None:
    for key in ("verify", "expected_files", "done_criteria", "title", "description"):
        if key in override:
            task[key] = override[key]


def _plan_goal_from_proposal(proposal_md: str, fallback: str) -> str:
    for line in proposal_md.splitlines():
        if line.startswith("# "):
            return line.removeprefix("# ").strip() or fallback
    return fallback


def _detect_package_manager(root: Path) -> str:
    if (root / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (root / "yarn.lock").exists():
        return "yarn"
    return "npm"


def _read_package_scripts(root: Path) -> dict[str, str]:
    pkg = root / "package.json"
    if not pkg.exists():
        return {}
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
    except Exception:
        return {}
    scripts = data.get("scripts")
    if not isinstance(scripts, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in scripts.items():
        if isinstance(k, str) and isinstance(v, str):
            out[k] = v
    return out


def _find_single_xcodeproj(root: Path, *, max_depth: int = 2) -> Path | None:
    # Keep cheap and deterministic: only search a couple levels.
    candidates: list[Path] = []
    for p in root.rglob("*.xcodeproj"):
        try:
            rel = p.relative_to(root)
        except Exception:
            continue
        if len(rel.parts) - 1 > max_depth:
            continue
        candidates.append(p)
    if len(candidates) == 1:
        return candidates[0]
    return None


def _suggest_verify_commands(root: Path) -> list[str] | None:
    cmds: list[str] = []

    xcodeproj = _find_single_xcodeproj(root)
    if xcodeproj is not None:
        cmds.append(f"xcodebuild -list -project {xcodeproj.relative_to(root)}")

    scripts = _read_package_scripts(root)
    if scripts:
        pm = _detect_package_manager(root)
        if "typecheck" in scripts:
            cmds.append(f"{pm} -s typecheck" if pm != "npm" else "npm run -s typecheck")
        elif "test" in scripts:
            cmds.append(f"{pm} -s test" if pm != "npm" else "npm test")

    # Keep it to 1-3 commands.
    cmds = [c for c in cmds if c.strip()]
    if not cmds:
        return None
    return cmds[:3]


def cmd_plan(args: argparse.Namespace) -> int:
    root = _repo_root(Path(args.repo))
    change = _slugify(args.change)
    pr = _slugify(args.pr or change)
    change_dir = root / "openspec" / "changes" / change
    if not change_dir.exists():
        raise SystemExit(f"Missing change folder: {change_dir} (run proposal first).")

    tasks_md_path = change_dir / "tasks.md"
    proposal_md_path = change_dir / "proposal.md"
    if not tasks_md_path.exists() or not proposal_md_path.exists():
        raise SystemExit("Missing tasks/proposal. Run: python3 .plan-code-scripts/specflow.py proposal <change>")

    tasks = _parse_tasks_md(tasks_md_path.read_text(encoding="utf-8"))
    if not tasks:
        raise SystemExit(f"No tasks found in {tasks_md_path} (expected '- [ ] ...' lines).")

    goal = _plan_goal_from_proposal(
        proposal_md_path.read_text(encoding="utf-8"),
        fallback=f"Implement OpenSpec change: {change}",
    )

    plan_dir = root / ".plans" / pr
    _mkdirp(plan_dir)

    overrides_path = Path(args.verify_overrides).expanduser().resolve() if args.verify_overrides else None
    overrides = _load_verify_overrides(overrides_path)
    default_verify = overrides.get("default_verify")
    if default_verify is not None and not isinstance(default_verify, list):
        raise SystemExit("verify_overrides.json: default_verify must be a list of strings")
    auto_verify = _suggest_verify_commands(root)

    context_md = f"""# Architect Context Pack ({pr})

## Source of truth
- Project conventions: `openspec/project.md`
- Approved proposal: `openspec/changes/{change}/proposal.md`
- Task list: `openspec/changes/{change}/tasks.md`
- Design notes (optional): `openspec/changes/{change}/design.md`
- Spec deltas (optional): `openspec/changes/{change}/specs/`

## Invariants (fill in)
- ...

## Entrypoints (fill in)
- ...

## Verification commands (fill in)
- ...
"""
    context_path = plan_dir / "CONTEXT.md"
    if not context_path.exists() or args.force:
        _write_text(context_path, context_md)

    plan_tasks: list[dict[str, object]] = []
    for idx, t in enumerate(tasks, start=1):
        task_id = f"task_{idx:03d}"
        verify = ["<fill: 1-3 concrete commands the planner will run>"]
        if isinstance(default_verify, list) and all(isinstance(x, str) for x in default_verify) and default_verify:
            verify = list(default_verify)
        elif auto_verify:
            verify = list(auto_verify)
        plan_tasks.append(
            {
                "id": task_id,
                "title": t.text,
                "description": (
                    f"Implement this OpenSpec task for change `{change}`:\n\n"
                    f"- {t.raw}\n\n"
                    "Follow `openspec/project.md` + `.plans/<PR>/CONTEXT.md`.\n"
                    "Do not implement other tasks."
                ),
                "expected_files": [],
                "verify": verify,
                "done_criteria": [
                    "Implementation matches the requirement implied by the task line.",
                    "Relevant tests/builds (if any) pass.",
                ],
                "depends_on": [],
                "status": "pending",
            }
        )

    # Apply overrides by task id or exact title.
    by_id = overrides.get("by_task_id")
    if isinstance(by_id, dict):
        for task in plan_tasks:
            tid = str(task.get("id") or "")
            ov = by_id.get(tid)
            if isinstance(ov, dict):
                _apply_task_override(task, ov)
    by_title = overrides.get("by_title")
    if isinstance(by_title, dict):
        for task in plan_tasks:
            title = str(task.get("title") or "")
            ov = by_title.get(title)
            if isinstance(ov, dict):
                _apply_task_override(task, ov)

    payload: dict[str, object] = {
        "goal": goal,
        "created_at": _utc_now_rfc3339(),
        "openspec": {
            "change": change,
            "proposal": str(proposal_md_path.relative_to(root)),
            "tasks": str(tasks_md_path.relative_to(root)),
            "spec_deltas_dir": str((change_dir / "specs").relative_to(root)),
        },
        "tasks": plan_tasks,
    }

    plan_path = plan_dir / "plan.json"
    if plan_path.exists() and not args.force:
        raise SystemExit(f"Refusing to overwrite existing plan: {plan_path} (use --force).")
    _write_json(plan_path, payload)

    master_path = plan_dir / "MASTER.md"
    stamp = _utc_now_rfc3339()
    master_entry = (
        f"## {stamp}\n"
        f"- Initialized plan from OpenSpec change: `openspec/changes/{change}/`\n"
        f"- Plan file: `.plans/{pr}/plan.json`\n"
        f"- Next: delegate `task_001` and verify.\n\n"
    )
    if not master_path.exists():
        _write_text(master_path, f"# {pr} — Planner Log\n\n{master_entry}")
    else:
        with master_path.open("a", encoding="utf-8") as fp:
            fp.write(master_entry)

    return 0


def cmd_archive(args: argparse.Namespace) -> int:
    root = _repo_root(Path(args.repo))
    change = _slugify(args.change)
    change_dir = root / "openspec" / "changes" / change
    if not change_dir.exists():
        raise SystemExit(f"Missing change folder: {change_dir}")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = root / "openspec" / "archive" / f"{change}-{stamp}"
    _mkdirp(dest.parent)
    shutil.move(str(change_dir), str(dest))
    return 0


def cmd_validate_plan(args: argparse.Namespace) -> int:
    root = _repo_root(Path(args.repo))
    pr = _slugify(args.pr)
    plan_path = root / ".plans" / pr / "plan.json"
    if not plan_path.exists():
        raise SystemExit(f"Missing plan file: {plan_path}")
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    tasks = payload.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise SystemExit(f"Invalid plan schema (missing tasks list): {plan_path}")
    for i, t in enumerate(tasks, start=1):
        if not isinstance(t, dict):
            raise SystemExit(f"Invalid task at index {i}: expected object")
        tid = str(t.get("id") or t.get("task_id") or "").strip()
        if not tid:
            raise SystemExit(f"Task at index {i} missing id/task_id")
        status = str(t.get("status") or "pending")
        if status not in ALLOWED_TASK_STATUSES:
            raise SystemExit(f"Task {tid} has invalid status: {status!r} (allowed: {sorted(ALLOWED_TASK_STATUSES)})")
        verify = t.get("verify")
        if not isinstance(verify, list):
            raise SystemExit(f"Task {tid} verify must be a list")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenSpec-inspired spec/task scaffolding for Codex planner workflows.")
    parser.add_argument("--repo", default=".", help="Repo root (default: .)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="Initialize openspec/ structure and templates.")
    p_init.add_argument("--force", action="store_true", help="Overwrite openspec/project.md if it exists.")
    p_init.set_defaults(func=cmd_init)

    p_prop = sub.add_parser("proposal", help="Create an OpenSpec-style change proposal folder.")
    p_prop.add_argument("change", help="Change name (slug).")
    p_prop.add_argument("--force", action="store_true", help="Overwrite proposal/tasks/design files if they exist.")
    p_prop.set_defaults(func=cmd_proposal)

    p_val = sub.add_parser("validate", help="Validate a change folder has required files and tasks.")
    p_val.add_argument("change", help="Change name (slug).")
    p_val.set_defaults(func=cmd_validate)

    p_plan = sub.add_parser("plan", help="Generate .plans/<PR>/plan.json from openspec/changes/<change>/tasks.md.")
    p_plan.add_argument("change", help="Change name (slug).")
    p_plan.add_argument("--pr", default=None, help="PR name (defaults to change).")
    p_plan.add_argument("--force", action="store_true", help="Overwrite .plans/<PR> artifacts if they exist.")
    p_plan.add_argument(
        "--verify-overrides",
        default=None,
        help="Path to verify_overrides.json (supports default_verify, by_task_id, by_title).",
    )
    p_plan.set_defaults(func=cmd_plan)

    p_vp = sub.add_parser("validate-plan", help="Validate .plans/<PR>/plan.json schema/status values.")
    p_vp.add_argument("--pr", required=True, help="PR name")
    p_vp.set_defaults(func=cmd_validate_plan)

    p_arch = sub.add_parser("archive", help="Move an OpenSpec change folder into openspec/archive/ with a timestamp.")
    p_arch.add_argument("change", help="Change name (slug).")
    p_arch.set_defaults(func=cmd_archive)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
