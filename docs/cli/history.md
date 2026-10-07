# `dovo history`

The `dovo history` command inspects past blueprint executions, enabling developers to filter past sessions and view granular step details and error messages.

## Usage

```bash
dovo history [OPTIONS]
dovo history list [OPTIONS]
```

### Options

| Flag | Description |
| --- | --- |
| `--limit, -l <int>` | Maximum number of history records to display (default: 20). |
| `--status, -s <status>` | Filter sessions by lifecycle status (`running`, `completed`, `failed`, `cancelled`, `paused`). |
| `--format [terminal\|json]` | Presentation format (`terminal` or `json`). |

Error messages shown by `dovo history list` and `dovo history show` are masked on read, including names listed under `environment.sensitive_variables` in `.dovo/config.json`, so rows stored before a name was listed are still hidden. Masked values print as `[REDACTED:<NAME>]` or `[REDACTED]`.

## Subcommands

### `dovo history` / `dovo history list` (Default)

Lists execution history sessions recorded in the centralized database. Executing `dovo history` without subcommands defaults to listing sessions.

```bash
dovo history [OPTIONS]
dovo history list [OPTIONS]
```

### `dovo history show`

Show granular metadata and error details for a specific execution session.

```bash
dovo history show <session_id> [OPTIONS]
```

#### Arguments

| Argument | Description |
| --- | --- |
| `session_id` | Required session identifier to inspect. |

#### Options

| Flag | Description |
| --- | --- |
| `--logs` | Also list the session's log files and the last 10 `session.log` events. See [`dovo logs`](logs.md) for full log output. The command exits `1` if the session's logs cannot be read. |
| `--format [terminal\|json]` | Presentation format (`terminal` or `json`). |

## Examples

List recent blueprint execution history:

```bash
dovo history
```

List failed executions limited to the 5 most recent sessions:

```bash
dovo history --status failed --limit 5
```

Output history list as structured NDJSON envelopes:

```bash
dovo history list --format json
```

Inspect details for a specific session:

```bash
dovo history show blueprint_a1b2c3d4
```

Inspect session details in JSON format:

```bash
dovo history show blueprint_a1b2c3d4 --format json
```
