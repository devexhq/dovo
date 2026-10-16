<!-- AUTO-GENERATED FROM rules_spec.yaml. DO NOT EDIT DIRECTLY. -->
# Architectural & Coding Invariants (Tests Domain)

> **Notice for Agents:** Code violating `BLOCKER` rules will fail verification.

- **[ARCH-001] Strict Layered Import Flow (BLOCKER):**
  Dependencies flow strictly one way: common/ -> core/ -> engine/ -> cli/. Within core/: project/ -> {db,git,worktree,catalog,inputs,sessions,status,artifacts,interaction}/ -> agents/ -> diagnostics/. Upward imports are strictly prohibited.

```python
# ✅ DO: from dovo.core.sessions import SessionLogEvent  # in engine/
# ❌ DO NOT: from dovo.engine.engine import Engine  # upward import in core/sessions/
```

- **[RENDER-003] Inline Error and Warning String Construction (SUGGESTION):**
  Construct errors, warnings, and fixes strings using inline f-strings or literals at call sites. Do not create private single-message formatting wrappers.

```python
# ✅ DO: errors.append(f"Blueprint '{path}' invalid: {err}")
# ❌ DO NOT: errors.append(self._format_validation_error(path, err))
```

- **[MODEL-001] Scoped Model Exceptions with Justifying Comment (BLOCKER):**
  Models are strict by default so unexpected data is rejected early; the few hand-authored YAML models that tolerate unknown keys are a named exception, and each explains why in a comment so the leniency stays deliberate.

  Answer yes to every criterion:
  - `MODEL-001.Q01` (judgment) Is extra='ignore' used only by the approved hand-authored YAML models, BlueprintDefinition and LoopStepBlock?

    ```python
    # ✅ DO: model_config = {"extra": "ignore", "populate_by_name": True}  # in BlueprintDefinition, core/catalog/definitions/blueprint.py
    # ❌ DO NOT: model_config = {"extra": "ignore"}  # hypothetical: on a result model such as a BaseResult subclass, which is not an approved model
    ```
  - `MODEL-001.Q02` (judgment) Does every model that uses extra='ignore' carry a comment justifying it?

    ```python
    # ✅ DO: # Hand-authored YAML loop blocks permit forward-compatible extra keys.  (directly above model_config in LoopStepBlock, core/catalog/definitions/step.py)
    # ❌ DO NOT: model_config = {"extra": "ignore"}  # hypothetical: no justifying comment above or beside it
    ```
  - `MODEL-001.Q03` (judgment) Does every model outside the approved list set strict=True in its configuration?

    ```python
    # ✅ DO: model_config = {"extra": "forbid", "strict": True}  # in common/mcp_servers.py
    # ❌ DO NOT: model_config = {"extra": "forbid"}  # hypothetical for a new model: strict=True omitted
    ```

- **[CODE-001] Full-Word Identifiers (BLOCKER):**
  Identifiers read as plain words so a reader never has to decode an invented abbreviation.

  Answer yes to every criterion:
  - `CODE-001.Q01` (judgment) Is every new or modified variable, parameter, attribute and function name spelled in full rather than shortened by dropping letters, even for familiar shortenings (exceptions are checked by Q02 and Q03)?

    ```python
    # ✅ DO: validation_result = validator.validate(document)  # hypothetical: every word spelled out
    # ❌ DO NOT: val_res = validator.validate(document); acc = []  # hypothetical: val_res and acc drop letters; other shortenings to flag include res, req, msg, val, idx, tmp, rel_path, fs, exc, err, cfg, resp
    ```
  - `CODE-001.Q02` (judgment) Is every new or modified single-letter name confined to a comprehension, a generator expression or a one-line loop?

    ```python
    # ✅ DO: any(a in ("--help", "-h") for a in raw_args)  # in cli/cli.py
    # ❌ DO NOT: e = load_entry(path); return e.name  # hypothetical: single-letter variable outside a comprehension, generator expression or one-line loop
    ```
  - `CODE-001.Q03` (judgment) Is every shortened name that remains in new or modified code either an initialism that is itself the domain term (db, cwd, ctx, id, url, cli, sha, ttl) or a framework-dictated name?

    ```python
    # ✅ DO: def invoke(self, ctx: Any) -> Any:  # in cli/cli.py, ctx dictated by Click; likewise tmp_path and monkeypatch dictated by pytest
    # ❌ DO NOT: cfg = load_config(path)  # hypothetical: cfg is not a domain-term initialism and no framework dictates it
    ```

- **[CODE-002] Logical Blank Lines Separation (NIT):**
  Blank lines mark where a function moves from one phase to the next, so a reader can see its structure at a glance; spacing that does not follow the logic hides that structure.

  Answer yes to every criterion:
  - `CODE-002.Q01` (judgment) Are distinct logical phases of a function body (setup, validate, persist, return) separated by a blank line?

    ```python
    # ✅ DO: In Engine._finalize_row (src/dovo/engine/engine.py), the load phase and its early-return branch are followed by a blank line before the "saved = store.save(...)" phase.
    # ❌ DO NOT: record = WorktreeRecord(...), a save call and "return result" on consecutive lines with no blank line between them (hypothetical: building, persisting and returning run together as one dense block)
    ```
  - `CODE-002.Q02` (judgment) Is every blank line in a function body free of separating two adjacent lines that work on the same value within one phase?

    ```python
    # ✅ DO: In Engine._finalize_row (src/dovo/engine/engine.py), the blank line at line 211 sits between the early "return" at line 210 and "saved = store.save(" at line 212, which starts a new phase and does not work on the value the "return" line ended, so the blank line is correctly placed.
    # ❌ DO NOT: store = SessionStateStore(self.db, self.paths, session_id), then a blank line, then loaded = store.load(), then a blank line, then "if not loaded.ok" (hypothetical: blank lines inserted between tightly coupled lines of one step)
    ```

