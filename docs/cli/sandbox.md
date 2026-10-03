# `dovo sandbox`

The `dovo sandbox` command provisions and manages isolated Git worktrees to allow AI agents or human developers to run experiments safely.

## Subcommands

### `dovo sandbox list`

List all active sandboxes associated with the current project repository:

```bash
dovo sandbox list [--format terminal|json]
```

### `dovo sandbox create`

Create a new isolated Git worktree:

```bash
dovo sandbox create [--name <name>] [--base-ref <ref>] [--wip/--no-wip] [--format terminal|json]
```

### `dovo sandbox show`

Inspect sandbox details, path, branch, and status:

```bash
dovo sandbox show <sandbox-id> [--format terminal|json]
```

### `dovo sandbox prune`

Safely prune stale sandboxes, orphaned directories, and temporary branches:

```bash
dovo sandbox prune [--dry-run] [--force] [--format terminal|json]
```

### `dovo sandbox delete`

Remove an isolated worktree sandbox when work is complete:

```bash
dovo sandbox delete <sandbox-id> [--force] [--format terminal|json]
```

### `dovo sandbox apply`

Apply changes from an isolated sandbox worktree back into the main workspace:

```bash
dovo sandbox apply <sandbox-id> [OPTIONS]
```

#### Options

| Flag | Description |
| --- | --- |
| `--strategy <patch\|squash>` | Strategy for applying changes: `patch` (default: uncommitted working tree changes) or `squash` (stages and creates a single Git commit). |
| `--allow-dirty` | Allow application even if the main workspace has uncommitted changes. |
| `--dry-run` | Perform pre-apply conflict checks without modifying the workspace. |
| `--delete`, `-d` | Delete the sandbox worktree and branch upon successful application. |
| `--message <msg>`, `-m <msg>` | Custom commit message when using `--strategy squash`. |
| `--format <terminal\|json>` | Presentation format (`terminal` or `json`). |

#### Examples

Apply changes as working tree modifications:
```bash
dovo sandbox apply sbx_8f2a1b9c
```

Apply and squash into a commit, then remove the sandbox:
```bash
dovo sandbox apply sbx_8f2a1b9c --strategy squash --message "feat: implement auth service" --delete
```

### `dovo sandbox diff`

Inspect differences between the sandbox worktree and its base commit:

```bash
dovo sandbox diff <sandbox-id> [OPTIONS]
```

#### Options

| Flag | Description |
| --- | --- |
| `--stat` | Output summary diffstat statistics (files changed, insertions, deletions) instead of the full unified diff. |
| `--format <terminal\|json>` | Presentation format (`terminal` or `json`). |

#### Examples

View unified diff:
```bash
dovo sandbox diff sbx_8f2a1b9c
```

View diffstat summary:
```bash
dovo sandbox diff sbx_8f2a1b9c --stat
```
