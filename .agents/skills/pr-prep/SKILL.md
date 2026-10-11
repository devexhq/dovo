---
name: pr-prep
description: >-
  Review the current uncommitted Dovo CLI changes and draft
  .agentic/commit-msg, .agentic/pr-title, and .agentic/pr-description for a
  human to run `uv run python scripts/pr.py` against to commit, push, and
  open (or reuse) the pull request. Invoked as /pr-prep. Use when asked to
  prep a commit and PR, or draft PR text from the working tree.
---

# pr-prep

Turn the current uncommitted diff into three drafted text files. That is the
entire scope of this skill — it never runs `scripts/pr.py` itself. Running
the script (which commits, pushes, and opens a PR) is a human's call to make
after reviewing the drafts.

## Step 1: Read the diff

```bash
git status --porcelain
git diff
git diff --staged
git rev-parse --abbrev-ref HEAD
```

If there is nothing uncommitted and nothing staged, say so and stop — there is
nothing to prep.

Read enough of the actual diff to describe it accurately. Do not draft from
file names alone.

## Step 2: Draft the handoff files

Create the directory if needed and write:

```bash
mkdir -p .agentic
```

- **`.agentic/commit-msg`** — full commit message: an imperative, semantic
  title line (`feat(ui):`, `fix(config):`, `refactor(cli):`, `docs:`,
  `test:`, ...), a blank line, then a short wrapped-prose body explaining what
  changed and why. No file-by-file listing.
- **`.agentic/pr-title`** — a single line, same imperative/semantic style as
  the commit title. Can match it exactly.
- **`.agentic/pr-description`** — the three-section format from
  `docs/agents/git-and-pr-conventions.md`:

  ```markdown
  ## Why

  <Context and motivation, 2-3 short sentences.>

  ## Approach

  - <Key technical decision or implementation detail, 1-2 sentences.>
  - <...>

  Fixes #<n>
  ```

  Omit the `Fixes #N` line entirely when there is no related issue — never
  invent one.

No AI attribution or tool co-author trailers in any of these three files, per
`docs/agents/git-and-pr-conventions.md`.

Show the drafted contents to the user once done.

## Step 3: Hand off to the human

Tell the user the drafts are ready and that running

```bash
uv run python scripts/pr.py
```

is theirs to do, not this skill's. Mention what it will do when they run it:
stage the uncommitted changes, commit with `.agentic/commit-msg`, create a
feature branch first if `HEAD` is still on the default branch, push, and then
create a new PR or report the already-open one for this branch — reading
`.agentic/pr-title` and `.agentic/pr-description` for the PR. On success it
deletes the three handoff files; on failure it leaves them in place so a
re-run doesn't require redrafting. Passing specific pathspecs
(`scripts/pr.py path/one path/two`) stages only those instead of everything
uncommitted.

Do not run it for them, even if asked to "finish the PR" in the same
breath — stop after the drafts and let them invoke it.

## Hard boundaries

- **Never run `scripts/pr.py` yourself, and never hand-run `git commit` /
  `git push` / `gh pr create` either.** This skill's output is three drafted
  files, nothing more. Committing, pushing, and opening the PR is a human
  action taken outside this skill.
- **Never run tests or tooling** (`dovo run tests`, `dovo run quality`, `ruff`,
  `basedpyright`). This skill packages what's already there; it doesn't gate it.
- **Never edit code** while drafting. If the diff looks wrong or mixes
  unrelated concerns, say so and ask before drafting, since this repo
  requires one fix or feature per PR.
- **Don't add reviewers** or PR labels unless the user names them.

## Report

State what was drafted (paraphrase, don't just repeat the files verbatim),
that `.agentic/commit-msg`, `.agentic/pr-title`, and `.agentic/pr-description`
are in place, and that running `uv run python scripts/pr.py` to commit, push,
and open the PR is left for the user to do.