- **[CODE-003] Terse Docstrings, Description Only for Hidden Contracts (SUGGESTION):**
  Docstrings stay short so a reader gets the title at a glance and finds a description only where it carries a fact the code cannot show and whose absence would cause wrong code.

  Answer yes to every criterion:
  - `CODE-003.Q01` (judgment) Does the docstring open with a one-line title stating what the thing is or does?

    ```python
    # ✅ DO: """Base for providers that edit the worktree directly instead of returning a diff."""  # title of the class in core/agents/cli_mutation.py
    # ❌ DO NOT:
    """Run the provider. Then resolve the baseline
    and build the prompt before diffing the worktree."""  # hypothetical: the opening wraps over two lines and holds two sentences, so there is no one-line title
    ```
  - `CODE-003.Q02` (judgment) Is the title free of outcome, failure-path and side-effect clauses, with a comma used only to enumerate a distinct list?

    ```python
    # ✅ DO: """Base for providers that edit the worktree directly instead of returning a diff."""  # outcomes live in the description
    # ❌ DO NOT: """Return the roots a whole-root read/write rule grants for read or write, or why a rule cannot be preserved."""  # outcome clause in the title, as in core/agents/copilot.py
    ```
  - `CODE-003.Q03` (judgment) Does a unit with a hidden contract, a data-loss or safety behavior, a non-obvious ordering constraint or a deliberate oddity carry a description stating it?

    ```python
    # ✅ DO:
    """Base for providers that edit the worktree directly instead of returning a diff.

    Edits that fail patch validation are discarded from the worktree; a failed discard is reported in the response errors."""  # CliDirectMutationAdapter in core/agents/cli_mutation.py: the discard is a safety behavior the code does not make obvious
    # ❌ DO NOT: """Base for providers that edit the worktree directly instead of returning a diff."""  # hypothetical: title only, although edits that fail validation are silently discarded from the worktree
    ```
  - `CODE-003.Q04` (judgment) Does every description state a fact the code cannot show, rather than something a reader can already see in the code?

    ```python
    # ✅ DO:
    """Base for providers that edit the worktree directly instead of returning a diff.

    Edits that fail patch validation are discarded from the worktree; a failed discard is reported in the response errors."""  # the discard behavior is not visible from the signature or the title
    # ❌ DO NOT:
    """Return the roots a rule grants.

    Uses a dict for lookup."""  # hypothetical: true, but the dict is plain to see in the body
    ```
  - `CODE-003.Q05` (judgment) Is every description free of step-by-step narration of the body?

    ```python
    # ✅ DO:
    """Base for providers that edit the worktree directly instead of returning a diff.

    Edits that fail patch validation are discarded from the worktree; a failed discard is reported in the response errors."""  # states a consequence, not the steps
    # ❌ DO NOT:
    """Run the provider.

    First resolve the baseline, then build the prompt, then run the tool."""  # hypothetical: the description retells the body step by step
    ```
  - `CODE-003.Q06` (judgment) Is every description free of text that restates the parameters, types or return annotation, or repeats the title in other words?

    ```python
    # ✅ DO:
    """Base for providers that edit the worktree directly instead of returning a diff.

    Edits that fail patch validation are discarded from the worktree; a failed discard is reported in the response errors."""  # adds a fact beyond the title and signature
    # ❌ DO NOT:
    """Return the session id.

    This returns the identifier of the session."""  # hypothetical: the description repeats the title in other words
    ```

- **[CODE-004] Docstrings Describe Only the Documented Unit (WARNING):**
  A docstring states only behavior the documented unit itself implements, so it cannot go stale when other code changes without any sign in the diff.

  Answer yes to every criterion:
  - `CODE-004.Q01` (judgment) Does each new or modified docstring avoid describing what a caller does with the unit's result?

    ```python
    # ✅ DO:
    """Render a restricted policy's allow and deny rules as dontAsk flags.

    A rule Claude Code cannot express yields its reason as a string."""  # _render_restricted in core/agents/claude_policy.py
    # ❌ DO NOT:
    """Render a restricted policy's allow and deny rules as dontAsk flags.

    The adapter surfaces a returned reason as an unsupported-policy error before the process is spawned."""  # hypothetical: describes what the caller does with the returned reason
    ```
  - `CODE-004.Q02` (judgment) Does each new or modified docstring avoid describing logic that lives in another module or class, other than a reference to a sibling in the same module?

    ```python
    # ✅ DO: """Return items without duplicates, in first-seen order."""  # _unique in core/agents/claude_policy.py, which describes only its own behavior
    # ❌ DO NOT: """Return items without duplicates; the Copilot adapter relies on this to reject repeated flags."""  # hypothetical: states behavior of another module
    ```

- **[CODE-005] One Docstring per Unit (WARNING):**
  A unit's documentation must be a single docstring, because a second bare string reads as documentation but is dead code. Extra text belongs in the docstring or nowhere.

  Answer yes to every criterion:
  - `CODE-005.Q01` (judgment) Is every new or modified module, class and function body free of a bare string statement after its first statement?

    ```python
    # ✅ DO: """Render a restricted policy as dontAsk flags."""  # hypothetical: the only string statement in the function body
    # ❌ DO NOT: """Return dontAsk flags for the rules."""  # hypothetical: followed by a second """...""" string statement that should be merged into the docstring or deleted
    ```

- **[TYPE-001] Ban on -> Any Return Annotations (BLOCKER):**
  -> Any on a public function is treated as a defect because it disables type checking transitively at every call site. Prefer object when values are only stored, compared, or passed through.

```python
# ✅ DO: def get_payload(self) -> object: ...
# ❌ DO NOT: def get_payload(self) -> Any: ...
```

- **[TYPE-002] Strict Scoping of Permitted Any (BLOCKER):**
  Any is permitted strictly in: (1) Pydantic mode='before' validators, (2) dict[str, Any] at serialization boundaries, (3) values read from user documents and compared, and (4) **kwargs: Any pass-throughs.

```python
# ✅ DO:
@field_validator('pattern', mode='before')
def _val(cls, v: Any) -> Any: ...
# ❌ DO NOT: def execute_step(step: Any) -> StepResult: ...
```

- **[TYPE-003] Banned Uses of Any (BLOCKER):**
  Banned: Any dodging import boundaries, Any as a test seam, Any where a model already exists, Any in third-party overrides (e.g. ctx: Any for click.Context), and Any filling a known generic (e.g. Popen[Any]).

```python
# ✅ DO: proc: subprocess.Popen[str] = ...
# ❌ DO NOT: proc: subprocess.Popen[Any] = ...
```

- **[TYPE-005] Precise Parameterization for Generator Types (NIT):**
  Avoid overly broad generic type annotations like Generator[Session] without complete yield/send/return parameterization. Specify all three generic parameters.

