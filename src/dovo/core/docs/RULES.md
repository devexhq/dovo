<!-- AUTO-GENERATED FROM rules_spec.yaml. DO NOT EDIT DIRECTLY. -->
# Architectural & Coding Invariants (Core Domain)

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

- **[ARCH-004] Standard Core Package Layout (Domain-Named Entrypoint) (BLOCKER):**
  New domain code follows one predictable package layout with a domain-named entrypoint, so a reader finds types, errors, coordination and operations in the same places in every domain.

  Every statement below must hold:
  - `ARCH-004.Q01` (judgment) Each new domain package under core/ defines its types in a models.py module.

    ```python
    # ✅ DO: src/dovo/core/worktree/models.py holds WorktreeListResult and the other worktree types
    # ❌ DO NOT: src/dovo/core/prune/ holds a single prune.py mixing types and logic  # hypothetical: no prune/ package exists today
    ```
  - `ARCH-004.Q02` (judgment) Each new domain package under core/ that raises its own errors defines them in an exceptions.py module.

    ```python
    # ✅ DO: src/dovo/core/worktree/exceptions.py
    # ❌ DO NOT: class PruneError(Exception) defined inside src/dovo/core/prune/prune.py  # hypothetical: no prune/ package exists today
    ```
  - `ARCH-004.Q03` (judgment) The entrypoint coordinator or service of each new domain package is named after the domain rather than generically.

    ```python
    # ✅ DO: src/dovo/core/sessions/sessions.py  # entrypoint named after the sessions domain
    # ❌ DO NOT: src/dovo/core/prune/facade.py  # hypothetical: generic facade.py instead of prune.py; no facade.py exists in src/ today
    ```
  - `ARCH-004.Q04` (judgment) Each new domain package under core/ keeps its imperative operations in a services/ package.

    ```python
    # ✅ DO: src/dovo/core/worktree/services/ contains list.py, delete.py and show.py
    # ❌ DO NOT: src/dovo/core/prune/ with its operations written in prune.py and no services/ directory  # hypothetical
    ```
  - `ARCH-004.Q05` (judgment) Each new imperative operation of a domain is placed in its own services/<verb>.py module rather than directly in the package root.

    ```python
    # ✅ DO: src/dovo/core/sessions/services/read_logs.py
    # ❌ DO NOT: A new archive() function defined in src/dovo/core/sessions/__init__.py  # hypothetical: no archive operation exists; logic in the package root without a structured module
    ```
  - `ARCH-004.Q06` (judgment) All public models of a new domain package are defined in its models.py rather than in services/.

    ```python
    # ✅ DO: class WorktreeListResult(BaseResult) defined in src/dovo/core/worktree/models.py
    # ❌ DO NOT: A public result model class defined inside services/prune.py  # hypothetical: public models belong in models.py
    ```
  - `ARCH-004.Q07` (judgment) New domain code avoids extending a legacy flat layout (a domain package or module that lacks the standard structure).

    ```python
    # ✅ DO: New domain code gets its own package laid out like src/dovo/core/worktree/ (models.py, exceptions.py, worktree.py, services/)
    # ❌ DO NOT: New domain logic appended to an existing flat module that has no models.py, exceptions.py or services/  # hypothetical
    ```

