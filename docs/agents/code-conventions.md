# Code Conventions

Coding standards and patterns for the Worktree CLI codebase.

---

## Pydantic Models

**Relevant sources:** `src/worktree/core/*/models.py`, `src/worktree/common/models.py`

- Scoped exceptions allowing non-strict model configuration (must carry a justifying comment):
  - `OllamaModelStdout` (`core/agents/ollama.py`): leniency for LLM-generated JSON.
  - `BlueprintDefinition`, `LoopStepBlock` (`core/catalog/definitions/blueprint.py`, `core/catalog/definitions/step.py`): hand-authored YAML models using `extra: "ignore"`.

---

## Variable Naming

Prioritize clarity and readability: code should read naturally and unambiguously.

- **Comprehensions and generator expressions**: Single-letter variables (e.g. `p`, `x`, `i`, `v`, `c`, `k, v`) are standard and encouraged for short, local scopes.
- **Accepted common abbreviations and idioms**: Widely recognized programming idioms and domain abbreviations are permitted when they keep code concise without hurting readability:
  - Key/value and loop constructs: `k, v` (in dict iteration or comprehensions), `i, v` (in enumerate).
  - Standard programming idioms: `req`, `res`, `fn` / `fn_node`, `idx`, `mod` / `mod_name`, `loc`, `tmp` / `temp`, `str`, `arr`, `num`, `len`, `val`, `msg`.
  - Established domain conventions: `exc`, `rel_path`, `fs`, `cwd`, `db`, `ctx`.
- **Disallowed**:
  - Cryptic, arbitrary, or idiosyncratic truncations that harm readability (e.g. `val_res` instead of `validation_result` or `result`, `err_msg` instead of `error_message`, or arbitrary letter-dropping like `acc` when context is ambiguous).
  - Arbitrary single-letter variables that carry no conventional meaning in context.
- **Readability rule of thumb**: Does the line of code still read easily with the abbreviation? If an abbreviation is widely understood in the context of the function and does not force the reader to pause or guess its meaning, it is acceptable. If it obscures intent or requires deciphering, write the full word.

---

## Structure and Complexity

- Avoid God-functions; decompose complex workflows into focused helpers.
- Do not add test seams to production function or class signatures.

### Blank Lines in Function Bodies
- Separate distinct logical phases (setup, validate, persist, return) with a blank line.
- Keep cohesive, tightly coupled lines together.

---

## Docstrings

**Relevant sources:** `pyproject.toml` (`[tool.ruff.lint.pydocstyle]`)

- All non-test public functions and methods must carry a docstring following Google docstring style.
- When documenting parameters (`Args:`), all function parameters must be documented (enforced by `D417`).
- Test functions under `tests/` are exempt from Ruff `D` rules per [testing.md](testing.md).
- Keep docstrings terse: a one-line title, plus a description only for hidden contracts or safety behavior the code cannot show (CODE-003).

---

## Core Package Layout

**Relevant sources:** `src/worktree/core/`

Standard package skeleton for domain logic:

```text
core/<domain>/
  __init__.py       # Re-export public API only
  models.py         # BaseModel, StrEnum, dataclasses, Protocols
  exceptions.py     # Domain exceptions
  facade.py         # Domain facade class (if applicable)
  services/         # Imperative operations
    <verb>.py       # loader, runner, renderer, resolver, etc.
```

- **Must:** Put new domain types in `models.py` and imperative operations in `services/<verb>.py`.
- **Must not:** Add logic directly under package roots (except documented entrypoints), define public models in `services/`, or extend legacy flat layouts.

---

## Result/Outcome Pattern

**Relevant sources:** `src/worktree/common/models.py`, `src/worktree/core/*/models.py`

Operations that can fail return a Pydantic result object subclassing `BaseResult` instead of raising:
- `status: StrEnum`: Outcome state.
- `warnings: list[str]`: Non-fatal issues (inherited from `BaseResult`).
- `errors: list[str]`: Fatal issues (inherited from `BaseResult`).
- `fixes: list[str]`: Suggested fixes or remediations (inherited from `BaseResult`).
- `ok: bool`: Property returning `not bool(self.errors)` or `status == OK`.
- Callers check `.ok` and render `.errors` / `.warnings` rather than catching exceptions.

---

## Atomic File Writes

**Relevant sources:** `src/worktree/common/filesystem/services/operations.py`, `src/worktree/common/filesystem/facade.py`

- Never write config or state files directly in-place.
- Write to a `.tmp` sibling, flush, `os.fsync`, and atomically swap via `Path.replace`.
- Use `Filesystem.atomic_write_json` and `Filesystem.atomic_write_text`.

---

## Console Output and Terminal Formatting

**Relevant sources:** `src/worktree/cli/ui/`

- `to_rich` derives nothing. It reads `transform(data)` and lays it out into Rich renderables.
- `to_raw` bypasses the view entirely and returns bytes the caller asked for; `DiffResultFormatter` is the only implementation.
- Domain shared table builders reside in `src/worktree/cli/ui/formatters/<domain>/common.py`.
- Construct `errors` and `warnings` messages using inline f-strings or literals at call sites. Do not create private single-message formatting wrappers (domain lookup tables of constant remediation strings, such as `REMEDIATION_MAP` in `core/status/services/collector.py`, are permitted as tables of literals).

---

## Type Annotations and `Any`

**Relevant sources:** `pyproject.toml` (`[tool.basedpyright]`)

