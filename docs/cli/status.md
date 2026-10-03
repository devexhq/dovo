# `dovo status`

The `dovo status` command displays the health and status of the current Dovo workspace, active Git branch, config validity, active sandboxes capacity, and catalog inventory.

## Usage

```bash
dovo status [--format terminal|json]
```

## Options

| Flag | Description |
| --- | --- |
| `--format <terminal\|json>` | Presentation format (`terminal` or `json`). Defaults to `terminal`. |

## Description

`dovo status` provides a scannable dashboard showing:
- **Project Name**: Name of the configured Dovo project.
- **Config Status**: Validation status and relative path to `.dovo/config.json`.
- **Active Git Branch**: Current Git branch (with dirty indicator if uncommitted changes exist).
- **Agent Model**: Configured agent model name.
- **Active Sandboxes**: Active sandboxes count and concurrency ceiling (`active / max max`).
- **Catalog Items**: Total valid blueprints and inventory breakdown (`valid / total`).
- **Warnings**: Actionable developer and workspace configuration warnings.

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
│ Project Name         │ worktree-cli                │
│ Config Status        │ ok (.dovo/config.json)  │
│ Active Git Branch    │ feature/status-cmd          │
│ Agent Model          │ gemini-2.5-flash            │
│ Active Sandboxes     │ 1 / 5 max                   │
│ Catalog Items        │ 2 valid / 2 total           │
└──────────────────────┴─────────────────────────────┘

⚠️ Configuration & Context Warnings:
  • max_active_sandboxes (10) is unusually high.
```

### Uninitialized workspace output

When `.dovo/config.json` is missing or the workspace is uninitialized:

```text
Dovo Workspace Status (Uninitialized)
┌──────────────────────┬────────────────────────────────────────┐
│ Property             │ Value                                  │
├──────────────────────┼────────────────────────────────────────┤
│ Project Name         │ [dim]Uninitialized[/dim]               │
│ Config Status        │ [yellow]CONFIG_NOT_FOUND[/yellow]         │
│ Active Git Branch    │ main                                   │
│ Agent Model          │ [dim]Not Configured[/dim]               │
│ Active Sandboxes     │ [dim]N/A[/dim]                         │
│ Catalog Items        │ [dim]N/A[/dim]                         │
└──────────────────────┴────────────────────────────────────────┘

⚠️ Configuration & Context Warnings:
  • Dovo workspace is not initialized. Run 'dovo init' to configure.

Next Steps & Remediation:
  • Run 'dovo init' to initialize Dovo in this repository.
```

### Degraded workspace output

When `config.json` is malformed, invalid, or run outside a Git repository:

```text
Dovo Workspace Status (Degraded)
┌──────────────────────┬────────────────────────────────────────┐
│ Property             │ Value                                  │
├──────────────────────┼────────────────────────────────────────┤
│ Project Name         │ [dim]Uninitialized[/dim]               │
│ Config Status        │ [red]CONFIG_MALFORMED_JSON[/red]       │
│ Active Git Branch    │ main                                   │
│ Agent Model          │ [dim]Not Configured[/dim]               │
│ Active Sandboxes     │ [dim]N/A[/dim]                         │
│ Catalog Items        │ [dim]N/A[/dim]                         │
└──────────────────────┴────────────────────────────────────────┘

⚠️ Configuration & Context Warnings:
  • Malformed config.json: Expecting property name enclosed in double quotes (line 2 col 1)

Next Steps & Remediation:
  • Repair JSON syntax in .dovo/config.json or restore from backup.
```

### JSON structured output

```bash
dovo status --format json
```

Emits a structured NDJSON payload suitable for automation and GUI integrations:

```json
{"event_type": "DovoStatusResult", "payload": {"health": "ok", "root_dir": "/path/to/project", "project_name": "my-project", "config_status": "ok", "config_path_relative": ".dovo/config.json", "git_branch": "main", "git_is_dirty": false, "uncommitted_files": 0, "agent_model": null, "active_sandboxes": 1, "max_active_sandboxes": 5, "valid_catalog_items": 2, "total_catalog_items": 2, "total_runs": 3, "errors": [], "warnings": [], "remediations": []}}
```
