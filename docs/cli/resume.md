# `dovo resume`

The `dovo resume` command continues a paused blueprint execution session, either by specifying an explicit session ID or by automatically resuming the latest paused run.

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
| `--format <terminal\|json>` | Presentation format (`terminal` or `json`). Defaults to `terminal`. |
| `--display <ansi\|live>` | Display format (`ansi` or `live`). Defaults to `ansi`. |

### Behavior

1. **Session Resolution**:
   - If `session_id` is provided, resumes that specific session.
   - If `session_id` is omitted, queries `SessionsRepository.get_latest_paused()` and picks up the most recent paused run. If no paused session is found, renders a formatted error panel and exits with code `1`.

2. **Readiness Classification**: Validates that the session exists, is in `paused` status, and has intact execution state and an accessible worktree (if worktree-backed).
3. **Execution**: Re-enters step execution via `Engine.resume`.
4. **Exit Codes**:
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