- **[ARCH-006] Secrets Environment Resolution (BLOCKER):**
  Secrets exist only in the process environment and only while a call needs them, so they never leak into stored configuration, the database or prompts.

  Every statement below must hold:
  - `ARCH-006.Q01` (judgment) Every API key or secret is read from environment variables at call time through a module-level resolve helper.

    ```python
    # ✅ DO: def resolve_copilot_token() -> str | None: return resolve_credential(COPILOT_TOKEN_ENVS)  # in core/agents/copilot.py, reads os.environ on each call
    # ❌ DO NOT: TOKEN = os.environ["COPILOT_TOKEN"]  # hypothetical: captured once at import time instead of resolved at call time
    ```
  - `ARCH-006.Q02` (judgment) Every secret is absent from config.json, including the fields of DovoConfig.

    ```python
    # ✅ DO: DovoConfig in core/config/models.py declares no api_key, token or secret field
    # ❌ DO NOT: copilot_token: str = ""  # hypothetical: a secret-bearing field added to DovoConfig
    ```
  - `ARCH-006.Q03` (judgment) Every secret is absent from data.db, including the SQLite record models.

    ```python
    # ✅ DO: The SQLModel record classes in core/db/models.py declare no credential column
    # ❌ DO NOT: api_key: str  # hypothetical: a credential column added to a SQLModel record class
    ```
  - `ARCH-006.Q04` (judgment) Every secret is kept out of the arguments passed into prompt builders.

    ```python
    # ✅ DO: build_mutation_prompt(request: AgentRequest) in core/agents/cli_mutation.py receives the whole request, but _prompt_body copies only mode, worktree_path, agent_scratch_path, instruction and payload and never reads request.env
    # ❌ DO NOT: build_prompt(task, api_key=resolve_credential(envs))  # hypothetical: a secret passed into a prompt builder
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

- **[MODEL-002] Result/Outcome Pattern over Exceptions (BLOCKER):**
  Callers must learn about business and operational failures from a result they can inspect and render, not from exceptions they must remember to catch.

  Every statement below must hold:
  - `MODEL-002.Q01` (judgment) The operation reports a business or operational failure by returning a result instead of raising an exception.

    ```python
    # ✅ DO: return WorktreeDeleteResult(status=WorktreeDeleteStatus.NOT_FOUND, errors=[...])  # in core/worktree/services/delete.py
    # ❌ DO NOT: raise WorktreeNotFoundError('Worktree does not exist')  # hypothetical: no such exception exists in src/
    ```
  - `MODEL-002.Q02` (judgment) An operation whose outcome reaches a domain entrypoint, a command handler, a formatter or the wire format returns a Pydantic result object subclassing BaseResult.

    ```python
    # ✅ DO: def delete(self, worktree_id: str) -> WorktreeDeleteResult  # in core/worktree/worktree.py; WorktreeDeleteResult subclasses BaseResult
    # ❌ DO NOT: def delete(...) -> bool  # hypothetical: a domain-entrypoint operation returning a plain value
    ```
  - `MODEL-002.Q03` (judgment) Callers of a result-returning operation check .ok rather than catching exceptions to learn the outcome.

    ```python
    # ✅ DO: if not result.ok: ...  # hypothetical caller that inspects the result
    # ❌ DO NOT: try: service.delete(...) except WorktreeNotFoundError: ...  # hypothetical: caller catches an exception to learn the outcome
    ```
  - `MODEL-002.Q04` (judgment) Callers that report a failure to the user render the result .errors and .warnings rather than the text of a caught exception.

    ```python
    # ✅ DO: for message in result.errors: render(message)  # hypothetical caller that renders the result
    # ❌ DO NOT: except Exception as exc: render(str(exc))  # hypothetical: failure text taken from a caught exception
    ```
  - `MODEL-002.Q05` (judgment) An internal helper that returns a plain value or an error message (str | None) serves a single in-layer caller that only branches on success.

    ```python
    # ✅ DO: BaseAgentProvider._tool_policy_preflight returns str | None; its only caller in core/agents/base.py wraps a non-None message in provider_error_response(...)
    # ❌ DO NOT: A hypothetical helper returning str | None that is called from several modules, or whose result reaches a command handler or the wire format
    ```

- **[MODEL-003] Reachable and Tested Status Enums (BLOCKER):**
  A status a result can report must be one the program really produces and the tests really check, so callers never branch on outcomes that cannot happen or that nobody has verified.

  Every statement below must hold:
  - `MODEL-003.Q01` (judgment) For each status StrEnum defined in a core/*/models.py or engine/**/models.py file and used by a BaseResult model, every variant is emitted by production code in src/.

    ```python
    # ✅ DO: ArtifactsPruneStatus.LOCKED is returned by `status=ArtifactsPruneStatus.LOCKED` in src/dovo/core/artifacts/services/prune.py
    # ❌ DO NOT: class PruneStatus(StrEnum): UNKNOWN = "unknown"  # hypothetical: no code in src/ ever assigns PruneStatus.UNKNOWN to a result
    ```
  - `MODEL-003.Q02` (judgment) For each status StrEnum defined in a core/*/models.py or engine/**/models.py file and used by a BaseResult model, every variant is asserted in a test under tests/.

    ```python
    # ✅ DO: ArtifactsPruneStatus.LOCKED is asserted by `assert result.status == ArtifactsPruneStatus.LOCKED` in tests/core/artifacts/services/test_prune.py
    # ❌ DO NOT: class PruneStatus(StrEnum): FAILED = "failed"  # hypothetical: emitted in src/ but no test under tests/ asserts PruneStatus.FAILED
    ```

- **[MODEL-004] Definition Exception Hierarchy (BLOCKER):**
  Failures about catalog definitions must be catchable through one shared error family, so callers can handle any definition problem uniformly instead of chasing unrelated base types.

  Every statement below must hold:
  - `MODEL-004.Q01` (judgment) Every exception class in src/dovo/core/catalog/exceptions.py, which covers locating, loading, validating and writing catalog definitions, descends from DefinitionError, DefinitionNotFoundError, DefinitionLoadError or DefinitionValidationError in common/exceptions.py, rather than having Exception or ValueError as its only base.

    ```python
    # ✅ DO: class BlueprintLoadError(DefinitionLoadError): """Raised when blueprint YAML syntax is invalid or unreadable."""  # in core/catalog/exceptions.py; an intermediate base such as CatalogError(DefinitionError) also counts, and an extra base is fine as in AskStepInvalidError(DefinitionValidationError, ValueError)
    # ❌ DO NOT: class BlueprintLoadError(Exception): """Raised when blueprint YAML syntax is invalid or unreadable."""  # hypothetical: inherits directly from Exception; class StepValidationError(ValueError) would fail the same way
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

