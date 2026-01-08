#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _tail(path: Path, *, max_lines: int = 80, max_bytes: int = 64_000) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    if len(data) > max_bytes:
        data = data[-max_bytes:]
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    return "\n".join(lines[-max_lines:])


def _extract_thread_id_from_log(path: Path) -> str | None:
    try:
        with path.open("rb") as fp:
            for raw in fp:
                if not raw.startswith(b"{"):
                    continue
                try:
                    obj = json.loads(raw.decode("utf-8", errors="replace"))
                except Exception:
                    continue
                if obj.get("type") == "thread.started" and isinstance(obj.get("thread_id"), str):
                    return str(obj["thread_id"])
    except OSError:
        return None
    return None


def _latest_run_dir(runs_root: Path) -> Path | None:
    if not runs_root.exists():
        return None
    dirs = sorted([p for p in runs_root.glob("*") if p.is_dir()])
    return dirs[-1] if dirs else None


def _kill_process_group(pid: int, *, grace_seconds: float = 4.0) -> None:
    try:
        os.killpg(pid, signal.SIGTERM)
    except Exception:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            return
    deadline = datetime.now().timestamp() + grace_seconds
    while datetime.now().timestamp() < deadline:
        try:
            os.kill(pid, 0)
        except Exception:
            return
    try:
        os.killpg(pid, signal.SIGKILL)
    except Exception:
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose a stuck task run (show PID, logs, and offer kill/rerun).")
    parser.add_argument("--repo", default=".", help="Repo root (default: .)")
    parser.add_argument("--pr", required=True, help="PR name (used for run lookup)")
    parser.add_argument(
        "--run-root",
        default=os.environ.get("CODEX_RUN_ROOT") or str((Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser() / "runs")),
        help="Run artifacts root (default: ${CODEX_HOME:-~/.codex}/runs; can set CODEX_RUN_ROOT).",
    )
    parser.add_argument("--run-dir", default=None, help="Explicit RUN_DIR to inspect (otherwise uses most recent).")
    parser.add_argument("--task", default=None, help="Task id (defaults to meta.json current_task_id)")
    parser.add_argument("--kill", action="store_true", help="Kill the agent PID/process group if available.")
    parser.add_argument("--grace-seconds", type=float, default=4.0, help="Grace period before SIGKILL (default: 4).")
    parser.add_argument("--print-rerun", action="store_true", help="Print a rerun command for this task.")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    pr = _slugify(args.pr)
    runs_root = Path(args.run_root).expanduser().resolve() / pr

    run_dir = Path(args.run_dir).expanduser().resolve() if args.run_dir else _latest_run_dir(runs_root)
    if not run_dir:
        raise SystemExit(f"No run dirs found under: {runs_root}")

    meta = _read_json(run_dir / "meta.json")
    control = run_dir / "control"
    pids = _read_json(control / "agent_pids.json")

    task_id = str(args.task or meta.get("current_task_id") or "").strip()
    if not task_id and isinstance(pids, dict) and pids:
        task_id = sorted(pids.keys())[-1]

    print(f"run_dir: {run_dir}")
    print(f"meta.status: {meta.get('status')}")
    print(f"meta.updated_at: {meta.get('updated_at')}")
    if task_id:
        print(f"task: {task_id}")

    pid = None
    if isinstance(pids, dict) and task_id and task_id in pids:
        try:
            pid = int(pids[task_id])
        except Exception:
            pid = None
    if pid is None and isinstance(meta.get("agent_pid"), int):
        pid = int(meta["agent_pid"])

    if pid is not None:
        print(f"agent_pid: {pid}")
        try:
            ps = subprocess.run(["ps", "-p", str(pid), "-o", "pid,ppid,stat,etime,command"], text=True, capture_output=True, check=False)
            print(ps.stdout.rstrip())
        except Exception:
            pass
    else:
        print("agent_pid: (not found)")

    results = run_dir / "results" / f"{task_id}.json" if task_id else None
    if results and results.exists():
        print(f"results: {results}")
    else:
        print("results: (missing)")

    log_path = meta.get("agent_log_path")
    if isinstance(log_path, str):
        log_file = Path(log_path)
        if not log_file.is_absolute():
            log_file = run_dir / log_file
    else:
        log_file = run_dir / f"agent_{task_id}.log" if task_id else None

    if log_file and log_file.exists():
        print(f"log: {log_file}")
        print("--- log tail ---")
        print(_tail(log_file))
        print("--- /log tail ---")
    else:
        print("log: (missing)")

    thread_id = None
    if isinstance(meta.get("thread_id"), str):
        thread_id = str(meta["thread_id"])
    elif log_file and log_file.exists():
        thread_id = _extract_thread_id_from_log(log_file)
    if thread_id:
        print(f"thread_id: {thread_id}")
    else:
        print("thread_id: (missing)")

    if args.kill:
        if pid is None:
            print("No PID available to kill.")
            return 2
        print(f"Killing PID {pid} (process group) at {_utc_now_rfc3339()} ...")
        _kill_process_group(pid, grace_seconds=float(args.grace_seconds))
        print("Done.")

    if args.print_rerun and task_id:
        plan_dir = repo / ".plans" / pr
        orch = repo / ".plan-code-scripts" / "codex-orchestrate"
        if not orch.exists():
            orch = Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser() / "bin" / "codex-orchestrate"
        cmd = (
            f"{orch} --plan-dir {plan_dir} --code-dir {repo} --run-root {Path(args.run_root).expanduser().resolve()} "
            f"--handshake results-json --compact-run --no-reuse-run --max-tasks 1 --task-id {task_id}"
        )
        print("\nrerun_command:")
        print(cmd)
        if thread_id:
            print("\nresume_command:")
            print(f"codex exec resume {thread_id} -")
            print("resume_prompt:")
            task_file = run_dir / "tasks" / f"{task_id}.md"
            results_path = run_dir / "results" / f"{task_id}.json"
            print(f"- Read: {task_file}")
            print(f"- Then ensure you write: {results_path} (include task_id + run_id if required by the task brief)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
