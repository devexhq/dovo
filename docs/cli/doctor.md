# `dovo doctor`

The `dovo doctor` command runs the registered diagnostic checks and prints a scannable workspace health report covering Git, configuration, filesystem permissions, worktree references, environment binaries, and agent setup.

`dovo doctor` requires `dovo init` first: without a valid `.dovo/project.json` it exits `1` with `Workspace is not initialized` and a `dovo init` prompt. Once initialized, it still runs and reports when `config.json` is missing or broken.

## Usage

```bash
dovo doctor [--category <name>] [--format terminal|json]
```

## Options

| Flag | Description |
| --- | --- |
| `--category <git\|config\|filesystem\|worktree\|agent\|environment>` | Restrict execution to a single check category. Defaults to running every registered check. |
| `--format <terminal\|json>` | Presentation format (`terminal` or `json`). Defaults to `terminal`. |

## Description

`dovo doctor` runs every registered diagnostic check (or, with `--category`, only the checks in that category) and reports one row per check: its id, category, status, and message. Below the table it prints a one-line summary of how many checks are `OK`, `WARNING`, or `FAILED`, and — when any check surfaced a remediation — a `Fixes:` section listing one actionable step per check.

The command exits `1` when at least one check has status `FAILED`, and `0` otherwise (including when checks have `WARNING` or `SKIPPED` status). Passing an unrecognized `--category` value exits `2` before any check runs.

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
└──────────────┴─────────────┴────────┴──────────────────────────────────────────────────────────┘
6 checks: 6 ok, 0 warning, 0 failed (8.4ms)
```

The default provider (`copilot`) requires `gh` on `PATH` and a `GH_TOKEN` or `GITHUB_TOKEN` credential; without them `env.binaries` warns and `agent.setup` fails.

### Warnings present output

```text
Dovo Doctor Report
┌──────────────┬─────────────┬─────────┬────────────────────────────────────────────────────┐
│ Check        │ Category    │ Status  │ Message                                              │
├──────────────┼─────────────┼─────────┼────────────────────────────────────────────────────┤
│ git.repo     │ git         │ OK      │ Git repository detected at '/repo' on branch 'main'. │
│ agent.setup  │ agent       │ WARNING │ Agent provider 'copilot' has no model configured.    │
└──────────────┴─────────────┴─────────┴────────────────────────────────────────────────────┘
2 checks: 1 ok, 1 warning, 0 failed (4.1ms)

Fixes:
  • agent.setup: Configure `agent.model` in `.dovo/config.json`
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
2 checks: 1 ok, 0 warning, 1 failed (3.2ms)

Fixes:
  • config.schema: Run `dovo init` to create `.dovo/config.json`
```

Running `dovo doctor` against this workspace exits with status code `1`.

### JSON structured output

```bash
dovo doctor --format json
```

Emits one JSON payload for automation:

```json
{"event_type": "DiagnosticsReport", "payload": {"ok": false, "has_warnings": true, "workspace_root": "/abs/path/to/repo", "total_duration_ms": 12.4, "checks": [{"check_id": "git.repo", "name": "Git Repository Check", "category": "git", "status": "ok", "message": "Git repository detected at '/abs/path/to/repo' on branch 'main'.", "details": {"root": "/abs/path/to/repo", "branch": "main"}, "duration_ms": 2.1, "error_code": null, "errors": [], "warnings": [], "remediations": []}, {"check_id": "agent.setup", "name": "Agent Setup Check", "category": "agent", "status": "failed", "message": "Missing required credential for agent provider 'copilot': GH_TOKEN or GITHUB_TOKEN.", "details": {"provider": "copilot", "missing_env_var": "GH_TOKEN or GITHUB_TOKEN"}, "duration_ms": 0.3, "error_code": "DOCTOR_AGENT_KEY_MISSING", "errors": ["Missing required credential for agent provider 'copilot': GH_TOKEN or GITHUB_TOKEN."], "warnings": [], "remediations": ["Export GH_TOKEN or GITHUB_TOKEN"]}]}}
```
