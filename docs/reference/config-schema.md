# Project Config Schema Reference

Schema for `.dovo/config.json` (version 1).

---

## Structure

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

---

## Section Breakdown

### 1. Root
* `version` *(integer, required)*: Must be integer `1`.
* `ignore_global_root_error` *(boolean)*: Controls global-root validation (default: `false`).

### 2. `project`
* `name` *(string)*: Project name. Defaults to the directory name, or `"unnamed_project"`.
* `initialized_at` *(string \| null)*: Creation timestamp, or `null`. The format is not validated.

### 3. `worktree`
* `base_ref` *(string)*: Base Git reference to branch from (default: `"HEAD"`).
* `max_active_worktrees` *(integer)*: Maximum allowed concurrent worktrees (default: `3`).
* `default_timeout_seconds` *(integer)*: Currently has no effect (default: `900`).

### 4. `agent`
* `provider` *(string)*: Agent provider. Only `copilot` is accepted (default: `"copilot"`); other values fail validation.
* `model` *(string \| null)*: Model name (default: `null`).
* `endpoint` *(string \| null)*: Custom API endpoint URL (default: `null`).
* `temperature` *(number)*: Sampling temperature (default: `0.2`).
* `max_tokens` *(integer)*: Maximum generation tokens (default: `4096`).
* `env_passthrough` *(array of string)*: Host environment names (or `PREFIX*` patterns) forwarded to the agent subprocess (default: `[]`). A repo-level list replaces a global one rather than merging. See [Agent Providers](../guides/agent-providers.md#subprocess-environment).
* `env_mode` *(string)*: `allowlist` (default) forwards a fixed base set plus your passthrough names; `inherit` forwards the full host environment.
* `tools` *(object, optional)*: Default agent tool policy with the same shape as a step's `tools` (`allow`, `deny`, `allow_all`; see [Step Schema](step-schema.md)). Absent means read and write on the worktree and scratch only (see [Tool Policy](../guides/agent-providers.md#tool-policy)). A higher tier's object replaces a lower tier's instead of merging. A plain string list is rejected.

### 5. `history`
* `save_attempt_logs` *(boolean)*: Save per-attempt stdout/stderr logs (default: `true`). See [`dovo logs`](../cli/logs.md).
* `save_agent_payloads` *(boolean)*: Currently has no effect (default: `true`).
* `save_final_diff` *(boolean)*: Currently has no effect (default: `true`).
* `max_sessions` *(integer)*: Currently has no effect (default: `1000`).

### 6. `doctor`
* `check_git` *(boolean)*: Check the Git binary and repository state (default: `true`).
* `check_paths_writable` *(boolean)*: Check that storage is writable (default: `true`).
* `check_config_schema` *(boolean)*: Check the configuration file (default: `true`).
* `check_stale_worktrees` *(boolean)*: Look for abandoned worktrees (default: `true`).
* `check_required_binaries` *(boolean)*: Check required binaries (default: `true`).

### 7. `prune`
* `remove_stale_worktrees` *(boolean)*: Currently has no effect (default: `true`).
* `remove_orphaned_worktrees` *(boolean)*: Currently has no effect (default: `true`).
* `remove_expired_artifacts` *(boolean)*: Whether `dovo artifacts prune` deletes expired artifacts without `--force` (default: `false`).
* `artifact_ttl_days` *(integer)*: Currently has no effect (default: `30`).

### 8. `telemetry`
* `enabled` *(boolean)*: Currently has no effect (default: `false`).

### 9. `concurrency`
* `lock_timeout_seconds` *(number)*: Seconds to wait for a workspace lock (default: `30.0`).

### 10. `environment`
* `sensitive_variables` *(array of string)*: Environment variable names whose values are masked in output and logs (default: `[]`). Names must match `^[A-Za-z_][A-Za-z0-9_]*$`. See [`dovo config`](../cli/config.md#environmentsensitive_variables).
