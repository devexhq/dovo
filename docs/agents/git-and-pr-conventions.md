# Git and PR Conventions

Conventions for git commits, branches, and pull requests.

---

## Commit Messages

- **Semantic messages**: Use human-readable, imperative commit titles and descriptive bodies. Start the title with a semantic prefix, `type(scope): ...` or `type: ...` (e.g. `docs(rules): sync agent docs`).

---

## Branches and Merging

- **Branch naming**: `<username>/<short-description>` (e.g. `ljb/refactor-tests`, `user/fix-config-load`).
- **Merge strategy**: Merge commits (do not squash).

---

## PR Scoping

- **Single responsibility**: One fix or feature concern per PR.
- **Decompose large refactors**: Separate model/schema updates, caller migrations, and obsolete path deletions into clean sequential PRs when appropriate.
- **Avoid grab-bag PRs**: Independent bug fixes or findings should be separate PRs.

---

## PR Descriptions

Write intent-focused PR descriptions with three concise sections:
- **Why**: Context and motivation in 2-3 short sentences.
- **Approach**: Bulleted list of key technical decisions and implementation details (1-2 sentences per bullet).
- **Linkage**: Issue cross-reference (`Fixes #N` or `Closes #N`), or `Fixes: none (no tracking issue)` when no issue exists.

---

## GitHub Issues

For authoring and revising issues, follow the structure in [github-issues.md](github-issues.md) and [examples/github-issue.md](examples/github-issue.md).
