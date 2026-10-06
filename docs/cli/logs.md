# `dovo logs`

The `dovo logs` command shows the persisted logs of a past execution session: the unified session timeline (`session.log`) by default, or one step's raw stdout/stderr capture with `--step`.

Logs live under the global storage root: `~/.dovo/storage/projects/<project_id>/logs/<session_id>/` (the root honors `DOVO_HOME`). A workspace with no project identity yet (no `.dovo/project.json`) keeps them in the repository at `.dovo/logs/<session_id>/`. They are kept after the run finishes.

```
logs/<session_id>/
├── session.log                          # JSON Lines timeline, one event per line
├── 01_setup_attempt_1.stdout.log    # raw per-attempt captures
├── 01_setup_attempt_1.stderr.log
└── 02_check_iter_1_attempt_1.stdout.log   # loop sub-steps carry an _iter_<n> segment
```

Per-attempt stdout/stderr capture is controlled by `history.save_attempt_logs` in `.dovo/config.json` (default `true`). `session.log` is always written when the run has a session ID. Loop sub-steps appear in `session.log` as their own `STEP_START`/`STEP_DONE` events, interleaved with the loop's `LOOP_*` events, exactly like top-level steps; loop body events carry `loop_id` and `iteration`, and every step event carries `step_name` (`STEP_DONE` also `duration_seconds`).

## Usage

```bash
dovo logs <session_id> [OPTIONS]
```

### Arguments

| Argument | Description |
| --- | --- |
| `session_id` | Required session identifier whose logs to show. |

### Options

| Flag | Description |
| --- | --- |
| `--step <step_id>` | Show the raw output captured for this step instead of the session timeline. |
| `--attempt <int>` | Attempt number to show with `--step` (default: the latest attempt). |
| `--stream [stdout\|stderr\|both]` | Output stream to show with `--step` (default: `both`, stdout lines first). |
| `--tail <int>` | Show only the last N lines (with `--step`) or events (without). |
| `--format [terminal\|json]` | Presentation format (`terminal` or `json`). |

`--attempt` and `--stream` only apply together with `--step`; without it they are ignored.

For a loop sub-step, `--step` matches the captures of every loop iteration: the selected attempt's output from each iteration is shown concatenated in iteration order, and the default attempt is the highest attempt number reached in any iteration. Captures from any steps that share the same step ID, such as a top-level step and a loop sub-step, are grouped under that ID. Capture file names do not include the loop's ID, so two loops that have a sub-step with the same ID at the same position write to the same files, and the later loop overwrites the earlier one's captures. Give loop sub-steps distinct IDs to keep both.

## Errors

The command exits `1` when:

- no session record or log directory exists for the session (`No logs found for session '<session_id>'`),
- `--step` names a step with no captured logs (the available step IDs are listed),
- `--attempt` names an attempt that was not captured (the available attempts are listed),
- a log file cannot be read, for example because of its permissions.

A truncated final `session.log` line, for example after the process was killed mid-write, is skipped rather than reported as an error. Bytes that are not valid UTF-8 are shown as `�`.

## Examples

Show the session timeline for a session:

```bash
dovo logs blueprint_a1b2c3d4
```

Show the latest attempt's output for the `test` step:

```bash
dovo logs blueprint_a1b2c3d4 --step test
```

Show the last 20 stderr lines of the second attempt:

```bash
dovo logs blueprint_a1b2c3d4 --step test --attempt 2 --stream stderr --tail 20
```

Output the session timeline as structured events:

```bash
dovo logs blueprint_a1b2c3d4 --format json
```