`typeCheckingMode = "recommended"` reports every `Any` as a warning
(`reportAny`, `reportExplicitAny`). Warnings do not fail
`basedpyright src --level error`, so acting on them is a judgement call, and
this is the basis for that judgement.

**Parameters and returns are not equivalent.** `Any` on a parameter loses
checking inside one function. `Any` on a return type loses it at every call
site, transitively. Treat `-> Any` as a defect unless the value is genuinely
unconstrained.

**Prefer `object` over `Any`** when a value is only stored, compared, or passed
through. `object` forces a narrow before use; `Any` forces nothing.

### Permitted, do not "fix" these

1. **Pydantic `mode="before"` validator signatures.** A pre-validator receives
   whatever the user wrote in `config.json` or a blueprint YAML, so
   `(cls, val: Any) -> Any` is the contract. See `core/catalog/definitions/step.py`,
   `core/inputs/models.py`, `core/catalog/definitions/blueprint.py`.
2. **`dict[str, Any]` at a serialization boundary.** The result of
   `model_dump(mode="json")`, parsed YAML, or a JSON payload.
3. **Values read out of a user document and then compared.**
   `core/step/services/conditions.py` evaluates `until:` expressions against
   arbitrary JSON. Use `object` where only equality or truthiness is needed;
   keep `Any` where the value is indexed or used arithmetically.
4. **`**kwargs: Any` on a pass-through wrapper** that does not inspect the
   values.

### Banned

1. **`Any` that dodges an import boundary.** An annotation that exists because
   the real type cannot be imported from the current package is a symptom of
   misplaced code. Move the code, then name the type.
2. **`Any` as a test seam.** An `output: Any = None` parameter that production
   never reads exists only so a test can pass a stub. Delete the parameter; see
   "No test seams in production code" in [testing.md](testing.md).
3. **`Any` where a model already exists.** `step: Any` or `metadata: Any` when
   `StepDefinition` is right there. Same defect as a
   `getattr(obj, "field", "unknown")` chain: it moves a type error to runtime
   and defaults it to a wrong value.
4. **`Any` in a third-party override.** `def invoke(self, ctx: Any) -> Any`
   overriding a Click method should name `click.Context`.
5. **`Any` filling a generic you did not want to think about.**
   `subprocess.Popen[Any]` should be `Popen[bytes]` or `Popen[str]`; the code
   already knows which.

### Rule of thumb

If you cannot say in one sentence what values can arrive, `Any` is honest. If
you can, name them.

---

## Type Checker Suppressions

**Relevant sources:** `pyproject.toml` (`[tool.basedpyright]`)

**Default: fix the type.** An ignore is a last resort, never a way to green `basedpyright --level error`. Every permitted ignore requires an explicit reason.

### Permitted, do not "fix" these

1. **Intentional ill-typed test inputs** whose subject is a runtime
   `TypeError` or `ValidationError`. `Step.load(123)` raising is the contract;
   the checker is correctly complaining. Prefer `Model.model_validate({...})`
   when that exercises the same path (Pydantic extra-field tests), because it
   needs no suppression. Keep the ignore when the call itself is the subject.
2. **Third-party stub conflicts** that our code cannot name correctly.
   SQLModel's `__tablename__: ClassVar[str]` versus SQLAlchemy's mapped type
   (`reportIncompatibleVariableOverride` in `core/db/models.py`) is the
   current instance.
3. **Platform-gated imports.** `msvcrt` does not exist when
   `pythonPlatform = "Linux"` (`reportMissingImports`,
   `reportConstantRedefinition` in `common/lock.py`).

Every permitted ignore carries a one-line reason naming which of the three
applies.

### Banned

1. **Any ignore that hides a type we can write.** `_fs: Filesystem = None`
   (`reportAssignmentType` in `core/config/facade.py`) is the teaching case:
   the annotation is lying, and the ignore is what keeps the lie compiling.
2. **`reportCallIssue` / `reportArgumentType` used to silence a sloppy test.**
   If the checker rejects a `MagicMock`, a wrong-shaped dict, or a missing
   generic argument, the fixture is the defect. `multiprocessing.Queue`
   becomes `Queue[dict[str, object]]`, not an ignore.
3. **`reportIncompatibleVariableOverride` except the SQLModel `__tablename__`
   stub.** A subclass that does not match its parent is a design bug.
4. **An ignore with no reason.**

### Rule of thumb

If the line is ill-typed on purpose, ignore with a reason. If it is ill-typed
by accident, fix it. If you cannot tell, it is an accident.

---

## Encapsulation and Private Members

- **Expose query properties**: Expose public boolean query properties (e.g. `is_interactive`, `is_terminal_format`, `is_enabled`, `has_*`) on classes rather than referencing private members from external callers.

---

## Backwards Compatibility

- Maintain backwards compatibility **only** for surfaces users interact with directly:
  - CLI commands, subcommands, arguments, and flags (e.g. renaming a sub-command).
  - Configuration files and blueprint YAML definitions (e.g. keys or values in `config.json`).
  - Stable machine-readable CLI output formats (e.g. JSON output event envelopes).
- Do **not** preserve backwards compatibility aliases, compatibility properties, or shim layers for internal code (`common/`, `core/`, or internal `cli/` modules) when refactoring or renaming symbols (e.g., do not keep `_unlock_fd` when renaming to `_unlock_file_descriptor`, or property aliases like `_fd`). Refactor internal callers and tests directly.
- **When in doubt**: Ask the user before introducing compatibility layers or deprecation shims for ambiguous boundaries.
