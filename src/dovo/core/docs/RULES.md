<!-- AUTO-GENERATED FROM rules_spec.yaml. DO NOT EDIT DIRECTLY. -->
# Architectural & Coding Invariants (Core Domain)

> **Notice for Agents:** Code violating `BLOCKER` rules will fail verification.

- **[ARCH-001] Strict Layered Import Flow (BLOCKER):**
  Dependencies flow strictly one way: common/ -> core/ -> engine/ -> cli/. Within core/: project/ -> {db,git,worktree,catalog,inputs,sessions,status,artifacts,interaction}/ -> agents/ -> diagnostics/. Upward imports are strictly prohibited.

```python
# ✅ DO: from dovo.core.sessions import SessionLogEvent  # in engine/
# ❌ DO NOT: from dovo.engine.engine import Engine  # upward import in core/sessions/
```

- **[ARCH-004] Standard Core Package Layout (Domain-Named Entrypoint) (BLOCKER):**
  Default layout for new domain code is core/<domain>/: models.py (types), exceptions.py (errors), <domain>.py (entrypoint coordinator/service, e.g. prune.py, moving away from generic facade.py), and services/<verb>.py (imperative operations). Do not use generic facade.py, do not put logic directly in package root without structured modules, do not define public models in services/, and do not extend legacy flat layouts.

```python
# ✅ DO: src/dovo/core/prune/ -> models.py, exceptions.py, prune.py, services/
# ❌ DO NOT: src/dovo/core/prune/ -> facade.py  # or monolithic prune.py mixing models & logic
```

- **[ARCH-006] Secrets Environment Resolution (BLOCKER):**
  API keys and secrets must be resolved from environment variables at call time via module-level resolve_<provider>_api_key(). Secrets must never be stored in config.json, persisted to data.db, or passed into prompt builders.

```python
# ✅ DO: api_key = resolve_credential(spec.credential_envs)  # reads environment dynamically
# ❌ DO NOT: api_key = config.agent.copilot_token  # serializing secrets in static config
```

- **[RENDER-003] Inline Error and Warning String Construction (SUGGESTION):**
  Construct errors, warnings, and fixes strings using inline f-strings or literals at call sites. Do not create private single-message formatting wrappers.

```python
# ✅ DO: errors.append(f"Blueprint '{path}' invalid: {err}")
# ❌ DO NOT: errors.append(self._format_validation_error(path, err))
```

- **[MODEL-001] Scoped Model Exceptions with Justifying Comment (BLOCKER):**
  Scoped exceptions allowing extra='ignore' are restricted to hand-authored YAML models (BlueprintDefinition, LoopStepBlock) and MUST carry a justifying comment.

```python
# ✅ DO:
# User YAML models permit forward-compatible extra keys
model_config = {'extra': 'ignore'}
# ❌ DO NOT: model_config = {'extra': 'ignore'}  # missing justifying comment
```

- **[MODEL-002] Result/Outcome Pattern over Exceptions (BLOCKER):**
  Operations that can fail must not raise for business or operational failures. When the outcome reaches a domain entrypoint, a command handler, a formatter, or the wire format, return a Pydantic result object subclassing BaseResult; callers check .ok and render .errors/.warnings rather than catching exceptions. An internal helper with a single in-layer caller that only branches on success may instead return a plain value or an error message (str | None).

```python
# ✅ DO: return WorktreeDeleteResult(status=WorktreeDeleteStatus.NOT_FOUND, errors=['not found'])
# ❌ DO NOT: raise WorktreeNotFoundError('Worktree does not exist')
```

- **[MODEL-003] Reachable and Tested Status Enums (BLOCKER):**
  Every status StrEnum variant on a BaseResult model must be reachable from production code and covered by a test.

```python
# ✅ DO: class PruneStatus(StrEnum): OK = 'ok'; FAILED = 'failed'  # all values emitted and tested
# ❌ DO NOT: class PruneStatus(StrEnum): UNKNOWN = 'unknown'  # never emitted in src/
```

- **[MODEL-004] Definition Exception Hierarchy (BLOCKER):**
  Catalog-backed domain exceptions must subclass DefinitionError, DefinitionNotFoundError, DefinitionLoadError, or DefinitionValidationError from common/exceptions.py.

