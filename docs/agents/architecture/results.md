# Results

What a public entrypoint returns and what a caller branches on. These entries hold #925's accepted target contract for command results.

## One strict result per public command

- **Decision:** a public entrypoint returns a Pydantic `BaseResult` subclass, one per public command, with `extra="forbid"` and `strict=True`.
  - A public entrypoint is a method of the entrypoint class in `core/<pkg>/<pkg>.py`, or an `engine/services/` runner, called from a layer above (R4).
  - Hand-authored YAML models may use `extra="ignore"` only with a justifying comment, as `BlueprintDefinition` and `LoopStepBlock` do.
- **Why:** exit codes, the JSON envelope and every caller read the result, so its shape is checked at construction.
- **Since:** #1106

## Helpers return plain values

- **Decision:** services and helpers behind an entrypoint return plain values, domain models or `@dataclass(frozen=True, slots=True)`.
  - They never define a `*Result` model or a `*Status` enum of their own.
  - A helper may build its entrypoint's own result type only when it is the one helper that builds that result on success, as `collect_worktree_show` builds `WorktreeShowResult` for `Worktree.show`. The entrypoint still catches the helper's failures and copies them into that result.
  - They signal a business failure by raising their package's `DomainError` (see [exceptions.md](exceptions.md)).
  - A helper that processes many items may catch each item's failure, collect it in its plain return value and continue; the entrypoint turns the collection into its result.
- **Why:** a failure carries its `ErrorCode` from the raise to the entrypoint, so no hop re-encodes it.
- **Since:** #1214

## error_code is the only failure classification

- **Decision:** a failure's cause is its `error_code`: the `ErrorCode` it was raised with, or `INTERNAL_ERROR` for an unexpected exception.
  - A status value never names a cause (`not_found`, `already_exists`, `write_failed`, `syntax_error`). When two values would differ only in which failure occurred, that difference is the `error_code`.
  - No code is inferred from message text, and no `match` or table maps a status onto a code or a code onto a status.
- **Why:** two classifications of one failure drift apart, and callers cannot tell which to trust.
- **Since:** #925

## status only where error_code cannot say it

- **Decision:** a command-result payload has a `status` field only for `dovo status` health (`ok`, `degraded`, `uninitialized`) and `dovo worktree prune` (`pruned`, `nothing_to_prune`, `partial_failure`).
  - A prune whose scan fails before any item is processed reports `status` `null`; its `error_code` names the failure.
  - `tests/lint/test_result_header.py` fails when any other command view declares a top-level `status`, or when either `status` admits other values.
  - A result whose only outcomes are success or failure has no `status`; it keeps a named boolean (`valid`, `created`, `deleted`) where it needs one.
  - A new status enum or value holds only an outcome `error_code` cannot express.
- **Why:** `status` reports health or how much of a multi-item operation failed, never why.
- **Since:** #925, #1309

## The six-field header comes first

- **Decision:** every command-result payload starts with `ok`, `error_code`, `errors`, `warnings`, `remediations` and `diagnostics`, in that order.
  - Streaming events (step, lock-wait, banner and message events) carry no header.
  - The header is defined once, by `ResultHeaderView` in `cli/ui/formatters/header.py`. Every command result's formatter declares a view that subclasses it and adds only the command's own fields, filled with `**header_fields(result)`; Pydantic puts parent fields first, so the header leads. `core` result serialization does not carry the header.
  - No command formatter overrides `to_json_serializable`; its payload is its view's dump. `tests/lint/test_result_header.py` walks `FORMATTER_GROUPS` and fails, naming the formatter, when a command formatter's view does not subclass `ResultHeaderView`, redeclares a header field, or the formatter builds its payload by hand.
- **Why:** one parser can tell whether any command succeeded, and why not, without knowing its payload.
- **Since:** #925, #1309

## Every JSON line is a versioned envelope under a declared name

- **Decision:** `UiDispatcher` writes every `--format json` line as `{"version": 1, "event_type": <name>, "payload": <view dump>}`, in that order.
  - The name is a literal the formatter declares as `event_type`; a catalog formatter declares one per `CatalogItemType` in `event_types` and is named by the result's `item_type`. Nothing derives a name from a class, module or command path.
  - A command result is named after its command path (`worktree.create`, `blueprint.list`); run events are `run.*`. A shortcut shares its long form's formatter, so it shares the name.
  - `tests/lint/test_event_names.py` walks `FORMATTER_GROUPS` and fails when a registered formatter declares no name, a name outside `^[a-z][a-z_]*(\.[a-z][a-z_]*)*$`, or a name another formatter also declares.
  - `version` changes only when the line's own keys or their meaning change, never for a payload change.
