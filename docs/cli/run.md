# `dovo run`

The `dovo run` command executes a blueprint by name from the catalog.

## Usage

```bash
dovo run <name> [OPTIONS] [-- <input-overrides>]
```

### Options

| Flag | Description |
| --- | --- |
| `--no-worktree` | Run execution in-place in the working tree without creating a Git worktree. Agent steps are rejected under it, because they require a worktree. |
| `--keep` | Keep the worktree whatever the run status. Each run gets its own worktree at `.dovo/worktrees/<session_id>/` on branch `dovo/<session_id>`, so repeated `--keep` runs of one blueprint do not collide. |
| `--discard` | Remove the worktree whatever the run status, including after a failed run. Cannot be combined with `--keep`: the pair exits `2` with a usage error before any session is created. |
| `--auto-apply` | Automatically apply worktree changes to the main workspace on successful completion. |
| `--agent <name>` | Use this agent provider for the run instead of `default_agent`. All agent settings (model, endpoint, temperature, max tokens, tools, environment) come from `agents.<name>`, or the field defaults when that block is absent; other providers' blocks are not read. An unknown name fails agent steps with `Unsupported agent provider '<name>'`; command and script steps are unaffected. |
| `--session-id <id>` | Explicit session identifier. |
| `--no-tty` | Disable interactive prompts; prompt_user failures abort the run instead of blocking for input, and a run that reaches an `ask` step pauses (exit `0`) so it can be answered with `dovo resume --answer`. |
| `--env-mode <allowlist\|inherit>` | Override `agents.<provider>.env_mode` for this invocation. |
| `--env-passthrough <name-or-prefix*>` | Forward a host variable name or a prefix ending in a single `*` to agent steps for this invocation; repeatable and appended to `agents.<provider>.env_passthrough`. |
| `--format <terminal\|json>` | Presentation format (`terminal` or `json`). Defaults to `terminal`. |
| `--display <ansi\|live>` | Display format (`ansi` or `live`). Defaults to `ansi`. |

Trailing CLI arguments (after options) are forwarded to declared blueprint inputs.

### Worktree retention

When the run ends, the worktree is kept or removed by the run status and the flags:

- `completed` and `cancelled` runs remove the worktree by default; `--keep` keeps it.
- `failed` runs keep the worktree by default, with or without `--keep`.
- `--discard` removes the worktree whatever the status.

A `paused` run always keeps its worktree so `dovo resume` can reopen it, and so does a run whose `--auto-apply` hit a conflict, because the worktree holds the only copy of the work that could not be applied. `--discard` does not override either.

A kept failed worktree prints where it is and how to inspect or remove it:

```text
Worktree: Kept at .dovo/worktrees/blueprint_a3cb925b (branch dovo/blueprint_a3cb925b)
Fix:
- Inspect it with 'git -C .dovo/worktrees/blueprint_a3cb925b status'
- Remove it with 'dovo worktree delete blueprint_a3cb925b'
```

Kept worktrees count toward `worktree.max_active_worktrees`; when the limit is reached a new run fails with a `Fix:` naming `dovo worktree delete`. Pass `--discard` in CI and scripts to restore the old cleanup of failed runs. If removal fails, the run status is unchanged and a `Failed to remove worktree '<path>': <detail>` warning is added.

The `path` of a `cleanup` event (including with `--format json`) is relative to the working directory when the worktree is inside it and absolute otherwise; the `ready` event keeps the absolute path.

### Behavior