```python
# ✅ DO: class BlueprintLoadError(DefinitionLoadError): """YAML syntax error."""
# ❌ DO NOT: class BlueprintLoadError(Exception): """YAML syntax error."""
```

- **[CODE-001] Full-Word Identifiers (BLOCKER):**
  Write names as full words; do not shorten a word by dropping letters (res, req, msg, val, idx, tmp, rel_path, fs, exc, err, cfg, resp, acc, val_res). Allowed: single letters in comprehensions, generator expressions, and one-line loops; initialisms that are the domain term itself (db, cwd, ctx, id, url, cli, sha, ttl); and names a framework dictates (tmp_path, monkeypatch, ctx on a Click callback).

```python
# ✅ DO: validation_result = validator.validate(document)
# ❌ DO NOT: val_res = validator.validate(document); acc = []
```

- **[CODE-002] Logical Blank Lines Separation (NIT):**
  Separate distinct logical phases (setup, validate, persist, return) with a blank line. Keep cohesive, tightly coupled lines together.

```python
# ✅ DO:
record = WorktreeRecord(...)

self.db.worktrees.save(record)

return result
# ❌ DO NOT:
record = WorktreeRecord(...)
self.db.worktrees.save(record)
return result
```

- **[CODE-003] Terse Docstrings, Description Only for Hidden Contracts (SUGGESTION):**
  Every docstring opens with a one-line title stating what the thing is or does. A comma may enumerate a distinct list, but outcomes, failure paths, and side effects belong in the description, not the title. Add a description only for what the code cannot show a reader and whose absence would cause wrong code - a hidden contract, a data-loss or safety behavior, a non-obvious ordering constraint, or a deliberate oddity. Do not narrate steps, restate parameters, types, or the return annotation, or repeat the title in other words.

```python
# ✅ DO:
"""Base for providers that edit the worktree directly instead of returning a diff.

Edits that fail patch validation are discarded from the worktree; a failed discard is reported in the response errors."""
# ❌ DO NOT: """Run the provider. First resolve the baseline, then build the prompt, then run the tool, then diff the worktree, then validate the patch."""  # or: """Return flags for the rules, or why a rule cannot be preserved."""  # outcome in the title
```

- **[CODE-004] Docstrings Describe Only the Documented Unit (WARNING):**
  A docstring states only behavior the documented unit itself implements. Do not describe what a caller does with its result or logic that lives in another module, because that claim goes stale when the other module changes and nothing in the diff shows it. A reference to a sibling in the same module is acceptable.

```python
# ✅ DO:
"""Render a restricted policy's allow and deny rules as dontAsk flags.

A rule Claude Code cannot express yields its reason as a string."""
# ❌ DO NOT:
"""Render a restricted policy's allow and deny rules as dontAsk flags.

The adapter surfaces a returned reason as an unsupported-policy error before the process is spawned."""
```

- **[CODE-005] One Docstring per Unit (WARNING):**
  A module, class, or function carries exactly one docstring. A bare string literal statement after the docstring is dead code that reads as documentation; merge it into the docstring or delete it.

```python
# ✅ DO: """Render a restricted policy as dontAsk flags."""
# ❌ DO NOT: """Return dontAsk flags for the rules."""  # followed by a second """...""" string statement
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

- **[TYPE-004] Explicit ClassVar Typing for Table Names and Constants (NIT):**
  Typing table names or string constants as Any or bare string instead of explicit ClassVar[str] causes type checker ambiguity. Declare class-level SQL constants explicitly.

```python
# ✅ DO: __tablename__: ClassVar[str] = 'worktrees'
# ❌ DO NOT: __tablename__ = 'worktrees'
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

- **[PERF-002] Database Migration Hygiene and Consumer Call Sites (BLOCKER):**
  Every new table or column must have real caller sites in src/ in the same PR. One-time data backfills must be versioned Alembic revisions (op.execute()), not ad-hoc raw SQL in models.

```python
# ✅ DO: # Migration adds a column and a repository method reads and writes it
# ❌ DO NOT: # Migration adds column future_flag with zero callers in src/
```

