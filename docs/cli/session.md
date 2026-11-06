# `dovo session`

`dovo session` lists past blueprint sessions and inspects, diffs, reads the logs of, or resumes one of them.

## Usage

```bash
dovo session [OPTIONS]
dovo session list [OPTIONS]
dovo session show [session_id] [OPTIONS]
dovo session logs [session_id] [OPTIONS]
dovo session diff [session_id] [OPTIONS]
dovo session resume [session_id] [OPTIONS]
```

`dovo session` with no subcommand behaves as `dovo session list`, including `--limit`, `--status` and `--format`. `dovo session ls` is a hidden alias of `dovo session list`.

## Shortcuts

`dovo logs`, `dovo diff` and `dovo resume` stay available as top-level commands. Each is registered with the same handler as its `dovo session` form, so options, output and exit codes are identical:

| Shortcut | Same as |
| --- | --- |
| `dovo logs [session_id]` | `dovo session logs [session_id]` |
| `dovo diff [session_id]` | `dovo session diff [session_id]` |
| `dovo resume [session_id]` | `dovo session resume [session_id]` |

## Latest session default

When `session_id` is omitted, `show`, `logs` and `diff` act on the project's latest session: the greatest `started_at`, ties broken by the greatest session id, whatever its status. In terminal format, `show` and `logs` then start with `Session: <session_id> (latest)`, and `diff` uses that text as its header instead of the artifact path. The line is not printed with `--patch` or `--format json`; the resolved id is already in the JSON payload. With an explicit `session_id` the line is never printed.

If the project has no sessions, these commands print a **Session Not Found** panel (`No sessions found.`, with the fix `Run 'dovo run <blueprint>' to create a session`) and exit `1`. An explicit `session_id` that does not exist exits `1` with `Session '<session_id>' not found.` in the not-found panel for that command.

With `--format json`, every failure of `list`, `show`, `logs` and `diff` carries a non-null `error_code` and its message in `errors`, with the same remediations the terminal panel shows; a success has `error_code` `null` and an empty `errors` list. Both not-found cases above report `error_code` `SESSION_NOT_FOUND`. The payloads carry no other classification of the outcome, so a script branches on `error_code` alone.

`resume` keeps its own rule: with no `session_id` it resumes the latest paused session.

Error messages shown by `list` and `show` are masked on read, including names listed under `environment.sensitive_variables` in `.dovo/config.json`, so rows stored before a name was listed are still hidden. Masked values print as `[REDACTED:<NAME>]` or `[REDACTED]`.

## `dovo session list`

Lists recorded sessions, newest first.

