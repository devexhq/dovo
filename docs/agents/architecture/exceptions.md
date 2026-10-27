# Exceptions

How business failures are raised, caught and classified behind an entrypoint. R4 and R6 in [AGENTS.md](../../../AGENTS.md) link here; the result side is in [results.md](results.md).

## One DomainError class per package

- **Decision:** each package defines exactly one `DomainError` subclass in its `exceptions.py`.
  - The `ErrorCode`, the message and any remediations are passed at the raise. No subclass per code, and no class derives from the package's class.
  - Code branches on `error_code`, never on the exception class (`isinstance` or `except` on a subclass).
- **Why:** a failure carries one classification, its `error_code`; a class per code is a second one that drifts from it.
- **Exemplar:** `src/dovo/core/project/exceptions.py`
- **Enforced by:** pending #1214 (phase 2)
  - Per-code subclasses remain in `src/dovo/core/catalog/exceptions.py`, `src/dovo/core/git/exceptions.py`, `src/dovo/core/worktree/exceptions.py` and `src/dovo/engine/exceptions.py`.
- **Since:** #1214

## Chain only causes that break Dovo's own state

- **Decision:** a `DomainError` raised while handling an `OSError` chains it with `from exc` only when the `OSError` hit Dovo's own state.
  - Dovo's own state: `.dovo/`, `.dovo/project.json`, the session directory and its temp directory.
  - A failure of an external tool or the environment that the code and message already name (git missing or failing to start) is raised `from None`.
- **Why:** `catch_failure` records a `DomainError` only when its `__cause__` is an `OSError`, so the cause alone decides whether an event file is written.
- **Exemplar:** `src/dovo/core/agents/mutation_git.py`
- **Enforced by:** review only
- **Since:** #1109

## Wrapping another package's DomainError

- **Decision:** re-raise another package's `DomainError` as your package's class, chained to the wrapped error's own cause: `raise <Package>Error(...) from error.__cause__`.
  - Never `from error`: the cause is then a `DomainError`, so `catch_failure` drops the wrapped `OSError` record.
  - Never `from None` when the wrapped error has a cause: the record is lost.
  - No package-private helper does this; the chain is written at the raise.
- **Why:** the wrapped error's `OSError` cause, and only that, is what `catch_failure` records, so the record survives the wrap exactly as it would unwrapped.
- **Enforced by:** pending #1214 (phase 2)
  - Worktree wraps git errors through the private `recorded_cause()` in `src/dovo/core/worktree/exceptions.py`, which chains the wrapped error itself when it has no `OSError` cause. Phase 2 deletes it.
- **Since:** #1214

## No code tokens in messages

- **Decision:** an error message never contains a code token such as `(WORKTREE_GIT_FAILED)`, including in the exemplar. Tests assert `error_code`, never a token in `errors`.
- **Why:** `error_code` is a failure's only classification (#925), and a token in the text can disagree with it or name no `ErrorCode` member.
- **Enforced by:** pending #1214 (phase 3)
  - Tokens remain in `src/dovo/core/worktree/services/lifecycle.py`, `src/dovo/core/catalog/services/validate.py`, `src/dovo/core/agents/responses.py`, `src/dovo/core/git/runner.py` and `src/dovo/core/project/services/identity.py`, among others.
- **Since:** #1214

## ErrorCode naming

- **Decision:** `ErrorCode` members are named `<PACKAGE>_<NOUN>_<STATE>`, such as `CATALOG_SCHEMA_INVALID`, `WORKTREE_NOT_FOUND` or `CONFIG_WRITE_FAILED`.
  - The prefix names the package that raises the code.
  - Every member is referenced in `src/`.
- **Why:** codes are public JSON that callers branch on; one pattern makes them predictable and stops each issue minting its own shape.
- **Enforced by:** pending #1214 (phase 4)
  - Members in `src/dovo/common/error_codes.py` that break the pattern, use another package's prefix or are unused are renamed or deleted in phase 4.
