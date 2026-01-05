#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
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


def _parse_rfc3339(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


@dataclass(frozen=True)
class RunResult:
    task_id: str
    status: str
    run_dir: Path
    created_at: datetime
    run_id: str


def _collect_results(*, runs_root: Path) -> list[RunResult]:
    results: list[RunResult] = []
    for run_dir in sorted([p for p in runs_root.glob("*") if p.is_dir()]):
        meta = run_dir / "meta.json"
        created_at = None
        run_id = ""
        if meta.exists():
            try:
                meta_payload = _read_json(meta)
                created_at = _parse_rfc3339(str(meta_payload.get("created_at") or ""))
                run_id = str(meta_payload.get("invocation_run_id") or "")
            except Exception:
                created_at = None
        if created_at is None:
            created_at = datetime.fromtimestamp(run_dir.stat().st_mtime, tz=timezone.utc)
        res_dir = run_dir / "results"
        if not res_dir.exists():
            continue
        for res_file in res_dir.glob("task_*.json"):
            try:
                payload = _read_json(res_file)
            except Exception:
                continue
            task_id = str(payload.get("task_id") or payload.get("task") or payload.get("id") or "").strip()
            if not task_id:
                continue
            status = str(payload.get("status") or payload.get("state") or "").strip().lower() or "agent_done"
            results.append(RunResult(task_id=task_id, status=status, run_dir=run_dir, created_at=created_at, run_id=run_id))
    results.sort(key=lambda r: r.created_at)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync .plans/<PR>/plan.json status fields from run results JSON.")
    parser.add_argument("--repo", default=".", help="Repo root (default: .)")
    parser.add_argument("--pr", required=True, help="PR name (used for .plans/<PR>/)")
    parser.add_argument(
        "--run-root",
        default=os.environ.get("CODEX_RUN_ROOT") or str((Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser() / "runs")),
        help="Run artifacts root (default: ${CODEX_HOME:-~/.codex}/runs; can set CODEX_RUN_ROOT).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print changes only; do not write plan.json.")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    pr = _slugify(args.pr)
    plan_path = repo / ".plans" / pr / "plan.json"
    if not plan_path.exists():
        raise SystemExit(f"Missing plan file: {plan_path}")

    runs_root = Path(args.run_root).expanduser().resolve() / pr
    if not runs_root.exists():
        print(f"No runs found at: {runs_root} (nothing to sync)")
        return 0

    payload = _read_json(plan_path)
    tasks = payload.get("tasks")
    if not isinstance(tasks, list):
        raise SystemExit(f"Invalid plan schema (missing tasks list): {plan_path}")

    by_id: dict[str, dict[str, Any]] = {}
    for t in tasks:
        if not isinstance(t, dict):
            continue
        tid = str(t.get("id") or t.get("task_id") or "").strip()
        if tid:
            by_id[tid] = t

    latest: dict[str, RunResult] = {}
    for rr in _collect_results(runs_root=runs_root):
        latest[rr.task_id] = rr

    changed = 0
    for task_id, rr in latest.items():
        t = by_id.get(task_id)
        if not t:
            continue
        current = str(t.get("status") or "pending")
        if current not in ALLOWED_STATUSES:
            t["status"] = "blocked"
            t["blocked_reason"] = f"invalid_status:{current}"
            t["blocked_at"] = _utc_now_rfc3339()
            changed += 1
            continue
        if current in ("done", "deferred"):
            continue
        t["run_dir"] = str(rr.run_dir)
        if not t.get("delegated_at"):
            t["delegated_at"] = rr.created_at.isoformat(timespec="seconds")
        t["last_run_id"] = rr.run_id

        s = rr.status.lower()
        if s in ("partial", "timeout", "failed", "error"):
            if current != "blocked":
                t["status"] = "blocked"
                t["blocked_reason"] = s
                t["blocked_at"] = _utc_now_rfc3339()
                changed += 1
        else:
            if current in ("pending", "in_progress"):
                t["status"] = "agent_done"
                t["agent_done_at"] = _utc_now_rfc3339()
                changed += 1

    if args.dry_run:
        print(f"Would update: {plan_path} (changed_tasks={changed})")
        return 0

    if changed:
        _write_json(plan_path, payload)
        print(f"Updated {plan_path} (changed_tasks={changed})")
    else:
        print("No changes needed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

