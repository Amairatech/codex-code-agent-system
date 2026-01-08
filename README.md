# Codex Planner Pipeline (Spec-first + Pre-plan Agents)

A file-driven workflow for running long, complex PRs without relying on chat history:

- A **planner/architect** owns the plan and progress (`.plans/<PR>/...`)
- Small, bounded **code agents** implement tasks one-at-a-time (or parallel by domain)
- A **research agent** writes pre-plan research notes to markdown
- A **Gemini 3 Pro** plan reviewer critiques the plan options before you commit to one

This repo is designed to be copied into *any* project as `.plan-code-scripts/`.

## Prerequisites

- Codex CLI (`codex`) installed and authenticated
- Python 3
- (Optional) Gemini CLI (`gemini`) installed and authenticated

## Install into your project

Clone this repo, then copy `.plan-code-scripts/` into the repo you want to work on:

```bash
git clone https://github.com/<you>/codex-code-agent-system
cp -R codex-code-agent-system/.plan-code-scripts /path/to/your-repo/.plan-code-scripts
```

Optional: add the orchestrator to PATH (so you can run `codex-orchestrate` anywhere):

```bash
sudo ln -sf "/path/to/your-repo/.plan-code-scripts/codex-orchestrate" /usr/local/bin/codex-orchestrate
```

## Workflow (high level)

### 0) Baseline (only if repo has no git history)

Use the orchestrator with `--baseline-commit` so the first commit isn’t “the whole repo”.

### 1) Spec-first scaffolding (recommended)

```bash
cd /path/to/your-repo
python3 .plan-code-scripts/specflow.py init
CHANGE="<change-slug>"
python3 .plan-code-scripts/specflow.py proposal "${CHANGE}"
python3 .plan-code-scripts/specflow.py validate "${CHANGE}"
python3 .plan-code-scripts/specflow.py plan "${CHANGE}" --pr "<PR_NAME>"
```

### 2) Pre-plan research agent (Codex)

```bash
python3 .plan-code-scripts/preplan_research.py --repo . --pr "<PR_NAME>" --goal "<GOAL>" --timeout-minutes 45
```

Expected output:
- `.plans/<PR_NAME>/research/RESEARCH.md`

### 3) Draft plan options (A/B/C)

Create/edit:
- `.plans/<PR_NAME>/preplan/PLAN_OPTIONS.md`

### 4) Plan critique (Gemini 3 Pro)

```bash
python3 .plan-code-scripts/preplan_gemini_review.py --repo . --pr "<PR_NAME>" --model "gemini-3-pro-preview"
```

Expected output:
- `.plans/<PR_NAME>/preplan/GEMINI_PLAN_REVIEW.md`

The reviewer script refuses non–Gemini-3 models and refuses Flash models.

### 5) Delegate one task (Codex code agent)

Run the orchestrator against `.plans/<PR_NAME>/plan.json`:

```bash
PR_NAME="<PR_NAME>"
RUN_ROOT="${CODEX_HOME:-$HOME/.codex}/runs"
mkdir -p ".plans/${PR_NAME}" "${RUN_ROOT}"
ORCH="./.plan-code-scripts/codex-orchestrate"
${ORCH} --plan-dir "$(pwd)/.plans/${PR_NAME}" --code-dir "$(pwd)" \
  --run-root "${RUN_ROOT}" --no-reuse-run --compact-run --handshake results-json \
  --ensure-git --baseline-commit --git-branch "pr/${PR_NAME}" \
  --sandbox danger-full-access --approval never --max-tasks 1
```

Planner then:
- verifies the changes with the task’s `verify` commands
- marks the task `done` in `.plans/<PR_NAME>/plan.json`
- commits with a `task_00X:`-style message

### 6) Record verification + sync plan state (optional)

If you verified something manually (device/simulator/etc.), record it in files:

```bash
python3 .plan-code-scripts/mark_verified.py --repo . --pr "<PR_NAME>" --task task_009 --note "device ok"
```

If your plan drifted (results exist in run folders but plan.json didn’t update), sync from run artifacts:

```bash
python3 .plan-code-scripts/sync_plan.py --repo . --pr "<PR_NAME>" --run-root "${CODEX_HOME:-$HOME/.codex}/runs"
```

### Run metadata (debugging)

Each orchestrator run writes:
- `RUN_DIR/meta.json` (status + timestamps + task/log pointers)
- `RUN_DIR/control/heartbeat.json` (updated periodically while waiting for results)

### Stuck task helper (optional)

Show the latest run’s PID + log tail, and print a rerun command:

```bash
python3 .plan-code-scripts/task_doctor.py --repo . --pr "<PR_NAME>" --print-rerun
```

## What to copy into your repo

Copy `.plan-code-scripts/`:
- `PLANNER_MODE_SYSTEM_PROMPT.md` (planner instructions)
- `specflow.py` (spec-first scaffolding)
- `preplan_research.py` (Codex research agent runner)
- `preplan_gemini_review.py` (Gemini 3 Pro plan critique)
- `codex-orchestrate` (spawns Codex agent sessions + run artifacts + handshake)

## Security notes

- `--sandbox danger-full-access --approval never` is intentionally high-trust. Only use it in environments you control.
- Don’t include secrets in agent briefs or planning artifacts; assume run directories may contain logs.
- If you run multiple planner sessions across projects, avoid `killall`/`pkill` (especially against `codex`). It can terminate every session at once. Prefer killing only specific PIDs you started.

## License

MIT — see `LICENSE`.