```python
# ✅ DO: def get_session() -> Generator[Session, None, None]: ...
# ❌ DO NOT: def get_session() -> Generator[Session]: ...
```

- **[TYPE-006] Scoped Pyright Suppressions With Reason (BLOCKER):**
  A bare `# type: ignore` is not honored by this repo's basedpyright config and suppresses nothing. Only `# pyright: ignore[reportRuleName]` suppresses an error, and only for one of three permitted cases, each with a one-line reason naming which applies: an intentional ill-typed test input whose subject is the raised error, a third-party stub conflict our code cannot name correctly (the SQLModel `__tablename__` case), or a platform-gated import. `reportCallIssue` and `reportArgumentType` silencing a wrong-shaped test double or fixture, and `reportIncompatibleVariableOverride` outside the SQLModel `__tablename__` stub, are never permitted suppressions: the fixture, annotation, or override is the defect to fix, not the error to hide.

```python
# ✅ DO: import msvcrt  # pyright: ignore[reportMissingImports]  # platform-gated: msvcrt does not exist on Linux
# ❌ DO NOT: queue: Queue = mock_queue  # type: ignore  # bare ignore, suppresses nothing and hides a fixable fixture type
```

- **[ENCAP-001] Expose Public Query Properties (BLOCKER):**
  Protects a class's freedom to change its internal representation. Callers learn its state through a public interface, so a refactor stays local instead of breaking every caller that reached inside.

  Answer yes to every criterion:
  - `ENCAP-001.Q01` (mechanical) Is every access to an underscore-prefixed, non-dunder member in src/dovo/ made on self, cls or mcs rather than on another object?

    ```python
    # ✅ DO: if runner.is_running: ...  # caller uses the public query property
    # ❌ DO NOT: if runner._process is not None: ...  # caller inspecting private state
    ```
  - `ENCAP-001.Q02` (judgment) Does each class that callers query for boolean state expose that state as a public property?

    ```python
    # ✅ DO: if runner.is_running: ...  # public @property on the class, backed by self._proc
    # ❌ DO NOT: if runner.get_process() is not None: ...  # caller re-derives boolean state from an exposed internal handle
    ```

- **[ENCAP-002] No Test Seams in Production Signatures (BLOCKER):**
  Keeps production signatures free of dead code written for tests. Every parameter exists because a production caller needs it, so the public surface reflects real behavior and tests do not shape production APIs.

  Answer yes to every criterion:
  - `ENCAP-002.Q01` (judgment) Is every parameter, kwarg or callback with a default value in a new or modified function in src/dovo/ passed by at least one production caller?

    ```python
    # ✅ DO: def run(self, cwd: Path) -> RunOutcome: ...  # tests monkeypatch at boundary
    # ❌ DO NOT: def run(self, cwd: Path, _runner: GitRunner | None = None) -> RunOutcome: ...  # only tests pass _runner
    ```

- **[PERF-001] No Ad-Hoc DB Instantiations in Loops (BLOCKER):**
  Never instantiate a repository or DovoDb inside loops or test helpers. Inject pre-initialized instances to prevent N+1 SQLite connection/migration checks.

```python
# ✅ DO:
repo = SessionsRepository(path)
for item in items: repo.create(item)
# ❌ DO NOT: for item in items: repo = SessionsRepository(path); repo.create(item)
```

- **[FS-001] Atomic File Writes via Temporary Sibling (BLOCKER):**
  Config and state files are never left half-written, because they are written atomically instead of in-place.

  Answer yes to every criterion:
  - `FS-001.Q01` (judgment) Is every write to a config or state file made through Filesystem.atomic_write_json or Filesystem.atomic_write_text?

    ```python
    # ✅ DO: Filesystem.atomic_write_json(config_path, data)
    # ❌ DO NOT: with open(config_path, 'w') as f: json.dump(data, f)  # hypothetical in-place write
    ```
  - `FS-001.Q02` (judgment) Is every config or state file write free of a direct open(..., 'w') call?

    ```python
    # ✅ DO: Filesystem.atomic_write_text(state_path, text)  # hypothetical caller; no open() call at the call site
    # ❌ DO NOT: with open(state_path, 'w') as f: f.write(text)  # hypothetical in-place write
    ```
  - `FS-001.Q03` (judgment) Is every config or state file write free of a direct Path.write_text call?

    ```python
    # ✅ DO: Filesystem.atomic_write_text(config_path, text)  # hypothetical caller
    # ❌ DO NOT: config_path.write_text(text)  # hypothetical in-place write
    ```
  - `FS-001.Q04` (judgment) Does each atomic write helper write to a .tmp sibling of the target and swap it into place with Path.replace?

    ```python
    # ✅ DO: atomic_write_text in src/dovo/common/filesystem/services/operations.py writes to path.with_name(f"{path.name}.tmp") and then calls tmp_path.replace(path)
    # ❌ DO NOT: A hypothetical helper that opens the target path directly in 'w' mode and never calls Path.replace
    ```
  - `FS-001.Q05` (judgment) Does each atomic write helper flush and call os.fsync on the temporary file before swapping it into place?

    ```python
    # ✅ DO: atomic_write_text in src/dovo/common/filesystem/services/operations.py calls f.flush() and os.fsync(f.fileno()) before tmp_path.replace(path)
    # ❌ DO NOT: A hypothetical helper that calls tmp_path.replace(path) without flushing or calling os.fsync first
    ```

- **[FS-002] Advisory Cross-Process Locking (BLOCKER):**
  Concurrent dovo processes must not corrupt shared worktree, catalog or state data, so mutations are serialized by the shared advisory workspace lock and a failure to obtain it is reported rather than ignored.

  Answer yes to every criterion:
  - `FS-002.Q01` (judgment) Is every worktree, catalog or state mutation that can run in more than one process at once performed while holding the .dovo/.lock advisory lock?

    ```python
    # ✅ DO: with WorkspaceLock(self.paths.lock_file): return self.lifecycle.create(...)  # in core/worktree/worktree.py
    # ❌ DO NOT: self.lifecycle.create(...)  # hypothetical: mutates the shared .dovo directory without acquiring the lock
    ```
  - `FS-002.Q02` (judgment) Is the lock acquired through WorkspaceLock from common/lock.py?

    ```python
    # ✅ DO: from dovo.common.lock import WorkspaceLock  # then: with WorkspaceLock(paths.lock_file):
    # ❌ DO NOT: fcntl.flock(open(lock_path, "w"), fcntl.LOCK_EX)  # hypothetical: hand-rolled locking outside common/lock.py
    ```
  - `FS-002.Q03` (judgment) Is LockTimeoutError handled where the lock is acquired or by a caller that reports it?

    ```python
    # ✅ DO: except LockTimeoutError as exc: return ArtifactsPruneResult(status=ArtifactsPruneStatus.LOCKED, ..., errors=[f"Failed to acquire workspace lock: {exc}"])  # in core/artifacts/services/prune.py
    # ❌ DO NOT: except LockTimeoutError: pass  # hypothetical: timeout swallowed, caller sees no failure
    ```

