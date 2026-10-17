# Testing

Testing conventions, taxonomy, and execution patterns for Dovo CLI. Narrative companion to
rules R12-R16 in the root [AGENTS.md](../../AGENTS.md); where the two disagree, AGENTS.md wins.

## The rule that matters most

**Test at contracts, not at implementation.** A contract is a boundary chosen deliberately: a
`BaseResult` object, a JSON payload, an exit code, a file on disk, a git ref. Everything else
(rendered layout, private helpers, call ordering, constructor assignments) is implementation and
must not be asserted on.

## 1:1 Source Parity Layout

Test structure mirrors `src/dovo/` 1:1 under `tests/`. Fixed mappings:
- `src/dovo/common/<m>.py` -> `tests/common/test_<m>.py`
- `src/dovo/core/<domain>/<m>.py` -> `tests/core/<domain>/test_<m>.py`
- `src/dovo/engine/<m>.py` -> `tests/engine/test_<m>.py` (the `executors/` and `services/`
  subpackages mirror as `tests/engine/executors/` and `tests/engine/services/`)
- `src/dovo/cli/ui/formatters/<domain>/<name>.py` -> `tests/cli/ui/formatters/<domain>/test_<name>.py`
- `src/dovo/cli/<command>/commands/<action>.py` -> `tests/cli/<command>/test_<command>_<action>.py`
  (one subdirectory per CLI command domain, `commands/` collapses, one file per action)

Rules: every source module has a test file, no exemption list; every test directory gets an
`__init__.py`; one test file per source module unless a stated architectural rule says otherwise or the module's
tests split into files named for the behavior (`test_step_executor_<behavior>.py`);
`tests/core/**` never imports `dovo.cli.*`; no part-numbered or grab-bag files. A per-domain
`conftest.py` holds only fixtures that its own domain's test files share, never one another domain
needs.

## Naming Conventions

- Test classes: `*Tests` (e.g. `ConfigLoaderTests`). Standalone `test_*` functions when a class
  buys no fixture reuse.
- Test methods: `test_<condition>_<outcome>`. The node id alone must convey the behavior.
- Banned (no outcome): `test_ok`, `test_success`, `test_present`, `test_missing`, `test_blank`,
  `test_basic`, `test_default`, `test_works`, `test_timeout`, `test_no_op`, `test_help`. A
  `should` prefix is filler, not an outcome.
- Docstrings are optional and must not restate the name; write one only for a constraint the
  identifier can't carry. It is one imperative sentence with no `[tier-N/type]` tag and no
  `<symbol>:` prefix, within 120 columns. A docstring on a helper, fixture, double,
  class, or module is a one-line title of what it is or does, plus a description only for a hidden
  contract.

## The Four Execution Tiers

Different subject, different mocking policy, different assertion style. Do not mix them in one test.

### Tier 1 - Domain Behavior (Most Tests)
Subject: services and domain entrypoints under `core/`. Assert on returned `BaseResult` objects and real
side effects (files, git refs, DB rows). No mocks except genuine process/network boundaries; use
real `tmp_path`, SQLite, `GitWorkspaceHarness`.

### Tier 2 - Presentation Contracts (Three Tests Per Formatter, Never One)
Every formatter under `src/dovo/cli/ui/formatters/<domain>/` gets three tests in
`tests/cli/ui/formatters/<domain>/test_<name>.py`, built on `tests.harness.formatter.FormatterCase`
(an identity formatter with no derived view model omits the Transform test and keeps the other two;
never add an alias like `SomeView = SomeEvent` to manufacture a third):
1. **Transform:** `transform(model) == ExpectedView(...)`.
2. **JSON wire:** `to_json_serializable(model)` as an exact literal dict, pinned at
   fully-populated and empty/sparse states. Never `== transform(model).model_dump(...)`.
3. **Rich render:** pinned width, assert only values sourced from the view model via `case.view`.
   Never assert a label, border, glyph, padding, or full sentence.

No subclass overrides `to_json_serializable`.

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
(R15 still applies), scoped to the command's own terminal `BaseResult` only — never another
dispatched object (`MessageEvent`, `WarningEvent`, `PromptEvent`, progress/lifecycle events).

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
No hardcoded sleeps in a test body: monkeypatch the clock at the module boundary
(`time.monotonic`, `asyncio.sleep`) and assert the resulting schedule, not only that a retry
happened. Inject the clock and id generators wherever their output reaches a result field, so
tests assert literal timestamps and ids. A wait on an OS event with no virtual equivalent (a
process group reaped after `SIGKILL`) goes through one shared harness poll helper,
`wait_until` in `tests/harness/poll.py`, that owns the interval and deadline; the only sleep
allowed is inside that helper (R16).
Subprocess tests register handles with `process_registry`; teardown sends `SIGKILL` to spawned
process groups. Secrets/env-dependent tests use `monkeypatch` to isolate environment variables.

### Rich Render Assertions
`render_rich(renderable, width=160)` via a real `Console` is the only supported capture method.
Width is pinned at 160 everywhere (`pyproject.toml`, the quality and tests blueprints, CI) via `COLUMNS=160`; tests must
not rely on ambient terminal size or mutate `os.environ["COLUMNS"]`.

### Fixtures and Scope
Shared in `tests/conftest.py`: `isolated_workspace(tmp_path)`, `git_repo(tmp_path)`,
`cli_runner()` (`NO_COLOR=1`, `COLUMNS=160`), and `artifacts_repository`, `artifact_store` and
`publish_artifact` over `artifacts_paths` (an area's `conftest.py` overrides `artifacts_paths` to
bind them to its own workspace). `tests/cli/conftest.py` adds `cli_workspace` (git, config,
database, catalog) and `workspace_config`. A fixture used by several modules lives in the nearest
common `conftest.py`; one used by a single module stays in that module. Shared doubles and
builders live in `tests/harness`. Fixtures yield plain tuples/paths, not opaque wrappers. Baseline
lives in the fixture; edge/error conditions mutate the handle inline in the test body.

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
  Never build a double's interface as the union of attributes from every branch of a `hasattr`
  chain (R15).
- No test may be the sole consumer of a production symbol — if deleting the test makes the code
  unreachable, delete both. The same holds for harness symbols (builder method, fixture,
  assertion helper): each needs a caller outside `tests/harness/`.
- Every public function or method (no leading underscore) in a changed production module gets at
  least one success test and one failure/boundary test in its mirrored test file; private helpers
  are covered only through their public callers.
- A modified command routing or multi-step orchestration path gets a test that exercises the
  updated branch.
- Never write negative existence tests for deleted symbols (`assert not hasattr(mod, "old_fn")`).
- No reaching into private state: no `obj._attr`, no importing underscore-prefixed symbols.
- Machine-readable output byte-exact; human-readable output by data presence — never scraped for
  exactness, never substring-matched when it needs to be exact. The exception is a CLI runner test
  under `tests/cli/`, which may assert a literal status line of the command's own output.
- The name and docstring are part of the assertion — a reviewer must be able to point at the line
  that proves the claim.
- `is not None` is not a complete assertion; `isinstance` only when it distinguishes two real code paths.
- Annotate test helpers as tightly as production; `Any` rules in `code-conventions.md` apply unchanged.
- Never `# pyright: ignore` a test-tree error — fix the fixture. Exception: an intentional ill-typed
  input whose subject is a runtime `TypeError` or `ValidationError`; see
  [code-conventions.md](code-conventions.md#type-checker-suppressions).
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
