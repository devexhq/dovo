# Testing

Testing conventions, taxonomy, and execution patterns for Dovo CLI. Narrative companion to
`tests/docs/RULES.md` (auto-generated, TEST-001+); where the two disagree, RULES.md wins.

## The rule that matters most

**Test at contracts, not at implementation.** A contract is a boundary chosen deliberately: a
`BaseResult` object, a JSON payload, an exit code, a file on disk, a git ref. Everything else
(rendered layout, private helpers, call ordering, constructor assignments) is implementation and
must not be asserted on.

## 1:1 Source Parity Layout

Test structure mirrors `src/dovo/` 1:1 under `tests/`. Fixed mappings:
- `src/dovo/common/<m>.py` -> `tests/common/test_<m>.py`
- `src/dovo/core/<domain>/<m>.py` -> `tests/core/<domain>/test_<m>.py`
- `src/dovo/cli/ui/formatters/<domain>/<name>.py` -> `tests/cli/ui/formatters/<domain>/test_<name>.py`
- `src/dovo/cli/<command>/commands/<action>.py` -> `tests/cli/<command>/test_<command>_<action>.py`
  (one subdirectory per CLI command domain, `commands/` collapses, one file per action)

Rules: every source module has a test file, no exemption list; every test directory gets an
`__init__.py`; one test file per source module unless a stated architectural rule says otherwise;
`tests/core/**` never imports `dovo.cli.*`; no part-numbered or grab-bag files.

## Naming Conventions

- Test classes: `*Tests` (e.g. `ConfigLoaderTests`). Standalone `test_*` functions when a class
  buys no fixture reuse.
- Test methods: `test_<condition>_<outcome>`. The node id alone must convey the behavior.
- Banned (no outcome): `test_ok`, `test_success`, `test_present`, `test_missing`, `test_blank`,
  `test_basic`, `test_default`, `test_works`, `test_timeout`, `test_no_op`, `test_help`. A
  `should` prefix is filler, not an outcome.
- Docstrings are optional and must not restate the name; write one only for a constraint the
  identifier can't carry.

## The Four Execution Tiers

Different subject, different mocking policy, different assertion style. Do not mix them in one test.

### Tier 1 - Domain Behavior (Most Tests)
Subject: services and domain entrypoints under `core/`. Assert on returned `BaseResult` objects and real
side effects (files, git refs, DB rows). No mocks except genuine process/network boundaries; use
real `tmp_path`, SQLite, `GitWorkspaceHarness`.

### Tier 2 - Presentation Contracts (Three Tests Per Formatter, Never One)
Every formatter under `src/dovo/cli/ui/formatters/<domain>/` gets three tests in
`tests/cli/ui/formatters/<domain>/test_<name>.py`, built on `tests.harness.formatter.FormatterCase`:
1. **Transform:** `transform(model) == ExpectedView(...)`.
2. **JSON wire:** `to_json_serializable(model)` as an exact literal dict, pinned at
   fully-populated and empty/sparse states. Never `== transform(model).model_dump(...)`.
3. **Rich render:** pinned width, assert only values sourced from the view model via `case.view`.
   Never assert a label, border, glyph, padding, or full sentence.

No subclass overrides `to_json_serializable` (enforced by `tests/lint/test_formatter_contracts.py`).

### Tier 3 - CLI Wiring (Runner Required, Root Tests When Earned)
Every command action gets a real `*CliIntegrationTests` suite (`runner.invoke(app, [...])`)
covering happy path exit 0, option and argument binding for every declared option or flag,
failure path non-zero exit, `--format json` against wire schema, and any interactive confirm/abort branch.

A `*RootTests` suite (direct handler call with `CliContext`, bypassing the runner) is added only
when the handler owns logic the domain layer doesn't: input coercion, branch selection across
services, result composition from more than one call, or an interactive abort path. A
pass-through handler needs zero root tests, and a root test never restates a `tests/core/`
contract for the same result type.

A `*CliIntegrationTests` suite may pin the command's own result DTO via a dispatch spy that
monkeypatches `ui_dispatcher.dispatch` while still calling through to the real dispatcher
(TEST-008 still applies), scoped to the command's own terminal `BaseResult` only — never another
dispatched object (`MessageEvent`, `WarningEvent`, `PromptEvent`, progress/lifecycle events). See
TEST-019.

### Tier 4 - Invariants (`tests/lint/`)
Static AST analysis and architectural boundary enforcement: layer isolation, output routing
(no direct `print`/`echo` outside the dispatcher), `*Result` hierarchy, remediation capitalization,
`dovo --help` vs `README.md` parity.

## Test Harness and Assertion Helpers

- **Fluent builders** (`tests/harness/builders/`): construct domain objects with sensible
  defaults and chained mutations, never raw dicts or monkeypatched internal state.
