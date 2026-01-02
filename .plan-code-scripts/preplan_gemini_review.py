#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_MODEL = "gemini-3-pro-preview"


def _utc_now_rfc3339() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _mkdirp(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _write_text(path: Path, content: str) -> None:
    _mkdirp(path.parent)
    path.write_text(content, encoding="utf-8")


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


def _detect_gemini() -> str:
    env = os.environ.get("GEMINI_BIN")
    if env:
        return env
    return "gemini"


def _default_out_md(*, repo: Path, pr: str) -> Path:
    return repo / ".plans" / _slugify(pr) / "preplan" / "GEMINI_PLAN_REVIEW.md"


def _read_optional(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _build_input_bundle(*, repo: Path, pr: str, plan_options: Path | None, extra_files: list[Path]) -> str:
    plan_dir = repo / ".plans" / _slugify(pr)
    candidates: list[Path] = []
    if plan_options:
        candidates.append(plan_options)
    candidates.extend(
        [
            plan_dir / "CONTEXT.md",
            plan_dir / "research" / "RESEARCH.md",
            plan_dir / "preplan" / "PLAN_OPTIONS.md",
        ]
    )
    candidates.extend(extra_files)

    parts: list[str] = []
    parts.append(f"# Gemini Plan Review Bundle\n\nGenerated: {_utc_now_rfc3339()}\n")
    parts.append(f"Repo: {repo}\nPR: {_slugify(pr)}\n\n")
    parts.append("## Included files\n")
    for p in candidates:
        rel = p.resolve()
        try:
            rel_display = str(rel.relative_to(repo))
        except Exception:
            rel_display = str(rel)
        parts.append(f"- {rel_display}\n")
    parts.append("\n---\n\n")

    for p in candidates:
        content = _read_optional(p)
        if not content.strip():
            continue
        try:
            title = str(p.resolve().relative_to(repo))
        except Exception:
            title = str(p)
        parts.append(f"\n\n# FILE: {title}\n\n")
        parts.append(content.rstrip() + "\n")
    return "".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description="Ask Gemini 3 Pro to critique plan options before delegation.")
    parser.add_argument("--repo", default=".", help="Repo root (default: .)")
    parser.add_argument("--pr", required=True, help="PR name (used for .plans/<PR>/)")
    parser.add_argument(
        "--plan-options",
        default=None,
        help="Path to plan options markdown (default: .plans/<PR>/preplan/PLAN_OPTIONS.md if present).",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Where to write Gemini's review (default: .plans/<PR>/preplan/GEMINI_PLAN_REVIEW.md).",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Gemini model to use (default: {DEFAULT_MODEL}). Use a *Pro* model, not Flash.",
    )
    parser.add_argument(
        "--extra",
        action="append",
        default=[],
        help="Extra file paths to include in the input bundle (can repeat).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Write a stub review file; do not call Gemini.")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    pr = _slugify(args.pr)
    model = str(args.model).strip()
    if not model.startswith("gemini-3-"):
        raise SystemExit(f"Refusing to run with non-Gemini-3 model: {model!r}")
    if "flash" in model.lower():
        raise SystemExit(f"Refusing to run with Flash model: {model!r}")
    if "pro" not in model.lower():
        raise SystemExit(f"Refusing to run with non-Pro model: {model!r}")
    out_path = Path(args.out).resolve() if args.out else _default_out_md(repo=repo, pr=pr)
    plan_options = Path(args.plan_options).resolve() if args.plan_options else None
    extra_files = [Path(p).resolve() for p in args.extra]

    if args.dry_run:
        _write_text(
            out_path,
            f"# Gemini Plan Review (DRY RUN)\n\n- model: `{model}`\n- generated_at: `{_utc_now_rfc3339()}`\n",
        )
        print(f"Wrote: {out_path}")
        return 0

    gemini = _detect_gemini()
    bundle = _build_input_bundle(repo=repo, pr=pr, plan_options=plan_options, extra_files=extra_files)

    prompt = (
        "You are a senior staff engineer reviewing a proposed implementation plan.\n"
        "Task:\n"
        "1) Read the included files (plan options, research brief, and context pack).\n"
        "2) Review the repo summary and suggest missing edge cases, risks, and verification commands.\n"
        "3) For each plan option, provide: strengths, gaps, risks, missing tasks, suggested verify commands, and a recommendation.\n"
        "Output:\n"
        "- Return a concise markdown review with headings:\n"
        "  - Summary\n"
        "  - Option A review\n"
        "  - Option B review\n"
        "  - Option C review\n"
        "  - Recommended verification contract (commands)\n"
        "Constraints:\n"
        "- Do not propose new features outside the stated goal.\n"
        "- Focus on correctness, testability, and maintainability.\n"
    )

    cmd = [
        gemini,
        "--output-format",
        "text",
        "-m",
        model,
        prompt,
    ]

    _mkdirp(out_path.parent)
    try:
        proc = subprocess.run(cmd, input=bundle, text=True, cwd=str(repo), capture_output=True, check=False)
    except FileNotFoundError:
        raise SystemExit(f"Gemini CLI not found: {gemini!r}")

    if proc.returncode != 0:
        msg = proc.stderr.strip() or proc.stdout.strip()
        raise SystemExit(f"Gemini failed (exit={proc.returncode}): {msg[:500]}")

    # Gemini sometimes prints credential/status lines; keep them but fence them for readability.
    content = proc.stdout.strip()
    _write_text(
        out_path,
        f"# Gemini Plan Review\n\n- model: `{model}`\n- generated_at: `{_utc_now_rfc3339()}`\n\n---\n\n{content}\n",
    )
    print(f"Wrote: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