- **[COMPAT-001] Strict Public-Only Backwards Compatibility (BLOCKER):**
  Users and scripts depend on the surfaces they interact with directly, so those surfaces keep working across changes. Compatibility is kept only for them, and nowhere else.

  Answer yes to every criterion:
  - `COMPAT-001.Q01` (judgment) Does the change leave every existing CLI command, subcommand, argument and flag usable as before, with no breaking rename or removal?

    ```python
    # ✅ DO: "--path", "-p" on the global callback in cli/cli.py  # option name and short form left unchanged
    # ❌ DO NOT: # hypothetical: renaming the --path option to --workspace without keeping --path as an alias
    ```
  - `COMPAT-001.Q02` (judgment) Does the change leave existing configuration files and blueprint YAML definitions (such as config.json keys and values) readable with the same meaning as before?

    ```python
    # ✅ DO: # hypothetical: a new optional config.json key added with a default, so existing config files load unchanged
    # ❌ DO NOT: # hypothetical: renaming an existing config.json key so that files written before the change no longer load
    ```
  - `COMPAT-001.Q03` (judgment) Does the change leave the stable machine-readable output formats, such as the JSON event envelope, with the same field names and meaning as before?

    ```python
    # ✅ DO: envelope = {"event_type": data_type.__name__, "payload": formatter.to_json_serializable(data)}  # in cli/ui/dispatcher.py, envelope keys unchanged
    # ❌ DO NOT: # hypothetical: renaming the envelope key "event_type" to "type" in the JSON output
    ```

- **[COMPAT-002] Ban on Internal Compatibility Shims and Aliases (BLOCKER):**
  Internal refactors must not leave old names behind, so internal code carries one name per symbol and no dead compatibility layers; callers and tests are updated to the new name instead.

  Answer yes to every criterion:
  - `COMPAT-002.Q01` (judgment) Is the change free of backwards-compatibility aliases that keep an old internal name pointing at a renamed symbol?

    ```python
    # ✅ DO: Hypothetical: a helper is renamed from _unlock_fd to unlock_file_descriptor and the old name no longer exists anywhere in the module.
    # ❌ DO NOT: Hypothetical: after renaming _unlock_fd, the module keeps the line "_unlock = _unlock_fd  # internal shim alias".
    ```
  - `COMPAT-002.Q02` (judgment) Is the change free of deprecated or compatibility properties, wrapper methods and shim layers that exist only to keep an old internal interface working?

    ```python
    # ✅ DO: Hypothetical: a renamed internal property replaces the old one outright, with no forwarding property left on the class.
    # ❌ DO NOT: Hypothetical: a class keeps "@property def old_name(self): return self.new_name" only so existing internal callers keep working.
    ```
  - `COMPAT-002.Q03` (judgment) Were all internal callers and tests updated directly to the new name when an internal symbol was renamed?

    ```python
    # ✅ DO: Hypothetical: a rename of _unlock_fd comes with edits to every internal call site and test that referenced it.
    # ❌ DO NOT: Hypothetical: a rename leaves internal callers on the old name and adds an alias so they keep working instead of editing them.
    ```

- **[COMPAT-003] Greenfield Architecture Default (BLOCKER):**
  The repository is treated as greenfield, so superseded code is replaced rather than carried alongside its replacement, unless an issue explicitly demands otherwise. This keeps one implementation of each behavior.

  Answer yes to every criterion:
  - `COMPAT-003.Q01` (judgment) Does the change leave a single implementation of the behavior it replaces, with no superseded implementation kept alongside the new one?

    ```python
    # ✅ DO: # hypothetical: the legacy loader is deleted and its callers now use the new unified loader
    # ❌ DO NOT: # hypothetical: LegacyLoader stays in the tree beside UnifiedLoader and both remain reachable
    ```
  - `COMPAT-003.Q02` (judgment) Does the change avoid adding a fallback branch or fallback adapter that routes to a superseded path?

    ```python
    # ✅ DO: # hypothetical: return UnifiedLoader().load()  # directly replaces the legacy loader
    # ❌ DO NOT: if config.use_legacy: return LegacyLoader().load()  # hypothetical: unnecessary compatibility branch
    ```
  - `COMPAT-003.Q03` (judgment) Does the change avoid planning a deprecation window for a superseded path?

    ```python
    # ✅ DO: # hypothetical: the old path is removed in this change set, with no warning period
    # ❌ DO NOT: # hypothetical: warnings.warn("load_legacy is deprecated, removed in 2 releases"); the old path keeps working
    ```
  - `COMPAT-003.Q04` (judgment) Are all callers of a superseded path updated in the same change set that replaces it?

    ```python
    # ✅ DO: # hypothetical: the loader is replaced and every call site is switched to the new one in the same change set
    # ❌ DO NOT: # hypothetical: the new loader is added, and callers are left on the old one to migrate later
    ```

- **[TEST-001] Test at Contracts, Not Implementation (BLOCKER):**
  Assert on deliberate contracts (BaseResult object, exact JSON dict, exit code, file on disk, git ref), never implementation details (rendered layout, private helpers, call ordering, constructor assignments).

```python
# ✅ DO: assert result == BlueprintLoadResult(status=BlueprintLoadStatus.OK, blueprint=expected_bp)
# ❌ DO NOT: assert service._internal_cache_size == 1; assert mock_parser.call_count == 1
```

