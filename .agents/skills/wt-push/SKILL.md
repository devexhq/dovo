---
name: wt-push
description: >-
  Commit the current worktree-cli changes, then either push and refresh the
  description of the branch's existing open pull request, or open a new pull
  request against the branch's base. Runs no tests, linters, type checks, or
  other tooling. Invoked as /wt-push. Use when asked to commit, push, open a PR,
  or update a PR for the current branch.
---

# wt-push

Get the current branch's work committed and represented by an open PR. Nothing else.

## Hard boundaries

- **Never run tests or tooling.** No `inv test`, no `pytest`, no `ruff`, no `basedpyright`, no `inv complexity`, no `uv sync`. `/wt-code` already gated the change; re-running here is not this skill's job. If you believe the gates were never run, say so and let the human decide rather than running them.
- **Never write code.** No fixes, no formatting, no "while I'm here" edits. If the diff looks wrong, report it and stop.
- **Never commit to the default branch.** If `HEAD` is on it, update against origin, create a branch and continue.
- **Never stage `.agentic/`.** The plan and review are working artifacts, not deliverables. Step 2 deletes them outright, which is the second guarantee behind the `/.agentic/` ignore rule. Leave `.worktree/` and `scratch/` alone.
- **No AI attribution.** No `Co-authored-by:` trailers, no tool attribution lines, in commits or PR bodies.
- **Do not add reviewers** unless the user names them.

## Step 1: Read the state

```bash
git rev-parse --abbrev-ref HEAD
git status --porcelain
git diff --stat && git diff --staged --stat
git log --oneline @{upstream}..HEAD 2>/dev/null || git log --oneline -10
gh repo view --json defaultBranchRef
gh pr view --json number,state,baseRefName,headRefName,title,body,url 2>/dev/null
```

Branch names follow `<username>/<short-description>`. If the current branch does not, mention it once and carry on; do not rename a branch that already has commits.

## Step 2: Clear the agentic artifacts

Before deleting anything, capture the issue number for later use in the PR description. `/wt-plan` writes `.agentic/plan.md` with a `# Issue #<n>: <title>` header (see `docs/agents/planning.md`); read that header now, since it's the only place the number lives once the file is gone:

```bash
head -1 .agentic/plan.md 2>/dev/null
```

Then delete the handoff files **before staging anything**, so no path under `.agentic/` can reach the index even if the ignore rule is missing or a stale copy was tracked:

```bash
rm -f .agentic/plan.md .agentic/review.md .agentic/review.json
git ls-files .agentic
```

The plan and review have served their purpose by push time, and `/wt-plan` recreates both at the start of the next cycle. If `git ls-files .agentic` prints anything, the file was committed before the ignore rule landed: stop, report it, and let the human decide whether to untrack it (`git rm -r --cached .agentic`) rather than folding that into this change. 🚨

## Step 3: Commit

Review the actual diff before writing a message: the message describes what the change does, not what the plan said it would do.

- Stage deliberately (`git add <paths>`), never `git add -A` with an unread `git status`.
- Prefer **one commit per coherent concern**. If the tree holds unrelated concerns, say so and ask before lumping them together, since this repo requires one fix or feature per PR. 🚨
- Title: imperative, human-readable, semantic prefix (`feat(ui):`, `fix(config):`, `refactor(cli):`, `docs:`, `test:`). Body: what changed and why, wrapped prose, no file-by-file listing.
- Nothing to commit and nothing unpushed means the work is already published: report that and stop.

## Step 4: Push, or open the PR

**An open PR exists for this branch** (`gh pr view` returned one):

1. `git push` (add `--set-upstream origin <branch>` on first push of the branch).
2. Decide whether the description is stale by comparing the PR body against the full commit list, not just the new commits. It needs an update when the branch now does something the body does not mention, the approach described no longer matches the code, or the issue linkage is missing or wrong. Cosmetic rewording is not an update.
3. If stale, apply the refreshed body with `gh pr edit <number> --body-file <file>` and show the user what changed. Never overwrite sections you did not verify, such as a human-written checklist.

**No open PR** (`gh pr view` found none):

1. Resolve the base. Default to the repository's default branch. If this branch was cut from another feature branch (a stacked change), name the candidate you found and ask which base to target rather than guessing. 🚨
2. `git push --set-upstream origin <branch>`.
3. `gh pr create --base <base> --head <branch> --title <title> --body-file <file>`, then report the URL.

## PR description format

Three sections, intent-focused, per `docs/agents/git-and-pr-conventions.md`:

```markdown
## Why

<Context and motivation, 2-3 short sentences.>

## Approach

- <Key technical decision or implementation detail, 1-2 sentences.>
- <...>

Fixes #<n>
```

Use `Fixes #N` (or `Closes #N`) when the work resolves an issue, with `N` from the header captured in Step 2; omit the line entirely when Step 2 found no issue number rather than inventing a reference. Keep the title in the same imperative, semantic style as the commit titles.

Write the body to a temp file outside the repo (`mktemp`), never into `.agentic/` or the working tree, so nothing is left behind for the next `git status` to pick up.

## Report

State the commits created (short sha and title), whether you pushed to an existing PR or opened a new one, the PR URL, whether the description was updated or left as-is, and that the `.agentic/` handoff files were removed. Close by noting that no tests or tooling were run, so CI is the first gate this change will meet.