- **Why:** renaming a class must never rename a public event, and a consumer must know which shape it reads.
- **Since:** #1407

## ok means error_code is null

- **Decision:** on every command-result payload, the result a CLI command dispatches, `ok` is true exactly when `error_code` is null.
  - No command result computes `ok` from `status`, `errors` or any other field. `ok` is defined once, as a property on `BaseResult`, and `ResultHeaderView` refuses a view whose `ok` disagrees with `error_code`; `tests/lint/test_result_header.py` fails when a dispatched result defines its own.
  - Internal results that no command dispatches (interaction, input resolution, agent settings) may define `ok` for their own callers.
  - A step's outcome inside a run is a `StepRecord`, a record rather than a result: a failed step's text is its `error_message`, and its `error_code` is a published one with no `errors`, set on every failed `command`, `script`, `internal` and `ask` step and `null` on a completed one.
- **Why:** callers branch on `ok`, so it must agree with the one failure classification.
- **Since:** #925, #1309

## errors and error_code agree

- **Decision:** `error_code` is a published `ErrorCode` member, set exactly when `errors` is non-empty.
  - `BaseResult` refuses a result where they disagree when it is built, with `error_code must be set exactly when errors is non-empty`, and its field type refuses any value outside the published set, a plain string included.
  - A result with only `warnings` or `remediations` carries no code.
  - A result that reports several errors carries the code of its first, chosen where that error is produced, and no result has `errors` added after it is built. A site that reports one more error builds a new result through validation, never with `model_copy`, which skips the check.
  - A result that sets one without the other is a programmer error, never a way to report a failure.
- **Why:** a failure without a code cannot be branched on, and a code without a message cannot be shown.
- **Since:** #925, #1421

## Exit codes come from error_code

- **Decision:** a command exits `0` when its result's `error_code` is null, and otherwise with the exit that `error_code` declares (`ErrorCode.exit_code`). Every handler in `cli/` calls `exit_for(result)` and picks no exit of its own.
  - A code decided only from the command's own arguments, before the workspace is read or changed, declares `2` and is listed in `ARGUMENT_ERROR_CODES`. `RUN_CANCELLED` and `SESSION_PROCESS_STOP_FAILED` (an interrupted run whose process group survived the stop) declare `130`, and `run_cli` then ends the process by the signal it actually received (`128` plus its number). Every other code declares `1`.
  - `exit_for` reads `error_code`, never `ok` or a status, so `dovo status` exits `0` whatever health it reports.
  - A failure no command result carries (an uninitialized workspace, a config that does not load, a lock timeout, an unexpected exception) is reported by the one handler in `DovoTyperGroup.invoke`: a panel, or under `--format json` exactly one final `error` event with the failed-result header, and exit `1`. Click usage errors pass through it with Click's exit `2`.
  - Each handler builds its `CliContext` with `cli_context(ctx)` after its own arguments parse, and `dovo run` auto-initializes the workspace there too, so a usage error exits `2` before the workspace or config is read, whatever their state.
  - Adding an `ErrorCode` decides its exit; `tests/common/test_error_codes.py` pins every member's.
- **Why:** a script branches on the exit code without knowing the command, and the exit cannot disagree with the JSON result.
- **Since:** #1312, #1443

## A step field appears in every step view

- **Decision:** a step has one surface with three views: the saved `StepRecord`, the `steps[]` row of `dovo session show` (`StepRunSummary`) and the step-done event of `dovo run` and `dovo resume` (`StepDoneEvent`).
  - A change that adds a step fact adds it to every view where a reader can observe it, in one change, and its PR names any view it skips and why. The `session.log` `STEP_DONE` timeline record carries no failure text, so `error_code` skips it.
  - A new field goes after the last field it relates to, or at the end, and defaults to `null`, an empty list or an empty map, so a session saved before it loads and every view reports the default. `error_code` follows `error_message`.
  - A failed step's `error_code` is a `STEP_*` code for a failure the engine detects itself, or the code of the failure it consumed, copied unchanged.
  - The `dovo session show` page documents the row keys in its Step keys table; `tests/lint/test_session_show_step_doc_parity.py` fails, naming each key, when the documented keys and the emitted ones differ as sets.
- **Why:** readers of each view branch on the same facts, and a field added to one view drifts from the others.
- **Since:** #1424

## A declined confirmation is a success

- **Decision:** declining a confirmation prompt is a success, not a failure.
  - The result has `cancelled: true`, no `error_code`, empty `errors`, and `ok` true.
  - The command exits 0, so `dovo ... && next` continues.
  - Where the prompt runs is in [exceptions.md](exceptions.md).
- **Why:** the user chose not to proceed, so nothing failed.
- **Since:** #1191
