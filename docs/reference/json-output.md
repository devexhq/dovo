# JSON Output

Every `dovo` command that takes `--format json` writes its output as JSON lines on stdout. This page describes the shape of those lines, the `error` event written for a failure no command result carries, and the exit codes every command shares.

## Event lines

Each line is one JSON object with exactly three keys, in this order:

```json
{"version": 1, "event_type": "<name>", "payload": {}}
```

| Key | Value |
| --- | --- |
| `version` | The integer `1`. It changes only when the shape of the line itself changes. |
| `event_type` | The event's name, from the table below. |
| `payload` | The event's fields. |

A command can write progress events before its result; the command result is its last line.

Under `--format json`, every line on stdout is an event line. A question a command asks, such as the confirmation of a `delete` run without `--force`, is written to stderr, and the notice that `--verbose` (`-v`) turns on is written as a `message` event.

### Event names

An event name is lowercase words joined by dots, matching `^[a-z][a-z_]*(\.[a-z][a-z_]*)*$`, and each name belongs to exactly one kind of event. A command result is named after the command path. A shortcut writes the same name as its long form: `dovo diff`, `dovo logs` and `dovo resume` write `session.diff`, `session.logs` and `run.result`, like `dovo session diff`, `dovo session logs` and `dovo session resume`.

