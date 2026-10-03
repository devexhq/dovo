# Project Config Schema Reference

This reference documents the complete JSON schema for `.dovo/config.json` (Version 1).

---

## Canonical V1 Configuration Structure

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
* `ignore_global_root_error` *(boolean)*: Controls global-root validation behavior (default: `false`).

### 2. `project`
* `name` *(string)*: Project identifier. Defaults to directory name or `"unnamed_project"`.
* `initialized_at` *(string \| null)*: Unrestricted creation-timestamp string or `null`; the schema does not validate ISO 8601 format.

### 3. `worktree`
* `base_ref` *(string)*: Base Git reference to branch from (default: `"HEAD"`).
* `max_active_worktrees` *(integer)*: Maximum allowed concurrent worktrees (default: `3`).
* `default_timeout_seconds` *(integer)*: Accepted worktree timeout setting (default: `900`); the current runtime does not consume it.

### 4. `agent`
* `provider` *(string)*: Schema-accepted value: `local`, `ollama`, `cursor`, `gemini`, `copilot`, `openai`, `anthropic`, `azure_openai`, or `custom`. Runtime adapters are only `local`, `ollama`, `cursor`, `gemini`, and `copilot`.
* `model` *(string \| null)*: Agent model metadata (default: `null`).
* `endpoint` *(string \| null)*: Custom API endpoint URL (e.g. for Ollama).
* `temperature` *(number)*: Sampling temperature (default: `0.2`).
* `max_tokens` *(integer)*: Maximum generation tokens (default: `4096`).

### 5. `history`
* `save_attempt_logs` *(boolean)*: Accepted history setting (default: `true`); currently stored but not applied as a runtime switch.
* `save_agent_payloads` *(boolean)*: Accepted history setting (default: `true`); currently stored but not applied as a runtime switch.
* `save_final_diff` *(boolean)*: Accepted history setting (default: `true`); currently stored but not applied as a runtime switch.
* `max_sessions` *(integer)*: Accepted history retention setting (default: `1000`); currently stored but not applied as a retention limit.

### 6. `doctor`
* `check_git` *(boolean)*: Verify Git binary presence and repository state (default: `true`).
* `check_paths_writable` *(boolean)*: Verify storage writability (default: `true`).
* `check_config_schema` *(boolean)*: Check configuration integrity (default: `true`).
* `check_stale_worktrees` *(boolean)*: Detect abandoned worktrees (default: `true`).
* `check_required_binaries` *(boolean)*: Verify required tool binaries (default: `true`).

### 7. `prune`
* `remove_stale_worktrees` *(boolean)*: Accepted prune setting (default: `true`); currently stored but not applied as an automatic runtime policy.
* `remove_orphaned_worktrees` *(boolean)*: Accepted prune setting (default: `true`); currently stored but not applied as an automatic runtime policy.
* `remove_expired_artifacts` *(boolean)*: Accepted prune setting (default: `false`); currently stored but not applied as an automatic runtime policy.
* `artifact_ttl_days` *(integer)*: Accepted artifact retention setting (default: `30`); currently stored but not applied as an automatic runtime policy.

### 8. `telemetry`
* `enabled` *(boolean)*: Accepted telemetry setting (default: `false`); currently stored but not applied as a runtime switch.

### 9. `concurrency`
* `lock_timeout_seconds` *(number)*: Lock-acquisition timeout in seconds (default: `30.0`).
