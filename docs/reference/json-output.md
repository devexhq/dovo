# JSON Output

Every `dovo` command that takes `--format json` writes its output as JSON lines on stdout. This page describes the shape of those lines, the `error` event written for a failure no command result carries, and the exit codes every command shares.

## Event lines

Each line is one JSON object with two keys:

```json
{"event_type": "<name>", "payload": {}}
```

`event_type` names the event, such as `BlueprintRunResult`, `StepStartEvent` or `error`, and `payload` holds its fields. A command can write progress events before its result; the command result is its last line.

## Command results

Most command results carry these header keys in their payload:

| Key | Value |
| --- | --- |
| `ok` | `true` exactly when `error_code` is `null`. |
| `error_code` | The code of the failure, or `null` when the command succeeded. |
| `errors` | The failure's messages; empty when the command succeeded. |
| `warnings` | Warnings that did not stop the command. |
| `remediations` | Suggested fixes, each with a `message` and a `command` (`null` when there is no command to run). |
| `diagnostics` | [Diagnostic log](../cli/doctor.md#diagnostic-logs) records, each with an `event_id` and a `log_path`. |

The payload's other keys belong to the command and are documented on its page. Which header keys a result carries, and where they appear among its other keys, differs from command to command: `dovo status` reports `health` and `config_error_code` with no `ok` or `error_code`, and `dovo doctor` reports `ok` and a list of `checks`, each with its own `error_code`. Read keys by name rather than by position.

## The `error` event

Some failures stop a command before it produces a result. Dovo then writes one `error` event instead:

- The workspace is not initialized.
- The configuration does not load, for a command that needs it.
- Another `dovo` process holds the workspace lock and the wait times out.
- An unexpected error escapes the command.

The `error` event is the last line of the output and the only line written for that failure. It is written whenever `--format json` or `--format=json` appears on the command line, even when the failure happens before the subcommand's own options are read. For example, `dovo worktree list --format json` in a repository without `.dovo/project.json` prints:

```json
{"event_type": "error", "payload": {"ok": false, "error_code": "WORKSPACE_NOT_INITIALIZED", "errors": ["Workspace is not initialized: no valid project identity at '/path/to/repo/.dovo/project.json'."], "warnings": [], "remediations": [{"message": "Initialize the workspace.", "command": "dovo init"}], "diagnostics": []}}
```

Its payload keys always come in this order:

| Key | Value |
| --- | --- |
| `ok` | Always `false`. |
| `error_code` | The failure's code (see the table below). |
| `errors` | The failure's message, then one entry per note attached to it. For example, when `dovo run` initializes the workspace automatically and both that initialization and its rollback fail, the rollback failure is a second entry. |
| `warnings` | Always `[]`. |
| `remediations` | Suggested fixes, each with a `message` and a `command` (`null` when there is no command to run). |
| `diagnostics` | Diagnostic log records, each with an `event_id` and a `log_path`. |

Without `--format json`, the same failure prints a red panel instead. The panel shows the messages, then a `Fix:` block listing each remediation's message, then one `Details logged: <path> (event <event_id>)` line per diagnostic log record.

| Failure | `error_code` | Panel title | Remediations |
| --- | --- | --- | --- |
| No valid `.dovo/project.json` | `WORKSPACE_NOT_INITIALIZED` | `Workspace Not Initialized` | See [Workspace not initialized](#workspace-not-initialized). |
| Configuration does not load | The configuration failure's code | `Invalid Configuration` | The configuration failure's fixes. |
| Workspace lock timeout | `WORKSPACE_LOCK_TIMEOUT` | `Workspace Lock Timeout` | `Wait for the other dovo process to release the workspace lock, then retry.` |
| Unexpected error | `INTERNAL_ERROR` | `Fatal Error` | None. |

Every `error` event, and every one of these panels, exits `1`.

### Workspace not initialized

Every command except `dovo init` and `dovo run` needs a valid `.dovo/project.json`. When it is missing, unreadable or invalid, the command fails with `Workspace is not initialized: no valid project identity at '<path>'.` and the remediation `Initialize the workspace.` (command `dovo init`). When `.dovo/project.json` exists but cannot be used, a second remediation follows: `Run 'dovo init --id <project-id> --force' to replace the unusable identity.` (command `dovo init --id <project-id> --force`).

`dovo run` initializes a workspace that has no `.dovo/project.json` itself; see [`dovo run`](../cli/run.md).

### Configuration does not load

Commands other than `dovo config`, `dovo doctor`, `dovo init` and `dovo status` load `.dovo/config.json` before they start. When it does not load, the `error_code` is the configuration failure's own code, such as `CONFIG_NOT_FOUND` when the file is missing or `CONFIG_JSON_INVALID` when it is not valid JSON. The codes are listed under [`dovo config show`](../cli/config.md#dovo-config-show).

### Workspace lock timeout

Commands that change the workspace wait for the workspace lock while another `dovo` process holds it. When the wait times out, the message is:

```text
Timed out waiting for workspace lock on '<lock path>' after <seconds>s (held by process <pid>).
```

### Unexpected error

An unexpected error reports `A fatal unexpected error occurred. Details: <error>` and writes a [diagnostic log](../cli/doctor.md#diagnostic-logs) record, listed in `diagnostics`.

## Usage errors

An unknown option, a missing required option, a value an option does not accept (such as `--format nope` or `dovo run --env-mode bogus`) and a rejected combination (such as `dovo run --keep --discard`) are usage errors. They print a usage message, never a JSON event, even with `--format json`, and exit `2`.

## Exit codes

Every command's exit code comes from its result:

| Exit | When |
| --- | --- |
| `0` | The result has no `error_code`. This includes a paused run, a declined delete confirmation, every `dovo status` report and a `dovo doctor` report whose checks only warn or skip. |
| `2` | A usage error, or a code decided only from the command's own arguments: `PROJECT_ID_INVALID`, `CONFIG_KEY_PATH_INVALID`, `AGENT_ENV_OVERRIDE_INVALID` or `INTERACTION_ARGUMENTS_INVALID`. |
| `130`, `143` | A `dovo run` or `dovo resume` you interrupt (`RUN_CANCELLED`): `128` plus the signal number, `130` for Ctrl-C (`SIGINT`) and `143` for `SIGTERM`. The run result prints first. |
| `1` | Every other `error_code`, and every `error` event. |

Each command page lists its own conditions under **Exit codes**.
