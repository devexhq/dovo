# Exceptions

How business failures are raised, caught and classified behind an entrypoint. R4 and R6 in [AGENTS.md](../../../AGENTS.md) link here; the result side is in [results.md](results.md).

## One DomainError class per package

- **Decision:** each package that raises business failures defines exactly one `DomainError` subclass in its `exceptions.py`.
  - The `ErrorCode`, the message and any remediations are passed at the raise. A failure that needs a built message gets a factory function returning the package class, never a subclass.
  - No subclass per code, and no class derives from the package's class.
  - Code branches on `error_code`, never on the exception class. `except GitError` or `isinstance(exc, GitError)` only names the package; the branch reads `error_code`.
  - A handler that catches the package class handles a set of codes and re-raises every other code unchanged.
- **Why:** a failure carries one classification, its `error_code`; a class per code is a second one that drifts from it.
- **Since:** #1214
- **Example:**
  ```python
  raise WorktreeError(
      ErrorCode.WORKTREE_NOT_FOUND,
      f"Worktree '{worktree_id}' not found.",
      [Remediation(message="Run 'dovo worktree list' to see known worktrees", command="dovo worktree list")],
  )
  ```

## Chain only causes that break Dovo's own state

- **Decision:** a `DomainError` raised while handling an `OSError` chains it with `from exc` only when the `OSError` hit Dovo's own state.
  - Dovo's own state: `.dovo/`, `.dovo/project.json`, the session directory and its temp directory.
  - User-authored catalog definitions (blueprint and step YAML in any catalog tier, `.dovo/catalog/` included) are not Dovo's own state. A failure to read one is reported by its message alone, raised `from None` and never recorded.
  - A failure of an external tool or the environment that the code and message already name (git missing or failing to start) is raised `from None`.
- **Why:** `catch_failure` records a `DomainError` only when its `__cause__` is an `OSError`, so the cause alone decides whether an event file is written.
- **Since:** #1109
- **Example:**
  ```python
  except OSError as exc:
      raise ProjectIdentityError(ErrorCode.PROJECT_IDENTITY_WRITE_FAILED, f"Unable to write '{path}': {exc}") from exc
  ```

## Wrapping another package's DomainError

- **Decision:** a package that adds context to another package's `DomainError` re-raises it as its own class, chained to the wrapped error's own cause: `from error.__cause__`.
  - Never `from error`: the cause is then a `DomainError`, so `catch_failure` drops the wrapped `OSError` record.
  - Never `from None` when the wrapped error has a cause: the record is lost.
  - A wrap that only adds context to the message keeps the wrapped code. A wrap that reports a different failure at its own level raises a code under its own prefix (see ErrorCode naming). A package with nothing to add lets the error propagate unchanged.
  - A re-raise inside one package that adds context, such as an `EngineError` re-raised as an `EngineError` with a "Cannot resume" message, chains the same way.
  - The chain is written at the raise, with no helper.
- **Why:** only the wrapped error's `OSError` cause is recorded, so the record survives the wrap exactly as it would unwrapped.
- **Since:** #1214
- **Example:**
  ```python
  except GitError as exc:
      raise WorktreeError(
          exc.error_code, f"Git failed {activity} for worktree '{worktree_id}': {exc}"
      ) from exc.__cause__
  ```

## No code tokens in messages

- **Decision:** an error message never contains a code token such as `(WORKTREE_PATH_CONFLICT)`.
  - Tests assert `error_code`, never a token in `errors`.
- **Why:** `error_code` is a failure's only classification (#925), and a token in the text can disagree with it or name no `ErrorCode` member.
- **Since:** #1214

## ErrorCode naming

- **Decision:** `ErrorCode` members are named `<PREFIX>_<NOUN>_<STATE>`, such as `CATALOG_SCHEMA_INVALID`, `PROJECT_IDENTITY_WRITE_FAILED` or `SESSION_STATE_CORRUPT`. `INTERNAL_ERROR` is the one exception.
  - The prefix names what failed as a user sees it. Usually that is the raising package's folder name in the singular (`AGENT`, `CATALOG`, `GIT`, `WORKTREE`). For `engine`, it is what the engine runs (`SESSION`, `BLUEPRINT`, `INTERACTION`). No prefix names a layer such as `ENGINE`.
  - A package raises a literal code only under its own prefixes, or another package's code for that package's failure, so one failure has one code whichever package finds it: `GIT_FAILED` for any git command that exits non-zero. The lint lists each prefix's packages and each shared code.
  - The noun is left out when it is the prefix's own subject (`WORKTREE_NOT_FOUND`). The state comes last, from the lint's closed list.
  - A failed operation is `<VERB>_FAILED` with the verb in its base form: `CREATE_FAILED`, never `CREATION_FAILED`.
  - Every member is referenced in `src/`.
- **Why:** codes are public JSON that callers branch on; one pattern makes them predictable, and one code per failure lets a caller branch once whichever command reported it.
- **Since:** #1214

## Caught once, at the public entrypoint

