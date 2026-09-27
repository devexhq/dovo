<!-- AUTO-GENERATED FROM rules_spec.yaml. DO NOT EDIT DIRECTLY. -->
# Architectural & Coding Invariants (Common Domain)

> **Notice for Agents:** Code violating `BLOCKER` rules will fail verification.

- **[ARCH-001] Strict Layered Import Flow (BLOCKER):**
  Dependencies flow strictly one way: common/ -> core/{db,git,sandbox,catalog,inputs,patch,diff,status}/ -> core/agents/ -> core/step/ -> {core/runtime/, core/blueprint/} -> core/engine/ -> {core/history/, core/logs/} -> cli/. Upward imports are strictly prohibited.

```python
# ✅ DO: from worktree.core.runtime.engine import run_steps  # in core/engine/
# ❌ DO NOT: from worktree.core.engine.engine import Engine  # upward import in core/runtime/
```

- **[RENDER-003] Inline Error and Warning String Construction (SUGGESTION):**
  Construct errors, warnings, and fixes strings using inline f-strings or literals at call sites. Do not create private single-message formatting wrappers.

```python
# ✅ DO: errors.append(f"Blueprint '{path}' invalid: {err}")
# ❌ DO NOT: errors.append(self._format_validation_error(path, err))
```

- **[MODEL-001] Scoped Model Exceptions with Justifying Comment (BLOCKER):**
  Scoped exceptions allowing extra='ignore' are restricted to hand-authored YAML models (BlueprintDefinition, BlueprintDefaults, LoopStepBlock) and LLM JSON output (OllamaModelStdout), and MUST carry a justifying comment.

```python
# ✅ DO:
# User YAML models permit forward-compatible extra keys
model_config = {'extra': 'ignore'}
# ❌ DO NOT: model_config = {'extra': 'ignore'}  # missing justifying comment
```

- **[CODE-001] Identifier Readability and Approved Abbreviations (BLOCKER):**
  Code must read naturally. Reject cryptic, arbitrary truncations (e.g. val_res, err_msg, acc). Permitted abbreviations: iteration variables (p, x, i, v, c, k, v), standard programming idioms (req, res, fn, fn_node, idx, mod, mod_name, loc, tmp/temp, str, arr, num, len, val, msg), and domain conventions (exc, rel_path, fs, cwd, db, ctx).

```python
# ✅ DO: validation_result = validator.validate(document)
# ❌ DO NOT: val_res = validator.validate(document); acc = []
```

- **[CODE-002] Logical Blank Lines Separation (NIT):**
  Separate distinct logical phases (setup, validate, persist, return) with a blank line. Keep cohesive, tightly coupled lines together.

```python
# ✅ DO:
record = SandboxRecord(...)

self.db.sandboxes.save(record)

return result
# ❌ DO NOT:
record = SandboxRecord(...)
self.db.sandboxes.save(record)
return result
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

- **[ENCAP-001] Expose Public Query Properties (SUGGESTION):**
  Expose public boolean query properties (e.g. is_interactive, is_enabled, has_*) on classes rather than referencing private members from external callers.

```python
# ✅ DO:
@property
def is_running(self) -> bool: return self._proc is not None and self._proc.poll() is None
# ❌ DO NOT: if runner._process is not None: ...  # caller inspecting private state
```

- **[ENCAP-002] No Test Seams in Production Signatures (BLOCKER):**
  Never add a parameter, kwarg, or callback solely for test injection. A parameter production never reads is dead code with a test attached. Monkeypatch collaborators at module boundaries instead.

```python
# ✅ DO: def run(self, cwd: Path) -> RunOutcome: ...  # tests monkeypatch at boundary
# ❌ DO NOT: def run(self, cwd: Path, _runner: GitRunner | None = None) -> RunOutcome: ...
```

- **[PERF-001] No Ad-Hoc DB Instantiations in Loops (BLOCKER):**
  Never instantiate a repository or WorktreeDb inside loops or test helpers. Inject pre-initialized instances to prevent N+1 SQLite connection/migration checks.

```python
# ✅ DO:
repo = RunsRepository(path)
for item in items: repo.create(item)
# ❌ DO NOT: for item in items: repo = RunsRepository(path); repo.create(item)
```

- **[FS-001] Atomic File Writes via Temporary Sibling (BLOCKER):**
  Never write config or state files directly in-place. Write to a .tmp sibling, flush, os.fsync, and atomically swap via Path.replace using Filesystem.atomic_write_json and Filesystem.atomic_write_text.

```python
# ✅ DO: Filesystem.atomic_write_json(config_path, data)
# ❌ DO NOT: with open(config_path, 'w') as f: json.dump(data, f)
```

- **[FS-002] Advisory Cross-Process Locking (BLOCKER):**
  Multi-process sandbox, catalog, or state mutations must acquire the .worktree/.lock advisory lock using common/lock.py and handle LockTimeoutError.

```python
# ✅ DO: with file_lock(lock_path, timeout=10.0): sandbox_service.create(...)
# ❌ DO NOT: sandbox_service.create(...)  # mutating shared dir without acquiring lock
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
  Update docs/agents/schemas.md whenever .worktree/config.json keys, blueprint YAML fields, domain DTOs, or database record shapes change.

