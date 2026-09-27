# `wt history`

The `wt history` command inspects past blueprint executions, enabling developers to filter past runs and view granular step details, error messages, and checkpoint data.

## Usage

```bash
wt history [OPTIONS]
wt history list [OPTIONS]
```

### Options

| Flag | Description |
| --- | --- |
| `--limit, -l <int>` | Maximum number of history records to display (default: 20). |
| `--status, -s <status>` | Filter runs by lifecycle status (`running`, `completed`, `failed`, `cancelled`, `paused`). |
| `--format [terminal\|json]` | Presentation format (`terminal` or `json`). |

## Subcommands

### `wt history` / `wt history list` (Default)

Lists execution history runs recorded in the centralized database. Executing `wt history` without subcommands defaults to listing runs.

```bash
wt history [OPTIONS]
wt history list [OPTIONS]
```

### `wt history show`

Show granular metadata, error details, and checkpoint contents for a specific execution session.

```bash
wt history show <session_id> [OPTIONS]
```

#### Arguments

| Argument | Description |
| --- | --- |
| `session_id` | Required session identifier to inspect. |

#### Options

| Flag | Description |
| --- | --- |
| `--logs` | Also list the session's log files and the last 10 `run.log` events. See [`wt logs`](logs.md) for full log output. The command exits `1` if the session's logs cannot be read. |
| `--format [terminal\|json]` | Presentation format (`terminal` or `json`). |

## Examples

List recent blueprint execution history:

```bash
wt history
```

List failed executions limited to the 5 most recent runs:

```bash
wt history --status failed --limit 5
```

Output history list as structured NDJSON envelopes:

```bash
wt history list --format json
```

Inspect details for a specific session:

```bash
wt history show blueprint_a1b2c3d4
```

Inspect session details in JSON format:

```bash
wt history show blueprint_a1b2c3d4 --format json
```
