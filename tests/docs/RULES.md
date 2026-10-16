<!-- AUTO-GENERATED FROM rules_spec.yaml. DO NOT EDIT DIRECTLY. -->
# Architectural & Coding Invariants (Tests Domain)

> **Notice for Agents:** Code violating `BLOCKER` rules will fail verification.

- **[ARCH-001] Strict Layered Import Flow (BLOCKER):**
  Imports flow one way through the layers, so a lower layer never depends on a higher one and each package can be understood, tested and changed without pulling in the code that sits above it.

  Every statement below must hold:
  - `ARCH-001.Q01` (mechanical) common/ imports nothing from core/, engine/ or cli/, other than the imports listed in LAYER_EXCEPTIONS in tests/lint/test_layer_direction.py.

    ```python
    # ✅ DO: from dovo.common.exceptions import DefinitionError  # in core/catalog/
    # ❌ DO NOT: from dovo.core.catalog.models import CatalogTier  # at runtime in common/ (hypothetical; the one real import of it, under TYPE_CHECKING in common/filesystem/models.py, is allowlisted)
    ```
  - `ARCH-001.Q02` (mechanical) core/ imports nothing from engine/ or cli/.

    ```python
    # ✅ DO: from dovo.core.sessions import SessionLogEvent  # in engine/
    # ❌ DO NOT: from dovo.engine.engine import Engine  # upward import in core/sessions/
    ```
  - `ARCH-001.Q03` (mechanical) core/ imports nothing from rich.

    ```python
    # ✅ DO: core/ returns models and leaves rendering to cli/ui/
    # ❌ DO NOT: from rich.console import Console  # in core/status/
    ```
  - `ARCH-001.Q04` (mechanical) engine/ imports nothing from cli/.

    ```python
    # ✅ DO: from dovo.core.sessions import SessionLogEvent  # in engine/
    # ❌ DO NOT: from dovo.cli.context import CliContext  # in engine/ (hypothetical module path)
    ```
  - `ARCH-001.Q05` (mechanical) git/ imports nothing from any other core/ package.

    ```python
    # ✅ DO: from dovo.common.process import is_process_group_alive  # in core/git/ (hypothetical import)
    # ❌ DO NOT: from dovo.core.project import ProjectIdentity  # in core/git/ (hypothetical import)
    ```
  - `ARCH-001.Q06` (mechanical) inputs/ imports nothing from catalog or agents.

    ```python
    # ✅ DO: from dovo.core.project import ProjectIdentity  # in core/inputs/ (hypothetical import)
    # ❌ DO NOT: from dovo.core.catalog.models import CatalogTier  # in core/inputs/ (hypothetical import)
    ```
  - `ARCH-001.Q07` (mechanical) catalog/ imports nothing from agents, sessions, engine or cli.

    ```python
    # ✅ DO: from dovo.core.inputs import ParameterInput  # in core/catalog/ (hypothetical import)
    # ❌ DO NOT: from dovo.core.sessions import SessionLogEvent  # in core/catalog/
    ```
  - `ARCH-001.Q08` (mechanical) artifacts/ imports nothing from engine.

    ```python
    # ✅ DO: from dovo.core.sessions import SessionLogEvent  # in core/artifacts/ (hypothetical import)
    # ❌ DO NOT: from dovo.engine.engine import Engine  # in core/artifacts/
    ```
  - `ARCH-001.Q09` (mechanical) agents/ imports nothing from engine.

    ```python
    # ✅ DO: from dovo.core.sessions import SessionLogEvent  # in core/agents/ (hypothetical import)
    # ❌ DO NOT: from dovo.engine.engine import Engine  # in core/agents/
    ```
  - `ARCH-001.Q10` (mechanical) sessions/ imports nothing from agents, worktree, catalog, engine or cli.

    ```python
    # ✅ DO: from dovo.core.db import get_session  # in core/sessions/ (hypothetical import)
    # ❌ DO NOT: from dovo.core.agents import BaseAgentProvider  # in core/sessions/ (hypothetical import)
    ```
  - `ARCH-001.Q11` (judgment) Each core/ package named in the chain project/ -> {db, git, worktree, catalog, inputs, sessions, status, artifacts, interaction}/ -> agents/ -> diagnostics/ imports only from packages at or before its own position in that chain.

    ```python
    # ✅ DO: from dovo.core.sessions import SessionLogEvent  # in core/diagnostics/ (hypothetical import)
    # ❌ DO NOT: from dovo.core.diagnostics import DiagnosticsReport  # in core/project/ (hypothetical import)
    ```

- **[RENDER-003] Inline Error and Warning String Construction (SUGGESTION):**
  Error, warning and fix messages stay readable where they are raised, so a reader sees the whole message at the call site instead of chasing a one-use wrapper.

  Every statement below must hold:
  - `RENDER-003.Q01` (judgment) Error, warning and fix strings are built inline with f-strings or literals at their call sites, with no private helper whose sole purpose is formatting a single error or warning string.

    ```python
    # ✅ DO: message = f"Project identity at '{path}' is invalid: {exc} (PROJECT_IDENTITY_INVALID_SCHEMA)."  # in core/project/services/identity.py
    # ❌ DO NOT: errors.append(self._format_validation_error(path, err))  # hypothetical: a private wrapper that only formats one message
    ```

- **[MODEL-001] Scoped Model Exceptions with Justifying Comment (BLOCKER):**
  Models are strict by default so unexpected data is rejected early; the few hand-authored YAML models that tolerate unknown keys are a named exception, and each explains why in a comment so the leniency stays deliberate.

  Every statement below must hold:
  - `MODEL-001.Q01` (judgment) extra='ignore' is used only by the approved hand-authored YAML models, BlueprintDefinition and LoopStepBlock.

    ```python
    # ✅ DO: model_config = {"extra": "ignore", "populate_by_name": True}  # in BlueprintDefinition, core/catalog/definitions/blueprint.py
    # ❌ DO NOT: model_config = {"extra": "ignore"}  # hypothetical: on a result model such as a BaseResult subclass, which is not an approved model
    ```
  - `MODEL-001.Q02` (judgment) Every model that uses extra='ignore' carries a comment justifying it.

    ```python
    # ✅ DO: # Hand-authored YAML loop blocks permit forward-compatible extra keys.  (directly above model_config in LoopStepBlock, core/catalog/definitions/step.py)
    # ❌ DO NOT: model_config = {"extra": "ignore"}  # hypothetical: no justifying comment above or beside it
    ```
  - `MODEL-001.Q03` (judgment) Every model outside the approved list sets strict=True in its configuration.

    ```python
    # ✅ DO: model_config = {"extra": "forbid", "strict": True}  # in common/mcp_servers.py
    # ❌ DO NOT: model_config = {"extra": "forbid"}  # hypothetical for a new model: strict=True omitted
    ```

- **[CODE-001] Full-Word Identifiers (BLOCKER):**
  Identifiers read as plain words so a reader never has to decode an invented abbreviation.

  Every statement below must hold:
  - `CODE-001.Q01` (judgment) Every new or modified variable, parameter, attribute and function name is spelled in full rather than shortened by dropping letters, even for familiar shortenings (exceptions are checked by Q02 and Q03).

    ```python
    # ✅ DO: validation_result = validator.validate(document)  # hypothetical: every word spelled out
    # ❌ DO NOT: val_res = validator.validate(document); acc = []  # hypothetical: val_res and acc drop letters; other shortenings to flag include res, req, msg, val, idx, tmp, rel_path, fs, exc, err, cfg, resp
    ```
  - `CODE-001.Q02` (judgment) Every new or modified single-letter name is confined to a comprehension, a generator expression or a one-line loop.

    ```python
    # ✅ DO: any(a in ("--help", "-h") for a in raw_args)  # in cli/cli.py
    # ❌ DO NOT: e = load_entry(path); return e.name  # hypothetical: single-letter variable outside a comprehension, generator expression or one-line loop
    ```
  - `CODE-001.Q03` (judgment) Every shortened name that remains in new or modified code is either an initialism that is itself the domain term (db, cwd, ctx, id, url, cli, sha, ttl) or a framework-dictated name.

    ```python
    # ✅ DO: def invoke(self, ctx: Any) -> Any:  # in cli/cli.py, ctx dictated by Click; likewise tmp_path and monkeypatch dictated by pytest
    # ❌ DO NOT: cfg = load_config(path)  # hypothetical: cfg is not a domain-term initialism and no framework dictates it
    ```

- **[CODE-002] Logical Blank Lines Separation (NIT):**
  Blank lines mark where a function moves from one phase to the next, so a reader can see its structure at a glance; spacing that does not follow the logic hides that structure.

  Every statement below must hold:
  - `CODE-002.Q01` (judgment) Distinct logical phases of a function body (setup, validate, persist, return) are separated by a blank line.

    ```python
    # ✅ DO: In Engine._finalize_row (src/dovo/engine/engine.py), the load phase and its early-return branch are followed by a blank line before the "saved = store.save(...)" phase.
    # ❌ DO NOT: record = WorktreeRecord(...), a save call and "return result" on consecutive lines with no blank line between them (hypothetical: building, persisting and returning run together as one dense block)
    ```
  - `CODE-002.Q02` (judgment) No blank line in a function body separates two adjacent lines that work on the same value within one phase.

    ```python
    # ✅ DO: In Engine._finalize_row (src/dovo/engine/engine.py), the blank line at line 211 sits between the early "return" at line 210 and "saved = store.save(" at line 212, which starts a new phase and does not work on the value the "return" line ended, so the blank line is correctly placed.
    # ❌ DO NOT: store = SessionStateStore(self.db, self.paths, session_id), then a blank line, then loaded = store.load(), then a blank line, then "if not loaded.ok" (hypothetical: blank lines inserted between tightly coupled lines of one step)
    ```

- **[CODE-003] Terse Docstrings, Description Only for Hidden Contracts (SUGGESTION):**
  Docstrings stay short so a reader gets the title at a glance and finds a description only where it carries a fact the code cannot show and whose absence would cause wrong code.

  Every statement below must hold:
  - `CODE-003.Q01` (judgment) The docstring opens with a one-line title stating what the thing is or does.

    ```python
    # ✅ DO: """Base for providers that edit the worktree directly instead of returning a diff."""  # title of the class in core/agents/cli_mutation.py
    # ❌ DO NOT:
    """Run the provider. Then resolve the baseline
    and build the prompt before diffing the worktree."""  # hypothetical: the opening wraps over two lines and holds two sentences, so there is no one-line title
    ```
  - `CODE-003.Q02` (judgment) The title is free of outcome, failure-path and side-effect clauses, with a comma used only to enumerate a distinct list.

    ```python
    # ✅ DO: """Base for providers that edit the worktree directly instead of returning a diff."""  # outcomes live in the description
    # ❌ DO NOT: """Return the roots a whole-root read/write rule grants for read or write, or why a rule cannot be preserved."""  # outcome clause in the title, as in core/agents/copilot.py
    ```
  - `CODE-003.Q03` (judgment) A unit with a hidden contract, a data-loss or safety behavior, a non-obvious ordering constraint or a deliberate oddity carries a description stating it.

    ```python
    # ✅ DO:
    """Base for providers that edit the worktree directly instead of returning a diff.

    Edits that fail patch validation are discarded from the worktree; a failed discard is reported in the response errors."""  # CliDirectMutationAdapter in core/agents/cli_mutation.py: the discard is a safety behavior the code does not make obvious
    # ❌ DO NOT: """Base for providers that edit the worktree directly instead of returning a diff."""  # hypothetical: title only, although edits that fail validation are silently discarded from the worktree
    ```
  - `CODE-003.Q04` (judgment) Every description states a fact the code cannot show, rather than something a reader can already see in the code.

    ```python
    # ✅ DO:
    """Base for providers that edit the worktree directly instead of returning a diff.

    Edits that fail patch validation are discarded from the worktree; a failed discard is reported in the response errors."""  # the discard behavior is not visible from the signature or the title
    # ❌ DO NOT:
    """Return the roots a rule grants.

    Uses a dict for lookup."""  # hypothetical: true, but the dict is plain to see in the body
    ```
  - `CODE-003.Q05` (judgment) Every description is free of step-by-step narration of the body.

    ```python
    # ✅ DO:
    """Base for providers that edit the worktree directly instead of returning a diff.

    Edits that fail patch validation are discarded from the worktree; a failed discard is reported in the response errors."""  # states a consequence, not the steps
    # ❌ DO NOT:
    """Run the provider.

    First resolve the baseline, then build the prompt, then run the tool."""  # hypothetical: the description retells the body step by step
    ```
  - `CODE-003.Q06` (judgment) Every description is free of text that restates the parameters, types or return annotation, or repeats the title in other words.

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

  Every statement below must hold:
  - `CODE-004.Q01` (judgment) Each new or modified docstring avoids describing what a caller does with the unit's result.

    ```python
    # ✅ DO:
    """Render a restricted policy's allow and deny rules as dontAsk flags.

    A rule Claude Code cannot express yields its reason as a string."""  # _render_restricted in core/agents/claude_policy.py
    # ❌ DO NOT:
    """Render a restricted policy's allow and deny rules as dontAsk flags.

    The adapter surfaces a returned reason as an unsupported-policy error before the process is spawned."""  # hypothetical: describes what the caller does with the returned reason
    ```
  - `CODE-004.Q02` (judgment) Each new or modified docstring avoids describing logic that lives in another module or class, other than a reference to a sibling in the same module.

    ```python
    # ✅ DO: """Return items without duplicates, in first-seen order."""  # _unique in core/agents/claude_policy.py, which describes only its own behavior
    # ❌ DO NOT: """Return items without duplicates; the Copilot adapter relies on this to reject repeated flags."""  # hypothetical: states behavior of another module
    ```