```python
# ✅ DO: # Adding SandboxPruneResult to section 2 of schemas.md
# ❌ DO NOT: # Adding new Result model in core/models.py without updating schemas.md
```

- **[DOC-004] Ban on Duplicating Source in Docs (SUGGESTION):**
  Docs must not hand-copy Pydantic model signatures, field tables, or enum lists that a Read of the source already gives unambiguously. Link to the model file and document only unexpressed behavior (validators, resolution order).

```python
# ✅ DO: See [`SandboxSession`](src/worktree/core/sandbox/models.py). Sandboxes live under .worktree/sandboxes/.
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
  Adhere strictly to definitions in docs/agents/glossary.md. Do not conflate Task (linear steps only) vs Workflow (allows loop steps), Blueprint (unified document), Step, Run, Session, Sandbox, Checkpoint.

```python
# ✅ DO: 'Task blueprint containing only linear step definitions.'
# ❌ DO NOT: 'Task blueprint containing a loop block.'  # tasks are strictly linear
```

- **[DOC-008] Verifiable Doc and Rule Claims (BLOCKER):**
  A doc, skill, or rule sentence asserting that something is enforced, gated, or checked must name the enforcing artifact by path, and that path must resolve to a file in the tree. The same applies to any helper, fixture, builder, or assertion function a doc instructs an implementer to use: the symbol exists before the instruction ships. Documentation of behavior that is planned rather than built is prohibited, including in rule examples, because an exemplar is copied more often than the guideline is read. When a claim becomes false, the fix is to delete or correct the sentence in the same change, never to leave it as aspiration.

```python
# ✅ DO: # "COLUMNS is pinned to 160 by [tool.pytest_env] in pyproject.toml" - resolvable and true
# ❌ DO NOT: # "enforced by tests/lint/test_doc_parity.py" in AGENTS.md, where the module does not exist
```

- **[CI-001] Pre-Commit Quality Suite Gate (BLOCKER):**
  Before committing, all five gates must pass: ruff format, ruff check, basedpyright src tests --level error with zero errors, inv complexity with complexity at or under 10, and inv test -c meeting the coverage floor configured in pyproject.toml under [tool.coverage.report] fail_under. That floor is the contract, it ratchets upward only as real contract tests land, and it is never lowered to make a commit pass. Coverage is a regression backstop, not a target: do not add tests to raise the percentage, and read a coverage drop caused by deleting duplicated or dead tests as a success.

```python
# ✅ DO: uv run inv test -c && ruff format . && ruff check . && basedpyright src tests --level error && inv complexity
# ❌ DO NOT: # fail_under lowered so a commit can pass, or tests added purely to reach a percentage
```

- **[CI-002] Git and Pull Request Attribution Hygiene (BLOCKER):**
  Commit messages must be semantic, imperative, and contain NO AI trailers (Co-authored-by: Cursor, Co-authored-by: ...). PRs must have single responsibility and contain concise Why, Approach, and Linkage sections.

```python
# ✅ DO: feat(sandbox): add prune subcommand for stale sandboxes
# ❌ DO NOT:
feat: prune sandboxes

Co-authored-by: Cursor <cursor@cursor.sh>
```

- **[CI-003] Agentic Process State Declaration (WARNING):**
  Before executing commands or editing files, the agent must state: (1) The specific directive/doc governing this action, and (2) The target scope (e.g. specific test package or module).

```python
# ✅ DO:
Governing directive: docs/agents/testing.md
Target scope: tests/core/sandbox/test_lifecycle.py
# ❌ DO NOT: Running write_to_file without stating governing directive or target scope
```

- **[CI-004] Mechanical Enforcement Parity (BLOCKER):**
  A BLOCKER rule that can be checked mechanically must have an executing check: an invariant test under tests/lint/, a prek hook, or a CI step. A declared gate whose configuration disables it, such as a coverage floor of zero or a type checker present in no hook and no workflow, counts as unenforced and must be either wired up or downgraded to a review-time WARNING. An invariant check must prove its own scope with a regression test shaped like the code it polices, and lands green by carrying an explicit burn-down allowlist of known violators; allowlist entries shrink and are never added to.

```python
# ✅ DO: # ARCH-001 enforced by tests/lint/test_import_boundaries.py
# ❌ DO NOT: # Scanner walks only tree.body functions, so class-based tests are never inspected
```