Before it lists, `dovo session list` settles sessions left `running` by a `dovo` process that no longer exists: it marks one `paused` when it saved its progress, its worktree still exists if it used one, and none of its processes is still running, and marks every other one `failed`. It adds `Paused 1 interrupted session; continue it with 'dovo resume <session_id>'.`, `Marked 1 interrupted session failed (session_id: <id>).`, or both sentences, paused first, to its warnings. If another `dovo` process holds the workspace lock until the wait times out, `dovo session list` stops with a Workspace Lock Timeout panel, or with `--format json` an `error` event with `error_code` `WORKSPACE_LOCK_TIMEOUT` (see [JSON Output](../reference/json-output.md#the-error-event)), and exits `1`.

### Options

| Flag | Description |
| --- | --- |
| `--limit, -l <int>` | Maximum number of sessions to display (default: 20). |
| `--status, -s <status>` | Filter sessions by lifecycle status (`running`, `completed`, `failed`, `cancelled`, `paused`). |
| `--format [terminal\|json]` | Presentation format (`terminal` or `json`). |

## `dovo session show`

Shows the metadata, errors and per-step results for one session.

For a finished run that used a worktree, the metadata includes a `Worktree` row: `kept (<path>)` when the worktree was kept, `removed` otherwise. With `--format json` the payload carries `worktree_kept` and `worktree_path`, both `null` for in-place and still-running sessions, and `worktree_path` is `null` when the worktree was removed. The row reports the outcome recorded when the run ended, so a later `dovo worktree delete` does not change it.

Below the metadata, a `Steps` table lists every step in plan order with its state, attempt count, exit code and duration. A loop appears as its own row followed by its body steps, labeled `<step id> (iter <n>)` for each iteration. A step or loop skipped by its `when` list shows its cause after its state: `skipped (when_false)` or `skipped (when_unresolved)`; a skipped loop has no body rows. An `if` block appears as its own row, with the branch it took after its state (`completed (else)`), followed by the steps of that branch, indented. Missing values show `-`, and durations under 0.1 seconds show `<0.1s`. Failed steps list their error message under the table, with secrets redacted. Step stdout, stderr and outputs are never shown here; use [`dovo session logs`](#dovo-session-logs) with `--step`.

```
Steps
┏━━━┳━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━┳━━━━━━━━━━┓
┃ # ┃ Step   ┃ State     ┃ Attempts ┃ Exit ┃ Duration ┃
┡━━━╇━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━╇━━━━━━━━━━┩
│ 1 │ format │ completed │        1 │    0 │     0.1s │
│ 2 │ tests  │ failed    │        1 │    1 │    31.9s │
└───┴────────┴───────────┴──────────┴──────┴──────────┘
• tests: Command failed with exit code 1.
Use 'dovo logs <session_id> --step <step_id>' for step output.
```

A failed agent step whose edit tripped a patch limit, or whose agent reported the task `unfixable` after editing files, lists `Discarded edit saved to <path>.`; with `--format json` the step carries `rejected_patch_path` (`null` for every other step). The saved patch is stored exactly as the agent wrote it and may contain secrets from the agent's output, so no command prints its content; treat the file like any other session artifact.

When a session has no recorded execution state, the command prints `No step data recorded for this session.` instead of the table; when the state cannot be parsed, it prints `Step data could not be read: <reason>`. An unexpected error reading the steps prints `Unexpected error during engine.session_show: <error>` in the same place and is recorded; see [Diagnostic logs](doctor.md#diagnostic-logs). None of these changes the exit code. With `--format json`, the payload carries a `steps` list of objects with the keys under [Step keys](#step-keys); when the state is missing, cannot be parsed or cannot be read, `steps` is `[]` and the reason is added to `warnings`.

### Step keys

| Key | Description |
| --- | --- |
| `kind` | `step`, `loop` or `if`. |
| `step_id` | The step, loop or `if` block id. |
| `name` | The name it sets, or `null`. |
| `state` | Its state, such as `completed`, `failed` or `skipped`. |
| `attempts` | How many attempts the step made; `null` on a loop or `if` row. |
| `exit_code` | The exit code of the step's last attempt, or `null`. |
| `duration_seconds` | The total duration of the step's attempts, or `null`. |
| `error_message` | Why the step's last attempt failed, with secrets redacted, or `null`. |
| `error_code` | The code of that failure, such as `STEP_COMMAND_FAILED` or `STEP_TIMEOUT` (see [Step failure codes](run.md#step-failure-codes)); `null` on a completed step, a loop or `if` row, or a step with no result. |
| `rejected_patch_path` | The saved discarded edit of the step's last attempt, or `null`. |
| `loop_id` | The loop a body step belongs to, or `null`. |
| `iteration` | The loop iteration a body step ran in, or `null`. |
| `iterations` | How many iterations a loop ran, on a loop row; otherwise `null`. |
| `skip_cause` | `when_false` or `when_unresolved` on a row a `when` list skipped; `null` on a row that was not skipped. |
| `branch` | `then`, `else` or `none` on an `if` row that started; otherwise `null`. |
| `if_id` | The `if` block each step of its chosen branch belongs to, or `null`. |

### Arguments

| Argument | Description |
| --- | --- |
| `session_id` | Optional session identifier to inspect. If omitted, the latest session is used. |

### Options

| Flag | Description |
| --- | --- |
| `--logs` | Also list the session's log files and the last 10 `session.log` events. See [`dovo session logs`](#dovo-session-logs) for full log output. If the session's logs cannot be read, the command prints a **Read Failure** panel (`Failed reading session logs in '<dir>': <detail>`), reports `error_code` `SESSION_LOGS_UNREADABLE` with `--format json`, records a [diagnostic log](doctor.md#diagnostic-logs) entry, and exits `1`. |
| `--format [terminal\|json]` | Presentation format (`terminal` or `json`). |

## `dovo session logs`

Shows the logs of a past session: the timeline (`session.log`) by default, or one step's stdout/stderr with `--step`.

Logs live under the global storage root: `~/.dovo/storage/projects/<project_id>/logs/<session_id>/` (the root honors `DOVO_HOME`). They are kept after the run finishes.

```
logs/<session_id>/
├── session.log                          # JSON Lines timeline, one event per line
├── 01_setup_attempt_1.stdout.log    # raw per-attempt captures
├── 01_setup_attempt_1.stderr.log
└── 02_check_iter_1_attempt_1.stdout.log   # loop sub-steps carry an _iter_<n> segment
```

Per-attempt stdout/stderr capture is controlled by `history.save_attempt_logs` in `.dovo/config.json` (default `true`). `session.log` is always written when the run has a session ID. Loop sub-steps appear in `session.log` as their own `STEP_START`/`STEP_DONE` events, interleaved with the loop's `LOOP_*` events, exactly like top-level steps; loop body events carry `loop_id` and `iteration`, and every step event carries `step_name` (`STEP_DONE` also `duration_seconds`).

Persisted step logs and error messages are masked when written and again when read, so a secret still present in the environment is hidden even in logs written before masking existed. Masked values print as `[REDACTED:<NAME>]` or `[REDACTED]`. Names listed under `environment.sensitive_variables` in `.dovo/config.json` are masked here too, including in logs written before the name was listed.

For agent steps, the capture files hold the provider's stdout and stderr lines as they arrive, after secret masking; the step's one-line JSON summary is not written to them. Raw provider output is never printed to the terminal. While an agent attempt runs, the terminal shows one `● Read|Ran|Edited|Searched <target>` entry per action the agent takes, with adjacent identical entries collapsed, and falls back to an `agent running (<elapsed>s)` line when 30 seconds pass without progress. When the agent writes text before an action, the first line of that text is shown as its own `●` entry just before the action, and the first lines of the tool's output are shown under the action behind a `⎿` connector; text still pending when the attempt ends, such as the closing message, is not shown. Each action or text entry also appends an `agent_progress` event to `session.log`, carrying the step's `step_id`, `attempt`, `loop_id` and `iteration` when present, plus `kind` (`edit`, `read`, `search`, `shell` or `note`) and the masked `target`; tool output is not logged there; `dovo session logs` lists it as `kind=<kind> target=<target>` in the Details column. Progress is reported per provider; a provider that does not report progress shows only the heartbeat line. Today the Copilot and Claude providers report progress.

### Arguments

| Argument | Description |
| --- | --- |
| `session_id` | Optional session identifier whose logs to show. If omitted, the latest session is used. |

### Options

| Flag | Description |
| --- | --- |
| `--step <step_id>` | Show the raw output captured for this step instead of the session timeline. |
| `--attempt <int>` | Attempt number to show with `--step` (default: the latest attempt). |
| `--stream [stdout\|stderr\|both]` | Output stream to show with `--step` (default: `both`, stdout lines first). |
| `--tail <int>` | Show only the last N lines (with `--step`) or events (without). |
| `--format [terminal\|json]` | Presentation format (`terminal` or `json`). |

`--attempt` and `--stream` only apply together with `--step`; without it they are ignored.

For a loop sub-step, `--step` matches the captures of every loop iteration: the selected attempt's output from each iteration is shown concatenated in iteration order, and the default attempt is the highest attempt number reached in any iteration. Captures from any steps that share the same step ID, such as a top-level step and a loop sub-step, are grouped under that ID. Capture file names do not include the loop's ID, so two loops that have a sub-step with the same ID at the same position write to the same files, and the later loop overwrites the earlier one's captures. Give loop sub-steps distinct IDs to keep both.

### Errors

`dovo session logs` exits `1` when:

- no session record exists for the session: a **Logs Not Found** panel with `Session '<session_id>' not found.` and `error_code` `SESSION_NOT_FOUND`,
- the session has no log directory: a **Logs Not Found** panel with `No logs found for session '<session_id>'.` and `error_code` `SESSION_NOT_FOUND`,
- `--step` names a step with no captured logs: a **Step Not Found** panel with `Step '<step>' not found in session '<session_id>'. Available steps: <ids, or none>.` and `error_code` `SESSION_STEP_NOT_FOUND`; with `--format json`, `available_steps` lists the captured step IDs,
- `--attempt` names an attempt that was not captured: an **Attempt Not Found** panel with `Attempt <n> not found for step '<step>' in session '<session_id>'. Available attempts: <numbers>.` and `error_code` `SESSION_ATTEMPT_NOT_FOUND`; with `--format json`, `available_attempts` lists the captured attempts,
- a log file cannot be read, for example because of its permissions: a **Read Failure** panel with `Failed reading session logs in '<dir>': <detail>` and `error_code` `SESSION_LOGS_UNREADABLE`, and a [diagnostic log](doctor.md#diagnostic-logs) entry is recorded.

A truncated final `session.log` line, for example after the process was killed mid-write, is skipped rather than reported as an error. Bytes that are not valid UTF-8 are shown as `�`.

## `dovo session diff`

Shows the syntax-highlighted diff recorded for a session.

### Arguments

| Argument | Description |
| --- | --- |
| `session_id` | Optional session identifier (e.g. `blueprint_a1b2c3d4`). If omitted, displays the latest session diff. |

### Options

| Flag | Description |
| --- | --- |
| `--patch` | Print the plain patch to stdout, with no header, colour or truncation. Cannot be combined with `--format json`. |
| `--full` / `--no-full` | Show the whole formatted diff instead of truncating it in a terminal. |
| `--format <terminal\|json>` | Presentation format (`terminal` or `json`). Defaults to `terminal`. |

### Behavior

1. **Session Resolution**:
   - Sessions resolve below `DOVO_HOME/storage/projects/<project-id>/sessions/` (or `~/.dovo/storage/projects/<project-id>/sessions/` when `DOVO_HOME` is unset).
   - A session exists only when its session record exists; a session directory without a session record is not found.
   - When `session_id` is supplied: resolves that session's `diff.patch`.
   - When `session_id` is omitted: selects the latest session as described in [Latest session default](#latest-session-default).
   - If no session record matches: displays a **Session Not Found** error panel (`error_code` `SESSION_NOT_FOUND`) and exits with code `1`.
2. **Artifact Loading**:
   - If `diff.patch` is missing: displays a **Diff Not Found** error panel with `Session '<session_id>' has no diff artifact.` (`error_code` `SESSION_DIFF_NOT_FOUND`) and exits with code `1`.
   - If `diff.patch` cannot be read: displays a **Read Failure** error panel with `Failed to read diff artifact at '<path>': <detail>` (`error_code` `SESSION_DIFF_UNREADABLE`), records a [diagnostic log](doctor.md#diagnostic-logs) entry, and exits with code `1`.
   - If `diff.patch` is empty (0 bytes or whitespace-only): the diff is a success like any other. The command prints `No changes recorded for session <session_id>.` and exits with code `0`; with `--format json`, `error_code` is `null`, `diff_text` is empty and `total_lines` is `0`.
3. **Rendering & Truncation**:
   - Interactive formatted output renders a header with the session ID and artifact path (or `(latest)` when the id was omitted), followed by syntax-highlighted diff text.
   - In an interactive terminal (TTY), if formatted diff output exceeds 500 lines (and `--full` is not provided), output is truncated at line 500 followed by a dim notice banner with hints to view the complete diff, page with `less -R`, print the plain patch with `--patch`, or open the artifact.
   - Passing `--full` renders all formatted lines without truncation.
   - Non-TTY stdout (e.g. piped to `cat` or redirected to a file) and `--patch` bypass truncation.
   - When `--patch` is passed, writes the exact patch content to stdout for redirection or piping into `git apply` / `patch`; an empty patch writes nothing.
4. **Secret masking**: `diff.patch` is written with secret values replaced by `[REDACTED:<NAME>]` or `[REDACTED]`, so a masked line will not reproduce the original value when applied with `git apply` or `patch`. Names listed under `environment.sensitive_variables` in `.dovo/config.json` are masked at write time; a diff written before a name was listed is not re-masked.
5. **Exit codes**:
   - `0`: Diff successfully displayed, or empty diff.
   - `1`: Session not found (`SESSION_NOT_FOUND`), diff artifact not found (`SESSION_DIFF_NOT_FOUND`), read failure (`SESSION_DIFF_UNREADABLE`), or a failure reported as an `error` event (see [Exit codes](#exit-codes)).
   - `2`: `--patch` combined with `--format json`, or any other usage error.

## `dovo session resume`

Continues a paused session, by ID or the latest paused one.

### Arguments

| Argument | Description |
| --- | --- |
| `session_id` | Optional session identifier to resume. If omitted, the latest paused session is automatically resumed. |

### Options

| Flag | Description |
| --- | --- |
| `--no-tty` | Disable interactive prompts; prompt_user failures abort the run, and an `ask` prompt pauses it again instead of blocking for input. |
| `--answer <step_id>=<value>` | Answer the pending `ask` prompt (`multiselect`: a JSON array or comma-separated values; `text`: the text). |
| `--comment <text>` | Comment accompanying `--answer`. |
| `--comment-file <path>` | Read the comment accompanying `--answer` from a file. |
| `--answers-file <path>` | JSON object `{"step_id": "...", "selected": ["B1"], "comment": "...", "text": "..."}`; cannot be combined with `--answer`, `--comment` or `--comment-file`. |
| `--env-mode <allowlist\|inherit>` | Override `agents.<provider>.env_mode` for this invocation. |
| `--env-passthrough <name-or-prefix*>` | Forward a host variable name or a prefix ending in a single `*` to agent steps for this invocation; repeatable and appended to `agents.<provider>.env_passthrough`. |
| `--format <terminal\|json>` | Presentation format (`terminal` or `json`). Defaults to `terminal`. |
| `--display <ansi\|live>` | Display format (`ansi` or `live`). Defaults to `ansi`. |

The flag list is pinned to the command by [`tests/lint/test_step_schema_parity.py`](https://github.com/devexhq/dovo/blob/main/tests/lint/test_step_schema_parity.py).

### Behavior

1. **Session Resolution**:
   - If `session_id` is provided, resumes that specific session.
   - If `session_id` is omitted, the most recent paused session is used, and the outcome line names it. If there is none, an error is shown and the command exits `1`.
   - Before resolving the session, the command settles sessions left `running` by a `dovo` process that no longer exists, as `dovo session list` does: a crashed run that can resume is paused, and is resumed by this command when it is the one named or the latest paused session. Resume a crashed run from the checkout that created it. If another `dovo` process holds the workspace lock until the wait times out, `dovo resume` stops with a Workspace Lock Timeout panel, or with `--format json` an `error` event with `error_code` `WORKSPACE_LOCK_TIMEOUT` (see [JSON Output](../reference/json-output.md#the-error-event)), and exits `1`.
2. **Readiness Classification**: Checks that the session exists, is paused, and has intact saved state and an accessible worktree (if it used one).
3. **Validation**: Checks the run's saved copy of its blueprint exactly as [`dovo blueprint validate`](blueprint.md#dovo-blueprint-validate) checks a blueprint, with the same `error_code`, messages, order, warnings and remediations, except that each `script_path` is looked up in the run's worktree (the repository root for a `--no-worktree` run). When validation reports errors, the command prints a `Run Failed` panel listing every error and exits `1`; no step runs and the session stays paused with its saved state unchanged. Each validation warning is one of the resume's warnings: it prints once, as `Warning: <text>`, before the outcome, and the resume continues. With `--format json` the run result carries the same validation as for [`dovo run`](run.md#validation-failures), with the paused session's `session_id`, `blueprint_name` and `session_status`.
4. **Execution**: Continues running from the paused step. A step a crashed run was running when its process died runs again from its start, with the warning `Step '<step_id>' was interrupted before it recorded a result and was run again as attempt <n>; check for side effects its interrupted attempt already made.` A crashed run whose steps all finished runs no step and ends `completed`, applying its changes when it was started with `--auto-apply`; one whose steps all finished with a failed step is marked `failed` with `Session interrupted by abnormal process termination` and the command exits `1`.
5. **Pending prompts**: a session paused at an `ask` step asks the stored prompt again; the producing steps do not re-run. With `--answer` or `--answers-file` the answer is validated before any step runs and the run continues in the same process; a later `ask` pauses again. An answer the pending prompt rejects (an unknown step id, a value that is not an option, a malformed multiselect JSON array, a missing required comment or a comment over 8000 characters) reports `INTERACTION_ANSWER_INVALID` and exits `1` with the valid choices; answers for a session with no pending prompt also exit `1`. Answer flags that conflict (`--comment` with `--comment-file`, `--answers-file` with `--answer`, `--comment` or `--comment-file`, or a comment without `--answer`), an `--answer` that is not `<step_id>=<value>`, or an `--answers-file` whose content is not a valid answers JSON object report `INTERACTION_ARGUMENTS_INVALID` and exit `2`. A `--comment-file` or `--answers-file` that cannot be read reports `INTERACTION_ARGUMENT_FILE_UNREADABLE` and exits `1`. Every one of these leaves the session paused. See [Human Input](../guides/human-input.md).
6. **Environment flags**: `--env-mode` and `--env-passthrough` apply only to this invocation, are never written to config or run state, and are validated before any step starts. An invalid `--env-passthrough` entry reports `AGENT_ENV_OVERRIDE_INVALID`, exits `2` and leaves the session paused; an `--env-mode` value other than `allowlist` or `inherit` is a usage error, also exit `2`.
7. **Diagnostic logs**: When a resume hits an unexpected error, Dovo writes a [diagnostic log](doctor.md#diagnostic-logs) record and, in terminal output, prints one `Details logged: <path> (event <event_id>)` line per record after the resume's warnings and outcome. This covers reconciling interrupted sessions, loading the session record, saving the resume's state or status, saving its diff, creating, applying (with `--auto-apply`) or removing its worktree, and a step launching its process, reading its output, calling its agent provider, publishing its artifacts, resolving its `uses:` step, checking an agent's edit or preparing its private scratch, including when the operating system refuses to create that scratch. The resume's errors and warnings, each step's error and the saved session hold the message alone, and a recorded step failure adds no `Details logged:` warning; the step's own warnings, such as a failed optional artifact publish, still appear. When the resume itself hits any other unexpected error, the resume fails with `Unexpected error during engine.resume: <error>`, or `Unexpected error during engine.resume_target: <error>` while finding the session to resume. Once the resume has restarted the session, an unexpected error records that session `failed`, never leaving it `running`. With `--format json` the records are the run result's `diagnostics`, each with its `event_id` and `log_path`, and no error or warning contains `Details logged:` text. When checking an agent's edit fails, the rejected edit is saved and discarded, or left in the worktree when it cannot be saved.
8. **Run result**: Every resume ends with the same [run result](run.md#run-result) as `dovo run`, with the same outcome texts, including a resume rejected before any step runs: an unknown session, no paused session, or an invalid `--answer`, `--comment` or `--answers-file`. A rejected resume's session fields are `null` when no session was loaded. A failed resume prints `Run Failed` and an interrupted one `Run Cancelled`.
9. **Exit codes**:
   - `0`: Successful completion or paused run (run state updated), including a run paused at an `ask` prompt.
   - `2`: Answer arguments that conflict or are malformed (`INTERACTION_ARGUMENTS_INVALID`), an invalid `--env-passthrough` entry (`AGENT_ENV_OVERRIDE_INVALID`), or a usage error such as an `--env-mode` value it does not accept.
   - `130` for Ctrl-C (`SIGINT`), `143` for `SIGTERM` and `129` for a closed terminal (`SIGHUP`), `128` plus the signal number (`RUN_CANCELLED`): `dovo resume` finishes its cleanup and prints its run result, then ends by the same signal. A resume interrupted while a step runs, or while it sets up, applies its changes or removes its worktree, stops the run's processes, is recorded `cancelled` once they have exited, with every step and loop that had started, and prints its `Run Cancelled` outcome first. When a process group is still running 5 seconds after the stop, it is recorded `failed` with `SESSION_PROCESS_STOP_FAILED` and prints `Run Failed` instead. A resume interrupted at a `prompt_user` failure prompt, an `ask` prompt or a loop `max_iterations` prompt stays paused and prints `Run Cancelled`.
   - `1`: Every other failure, including a resume validation error, a blueprint snapshot that fails validation, an answer the prompt rejects (`INTERACTION_ANSWER_INVALID`), an unreadable `--comment-file` or `--answers-file` (`INTERACTION_ARGUMENT_FILE_UNREADABLE`), a failed step, no paused session found, and a failure reported as an `error` event.

## Exit codes

Every `dovo session` subcommand exits by its result's `error_code`:

- `0`: the command succeeded. For `resume` this includes a run that pauses again.
- `2`: a usage error, such as an unknown option or `--patch` with `--format json`; for `resume`, also the answer and `--env-passthrough` errors listed under [`dovo session resume`](#dovo-session-resume).
- `130`, `143` or `129`: a `resume` you interrupt with Ctrl-C (`SIGINT`), `SIGTERM` or by closing the terminal (`SIGHUP`) (`RUN_CANCELLED`, or `SESSION_PROCESS_STOP_FAILED` when a process group survived the stop).
- `1`: every other failure, such as `SESSION_NOT_FOUND`, and every failure reported as an `error` event: a workspace that is not initialized, a configuration that does not load, a workspace lock timeout or an unexpected error.

See [JSON Output](../reference/json-output.md#exit-codes) for the rule every command follows and the `error` event's payload.

## Examples

Answer a prompt a headless run paused at, then continue:

```bash
dovo resume blueprint_a1b2c3d4 --answer approve=revise --comment "Keep config compatible" --no-tty
```

List recent sessions:

```bash
dovo session
```

List failed sessions limited to the 5 most recent:

```bash
dovo session list --status failed --limit 5
```

Output the session list as structured JSON envelopes:

```bash
dovo session list --format json
```

Inspect the latest session, or a specific one:

```bash
dovo session show
dovo session show blueprint_a1b2c3d4 --format json
```

Show the latest session's timeline, or one step's output from a specific session:

```bash
dovo session logs
dovo session logs blueprint_a1b2c3d4 --step test --attempt 2 --stream stderr --tail 20
```

View the latest session's diff, or write a specific session's plain patch to a file:

```bash
dovo session diff
dovo diff blueprint_a1b2c3d4 --patch > latest_fix.patch
```

Resume the latest paused session, or a specific one non-interactively:

```bash
dovo session resume
dovo resume blueprint_a1b2c3d4 --no-tty
```
