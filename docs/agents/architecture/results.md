# Results

What a public entrypoint returns and what a caller branches on. This holds #925's accepted target contract; each rule `main` does not yet meet is marked pending with the issue that lands it.

## One strict result per public command

- **Decision:** a public entrypoint returns a Pydantic `BaseResult` subclass, one per public command, with `extra="forbid"` and `strict=True`.
  - A public entrypoint is a method of the entrypoint class in `core/<pkg>/<pkg>.py`, or an `engine/services/` runner, called from a layer above (R4).
  - Hand-authored YAML models may use `extra="ignore"` only with a justifying comment, as `BlueprintDefinition` and `LoopStepBlock` do.
- **Why:** exit codes, the JSON envelope and every caller read the result, so its shape is checked at construction.
- **Exemplar:** `src/dovo/core/worktree/models.py`
- **Enforced by:** `tests/lint/test_result_contracts.py`, `tests/lint/test_model_strictness.py`
- **Since:** #1106

## Helpers return plain values

- **Decision:** services and helpers behind an entrypoint return plain values, domain models or `@dataclass(frozen=True, slots=True)`.
  - They never return a `*Result`, and never define a `*Status` enum to classify their own failures.
  - They signal a business failure by raising their package's `DomainError` (see [exceptions.md](exceptions.md)).
- **Why:** a failure carries its `ErrorCode` from the raise to the entrypoint, so no hop re-encodes it.
- **Exemplar:** `src/dovo/core/worktree/services/show.py`
- **Enforced by:** review only
- **Since:** #1106

## error_code is the only failure classification

- **Decision:** a failure's cause is its `error_code`: the `ErrorCode` it was raised with, or `INTERNAL_ERROR` for an unexpected exception.
  - A status value never names a cause (`not_found`, `already_exists`, `write_failed`, `syntax_error`). When two values would differ only in which failure occurred, that difference is the `error_code`.
  - No code is inferred from message text, and no `match` or table maps a status onto a code or a code onto a status.
- **Why:** two classifications of one failure drift apart, and callers cannot tell which to trust.
- **Exemplar:** `src/dovo/core/worktree/worktree.py`
- **Enforced by:** pending #1131
  - Failure-kind statuses remain in sessions and artifacts (#1197), config (#1118) and `dovo init`'s `failure_mode` (#1112). New code adds none.
- **Since:** #925

## status only where error_code cannot say it

- **Decision:** a command-result payload has a `status` field only for `dovo status` health (`ok`, `degraded`, `uninitialized`) and `dovo worktree prune` (`ok`, `partial_failure`).
  - A result whose only outcomes are success or failure has no `status`; it keeps a named boolean (`valid`, `created`, `deleted`) where it needs one.
  - A new status enum or value holds only an outcome `error_code` cannot express.
- **Why:** `status` reports health or how much of a multi-item operation failed, never why.
- **Exemplar:** `src/dovo/core/worktree/models.py`
- **Enforced by:** pending #1131
- **Since:** #925

## The six-field header comes first

- **Decision:** every command-result payload starts with `ok`, `error_code`, `errors`, `warnings`, `remediations` and `diagnostics`, in that order. Streaming events carry no header.
- **Why:** one parser can tell whether any command succeeded, and why not, without knowing its payload.
- **Enforced by:** pending #1131
  - The run and resume result gets the header in #1132.
- **Since:** #925

## ok means error_code is null

- **Decision:** `ok` is true exactly when `error_code` is null. No result computes `ok` from `status`, `errors` or any other field.
- **Why:** callers branch on `ok`, so it must agree with the one failure classification.
- **Exemplar:** `src/dovo/core/worktree/models.py`
- **Enforced by:** pending #1131
  - Sessions and artifacts compute `ok` from `status` (#1197), catalog list and show from `errors`, and the run result from its session record and `errors` (#1132).
  - Config computes it from `status` or `errors` in `src/dovo/core/config/loader.py`, `src/dovo/core/config/mutate.py`, `src/dovo/core/config/validate.py` and `src/dovo/core/config/models.py` (#1118, #1120).
  - `dovo init` computes it from `errors` in `src/dovo/core/bootstrap/models.py` (#1112), and `dovo doctor` from check statuses in `src/dovo/core/diagnostics/models.py` (#1131).
- **Since:** #925

## errors and error_code agree

- **Decision:** `errors` is non-empty exactly when `error_code` is set, and `BaseResult` rejects a result where they disagree.
- **Why:** a failure without a code cannot be branched on, and a code without a message cannot be shown.
- **Enforced by:** pending #641
  - `validate_error_code_presence` in `src/dovo/common/models.py` is a no-op today.
- **Since:** #925

## A declined confirmation is a success

- **Decision:** declining a confirmation prompt returns `cancelled: true`, no `error_code`, empty `errors`, and exits 0. Where the prompt runs is in [exceptions.md](exceptions.md).
  - The JSON `ok: true` key arrives with the header (#1131).
- **Why:** the user chose not to proceed, so nothing failed and `dovo ... && next` should continue.
- **Exemplar:** `src/dovo/core/worktree/worktree.py`
- **Enforced by:** `tests/cli/worktree/test_worktree_delete.py`, `tests/cli/blueprint/test_blueprint_delete.py`, `tests/cli/step/test_step_delete.py`
- **Since:** #1191
