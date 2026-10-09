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
| `--keep` | Retain the worktree after execution. Each run gets its own worktree at `.dovo/worktrees/<session_id>/` on branch `dovo/<session_id>`, so repeated `--keep` runs of one blueprint do not collide. |
| `--auto-apply` | Automatically apply worktree changes to the main workspace on successful completion. |
| `--agent <name>` | Override the agent provider for this run (model, endpoint, temperature, and max tokens still come from config); an unregistered name fails an agent step with `AGENT_PROVIDER_UNSUPPORTED` and has no effect on command/script steps. |
| `--session-id <id>` | Explicit session identifier. |
| `--no-tty` | Disable interactive prompts; prompt_user failures abort the run instead of blocking for input. |
| `--env-mode <allowlist\|inherit>` | Override `agent.env_mode` for this invocation. |
| `--env-passthrough <name-or-prefix*>` | Forward a host variable name or a prefix ending in a single `*` to agent steps for this invocation; repeatable and appended to `agent.env_passthrough`. |
| `--format <terminal\|json>` | Presentation format (`terminal` or `json`). Defaults to `terminal`. |
| `--display <ansi\|live>` | Display format (`ansi` or `live`). Defaults to `ansi`. |

Trailing CLI arguments (after options) are forwarded to declared blueprint inputs.

### Behavior

1. **Resolution**: Resolves `<name>` from `.dovo/catalog/` via `Blueprint.load`.
2. **Execution**: Runs the blueprint through the unified runtime engine (`BlueprintRunService`).
3. **Agent steps**: An agent step sends its interpolated `prompt` to the resolved provider in `direct` mode and applies any returned patch inside the worktree only. It fails with `Agent steps require an active git worktree.` under `--no-worktree` or a resumed in-place run. Its stdout is one JSON object (`status`, `summary`, `unfixable_reason`, `touched_files`); see [Agent-Step Adapters](../guides/agent-providers.md).
4. **Secret masking**: Streamed step output, the per-step capture, and failure messages mask secret values: environment variables whose names end in `_KEY`, `_TOKEN`, `_SECRET`, `_PASSWORD`, or `_AUTH` (values of 6+ characters), matching keys in the repository `.env`, the step's own `env:` entries under those same name suffixes and 6+ character rule, and common credential formats (GitHub tokens, Anthropic keys, AWS access key IDs). Names listed under `environment.sensitive_variables` in `.dovo/config.json` are also masked with no length floor, including when set only in a step's `env:`; see [`dovo config`](config.md). A masked value prints as `[REDACTED:<NAME>]` or `[REDACTED]`. Assertions still evaluate the raw output.
5. **Environment flags**: `--env-mode` and `--env-passthrough` apply only to this invocation, are never written to config or run state, and are validated before any step starts; an invalid value exits `1`. See [Agent Providers](../guides/agent-providers.md#subprocess-environment).
6. **Exit Codes**:
   - `0`: Successful run or paused run (with run state saved).
   - `1`: Failed or cancelled run.

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
