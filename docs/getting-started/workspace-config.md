# Workspace Configuration

Dovo (`dovo`) operates with a local `.dovo/` directory in your Git repository root. This directory contains the configuration file (`config.json`) and the blueprint catalog (`catalog/`). Run and session state lives in a centralized SQLite database shared across all projects (under `DOVO_HOME` or `~/.dovo` by default), scoped to this project.

---

## Workspace Setup (`dovo init`)

Run `dovo init` at the root of your Git repository:

```bash
dovo init
```

This provisions the local `.dovo/` directory structure:

```text
.dovo/
├── .gitignore          # Local state exclusions
├── .meta/              # Catalog metadata
├── config.json         # Workspace configuration settings
├── project.json        # Project identity
└── catalog/            # Project blueprint definitions
    ├── blueprints/
    └── steps/
```

### Flags & Repair Options

* `--repair`: Non-destructively inserts missing required keys into `.dovo/config.json` while preserving your custom project settings and timestamps.
* `--overwrite`: Completely replaces `.dovo/config.json` with fresh canonical V1 defaults (destructive).

```bash
# Repair an existing config file with updated schema keys
dovo init --repair

# Reset configuration to fresh defaults
dovo init --overwrite
```

---

## Workspace Status (`dovo status`)

Inspect workspace health, configuration, catalog, worktrees, and recorded sessions:

```bash
dovo status
```

Output includes:
* Project configuration validation status.
* Database path and session record counts.
* Active Git worktrees (`dovo/dovo_*` branches).

---

## Managing Configuration (`dovo config`)

Inspect and modify your Dovo configuration directly using the `dovo config` subcommands.

### Configuration Precedence

`dovo config show` and blueprint execution (`dovo run`/`dovo resume`) both resolve the identical four-tier merged configuration: Packaged defaults, then Global (`$DOVO_HOME/global/config.json`), User (`$DOVO_HOME/user/config.json`), and Repo (`.dovo/config.json`), each tier overriding the fields the previous tiers set. `DOVO_HOME` defaults to `~/.dovo` when unset. See [`dovo config`](../cli/config.md#configuration-precedence) for the full precedence and error-handling contract.

### Show Effective Configuration

Display normalized effective configuration as formatted JSON:

```bash
dovo config show
```

### Update Configuration Values

Set specific configuration keys or nested dot-paths:

```bash
dovo config set agent.provider copilot
dovo config set agent.model gpt-4.1
dovo config set worktree.base_ref main
```

### Validate Configuration

Validate `.dovo/config.json` against the schema and semantic rules:

```bash
dovo config validate
```

---

## Configuration Overview

Below is the canonical `.dovo/config.json` structure:

```json
{
  "version": 1,
  "project": {
    "name": "my-project",
    "initialized_at": "2026-08-06T00:00:00Z"
  },
  "ignore_global_root_error": false,
  "worktree": {
    "base_ref": "HEAD",
    "max_active_worktrees": 3,
    "default_timeout_seconds": 900
  },
  "agent": {
    "provider": "copilot",
    "model": null,
    "endpoint": null,
    "temperature": 0.2,
    "max_tokens": 4096
  },
  "history": {
    "save_attempt_logs": true,
    "save_agent_payloads": true,
    "save_final_diff": true,
    "max_sessions": 1000
  },
  "doctor": {
    "check_git": true,
    "check_paths_writable": true,
    "check_config_schema": true,
    "check_stale_worktrees": true,
    "check_required_binaries": true
  },
  "prune": {
    "remove_stale_worktrees": true,
    "remove_orphaned_worktrees": true,
    "remove_expired_artifacts": false,
    "artifact_ttl_days": 30
  },
  "telemetry": {
    "enabled": false
  },
  "concurrency": {
    "lock_timeout_seconds": 30.0
  }
}
```

For full details on each field and validation rule, see the [Project Config Schema Reference](../reference/config-schema.md).

---

## API Keys & Environment Setup

The configuration schema accepts only `copilot`; agent steps invoke it inside a Git worktree. Credentials can be checked by `dovo doctor`. The default provider requires the GitHub CLI (`gh`) on `PATH`, so `dovo doctor` reports `DOCTOR_BINARY_MISSING` or `DOCTOR_AGENT_KEY_MISSING` until both are available.

```bash
# GitHub Copilot Provider
export GITHUB_TOKEN="ghp_..."
```

For persistent environment setup, save provider credentials to your local shell profile (`.bashrc` / `.zshrc`) or local `.env` file (ensure `.env` is listed in `.gitignore`).

For the runtime adapter distinction and current limitation, see the [AI Agent Providers Guide](../guides/agent-providers.md).
