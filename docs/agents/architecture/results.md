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
  - They never return a `*Result`, and never define a `*Status` enum to classify their own failures.
  - They signal a business failure by raising their package's `DomainError` (see [exceptions.md](exceptions.md)).
  - A helper that processes many items may catch each item's failure, collect it in its plain return value and continue; the entrypoint turns the collection into its result.
- **Why:** a failure carries its `ErrorCode` from the raise to the entrypoint, so no hop re-encodes it.
- **Since:** #1106

## error_code is the only failure classification

- **Decision:** a failure's cause is its `error_code`: the `ErrorCode` it was raised with, or `INTERNAL_ERROR` for an unexpected exception.
  - A status value never names a cause (`not_found`, `already_exists`, `write_failed`, `syntax_error`). When two values would differ only in which failure occurred, that difference is the `error_code`.
  - No code is inferred from message text, and no `match` or table maps a status onto a code or a code onto a status.
- **Why:** two classifications of one failure drift apart, and callers cannot tell which to trust.
- **Since:** #925

## status only where error_code cannot say it

- **Decision:** a command-result payload has a `status` field only for `dovo status` health (`ok`, `degraded`, `uninitialized`) and `dovo worktree prune` (`ok`, `partial_failure`).
  - A result whose only outcomes are success or failure has no `status`; it keeps a named boolean (`valid`, `created`, `deleted`) where it needs one.
  - A new status enum or value holds only an outcome `error_code` cannot express.
- **Why:** `status` reports health or how much of a multi-item operation failed, never why.
- **Since:** #925

## The six-field header comes first

- **Decision:** every command-result payload starts with `ok`, `error_code`, `errors`, `warnings`, `remediations` and `diagnostics`, in that order.
  - Streaming events (step, lock-wait, banner and message events) carry no header.
- **Why:** one parser can tell whether any command succeeded, and why not, without knowing its payload.
- **Since:** #925

## ok means error_code is null

- **Decision:** on every command-result payload, the result a CLI command dispatches, `ok` is true exactly when `error_code` is null.
  - No command result computes `ok` from `status`, `errors` or any other field.
  - Internal results that no command dispatches (step, interaction, input resolution, agent settings) may define `ok` for their own callers.
- **Why:** callers branch on `ok`, so it must agree with the one failure classification.
- **Since:** #925

## errors and error_code agree

- **Decision:** `errors` is non-empty exactly when `error_code` is set, and `BaseResult` rejects a result where they disagree.
- **Why:** a failure without a code cannot be branched on, and a code without a message cannot be shown.
- **Since:** #925

## A declined confirmation is a success

- **Decision:** declining a confirmation prompt is a success, not a failure.
  - The result has `cancelled: true`, no `error_code`, empty `errors`, and `ok` true.
  - The command exits 0, so `dovo ... && next` continues.
  - Where the prompt runs is in [exceptions.md](exceptions.md).
- **Why:** the user chose not to proceed, so nothing failed.
- **Since:** #1191
