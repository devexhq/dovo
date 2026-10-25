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
| `--keep` | Keep the worktree whatever the run status. Each run gets its own worktree at `.dovo/worktrees/<session_id>/` on branch `dovo/<session_id>`, so repeated `--keep` runs of one blueprint do not collide. |
| `--discard` | Remove the worktree whatever the run status, including after a failed run. Cannot be combined with `--keep`: the pair exits `2` with a usage error before any session is created. |
| `--auto-apply` | Automatically apply worktree changes to the main workspace on successful completion. |
| `--agent <name>` | Use this agent provider for the run instead of `default_agent`. All agent settings (model, endpoint, temperature, max tokens, tools, environment) come from `agents.<name>`, or the field defaults when that block is absent; other providers' blocks are not read. An unknown name fails agent steps with `AGENT_PROVIDER_UNSUPPORTED`; command and script steps are unaffected. |
| `--session-id <id>` | Explicit session identifier. |
| `--no-tty` | Disable interactive prompts; prompt_user failures abort the run instead of blocking for input, and a run that reaches an `ask` step pauses (exit `0`) so it can be answered with `dovo resume --answer`. |
| `--env-mode <allowlist\|inherit>` | Override `agents.<provider>.env_mode` for this invocation. |
| `--env-passthrough <name-or-prefix*>` | Forward a host variable name or a prefix ending in a single `*` to agent steps for this invocation; repeatable and appended to `agents.<provider>.env_passthrough`. |
| `--format <terminal\|json>` | Presentation format (`terminal` or `json`). Defaults to `terminal`. |
| `--display <ansi\|live>` | Display format (`ansi` or `live`). Defaults to `ansi`. |

Trailing CLI arguments (after options) are forwarded to declared blueprint inputs.

### Worktree retention

When the run ends, the worktree is kept or removed by the run status and the flags:

- `completed` and `cancelled` runs remove the worktree by default; `--keep` keeps it.
- `failed` runs keep the worktree by default, with or without `--keep`.
- `--discard` removes the worktree whatever the status.

A `paused` run always keeps its worktree so `dovo resume` can reopen it, and so does a run whose `--auto-apply` hit a conflict, because the worktree holds the only copy of the work that could not be applied. `--discard` does not override either.

A kept failed worktree prints where it is and how to inspect or remove it:

```text
Worktree: Kept at .dovo/worktrees/blueprint_a3cb925b (branch dovo/blueprint_a3cb925b)
Fix:
- Inspect it with 'git -C .dovo/worktrees/blueprint_a3cb925b status'
- Remove it with 'dovo worktree delete blueprint_a3cb925b'
```

Kept worktrees count toward `worktree.max_active_worktrees`; when the limit is reached a new run fails with a `Fix:` naming `dovo worktree delete`. Pass `--discard` in CI and scripts to restore the old cleanup of failed runs. If removal fails, the run status is unchanged and a `Failed to remove worktree '<path>': <detail>` warning is added.

The `path` of a `cleanup` event (including with `--format json`) is relative to the working directory when the worktree is inside it and absolute otherwise; the `ready` event keeps the absolute path.

### Behavior

1. **Initialization**: When the workspace has no `.dovo/project.json`, `dovo run` initializes it first, as `dovo init` would, and prints nothing when that succeeds. When it fails (for example outside a Git repository, or in a directory whose `.dovo` is `DOVO_HOME`), it prints the same error and fix as `dovo init` and exits `1` without running the blueprint. It first undoes what the failed attempt wrote, leaving `.dovo/` as it was before, so the next `dovo run` tries again. Unlike `dovo run`, `dovo init` leaves a failed attempt's files in place.
2. **Resolution**: Looks up `<name>` in the blueprint catalog.
3. **Execution**: Runs the blueprint.
4. **Agent steps**: An agent step sends its `prompt` to the provider and applies any change inside the worktree. Under `--no-worktree`, or a resumed in-place run, it fails with `Agent steps require an active git worktree.` Its stdout is one JSON object (`status`, `summary`, `unfixable_reason`, `touched_files`); see [Agent Providers](../guides/agent-providers.md).
5. **Secret masking**: Streamed step output, the per-step capture, and failure messages mask secret values: environment variables whose names end in `_KEY`, `_TOKEN`, `_SECRET`, `_PASSWORD`, or `_AUTH` (values of 6+ characters), matching keys in the repository `.env`, the step's own `env:` entries under those same name suffixes and 6+ character rule, and common credential formats (GitHub tokens, Anthropic keys, AWS access key IDs). Names listed under `environment.sensitive_variables` in `.dovo/config.json` are also masked with no length floor, including when set only in a step's `env:`; see [`dovo config`](config.md). A masked value prints as `[REDACTED:<NAME>]` or `[REDACTED]`. Assertions still evaluate the raw output.
6. **Environment flags**: `--env-mode` and `--env-passthrough` apply only to this invocation, are never written to config or run state, and are validated before any step starts; an invalid value exits `1`. See [Agent Providers](../guides/agent-providers.md#subprocess-environment).
7. **Diagnostic logs**: When a run hits an unexpected error saving its state or managing its workspace, the error or warning ends with a `Details logged:` line naming a local record. When a step hits an unexpected error launching its process, reading its output, calling its agent provider or publishing its artifacts, the step's error is unchanged and the line is added to the run's warnings; see [Diagnostic logs](doctor.md#diagnostic-logs). When a step hits an unexpected error checking an agent's edit or preparing its private scratch, or the operating system refuses to create that scratch, the line ends the step's error instead; a rejected edit is discarded.
8. **Exit Codes**:
   - `0`: Successful run or paused run (with run state saved).
   - `1`: Failed or cancelled run, or a failed automatic initialization.

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