- **[TEST-002] 1:1 Source Parity Layout (BLOCKER):**
  Test structure mirrors src/dovo/ 1:1 under tests/, one test file per source module, and every test directory carries an __init__.py because basenames repeat across the tree. Five mappings are fixed: src/dovo/common/<m>.py to tests/common/test_<m>.py, src/dovo/core/<domain>/<m>.py to tests/core/<domain>/test_<m>.py, src/dovo/cli/ui/formatters/<domain>/<name>.py to tests/cli/ui/formatters/<domain>/test_<name>.py, and src/dovo/cli/<command>/commands/<action>.py to tests/cli/<command>/test_<command>_<action>.py — a subdirectory per CLI command domain, mirroring src/dovo/cli/<command>/ (the commands/ subpackage level collapses; a per-domain conftest.py holds fixtures that domain's test files share, never fixtures another domain needs). The fifth is src/dovo/engine/<m>.py to tests/engine/test_<m>.py (executors/ and services/ subpackages mirror as tests/engine/executors/ and tests/engine/services/). One test file per CLI command action, not one file per command domain. Every source module must have a corresponding test file; there is no exemption list. Grouping several formatters, domains, or command actions into one part-numbered or collapsed file is prohibited.

```python
# ✅ DO: src/dovo/cli/worktree/commands/worktree_create.py -> tests/cli/worktree/test_worktree_create.py
# ❌ DO NOT: tests/cli/commands/test_worktree_create.py  # flat under tests/cli/commands/, not nested under tests/cli/worktree/
```

- **[TEST-003] Standardized Test Naming and Vague Name Ban (BLOCKER):**
  Test classes must be named *Tests. Methods must follow test_<condition>_<outcome>. Banned vague names with no outcome: test_ok, test_success, test_present, test_missing, test_blank, test_basic, test_default, test_works, test_timeout, test_no_op, test_help. No 'should' prefix.

```python
# ✅ DO:
class ConfigLoaderTests:
    def test_missing_config_returns_not_found_status(self): ...
# ❌ DO NOT:
class TestConfig:
    def test_ok(self): ...
    def test_should_timeout(self): ...
```

- **[TEST-004] CLI Tier Matrix and Handler Test Necessity (BLOCKER):**
  Every CLI command action requires at least one real *CliIntegrationTests suite invoking runner.invoke to pin option and argument binding, exit codes, and dispatcher output. The suite must cover five scenario classes: (1) happy path exit 0 with default arguments, (2) option and argument binding, ensuring every declared Typer option, flag, and argument is exercised in at least one invocation to verify CLI input binding and propagation to the domain layer without duplicating domain branch matrices, (3) failure path with the expected non-zero exit code when the domain returns a non-ok result, (4) --format json emitting the wire schema, and (5) any interactive confirmation or abort branch. *RootTests calling the handler directly with a CliContext are added only when an author judges the handler owns logic the domain layer does not: input coercion, branch selection across services, result composition from more than one call, or an interactive abort path. A pass-through handler is fully compliant with zero root tests; a root test is never required per action. A root test may never restate a contract already asserted under tests/core/ for the same result type.

```python
# ✅ DO: class ConfigSetRootTests: ...  # handler owns string-to-bool coercion, so it earns a root test
# ❌ DO NOT: class ConfigShowRootTests: ...  # pass-through handler re-asserting the ConfigLoadResult from tests/core/config/
```

- **[TEST-005] Formatter Presentation Contract Protocol (BLOCKER):**
  Every formatter under cli/ui/formatters/<domain>/ is tested in its own file at tests/cli/ui/formatters/<domain>/test_<name>.py against three contracts built from FormatterCase: view model transform equality, JSON wire format as an exact literal dict pinned at both a fully populated and an empty or sparse boundary state, and a Rich render at pinned width 160 asserting only semantic values taken from case.view. Where the formatter has no derived view model, meaning transform is provably the identity and the declared view type is the input event type, the transform test is omitted and the remaining two contracts are mandatory; an alias such as SomeView = SomeEvent must not be introduced to manufacture a third test. Never assert to_json_serializable(data) == transform(data).model_dump(), and never assert a label, border, glyph, padding, or full sentence in the render test.

```python
# ✅ DO: assert formatter.to_json_serializable(data) == {"health": "ok", "warnings": []}  # exact literal
# ❌ DO NOT: ErrorPanelView = ErrorPanelEvent  # alias so an identity transform can be asserted
```

- **[TEST-006] Parameterization-First for Sibling Variations (BLOCKER):**
  Parameterization is the primary approach for testing variations of the same contract or function. Never duplicate test functions across input variants, polymorphic target types (e.g. BaseModel vs dict), or invalid payload permutations. Never use `for` loops or stacked assertions over scenarios. Use `@pytest.mark.parametrize` with explicit `pytest.param(..., id="...")` labels. Separate `def test_*` methods are reserved for fundamentally distinct lifecycles, differing fixture requirements, or divergent assertion contracts.

```python
# ✅ DO: @pytest.mark.parametrize("target", [pytest.param(model, id="model"), pytest.param(dict, id="dict")])
# ❌ DO NOT: def test_eval_model(self): ...; def test_eval_dict(self): ...  # duplicate sibling methods
```

- **[TEST-007] Assert Domain Invariants on Results; Pin Schemas in Dedicated Contract Tests (SUGGESTION):**
  Protects tests that fail only when behavior breaks. Assertions target the domain outcome under test, and model shapes and defaults are pinned once in contract tests, so unrelated changes do not break scenario tests.

  Answer yes to every criterion:
  - `TEST-007.Q01` (judgment) Does every field assertion in a scenario test check a field that carries the scenario's domain outcome rather than an incidental envelope field or default?

    ```python
    # ✅ DO: assert result.status == WorktreeDiffStatus.NOT_FOUND; assert 'not found' in result.errors[0].lower()
    # ❌ DO NOT: assert result.status == WorktreeDiffStatus.NOT_FOUND; assert result.error_code is None; assert result.warnings == []; assert result.fixes == []  # incidental defaults in a not-found scenario
    ```
  - `TEST-007.Q02` (judgment) Does each scenario or integration test of a specific behavior or error condition use targeted field assertions instead of whole-model equality?

    ```python
    # ✅ DO: assert result.status == WorktreeDiffStatus.NOT_FOUND; assert result.worktree_id == 'wt-1'
    # ❌ DO NOT: assert result == WorktreeDiffResult(status=WorktreeDiffStatus.NOT_FOUND, worktree_id='wt-1')  # whole-model equality for a status check
    ```
  - `TEST-007.Q03` (judgment) Does each new or modified model with defaults or a serialization contract have a dedicated contract test in tests/common/ or tests/core/*/test_models.py that asserts them?

    ```python
    # ✅ DO: tests/core/interaction/test_models.py::test_serialized_shape_and_defaults_are_pinned pins the shape and defaults of the interaction models
    # ❌ DO NOT: A new BaseResult subclass with default_factory fields and no test in tests/core/*/test_models.py asserting its defaults
    ```
  - `TEST-007.Q04` (judgment) Are model shape, default values and serialization asserted only in dedicated contract tests rather than in scenario or integration tests?

    ```python
    # ✅ DO: A diff scenario test in tests/core/worktree/test_worktree.py asserts only result.status, while defaults are pinned in a test_models.py contract test
    # ❌ DO NOT: A variant of that diff test that also asserts result.warnings == [] and result.fixes == [] to pin the model defaults
    ```
  - `TEST-007.Q05` (judgment) Does each model have at most two dedicated contract tests?

    ```python
    # ✅ DO: tests/core/interaction/test_models.py::test_serialized_shape_and_defaults_are_pinned is the single test pinning shape and defaults for its model
    # ❌ DO NOT: A test_models.py file with three or more separate tests that each re-pin the defaults of the same model
    ```
  - `TEST-007.Q06` (judgment) Does each test of a wire payload with a published schema contract, such as a CLI JSON formatter, verify the exact wire payload?

    ```python
    # ✅ DO: assert_json_payload_matches_published_shape(WorktreeDiffFormatter, case.data, expected_payload)  # tests/cli/ui/formatters/worktree/test_worktree_diff.py
    # ❌ DO NOT: assert json.loads(output)['status'] == 'not_found'  # the rest of the published payload goes unverified
    ```
  - `TEST-007.Q07` (judgment) Does each test avoid faux-matchers such as ANY_TIMESTAMP and ANY_UUID where their only purpose is to satisfy whole-object matching on database records or unowned system state?

    ```python
    # ✅ DO: assert record.branch_name == 'feature-x'; assert record.status == WorktreeStatus.ACTIVE  # deterministic business fields asserted directly
    # ❌ DO NOT: assert record == expected  # expected substitutes the ANY_TIMESTAMP faux-matcher for created_at and updated_at only to allow whole-object matching
    ```

- **[TEST-008] Test Double Realism and Production Types (BLOCKER):**
  Test doubles must be types production actually passes or implement a Protocol production is typed against. Never build a stub whose interface is the union of every branch in a hasattr chain.

```python
# ✅ DO: class FakeBaseAgentProvider(BaseAgentProvider): def _invoke(self, request: AgentRequest) -> AgentResponse: ...
# ❌ DO NOT: class MockAdapter: def __getattr__(self, name): return MagicMock()
```

- **[TEST-009] Ban on Negative Existence Tests for Deleted Symbols (BLOCKER):**
  Tests prove what the live codebase does, never the historical outcome of a refactor. When dead or obsolete functions, classes, or aliases are removed from production, delete the tests that called them. Never write assert not hasattr(mod, 'old_fn').

```python
# ✅ DO: # Deleted test_old_helper when old_helper was removed from production
# ❌ DO NOT: assert not hasattr(filesystem, 'old_helper')  # negative existence test
```

- **[TEST-010] Complete Assertions Invariant (BLOCKER):**
  assert obj is not None is not an assertion; if it is the only assert, the test is unfinished. Use isinstance only when it distinguishes two real code paths (basedpyright already proves the rest).

```python
# ✅ DO: assert result.session_id == 'wf_abcdef12'
# ❌ DO NOT: assert result is not None; assert isinstance(result, RunOutcome)
```

- **[TEST-011] Determinism and Clock Virtualization (BLOCKER):**
  No hardcoded sleeps in test bodies. Retry and backoff intervals use VirtualClock or monkeypatch the clock at the module boundary (time.monotonic, asyncio.sleep), and a retry test asserts the resulting schedule rather than that a retry eventually happened. Inject the clock and the id factory anywhere their output reaches a result field, so timestamps and generated ids are literals a test can state when asserting time-dependent contracts. Waiting on an operating system event that has no virtual equivalent, such as a process group being reaped after SIGKILL, is permitted only through a shared harness helper that owns the poll interval and deadline; tests must not open-code a poll loop. Registering spawned processes with the harness process registry is mandatory so a failing test cannot leak a process group.

```python
# ✅ DO: assert wait_pid_dead(child_pid) is True
# ❌ DO NOT:
while time.monotonic() < deadline:
    time.sleep(0.05)  # open-coded poll loop in a test module
```

- **[TEST-012] Rich Render Width Pinning (BLOCKER):**
  render_rich(renderable, width=160) is the only supported way to capture rendered output, and console width for rendered assertions is authoritatively pinned to 160. Tests must not rely on ambient terminal size or in-process os.environ['COLUMNS'] mutations. A render assertion checks semantic values carried by the view model, such as an identifier, count, or status token; it never checks a panel title, field caption, border glyph, padding, or prose sentence, because those are layout and change without any contract changing.

```python
# ✅ DO:
rendered = render_rich(formatter.to_rich(data))
assert 'wf_abcdef12' in rendered  # view value, not a caption
# ❌ DO NOT: assert 'Session ID' in rendered  # panel caption, changes with layout and pins no contract
```

- **[TEST-013] No Sole Consumer Tests (BLOCKER):**
  No test may be the sole consumer of a production symbol. If deleting the test would make production code unreachable, the production code is dead and both go. The same applies to the test harness: a builder method, fixture, or assertion helper whose only caller is its own verification test is dead harness. Delete the capability rather than testing it, and prove harness behavior through the first domain test that needs it.

```python
# ✅ DO: # WorkspaceBuilder.with_git() used by tests/core/worktree/test_services.py
# ❌ DO NOT: # StepBuilder.with_retry() exercised only by tests/harness/test_builders.py
```

- **[TEST-014] Strict Typing on Test Helpers and Fixtures (BLOCKER):**
  Annotate test helpers and fixtures as tightly as production code. Give fixtures real return types and type helper parameters against what production passes. No MagicMock in helper signatures.

```python
# ✅ DO: def create_test_session(db: DovoDb, session_id: str) -> SessionRecord: ...
# ❌ DO NOT: def create_test_session(db: Any, mock_obj: MagicMock) -> Any: ...
```

- **[TEST-015] Orchestration Path Test Coverage (WARNING):**
  Modified command orchestration paths and multi-step dispatch workflows must include covering integration tests. Merging changes with gaps in end-to-end orchestration tests leaves regression risks.

```python
# ✅ DO: class ResumeCommandCliIntegrationTests: def test_resume_executes_remaining_steps(self): ...
# ❌ DO NOT: # PR modifying Engine.resume() without integration tests for resumed step loop
```

- **[TEST-016] Public Function Happy and Unhappy Path Coverage (BLOCKER):**
  Every public function and method (no leading underscore) in a production module requires at least one happy-path test (valid inputs producing the expected successful outcome) and at least one unhappy-path test (invalid input, missing dependency, boundary condition, or failure state producing the expected error result, exception, or classified non-ok outcome). Private functions and methods (leading underscore) are covered exclusively as byproducts of their public callers and never receive dedicated test methods. The coverage gate (CI-001) validates that private paths are reached. Properties containing no branching logic beyond a single field return are exempt from the unhappy-path requirement; their happy path is satisfied by any test that reads them. Dunder methods are exempt unless they contain domain validation (e.g. a __init__ that coerces or rejects values). When a public method has multiple independent failure modes, TEST-006 parameterization is the primary tool for covering them; a separate test method per failure mode is reserved for cases with divergent fixture requirements.

```python
# ✅ DO:
class SessionStateStoreTests:
    def test_save_persists_state_and_increments_revision(self): ...
    def test_save_with_stale_revision_returns_conflict_error(self): ...
    def test_load_returns_persisted_state(self): ...
    def test_load_with_corrupt_json_returns_classified_failure(self): ...
# ❌ DO NOT:
class SessionStateStoreTests:
    def test__validate_schema_version(self): ...  # dedicated test for private method
    def test_save_persists_state(self): ...  # happy path only, no unhappy path for save()
```

- **[TEST-017] CLI Runner Assertion Boundary (BLOCKER):**
  CLI runner tests assert wiring and observable behavior: exit codes, --format json payloads as exact literal dicts, resulting disk or git state, and rendered CLI output. A rendered-output assertion is not restricted to a published error code token; asserting a literal status label, panel line, or full rendered string is permitted, ideally pinned via snapshot testing so the assertion cannot drift silently. Never assert help text wording; assert command registration and option names through Click metadata instead.

```python
# ✅ DO:
assert result.exit_code == 1
assert "Status: valid with warnings" in result.stdout  # literal rendered output, not restricted to an error-code token
# ❌ DO NOT: assert "Show the current configuration value" in result.output  # help text wording; assert Click metadata instead
```

- **[TEST-018] Test Layer Import Boundary (BLOCKER):**
  tests/core/** must never import dovo.cli.*. A core test proves the domain layer's contract independent of any presentation concern, and an import of the CLI package from a core test either leaks a presentation dependency into the domain suite or signals the test belongs under tests/cli/ instead.

```python
# ✅ DO: from dovo.core.worktree.prune import prune_worktrees  # tests/core/worktree/test_prune.py
# ❌ DO NOT: from dovo.cli.worktree.commands.prune import prune_command  # imported from tests/core/worktree/test_prune.py
```

- **[TEST-019] Command Result DTO Spy Scope for CLI Integration Tests (BLOCKER):**
  A *CliIntegrationTests suite may pin the exact domain DTO a command handler produces, beyond what --format json's wire payload already proves, with a call-through spy fixture on ui_dispatcher.dispatch that still exercises the real formatter and render path (never a replacement stub, so this does not trip TEST-008). What that spy may be asserted against is scoped to one thing: the command action's own terminal BaseResult — the same type its domain entrypoint or service call returns and the JSON envelope wraps (WorktreeCreateResult for dovo worktree create, DovoStatusResult for dovo status). It is never used to assert on other objects the same invocation dispatches — MessageEvent, WarningEvent, PromptEvent, or a lifecycle/progress event — even when the spy's fixture captures them incidentally. A command action known to dispatch only its own terminal result per invocation may assert the spy's sole captured item directly; a command action that also dispatches other event types must first isolate the captured instance of its own Result type rather than assume position or length. This is a genuine contract comparison against a BaseResult (TEST-001's good pattern), not a call-count check on a mocked collaborator, because the spy calls through to production and the assertion targets the DTO's fields, not the fact that dispatch fired.

```python
# ✅ DO: assert len(dispatch_spy) == 1; assert isinstance(dispatch_spy[0], WorktreeCreateResult)  # worktree create dispatches only its own terminal result
# ❌ DO NOT: assert isinstance(dispatch_spy[-1], MessageEvent)  # asserting an incidental progress event through the result spy
```

- **[TEST-020] Test-Layer Docstrings Are Terse Contracts Without Tags (WARNING):**
  A test method docstring exists only when it adds a fact the test name and assertions do not, such as an exact literal, an ordering contract, or the inputs behind a parametrized case. It is one imperative sentence stating the outcome, with no `[tier-N/type]` tag, no `<symbol>:` or `dovo <command>:` prefix, and every line within 120 columns. The execution tier follows from the test file path. A docstring on any other unit under tests/ (helper, fixture, test double, class, module) opens with a one-line title stating what the unit is or does, with no outcome clause and no list of steps. It adds a description only for a hidden contract, a safety behavior, or a non-obvious ordering constraint. It does not retell the body, restate parameters, types, or the return annotation, or describe how another module or caller behaves. Every unit carries exactly one docstring, so a bare string statement after it is dead code.

```python
# ✅ DO:
"""Exit 1 with 'Maximum active worktrees reached' in stdout when 3 worktrees already exist."""
"""Build the registered provider with its winning credential set and a fake finished-stream runner."""  # helper
# ❌ DO NOT:
"""[tier-3/integration] dovo worktree create: 3 prior worktrees exist -> exit 1, 'Maximum active worktrees reached' in stdout."""
"""Give the provider's winning credential 'test-token', patch a finished-stream runner, and return both."""  # helper: lists steps and restates the return type
```

- **[DOC-001] Architecture Doc Structural Gate (BLOCKER):**
  Update docs/agents/architecture.md structure sections ONLY when package layout, domain ownership, or import boundaries change. Do not append feature behavior essays there. Pure refactors need no architecture diff.

```python
# ✅ DO: # Updating layers tree in architecture.md for new core/prune/ domain
# ❌ DO NOT: # 100 lines of narrative prose explaining pruning heuristics in architecture.md
```

- **[DOC-003] Schema and Entity Documentation Gate (BLOCKER):**
  Update docs/agents/schemas.md only when a cross-cutting contract changes (config tier merge rules, path ownership, persistence authority, secrets handling, catalog or database scoping). Adding or changing config keys, blueprint fields, DTOs, enum members, exceptions, entrypoint methods, or commands needs no schemas.md edit.

```python
# ✅ DO: # Updating schemas.md because a config key switched from replace to union merge across tiers
# ❌ DO NOT: # Listing a new Result model's fields in schemas.md
```

- **[DOC-004] Ban on Duplicating Source in Docs (SUGGESTION):**
  Docs must not hand-copy Pydantic model signatures, field tables, or enum lists that a Read of the source already gives unambiguously. Link to the model file and document only unexpressed behavior (validators, resolution order).

```python
# ✅ DO: See [`WorktreeSession`](src/dovo/core/worktree/models.py). Worktrees live under .dovo/worktrees/.
# ❌ DO NOT:
| Field | Type | Default |
| session_id | str | required |
```

- **[DOC-005] Unavoidable Spec Tables Require Parity Tests (BLOCKER):**
  When a field table is genuinely unavoidable, meaning it is the specification for an external JSON Schema or a stable CLI output format, add or extend an automated test under tests/lint/ that fails when the table and the source disagree. The test must exist in the tree at the moment the doc claim lands, and the doc must name it by path. Citing a parity test that has not been written is a DOC-008 breach, not a pending task.

```python
# ✅ DO: # tests/lint/test_import_boundaries.py exists and is named by the doc claim it backs
# ❌ DO NOT: # "enforced by tests/lint/test_doc_parity.py" where that module is nowhere in the tree
```

- **[DOC-006] Prefer Deletion Over Accretion (SUGGESTION):**
  When updating docs, replace or delete stale bullets rather than appending parallel conflicting truths. Flag any doc that states contradictory rules.

```python
# ✅ DO: # Replaced outdated formatters.py reference with cli/ui/formatters/
# ❌ DO NOT: # Appended new rule while leaving old formatters.py text intact
```

- **[DOC-007] Canonical Terminology Invariant (SUGGESTION):**
  Adhere strictly to the Terminology table in docs/agents/architecture.md. Use only the terms it defines (Blueprint, Step, Loop step, Catalog, Run, Session, Worktree, Checkpoint), and keep each to its stated meaning.

```python
# ✅ DO: 'A blueprint containing a loop step.'
# ❌ DO NOT: 'A session executes the blueprint.'  # the act of executing is a run; the session is what it leaves behind
```

- **[DOC-008] Verifiable Doc and Rule Claims (BLOCKER):**
  A doc, skill, rule, or docstring sentence asserting that something is enforced, gated, or checked must name the enforcing artifact by path, and that path must resolve to a file in the tree. The same applies to any helper, fixture, builder, or assertion function a doc instructs an implementer to use: the symbol exists before the instruction ships. Documentation of behavior that is planned rather than built is prohibited, including in rule examples, because an exemplar is copied more often than the guideline is read. When a claim becomes false, the fix is to delete or correct the sentence in the same change, never to leave it as aspiration. A change that alters the behavior a docstring describes owns that docstring: it re-reads it against the new code and corrects or deletes any sentence the change made false.

```python
# ✅ DO: # "COLUMNS is pinned to 160 by [tool.pytest_env] in pyproject.toml" - resolvable and true
# ❌ DO NOT: # "enforced by tests/lint/test_doc_parity.py" in AGENTS.md, where the module does not exist
```

- **[CI-001] Pre-Commit Quality Suite Gate (BLOCKER):**
  Before committing, `dovo run quality` must pass: ruff format, ruff check, basedpyright src tests --level error with zero errors, mkdocs build --strict, complexity at or under 10 on changed files, and the test suite meeting the coverage floor configured in pyproject.toml under [tool.coverage.report] fail_under. That floor is the contract, it ratchets upward only as real contract tests land, and it is never lowered to make a commit pass. Coverage is a regression backstop, not a target: do not add tests to raise the percentage, and read a coverage drop caused by deleting duplicated or dead tests as a success.

```python
# ✅ DO: uv run ruff format . && uv run dovo run quality
# ❌ DO NOT: # fail_under lowered so a commit can pass, or tests added purely to reach a percentage
```

- **[CI-002] Git and Pull Request Attribution Hygiene (BLOCKER):**
  Commit messages must be semantic, imperative, and contain NO AI trailers (Co-authored-by: Cursor, Co-authored-by: ...). PRs must have single responsibility and contain concise Why, Approach, and Linkage sections.

```python
# ✅ DO: feat(worktree): add prune subcommand for stale worktrees
# ❌ DO NOT:
feat: prune worktrees

Co-authored-by: Cursor <cursor@cursor.sh>
```

- **[CI-003] Agentic Process State Declaration (WARNING):**
  Before executing commands or editing files, the agent must state: (1) The specific directive/doc governing this action, and (2) The target scope (e.g. specific test package or module).

```python
# ✅ DO:
Governing directive: docs/agents/testing.md
Target scope: tests/core/worktree/test_lifecycle.py
# ❌ DO NOT: Running write_to_file without stating governing directive or target scope
```

- **[CI-004] Mechanical Enforcement Parity (BLOCKER):**
  A BLOCKER rule that can be checked mechanically must have an executing check: an invariant test under tests/lint/, a prek hook, or a CI step. A declared gate whose configuration disables it, such as a coverage floor of zero or a type checker present in no hook and no workflow, counts as unenforced and must be either wired up or downgraded to a review-time WARNING. An invariant check must prove its own scope with a regression test shaped like the code it polices, and lands green by carrying an explicit burn-down allowlist of known violators; allowlist entries shrink and are never added to.

```python
# ✅ DO: # ARCH-001 enforced by tests/lint/test_import_boundaries.py
# ❌ DO NOT: # Scanner walks only tree.body functions, so class-based tests are never inspected
```
