#!/usr/bin/env bash
# Copy session SESSION_ID's stored records (session.json, definitions, diff),
# logs, step outputs and, while its worktree is still on disk, the worktree's
# .agentic/ directory into sessions/<SESSION_ID>/ of SESSION_DATA_REPO, then
# commit and push them to SESSION_DATA_BRANCH. Saving a session again replaces
# its folder; an unchanged session pushes nothing.
set -euo pipefail

if [[ ! "$SESSION_ID" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]]; then
  echo "Invalid session ID '$SESSION_ID'." >&2
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

target="$clone/sessions/$SESSION_ID"
rm -rf "$target"
mkdir -p "$target"
cp -R "$storage/sessions/$SESSION_ID/." "$target/"
[ -d "$storage/logs/$SESSION_ID" ] && cp -R "$storage/logs/$SESSION_ID" "$target/logs"
[ -d "$storage/tmp/$SESSION_ID" ] && cp -R "$storage/tmp/$SESSION_ID" "$target/outputs"
if [ -n "$agentic" ] && [ -d "$agentic" ]; then
  cp -R "$agentic" "$target/.agentic"
else
  echo "No .agentic/ directory on disk for session '$SESSION_ID'; saving without it."
fi

git -C "$clone" add -A "sessions/$SESSION_ID"
if git -C "$clone" diff --cached --quiet; then
  echo "Session '$SESSION_ID' is already saved unchanged."
else
  git -C "$clone" commit --quiet -m "chore(sessions): save $SESSION_ID ($summary)"
  # Another save may have pushed first: rebase onto it once and retry.
  if ! git -C "$clone" push --quiet origin "HEAD:$SESSION_DATA_BRANCH"; then
    git -C "$clone" pull --quiet --rebase origin "$SESSION_DATA_BRANCH"
    git -C "$clone" push --quiet origin "HEAD:$SESSION_DATA_BRANCH"
  fi
  echo "Saved session '$SESSION_ID' to $SESSION_DATA_REPO (sessions/$SESSION_ID)."
fi

{
  echo "commit=$(git -C "$clone" rev-parse HEAD)"
  echo "path=sessions/$SESSION_ID"
} >>"${DOVO_OUTPUT:-/dev/stdout}"