- **[CODE-005] One Docstring per Unit (WARNING):**
  A unit's documentation must be a single docstring, because a second bare string reads as documentation but is dead code. Extra text belongs in the docstring or nowhere.

  Every statement below must hold:
  - `CODE-005.Q01` (judgment) Every new or modified module, class and function body is free of a bare string statement after its first statement.

    ```python
    # ✅ DO: """Render a restricted policy as dontAsk flags."""  # hypothetical: the only string statement in the function body
    # ❌ DO NOT: """Return dontAsk flags for the rules."""  # hypothetical: followed by a second """...""" string statement that should be merged into the docstring or deleted
    ```

- **[TYPE-001] Ban on -> Any Return Annotations (BLOCKER):**
  A public function's return type keeps type checking intact at every call site instead of switching it off for each caller. Where a value is only stored, compared or passed through, object is preferred to Any.

  Every statement below must hold:
  - `TYPE-001.Q01` (judgment) The return annotation of every public function (a module-level function or a method whose name does not start with an underscore) is not Any.

    ```python
    # ✅ DO: def get_payload(self) -> object: ...  # hypothetical: callers must narrow the value before using it
    # ❌ DO NOT: def to_rich(self, data: T) -> Any:  # public abstract method of the formatter base class in src/dovo/common/types.py
    ```

- **[TYPE-002] Strict Scoping of Permitted Any (BLOCKER):**
  Any switches off type checking wherever it appears, so it is confined to the few boundaries where the value really is unconstrained, and everywhere else the type checker keeps working.

  Every statement below must hold:
  - `TYPE-002.Q01` (judgment) Every use of Any in the change is confined to one of four boundaries, namely a Pydantic mode="before" validator, a dict[str, Any] at a serialization boundary, a value read from a user document and compared, or a **kwargs: Any pass-through.

    ```python
    # ✅ DO: @field_validator("capability", mode="before") then def coerce_capability(cls, val: Any) -> Any  # in common/tool_policy.py; a pre-validator receives whatever the user wrote
    # ❌ DO NOT: def execute_step(step: Any) -> StepResult: ...  # hypothetical: step is not a permitted boundary, so it should be typed as the step model
    ```

- **[TYPE-003] Banned Uses of Any (BLOCKER):**
  Any switches off the type checker, so it is not used to hide misplaced code, a test seam, a type that already exists or a type the code already knows.

  Every statement below must hold:
  - `TYPE-003.Q01` (judgment) Every annotation is free of Any that exists only because the real type cannot be imported from the current package.

    ```python
    # ✅ DO: def run(step: StepDefinition) -> None: ...  # hypothetical: the code that needs StepDefinition was moved to a package that can import it
    # ❌ DO NOT: def run(step: Any) -> None: ...  # hypothetical: Any used because StepDefinition cannot be imported from this package
    ```
  - `TYPE-003.Q02` (judgment) Every parameter is free of an Any annotation that exists only so a test can pass a stub.

    ```python
    # ✅ DO: def run(self) -> None: ...  # hypothetical: no extra parameter, the test patches at the boundary instead
    # ❌ DO NOT: def run(self, output: Any = None) -> None: ...  # hypothetical: output is never read in production and exists for tests
    ```
  - `TYPE-003.Q03` (judgment) Every annotation is free of Any where a model that describes the value already exists.

    ```python
    # ✅ DO: def render(step: StepDefinition) -> str: ...  # hypothetical signature naming the existing model
    # ❌ DO NOT: def render(step: Any, metadata: Any) -> str: ...  # hypothetical: StepDefinition already describes step
    ```
  - `TYPE-003.Q04` (judgment) Every override of a third-party method names the third-party type instead of Any.

    ```python
    # ✅ DO: def invoke(self, ctx: click.Context) -> object: ...  # hypothetical override of a Click method
    # ❌ DO NOT: def invoke(self, ctx: Any) -> Any: ...  # hypothetical override of a Click method
    ```
  - `TYPE-003.Q05` (judgment) Every generic type is parameterized with the concrete type the code already knows, rather than Any.

    ```python
    # ✅ DO: proc: subprocess.Popen[str] = ...
    # ❌ DO NOT: proc: subprocess.Popen[Any] = ...  # hypothetical: the process is started with text=True, so Popen[str] is known
    ```

- **[TYPE-005] Precise Parameterization for Generator Types (NIT):**
  Generator annotations state the full contract of what a generator yields, accepts and returns, so readers and the type checker never have to infer the omitted parts.

  Every statement below must hold:
  - `TYPE-005.Q01` (judgment) Every Generator[...] annotation specifies all three parameters, yield, send and return, as in Generator[YieldType, SendType, ReturnType].

    ```python
    # ✅ DO: def get_session(engine: Engine) -> Generator[Session, None, None]: ...  # hypothetical: the full three-parameter form of the real function in core/db/connection.py
    # ❌ DO NOT: def get_session(engine: Engine) -> Generator[Session]: ...  # in core/db/connection.py
    ```

- **[TYPE-006] Scoped Pyright Suppressions With Reason (BLOCKER):**
  A type error is silenced only where the code is correct and the checker cannot express it, never to hide a defect, so every suppression is one the checker honors, is narrow, and says why it exists.

  Every statement below must hold:
  - `TYPE-006.Q01` (judgment) The code uses no bare `# type: ignore`, which this repo's basedpyright config does not honor and which suppresses nothing.

    ```python
    # ✅ DO: self.value = compute()  # pyright: ignore[reportAttributeAccessIssue]  # stub conflict: third-party stub cannot name the attribute (hypothetical line)
    # ❌ DO NOT: queue: Queue = mock_queue  # type: ignore  # bare ignore, suppresses nothing and hides a fixable fixture type (hypothetical line)
    ```
  - `TYPE-006.Q02` (judgment) Every `# pyright: ignore[reportRuleName]` names the specific rule it silences in brackets.

    ```python
    # ✅ DO: _IncompleteProvider()  # pyright: ignore[reportAbstractUsage]  # intentional ill-typed input: the subject is the raised TypeError  (tests/core/agents/test_base.py)
    # ❌ DO NOT: value = call()  # pyright: ignore  # silences every rule on the line (hypothetical line)
    ```
  - `TYPE-006.Q03` (judgment) Every `# pyright: ignore[...]` carries a one-line reason that names which permitted case applies.

    ```python
    # ✅ DO: _IncompleteProvider()  # pyright: ignore[reportAbstractUsage]  # intentional ill-typed input: the subject is the raised TypeError  (tests/core/agents/test_base.py)
    # ❌ DO NOT: resume = command.commands["resume"]  # pyright: ignore[reportAttributeAccessIssue]  (tests/lint/test_step_schema_parity.py; an existing line with no reason after the ignore)
    ```
  - `TYPE-006.Q04` (judgment) Every `# pyright: ignore[...]` is one of the three permitted cases: an intentional ill-typed test input whose subject is the raised error, a third-party stub conflict our code cannot name correctly, or a platform-gated import.

    ```python
    # ✅ DO: import msvcrt  # pyright: ignore[reportMissingImports]  # platform-gated: msvcrt does not exist on Linux (src/dovo/common/lock.py has this import and ignore; the reason text is a hypothetical addition)
    # ❌ DO NOT: result = parse(raw)  # pyright: ignore[reportReturnType]  # inconvenient to annotate (hypothetical line; fix the annotation instead)
    ```
  - `TYPE-006.Q05` (judgment) `reportCallIssue` or `reportArgumentType` is never suppressed to silence a wrong-shaped test double or fixture.

    ```python
    # ✅ DO: CostRecord(...)  # pyright: ignore[reportCallIssue] # intentional: omitted project_id is this test's subject (tests/core/db/test_models.py; the call is the ill-typed input under test)
    # ❌ DO NOT: service = Service(queue=fake_queue)  # pyright: ignore[reportArgumentType]  # the fake does not match the Queue type (hypothetical line; fix the fixture type instead)
    ```
  - `TYPE-006.Q06` (judgment) `reportIncompatibleVariableOverride` is suppressed only on the SQLModel `__tablename__` stub conflict.

    ```python
    # ✅ DO: __tablename__: ClassVar[str] = "worktrees"  # pyright: ignore[reportIncompatibleVariableOverride] (src/dovo/core/db/models.py, WorktreeRecord)
    # ❌ DO NOT: name: str = "x"  # pyright: ignore[reportIncompatibleVariableOverride]  # in a subclass whose base declares name: int (hypothetical line; fix the override instead)
    ```

- **[ENCAP-001] Expose Public Query Properties (BLOCKER):**
  Protects a class's freedom to change its internal representation. Callers learn its state through a public interface, so a refactor stays local instead of breaking every caller that reached inside.

  Every statement below must hold:
  - `ENCAP-001.Q01` (mechanical) Every access to an underscore-prefixed, non-dunder member in src/dovo/ is made on self, cls or mcs rather than on another object.

    ```python
    # ✅ DO: if runner.is_running: ...  # caller uses the public query property
    # ❌ DO NOT: if runner._process is not None: ...  # caller inspecting private state
    ```
  - `ENCAP-001.Q02` (judgment) Each class that callers query for boolean state exposes that state as a public property.

    ```python
    # ✅ DO: if runner.is_running: ...  # public @property on the class, backed by self._proc
    # ❌ DO NOT: if runner.get_process() is not None: ...  # caller re-derives boolean state from an exposed internal handle
    ```

- **[ENCAP-002] No Test Seams in Production Signatures (BLOCKER):**
  Keeps production signatures free of dead code written for tests. Every parameter exists because a production caller needs it, so the public surface reflects real behavior and tests do not shape production APIs.

  Every statement below must hold:
  - `ENCAP-002.Q01` (judgment) Every parameter, kwarg or callback with a default value in a new or modified function in src/dovo/ is passed by at least one production caller.

    ```python
    # ✅ DO: def run(self, cwd: Path) -> RunOutcome: ...  # tests monkeypatch at boundary
    # ❌ DO NOT: def run(self, cwd: Path, _runner: GitRunner | None = None) -> RunOutcome: ...  # only tests pass _runner
    ```

- **[PERF-001] No Ad-Hoc DB Instantiations in Loops (BLOCKER):**
  Database access reuses one pre-initialized instance, so repeated work does not pay for a new SQLite connection and migration check each time.

  Every statement below must hold:
  - `PERF-001.Q01` (judgment) Every repository or DovoDb used inside a for or while loop is created outside that loop.

    ```python
    # ✅ DO:
    db = DovoDb(paths.database_file, project_id=paths.project_id)
    for item in items: db.sessions.create(...)  # illustrative; create() takes more arguments
    # ❌ DO NOT: for item in items: db = DovoDb(paths.database_file, project_id=paths.project_id); db.sessions.create(...)  # illustrative; create() takes more arguments
    ```
  - `PERF-001.Q02` (judgment) Every helper function, including every test helper, receives its repository or DovoDb as an argument instead of instantiating one.

    ```python
    # ✅ DO: def seed_sessions(db: DovoDb, items: list[str]) -> None: ...  # illustrative helper that takes the instance
    # ❌ DO NOT: def seed_session(paths: WorkspacePaths) -> None: DovoDb(paths.database_file, project_id=paths.project_id).sessions.create(...)  # illustrative helper that builds its own instance on every call
    ```

