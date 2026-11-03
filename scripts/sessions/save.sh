#!/usr/bin/env bash
# Copy session SESSION_ID's stored records (session.json, definitions, diff),
# logs, step outputs and, while its worktree is still on disk, the worktree's
# .agentic/ directory into sessions/<SESSION_ID>/ of SESSION_DATA_REPO, then
# record the session against pull request SESSION_PR (of SESSION_PR_REPO) in
# ledger.md beside sessions/, then commit and push them to SESSION_DATA_BRANCH.
# Saving a session again replaces its folder and its ledger row; an unchanged
# session pushes nothing.
set -euo pipefail

if [[ ! "$SESSION_ID" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]]; then
  echo "Invalid session ID '$SESSION_ID'." >&2
  exit 1
fi

SESSION_PR="${SESSION_PR#\#}"
if [[ ! "$SESSION_PR" =~ ^[1-9][0-9]*$ ]]; then
  echo "Invalid pull request number '$SESSION_PR'." >&2
  exit 1
fi

# Confirm the session exists and read its blueprint and status for the commit message.
if ! show=$(uv run dovo session show "$SESSION_ID" --format json 2>/dev/null); then
  echo "Session '$SESSION_ID' not found; run 'dovo session list' to see saved sessions." >&2
  exit 1
fi
summary=$(python3 -c '
import json, sys
session = json.loads(sys.stdin.read())["payload"]["session"]
print(session["blueprint_name"], session["status"])
' <<<"$show")

project_id=$(python3 -c 'import json; print(json.load(open(".dovo/project.json"))["id"])')
storage="${DOVO_HOME:-$HOME/.dovo}/storage/projects/$project_id"
if [ ! -d "$storage/sessions/$SESSION_ID" ]; then
  echo "No stored records for session '$SESSION_ID' under $storage/sessions." >&2
  exit 1
fi

# A session run in a worktree shares its ID with the worktree; its .agentic/
# directory holds the run's handoff files and reports until the worktree is removed.
agentic=$(uv run dovo worktree show "$SESSION_ID" --format json 2>/dev/null | python3 -c '
import json, sys
payload = json.loads(sys.stdin.read())["payload"]
worktree = payload["worktree"]
if payload["disk_present"] and worktree:
    print(worktree["worktree_path"] + "/.agentic")
' || true)

clone=$(mktemp -d)
trap 'rm -rf "$clone"' EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
trap 'exit 129' HUP

if git ls-remote --exit-code --heads "$SESSION_DATA_REPO" "$SESSION_DATA_BRANCH" >/dev/null; then
  git clone --quiet --depth 1 --branch "$SESSION_DATA_BRANCH" "$SESSION_DATA_REPO" "$clone"
else
  # A new repository has no SESSION_DATA_BRANCH yet; the first save creates it.
  git clone --quiet "$SESSION_DATA_REPO" "$clone"
  git -C "$clone" checkout --quiet --orphan "$SESSION_DATA_BRANCH"
fi

# Copy the session into the clone, record it in ledger.md and stage both.
stage() {
  local target="$clone/sessions/$SESSION_ID"
  rm -rf "$target"
  mkdir -p "$target"
  cp -R "$storage/sessions/$SESSION_ID/." "$target/"
  if [ -d "$storage/logs/$SESSION_ID" ]; then cp -R "$storage/logs/$SESSION_ID" "$target/logs"; fi
  if [ -d "$storage/tmp/$SESSION_ID" ]; then cp -R "$storage/tmp/$SESSION_ID" "$target/outputs"; fi
  if [ -n "$agentic" ] && [ -d "$agentic" ]; then
    cp -R "$agentic" "$target/.agentic"
  fi
  record_ledger
  git -C "$clone" add -A "sessions/$SESSION_ID" ledger.md
}

# ledger.md links each saved session to its pull request: one row per session,
# replaced in place when the session is saved again, new sessions first.
record_ledger() {
  python3 - "$clone/ledger.md" "$SESSION_ID" "$SESSION_PR" "${SESSION_PR_REPO%/}" "$summary" <<'PY'
import os, sys

path, session_id, pr, pr_repo, summary = sys.argv[1:]
blueprint, _, status = summary.partition(" ")
header = [
    "# Ledger",
    "",
    "Each saved session and the pull request it belongs to.",
    "",
    "| PR | Session | Blueprint | Status |",
    "|---|---|---|---|",
]
rows = []
if os.path.exists(path):
    with open(path) as handle:
        lines = handle.read().splitlines()
    start = next((i + 1 for i, line in enumerate(lines) if line.startswith("|---")), len(lines))
    rows = [line for line in lines[start:] if line.startswith("|")]
key = f"[{session_id}](sessions/{session_id}/)"
row = f"| [#{pr}]({pr_repo}/pull/{pr}) | {key} | {blueprint} | {status} |"
saved = [i for i, line in enumerate(rows) if f"| {key} |" in line]
if saved:
    rows[saved[0]] = row
    rows = [line for i, line in enumerate(rows) if i not in saved[1:]]
else:
    rows.insert(0, row)
with open(path, "w") as handle:
    handle.write("\n".join([*header, *rows]) + "\n")
PY
}

if [ -z "$agentic" ] || [ ! -d "$agentic" ]; then
  echo "No .agentic/ directory on disk for session '$SESSION_ID'; saving without it."
fi

stage
if git -C "$clone" diff --cached --quiet; then
  echo "Session '$SESSION_ID' is already saved unchanged."
else
  message="chore(sessions): save $SESSION_ID for PR #$SESSION_PR ($summary)"
  git -C "$clone" commit --quiet -m "$message"
  # Another save may have pushed first, and both edit ledger.md: rebuild this
  # save on top of it once and retry rather than rebasing a conflicting row.
  if ! git -C "$clone" push --quiet origin "HEAD:$SESSION_DATA_BRANCH"; then
    git -C "$clone" fetch --quiet origin "$SESSION_DATA_BRANCH"
    git -C "$clone" reset --quiet --hard FETCH_HEAD
    stage
    git -C "$clone" commit --quiet -m "$message"
    git -C "$clone" push --quiet origin "HEAD:$SESSION_DATA_BRANCH"
  fi
  echo "Saved session '$SESSION_ID' for PR #$SESSION_PR to $SESSION_DATA_REPO (sessions/$SESSION_ID)."
fi

{
  echo "commit=$(git -C "$clone" rev-parse HEAD)"
  echo "path=sessions/$SESSION_ID"
  echo "ledger=ledger.md"
} >>"${DOVO_OUTPUT:-/dev/stdout}"