| `event_type` | Written by |
| --- | --- |
| `artifacts.list`, `artifacts.download`, `artifacts.prune` | `dovo artifacts list`, `download` and `prune` |
| `blueprint.list`, `blueprint.show`, `blueprint.create`, `blueprint.delete`, `blueprint.validate` | `dovo blueprint list`, `show`, `create`, `delete` and `validate` |
| `step.list`, `step.show`, `step.create`, `step.delete`, `step.validate` | `dovo step list`, `show`, `create`, `delete` and `validate` |
| `config.show`, `config.validate`, `config.set`, `config.unset` | `dovo config show`, `validate`, `set` and `unset` |
| `session.list`, `session.show`, `session.logs`, `session.diff` | `dovo session list`, `show`, `logs` and `diff` |
| `worktree.create`, `worktree.list`, `worktree.show`, `worktree.diff`, `worktree.apply`, `worktree.delete`, `worktree.prune` | `dovo worktree create`, `list`, `show`, `diff`, `apply`, `delete` and `prune` |
| `init` | `dovo init` |
| `status` | `dovo status` |
| `doctor` | `dovo doctor` |
| `run.result` | The last line of `dovo run` and `dovo resume` (see [`dovo run`](../cli/run.md#run-result)) |
| `run.step_start`, `run.step_done` | A step starting and finishing during `dovo run` or `dovo resume` |
| `run.step_skipped` | A step or loop skipped by its `when` guard |
| `run.if_branch` | The branch an `if` block chose |
| `run.step_output` | One line of a running step's output |
| `run.agent_progress` | One action of an agent step |
| `run.worktree` | The run's worktree being created, activated or cleaned up |
| `run.loop` | A loop's progress |
| `run.prompt` | A prompt that waits for an answer |
| `message` | A notice, such as how to answer a paused run |
| `lock.wait` | Waiting for another `dovo` process to release the workspace lock |
| `banner` | The welcome banner |
| `error` | A failure no command result carries (see [The `error` event](#the-error-event)) |

## Command results

Every command result's payload starts with these six header keys, in this order:

| Key | Value |
| --- | --- |
| `ok` | `true` exactly when `error_code` is `null`. |
| `error_code` | The code of the failure, or `null` when the command succeeded. |
| `errors` | The failure's messages; empty when the command succeeded. |
| `warnings` | Warnings that did not stop the command. |
| `remediations` | Suggested fixes, each with a `message` and a `command` (`null` when there is no command to run). |
| `diagnostics` | [Diagnostic log](../cli/doctor.md#diagnostic-logs) records, each with an `event_id` and a `log_path`. |

`errors`, `warnings`, `remediations` and `diagnostics` are always lists, never `null`, and `errors` is non-empty exactly when `error_code` is set. `warnings` and `remediations` can be non-empty when `ok` is `true`.

The payload's other keys follow the header. They belong to the command and are documented on its page. Only two commands report a `status` key: `dovo status` (the workspace's health) and `dovo worktree prune` (what the run did). Every other command reports a failure only through `error_code`. Streaming events written before the result, such as `run.step_start`, carry no header.

## The `error` event

Some failures stop a command before it produces a result. Dovo then writes one `error` event instead:

- The workspace is not initialized.
- The configuration does not load, for a command that needs it.
- Another `dovo` process holds the workspace lock and the wait times out.
- The automatic initialization of `dovo run` fails, so no run starts.
- An unexpected error escapes the command.

The `error` event is the last line of the output and the only line written for that failure. It is written whenever `--format json` or `--format=json` appears on the command line, even when the failure happens before the subcommand's own options are read. For example, `dovo worktree list --format json` in a repository without `.dovo/project.json` prints:

```json
{"version": 1, "event_type": "error", "payload": {"ok": false, "error_code": "WORKSPACE_NOT_INITIALIZED", "errors": ["Workspace is not initialized: no valid project identity at '/path/to/repo/.dovo/project.json'."], "warnings": [], "remediations": [{"message": "Run 'dovo init' to initialize the workspace.", "command": "dovo init"}], "diagnostics": []}}
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
| Automatic initialization of `dovo run` fails | The initialization failure's code, such as `BOOTSTRAP_PATH_INVALID` | `Initialization Failed!` | The initialization failure's fixes, as `dovo init` prints them. |
| Unexpected error | `INTERNAL_ERROR` | `Fatal Error` | None. |

Every `error` event, and every one of these panels, exits `1`.

### Workspace not initialized

Every command except `dovo init` and `dovo run` needs a valid `.dovo/project.json`. When it is missing, unreadable or invalid, the command fails with `Workspace is not initialized: no valid project identity at '<path>'.` and the remediation `Run 'dovo init' to initialize the workspace.` (command `dovo init`). When `.dovo/project.json` exists but cannot be used, a second remediation follows: `Run 'dovo init --id <project-id> --force' to replace the unusable identity.` (command `dovo init --id <project-id> --force`).

`dovo run` initializes a workspace that has no `.dovo/project.json` itself; see [`dovo run`](../cli/run.md). When that initialization fails, `dovo run --format json` writes the `error` event with the initialization failure's code, messages and remediations instead of a `run.result`, because no run has started.

### Configuration does not load

Commands other than `dovo config`, `dovo doctor`, `dovo init` and `dovo status` load `.dovo/config.json` before they start. When it does not load, the `error_code` is the configuration failure's own code, such as `CONFIG_NOT_FOUND` when the file is missing or `CONFIG_JSON_INVALID` when it is not valid JSON. The codes are listed under [`dovo config show`](../cli/config.md#dovo-config-show).

### Workspace lock timeout

Commands that change the workspace wait for the workspace lock while another `dovo` process holds it. When the wait starts, a command run with `--format json` writes one `lock.wait` line, and without it prints a `Lock Held` panel. When the wait times out, the message is:

```text
Timed out waiting for workspace lock on '<lock path>' after <seconds>s (held by process <pid>).
```

### Unexpected error

An unexpected error reports `A fatal unexpected error occurred. Details: <error>` and writes a [diagnostic log](../cli/doctor.md#diagnostic-logs) record, listed in `diagnostics`.

## Usage errors

An unknown option, a missing required option, a value an option does not accept (such as `--format nope` or `dovo run --env-mode bogus`) and a rejected combination (such as `dovo run --keep --discard`) are usage errors. They print a usage message, never a JSON event, even with `--format json`, and exit `2`, whether or not the workspace is initialized or its configuration loads.

`dovo run` is the only command that accepts options and arguments it does not declare: it passes them to the blueprint as inputs. One that matches no declared input is not a usage error: the run ignores it and its result carries the warning `Ignoring unrecognized option '<option>'.`, or `Ignoring unexpected argument '<argument>'.` for a bare value, so `dovo run <blueprint> --bogus 1` warns about both `--bogus` and `1`. A declared input given a value its type rejects, or a required input left out, fails the run with `BLUEPRINT_INPUT_INVALID` and exits `1`.

## Exit codes

Every command's exit code comes from its result:

| Exit | When |
| --- | --- |
| `0` | The result has no `error_code`. This includes a paused run, a declined delete confirmation, every `dovo status` report and a `dovo doctor` report whose checks only warn or skip. |
| `2` | A usage error, or a code decided only from the command's own arguments: `PROJECT_ID_INVALID`, `CONFIG_KEY_PATH_INVALID`, `AGENT_ENV_OVERRIDE_INVALID`, `INTERACTION_ARGUMENTS_INVALID` or `CATALOG_TIER_CONFLICT`. |
| `130`, `143`, `129` | A `dovo run` or `dovo resume` you interrupt (`RUN_CANCELLED`, or `SESSION_PROCESS_STOP_FAILED` when a process group it started survived the stop): `128` plus the signal number, `130` for Ctrl-C (`SIGINT`), `143` for `SIGTERM` and `129` for a closed terminal (`SIGHUP`). The run result prints first. |
| `1` | Every other `error_code`, and every `error` event. |

Each command page lists its own conditions under **Exit codes**.