- **[FS-001] Atomic File Writes via Temporary Sibling (BLOCKER):**
  Config and state files are never left half-written, because they are written atomically instead of in-place.

  Every statement below must hold:
  - `FS-001.Q01` (judgment) Every write to a config or state file is made through Filesystem.atomic_write_json or Filesystem.atomic_write_text.

    ```python
    # ✅ DO: Filesystem.atomic_write_json(config_path, data)
    # ❌ DO NOT: with open(config_path, 'w') as f: json.dump(data, f)  # hypothetical in-place write
    ```
  - `FS-001.Q02` (judgment) Every config or state file write is free of a direct open(..., 'w') call.

    ```python
    # ✅ DO: Filesystem.atomic_write_text(state_path, text)  # hypothetical caller; no open() call at the call site
    # ❌ DO NOT: with open(state_path, 'w') as f: f.write(text)  # hypothetical in-place write
    ```
  - `FS-001.Q03` (judgment) Every config or state file write is free of a direct Path.write_text call.

    ```python
    # ✅ DO: Filesystem.atomic_write_text(config_path, text)  # hypothetical caller
    # ❌ DO NOT: config_path.write_text(text)  # hypothetical in-place write
    ```
  - `FS-001.Q04` (judgment) Each atomic write helper writes to a .tmp sibling of the target and swaps it into place with Path.replace.

    ```python
    # ✅ DO: atomic_write_text in src/dovo/common/filesystem/services/operations.py writes to path.with_name(f"{path.name}.tmp") and then calls tmp_path.replace(path)
    # ❌ DO NOT: A hypothetical helper that opens the target path directly in 'w' mode and never calls Path.replace
    ```
  - `FS-001.Q05` (judgment) Each atomic write helper flushes and calls os.fsync on the temporary file before swapping it into place.

    ```python
    # ✅ DO: atomic_write_text in src/dovo/common/filesystem/services/operations.py calls f.flush() and os.fsync(f.fileno()) before tmp_path.replace(path)
    # ❌ DO NOT: A hypothetical helper that calls tmp_path.replace(path) without flushing or calling os.fsync first
    ```

- **[FS-002] Advisory Cross-Process Locking (BLOCKER):**
  Concurrent dovo processes must not corrupt shared worktree, catalog or state data, so mutations are serialized by the shared advisory workspace lock and a failure to obtain it is reported rather than ignored.

  Every statement below must hold:
  - `FS-002.Q01` (judgment) Every worktree, catalog or state mutation that can run in more than one process at once is performed while holding the .dovo/.lock advisory lock.

    ```python
    # ✅ DO: with WorkspaceLock(self.paths.lock_file): return self.lifecycle.create(...)  # in core/worktree/worktree.py
    # ❌ DO NOT: self.lifecycle.create(...)  # hypothetical: mutates the shared .dovo directory without acquiring the lock
    ```
  - `FS-002.Q02` (judgment) The lock is acquired through WorkspaceLock from common/lock.py.

    ```python
    # ✅ DO: from dovo.common.lock import WorkspaceLock  # then: with WorkspaceLock(paths.lock_file):
    # ❌ DO NOT: fcntl.flock(open(lock_path, "w"), fcntl.LOCK_EX)  # hypothetical: hand-rolled locking outside common/lock.py
    ```
  - `FS-002.Q03` (judgment) LockTimeoutError is handled where the lock is acquired or by a caller that reports it.

    ```python
    # ✅ DO: except LockTimeoutError as exc: return ArtifactsPruneResult(status=ArtifactsPruneStatus.LOCKED, ..., errors=[f"Failed to acquire workspace lock: {exc}"])  # in core/artifacts/services/prune.py
    # ❌ DO NOT: except LockTimeoutError: pass  # hypothetical: timeout swallowed, caller sees no failure
    ```

- **[COMPAT-001] Strict Public-Only Backwards Compatibility (BLOCKER):**
  Users and scripts depend on the surfaces they interact with directly, so those surfaces keep working across changes. Compatibility is kept only for them, and nowhere else.

  Every statement below must hold:
  - `COMPAT-001.Q01` (judgment) The change leaves every existing CLI command, subcommand, argument and flag usable as before, with no breaking rename or removal.

    ```python
    # ✅ DO: "--path", "-p" on the global callback in cli/cli.py  # option name and short form left unchanged
    # ❌ DO NOT: # hypothetical: renaming the --path option to --workspace without keeping --path as an alias
    ```
  - `COMPAT-001.Q02` (judgment) The change leaves existing configuration files and blueprint YAML definitions (such as config.json keys and values) readable with the same meaning as before.

    ```python
    # ✅ DO: # hypothetical: a new optional config.json key added with a default, so existing config files load unchanged
    # ❌ DO NOT: # hypothetical: renaming an existing config.json key so that files written before the change no longer load
    ```
  - `COMPAT-001.Q03` (judgment) The change leaves the stable machine-readable output formats, such as the JSON event envelope, with the same field names and meaning as before.

    ```python
    # ✅ DO: envelope = {"event_type": data_type.__name__, "payload": formatter.to_json_serializable(data)}  # in cli/ui/dispatcher.py, envelope keys unchanged
    # ❌ DO NOT: # hypothetical: renaming the envelope key "event_type" to "type" in the JSON output
    ```

- **[COMPAT-002] Ban on Internal Compatibility Shims and Aliases (BLOCKER):**
  Internal refactors must not leave old names behind, so internal code carries one name per symbol and no dead compatibility layers; callers and tests are updated to the new name instead.

  Every statement below must hold:
  - `COMPAT-002.Q01` (judgment) The change is free of backwards-compatibility aliases that keep an old internal name pointing at a renamed symbol.

    ```python
    # ✅ DO: Hypothetical: a helper is renamed from _unlock_fd to unlock_file_descriptor and the old name no longer exists anywhere in the module.
    # ❌ DO NOT: Hypothetical: after renaming _unlock_fd, the module keeps the line "_unlock = _unlock_fd  # internal shim alias".
    ```
  - `COMPAT-002.Q02` (judgment) The change is free of deprecated or compatibility properties, wrapper methods and shim layers that exist only to keep an old internal interface working.

    ```python
    # ✅ DO: Hypothetical: a renamed internal property replaces the old one outright, with no forwarding property left on the class.
    # ❌ DO NOT: Hypothetical: a class keeps "@property def old_name(self): return self.new_name" only so existing internal callers keep working.
    ```
  - `COMPAT-002.Q03` (judgment) All internal callers and tests were updated directly to the new name when an internal symbol was renamed.

    ```python
    # ✅ DO: Hypothetical: a rename of _unlock_fd comes with edits to every internal call site and test that referenced it.
    # ❌ DO NOT: Hypothetical: a rename leaves internal callers on the old name and adds an alias so they keep working instead of editing them.
    ```

- **[COMPAT-003] Greenfield Architecture Default (BLOCKER):**
  The repository is treated as greenfield, so superseded code is replaced rather than carried alongside its replacement, unless an issue explicitly demands otherwise. This keeps one implementation of each behavior.

  Every statement below must hold:
  - `COMPAT-003.Q01` (judgment) The change leaves a single implementation of the behavior it replaces, with no superseded implementation kept alongside the new one.

    ```python
    # ✅ DO: # hypothetical: the legacy loader is deleted and its callers now use the new unified loader
    # ❌ DO NOT: # hypothetical: LegacyLoader stays in the tree beside UnifiedLoader and both remain reachable
    ```
  - `COMPAT-003.Q02` (judgment) The change avoids adding a fallback branch or fallback adapter that routes to a superseded path.

    ```python
    # ✅ DO: # hypothetical: return UnifiedLoader().load()  # directly replaces the legacy loader
    # ❌ DO NOT: if config.use_legacy: return LegacyLoader().load()  # hypothetical: unnecessary compatibility branch
    ```
  - `COMPAT-003.Q03` (judgment) The change avoids planning a deprecation window for a superseded path.

    ```python
    # ✅ DO: # hypothetical: the old path is removed in this change set, with no warning period
    # ❌ DO NOT: # hypothetical: warnings.warn("load_legacy is deprecated, removed in 2 releases"); the old path keeps working
    ```
  - `COMPAT-003.Q04` (judgment) All callers of a superseded path are updated in the same change set that replaces it.

    ```python
    # ✅ DO: # hypothetical: the loader is replaced and every call site is switched to the new one in the same change set
    # ❌ DO NOT: # hypothetical: the new loader is added, and callers are left on the old one to migrate later
    ```

- **[TEST-001] Test at Contracts, Not Implementation (BLOCKER):**
  Tests pin the behavior callers and users depend on, so a refactor that keeps that behavior never breaks a test and a test that fails always points at a real change in what the code promises.

  Every statement below must hold:
  - `TEST-001.Q01` (judgment) Every assertion checks a deliberate contract, such as a BaseResult object, an exact JSON dict, an exit code, a file on disk or a git ref.

    ```python
    # ✅ DO: assert result == ReconciliationResult(reconciled=[], warning=None)  # in tests/core/sessions/services/test_reconcile.py; ReconciliationResult is a BaseResult
    # ❌ DO NOT: assert service._internal_cache_size == 1  # hypothetical: an internal cache is not a contract
    ```
  - `TEST-001.Q02` (judgment) The assertions avoid reading private helpers and private attributes of the code under test.

    ```python
    # ✅ DO: assert result.exit_code == 0  # in tests/cli/session/test_session_logs.py, observed through the CLI
    # ❌ DO NOT: assert service._internal_cache_size == 1  # hypothetical: reads a private attribute
    ```
  - `TEST-001.Q03` (judgment) The assertions avoid asserting mock call counts and call ordering.

    ```python
    # ✅ DO: assert result == ReconciliationResult(reconciled=[], warning=None)  # the outcome is asserted, not how it was reached
    # ❌ DO NOT: assert mock_parser.call_count == 1  # hypothetical: counts calls on a mock
    ```
  - `TEST-001.Q04` (judgment) The assertions avoid pinning rendered layout, such as exact spacing, column widths or box-drawing.

    ```python
    # ✅ DO: assert "iteration 3/5" in rendered  # in tests/cli/ui/test_live.py: checks a fact in the rendered panel, not its layout
    # ❌ DO NOT: assert output == "| name  | status |\n|-------|--------|"  # hypothetical: pins table layout
    ```
  - `TEST-001.Q05` (judgment) The assertions avoid checking how a constructor assigned its arguments to attributes.

    ```python
    # ✅ DO: assert loader.load(path) == expected_config  # hypothetical: checks what the constructed object does
    # ❌ DO NOT: assert loader.path == path  # hypothetical: only checks the constructor stored its argument
    ```

- **[TEST-002] 1:1 Source Parity Layout (BLOCKER):**
  A test's location is predictable from the source module it covers, so tests are easy to find and no source module goes untested or hides inside a shared catch-all file.

  Every statement below must hold:
  - `TEST-002.Q01` (judgment) Each src/dovo/common/<m>.py module has its tests in tests/common/test_<m>.py.

    ```python
    # ✅ DO: src/dovo/common/lock.py -> tests/common/test_lock.py
    # ❌ DO NOT: tests/common/test_locking_helpers.py  # hypothetical: covers src/dovo/common/lock.py under a name that does not mirror the module
    ```
  - `TEST-002.Q02` (judgment) Each src/dovo/core/<domain>/<m>.py module has its tests in tests/core/<domain>/test_<m>.py.

    ```python
    # ✅ DO: src/dovo/core/git/runner.py -> tests/core/git/test_runner.py
    # ❌ DO NOT: tests/core/test_runner.py  # hypothetical: covers src/dovo/core/git/runner.py but skips the git/ domain directory
    ```
  - `TEST-002.Q03` (judgment) Each src/dovo/engine/<m>.py module has its tests in tests/engine/test_<m>.py, with the executors/ and services/ subpackages mirrored as tests/engine/executors/ and tests/engine/services/.

    ```python
    # ✅ DO: src/dovo/engine/executors/conditions.py -> tests/engine/executors/test_conditions.py
    # ❌ DO NOT: tests/engine/test_conditions.py  # hypothetical: covers src/dovo/engine/executors/conditions.py but drops the executors/ subpackage level
    ```
  - `TEST-002.Q04` (judgment) Each src/dovo/cli/ui/formatters/<domain>/<name>.py module has its tests in tests/cli/ui/formatters/<domain>/test_<name>.py.

    ```python
    # ✅ DO: src/dovo/cli/ui/formatters/worktree/worktree_create.py -> tests/cli/ui/formatters/worktree/test_worktree_create.py
    # ❌ DO NOT: tests/cli/ui/formatters/test_worktree_create.py  # hypothetical: omits the worktree/ domain directory
    ```
  - `TEST-002.Q05` (judgment) Each src/dovo/cli/<command>/commands/<action>.py module has its tests in tests/cli/<command>/test_<command>_<action>.py, nested under its own tests/cli/<command>/ subdirectory with the commands/ level collapsed.

    ```python
    # ✅ DO: src/dovo/cli/worktree/commands/worktree_create.py -> tests/cli/worktree/test_worktree_create.py
    # ❌ DO NOT: tests/cli/commands/test_worktree_create.py  # flat under tests/cli/commands/, not nested under tests/cli/worktree/
    ```
  - `TEST-002.Q06` (judgment) There is one test file per CLI command action, rather than one file per command domain.

    ```python
    # ✅ DO: tests/cli/worktree/ holds test_worktree_create.py, test_worktree_delete.py and test_worktree_list.py, one per action
    # ❌ DO NOT: tests/cli/test_worktree.py  # hypothetical: a single file holding the tests for create, delete and list
    ```
  - `TEST-002.Q07` (judgment) No test file groups several formatters or several domains into one part-numbered or collapsed file.

    ```python
    # ✅ DO: Each formatter has its own file, for example tests/cli/ui/formatters/worktree/test_worktree_apply.py and test_worktree_show.py.
    # ❌ DO NOT: tests/cli/ui/test_command_formatters_part1.py  # hypothetical: part-numbered grab-bag covering several formatters
    ```
  - `TEST-002.Q08` (judgment) Every test directory carries an __init__.py.

    ```python
    # ✅ DO: tests/cli/worktree/__init__.py exists next to tests/cli/worktree/test_worktree_create.py
    # ❌ DO NOT: tests/cli/newcmd/test_newcmd_run.py with no tests/cli/newcmd/__init__.py  # hypothetical: basenames repeat across the tree, so the missing package file risks module-name collisions
    ```
  - `TEST-002.Q09` (judgment) Each per-domain conftest.py holds only fixtures that its own domain's test files share, and none that another domain needs.

    ```python
    # ✅ DO: tests/cli/worktree/conftest.py holds fixtures shared by the worktree command tests
    # ❌ DO NOT: A fixture in tests/cli/worktree/conftest.py that tests/cli/session/ also imports  # hypothetical: a fixture another domain needs belongs in a shared conftest
    ```
  - `TEST-002.Q10` (judgment) Every source module has a corresponding test file at its mirrored path, with no exemption list.

    ```python
    # ✅ DO: src/dovo/core/git/runner.py is covered by tests/core/git/test_runner.py
    # ❌ DO NOT: A new source module added with no test file and an entry in a skip or exemption list  # hypothetical
    ```