- **[PERF-003] Indexed Column Queries vs O(n) In-Memory Lookups (WARNING):**
  Do not perform O(n) in-memory list comprehensions or linear scans over repository records when an indexed database column exists (e.g., querying _record_for_rel_path). Query the repository slice directly with filtered SQL.

```python
# ✅ DO: record = repo.get_by_path(relative_path)
# ❌ DO NOT: record = next((r for r in repo.list() if r.path == relative_path), None)
```

- **[API-001] Sibling Repository CRUD Method Naming Consistency (WARNING):**
  Sibling repositories name the same operation with the same verb, so a caller who knows one repository can use the others without reading each class, and a divergent name reads as a different behavior.

  Answer yes to every criterion:
  - `API-001.Q01` (judgment) Does every repository class that inserts a new record expose that operation as a public create() method?

    ```python
    # ✅ DO: WorktreesRepository.create(...) and SessionsRepository.create(...)
    # ❌ DO NOT: CostsRepository.record_token_usage(...) inserting a CostRecord while sibling repositories use create()
    ```
  - `API-001.Q02` (judgment) Does every repository method that returns at most one record looked up by its identifying key have the name get?

    ```python
    # ✅ DO: def get(self, id: str) -> WorktreeRecord | None:
    # ❌ DO NOT: def find(self, id: str) -> WorktreeRecord | None:  # siblings use get()
    ```
  - `API-001.Q03` (judgment) Does every repository method that returns a collection of records of one entity have the name list?

    ```python
    # ✅ DO: def list(self, status: WorktreeStatus | None = None) -> list[WorktreeRecord]:
    # ❌ DO NOT: def all(self) -> list[WorktreeRecord]:  # siblings use list()
    ```
  - `API-001.Q04` (judgment) Does every repository method that removes a record have the name delete?

    ```python
    # ✅ DO: def delete(self, id: str) -> bool:
    # ❌ DO NOT: def remove(self, id: str) -> bool:  # siblings use delete()
    ```
  - `API-001.Q05` (judgment) Does every repository method that persists changes to a whole existing record have the name save?

    ```python
    # ✅ DO: def save(self, record: SessionRecord) -> SessionRecord:
    # ❌ DO NOT: def store(self, record: SessionRecord) -> SessionRecord:  # whole-record persist not named save()
    ```
  - `API-001.Q06` (judgment) Does every repository method named insert, add or upsert have a docstring or comment stating why a canonical verb does not fit?

    ```python
    # ✅ DO: def upsert(self, ...) -> ...:  with the docstring "Not save(): replaces the row only when the revision matches."
    # ❌ DO NOT: def insert(self, record: WorktreeRecord) -> WorktreeRecord:  # no stated reason; siblings use create()
    ```
  - `API-001.Q07` (judgment) Do all repository classes that expose the same non-CRUD operation give it the same method name?

    ```python
    # ✅ DO: WorktreesRepository.update_status(...) and SessionsRepository.update_status(...)
    # ❌ DO NOT: WorktreesRepository.update_status(...) next to SessionsRepository.set_state(...) for the same status change
    ```

- **[API-002] Consistent Error Semantics Across Queries (WARNING):**
  Query paths that read the same data report bad rows the same way, so a caller never mistakes a silently swallowed failure in one path for an empty result while a sibling path reports it.

  Answer yes to every criterion:
  - `API-002.Q01` (judgment) Does every query method that reads an entity report an invalid enum value or corrupt row, by raising or by returning a BaseResult error, whenever a sibling query method that reads that entity reports it?

    ```python
    # ✅ DO: get() and list() both raise ValueError for a row with an invalid status
    # ❌ DO NOT: list() raises on a row with an invalid status while get() returns None for the same row
    ```
  - `API-002.Q02` (judgment) Does every except block in a query method re-raise or convert the caught error into a BaseResult error, instead of returning None or an empty value?

    ```python
    # ✅ DO: except ValidationError as error: return RecordResult.from_error(error)
    # ❌ DO NOT: except ValidationError: return None  # swallows the error
    ```

