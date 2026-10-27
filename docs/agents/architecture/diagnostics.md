# Diagnostics

How caught exceptions are recorded in the diagnostic log (`<DOVO_HOME>/logs/`) and how a record reaches the caller. Who catches is in [exceptions.md](exceptions.md).

## Records go in diagnostics, never in text

- **Decision:** an error-level record reaches the caller only as an entry in a `diagnostics` list, in the order recorded.
  - No message, warning, step error, session row or persisted session state carries a `Details logged:` line.
  - The terminal renders one `Details logged: <log_path> (event <event_id>)` line per entry, from the result.
  - A record that could not be written adds nothing.
- **Why:** text references cannot be parsed by JSON callers, and persisted text keeps a path that outlives the log's retention.
- **Exemplar:** `src/dovo/engine/workspace.py`
- **Enforced by:** `tests/cli/ui/formatters/test_result_diagnostics.py`
  - Run and resume JSON payloads carry no records yet (pending #1132).
  - The top-level `Fatal Error` panel in `src/dovo/cli/cli.py` appends the line to its message (pending #1135).
- **Since:** #1196

## Recording levels

- **Decision:** a caught exception is recorded at one of two levels.
  - **Error:** the failure is reported to the caller as an error or warning. `catch_failure` records unexpected exceptions and `OSError` causes at this level, and a site that reports and continues does the same.
  - **Debug:** Dovo recovered and reports nothing, such as an unreadable catalog index or a failing run observer. A debug record never reaches `diagnostics`.
  - An expected failure whose message already names the cause (git failing, a missing git binary) is not recorded.
- **Why:** error-level records explain something the user saw; debug records keep recovered failures diagnosable without alarming anyone.
- **Exemplar:** `src/dovo/core/worktree/services/lifecycle.py`
- **Enforced by:** review only
- **Since:** #1133

## Each failure is recorded once

- **Decision:** one failure leaves one event file.
  - A site that records an exception does not also raise it into a `catch_failure` that records it again.
  - A `DomainError` is recorded through its `OSError` cause, by `catch_failure` or by the site that reports it and continues.
  - Records that reach a step, an agent attempt or an agent response are copied into the run result's `diagnostics`, not recorded again.
- **Why:** a duplicate event file makes one failure look like two and doubles the `diagnostics` entries.
- **Exemplar:** `src/dovo/core/worktree/services/lifecycle.py`
- **Enforced by:** review only
- **Since:** #1196
