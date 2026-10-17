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
  exceptions.py     # Domain exceptions
  <domain>.py       # Domain entrypoint (named after the domain, e.g. prune.py; never facade.py)
  services/         # Imperative operations
    <verb>.py       # loader, runner, renderer, resolver, etc.
```

- Put domain types in `models.py` and imperative operations in `services/<verb>.py`.
- Do not define public models inside `services/` or add domain logic directly under package roots.

---

## Models and Result/Outcome Pattern

- **External / Wire / Command Results:** Operations whose outcome reaches a domain entrypoint, command handler, formatter, or wire format return a Pydantic `BaseResult` subclass (`extra="forbid"`, `strict=True`). Scoped exceptions (`extra="ignore"` on hand-authored YAML models `BlueprintDefinition` and `LoopStepBlock`) must carry a justifying comment (`tests/lint/test_model_strictness.py`).
- **Internal Helpers:** Single-caller internal helpers and in-memory specs/context hops should return plain values, `@dataclass(frozen=True, slots=True)`, or `str | None` rather than inventing one-off Pydantic wrappers.

---

## Docstrings

- All non-test public functions and methods carry a Google-style docstring (`D417` requires all parameters documented when `Args:` is present).
- Keep docstrings terse: a one-line title, plus a description only for hidden contracts or safety behavior the signature cannot show.
- Describe only the documented unit's own behavior, never what callers do with it.

---

## Type Annotations and `Any`

- **`Any` on a return type (`-> Any`)** loses type checking at every call site transitively; treat it as a defect unless the value is genuinely unconstrained.
- **Prefer `object` over `Any`** when a value is only stored, compared, or passed through.
- **Permitted `Any`:**
  1. Pydantic `mode="before"` validator signatures (`(cls, value: Any) -> Any`).
  2. `dict[str, Any]` at JSON/YAML serialization boundaries (`model_dump(mode="json")`).
  3. Values evaluated dynamically from user documents (`engine/executors/conditions.py`).
  4. `**kwargs: Any` on a pass-through wrapper.
- **Banned `Any`:** Dodging an import boundary, test seams, replacing an existing model type, or leaving generic parameters unannotated (`Popen[Any]` instead of `Popen[str]`).

---

## Type Checker Suppressions

Default: fix the type. Every `# pyright: ignore[reportRuleName]` requires an explicit trailing reason (`tests/lint/test_pyright_ignores.py`). Bare `# type: ignore` is never honored.

- **Permitted (with reason):**
  1. Intentional ill-typed test inputs whose subject is a runtime `TypeError` or `ValidationError`.
  2. Third-party stub conflicts (`__tablename__: ClassVar[str]` in `core/db/models.py`).
  3. Platform-gated imports (`msvcrt` when `pythonPlatform = "Linux"` in `common/lock.py`).
- **Banned:** Silencing wrong-shaped test fixtures/mocks, hiding nullable attributes, or omitting the reason.
