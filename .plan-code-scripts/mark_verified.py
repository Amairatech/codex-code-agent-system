#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ALLOWED_STATUSES = {"pending", "in_progress", "agent_done", "done", "deferred", "blocked"}


def _utc_now_rfc3339() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _slugify(name: str) -> str:
    s = name.strip()
    if not s:
        raise ValueError("Empty PR name")
    safe = []
    for ch in s:
        if ch.isalnum() or ch in ("-", "_", "/"):
            safe.append(ch)
        else:
            safe.append("-")
    out = "".join(safe).strip("-")
    return out or "pr"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _ensure_master(master: Path, pr: str) -> None:
    if master.exists():
        return
    master.parent.mkdir(parents=True, exist_ok=True)
    master.write_text(f"# {pr} — Planner Log\n\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Record manual verification for a task (updates MASTER.md + plan.json).")
    parser.add_argument("--repo", default=".", help="Repo root (default: .)")
    parser.add_argument("--pr", required=True, help="PR name (used for .plans/<PR>/)")
    parser.add_argument("--task", required=True, help="Task id (e.g. task_009)")
    parser.add_argument("--note", default="", help="Verification note (e.g. 'device ok')")
    parser.add_argument(
        "--status",
        default="done",
        choices=sorted(ALLOWED_STATUSES),
        help="Status to set on the task (default: done)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print intended edits; do not write files.")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    pr = _slugify(args.pr)
    task_id = str(args.task).strip()
    status = str(args.status).strip()
    if status not in ALLOWED_STATUSES:
        raise SystemExit(f"Invalid status: {status!r}")

    plan_path = repo / ".plans" / pr / "plan.json"
    master_path = repo / ".plans" / pr / "MASTER.md"
    if not plan_path.exists():
        raise SystemExit(f"Missing plan file: {plan_path}")

    payload = _read_json(plan_path)
    tasks = payload.get("tasks")
    if not isinstance(tasks, list):
        raise SystemExit(f"Invalid plan schema (missing tasks list): {plan_path}")

    found = False
    now = _utc_now_rfc3339()
    for t in tasks:
        if not isinstance(t, dict):
            continue
        tid = str(t.get("id") or t.get("task_id") or "")
        if tid != task_id:
            continue
        t["status"] = status
        t["verified_at"] = now
        t["verified_by"] = "manual"
        if args.note:
            t["verified_note"] = args.note
        found = True
        break
    if not found:
        raise SystemExit(f"Task not found in plan: {task_id}")

    entry = f"### {now} ({task_id} manual verification)\n- Result: set status `{status}`.\n"
    if args.note:
        entry += f"- Note: {args.note}\n"
    entry += "\n"

    if args.dry_run:
        print(f"Would update: {plan_path}")
        print(f"Would append: {master_path}")
        print(entry.rstrip())
        return 0

    _write_json(plan_path, payload)
    _ensure_master(master_path, pr)
    with master_path.open("a", encoding="utf-8") as fp:
        fp.write(entry)
    print(f"Updated {plan_path} and appended verification to {master_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