1. **Initialization**: When the workspace has no `.dovo/project.json`, `dovo run` initializes it first, as `dovo init` would, and prints nothing when that succeeds. When it fails (for example outside a Git repository, or in a directory whose `.dovo` is `DOVO_HOME`), it prints the same error and fix as `dovo init` and exits `1` without running the blueprint; with `--format json` it writes them as one [`error` event](../reference/json-output.md#the-error-event) instead of a `run.result`. It first undoes what the failed attempt wrote, leaving `.dovo/` as it was before, so the next `dovo run` tries again. Unlike `dovo run`, `dovo init` leaves a failed attempt's files in place.
2. **Resolution and validation**: Looks up `<name>` in the blueprint catalog and checks the blueprint it finds exactly as [`dovo blueprint validate <name>`](blueprint.md#dovo-blueprint-validate) does: the same checks, `error_code`, messages, order, warnings and remediations. A file in the repository named like the blueprint is never what gets checked. When validation reports errors, `dovo run` prints a `Run Failed` panel listing every error and exits `1` before it creates a session or a worktree, so no step runs (see [Validation failures](#validation-failures)). Each validation warning is one of the run's warnings: it prints once, as `Warning: <text>`, before the outcome, and the run continues.
3. **Guards and branches**: A step or loop whose `when` list fails is skipped and prints `[<n>/<total>] <name> — skipped (when: <expression> → FALSE)`, or `→ UNRESOLVED` when the expression referenced a value that does not exist. A step inside a loop's `do` counts its position in `do`. With `--format json` the line is a `run.step_skipped` event carrying `idx`, `total`, `step_id`, `name`, `cause` and `expression`. An `if` block prints `[<n>/<total>] <name> — <branch> (if: <expression> → TRUE|FALSE|UNRESOLVED; ...)` once, when it chooses its branch, every expression in the order written; with `--format json` that is a `run.if_branch` event carrying `idx`, `total`, `block_id`, `name`, `branch` and `conditions`. A step inside a branch shows its block's position. A loop that sets `name` shows it instead of its id on its progress lines. See [`when` Guards](../reference/step-schema.md#when-guards) and [`if` Block](../reference/step-schema.md#if-block).
4. **Execution**: Runs the blueprint. Before it runs, `dovo run` settles sessions left `running` by a `dovo` process that no longer exists: it marks one `paused` when it saved its progress, its worktree still exists if it used one, and none of its processes is still running, and marks every other one `failed`. It adds `Paused 1 interrupted session; continue it with 'dovo resume <session_id>'.`, `Marked 1 interrupted session failed (session_id: <id>).`, or both sentences, paused first, to its warnings. If another `dovo` process holds the workspace lock until the wait times out, `dovo run` stops with a Workspace Lock Timeout panel, or with `--format json` an `error` event with `error_code` `WORKSPACE_LOCK_TIMEOUT` (see [JSON Output](../reference/json-output.md#the-error-event)), and exits `1`.
5. **Agent steps**: An agent step sends its `prompt` to the provider and applies any change inside the worktree. Under `--no-worktree`, or a resumed in-place run, it fails with `Agent steps require an active git worktree.` Its stdout is one JSON object (`status`, `summary`, `unfixable_reason`, `touched_files`); see [Agent Providers](../guides/agent-providers.md).
6. **Secret masking**: Streamed step output, the per-step capture, and failure messages mask secret values: environment variables whose names end in `_KEY`, `_TOKEN`, `_SECRET`, `_PASSWORD`, or `_AUTH` (values of 6+ characters), matching keys in the repository `.env`, the step's own `env:` entries under those same name suffixes and 6+ character rule, and common credential formats (GitHub tokens, Anthropic keys, AWS access key IDs). Names listed under `environment.sensitive_variables` in `.dovo/config.json` are also masked with no length floor, including when set only in a step's `env:`; see [`dovo config`](config.md). A masked value prints as `[REDACTED:<NAME>]` or `[REDACTED]`. Assertions still evaluate the raw output.
7. **Environment flags**: `--env-mode` and `--env-passthrough` apply only to this invocation, are never written to config or run state, and are validated before any step starts. An invalid `--env-passthrough` entry fails the run with `error_code` `AGENT_ENV_OVERRIDE_INVALID` and exits `2`; an `--env-mode` value other than `allowlist` or `inherit` is a usage error, also exit `2`. See [Agent Providers](../guides/agent-providers.md#subprocess-environment).
8. **Diagnostic logs**: When a run hits an unexpected error, Dovo writes a [diagnostic log](doctor.md#diagnostic-logs) record and, in terminal output, prints one `Details logged: <path> (event <event_id>)` line per record after the run's warnings and outcome. This covers loading the session record, saving the run's state or status, saving its diff, creating, applying (with `--auto-apply`) or removing its worktree, reconciling interrupted sessions, loading the blueprint (including snapshotting its definitions), and a step launching its process, reading its output, calling its agent provider, publishing its artifacts, resolving its `uses:` step, checking an agent's edit or preparing its private scratch, including when the operating system refuses to create that scratch. The run's errors and warnings, each step's error and the saved session hold the message alone, and a recorded step failure adds no `Details logged:` warning; the step's own warnings, such as a failed optional artifact publish, still appear. When loading the blueprint or the run itself hits any other unexpected error, the run fails with `Unexpected error during engine.run: <error>`; once the run has created its session, that session is recorded `failed` and the run result names it. With `--format json` the records are the run result's `diagnostics`, each with its `event_id` and `log_path`, and no error or warning contains `Details logged:` text. When checking an agent's edit fails, the rejected edit is saved and discarded, or left in the worktree when it cannot be saved.
9. **Run result**: Every `dovo run` ends with one run result, printed after every other line (see [Run result](#run-result)), unless a failure stops it outside the run (see [JSON Output](../reference/json-output.md#the-error-event)).
10. **Exit codes**: see [Exit codes](#exit-codes).

### Step failure codes

With `--format json`, each step that finishes prints a `run.step_done` event carrying `idx`, `total`, `step_id`, `ok`, `exit_code`, `duration_seconds`, `error_message` and `error_code`; `dovo resume` prints the same event. `error_code` is `null` for a step that completed. A failed step carries one of the codes below, or, when it failed because something it used failed, that failure's code: `CATALOG_NOT_FOUND` for a `uses:` step that does not exist, an `ARTIFACT_*` code for an artifact that cannot be published, downloaded or inlined, an `ASK_*` code for an `ask` prompt that cannot be built, or, for an agent step, the `PATCH_*` code its edit failed validation with (`PATCH_FILE_COUNT_EXCEEDED` for an edit over `max_files`, for example), or the `GIT_*` code of a git command on its worktree that failed (`GIT_TIMEOUT` when it timed out, `GIT_FAILED` for most other failures). A step continued past with `on_failure: continue`, or with `continue` at a failure prompt, keeps its code, and a retried step carries the code of its last attempt. [`dovo session show`](session.md#dovo-session-show) reports the same code for each step.

| Code | Cause |
| --- | --- |
| `STEP_WORKTREE_MISSING` | The step's working directory does not exist or is not a directory, or an agent step ran without a worktree (`--no-worktree`). |
| `STEP_DEFINITION_INVALID` | The step cannot run as written: an unsupported type, a `command` or `internal` step with no command, a `script` step with no `script_path`, an unknown internal command, or an `ask` step with no interaction. |
| `STEP_SCRIPT_NOT_FOUND` | The file named by `script_path` does not exist. |
| `STEP_LAUNCH_FAILED` | The command or script could not be started. |
| `STEP_OUTPUT_READ_FAILED` | Reading the command's or script's output failed. |
| `STEP_TIMEOUT` | The step ran longer than its `timeout_seconds`. |
| `STEP_COMMAND_FAILED` | The command or script exited with a non-zero code. |
| `STEP_ASSERT_FAILED` | One of the step's `assert` checks failed. |
| `STEP_SESSION_MISSING` | An `artifacts.upload` or `artifacts.download` step ran with no active session. |
| `STEP_INPUT_INVALID` | An `artifacts.upload` or `artifacts.download` step is missing `ARTIFACT_NAME`, `ARTIFACT_PATH` or `ARTIFACT_DEST`, or its `ARTIFACT_REVISION` is not a positive integer. |
| `STEP_ANSWER_FAILED` | An `ask` step was answered with an option marked `fail: true`. |
| `STEP_AGENT_SETTINGS_MISSING` | An agent step ran with no resolved agent settings. |
| `STEP_PROMPT_EMPTY` | An agent step's prompt is blank after interpolation. |
| `AGENT_SCRATCH_UNAVAILABLE` | The agent step's private scratch directory could not be prepared. |
| `AGENT_PROVIDER_UNSUPPORTED` | The agent step's provider is not a supported provider. |
| `AGENT_TIMEOUT` | The agent ran longer than the step's `timeout_seconds`. |
| `AGENT_TASK_UNFIXABLE` | The agent reported the task unfixable. |
| `AGENT_TASK_BLOCKED` | The agent was blocked by its tool permissions and made no change. |
| `AGENT_PROVIDER_FAILED` | Any other agent provider failure, such as a missing credential, a provider that exited with an error, or an unreadable unfixable report. |
| `INTERNAL_ERROR` | An internal command, or an agent provider, failed with an unexpected error, an agent step's output could not be delivered, or the step itself failed with an unexpected error, such as writing bytes that are not UTF-8 to `$DOVO_OUTPUT`. Its `error_message` is `Unexpected error during engine.step: <error>`. Like any failed step it follows its `on_failure` policy, and `dovo resume` does not run it again. |

### Run result

Every `dovo run` that gets past initialization ends with one run result, after every other line, unless a configuration that does not load or a workspace lock timeout stops it first: when it completes, pauses, fails or is cancelled, including when an unexpected error stops it. Once the run has created its session, the run result names that session, and the session and every step and loop that had started end with a final status, never `running`. In the terminal it prints each warning as a yellow `Warning: <text>` line, then the outcome, then one `Details logged: <path> (event <event_id>)` line per diagnostic log record. The outcome is one of:

- `Blueprint Run Completed: <blueprint_name> (session: <session_id>, status: completed)` for a completed run.
- `Blueprint paused; checkpoint saved.` for a run that paused without failing, such as a `--no-tty` run at an `ask` prompt. The answer hint, `Run paused at prompt '<id>'. Answer with 'dovo resume <session_id> --answer <id>=<value>'.` (or `Resume with 'dovo resume <session_id>'.` in an interactive terminal), prints just before it, and also before the `Run Cancelled` panel of a run interrupted at an `ask` prompt.
- A `Run Cancelled` panel for a run you interrupted, holding `Execution cancelled by user.`, or the failed step's message when you interrupted its failure prompt.
- A `Run Failed` panel for any other failure, with its errors separated by a blank line and a `Fix:` block when the failure has remediations.

With `--format json` the run result is the last line, a `run.result` event. Its payload holds:

| Key | Value |
| --- | --- |
| `ok` | `true` exactly when `error_code` is `null`. A paused run is `ok`. |
| `error_code` | Why the run failed, or `null`. An interrupted run is `RUN_CANCELLED`, or `SESSION_PROCESS_STOP_FAILED` when a process group it started was still running after the stop. |
| `errors` | The failure's messages. |
| `warnings` | Every warning of the run, including validation warnings. |
| `remediations` | The failure's fixes, each with a `message` and a `command` or `null`. |
| `diagnostics` | The run's diagnostic log records, each with an `event_id` and a `log_path`. |
| `session_id`, `blueprint_name` | The session the run created, or `null` when it created none. |
| `session_status` | The session's own status (`completed`, `paused`, `failed` or `cancelled`), or `null` when it created none. A failure is reported by `error_code`, never by this status. |
| `paused_prompt_id` | The `ask` step the run paused at, or `null`. |

```json
{"version": 1, "event_type": "run.result", "payload": {"ok": true, "error_code": null, "errors": [], "warnings": [], "remediations": [], "diagnostics": [], "session_id": "blueprint_a3cb925b", "blueprint_name": "deploy", "session_status": "completed", "paused_prompt_id": null}}
```

When a workspace lock timeout stops the run while it removes its worktree, the invocation ends with that lock timeout failure only: the Workspace Lock Timeout panel, or with `--format json` the `error` event, with no run result, and exit `1`. [`dovo session show`](session.md) still reports how the run ended.

### Validation failures

A blueprint with two steps that share the id `a` stops before any step:

```text
Running blueprint 'deploy'...
╭──────────────────────────────── Run Failed ─────────────────────────────────╮
│ Step id 'a' is used by more than one step.                                  │
│ Fix:                                                                        │
│ - Resolve: Step id 'a' is used by more than one step.                       │
╰─────────────────────────────────────────────────────────────────────────────╯
```

A schema error names the step by its position in `steps`, as in `steps.0: Value error, Agent steps must specify a non-empty 'prompt' string.`

With `--format json`, the run result carries the `error_code`, `errors` (one entry per error), `warnings` and `remediations` that `dovo blueprint validate` reports, and every session field is `null`:

```json
{"version": 1, "event_type": "run.result", "payload": {"ok": false, "error_code": "CATALOG_SCHEMA_INVALID", "errors": ["Step id 'a' is used by more than one step."], "warnings": [], "remediations": [{"message": "Resolve: Step id 'a' is used by more than one step.", "command": null}], "diagnostics": [], "session_id": null, "blueprint_name": null, "session_status": null, "paused_prompt_id": null}}
```

### Exit codes

`dovo run` exits by its run result's `error_code`:

- `0`: the run completed, or paused with its state saved (for example a `--no-tty` run at an `ask` step).
- `2`: an invalid `--env-passthrough` entry (`AGENT_ENV_OVERRIDE_INVALID`), or a usage error such as `--keep` with `--discard` or an `--env-mode` value it does not accept. A usage error prints a usage message, never a JSON event.
- `130` for Ctrl-C (`SIGINT`), `143` for `SIGTERM` and `129` for a closed terminal (`SIGHUP`), `128` plus the signal number (`RUN_CANCELLED`): `dovo run` finishes its cleanup, releases the workspace lock and prints its run result, then ends by the same signal, so a shell loop stops. A run interrupted while a step runs stops the step's processes and waits up to 5 seconds for them to exit. It is then recorded `cancelled`, with every step and loop that had started, and prints its `Run Cancelled` outcome first; its worktree is removed unless `--keep` was given. A run interrupted while it sets up, creates its worktree, applies its changes with `--auto-apply`, saves its diff or removes its worktree is recorded and reported the same way; one interrupted while it creates or removes its worktree may leave that worktree behind. When a process group is still running after that wait, the run and the steps and loops that had started are recorded `failed` instead, with `SESSION_PROCESS_STOP_FAILED` and a `Run Failed` panel naming each group and the `kill -9 -<pgid>` command that stops it; the run still ends by the signal. A run interrupted at a `prompt_user` failure prompt, an `ask` prompt or a loop `max_iterations` prompt stays paused and prints `Run Cancelled`.
- `1`: every other failure, including a failed run, a blueprint that fails validation, a failed automatic initialization, a configuration that does not load, a workspace lock timeout and an unexpected error.

See [JSON Output](../reference/json-output.md#exit-codes) for the rule every command follows and for failures reported outside the run result.

## Examples

Run a blueprint:

```bash
dovo run build-task
```

Run a blueprint in-place without worktree:

```bash
dovo run release-flow --no-worktree
```

Pass declared blueprint inputs:

```bash
dovo run test-suite --target src/dovo --verbose true
```

Run non-interactively in CI:

```bash
dovo run lint-all --no-tty
```
