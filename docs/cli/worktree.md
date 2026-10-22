# `dovo worktree`

`dovo worktree` creates and manages isolated Git worktrees.

When `create`, `apply`, `diff` or `prune` fails with an unexpected error, or `delete` hits one while removing the worktree, Dovo writes a [diagnostic log](doctor.md#diagnostic-logs) record. The error or warning message then ends with a `Details logged: <path> (event <event_id>)` line naming that file; if the record cannot be written, the line is absent. A failed scan for stale branches during `prune` is recorded without a message, so no line points to it.

## Subcommands

### `dovo worktree list`

List all active worktrees associated with the current project repository:

```bash
dovo worktree list [--format terminal|json]
```

### `dovo worktree create`

Create a new isolated Git worktree:

```bash
dovo worktree create [--name <name>] [--base-ref <ref>] [--wip/--no-wip] [--format terminal|json]
```

### `dovo worktree show`

Inspect worktree details, path, branch, and status:

```bash
dovo worktree show <worktree-id> [--format terminal|json]
```

### `dovo worktree prune`

Safely prune stale worktrees, orphaned directories, and temporary branches:

```bash
dovo worktree prune [--dry-run] [--force] [--format terminal|json]
```

### `dovo worktree delete`

Remove an isolated worktree when work is complete:

```bash
dovo worktree delete <worktree-id> [--force] [--format terminal|json]
```

If removing the directory, deleting the branch, updating the local database or pruning git's worktree list fails, the worktree is still reported as deleted and each failure is listed as a warning.

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
