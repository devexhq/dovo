# `dovo worktree`

`dovo worktree` creates and manages isolated Git worktrees.

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

### `dovo worktree apply`

Apply changes from an isolated worktree back into the main workspace:

```bash
dovo worktree apply <worktree-id> [OPTIONS]
```

#### Options

| Flag | Description |
| --- | --- |
| `--strategy <patch\|squash>` | Strategy for applying changes: `patch` (default: uncommitted working tree changes) or `squash` (stages and creates a single Git commit). |
| `--allow-dirty` | Allow application even if the main workspace has uncommitted changes. |
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

Inspect differences between the worktree and its base commit:

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
