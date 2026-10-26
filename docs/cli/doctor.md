# `dovo doctor`

The `dovo doctor` command runs the registered diagnostic checks and prints a scannable workspace health report covering Git, configuration, filesystem permissions, worktree references, environment binaries, agent setup, and artifact revision integrity.

`dovo doctor` requires `dovo init` first: without a valid `.dovo/project.json` it exits `1` with `Workspace is not initialized` and a `dovo init` prompt. Once initialized, it still runs and reports when `config.json` is missing or broken.

## Usage

```bash
dovo doctor [--category <name>] [--format terminal|json]
```

## Options

| Flag | Description |
| --- | --- |
| `--category <git\|config\|filesystem\|worktree\|agent\|environment\|artifacts>` | Restrict execution to a single check category. Defaults to running every registered check. |
| `--format <terminal\|json>` | Presentation format (`terminal` or `json`). Defaults to `terminal`. |

## Description

`dovo doctor` runs every registered diagnostic check (or, with `--category`, only the checks in that category) and reports one row per check: its id, category, status, and message. Below the table it prints a one-line summary of how many checks are `OK`, `WARNING`, or `FAILED` and the total run time in seconds, and — when any check surfaced a remediation — a `Fix:` block with one line per remediation, in check order.

The command exits `1` when at least one check has status `FAILED`, and `0` otherwise (including when checks have `WARNING` or `SKIPPED` status). Passing an unrecognized `--category` value exits `2` before any check runs.

## Diagnostic logs

When a catalog, worktree, session, doctor or init operation catches an unexpected error, Dovo writes one JSON file to `$DOVO_HOME/logs/<YYYY-MM-DD>/<event_id>.json`. With no `DOVO_HOME` set, that is `~/.dovo/logs/`. The date is your local date, and the file is readable only by you (mode `0600`). Catalog operations are listing, showing, creating, deleting and validating blueprints and steps, reading a catalog index, and loading a blueprint or step definition during `dovo run`; an operating-system error while writing or deleting a catalog file, writing an index, or reading a shown file, is recorded too. Worktree operations are creating a worktree, applying a worktree, showing its diff, pruning stale worktrees and scanning for them during `dovo doctor`, and removing a worktree, through `dovo worktree delete`, `dovo worktree apply --delete` or at the end of `dovo run`. Session operations are starting, pausing, resuming and finishing a run with `dovo run` or `dovo session resume`, including persisting its status, saving its diff and removing its workspace; running a step: launching its process, reading its output, calling an agent provider and publishing its artifacts; and reconciling interrupted sessions at the start of `dovo run`, `dovo status` and `dovo session list`. Doctor operations are running doctor checks. Init operations are setting up the project identity with `dovo init`, or when `dovo run` initializes the workspace; an operating-system error (for example, a permission error) while reading or writing `.dovo/project.json` there is recorded too. Dovo also records the operating-system error when a command that needs an initialized workspace cannot read `.dovo/project.json` because of one. Each file holds one JSON object with these fields:

| Field | Description |
| --- | --- |
| `event_id` | Identifier of the form `exc_<12 hex characters>`; it is also the file name. |
| `timestamp` | Local ISO-8601 time with its UTC offset, to the second. |
| `level` | `error` for a failure Dovo reported, `debug` for one it recovered from, such as falling back to the built-in catalog skeleton when the packaged template cannot be read, removing a worktree directory directly after `git worktree remove` fails, running doctor checks with default settings when `config.json` cannot be loaded, or a run observer that fails to display progress. |
| `operation` | The internal operation that failed, for example `catalog.load_definition`. |
| `command` | The first CLI word of the invocation, for example `blueprint`, or `null` outside the CLI. |
| `context` | A few identifying values for the operation, such as the item type and name. |
| `exception_type` | The exception class name. |
| `exception_message` | The exception message. |
| `formatted_traceback` | The full traceback, including every chained exception. |

Dovo keeps the 14 newest date directories and deletes older ones each time it writes a record. Other files and directories under `logs/` are left alone. If the file cannot be written, the command carries on and reports its original error.

When an `error` record is written, Dovo names it in one line:

```text
Details logged: /home/you/.dovo/logs/2026-10-19/exc_3f9a1c07b2de.json (event exc_3f9a1c07b2de)
```

For a catalog, init, doctor-check, reconciliation, `dovo doctor` worktree scan or `dovo worktree prune` record, the error or warning message is unchanged. Terminal output prints the line after the command's errors and warnings; for `dovo run`, it prints after the run's warnings and outcome. With `--format json`, the command's payload lists each such record in `diagnostics` as `{"event_id": …, "log_path": …}`; for `dovo doctor`, a check's record is listed both in that check's `diagnostics` and in the top-level `diagnostics`. `dovo run` reports these records in terminal output only. For other worktree operations and a run's own state and steps, the line still ends the error or warning message, in terminal and `--format json` output alike. For a failed step, the line is usually added to the run's warnings rather than the step's error message; [`dovo run`](run.md) lists the failures that put it in the step's error.

Two cases have no message to carry the line: a failure removing a run's worktree after its session storage link could not be created, where the run reports only the storage link error; and a command that needs an initialized workspace and cannot read `.dovo/project.json`, whose not-initialized error keeps its usual message.

To report the problem, open a bug report that quotes the event id, and attach that file after reviewing it.

Records stay on your machine; Dovo never uploads them. They can contain file paths and error message text. Values of secret environment variables (names ending in `_KEY`, `_TOKEN`, `_SECRET`, `_PASSWORD` or `_AUTH`, with values of at least six characters) and known credential formats are masked as `[REDACTED:<name>]` or `[REDACTED]` in `context` values, `exception_message` and `formatted_traceback`. Review a record before you share it.

