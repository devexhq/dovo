# `dovo run`

The `dovo run` command executes a blueprint by name from the catalog.

## Usage

```bash
dovo run <name> [OPTIONS] [-- <input-overrides>]
```

### Options

| Flag | Description |
| --- | --- |
| `--no-sandbox` | Run execution in-place in the working tree without creating a Git sandbox. Agent steps are rejected under it, because they require a sandbox. |
| `--keep` | Retain the sandbox worktree after execution. |
| `--auto-apply` | Automatically apply sandbox changes to the main workspace on successful completion. |
| `--agent <name>` | Override the agent provider for this run (model, endpoint, temperature, and max tokens still come from config); an unregistered name fails an agent step with `AGENT_PROVIDER_UNSUPPORTED` and has no effect on command/script steps. |
| `--session-id <id>` | Explicit session identifier. |
| `--no-tty` | Disable interactive prompts; prompt_user failures abort the run instead of blocking for input. |
| `--format <terminal\|json>` | Presentation format (`terminal` or `json`). Defaults to `terminal`. |
| `--display <ansi\|live>` | Display format (`ansi` or `live`). Defaults to `ansi`. |

Trailing CLI arguments (after options) are forwarded to declared blueprint inputs.

### Behavior

1. **Resolution**: Resolves `<name>` from `.dovo/catalog/` via `Blueprint.load`.
2. **Execution**: Runs the blueprint through the unified runtime engine (`BlueprintRunService`).
3. **Agent steps**: An agent step sends its interpolated `prompt` to the resolved provider in `direct` mode and applies any returned patch inside the sandbox only. It fails with `Agent steps require an active Dovo Git sandbox.` under `--no-sandbox` or a resumed in-place run. Its stdout is one JSON object (`status`, `summary`, `unfixable_reason`, `touched_files`); see [Agent-Step Adapters](../guides/agent-providers.md).
4. **Exit Codes**:
   - `0`: Successful run or paused run (with run state saved).
   - `1`: Failed or cancelled run.

## Examples

Run a blueprint:

```bash
dovo run build-task
```

Run a blueprint in-place without sandbox:

```bash
dovo run release-flow --no-sandbox
```

Pass declared blueprint inputs:

```bash
dovo run test-suite --target src/dovo --verbose true
```

Run non-interactively in CI:

```bash
dovo run lint-all --no-tty
```