- **`assert_exact_json(actual, expected_dict)`**: byte/key-exact wire format, no ignored extras.
- Legacy helpers (`tests/helpers/legacy.py`, `make.py`, old `git_fs`/`fs` wrappers) are obsolete;
  do not reference or extend them.

### Determinism & Process Isolation Strategy
No hardcoded sleeps: inject `VirtualClock` or monkeypatch `time.monotonic`/`asyncio.sleep`.
Subprocess tests register handles with `process_registry`; teardown sends `SIGKILL` to spawned
process groups. Secrets/env-dependent tests use `monkeypatch` to isolate environment variables.

### Rich Render Assertions
`render_rich(renderable, width=160)` via a real `Console` is the only supported capture method.
Width is pinned at 160 everywhere (`pyproject.toml`, the quality and tests blueprints, CI) via `COLUMNS=160`; tests must
not rely on ambient terminal size or mutate `os.environ["COLUMNS"]`.

### Fixtures and Scope
Shared in `tests/conftest.py`: `isolated_workspace(tmp_path)`, `git_repo(tmp_path)`,
`cli_runner()` (`NO_COLOR=1`, `COLUMNS=160`). Domain-specific fixtures live in their own test
module, not `conftest.py`. Fixtures yield plain tuples/paths, not opaque wrappers. Baseline lives
in the fixture; edge/error conditions mutate the handle inline in the test body.

## Parameterization as Primary Approach

`@pytest.mark.parametrize` is the default for exercising one contract across varying conditions —
never a `for` loop over scenarios. Parameterize input/boundary matrices, error/code permutations,
type polymorphism, and CLI option matrices; split into separate `def test_*` when a case needs a
divergent fixture, the lifecycle/workflow differs, or (Tier 2) the transform/JSON/Rich trio.
Always `pytest.param(..., id="descriptive_case_id")`, no broad `Any` in signatures, and no
`if/else` branching on divergent assertion contracts inside one parameterized test — split instead.

## Core Testing Rules

- Parameterize sibling variations; separate tests for distinct behaviors. Never a `for` loop,
  stacked assertions, or copy-pasted sibling functions differing only by inputs.
- Assert domain invariants and behavioral contracts: prefer targeted assertions on the relevant
  fields for scenario tests, and reserve full schema / default verification for dedicated contract tests.
  When using whole-object comparisons (`assert result == Expected(...)`), allow
  schema defaults to populate incidental envelope fields.
- No test seams in production code; monkeypatch collaborators at module boundaries instead.
- A seam is not tested until a test proves a real caller uses it from the production path.
- Test doubles must be types production actually passes, or a Protocol production is typed against.
- No test may be the sole consumer of a production symbol — if deleting the test makes the code
  unreachable, delete both.
- Never write negative existence tests for deleted symbols (`assert not hasattr(mod, "old_fn")`).
- No reaching into private state: no `obj._attr`, no importing underscore-prefixed symbols.
- Machine-readable output byte-exact; human-readable output by data presence — never scraped for
  exactness, never substring-matched when it needs to be exact.
- The name and docstring are part of the assertion — a reviewer must be able to point at the line
  that proves the claim.
- `is not None` is not a complete assertion; `isinstance` only when it distinguishes two real code paths.
- Annotate test helpers as tightly as production; `Any` rules in `code-conventions.md` apply unchanged.
- Never `# pyright: ignore` a test-tree error — fix the fixture. Exception: `pytest.raises(TypeError)`.
- Never assert help text wording — assert Click metadata instead. Exception: the `dovo --help` vs
  `README.md` check in `tests/lint/`.
- Cover every branch of a factory, dispatcher, or `elif` chain.

## Running Tests and Coverage Gates

```bash
uv run dovo run tests                                # full suite, parallel
uv run dovo run tests --coverage                     # coverage report (no floor enforced)
uv run dovo run tests --fast-fail                    # stop on first failure
uv run dovo run tests --path tests/core/             # scope to a file or directory subtree
uv run dovo run quality                              # full gate: adds format, lint, typecheck, docs, complexity, coverage floor
```

`dovo run tests` is for iteration. The coverage floor is enforced only by `dovo run quality`.

Global coverage floor: >= 80% (`fail_under = 80`), branch coverage enabled. Coverage is a
regression backstop, not a target — don't add tests to raise the percentage. A coverage drop from
deleting dead code is a success.

## PR Review Hygiene and Testing Documentation Review Rule

Every PR touching tests is checked against: 1:1 parity, execution tiers (CLI coverage per action,
root tests only where earned, formatter trio), naming, contract-only assertions, and harness
hygiene (standard fixtures, shared matchers, no legacy helpers).
