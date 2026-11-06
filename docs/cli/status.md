# `dovo status`

`dovo status` shows the workspace's health: Git branch, config validity, worktree usage and catalog contents.

## Usage

```bash
dovo status [--format terminal|json]
```

## Options

| Flag | Description |
| --- | --- |
| `--format <terminal\|json>` | Presentation format (`terminal` or `json`). Defaults to `terminal`. |

## Description

It shows:
- **Project Name**: Name of the configured Dovo project.
- **Config Status**: `ok` with the relative path to `.dovo/config.json` when the config loads, or the config load's error code (for example `CONFIG_JSON_INVALID`) when it does not.
- **Active Git Branch**: Current Git branch (with dirty indicator if uncommitted changes exist), `NOT_A_GIT_REPO` outside a Git repository, or the code of why `git` could not be read: `GIT_TIMEOUT` or `GIT_BINARY_MISSING`.
- **Agent Model**: Configured agent model name.
- **Active Worktrees**: Active worktrees count and concurrency ceiling (`active / max`).
- **Catalog Items**: Total valid blueprints and inventory breakdown (`valid / total`).
- **Warnings**: Configuration warnings and remediation steps. When the config fails to load, its remediations are the ones `dovo config show` reports for the same failure, followed by `git init` outside a Git repository.

Dovo stops any `git` command that runs longer than 120 seconds, or `DOVO_GIT_TIMEOUT_SECONDS` when that is set to a positive number of seconds, up to one day (86400). When `git` times out, `dovo status` reports it as a timeout, not as a directory outside a Git repository: the health is `degraded`, the warnings include `Git timed out after <seconds>s ('git <command>')`, and the fix is to check for git lock files, credential prompts or stuck git processes and retry, or to raise `DOVO_GIT_TIMEOUT_SECONDS`. When `git` is not on `PATH`, the warning names the missing `git` and the fix is to install Git. `git init` is suggested only when `git` answers that the directory is not a repository.

