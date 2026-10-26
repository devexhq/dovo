# Code Conventions

Coding standards and patterns for the Dovo CLI codebase. Companion to [AGENTS.md](../../AGENTS.md).

---

## Variable Naming

Write names as full words. Do not shorten a word by dropping letters.

- **Allowed:**
  - Single letters in comprehensions, generator expressions (`p`, `x`, `i`, `k, v`), and one-line loops.
  - Domain initialisms: `db`, `cwd`, `ctx`, `id`, `url`, `cli`, `sha`, `ttl`.
  - Framework-dictated names: pytest's `tmp_path` and `monkeypatch`, or Click's `ctx`.
- **Banned:** Abbreviated words (`res`, `req`, `msg`, `val`, `idx`, `tmp`, `rel_path`, `fs`, `exc`, `err`, `cfg`, `resp`, `acc`, `val_res`). Spell them out: `result`, `request`, `message`, `value`, `index`, `relative_path`, `filesystem`, `error`.
- **Scope:** Apply to names you add or modify; do not rename unrelated code in a feature PR.

---

## Core Package Layout

Standard package skeleton for domain logic under `src/dovo/core/`:

```text
core/<domain>/
  __init__.py       # Re-export public API only
  models.py         # BaseModel, StrEnum, dataclasses, Protocols
  exceptions.py     # The package's one DomainError subclass
  <domain>.py       # Domain entrypoint (named after the domain, e.g. prune.py; never facade.py)
  services/         # Imperative operations
    <verb>.py       # loader, runner, renderer, resolver, etc.
```

- Put domain types in `models.py` and imperative operations in `services/<verb>.py`.
- Do not define public models inside `services/` or add domain logic directly under package roots.

---

## Models and Result/Outcome Pattern

- **Entrypoint results:** A public entrypoint (a method of the entrypoint class in `core/<pkg>/<pkg>.py`, or an `engine/services/` runner, called from a layer above) returns a Pydantic `BaseResult` subclass (`extra="forbid"`, `strict=True`), one per public command. Scoped exceptions (`extra="ignore"` on hand-authored YAML models `BlueprintDefinition` and `LoopStepBlock`) must carry a justifying comment (`tests/lint/test_model_strictness.py`).
- **Internal helpers:** Services and helpers behind an entrypoint return plain values, domain models, or `@dataclass(frozen=True, slots=True)`. They never return a `*Result`, and never define a `*Status` enum to classify their own failures.
- **Statuses and error codes:** `error_code` is the only classification of a failure. A status value never names a failure cause (`not_found`, `already_exists`, `write_failed`, `syntax_error`): when two values would differ only in which failure occurred, that difference is the `error_code`. A new status enum or value holds only outcomes `error_code` cannot express, such as `dovo status` health or how much of a multi-item operation failed. No `match` or table picks a status from an error code, or an error code from a status. Some existing results still carry failure-kind statuses; the conversion issues delete them, and new code does not add to them.
- **Domain errors:** Each package defines one `DomainError` subclass in `exceptions.py` (`ProjectIdentityError` in `src/dovo/core/project/exceptions.py`). Helpers raise it with an `ErrorCode`, a message and remediations. The entrypoint, or a caller in another package, catches it once with `catch_failure` (`dovo.common.exceptions`) and copies the failure's `error_code`, `message` and `remediations` into its result, and its `diagnostic`, when one was recorded, into the result's `diagnostics` (`error_diagnostics`). `catch_failure` records a `DomainError` only when its cause is an `OSError`, so the cause decides whether a failure leaves an event file. A `DomainError` raised while handling an `OSError` on Dovo's own state (`.dovo/`, the session directory and its temp directory, `.dovo/project.json`) chains it with `from exc`, so the broken workspace can be diagnosed. One raised for an external tool or the environment, which its code and message already name (git missing or failing to start), is raised `from None` and leaves no event file. `catch_failure` records unexpected exceptions in the diagnostic log, so helpers do not catch broad `Exception` to report it; a helper that recovers from a failure, or collects per-item failures and continues, records each exception it catches with `record_exception` (`dovo.common.diagnostic_log`). No module maps one status enum or error code onto another.

---

## Docstrings

- All non-test public functions and methods carry a Google-style docstring (`D417` requires all parameters documented when `Args:` is present).
- Keep docstrings terse: a one-line title, plus a description only for hidden contracts or safety behavior the signature cannot show.
- Describe only the documented unit's own behavior, never what callers do with it.

---

## Type Annotations and `Any`

- **No bare `Any` annotation:** ruff `ANN401` rejects `Any` as a parameter or return annotation, `*args: Any` and `**kwargs: Any` included. Type the value, or use `object` when it is unconstrained.
- **Prefer `object` over `Any`** when a value is only stored, compared, or passed through. Values from YAML documents or user input (`engine/executors/conditions.py`, `assertions/json_match.py`) are `object`, narrowed with `isinstance` or a `TypeIs` guard where the code operates on them. Pydantic `mode="before"` validators take and return `object` (`(cls, value: object) -> object`).
- **`pydantic.JsonValue`** only for data that must be JSON, such as `json.loads` output or a value written to `config.json` (`core/config/parser.py`). `yaml.safe_load` can return `datetime` and `date`, so YAML data is `object`.
- **Permitted `Any`:** `dict[str, Any]` at JSON/YAML serialization boundaries (`model_dump(mode="json")`).
- **Banned `Any`:** Dodging an import boundary, test seams, replacing an existing model type, or leaving generic parameters unannotated (`Popen[Any]` instead of `Popen[str]`).
- **Budget:** `tests/lint/test_explicit_any.py` fails when a file names `Any` more often than its `ANY_BUDGET` entry (zero when unlisted), and when an entry is higher than the file's count. Removing an `Any` means lowering its entry; adding a permitted one means raising it in the same change.

---

## Type Checker Suppressions

Default: fix the type. Every `# ty: ignore[rule-name]` requires an explicit trailing reason (`tests/lint/test_type_ignores.py`). Bare `# type: ignore` is never honored (`respect-type-ignore-comments = false`).

- **Permitted (with reason):**
  1. Intentional ill-typed test inputs whose subject is a runtime `TypeError` or `ValidationError`.
  2. Third-party stub gaps where narrowing or a typed constructor (`SQLModelConfig`, `isinstance(command, TyperGroup)`) is not possible.
- **Banned:** Silencing wrong-shaped test fixtures/mocks, hiding nullable attributes, or omitting the reason.