- **[TEST-003] Standardized Test Naming and Vague Name Ban (BLOCKER):**
  A test's name alone should tell a reader which behavior it pins and what is expected, so failures are legible without opening the test.

  Every statement below must hold:
  - `TEST-003.Q01` (judgment) Every test class is named with the suffix Tests.

    ```python
    # ✅ DO: class ConfigLoaderTests:  # in tests/core/config/test_loader.py
    # ❌ DO NOT: class TestConfig:  # hypothetical: prefix naming instead of the *Tests suffix
    ```
  - `TEST-003.Q02` (judgment) Every test method follows the test_<condition>_<outcome> pattern, naming both the condition and the expected outcome.

    ```python
    # ✅ DO: def test_load_missing_config_returns_failure_result(self, workspace_paths): ...  # in ConfigLoaderTests, tests/core/config/test_loader.py
    # ❌ DO NOT: def test_config(self): ...  # hypothetical: no outcome in the name
    ```
  - `TEST-003.Q03` (judgment) Every test method name is free of the banned vague names test_ok, test_success, test_present, test_missing, test_blank, test_basic, test_default, test_works, test_timeout, test_no_op and test_help.

    ```python
    # ✅ DO: def test_load_missing_config_returns_failure_result(self, workspace_paths): ...  # in ConfigLoaderTests, tests/core/config/test_loader.py
    # ❌ DO NOT: def test_ok(self): ...  # hypothetical: banned name with no outcome
    ```
  - `TEST-003.Q04` (judgment) Every test method name is free of a should prefix.

    ```python
    # ✅ DO: def test_load_missing_config_returns_failure_result(self, workspace_paths): ...  # in ConfigLoaderTests, tests/core/config/test_loader.py
    # ❌ DO NOT: def test_should_timeout(self): ...  # hypothetical: should prefix and no outcome
    ```

- **[TEST-004] CLI Tier Matrix and Handler Test Necessity (BLOCKER):**
  CLI wiring is proven through the real runner, so option binding, exit codes and output cannot silently break. Handler-level tests exist only where the handler owns logic of its own, and never repeat what the domain tests pin.

  Every statement below must hold:
  - `TEST-004.Q01` (judgment) Every CLI command action has at least one *CliIntegrationTests suite that invokes the command through runner.invoke.

    ```python
    # ✅ DO: class ConfigShowCliIntegrationTests: ...  # in tests/cli/config/test_config_show.py, calls cli_runner.invoke(app, ["-p", ..., "config", "show"])
    # ❌ DO NOT: A command action whose only tests are a *RootTests suite calling the handler directly (hypothetical: no runner.invoke suite exists for it)
    ```
  - `TEST-004.Q02` (judgment) The integration suite includes a happy-path test with default arguments that asserts exit code 0.

    ```python
    # ✅ DO: res = cli_runner.invoke(app, ["-p", str(isolated_workspace), "config", "show"])  # then asserts res.exit_code == 0 (hypothetical assertion shown; the invocation is in test_config_show.py)
    # ❌ DO NOT: A suite that only invokes the command with non-default options and never runs the plain command (hypothetical)
    ```
  - `TEST-004.Q03` (judgment) Every declared Typer option, flag and argument of the command is exercised in at least one invocation, verifying input binding and propagation to the domain layer.

    ```python
    # ✅ DO: One invocation passes "--format", "json" and another passes the positional key and value of config set, so each declared parameter is bound at least once (hypothetical combination)
    # ❌ DO NOT: A command that declares a --force flag where no invocation in its integration suite passes --force (hypothetical)
    ```
  - `TEST-004.Q04` (judgment) The integration suite avoids duplicating domain branch matrices, limiting its binding checks to what the CLI layer adds.

    ```python
    # ✅ DO: The CLI suite checks that an option reaches the domain call; the branch outcomes of that option are asserted under tests/core/ (hypothetical)
    # ❌ DO NOT: A CLI integration suite that re-runs every domain status branch of the result through runner.invoke (hypothetical)
    ```
  - `TEST-004.Q05` (judgment) The integration suite includes a failure-path test that asserts the expected non-zero exit code when the domain returns a non-ok result.

    ```python
    # ✅ DO: A test invokes "config show" in a workspace with no config.json and asserts a non-zero exit code (hypothetical assertion; the NOT_FOUND status is real, ConfigLoadStatus.NOT_FOUND)
    # ❌ DO NOT: A suite where every test asserts exit code 0 even though the domain can return an error status (hypothetical)
    ```
  - `TEST-004.Q06` (judgment) The integration suite includes a test invoking the command with --format json that asserts the emitted payload matches the wire schema.

    ```python
    # ✅ DO: res = cli_runner.invoke(app, ["-p", str(isolated_workspace), "config", "show", "--format", "json"])  # in tests/cli/config/test_config_show.py
    # ❌ DO NOT: A command that supports --format json whose suite never passes --format json (hypothetical)
    ```
  - `TEST-004.Q07` (judgment) Every interactive confirmation or abort branch of the command is exercised by the integration suite.

    ```python
    # ✅ DO: A test supplies "n" as input to the confirmation prompt and asserts the abort exit code and that nothing was changed (hypothetical)
    # ❌ DO NOT: A command that prompts for confirmation where every test bypasses the prompt with a skip flag (hypothetical)
    ```
  - `TEST-004.Q08` (judgment) Every *RootTests suite is written for a handler that owns logic the domain layer does not, such as input coercion, branch selection across services, result composition from more than one call, or an interactive abort path.

    ```python
    # ✅ DO: class DemoRootTests: ...  # hypothetical: the handler picks between two services depending on its arguments, so it earns a root test
    # ❌ DO NOT: class ConfigShowRootTests: ...  # in tests/cli/config/test_config_show.py; config_show_command only calls the domain and dispatches the result
    ```
  - `TEST-004.Q09` (judgment) Every *RootTests suite avoids restating a contract already asserted under tests/core/ for the same result type.

    ```python
    # ✅ DO: A root test asserts only what the handler itself decides, leaving the result fields to tests/core/ (hypothetical)
    # ❌ DO NOT: test_config_show_missing_config_returns_not_found in ConfigShowRootTests re-asserts the CONFIG_NOT_FOUND errors already asserted in tests/core/config/test_loader.py
    ```

- **[TEST-005] Formatter Presentation Contract Protocol (BLOCKER):**
  A formatter's view model, JSON wire format and rendered content are separate published contracts, so each is tested independently and fails only when that contract changes, not when styling does.

  Every statement below must hold:
  - `TEST-005.Q01` (judgment) Every formatter under cli/ui/formatters/<domain>/ is tested in its own file at tests/cli/ui/formatters/<domain>/test_<name>.py.

    ```python
    # ✅ DO: tests/cli/ui/formatters/status/test_dovo_status.py tests DovoStatusFormatter from src/dovo/cli/ui/formatters/status/dovo_status.py
    # ❌ DO NOT: DovoStatusFormatter tested only inside a shared tests/cli/ui/formatters/test_all_formatters.py (hypothetical file)
    ```
  - `TEST-005.Q02` (judgment) The formatter is tested through cases built from FormatterCase, each holding the input data, the expected view and the render expectations.

    ```python
    # ✅ DO: HEALTHY = FormatterCase(data=..., view=_make_status_view(...), render_expectations=[...])  # in tests/cli/ui/formatters/status/test_dovo_status.py
    # ❌ DO NOT: a test that builds the formatter input and expected strings inline in the test body, bypassing FormatterCase (hypothetical)
    ```
  - `TEST-005.Q03` (judgment) A test asserts that transform equals the expected view for a formatter that derives a view model from its input.

    ```python
    # ✅ DO: assert_transform_derives_expected_view(DovoStatusFormatter, case.data, case.view)  # in DovoStatusFormatterTests.test_transform_derives_expected_view
    # ❌ DO NOT: a DovoStatusFormatter test file with only JSON and render tests, leaving StatusView derivation unpinned (hypothetical)
    ```
  - `TEST-005.Q04` (judgment) The JSON wire format is asserted as an exact literal dict.

    ```python
    # ✅ DO: assert_json_payload_matches_published_shape(DovoStatusFormatter, case.data, {"health": "ok", "root_dir": "/workspace/my-repo", ...})  # in tests/cli/ui/formatters/status/test_dovo_status.py
    # ❌ DO NOT: assert formatter.to_json_serializable(data) == {...} replaced by a comparison against a value computed by the code under test (hypothetical)
    ```
  - `TEST-005.Q05` (judgment) The JSON wire format is pinned at both a fully populated state and an empty or sparse boundary state.

    ```python
    # ✅ DO: STATUS_PAYLOAD_CASES pins HEALTHY (fully populated) and UNINITIALIZED_NON_GIT (null fields, empty lists) in tests/cli/ui/formatters/status/test_dovo_status.py
    # ❌ DO NOT: a payload test parametrized only with HEALTHY, so null and empty-list output is never pinned (hypothetical)
    ```
  - `TEST-005.Q06` (judgment) The Rich render is asserted at the pinned width of 160 columns.

    ```python
    # ✅ DO: assert_rich_render_shows_every_view_value(DovoStatusFormatter, case.data, case.render_expectations)  # renders through render_rich with width=160 from tests/harness/formatter.py
    # ❌ DO NOT: rendering through a Console(width=80) built inside the test, so wrapping differs from the pinned width (hypothetical)
    ```
  - `TEST-005.Q07` (judgment) Every expected string in the render test is a semantic value taken from case.view, and never a label, border, glyph, padding or full sentence.

    ```python
    # ✅ DO: render_expectations=["raw-project", "feature/status-cmd", "Invalid schema"]  # view values only, in DEGRADED_RAW_CONFIG_PROJECT
    # ❌ DO NOT: assert "Project:" in rendered and "╭" in rendered  # hypothetical: asserts a label and a border glyph
    ```
  - `TEST-005.Q08` (judgment) A formatter with no derived view model omits the transform test while keeping both the JSON and the render test.

    ```python
    # ✅ DO: WarningFormatterTests in tests/cli/ui/formatters/events/test_warning.py has only test_json_payload_matches_published_shape and test_rich_render_shows_every_view_value
    # ❌ DO NOT: a WarningFormatter test file with a transform test that only checks WarningEvent equals itself (hypothetical)
    ```
  - `TEST-005.Q09` (judgment) The change is free of an alias such as SomeView = SomeEvent introduced to manufacture a third test for an identity formatter.

    ```python
    # ✅ DO: WarningFormatter(ComponentFormatter[WarningEvent]) declares no separate view type
    # ❌ DO NOT: ErrorPanelView = ErrorPanelEvent  # alias so an identity transform can be asserted (hypothetical)
    ```
  - `TEST-005.Q10` (judgment) The change is free of an assertion that to_json_serializable(data) equals transform(data).model_dump().

    ```python
    # ✅ DO: assert_json_payload_matches_published_shape(DovoStatusFormatter, case.data, expected_payload)  # expected_payload is a literal dict
    # ❌ DO NOT: assert formatter.to_json_serializable(data) == formatter.transform(data).model_dump()  # hypothetical: compares code to itself
    ```

