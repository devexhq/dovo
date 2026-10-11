# `dovo resume`

`dovo resume` continues a paused session, by ID or the latest one.

## Usage

```bash
dovo resume [session_id] [OPTIONS]
```

### Arguments

| Argument | Description |
| --- | --- |
| `session_id` | Optional session identifier to resume. If omitted, the latest paused session is automatically resumed. |

### Options

| Flag | Description |
| --- | --- |
| `--no-tty` | Disable interactive prompts; prompt_user failures abort the run instead of prompting. |
| `--env-mode <allowlist\|inherit>` | Override `agent.env_mode` for this invocation. |
| `--env-passthrough <name-or-prefix*>` | Forward a host variable name or a prefix ending in a single `*` to agent steps for this invocation; repeatable and appended to `agent.env_passthrough`. |
| `--format <terminal\|json>` | Presentation format (`terminal` or `json`). Defaults to `terminal`. |
| `--display <ansi\|live>` | Display format (`ansi` or `live`). Defaults to `ansi`. |

### Behavior

1. **Session Resolution**:
   - If `session_id` is provided, resumes that specific session.
   - If `session_id` is omitted, the most recent paused session is used. If there is none, an error is shown and the command exits `1`.

2. **Readiness Classification**: Checks that the session exists, is paused, and has intact saved state and an accessible worktree (if it used one).
3. **Execution**: Continues running from the paused step.
4. **Environment flags**: `--env-mode` and `--env-passthrough` apply only to this invocation, are never written to config or run state, and are validated before any step starts; an invalid value exits `1` and leaves the session paused.
5. **Exit Codes**:
   - `0`: Successful completion or paused run (run state updated).
   - `1`: Resume validation error, failed step, cancelled run, or no paused session found.

## Examples

Auto-resume the latest paused run:

```bash
dovo resume
```

Resume a specific session by ID:

```bash
dovo resume blueprint_a1b2c3d4
```

Resume non-interactively (e.g. in automated scripts):

```bash
dovo resume --no-tty
```