## Examples

```bash
dovo doctor
```

### Healthy workspace output

```text
Dovo Doctor Report
┌──────────────┬─────────────┬────────┬──────────────────────────────────────────────────────────┐
│ Check        │ Category    │ Status │ Message                                                    │
├──────────────┼─────────────┼────────┼──────────────────────────────────────────────────────────┤
│ git.repo     │ git         │ OK     │ Git repository detected at '/repo' on branch 'main'.       │
│ config.schema│ config      │ OK     │ `.dovo/config.json` is present and passes the schema... │
│ filesystem.writable │ filesystem │ OK │ All configured workspace paths are writable.               │
│ worktree.refs │ worktree     │ OK     │ 2 worktree(es) verified against database and Git worktree... │
│ env.binaries │ environment │ OK     │ 2 required binary(s) verified on PATH.                     │
│ agent.setup  │ agent       │ OK     │ Agent provider 'copilot' is configured with model '...'.   │
│ artifacts.integrity │ artifacts │ OK │ 3 artifact revision(s) verified against the database.      │
└──────────────┴─────────────┴────────┴──────────────────────────────────────────────────────────┘
7 checks: 7 ok, 0 warning, 0 failed (0.008s)
```

`artifacts.integrity` warns when an artifact revision directory has no database row (`DOCTOR_ARTIFACT_ORPHAN_DIR`) or a row's directory is missing (`DOCTOR_ARTIFACT_MISSING_DIR`); it never repairs either.

The default provider (`copilot`) requires `gh` on `PATH` and a `GH_TOKEN` or `GITHUB_TOKEN` credential; without them `env.binaries` warns and `agent.setup` fails. For `claude`, `agent.setup` passes with a `CLAUDE_CODE_OAUTH_TOKEN`, a `claude auth login` session or `ANTHROPIC_API_KEY`, and its message and `details.auth` name the mode in use (`cli session`, `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY`).

### Warnings present output

```text
Dovo Doctor Report
┌──────────────┬─────────────┬─────────┬────────────────────────────────────────────────────┐
│ Check        │ Category    │ Status  │ Message                                              │
├──────────────┼─────────────┼─────────┼────────────────────────────────────────────────────┤
│ git.repo     │ git         │ OK      │ Git repository detected at '/repo' on branch 'main'. │
│ agent.setup  │ agent       │ WARNING │ Agent provider 'copilot' has no model configured.    │
└──────────────┴─────────────┴─────────┴────────────────────────────────────────────────────┘
2 checks: 1 ok, 1 warning, 0 failed (0.004s)
Fix:
- Run 'dovo config set agents.copilot.model "<model>"' to set 'agents.copilot.model' in '.dovo/config.json' to a model supported by that provider.
```

### Failures present output

```text
Dovo Doctor Report
┌──────────────┬─────────┬────────┬──────────────────────────────────────────────────────────────┐
│ Check        │ Category│ Status │ Message                                                        │
├──────────────┼─────────┼────────┼──────────────────────────────────────────────────────────────┤
│ git.repo     │ git     │ OK     │ Git repository detected at '/repo' on branch 'main'.          │
│ config.schema│ config  │ FAILED │ Configuration file not found at '/repo/.dovo/config.json'. │
└──────────────┴─────────┴────────┴──────────────────────────────────────────────────────────────┘
2 checks: 1 ok, 0 warning, 1 failed (0.003s)
Fix:
- Run 'dovo init' to create '.dovo/config.json' and the required workspace directory structure.
```

Running `dovo doctor` against this workspace exits with status code `1`.

### JSON structured output

```bash
dovo doctor --format json
```

Emits one JSON payload for automation:

```json
{"event_type": "DiagnosticsReport", "payload": {"ok": false, "has_warnings": true, "workspace_root": "/abs/path/to/repo", "duration_seconds": 0.0124, "checks": [{"check_id": "git.repo", "name": "Git Repository Check", "category": "git", "status": "ok", "message": "Git repository detected at '/abs/path/to/repo' on branch 'main'.", "details": {"root": "/abs/path/to/repo", "branch": "main"}, "duration_seconds": 0.0021, "error_code": null, "errors": [], "warnings": [], "remediations": [], "diagnostics": []}, {"check_id": "agent.setup", "name": "Agent Setup Check", "category": "agent", "status": "failed", "message": "Missing required credential for agent provider 'copilot': GH_TOKEN or GITHUB_TOKEN.", "details": {"provider": "copilot", "missing_env_var": "GH_TOKEN or GITHUB_TOKEN", "credential_envs": ["GH_TOKEN", "GITHUB_TOKEN"]}, "duration_seconds": 0.0003, "error_code": "DOCTOR_AGENT_KEY_MISSING", "errors": ["Missing required credential for agent provider 'copilot': GH_TOKEN or GITHUB_TOKEN."], "warnings": [], "remediations": [{"message": "Run 'export GH_TOKEN=\"<your-api-key>\"' in your active shell, or set 'GH_TOKEN' in your '.env' file, so the configured agent provider can authenticate.", "command": "export GH_TOKEN=\"<your-api-key>\""}, {"message": "Run 'export GITHUB_TOKEN=\"<your-api-key>\"' in your active shell, or set 'GITHUB_TOKEN' in your '.env' file, so the configured agent provider can authenticate.", "command": "export GITHUB_TOKEN=\"<your-api-key>\""}], "diagnostics": []}], "diagnostics": []}}
```

`duration_seconds` on the payload is the wall time of the whole run, and `duration_seconds` on each check is that check's run time, both in seconds as measured by Dovo. A check skipped by configuration reports `0.0`.
