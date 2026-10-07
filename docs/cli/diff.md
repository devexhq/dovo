# `dovo diff`

The `dovo diff` command views syntax-highlighted unified diffs from loop run sessions directly in the terminal without manually locating their session storage.

## Usage

```bash
dovo diff [session_id] [OPTIONS]
```

### Arguments

| Argument | Description |
| --- | --- |
| `session_id` | Optional session identifier (e.g. `blueprint_a1b2c3d4`). If omitted, displays the latest session diff. |

### Options

| Flag | Description |
| --- | --- |
| `--raw` | Output unformatted plain text diff directly to stdout without headers, Rich panels, or ANSI codes. Truncation limits are completely bypassed. |
| `--full` / `--no-full` | Bypass line truncation limits in interactive terminals (TTY) and render complete formatted diff. |
| `--format <terminal\|json>` | Presentation format (`terminal` or `json`). Defaults to `terminal`. |

## Behavior

1. **Session Resolution**:
   - Sessions resolve below `DOVO_HOME/storage/projects/<project-id>/sessions/` (or `~/.dovo/storage/projects/<project-id>/sessions/` when `DOVO_HOME` is unset).
   - A session exists only when its session record exists; a session directory without a session record is not found.
   - When `session_id` is supplied: resolves that session's `diff.patch`.
   - When `session_id` is omitted: selects the session with the latest `started_at`, ties broken by the latest run id.
   - If no session record matches: displays a **Session Not Found** error panel and exits with code `1`.
2. **Artifact Loading**:
   - If `diff.patch` is missing: displays a **Diff Not Found** error panel and exits with code `1`.
   - If `diff.patch` is empty (0 bytes or whitespace-only): prints `No changes recorded for session <session_id>.` and exits with code `0`.
3. **Rendering & Truncation**:
   - Interactive formatted output renders a header with the session ID and artifact path, followed by syntax-highlighted diff text.
   - In an interactive terminal (TTY), if formatted diff output exceeds 500 lines (and `--full` is not provided), output is truncated at line 500 followed by a dim notice banner with hints to view the complete diff, page with `less -R`, or view raw/artifact contents.
   - Passing `--full` renders all formatted lines without truncation.
   - Non-TTY stdout (e.g. piped to `cat` or redirected to a file) and `--raw` mode automatically bypass truncation limits.
   - When `--raw` is passed, outputs the exact patch content directly to stdout for redirection or piping into `git apply` / `patch`.
4. **Secret masking**: `diff.patch` is written with secret values replaced by `[REDACTED:<NAME>]` or `[REDACTED]`, so a masked line will not reproduce the original value when applied with `git apply` or `patch`.
5. **Exit Codes**:
   - `0`: Diff successfully displayed, or empty diff.
   - `1`: Session not found, diff artifact not found, or read failure.

## Examples

View diff for the latest session:

```bash
dovo diff
```

View diff for an explicit session ID:

```bash
dovo diff blueprint_a1b2c3d4
```

Output raw patch text (useful for piping into `git apply` or saving to a file):

```bash
dovo diff blueprint_a1b2c3d4 --raw > latest_fix.patch
```
