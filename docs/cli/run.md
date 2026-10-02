# `wt run`

The `wt run` command executes a blueprint by name from the catalog.

## Usage

```bash
wt run <name> [OPTIONS] [-- <input-overrides>]
```

### Options

| Flag | Description |
| --- | --- |
| `--no-sandbox` | Run execution in-place in the working tree without creating a Git sandbox. |
| `--keep` | Retain the sandbox worktree after execution. |
| `--auto-apply` | Automatically apply sandbox changes to the main workspace on successful completion. |
| `--agent <name>` | Override the agent provider for this run (model, endpoint, temperature, and max tokens still come from config); an unregistered name fails an agent step with `AGENT_PROVIDER_UNSUPPORTED` and has no effect on command/script steps. This does not enable provider invocation. |
| `--session-id <id>` | Explicit session identifier. |
| `--no-tty` | Disable interactive prompts; prompt_user failures abort the run instead of blocking for input. |
| `--format <terminal\|json>` | Presentation format (`terminal` or `json`). Defaults to `terminal`. |
| `--display <ansi\|live>` | Display format (`ansi` or `live`). Defaults to `ansi`. |

Trailing CLI arguments (after options) are forwarded to declared blueprint inputs.

### Behavior

1. **Resolution**: Resolves `<name>` from `.worktree/catalog/` via `Blueprint.load`.
2. **Execution**: Runs the blueprint through the unified runtime engine (`BlueprintRunService`).
3. **Exit Codes**:
   - `0`: Successful run or paused run (with run state saved).
   - `1`: Failed or cancelled run.

## Examples

Run a blueprint:

```bash
wt run build-task
```

Run a blueprint in-place without sandbox:

```bash
wt run release-flow --no-sandbox
```

Pass declared blueprint inputs:

```bash
wt run test-suite --target src/worktree --verbose true
```

Run non-interactively in CI:

```bash
wt run lint-all --no-tty
```