- **[DRY-001] Shared Repository Transaction Logic in BaseRepository (SUGGESTION):**
  Transaction handling for repository writes lives once in BaseRepository, so commit, rollback, deletion and session behavior stay identical across repositories and a fix is made in one place instead of in every sibling.

  Answer yes to every criterion:
  - `DRY-001.Q01` (judgment) Does every repository method that persists a record commit through BaseRepository._commit?

    ```python
    # ✅ DO: return self._commit(session, record, f"Worktree with id '{id}' already exists")
    # ❌ DO NOT: session.add(record); session.commit()  # in a repository subclass
    ```
  - `DRY-001.Q02` (judgment) Does every repository method that deletes a single record delete it through BaseRepository._delete_where?

    ```python
    # ✅ DO: return self._delete_where(session, select(WorktreeRecord).where(WorktreeRecord.id == id))
    # ❌ DO NOT: session.delete(record); session.commit()  # in a repository subclass
    ```
  - `DRY-001.Q03` (judgment) Is the rollback after a failed commit written only in BaseRepository?

    ```python
    # ✅ DO: No try/except IntegrityError and no session.rollback() in worktrees.py, sessions.py, artifacts.py or costs.py
    # ❌ DO NOT: except IntegrityError: session.rollback(); raise ValueError(...)  # in a repository subclass
    ```
  - `DRY-001.Q04` (judgment) Does every repository method obtain its session from BaseRepository.session()?

    ```python
    # ✅ DO: with self.session() as session:
    # ❌ DO NOT: with get_session(self.db_engine) as session:  # in a repository subclass
    ```
  - `DRY-001.Q05` (judgment) Is every other transaction block shared by two or more repository classes defined once in BaseRepository?

    ```python
    # ✅ DO: Two repositories both call self._delete_where(...) for single-record deletes
    # ❌ DO NOT: The same try/commit/rollback/refresh block copied into both SessionsRepository and WorktreesRepository
    ```

- **[DB-001] Redundant Index Declaration on Unique Columns (NIT):**
  Declaring index=True on a column that already declares unique=True creates redundant SQLite B-tree indexes. A unique constraint already creates a backing index automatically.

```python
# ✅ DO: session_id: str = Field(unique=True)
# ❌ DO NOT: session_id: str = Field(unique=True, index=True)
```

- **[DB-002] Released Migrations Are Immutable and Reversible (BLOCKER):**
  A migration revision that exists in a release tag must not be edited, renamed or deleted; fix it with a new revision. Every revision must implement both upgrade() and downgrade() using dialect-neutral Alembic operations (op.batch_alter_table for column and constraint changes, no SQLite-only SQL).

```python
# ✅ DO: # New revision 0003 with upgrade(), downgrade() and a row-preserving upgrade/downgrade test
# ❌ DO NOT: # Editing 0001_initial_schema.py after v0.6.0, or a revision whose downgrade() is `pass`
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
  Maintain backwards compatibility ONLY for surfaces users interact with directly: CLI command surface (commands, subcommands, arguments, flags), configuration files and blueprint YAMLs (config.json), and stable machine-readable output formats (JSON event envelopes).

```python
# ✅ DO: output_format: OutputFormat = typer.Option(OutputFormat.RICH, '--output-format', '-o')
# ❌ DO NOT: # Renaming user CLI option --timeout to --wait without alias
```

- **[COMPAT-002] Ban on Internal Compatibility Shims and Aliases (BLOCKER):**
  Do NOT preserve backwards compatibility aliases, compatibility properties, or shim layers for internal code (common/, core/, or internal CLI modules) when refactoring or renaming symbols. Update callers and tests directly.

```python
# ✅ DO: # Renamed _unlock_fd directly and updated all internal callers
# ❌ DO NOT:
def _unlock_fd(self): ...
_unlock = _unlock_fd  # internal shim alias
```

- **[COMPAT-003] Greenfield Architecture Default (BLOCKER):**
  Treat the repository as greenfield by default: plan no dual code paths, fallback adapters, or deprecation windows unless an issue explicitly demands one. Replace superseded paths and update callers in the same change set.

```python
# ✅ DO: # Directly replaces legacy loader with new unified loader
# ❌ DO NOT: if config.use_legacy: return LegacyLoader().load()  # unnecessary compatibility branch
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
