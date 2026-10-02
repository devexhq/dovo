# Workspace Configuration

Worktree (`wt`) operates with a local `.worktree/` directory in your Git repository root. This directory contains the configuration file (`config.json`) and the blueprint catalog (`catalog/`). Run and session state lives in a centralized SQLite database shared across all projects (under `WORKTREE_HOME` or `~/.worktree` by default), scoped to this project.

---

## Workspace Setup (`wt init`)

Run `wt init` at the root of your Git repository:

```bash
wt init
```

This provisions the local `.worktree/` directory structure:

```text
.worktree/
├── .gitignore          # Local state exclusions
├── .meta/              # Catalog metadata
├── config.json         # Workspace configuration settings
├── project.json        # Project identity
└── catalog/            # Project blueprint definitions
    ├── blueprints/
    └── steps/
```

### Flags & Repair Options

* `--repair`: Non-destructively inserts missing required keys into `.worktree/config.json` while preserving your custom project settings and timestamps.
* `--overwrite`: Completely replaces `.worktree/config.json` with fresh canonical V1 defaults (destructive).

```bash
# Repair an existing config file with updated schema keys
wt init --repair

# Reset configuration to fresh defaults
wt init --overwrite
```

---

## Workspace Status (`wt status`)

Inspect workspace health, configuration, catalog, sandboxes, and recorded sessions:

```bash
wt status
```

Output includes:
* Project configuration validation status.
* Database path and session record counts.
* Active Git worktree sandboxes (`worktree/sandbox-*` branches).

---

## Managing Configuration (`wt config`)

Inspect and modify your Worktree configuration directly using the `wt config` subcommands.

### Configuration Precedence

`wt config show` and blueprint execution (`wt run`/`wt resume`) both resolve the identical four-tier merged configuration: Packaged defaults, then Global (`$WORKTREE_HOME/global/config.json`), User (`$WORKTREE_HOME/user/config.json`), and Repo (`.worktree/config.json`), each tier overriding the fields the previous tiers set. `WORKTREE_HOME` defaults to `~/.worktree` when unset. See [`wt config`](../cli/config.md#configuration-precedence) for the full precedence and error-handling contract.

### Show Effective Configuration

Display normalized effective configuration as formatted JSON:

```bash
wt config show
```

### Update Configuration Values

Set specific configuration keys or nested dot-paths:

```bash
wt config set agent.provider ollama
wt config set agent.model llama3.1
wt config set sandbox.base_ref main
```

### Validate Configuration

Validate `.worktree/config.json` against the schema and semantic rules:

```bash
wt config validate
```

---

## Configuration Overview

Below is the canonical `.worktree/config.json` structure:

```json
{
  "version": 1,
  "project": {
    "name": "my-project",
    "initialized_at": "2026-08-06T00:00:00Z"
  },
  "ignore_global_root_error": false,
  "sandbox": {
    "base_ref": "HEAD",
    "max_active_sandboxes": 3,
    "default_timeout_seconds": 900
  },
  "agent": {
    "provider": "local",
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
    "remove_orphaned_sandboxes": true,
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

The configuration schema accepts `local`, `ollama`, `cursor`, `gemini`, `copilot`, `openai`, `anthropic`, `azure_openai`, and `custom`. Runtime adapter selection supports only `local`, `ollama`, `cursor`, `gemini`, and `copilot`; agent steps invoke the selected adapter inside a Git sandbox. Credentials can be checked by `wt doctor`.

```bash
# Gemini Provider
export GEMINI_API_KEY="AIzaSy..."

# GitHub Copilot Provider
export GITHUB_TOKEN="ghp_..."

# Cursor Provider
export CURSOR_API_KEY="cur_..."

# Ollama Endpoint (Local LLM)
export OLLAMA_HOST="http://localhost:11434"
```

For persistent environment setup, save provider credentials to your local shell profile (`.bashrc` / `.zshrc`) or local `.env` file (ensure `.env` is listed in `.gitignore`).

For the runtime adapter distinction and current limitation, see the [AI Agent Providers Guide](../guides/agent-providers.md).