- **[TYPE-004] Explicit ClassVar Typing for Table Names and Constants (NIT):**
  The type checker should see class-level SQL names and constants as exactly what they are, so it never has to guess between Any, a bare string and a class attribute.

  Every statement below must hold:
  - `TYPE-004.Q01` (judgment) Every SQLModel __tablename__ is annotated as ClassVar[str].

    ```python
    # ✅ DO: __tablename__: ClassVar[str] = "worktrees"  # in WorktreeRecord, core/db/models.py (the real line also carries a pyright ignore for the SQLModel stub conflict, omitted here)
    # ❌ DO NOT: __tablename__ = "worktrees"  # hypothetical: unannotated, so the type checker has to infer it
    ```
  - `TYPE-004.Q02` (judgment) Every other class-level SQL string constant is annotated explicitly as ClassVar[str] rather than Any or a bare str.

    ```python
    # ✅ DO: INDEX_NAME: ClassVar[str] = "ix_worktrees_status"  # hypothetical constant on a table class
    # ❌ DO NOT: INDEX_NAME: Any = "ix_worktrees_status"  # hypothetical: Any leaves the constant ambiguous to the type checker
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

- **[PERF-002] Database Migration Hygiene and Consumer Call Sites (BLOCKER):**
  The schema holds only what production code uses, and data changes ship as versioned revisions, so every installed database upgrades the same way and no dead columns accumulate.

  Every statement below must hold:
  - `PERF-002.Q01` (judgment) Every table or column added by a migration in the change is written by production code in src/ in the same PR.

    ```python
    # ✅ DO: Migration 0003_artifact_revisions adds the digest column, and ArtifactsRepository.create() in src/dovo/core/db/repositories/artifacts.py accepts and stores digest
    # ❌ DO NOT: # Hypothetical: a migration adds column future_flag to sessions and no code under src/ ever sets it
    ```
  - `PERF-002.Q02` (judgment) Every table or column added by a migration in the change is queried by production code in src/ in the same PR.

    ```python
    # ✅ DO: Migration 0004_session_discard adds sessions.discard, and the session settings builder in src/dovo/engine/session.py reads it back with discard=row.discard
    # ❌ DO NOT: # Hypothetical: a migration adds column future_flag to sessions and no code under src/ ever reads or filters on it
    ```
  - `PERF-002.Q03` (judgment) Every one-time data backfill is shipped as a versioned Alembic revision that runs it with op.execute().

    ```python
    # ✅ DO: A new revision file under src/dovo/core/db/alembic/versions/ calls op.execute("UPDATE ...") to backfill existing rows (the existing op.execute call in 0003_artifact_revisions is a downgrade data cleanup, used here as the pattern)
    # ❌ DO NOT: # Hypothetical: a model or repository in src/dovo/core/db/ runs a raw UPDATE at startup to backfill old rows, outside any Alembic revision
    ```

- **[PERF-003] Indexed Column Queries vs O(n) In-Memory Lookups (WARNING):**
  Lookups use the database's filtering and indexes, so the cost of finding a record does not grow with the number of rows fetched and scanned in Python.

  Every statement below must hold:
  - `PERF-003.Q01` (judgment) Code that needs a subset of repository records gets it with a filtered repository query instead of filtering a full fetch in memory, whenever an indexed query method or column exists.

    ```python
    # ✅ DO: paused = db.sessions.list(status=SessionStatus.PAUSED)  # status is an indexed column; list() filters it in SQL
    # ❌ DO NOT: paused = [s for s in db.sessions.list() if s.status == SessionStatus.PAUSED]  # fetches every session, then filters in Python although list(status=...) exists
    ```

- **[API-001] Sibling Repository CRUD Method Naming Consistency (WARNING):**
  Sibling repositories name the same operation with the same verb, so a caller who knows one repository can use the others without reading each class, and a divergent name reads as a different behavior.

  Every statement below must hold:
  - `API-001.Q01` (judgment) Every repository class that inserts a new record exposes that operation as a public create() method.

    ```python
    # ✅ DO: WorktreesRepository.create(...) and SessionsRepository.create(...)
    # ❌ DO NOT: CostsRepository.record_token_usage(...) inserting a CostRecord while sibling repositories use create()
    ```
  - `API-001.Q02` (judgment) Every repository method that returns at most one record looked up by its identifying key has the name get.

    ```python
    # ✅ DO: def get(self, id: str) -> WorktreeRecord | None:
    # ❌ DO NOT: def find(self, id: str) -> WorktreeRecord | None:  # siblings use get()
    ```
  - `API-001.Q03` (judgment) Every repository method that returns a collection of records of one entity has the name list.

    ```python
    # ✅ DO: def list(self, status: WorktreeStatus | None = None) -> list[WorktreeRecord]:
    # ❌ DO NOT: def all(self) -> list[WorktreeRecord]:  # siblings use list()
    ```
  - `API-001.Q04` (judgment) Every repository method that removes a record has the name delete.

    ```python
    # ✅ DO: def delete(self, id: str) -> bool:
    # ❌ DO NOT: def remove(self, id: str) -> bool:  # siblings use delete()
    ```
  - `API-001.Q05` (judgment) Every repository method that persists changes to a whole existing record has the name save.

    ```python
    # ✅ DO: def save(self, record: SessionRecord) -> SessionRecord:
    # ❌ DO NOT: def store(self, record: SessionRecord) -> SessionRecord:  # whole-record persist not named save()
    ```
  - `API-001.Q06` (judgment) Every repository method named insert, add or upsert has a docstring or comment stating why a canonical verb does not fit.

    ```python
    # ✅ DO: def upsert(self, ...) -> ...:  with the docstring "Not save(): replaces the row only when the revision matches."
    # ❌ DO NOT: def insert(self, record: WorktreeRecord) -> WorktreeRecord:  # no stated reason; siblings use create()
    ```
  - `API-001.Q07` (judgment) All repository classes that expose the same non-CRUD operation give it the same method name.

    ```python
    # ✅ DO: WorktreesRepository.update_status(...) and SessionsRepository.update_status(...)
    # ❌ DO NOT: WorktreesRepository.update_status(...) next to SessionsRepository.set_state(...) for the same status change
    ```

- **[API-002] Consistent Error Semantics Across Queries (WARNING):**
  Query paths that read the same data report bad rows the same way, so a caller never mistakes a silently swallowed failure in one path for an empty result while a sibling path reports it.

  Every statement below must hold:
  - `API-002.Q01` (judgment) Every query method that reads an entity reports an invalid enum value or corrupt row, by raising or by returning a BaseResult error, whenever a sibling query method that reads that entity reports it.

    ```python
    # ✅ DO: get() and list() both raise ValueError for a row with an invalid status
    # ❌ DO NOT: list() raises on a row with an invalid status while get() returns None for the same row
    ```
  - `API-002.Q02` (judgment) Every except block in a query method re-raises or converts the caught error into a BaseResult error, instead of returning None or an empty value.

    ```python
    # ✅ DO: except ValidationError as error: return RecordResult.from_error(error)
    # ❌ DO NOT: except ValidationError: return None  # swallows the error
    ```

- **[DRY-001] Shared Repository Transaction Logic in BaseRepository (SUGGESTION):**
  Transaction handling for repository writes lives once in BaseRepository, so commit, rollback, deletion and session behavior stay identical across repositories and a fix is made in one place instead of in every sibling.

  Every statement below must hold:
  - `DRY-001.Q01` (judgment) Every repository method that persists a record commits through BaseRepository._commit.

    ```python
    # ✅ DO: return self._commit(session, record, f"Worktree with id '{id}' already exists")
    # ❌ DO NOT: session.add(record); session.commit()  # in a repository subclass
    ```
  - `DRY-001.Q02` (judgment) Every repository method that deletes a single record deletes it through BaseRepository._delete_where.

    ```python
    # ✅ DO: return self._delete_where(session, select(WorktreeRecord).where(WorktreeRecord.id == id))
    # ❌ DO NOT: session.delete(record); session.commit()  # in a repository subclass
    ```
  - `DRY-001.Q03` (judgment) The rollback after a failed commit is written only in BaseRepository.

    ```python
    # ✅ DO: No try/except IntegrityError and no session.rollback() in worktrees.py, sessions.py, artifacts.py or costs.py
    # ❌ DO NOT: except IntegrityError: session.rollback(); raise ValueError(...)  # in a repository subclass
    ```
  - `DRY-001.Q04` (judgment) Every repository method obtains its session from BaseRepository.session().

    ```python
    # ✅ DO: with self.session() as session:
    # ❌ DO NOT: with get_session(self.db_engine) as session:  # in a repository subclass
    ```
  - `DRY-001.Q05` (judgment) Every other transaction block shared by two or more repository classes is defined once in BaseRepository.

    ```python
    # ✅ DO: Two repositories both call self._delete_where(...) for single-record deletes
    # ❌ DO NOT: The same try/commit/rollback/refresh block copied into both SessionsRepository and WorktreesRepository
    ```

- **[DB-001] Redundant Index Declaration on Unique Columns (NIT):**
  A unique constraint already gets a backing index in SQLite, so a column declares uniqueness or an extra index, never both, and the database does not carry a second index that does nothing.

  Every statement below must hold:
  - `DB-001.Q01` (judgment) Every SQLModel Field that declares unique=True is free of an index=True declaration.

    ```python
    # ✅ DO: session_id: str = Field(unique=True)  # in SessionRecord, src/dovo/core/db/models.py
    # ❌ DO NOT: session_id: str = Field(unique=True, index=True)  # hypothetical: no field in models.py declares both
    ```

- **[DB-002] Released Migrations Are Immutable and Reversible (BLOCKER):**
  Databases that users already upgraded can always be reached from, and returned to, a known schema, because a revision that shipped never changes and every revision can be undone on any supported database.

  Every statement below must hold:
  - `DB-002.Q01` (judgment) Every revision file that exists in the latest v* tag is left unedited, unrenamed and undeleted.

    ```python
    # ✅ DO: A schema fix for a released column ships as a new file, 0005_fix_discard_default.py (hypothetical), and 0001_initial_schema.py is untouched
    # ❌ DO NOT: Editing 0001_initial_schema.py, which exists in the v0.6.0 tag, to change a column definition
    ```
  - `DB-002.Q02` (judgment) Every new revision defines both upgrade() and a downgrade() that has a real body rather than a bare `pass`.

    ```python
    # ✅ DO: 0004_session_discard.py defines downgrade() that drops the discard column from sessions inside op.batch_alter_table
    # ❌ DO NOT: A revision whose downgrade() is `pass`
    ```
  - `DB-002.Q03` (judgment) Every new revision avoids PRAGMA and sqlite_master.

    ```python
    # ✅ DO: 0004_session_discard.py contains no PRAGMA statement and no reference to sqlite_master
    # ❌ DO NOT: op.execute("PRAGMA foreign_keys=OFF")  # or a query against sqlite_master, in a revision
    ```
  - `DB-002.Q04` (judgment) Every new revision makes its column and constraint changes through op.batch_alter_table.

    ```python
    # ✅ DO: with op.batch_alter_table("sessions") as batch_op: batch_op.drop_column("discard")
    # ❌ DO NOT: op.add_column("sessions", sa.Column("discard", sa.Boolean()))  # outside a batch_alter_table block (hypothetical revision)
    ```
  - `DB-002.Q05` (judgment) Every new revision has both an upgrade test and a downgrade test in tests/core/db/test_migrations.py.

    ```python
    # ✅ DO: SessionDiscardMigrationTests has test_upgrade_adds_discard_column_defaulting_existing_rows_false and test_downgrade_drops_discard_column_and_keeps_rows
    # ❌ DO NOT: A new revision 0005 (hypothetical) with no test class for it in test_migrations.py, or one that tests only the upgrade
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