- **[TEST-006] Parameterization-First for Sibling Variations (BLOCKER):**
  Variations of one contract are tested through a single parameterized test, so each case is named and reported separately and a new case costs one line instead of a copied test.

  Every statement below must hold:
  - `TEST-006.Q01` (judgment) Variations of the same contract or function (input variants, polymorphic target types, invalid payload permutations) are covered by one parameterized test rather than by copy-pasted or near-identical sibling test methods.

    ```python
    # ✅ DO: @pytest.mark.parametrize("target", [pytest.param(model, id="model"), pytest.param(dict, id="dict")])  # hypothetical: one test covers both target types
    # ❌ DO NOT: def test_eval_model(self): ...; def test_eval_dict(self): ...  # hypothetical: duplicate sibling methods that differ only by target type
    ```
  - `TEST-006.Q02` (judgment) Each test is free of `for` loops and stacked assertions that iterate over scenarios.

    ```python
    # ✅ DO: @pytest.mark.parametrize("pattern", [pytest.param("../x", id="dotdot"), pytest.param("/etc/**", id="absolute")])  # shape used in tests/common/test_tool_policy.py
    # ❌ DO NOT: for pattern in ["../x", "/etc/**"]: assert not is_valid(pattern)  # hypothetical: the loop hides which scenario failed
    ```
  - `TEST-006.Q03` (judgment) Every parameterized case uses pytest.param with an explicit, readable id label.

    ```python
    # ✅ DO: pytest.param("ghp_" + "a" * 36, id="github-classic")  # in tests/common/test_redact.py
    # ❌ DO NOT: @pytest.mark.parametrize("token", ["ghp_aaa", "sk-ant-aaa"])  # hypothetical: bare values, so failures report auto-generated ids
    ```
  - `TEST-006.Q04` (judgment) Separate test methods are used only where the cases have fundamentally distinct lifecycles, differing fixture requirements or divergent assertion contracts.

    ```python
    # ✅ DO: def test_create_rolls_back_on_failure(self, failing_git): ...; def test_create_returns_path(self, tmp_repo): ...  # hypothetical: different fixtures and assertion contracts justify separate methods
    # ❌ DO NOT: def test_rejects_empty_name(self): ...; def test_rejects_blank_name(self): ...  # hypothetical: same fixtures and same assertion, split only by input
    ```