- **Decision:** a `DomainError` is caught once, with `catch_failure`, by the public entrypoint or by a caller in another package, and its failure is copied into the result.
  - The copy is `Failure.result_fields()` spread into the result constructor, never a hand-written copy of the failure's fields.
  - Code behind an entrypoint catches only to recover, or to collect per-item failures and continue. It records what it caught with `record_exception`, at the level [diagnostics.md](diagnostics.md) sets.
  - Nothing inside a `catch_failure` block catches and re-raises a `DomainError` to carry data out of it; the raise passes `data=` instead.
  - `catch_failure` re-raises `LockTimeoutError` and records unexpected exceptions, so no helper catches broad `Exception` to report it.
- **Why:** one catch site means one copy into the result, one diagnostic record and one place a caller looks.
- **Since:** #1214
- **Example:**
  ```python
  with catch_failure("worktree.show", worktree_id=worktree_id) as caught:
      shown = collect_worktree_show(self.paths, self.db, worktree_id)
  if caught.failure is not None:
      return WorktreeShowResult(**caught.failure.result_fields())
  ```

## Failure data and the copy into a result

- **Decision:** a failure that carries more than its code, message and remediations passes it as `data=` at the raise, and an entrypoint copies every failure into its result with `Failure.result_fields()`.
  - The data is a frozen dataclass subclassing `FailureData`, defined in the raising package's `exceptions.py`. `catch_failure` carries it onto `Failure.data`.
  - The entrypoint reads it after the block with `isinstance(caught.failure.data, <the FailureData subclass>)`. That narrows the data, not the error, so it is not a branch on the exception class.
  - `result_fields()` returns `error_code`, the message as `errors`, `remediations` and `diagnostics`, spread into the result constructor so ty checks each field. `prior_diagnostics` puts records gathered earlier first.
  - A site that replaces the remediations assigns `fields["remediations"]` on the returned dict. There is no hand-written copy of a failure's fields and no per-package builder.
  - One failure that reports several messages, such as a definition with several schema errors, is raised once with one remediation per message. Its data carries every message, its own message first, so `errors[0]` equals `Failure.message`; the site that builds the result assigns `fields["errors"]` from the data. A helper that processes several items uses the per-item collector in [results.md](results.md) instead.
  - A site that reports the failure as something other than its result's failure (a warning, a doctor check result, or a step or agent error) does not copy it into the result header. It carries the message, and the diagnostic through `error_diagnostics(failure.diagnostic)` where it keeps one, and never sets `error_code=` or `remediations=` from the failure.
- **Why:** one copy keeps every result header the same, and typed data reaches the result without a catch and re-raise inside the block.
- **Since:** #1214
- **Example:**
  ```python
  raise WorktreeError(
      ErrorCode.WORKTREE_APPLY_CONFLICT, message, remediations, data=WorktreeApplyConflict(tuple(conflicts))
  )
  # in the entrypoint, after the catch_failure block:
  conflict = caught.failure.data
  files = list(conflict.conflicting_files) if isinstance(conflict, WorktreeApplyConflict) else []
  return WorktreeApplyResult(worktree_id=worktree_id, conflicting_files=files, **caught.failure.result_fields())
  ```

## catch_failure operation names

- **Decision:** the `operation` passed to `catch_failure` is `<package>.<entrypoint>`.
  - The package is written as its folder name, such as `agents`, `sessions` or `diagnostics`, or `engine` for code under `src/dovo/engine/`.
  - The entrypoint is the method that catches, never a sub-step or another package's call.
  - Each name is unique per `catch_failure` block.
- **Why:** the name is written to every event file, so it must point at the one entrypoint that reported the failure.
- **Since:** #1106

## Lock timeouts propagate

- **Decision:** every `LockTimeoutError` propagates to the top-level `Workspace Lock Timeout` handler.
  - No package turns it into an error code, a warning or a status.
  - `catch_failure` re-raises it, so an entrypoint needs no handler of its own.
- **Why:** this is what R6's "report it" means: one handler reports contention the same way for every command.
- **Since:** #1214

## Declined confirmation is decided in core

- **Decision:** a delete that asks for confirmation takes the confirm callback in its core entrypoint.
  - The entrypoint resolves the target first; an unknown or already-removed target fails or returns without a prompt.
  - Every check that can refuse the delete, such as a protected or wrong-tier target, also runs before the prompt; only failures of the delete itself can follow it.
  - The prompt runs before the workspace lock is taken.
  - A declined prompt returns a success with `cancelled` set (see [results.md](results.md)).
  - The handler makes one call and sets no result field (R2).
- **Why:** prompting in the CLI asks to confirm deleting a target that does not exist, and splits one outcome across two layers.
- **Since:** #1214
- **Example:**
  ```python
  row = find_worktree(self.db, worktree_id)
  if confirm is not None and not confirm(row):
      return WorktreeDeleteResult(worktree_id=worktree_id, worktree=row, cancelled=True)
  with WorkspaceLock(self.paths.lock_file):
      removal = self.lifecycle.cleanup(row)
  ```
