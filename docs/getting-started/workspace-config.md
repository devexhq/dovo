# Workspace Configuration

Dovo keeps its configuration (`config.json`) and blueprint catalog (`catalog/`) in a `.dovo/` directory at your Git repository root. Run and session history is stored separately, under `DOVO_HOME` (default `~/.dovo`).

---

## Workspace Setup (`dovo init`)

Run `dovo init` at the root of your Git repository:

```bash
dovo init
```

This creates:

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

Every command except `dovo init` requires this step first: without a valid `.dovo/project.json` it exits `1` with `Workspace is not initialized` and a `dovo init` prompt. `dovo run` initializes the workspace automatically.

### Flags & Repair Options

* `--repair`: Adds missing keys to `.dovo/config.json` and keeps your existing settings.
* `--overwrite`: Replaces `.dovo/config.json` with fresh defaults (destructive).

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

Use `dovo config` to inspect and change configuration.

### Configuration Precedence

`dovo config show`, `dovo run` and `dovo resume` all use the same merged configuration. Tiers, lowest to highest precedence: Packaged defaults, Global (`$DOVO_HOME/global/config.json`), User (`$DOVO_HOME/user/config.json`), and Repo (`.dovo/config.json`). `DOVO_HOME` defaults to `~/.dovo`. See [`dovo config`](../cli/config.md#configuration-precedence) for details.

### Show Effective Configuration

Print the effective configuration as JSON:

```bash
dovo config show
```

### Update Configuration Values

Set a key or nested dot-path:

```bash
dovo config set default_agent copilot
dovo config set agents.copilot.model gpt-4.1
dovo config set worktree.base_ref main
```

### Validate Configuration

Check `.dovo/config.json` against the schema:

```bash
dovo config validate
```

---

## Configuration Overview

The default `.dovo/config.json`:

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
  "default_agent": "copilot",
  "agents": {},
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

See the [Project Config Schema](../reference/config-schema.md) for every field.

---

## API Keys & Environment Setup

The supported agent providers are `copilot` and `claude`. `copilot` needs the GitHub CLI (`gh`) on `PATH` and a token. `claude` needs Claude Code (`claude`) on `PATH` and one of `CLAUDE_CODE_OAUTH_TOKEN`, a `claude auth login` session or `ANTHROPIC_API_KEY`. `dovo doctor` reports `DOCTOR_BINARY_MISSING` or `DOCTOR_AGENT_KEY_MISSING` until both are available.

```bash
# GitHub Copilot Provider
export GITHUB_TOKEN="ghp_..."

# Claude Code Provider
export ANTHROPIC_API_KEY="sk-ant-..."
```

To keep the token across sessions, add it to your shell profile (`.bashrc` / `.zshrc`) or a `.env` file that is listed in `.gitignore`.

See [AI Agent Providers](../guides/agent-providers.md) for provider setup.