- **Since:** #1214

## Caught once, at the public entrypoint

- **Decision:** a `DomainError` is caught once, with `catch_failure`, by the public entrypoint or by a caller in another package.
  - The catcher copies the failure's `error_code`, its message into `errors`, its `remediations`, and its diagnostic through `error_diagnostics` into `diagnostics`.
  - Code behind an entrypoint catches only to recover, or to collect per-item failures and continue, and records what it caught with `record_exception` (see [diagnostics.md](diagnostics.md)).
  - `catch_failure` re-raises `LockTimeoutError` and records unexpected exceptions, so no helper catches broad `Exception` to report it.
- **Why:** one catch site means one copy into the result, one diagnostic record and one place a caller looks.
- **Exemplar:** `src/dovo/core/worktree/worktree.py`
- **Enforced by:** review only
- **Since:** #1106

## catch_failure operation names

- **Decision:** the `operation` passed to `catch_failure` is `<package>.<entrypoint>`.
  - The package is written as its folder name, such as `agents`, `sessions` or `diagnostics`, or `engine` for code under `src/dovo/engine/`.
  - The entrypoint is the method that catches, never a sub-step or another package's call.
  - Each name is unique per `catch_failure` block.
- **Why:** the name is written to every event file, so it must point at the one entrypoint that reported the failure.
- **Enforced by:** pending #1214 (phase 7)
  - Sub-step or other-package names remain in `src/dovo/core/bootstrap/services/initialize.py`, `src/dovo/core/agents/cli_mutation.py`, `src/dovo/engine/services/run.py`, `src/dovo/engine/services/session_show.py` and `src/dovo/core/diagnostics/checks/worktree_refs.py`.
  - Names repeat across blocks in `src/dovo/core/catalog/catalog.py` and `src/dovo/engine/services/resume.py`.
- **Since:** #1106

## Extra failure data and the copy into a result

- **Decision:** extra data a failure needs (such as conflicting files) travels on `Failure`'s optional extra-data field.
  - Never catch and re-raise inside the `catch_failure` block to smuggle it out.
  - An entrypoint copies a `Failure` into its result with the one shared helper, never a hand-written copy.
- **Why:** the copy is repeated at every entrypoint today, and a hand-written copy is where a field gets dropped.
- **Enforced by:** pending #1214 (phase 2)
  - `Worktree.apply` in `src/dovo/core/worktree/worktree.py` catches `WorktreeApplyConflictError` inside its block.
- **Since:** #1214

## Lock timeouts propagate

- **Decision:** every `LockTimeoutError` propagates to the top-level `Workspace Lock Timeout` handler. No package turns it into an error code, a warning or a status.
- **Why:** this is what R6's "report it" means: one handler reports contention the same way for every command.
- **Exemplar:** `src/dovo/core/worktree/worktree.py`
- **Enforced by:** pending #1214 (phase 8)
  - `src/dovo/core/project/services/identity.py` turns it into `PROJECT_IDENTITY_LOCKED` (#1214, phase 8).
  - `src/dovo/engine/workspace.py` turns it into a warning when discarding a worktree (#1135).
  - `src/dovo/core/artifacts/services/prune.py` turns it into a status (#1197).
- **Since:** #1214

## Declined confirmation is decided in core

- **Decision:** a delete that asks for confirmation takes the confirm callback in its core entrypoint, which resolves the target first and never prompts for an unknown one.
  - The handler makes one call and sets no result field (R2).
  - A declined prompt returns a success with `cancelled` set (see [results.md](results.md)).
- **Why:** prompting in the CLI asks to confirm deleting a target that does not exist, and splits one outcome across two layers.
- **Exemplar:** `src/dovo/core/worktree/worktree.py`
- **Enforced by:** pending #1214 (phase 6)
  - `src/dovo/cli/blueprint/commands/blueprint_delete.py` and `src/dovo/cli/step/commands/step_delete.py` prompt and build the result in the CLI.
- **Since:** #1194