- **[TEST-007] Assert Domain Invariants on Results; Pin Schemas in Dedicated Contract Tests (SUGGESTION):**
  Protects tests that fail only when behavior breaks. Assertions target the domain outcome under test, and model shapes and defaults are pinned once in contract tests, so unrelated changes do not break scenario tests.

  Every statement below must hold:
  - `TEST-007.Q01` (judgment) Every field assertion in a scenario test checks a field that carries the scenario's domain outcome rather than an incidental envelope field or default.

    ```python
    # ✅ DO: assert result.status == WorktreeDiffStatus.NOT_FOUND; assert 'not found' in result.errors[0].lower()
    # ❌ DO NOT: assert result.status == WorktreeDiffStatus.NOT_FOUND; assert result.error_code is None; assert result.warnings == []; assert result.fixes == []  # incidental defaults in a not-found scenario
    ```
  - `TEST-007.Q02` (judgment) Each scenario or integration test of a specific behavior or error condition uses targeted field assertions instead of whole-model equality.

    ```python
    # ✅ DO: assert result.status == WorktreeDiffStatus.NOT_FOUND; assert result.worktree_id == 'wt-1'
    # ❌ DO NOT: assert result == WorktreeDiffResult(status=WorktreeDiffStatus.NOT_FOUND, worktree_id='wt-1')  # whole-model equality for a status check
    ```
  - `TEST-007.Q03` (judgment) Each new or modified model with defaults or a serialization contract has a dedicated contract test in tests/common/ or tests/core/*/test_models.py that asserts them.

    ```python
    # ✅ DO: tests/core/interaction/test_models.py::test_serialized_shape_and_defaults_are_pinned pins the shape and defaults of the interaction models
    # ❌ DO NOT: A new BaseResult subclass with default_factory fields and no test in tests/core/*/test_models.py asserting its defaults
    ```
  - `TEST-007.Q04` (judgment) Model shape, default values and serialization are asserted only in dedicated contract tests rather than in scenario or integration tests.

    ```python
    # ✅ DO: A diff scenario test in tests/core/worktree/test_worktree.py asserts only result.status, while defaults are pinned in a test_models.py contract test
    # ❌ DO NOT: A variant of that diff test that also asserts result.warnings == [] and result.fixes == [] to pin the model defaults
    ```
  - `TEST-007.Q05` (judgment) Each model has at most two dedicated contract tests.

    ```python
    # ✅ DO: tests/core/interaction/test_models.py::test_serialized_shape_and_defaults_are_pinned is the single test pinning shape and defaults for its model
    # ❌ DO NOT: A test_models.py file with three or more separate tests that each re-pin the defaults of the same model
    ```
  - `TEST-007.Q06` (judgment) Each test of a wire payload with a published schema contract, such as a CLI JSON formatter, verifies the exact wire payload.

    ```python
    # ✅ DO: assert_json_payload_matches_published_shape(WorktreeDiffFormatter, case.data, expected_payload)  # tests/cli/ui/formatters/worktree/test_worktree_diff.py
    # ❌ DO NOT: assert json.loads(output)['status'] == 'not_found'  # the rest of the published payload goes unverified
    ```
  - `TEST-007.Q07` (judgment) Each test avoids faux-matchers such as ANY_TIMESTAMP and ANY_UUID where their only purpose is to satisfy whole-object matching on database records or unowned system state.

    ```python
    # ✅ DO: assert record.branch_name == 'feature-x'; assert record.status == WorktreeStatus.ACTIVE  # deterministic business fields asserted directly
    # ❌ DO NOT: assert record == expected  # expected substitutes the ANY_TIMESTAMP faux-matcher for created_at and updated_at only to allow whole-object matching
    ```

- **[TEST-008] Test Double Realism and Production Types (BLOCKER):**
  A test double must stand in for something production really uses, so tests fail when production contracts change instead of passing against an invented interface.

  Every statement below must hold:
  - `TEST-008.Q01` (judgment) Every test double or stub inherits from a type production actually passes or satisfies a typing.Protocol production is typed against.

    ```python
    # ✅ DO: class FakeAgentProvider(BaseAgentProvider): ...  # in tests/harness/fakes.py, subclassing the production base class
    # ❌ DO NOT: class MockAdapter: def __getattr__(self, name): return MagicMock()  # hypothetical: matches no production type or Protocol
    ```
  - `TEST-008.Q02` (judgment) Every test double is free of an interface built as the union of the attributes from every branch of a hasattr chain.

    ```python
    # ✅ DO: class FakeRunObserver: def on_worktree_ready(self, path, active): ...  # hypothetical: implements only the RunObserver Protocol in engine/models.py
    # ❌ DO NOT: class StubTarget: ...  # hypothetical: defines every attribute that any branch of production hasattr(target, ...) checks, so it matches no single production type
    ```

- **[TEST-009] Ban on Negative Existence Tests for Deleted Symbols (BLOCKER):**
  Tests prove what the live codebase does, never the historical outcome of a refactor, so a removed symbol leaves no trace in the test suite.

  Every statement below must hold:
  - `TEST-009.Q01` (judgment) The test suite is free of negative existence assertions such as assert not hasattr(module, name).

    ```python
    # ✅ DO: assert result.session_id == "wf_abcdef12"  # asserts live behavior (hypothetical test)
    # ❌ DO NOT: assert not hasattr(filesystem, "old_helper")  # hypothetical: proves a removed helper stays removed
    ```
  - `TEST-009.Q02` (judgment) The tests that called a function, class or alias were deleted in the same change that removed it from production.

    ```python
    # ✅ DO: # hypothetical: test_old_helper deleted when old_helper was removed from production
    # ❌ DO NOT: # hypothetical: test_old_helper kept after old_helper was removed, rewritten to expect AttributeError or a missing attribute
    ```

- **[TEST-010] Complete Assertions Invariant (BLOCKER):**
  A test must check the behavior it exists for, so a passing run shows the code did the right thing and not merely that it returned something.

  Every statement below must hold:
  - `TEST-010.Q01` (judgment) Every test checks a concrete value or effect beyond `assert x is not None`, so that `assert x is not None` is never its only assertion.

    ```python
    # ✅ DO: assert outcome.session_id == seen[0]  # in tests/engine/test_engine.py, RunOutcome.session_id is str | None
    # ❌ DO NOT: assert outcome is not None  # hypothetical: the only assertion in the test, so the test is unfinished
    ```
  - `TEST-010.Q02` (judgment) isinstance is used in a test only where it distinguishes two real code paths, rather than repeating what basedpyright already proves.

    ```python
    # ✅ DO: assert isinstance(result, ErrorResult)  # hypothetical: the call can return either a success or an error model and the test must tell which
    # ❌ DO NOT: assert isinstance(outcome, RunOutcome)  # hypothetical: engine.run is already typed to return RunOutcome, so the check proves nothing
    ```

- **[TEST-011] Determinism and Clock Virtualization (BLOCKER):**
  Tests must give the same result on every run and every machine, so they do not depend on real elapsed time or on leftover processes, and a failing test cannot leak anything into later ones.

  Every statement below must hold:
  - `TEST-011.Q01` (judgment) Every test body is free of hardcoded sleeps, other than a sleep inside the shared harness poll helper.

    ```python
    # ✅ DO: monkeypatch.setattr("dovo.common.lock.time.monotonic", fake_monotonic)  # hypothetical: the test advances a fake clock instead of waiting
    # ❌ DO NOT: time.sleep(0.05)  # bare sleep in a test body, as in _sync_and_interrupt in tests/engine/executors/test_process_group.py
    ```
  - `TEST-011.Q02` (judgment) Tests of retry or backoff intervals drive time through VirtualClock or a monkeypatched clock at the module boundary (time.monotonic, asyncio.sleep).

    ```python
    # ✅ DO: monkeypatch.setattr("dovo.engine.executors.step_executor.time.monotonic", clock.now)  # hypothetical: VirtualClock does not exist in the repo yet; the patch targets time.monotonic as imported by step_executor
    # ❌ DO NOT: time.sleep(backoff_seconds)  # hypothetical: the test waits out the real backoff interval
    ```
  - `TEST-011.Q03` (judgment) A retry test asserts the resulting schedule rather than only that a retry eventually happened.

    ```python
    # ✅ DO: assert sleeps == [1.0, 2.0, 4.0]  # hypothetical: the recorded backoff schedule
    # ❌ DO NOT: assert attempts == 3  # hypothetical: proves a retry happened but not when
    ```
  - `TEST-011.Q04` (judgment) The clock is injected anywhere its output reaches a result field, so a time-dependent test asserts literal timestamps.

    ```python
    # ✅ DO: service = Service(clock=lambda: datetime(2026, 1, 1, tzinfo=UTC)); assert result.started_at == datetime(2026, 1, 1, tzinfo=UTC)  # hypothetical constructor parameter
    # ❌ DO NOT: assert result.started_at is not None  # hypothetical: the real clock fed the field, so the test cannot state the value
    ```
  - `TEST-011.Q05` (judgment) Generated ids are injected anywhere their output reaches a result field, so a test asserts literal ids.

    ```python
    # ✅ DO: assert result.run_id == "run-1"  # hypothetical: the test supplied the id source, so it states the literal
    # ❌ DO NOT: assert len(result.run_id) == 32  # hypothetical: the id was generated internally, so the test can only check its shape
    ```
  - `TEST-011.Q06` (judgment) Every wait on an operating system event with no virtual equivalent, such as a process group reaped after SIGKILL, is done through a shared harness helper owning the poll interval and deadline, with no open-coded poll loop.

    ```python
    # ✅ DO: assert wait_pid_dead(child_pid) is True  # harness helper owns the poll interval (hypothetical: today _wait_pid_dead is private to tests/engine/executors/test_process_group.py, not a shared helper in tests/harness/)
    # ❌ DO NOT:
    while not (pid_dir / 'child.pid').exists():
        threading.Event().wait(0.05)  # hypothetical: poll loop written inside the test module
    ```
  - `TEST-011.Q07` (judgment) Every test that spawns a process registers it with the harness process registry, so a failing test cannot leak a process group.

    ```python
    # ✅ DO: process_registry.register(proc, pgid=pgid)  # ProcessRegistry in src/dovo/common/process.py; stream_isolated_process registers its child this way
    # ❌ DO NOT: subprocess.Popen([sys.executable, "-c", script])  # hypothetical: spawned in a test and never registered, so a failure leaves it running
    ```

- **[TEST-012] Rich Render Width Pinning (BLOCKER):**
  Rendered-output tests must give the same result on every machine and keep failing only when a real contract changes, not when layout, terminal size or wording shifts.

  Every statement below must hold:
  - `TEST-012.Q01` (judgment) Every capture of rendered Rich output in a test is made through render_rich from tests/harness/formatter.py.

    ```python
    # ✅ DO: rendered = render_rich(LoopLifecycleFormatter().to_rich(CONDITIONS_EVALUATED.data))  # in tests/cli/ui/formatters/events/test_loop.py
    # ❌ DO NOT: console = Console(record=True); console.print(panel); rendered = console.export_text()  # hypothetical: hand-built console instead of render_rich
    ```
  - `TEST-012.Q02` (judgment) The console width used for a rendered assertion is the pinned width of 160, with no other width passed to render_rich.

    ```python
    # ✅ DO: rendered = render_rich(formatter.to_rich(data))  # in tests/harness/formatter.py; the default width=160 applies
    # ❌ DO NOT: rendered = render_rich(formatter.to_rich(data), width=80)  # hypothetical: width other than 160
    ```
  - `TEST-012.Q03` (judgment) The test avoids relying on the ambient terminal size and on in-process os.environ["COLUMNS"] mutations.

    ```python
    # ✅ DO: render_rich(renderable)  # width is set explicitly on the Console, independent of the terminal
    # ❌ DO NOT: os.environ["COLUMNS"] = "120"; rendered = Console(record=True)...  # hypothetical: width taken from the environment or ambient terminal
    ```
  - `TEST-012.Q04` (judgment) Every render assertion checks a semantic value carried by the view model, such as an identifier, count or status token, rather than a panel title, field caption, border glyph, padding or prose sentence.

    ```python
    # ✅ DO: rendered = render_rich(formatter.to_rich(data)); assert "wf_abcdef12" in rendered  # hypothetical: a view value, not a caption
    # ❌ DO NOT: assert "Session ID" in rendered  # hypothetical: a panel caption, changes with layout and pins no contract
    ```

- **[TEST-013] No Sole Consumer Tests (BLOCKER):**
  Code exists to serve real behavior, so nothing stays alive only because a test exercises it. Unused production or harness code is deleted together with its test, not kept and verified.

  Every statement below must hold:
  - `TEST-013.Q01` (judgment) Every touched production function has a real caller in src/, so that deleting its tests would not leave it unreachable.

    ```python
    # ✅ DO: A function in src/ called by another production module, with tests that exercise it through that caller (hypothetical example)
    # ❌ DO NOT: A function in src/ whose only caller is its own unit test; the function is dead, so the function and the test are both deleted (hypothetical example)
    ```
  - `TEST-013.Q02` (judgment) Every harness symbol (builder method, fixture or assertion helper) has at least one caller outside tests/harness/.

    ```python
    # ✅ DO: WorkspaceBuilder.with_git() in tests/harness/builders/workspace.py, called by tests/cli/session/conftest.py and other domain tests
    # ❌ DO NOT: A builder method called only by its own test in tests/harness/test_builders.py (hypothetical; delete the method rather than test it)
    ```
  - `TEST-013.Q03` (judgment) Harness behavior is proven through the first domain test that needs it, rather than through a harness verification suite that exhaustively permutes options no domain test uses.

    ```python
    # ✅ DO: A new builder option is introduced together with the domain test that needs it, and that test is what proves it works (hypothetical example)
    # ❌ DO NOT: A new parametrized case in tests/harness/test_builders.py for a builder option that no domain test calls (hypothetical example)
    ```

- **[TEST-014] Strict Typing on Test Helpers and Fixtures (BLOCKER):**
  Test helpers and fixtures are code that every test leans on, so they are typed as tightly as production code and a wrong call is caught by the type checker instead of surfacing as a confusing test failure.

  Every statement below must hold:
  - `TEST-014.Q01` (judgment) Every test helper and fixture function is free of missing type annotations.

    ```python
    # ✅ DO: def workspace_paths_factory() -> Callable[[Path, Path | None], WorkspacePaths]: ...  # fixture in tests/conftest.py
    # ❌ DO NOT: def make_session(db, session_id): ...  # hypothetical: helper with no parameter or return annotations
    ```
  - `TEST-014.Q02` (judgment) Fixtures declare a real return type rather than Any.

    ```python
    # ✅ DO: def isolated_workspace(tmp_path: Path) -> Path: ...  # fixture in tests/conftest.py
    # ❌ DO NOT: def db_fixture() -> Any: ...  # hypothetical: fixture whose return type says nothing about what it yields
    ```
  - `TEST-014.Q03` (judgment) Helper parameters are typed against the types production code passes, such as DovoDb or SessionRecord, rather than Any.

    ```python
    # ✅ DO: def create_test_session(db: DovoDb, session_id: str) -> SessionRecord: ...  # hypothetical helper; DovoDb and SessionRecord exist in dovo.core.db
    # ❌ DO NOT: def create_test_session(db: Any, mock_obj: MagicMock) -> Any: ...  # hypothetical: untyped db parameter and a mock in the signature
    ```
  - `TEST-014.Q04` (judgment) MagicMock is absent from the signatures of test helpers and fixtures, both their parameters and their return types.

    ```python
    # ✅ DO: def build_workspace(root: Path) -> WorkspacePaths: ...  # hypothetical helper taking and returning real types
    # ❌ DO NOT: def build_workspace(root: MagicMock) -> MagicMock: ...  # hypothetical: mock type used in the signature
    ```

- **[TEST-015] Orchestration Path Test Coverage (WARNING):**
  Changes to command routing and multi-step dispatch are verified end to end, so a regression in an orchestration branch is caught by a test instead of reaching users.

  Every statement below must hold:
  - `TEST-015.Q01` (judgment) A test exercises the updated execution branch of every modified command routing or multi-step orchestration path.

    ```python
    # ✅ DO: class SessionResumeCliIntegrationTests: def test_resume_cli_from_paused_state_completes_remaining_steps_exits_zero(self, ...): ...  # in tests/cli/session/test_session_resume.py, covering the resume path
    # ❌ DO NOT: # hypothetical: a change to Engine.resume() in engine/engine.py that skips steps already completed, merged with no test that resumes a paused run and checks the remaining steps execute
    ```

- **[TEST-016] Public Function Happy and Unhappy Path Coverage (BLOCKER):**
  A public function's contract includes how it succeeds and how it fails, so both outcomes are tested directly. Private helpers are verified only through their public callers.

  Every statement below must hold:
  - `TEST-016.Q01` (judgment) Every public function or method (no leading underscore) in a changed production module has at least one test of a valid input producing the expected successful outcome, in its corresponding test file per TEST-002.

    ```python
    # ✅ DO: def test_load_returns_validated_state_when_projection_matches(self, tmp_path: Path) -> None  # in tests/engine/test_state_store.py, covering SessionStateStore.load
    # ❌ DO NOT: SessionStateStore.regenerate_projection() is added to src/dovo/engine/state_store.py with no test calling it (hypothetical: the real method is covered by test_regenerate_projection_writes_payload_json_and_returns_path)
    ```
  - `TEST-016.Q02` (judgment) Every public function or method in a changed production module has, in its TEST-002 test file, at least one test of an invalid input, missing dependency, boundary or failure state producing the expected error or non-ok outcome.

    ```python
    # ✅ DO: def test_save_stale_revision_returns_conflict_and_leaves_row_and_projection(self, tmp_path: Path) -> None  # in tests/engine/test_state_store.py, covering SessionStateStore.save
    # ❌ DO NOT: Only test_save_loaded_state_persists_next_revision_in_row_and_projection exists for save() (hypothetical: the real suite also has stale-revision, missing-row and projection-failure tests), so no failure path is covered
    ```
  - `TEST-016.Q03` (judgment) Private functions and methods (leading underscore) are covered only as byproducts of their public callers, with no dedicated test method targeting them.

    ```python
    # ✅ DO: SessionStateStore._classify_write_failure has no dedicated test; it is reached through the public save() and initialize() by tests such as test_save_stale_revision_returns_conflict_and_leaves_row_and_projection and test_save_missing_row_returns_not_found; the coverage gate (CI-001) checks that private paths are reached
    # ❌ DO NOT: def test__classify_write_failure(self): ...  # hypothetical: dedicated test calling a private method directly
    ```
  - `TEST-016.Q04` (judgment) The independent failure modes of a public method are covered by TEST-006 parameterization, with a separate test method per failure mode used only when the fixture requirements diverge.

    ```python
    # ✅ DO: One @pytest.mark.parametrize test over several invalid inputs (hypothetical), the failure modes sharing the same fixture
    # ❌ DO NOT: Three near-identical test methods, one per invalid input, all built from the same fixture (hypothetical)
    ```

- **[TEST-017] CLI Runner Assertion Boundary (BLOCKER):**
  CLI runner tests should pin wiring and observable behavior, not help prose, so that help text can be reworded freely without breaking the suite.

  Every statement below must hold:
  - `TEST-017.Q01` (judgment) The CLI runner tests under tests/cli/ avoid asserting help text wording in stdout or rendered output.

    ```python
    # ✅ DO:
    assert result.exit_code == 0
    assert "Status: valid with warnings" in result.stdout  # permitted: asserting a literal status label, panel line, or full rendered string is allowed, ideally pinned via snapshot testing so the assertion cannot drift silently; not restricted to a published error code token; from tests/cli/config/test_config_validate.py
    # ❌ DO NOT: assert "Show the current configuration value" in result.output  # hypothetical help text wording; assert Click metadata instead
    ```
  - `TEST-017.Q02` (judgment) A CLI test that checks command registration or option names reads them from Click metadata rather than from rendered help output.

    ```python
    # ✅ DO: root_callbacks = {command.name: command.callback for command in app.registered_commands}  # from tests/cli/test_cli.py
    # ❌ DO NOT: assert "--format" in result.stdout  # hypothetical: option name checked by scanning --help output
    ```

- **[TEST-018] Test Layer Import Boundary (BLOCKER):**
  A core test proves the domain layer's contract independent of any presentation concern. Importing the CLI package from a core test leaks a presentation dependency into the domain suite, or shows the test belongs under tests/cli/ instead.

  Every statement below must hold:
  - `TEST-018.Q01` (mechanical) The files under tests/core/ import nothing from dovo.cli or any submodule of it.

    ```python
    # ✅ DO: from dovo.core.worktree.worktree import Worktree  # in tests/core/worktree/test_worktree.py
    # ❌ DO NOT: from dovo.cli.worktree.commands.worktree_prune import worktree_prune_command  # hypothetical: a CLI import in tests/core/worktree/test_services.py
    ```

- **[TEST-019] Command Result DTO Spy Scope for CLI Integration Tests (BLOCKER):**
  Result assertions in CLI integration tests stay a genuine contract check on the command's own output, while the real formatting and rendering path keeps running so rendering regressions still surface.

  Every statement below must hold:
  - `TEST-019.Q01` (judgment) Every dispatch spy used in a *CliIntegrationTests suite calls through to the real ui_dispatcher.dispatch, instead of replacing it with a stub.

    ```python
    # ✅ DO: The dispatch_spy fixture in tests/cli/conftest.py appends each DTO to a list and then calls original_dispatch(data, **kwargs), so a test may pin the exact DTO beyond what the --format json payload proves
    # ❌ DO NOT: monkeypatch.setattr(ui_dispatcher, "dispatch", lambda data, **kwargs: None)  # replacement stub; the real formatter never runs, which TEST-008 forbids (hypothetical)
    ```
  - `TEST-019.Q02` (judgment) Every assertion made against an item captured by the dispatch spy is an assertion on an instance of that command action's own terminal BaseResult type.

    ```python
    # ✅ DO: prune_results = [item for item in dispatch_spy if isinstance(item, ArtifactsPruneResult)]; assert prune_results == [ArtifactsPruneResult(...)]  # compares the DTO's own fields, a genuine contract comparison rather than a check that dispatch fired
    # ❌ DO NOT: assert isinstance(dispatch_spy[-1], MessageEvent)  # asserting an incidental MessageEvent (or WarningEvent, PromptEvent, lifecycle or progress event) through the result spy
    ```
  - `TEST-019.Q03` (judgment) A spy assertion avoids relying on spy[0], spy[-1] or len(spy) == 1 unless the command action dispatches only its own terminal result per invocation, and otherwise isolates the instance of the command action's own Result type.

    ```python
    # ✅ DO: assert len(dispatch_spy) == 1; assert isinstance(dispatch_spy[0], WorktreeCreateResult)  # a command action known to dispatch only its own terminal result may assert the sole captured item directly (the isinstance line is illustrative; test_worktree_create.py reads payload = dispatch_spy[0] directly)
    # ❌ DO NOT: assert isinstance(dispatch_spy[-1], WorktreeCreateResult)  # on a command action that can also dispatch a MessageEvent or WarningEvent, without first filtering for WorktreeCreateResult (hypothetical)
    ```

- **[TEST-020] Test-Layer Docstrings Are Terse Contracts Without Tags (WARNING):**
  Test docstrings stay short and carry only facts the code does not already show, so readers scan names and assertions first and the docstrings never go stale or duplicate the body.

  Every statement below must hold:
  - `TEST-020.Q01` (judgment) Every test method docstring adds a fact that the test name and assertions do not, such as an exact literal, an ordering contract, or the inputs behind a parametrized case.

    ```python
    # ✅ DO: Hypothetical: """Emit the start event before the first step log line, because the dispatcher streams them in order.""" on a test whose assertions only check that both events were emitted; the ordering contract is a fact the name and assertions do not show.
    # ❌ DO NOT: Hypothetical: """Exit 1 when capacity is exceeded.""" on test_worktree_create_cli_capacity_exceeded_exits_one in tests/cli/worktree/test_worktree_create.py only rewords the test name.
    ```
  - `TEST-020.Q02` (judgment) Every test method docstring is a single sentence.

    ```python
    # ✅ DO: Hypothetical: """Exit 1 with 'Maximum active worktrees reached' in stdout when 3 worktrees already exist."""
    # ❌ DO NOT: Hypothetical: """Create three worktrees. Then create a fourth and expect exit 1."""  # two sentences
    ```
  - `TEST-020.Q03` (judgment) Every test method docstring opens with an imperative verb.

    ```python
    # ✅ DO: Hypothetical: """Exit 1 with 'Maximum active worktrees reached' in stdout when 3 worktrees already exist."""
    # ❌ DO NOT: Hypothetical: """This test checks that the command exits 1 when capacity is exceeded."""  # opens with a non-imperative word
    ```
  - `TEST-020.Q04` (judgment) Every test method docstring is free of a `[tier-N/type]` tag.

    ```python
    # ✅ DO: Hypothetical: """Exit 1 with 'Maximum active worktrees reached' in stdout when 3 worktrees already exist."""  # the tier follows from the file path under tests/cli/
    # ❌ DO NOT: Hypothetical: """[tier-3/integration] Exit 1 with 'Maximum active worktrees reached' in stdout."""
    ```
  - `TEST-020.Q05` (judgment) Every test method docstring is free of a `<symbol>:` or `dovo <command>:` prefix.

    ```python
    # ✅ DO: Hypothetical: """Exit 1 with 'Maximum active worktrees reached' in stdout when 3 worktrees already exist."""
    # ❌ DO NOT: Hypothetical: """dovo worktree create: exit 1 with 'Maximum active worktrees reached' in stdout."""
    ```
  - `TEST-020.Q06` (judgment) Every line of every test method docstring is within 120 columns.

    ```python
    # ✅ DO: Hypothetical: a one-line docstring of 100 characters including indentation and quotes.
    # ❌ DO NOT: Hypothetical: a one-line docstring that runs to 140 columns.
    ```
  - `TEST-020.Q07` (judgment) Every docstring on a non-test unit under tests/ (helper, fixture, test double, class, module) opens with a one-line title.

    ```python
    # ✅ DO: """Build the registered provider with its winning credential set and a fake finished-stream runner."""  # on _provider_with_fake_runner in tests/core/agents/test_base.py
    # ❌ DO NOT: Hypothetical: a fixture docstring that begins with a multi-line paragraph before any one-line title.
    ```
  - `TEST-020.Q08` (judgment) The title of a non-test unit is free of any outcome clause.

    ```python
    # ✅ DO: """Build the registered provider with its winning credential set and a fake finished-stream runner."""  # states what the helper does; on _provider_with_fake_runner in tests/core/agents/test_base.py
    # ❌ DO NOT: Hypothetical: """Build a provider, so that the test can assert the run finishes cleanly."""  # carries an outcome clause
    ```
  - `TEST-020.Q09` (judgment) The title of a non-test unit is a single description of the unit rather than a list of steps.

    ```python
    # ✅ DO: """Build the registered provider with its winning credential set and a fake finished-stream runner."""  # on _provider_with_fake_runner in tests/core/agents/test_base.py
    # ❌ DO NOT: """Give the provider's winning credential 'test-token', patch a finished-stream runner, and return both."""  # hypothetical helper docstring: lists steps
    ```
  - `TEST-020.Q10` (judgment) A non-test unit's docstring includes a description beyond its title only to record a hidden contract, a safety behavior, or a non-obvious ordering constraint.

    ```python
    # ✅ DO: Hypothetical: a fixture docstring whose second paragraph says the fixture must be requested before the monkeypatched environment, because ordering changes which credential wins.
    # ❌ DO NOT: Hypothetical: a fixture docstring whose second paragraph repeats the title in other words.
    ```
  - `TEST-020.Q11` (judgment) Every docstring on a non-test unit under tests/ is free of text that retells the body.

    ```python
    # ✅ DO: Hypothetical: """Fake agent runner that replays a canned stdout stream."""
    # ❌ DO NOT: Hypothetical: """Fake agent runner. It reads the canned stdout, splits it by line, and yields each line."""  # retells the body
    ```
  - `TEST-020.Q12` (judgment) Every docstring on a non-test unit under tests/ is free of text that restates parameters, types, or the return annotation.

    ```python
    # ✅ DO: Hypothetical: """Build a provider wired to a fake finished-stream runner."""  # the signature already shows the arguments and return type
    # ❌ DO NOT: Hypothetical: """Build a provider. Args: token: the provider token. Returns: a tuple of the provider and the runner."""
    ```
  - `TEST-020.Q13` (judgment) Every docstring on a non-test unit under tests/ is free of sentences describing how another module or caller behaves.

    ```python
    # ✅ DO: Hypothetical: """Fake agent runner that replays a canned stdout stream."""
    # ❌ DO NOT: Hypothetical: """Fake agent runner. The real engine calls it once per step and then parses the stream."""  # describes the engine, not the unit
    ```
  - `TEST-020.Q14` (judgment) Every unit under tests/ carries exactly one docstring, with no bare string statement after it.

    ```python
    # ✅ DO: Hypothetical: a helper whose body begins with its docstring and then goes straight to code.
    # ❌ DO NOT: Hypothetical: a helper with a docstring followed by a second bare string literal statement, which is dead code.
    ```

- **[DOC-001] Architecture Doc Structural Gate (BLOCKER):**
  The architecture doc stays a short, trustworthy map of how the code is laid out, so readers find structure there and never have to dig it out from feature narrative or churn from refactors.

  Every statement below must hold:
  - `DOC-001.Q01` (judgment) The diff to docs/agents/architecture.md modifies only structure sections (the layers tree and the package boundaries).

    ```python
    # ✅ DO: # Hypothetical: adding a core/prune/ line to the layers tree under "## Layers" in architecture.md
    # ❌ DO NOT: # Hypothetical: adding a new section to architecture.md that explains how pruning picks which sessions to remove
    ```
  - `DOC-001.Q02` (judgment) Each structure-section edit to docs/agents/architecture.md is made because a package was added, removed or moved, domain ownership changed, or an import boundary changed.

    ```python
    # ✅ DO: # Hypothetical: a new core/prune/ package is added and the layers tree gains a core/prune/ entry
    # ❌ DO NOT: # Hypothetical: a pure refactor splits one module into two inside core/artifacts/ and architecture.md is edited to match
    ```
  - `DOC-001.Q03` (judgment) The diff to docs/agents/architecture.md is free of feature behavior essays.

    ```python
    # ✅ DO: # Hypothetical: a one-line layers-tree entry "prune/   Session artifact pruning" for a new package
    # ❌ DO NOT: # Hypothetical: 100 lines of narrative prose explaining pruning heuristics in architecture.md
    ```
  - `DOC-001.Q04` (judgment) Each new import boundary described in docs/agents/architecture.md also has a row in LAYER_RULES in tests/lint/test_layer_direction.py.

    ```python
    # ✅ DO: # Hypothetical: a new boundary "prune/ imports nothing from engine/" is added to the package boundaries section and as a new entry in LAYER_RULES
    # ❌ DO NOT: # Hypothetical: the package boundaries section gains a new boundary but LAYER_RULES in tests/lint/test_layer_direction.py is left unchanged
    ```

- **[DOC-003] Schema and Entity Documentation Gate (BLOCKER):**
  schemas.md records only the cross-cutting contracts that span the codebase, so it stays short and accurate instead of becoming a second copy of entity definitions that the source already holds.

  Every statement below must hold:
  - `DOC-003.Q01` (judgment) Every edit to docs/agents/schemas.md follows a change to a cross-cutting contract (config tier merge rules, path ownership, persistence authority, secrets handling, or catalog or database scoping).

    ```python
    # ✅ DO: # Updating schemas.md because a config key switched from replace to union merge across tiers (hypothetical). A change that only adds or changes config keys, blueprint fields, DTOs, enum members, exceptions, entrypoint methods or commands needs no schemas.md edit and correctly leaves it untouched.
    # ❌ DO NOT: # Editing schemas.md for a change that alters no cross-cutting contract, such as reflowing a section while adding a new config key (hypothetical)
    ```
  - `DOC-003.Q02` (judgment) The edits to schemas.md are free of added field lists, enum members, exception names and method lists.

    ```python
    # ✅ DO: # A schemas.md edit that states how config tiers merge, with no list of keys or fields (hypothetical)
    # ❌ DO NOT: # Listing a new Result model's fields, a new enum's members or a new exception's name in schemas.md (hypothetical)
    ```

- **[DOC-004] Ban on Duplicating Source in Docs (SUGGESTION):**
  Docs do not restate what the source already says unambiguously, so a model change cannot leave a stale copy behind and readers are sent to the one place that is always correct.

  Every statement below must hold:
  - `DOC-004.Q01` (judgment) The docs avoid hand-copying Pydantic model signatures, field tables or enum lists that a read of the source already gives unambiguously.

    ```python
    # ✅ DO: See [`WorktreeSession`](src/dovo/core/worktree/models.py). Worktrees live under .dovo/worktrees/.
    # ❌ DO NOT:
    | Field | Type | Default |
    | session_id | str | required |  (a table copying the fields of WorktreeSession)
    ```
  - `DOC-004.Q02` (judgment) Every doc that refers to a specific Pydantic model links to the file that defines it.

    ```python
    # ✅ DO: See [`WorktreeSession`](src/dovo/core/worktree/models.py).
    # ❌ DO NOT: WorktreeSession carries the session id and its paths.  (names the model with no link to src/dovo/core/worktree/models.py; hypothetical sentence)
    ```
  - `DOC-004.Q03` (judgment) A doc that describes a model adds only behavior the source does not express, such as validators or resolution order, rather than field lists or enum members copied without added context.

    ```python
    # ✅ DO: Worktrees live under .dovo/worktrees/.  (a placement fact beside the model link, not a copy of its fields)
    # ❌ DO NOT: A bullet list of every member of an enum, pasted from the source with no note on when each applies (hypothetical doc text)
    ```

- **[DOC-005] Unavoidable Spec Tables Require Parity Tests (BLOCKER):**
  A specification table that cannot be avoided must not drift from the source it describes, so an automated test that exists when the claim is made keeps the two in agreement.

  Every statement below must hold:
  - `DOC-005.Q01` (judgment) Every unavoidable field table (the specification for an external JSON Schema or a stable CLI output format) has an automated test under tests/lint/ that fails when the table and the source disagree.

    ```python
    # ✅ DO: docs/reference/step-schema.md has a field table and states it is pinned to the models by tests/lint/test_step_schema_parity.py, which exists in the tree
    # ❌ DO NOT: A hypothetical field table of a stable CLI output format is added to docs/ and no test under tests/lint/ compares it with the source
    ```
  - `DOC-005.Q02` (judgment) The parity test was already in the tree when the doc claim landed.

    ```python
    # ✅ DO: The commit that adds a doc sentence naming tests/lint/test_config_schema_parity.py also adds that file, or the file already exists
    # ❌ DO NOT: A doc sentence names a parity test as a follow-up to be written later (hypothetical), so the claim lands before the test
    ```
  - `DOC-005.Q03` (judgment) The doc names the parity test by a path that resolves to a file in the tree.

    ```python
    # ✅ DO: docs/agents/schemas.md names tests/lint/test_config_schema_parity.py, and that file exists
    # ❌ DO NOT: "enforced by tests/lint/test_doc_parity.py" where that module is nowhere in the tree (it does not exist today)
    ```

- **[DOC-006] Prefer Deletion Over Accretion (SUGGESTION):**
  Docs stay a single consistent source of truth, so a reader never has to guess which of two conflicting statements is current.

  Every statement below must hold:
  - `DOC-006.Q01` (judgment) Every doc edit that changes a statement replaces or deletes the stale text instead of appending a new bullet beside it.

    ```python
    # ✅ DO: Hypothetical edit: a bullet citing the removed formatters.py is replaced by one citing cli/ui/formatters/ (a package that exists in src/dovo/cli/ui/)
    # ❌ DO NOT: Hypothetical edit: a new bullet citing cli/ui/formatters/ is appended while the old bullet citing formatters.py stays
    ```
  - `DOC-006.Q02` (judgment) Every statement the change adds or edits is free of contradiction with other statements in docs/ about the same rule or fact, found by searching docs/ for what it names.

    ```python
    # ✅ DO: Hypothetical: an edit states formatters live in cli/ui/formatters/; searching docs/ for "formatters" finds no other doc naming a different location
    # ❌ DO NOT: Hypothetical: an edit states formatters live in cli/ui/formatters/; searching docs/ for "formatters" finds another doc still saying they live in formatters.py
    ```

- **[DOC-007] Canonical Terminology Invariant (SUGGESTION):**
  Docs and code comments name the core concepts the same way everywhere, so a reader never has to guess whether two words mean one thing or one word means two.

  Every statement below must hold:
  - `DOC-007.Q01` (judgment) Doc prose and every code comment name Blueprint, Step, Loop step, Catalog, Run, Session, Worktree and Checkpoint concepts by the term the Terminology table in docs/agents/architecture.md defines, rather than by a term the table does not define.

    ```python
    # ✅ DO: 'A blueprint containing a loop step.'
    # ❌ DO NOT: 'A workflow containing a repeat block.'  # hypothetical: the table defines blueprint and loop step, not workflow or repeat block
    ```
  - `DOC-007.Q02` (judgment) Doc prose and every code comment use each defined term only within its stated meaning in the Terminology table.

    ```python
    # ✅ DO: 'A run executes the blueprint and leaves a session behind.'  # the run is the act of executing; the session is what it leaves behind
    # ❌ DO NOT: 'A session executes the blueprint.'  # the act of executing is a run; the session is what it leaves behind
    ```

- **[DOC-008] Verifiable Doc and Rule Claims (BLOCKER):**
  Readers act on what docs, skills, rules and docstrings say, so every claim must be true and checkable today. False or aspirational statements mislead them, and an example is copied more often than the rule is read.

  Every statement below must hold:
  - `DOC-008.Q01` (judgment) Every doc, skill, rule or docstring sentence that asserts something is enforced, gated or checked names the enforcing artifact by path.

    ```python
    # ✅ DO: # "COLUMNS is pinned to 160 by [tool.pytest_env] in pyproject.toml" - names the enforcing file, and [tool.pytest_env] there sets COLUMNS = "160"
    # ❌ DO NOT: # "Command output width is always pinned in tests." (hypothetical sentence) - claims enforcement and names no artifact
    ```
  - `DOC-008.Q02` (judgment) Every path named as an enforcing artifact, or in a "see <path>" claim, resolves to a file in the tree.

    ```python
    # ✅ DO: # "enforced by tests/lint/test_layer_direction.py" - that file exists in the tree
    # ❌ DO NOT: # "enforced by tests/lint/test_doc_parity.py" (hypothetical sentence) - no such file exists under tests/lint/
    ```
  - `DOC-008.Q03` (judgment) Every helper, fixture, builder or assertion function that a doc instructs an implementer to use exists in the tree before the instruction ships.

    ```python
    # ✅ DO: # "Acquire the lock with WorkspaceLock" - WorkspaceLock is defined in src/dovo/common/lock.py
    # ❌ DO NOT: # "Build the input with make_session_fixture()" (hypothetical) - no function of that name is defined anywhere in the tree
    ```
  - `DOC-008.Q04` (judgment) The change documents only behavior that is already built, in prose and in rule examples alike.

    ```python
    # ✅ DO: # A rule example that cites core/artifacts/services/prune.py, which exists and does what the example says
    # ❌ DO NOT: # A paragraph describing a "dovo sync --all" flag (hypothetical) that is planned but absent from the CLI
    ```
  - `DOC-008.Q05` (judgment) The change deletes or corrects, rather than leaves as aspiration, every doc, skill or rule sentence that it makes false.

    ```python
    # ✅ DO: # A change that removes a CLI flag also deletes the sentence in docs/cli/ that described it (hypothetical change)
    # ❌ DO NOT: # A change that removes a CLI flag leaves its docs sentence behind with "to be restored later" (hypothetical change)
    ```
  - `DOC-008.Q06` (judgment) Every docstring of a function whose behavior the change alters, or that appears in the diff, is free of sentences the new code makes false.

    ```python
    # ✅ DO: # A change makes a function raise on empty input and its docstring "returns an empty list" sentence is updated in the same diff (hypothetical)
    # ❌ DO NOT: # A change makes a function raise on empty input but its docstring still says "returns an empty list" (hypothetical)
    ```

- **[CI-001] Pre-Commit Quality Suite Gate (BLOCKER):**
  Every commit passes the full quality gate, and the coverage floor stays a regression backstop, never loosened or chased. A drop from deleting duplicated or dead tests is a success.

  Every statement below must hold:
  - `CI-001.Q01` (judgment) `dovo run quality` has passed (ruff format, ruff check, basedpyright src tests --level error with zero errors, mkdocs build --strict, complexity at or under 10 on changed files, and the test suite meeting the coverage floor) before the commit.

    ```python
    # ✅ DO: uv run ruff format . && uv run dovo run quality
    # ❌ DO NOT: git commit after only running a scoped `uv run dovo run tests --path tests/core/` (hypothetical: the full quality gate was never run)
    ```
  - `CI-001.Q02` (judgment) The fail_under value in pyproject.toml under [tool.coverage.report] is greater than or equal to its previous committed value.

    ```python
    # ✅ DO: # fail_under raised from 80 to 82 under [tool.coverage.report] in pyproject.toml in the same change (hypothetical numbers)
    # ❌ DO NOT: # fail_under lowered from 80 to 75 so a commit can pass (hypothetical)
    ```
  - `CI-001.Q03` (judgment) The stated justification for each added test is a reason other than raising the coverage percentage.

    ```python
    # ✅ DO: A new test asserting a public result contract, with a PR description that names the contract
    # ❌ DO NOT: # tests added purely to reach a percentage, with a PR description that says "bump coverage to 85%" (hypothetical)
    ```

- **[CI-002] Git and Pull Request Attribution Hygiene (BLOCKER):**
  Commit history and pull requests describe the change in the author's own words and stay focused, so they carry no tool attribution and each pull request can be reviewed as one concern.

  Every statement below must hold:
  - `CI-002.Q01` (judgment) The commit title starts with a semantic prefix of the form type(scope) or type, followed by a colon, as in "docs(rules): ..." in the commit history.

    ```python
    # ✅ DO: feat(worktree): add prune subcommand for stale worktrees (hypothetical message; the dovo worktree prune command exists)
    # ❌ DO NOT: Updated stuff and fixed things (hypothetical message)
    ```
  - `CI-002.Q02` (judgment) The commit title is written in the imperative mood.

    ```python
    # ✅ DO: feat(worktree): add prune subcommand (hypothetical message)
    # ❌ DO NOT: feat(worktree): added prune subcommand (hypothetical message)
    ```
  - `CI-002.Q03` (judgment) The commit message is free of AI trailers such as "Co-authored-by: Cursor".

    ```python
    # ✅ DO: feat: prune worktrees, with a body that ends after the description and has no trailer lines (hypothetical message)
    # ❌ DO NOT:
    feat: prune worktrees

    Co-authored-by: Cursor <cursor@cursor.sh>  (hypothetical message)
    ```
  - `CI-002.Q04` (judgment) The pull request description is free of AI or tool attribution lines.

    ```python
    # ✅ DO: A PR description whose last line is the Linkage entry "Closes #123" (hypothetical description)
    # ❌ DO NOT: A PR description ending in a "Generated with <tool>" attribution line (hypothetical description)
    ```
  - `CI-002.Q05` (judgment) The pull request addresses a single responsibility.

    ```python
    # ✅ DO: One PR that adds the worktree prune subcommand and its tests (hypothetical PR)
    # ❌ DO NOT: One PR that adds a subcommand and also fixes an unrelated config-loading bug (hypothetical PR)
    ```
  - `CI-002.Q06` (judgment) The pull request description has a Why section of 2-3 sentences giving the context and motivation.

    ```python
    # ✅ DO: A "Why" section of two sentences explaining the problem and why it matters now (hypothetical description)
    # ❌ DO NOT: A description with no Why section, or a Why section of a full page of history (hypothetical description)
    ```
  - `CI-002.Q07` (judgment) The pull request description has an Approach section made of bullets of 1-2 sentences each.

    ```python
    # ✅ DO: An "Approach" section with three bullets, each one or two sentences (hypothetical description)
    # ❌ DO NOT: An "Approach" section written as one long unbroken paragraph (hypothetical description)
    ```
  - `CI-002.Q08` (judgment) The pull request description has a Linkage section with an issue cross-reference such as Fixes #N or Closes #N.

    ```python
    # ✅ DO: A "Linkage" section reading "Closes #123" (hypothetical description)
    # ❌ DO NOT: A description with no Linkage section (hypothetical description)
    ```

- **[CI-003] Agentic Process State Declaration (WARNING):**
  Reviewers can tell what an agent is about to change and which project rule it is following, so each action is announced before it is taken.

  Every statement below must hold:
  - `CI-003.Q01` (judgment) The agent states the specific directive or doc governing an action before executing commands or editing files.

    ```python
    # ✅ DO: Governing directive: docs/agents/testing.md
    # ❌ DO NOT: Running write_to_file without stating the governing directive (hypothetical agent turn)
    ```
  - `CI-003.Q02` (judgment) The agent states the target scope of an action, such as a specific test package or module, before executing commands or editing files.

    ```python
    # ✅ DO: Target scope: tests/core/worktree/test_worktree.py
    # ❌ DO NOT: Running write_to_file without stating the target scope (hypothetical agent turn)
    ```

- **[CI-004] Mechanical Enforcement Parity (BLOCKER):**
  A BLOCKER rule that can be checked by a machine is actually checked by one, so a declared gate cannot silently stop protecting the codebase and an allowlist cannot quietly grow.

  Every statement below must hold:
  - `CI-004.Q01` (judgment) Every BLOCKER rule that can be checked mechanically has an executing check (an invariant test under tests/lint/, a prek hook or a CI step) or an explicit review-only classification.

    ```python
    # ✅ DO: ARCH-001 is enforced by tests/lint/test_layer_direction.py, which runs in the test suite
    # ❌ DO NOT: A new BLOCKER rule about import order is added to rules_spec.yaml with no test, hook or CI step behind it (hypothetical)
    ```
  - `CI-004.Q02` (judgment) Every declared gate is configured so that it can fail, with no setting that disables it, such as a coverage floor of zero or a type checker present in no hook and no workflow.

    ```python
    # ✅ DO: fail_under = 80 in pyproject.toml, so the coverage gate fails below 80 percent
    # ❌ DO NOT: fail_under = 0 in pyproject.toml (hypothetical): the coverage gate is declared but can never fail
    ```
  - `CI-004.Q03` (judgment) A gate whose configuration disables it is either wired up or downgraded to a review-time WARNING, rather than left declared as an enforced BLOCKER.

    ```python
    # ✅ DO: A type-check step missing from every hook and workflow is added to .github/workflows/ci.yml, or its rule is downgraded to WARNING (hypothetical)
    # ❌ DO NOT: basedpyright is declared as a BLOCKER gate but appears in no hook and no workflow, and the rule stays BLOCKER (hypothetical)
    ```
  - `CI-004.Q04` (judgment) Every invariant check includes a regression test shaped like the code it polices, such as assertions inside a *Tests class rather than a module-level function.

    ```python
    # ✅ DO: ImportBoundariesTests.test_unexpected_entries_flags_subpackage_and_ignores_pycache in tests/lint/test_import_boundaries.py builds a sample directory in tmp_path with a stray subpackage and module, then asserts _unexpected_entries flags exactly those entries
    # ❌ DO NOT: A scanner walks only tree.body functions, so test methods inside a *Tests class are never inspected (hypothetical)
    ```
  - `CI-004.Q05` (judgment) An invariant check that lands green carries an explicit burn-down allowlist of known violators, rather than being merged red or left disabled.

    ```python
    # ✅ DO: LAYER_EXCEPTIONS in tests/lint/test_layer_direction.py lists the known allowed violations, so the check passes today
    # ❌ DO NOT: A new invariant is added with known violators and is skipped or marked xfail instead of carrying an allowlist (hypothetical)
    ```
  - `CI-004.Q06` (judgment) Every allowlist of known violators has only shrunk, with no entry added.

    ```python
    # ✅ DO: test_layer_exception_still_occurs in tests/lint/test_layer_direction.py fails when an allowlisted violation is fixed, forcing its entry to be deleted from LAYER_EXCEPTIONS
    # ❌ DO NOT: A new tuple is added to LAYER_EXCEPTIONS to make a fresh upward import pass (hypothetical)
    ```