When reading the database fails, the health is `degraded`. When `.dovo/config.json` cannot be read, the health is `degraded` and `config_error_code` is `CONFIG_UNREADABLE`. When a catalog file cannot be read, `dovo status` counts it as an invalid catalog file, so the health is `degraded` and the warnings count it. When the database opens but its worktree records cannot be read, `dovo status` counts the worktree directories instead and warns `Could not read worktrees from the database; counted worktree directories instead.` Each failure `dovo status` reports this way, and a `git` timeout or missing `git`, writes a [diagnostic log](doctor.md#diagnostic-logs) record listed in `diagnostics`. A catalog file that reads but is not a YAML mapping is counted as invalid without a record.

Before it reports, `dovo status` settles sessions left `running` by a `dovo` process that no longer exists: it marks one `paused` when it saved its progress, its worktree still exists if it used one, and none of its processes is still running, and marks every other one `failed`. It adds `Paused 1 interrupted session; continue it with 'dovo resume <session_id>'.`, `Marked 1 interrupted session failed (session_id: <id>).`, or both sentences, paused first, to its warnings. If another `dovo` process holds the workspace lock until the wait times out, `dovo status` stops with a Workspace Lock Timeout panel, or with `--format json` an `error` event with `error_code` `WORKSPACE_LOCK_TIMEOUT` (see [JSON Output](../reference/json-output.md#the-error-event)), and exits `1`.

## Exit codes

- `0`: the report printed, whatever its health (`ok`, `degraded` or `uninitialized`) and whatever the config error code.
- `2`: a usage error, such as an unknown option.
- `1`: a failure reported as an `error` event: a workspace without a valid `.dovo/project.json`, a workspace lock timeout or an unexpected error.

See [JSON Output](../reference/json-output.md#exit-codes) for the rule every command follows.

## Examples

```bash
dovo status
```

### Healthy workspace output

```text
Dovo Workspace Status
┌──────────────────────┬─────────────────────────────┐
│ Property             │ Value                       │
├──────────────────────┼─────────────────────────────┤
│ Project Name         │ dovo                        │
│ Config Status        │ ok (.dovo/config.json)      │
│ Active Git Branch    │ feature/status-cmd          │
│ Agent Model          │ gpt-4.1                     │
│ Active Worktrees     │ 1 / 5 max                   │
│ Catalog Items        │ 2 valid / 2 total           │
└──────────────────────┴─────────────────────────────┘

⚠️ Configuration & Context Warnings:
  • max_active_worktrees (10) is unusually high.
```

### Uninitialized workspace output

`dovo status` requires `dovo init` first: without a valid `.dovo/project.json` it prints a Workspace Not Initialized panel, or with `--format json` an `error` event with `error_code` `WORKSPACE_NOT_INITIALIZED`, whose fix is `dovo init` (see [Workspace not initialized](../reference/json-output.md#workspace-not-initialized)), and exits `1`. When the identity exists but `.dovo/config.json` is missing, the report is:

```text
Dovo Workspace Status (Uninitialized)
┌──────────────────────┬────────────────────────────────────────┐
│ Property             │ Value                                  │
├──────────────────────┼────────────────────────────────────────┤
│ Project Name         │ Uninitialized               │
│ Config Status        │ CONFIG_NOT_FOUND         │
│ Active Git Branch    │ main                                   │
│ Agent Model          │ Not Configured               │
│ Active Worktrees     │ N/A                         │
│ Catalog Items        │ N/A                         │
└──────────────────────┴────────────────────────────────────────┘

⚠️ Configuration & Context Warnings:
  • Dovo workspace is not initialized. Run 'dovo init' to configure.

Fix:
- Run 'dovo init' to create '.dovo/config.json'
```

### Degraded workspace output

When `config.json` is malformed, invalid, or run outside a Git repository:

```text
Dovo Workspace Status (Degraded)
┌──────────────────────┬────────────────────────────────────────┐
│ Property             │ Value                                  │
├──────────────────────┼────────────────────────────────────────┤
│ Project Name         │ Uninitialized               │
│ Config Status        │ CONFIG_JSON_INVALID         │
│ Active Git Branch    │ main                                   │
│ Agent Model          │ Not Configured               │
│ Active Worktrees     │ N/A                         │
│ Catalog Items        │ N/A                         │
└──────────────────────┴────────────────────────────────────────┘

⚠️ Configuration & Context Warnings:
  • Malformed config.json: Expecting property name enclosed in double quotes (line 2 col 1)

Fix:
- Repair JSON syntax, or restore from backup
```

### JSON structured output

```bash
dovo status --format json
```

Emits a JSON payload for automation. It starts with the fields every command result carries: `ok`, `error_code`, `errors`, `warnings`, `remediations` and `diagnostics`. Workspace health is not a failure, so `ok` is `true` and `error_code` is `null` whatever the health, and the payload still lists the warnings and remediations. `status` reports the health, and the table title shows the same value: `uninitialized` exactly when the config load's error code is `CONFIG_NOT_FOUND` or `.dovo/` is absent, `degraded` when the workspace is not a Git repository, `git` could not be read, the config is invalid, the database cannot be opened or the catalog has invalid files, and `ok` otherwise. `config_error_code` is the config load's error code, or `null` when the config loaded. `git_branch` is `null` whenever no branch was read. `git_error_code` is `GIT_TIMEOUT` when `git` timed out or `GIT_BINARY_MISSING` when it is not on `PATH`, and `null` otherwise, including outside a Git repository. `dovo status` exits `0` whatever the config error code:

```json
{"version": 1, "event_type": "status", "payload": {"ok": true, "error_code": null, "errors": [], "warnings": [], "remediations": [], "diagnostics": [], "status": "ok", "root_dir": "/path/to/project", "project_name": "my-project", "config_error_code": null, "config_path_relative": ".dovo/config.json", "git_branch": "main", "git_error_code": null, "git_is_dirty": false, "uncommitted_files": 0, "agent_model": null, "active_worktrees": 1, "max_active_worktrees": 5, "valid_catalog_items": 2, "total_catalog_items": 2, "total_sessions": 3}}
```
