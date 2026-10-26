# `dovo worktree`

`dovo worktree` creates and manages isolated Git worktrees.

When `create` fails with an unexpected error or an operating-system error, or cannot save the worktree's metadata, or a step of removing the worktree hits an unexpected or operating-system error during `delete`, Dovo writes a [diagnostic log](doctor.md#diagnostic-logs) record, prints a `Details logged: <path> (event <event_id>)` line after the output, and lists the record in `diagnostics` with `--format json`; the error or warning message is unchanged. When `apply` or `diff` fails with an unexpected error, the error or warning message instead ends with that line. If the record cannot be written, the line is absent.

## Subcommands

### `dovo worktree list`

List all active worktrees associated with the current project repository:

```bash
dovo worktree list [--format terminal|json]
```

An unexpected error reports `error_code` `INTERNAL_ERROR`, exits with code 1, and writes a [diagnostic log](doctor.md#diagnostic-logs) record: a `Details logged: <path> (event <event_id>)` line prints after the output, and with `--format json` the record is listed in `diagnostics`.

### `dovo worktree create`

Create a new isolated Git worktree:

```bash
dovo worktree create [--name <name>] [--base-ref <ref>] [--wip/--no-wip] [--format terminal|json]
```

A failure exits with code 1 and reports its `error_code`: `WORKTREE_PATH_CONFLICT` when the worktree directory or branch already exists, `WORKTREE_CAPACITY_EXCEEDED` when `worktree.max_active_worktrees` worktrees are active, `WORKTREE_CREATION_FAILED` when the target path cannot be checked, the git failure's code (`GIT_FAILED`, `GIT_BINARY_MISSING` or `GIT_TIMEOUT`) when a git command fails, including while `--wip` overlays your uncommitted changes, `FS_NOT_FOUND` when the working directory disappeared while git ran, `WORKTREE_WIP_FAILED` or `WORKTREE_MISSING` when `--wip` otherwise cannot overlay your uncommitted changes, and `INTERNAL_ERROR` for an unexpected error. A partly created worktree is removed first.

### `dovo worktree show`

Inspect worktree details, path, branch, and status:

```bash
dovo worktree show <worktree-id> [--format terminal|json]
```

An unknown id reports `error_code` `WORKTREE_NOT_FOUND` and exits with code 1. An unexpected error reports `INTERNAL_ERROR`, also exits with code 1, and writes a [diagnostic log](doctor.md#diagnostic-logs) record, listed in `diagnostics` with `--format json`.

### `dovo worktree prune`

Safely prune stale worktrees, orphaned directories, and temporary branches:

```bash
dovo worktree prune [--dry-run] [--force] [--format terminal|json]
```

With `--format json`, `status` is `ok`, or `partial_failure` when at least one item could not be pruned; that run reports `error_code` `WORKTREE_PRUNE_ITEMS_FAILED` and lists one error per failed item. A failure that stops the scan reports `status` `null` and its `error_code`, for example `GIT_FAILED` when `git worktree list` fails, or `INTERNAL_ERROR` for an unexpected error. Either exits with code 1.

When an unexpected error stops prune, or an item cannot be pruned, Dovo writes a [diagnostic log](doctor.md#diagnostic-logs) record and prints a `Details logged: <path> (event <event_id>)` line after the output; with `--format json` the record is listed in `diagnostics`. Without `--dry-run`, prune waits for the workspace lock and exits with code 1 if another `dovo` process holds it too long.

### `dovo worktree delete`

Remove an isolated worktree when work is complete:

```bash
dovo worktree delete <worktree-id> [--force] [--format terminal|json]
```

If removing the directory, deleting the branch, updating the local database or pruning git's worktree list fails, the worktree is still reported as deleted and each failure is listed as a warning.

Declining the confirmation prints `Deletion cancelled.`, removes nothing, and exits with code 0; with `--format json` the result has `cancelled: true`. Deleting a worktree that is already cleaned asks nothing and reports `already_cleaned: true`, also with exit code 0. An unknown id reports `error_code` `WORKTREE_NOT_FOUND` and exits with code 1, and an unexpected error reports `INTERNAL_ERROR`.

This is also how a failed run's kept worktree is removed (see [`dovo run`](run.md#worktree-retention)). Kept worktrees count toward `worktree.max_active_worktrees`, and `dovo worktree prune` does not remove them.

### `dovo worktree apply`

Apply changes from an isolated worktree back into the main workspace:

```bash
dovo worktree apply <worktree-id> [OPTIONS]
```

#### Options

| Flag | Description |
| --- | --- |
| `--strategy <patch\|squash>` | Strategy for applying changes: `patch` (default: uncommitted working tree changes) or `squash` (creates a single Git commit containing only the paths the worktree changed). |
| `--allow-dirty` | Allow application even if the main workspace has uncommitted changes. With `--strategy squash`, those changes stay uncommitted and keep their staged, unstaged or untracked state, and the apply is refused if any of them is in a file the worktree also changes. |
| `--dry-run` | Perform pre-apply conflict checks without modifying the workspace. |
| `--delete`, `-d` | Delete the worktree and branch upon successful application. |
| `--message <msg>`, `-m <msg>` | Custom commit message when using `--strategy squash`. |
| `--format <terminal\|json>` | Presentation format (`terminal` or `json`). |

With `--delete`, a failed removal step is listed as a warning and its record is listed in `diagnostics`.

#### Examples

Apply changes as working tree modifications:
```bash
dovo worktree apply dovo_8f2a1b9c
```

Apply and squash into a commit, then remove the worktree:
```bash
dovo worktree apply dovo_8f2a1b9c --strategy squash --message "feat: implement auth service" --delete
```

### `dovo worktree diff`

Inspect differences between the worktree and its base commit. The diff covers commits on the worktree branch, staged, unstaged and deleted files, and untracked files not matched by `.gitignore`; computing it does not change the worktree's index or `git status`:

```bash
dovo worktree diff <worktree-id> [OPTIONS]
```

#### Options

| Flag | Description |
| --- | --- |
| `--stat` | Output summary diffstat statistics (files changed, insertions, deletions) instead of the full unified diff. |
| `--format <terminal\|json>` | Presentation format (`terminal` or `json`). |

#### Examples

View unified diff:
```bash
dovo worktree diff dovo_8f2a1b9c
```

View diffstat summary:
```bash
dovo worktree diff dovo_8f2a1b9c --stat
```
