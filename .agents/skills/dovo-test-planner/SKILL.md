---
name: dovo-test-planner
description: >-
  Given a source module (or --plan to read from an in-progress plan), produce a skeleton of test class(es) and test method stubs
  covering public observable contracts, interaction and ordering contracts, and conforming
  to this repo's tests/docs/RULES.md (TEST-001 through TEST-018). Invoked as /dovo-test-planner <source-module> or
  /dovo-test-planner --plan [<path>]. Use when planning, drafting, or structuring tests for a module or plan.
---

# dovo-test-planner

## Purpose

Given a source module or an in-progress plan, produce a skeleton of test class(es) and test method stubs that:

- Cover every **public** symbol's observable contracts.
- Surface private-function behaviour through their public callers only.
- Produce a **balanced mix** of unit tests (fast, isolated) and integration tests (real FS,
  real subprocesses, real DB) appropriate to the module under test.
- Capture **interaction and ordering contracts** between collaborators, not just branch-by-branch
  outcomes.
- Flag every test that is parameterisable and prefer `@pytest.mark.parametrize` for sibling
  variations.
- Conform to this repo's `tests/docs/RULES.md` (TEST-001 through TEST-018).

---

## Invocation and Modes

The skill supports two data sources:

- **Existing-code mode** (default, `--plan` omitted):
  - **Invocation**: `/dovo-test-planner <source-module>` (e.g. `/dovo-test-planner src/dovo/engine/engine.py`).
  - **Data source**: A Python source file under `src/`.
  - **Audit**: Grep source code and audit existing test suites under `tests/`.
- **Planning mode** (`--plan` provided):
  - **Invocation**: `/dovo-test-planner --plan [<path>]` (defaults to `.agentic/plan.md`).
  - **Data source**: An in-progress plan, specifically `## Ground truth`, `## Artifact inventory`, and `## FR-<n>`.
  - **Output**: Generates the `### Tests` section (summary table + contract-bearing stubs) formatted to fold directly into the plan.

---

## Step 1 — Read the governing rules and data source

Read before drafting:

1. `tests/docs/RULES.md` (TEST-*, TYPE-*, ENCAP-*, etc.).
2. **Data source**:
   - **Existing-code mode**: The source module and returned models/DTOs (follow imports for `BaseResult` subtypes).
   - **Planning mode (`--plan`)**: The plan document (`## Contract`, `## Ground truth`, `## Artifact inventory`, and relevant `## FR-<n>`).

---

## Step 1b — Trace reachable status values

For each service or entrypoint method delegated to, audit reachable status values:

- **Existing-code mode**: For each `BaseResult` subtype returned, list enum values, grep the call chain for assignments, and mark any unassigned value as `DEAD`.
- **Planning mode (`--plan`)**: Audit the plan's `## Ground truth` and `Traps (explicitly not touched)` to confirm reachable vs. dead status paths.

> **Rule**: Do not draft tests for `DEAD` status values.

Record the audit before proceeding:

```
Status value                      | Assigned in service? | Notes
----------------------------------|----------------------|------------------------------
WorktreeListStatus.OK              | ✅ list.py:40        | unconditional
WorktreeListStatus.NOT_INITIALIZED | ❌ never             | declared only — dead path
```

---

## Step 1c — Read the Typer binding

Find the CLI command binding wrapping the handler:
- **Existing-code mode**: The `@<app>.command(...)` function in `app.py`.
- **Planning mode (`--plan`)**: The planned Typer app/command binding in `## Ground truth` and `## Artifact inventory`.

Record:
- Coercions before the handler is called (enum parsing, argument casting, default fallbacks).
- Exit behavior (`if not result.ok: raise typer.Exit(code=1)`).
- Options and arguments handled exclusively by Typer.

> **Rule**: Per TEST-004, ensure every declared Typer option, flag, and argument is exercised in at least one CLI invocation test to pin CLI option binding and wire propagation. Do not duplicate underlying domain matrix variations through the CLI runner.

---

## Step 1d — Check existing tests

Check what existing tests already cover:
- **Existing-code mode**: Search `tests/core/<domain>/` for tests asserting the same result fields.
- **Planning mode (`--plan`)**: Check citations and test coverage in `## Ground truth`.

Assert wiring at the CLI tier (TEST-004 exit codes, TEST-005 JSON wire schema dict, TEST-012 / TEST-017 rendered tokens). Do not re-assert domain service fields in CLI tests.

---

## Step 2 — Identify the public surface

- **Existing-code mode**: List every public symbol (symbols not starting with `_`).
- **Planning mode (`--plan`)**: Extract planned public symbols from `## Artifact inventory` and `### Code`.

> **Rule**: Map each test class 1:1 to one public symbol. Private functions never get their own test class.

---

## Step 3 — Map private helpers to their public callers

Map each private function to the public function(s) calling it:

```
private → public caller(s)
_parse_timestamp   → is_run_stale
_is_pid_reused     → is_run_stale
_reconcile_records → reconcile_stale_runs
```

Cover private functions by varying inputs to their public callers.

---

## Step 4 — Classify the module under test

Select test tiers appropriate to the module:

| Module characteristic | Tier(s) to include |
|---|---|
| Pure functions with no I/O (parsers, math, string formatting) | Unit |
| Functions reading/writing filesystem or DB | Unit + Integration |
| Subprocess execution or step-execution loops | Integration primary, Unit for edge cases |
| Multi-collaborator orchestration | Integration for workflows, Unit for error injection |

Plan a separate test class per tier/scenario group with a descriptive suffix (e.g. `RunStepsExecutionTests`, `RunStepsFailurePolicyTests`, `RunStepsRobustnessTests`). Do not use monolithic classes across tier boundaries.

---

## Step 5 — Identify interaction and ordering contracts

Scan for sequences where collaborators must execute in a specific order:
1. **Ordering**: Does function A produce side-effects that function B must observe? (e.g. checkpoint saved before prompter call).
2. **Lifecycle events**: Does a function emit ordered callbacks? (`on_worktree_ready → on_step_start → on_step_done → on_worktree_cleanup`).
3. **Serial vs concurrent**: Does execution require strict sequential ordering?
4. **Error propagation**: When an inner collaborator raises, does the outer function swallow, wrap, or re-raise?
5. **Partial failure state**: When step N fails, is step N+1 attempted, and does accumulated state remain intact?

Draft dedicated test methods naming the interaction:

```python
def test_checkpoint_persisted_before_prompter_is_consulted(self): ...
def test_observer_receives_lifecycle_callbacks_in_order(self): ...
def test_steps_execute_strictly_serially_never_concurrently(self): ...
```

---

## Step 6 — Draft test classes and methods

### Naming (TEST-003)

Follow `test_<condition>_<outcome>`.
Banned names: `test_ok`, `test_success`, `test_basic`, `test_default`, `test_works`, `test_missing`, `test_blank`, `test_present`, `test_timeout`, `test_no_op`, `test_help`. No `should_` prefix.

### Contract Docstring (CODE-003)

Give a method a one-line docstring only when it states a fact the test name does not: an exact literal, an ordering or interaction contract, or the inputs behind a parametrized case. Omit the docstring when the name and the assertion already say it. The execution tier follows from the test file path (`docs/agents/testing.md#the-four-execution-tiers`), so never write it into the docstring.

- Open with an imperative verb ("Return", "Raise", "Exit 1 with", "Emit") that states the outcome.
- State the exact contract outcome (exit codes, status enums, return values, wire dicts) and the condition behind it.
- Do not prefix the public symbol or command: the class and test name carry it.
- Keep each line within 120 columns, indentation included.

```python
def test_checkpoint_persisted_before_prompter_is_consulted(self):
    """Save the checkpoint before failure_prompter.prompt_step_failure is called."""
    raise NotImplementedError


def test_dead_pid_returns_true(self):
    """Treat a dead pid as stale, skipping the reuse check when the pid is not alive."""
    raise NotImplementedError
```

Merge or remove any draft test whose docstring only names private functions.

### Integration tests and test doubles

Run actual shell commands or real filesystem operations for integration tests. Inject test doubles only at protocol boundaries accepted as parameters (e.g. `FailurePrompter`, `RunObserver`). Do not monkeypatch internal functions of the module under test.

For destructive or unreachable error paths (e.g. atomic write `OSError`), patch at the module import boundary and note it in the test docstring.

### Assertion contracts (TEST-001, TEST-007)

- `BaseResult`/`BaseModel`: assert relevant domain fields and invariants; reserve whole-schema pinning for dedicated model contract tests.
- Scalar values: assert exact return values.
- Event sequences: assert the full ordered list.
- Ordering: assert filesystem or state artifacts proving sequence.
- Never assert call counts, private attributes, or rendered console layout.

### Parameterisation (TEST-006)

Collapse sibling tests sharing the same function under test and assertion contract into `@pytest.mark.parametrize` with explicit `pytest.param(..., id="...")` labels.

```python
@pytest.mark.parametrize(
    ("ctx_kwargs", "warning_substr"),
    [
        pytest.param({"no_tty": True}, "non-interactive", id="no_tty"),
        pytest.param({}, "no failure prompter", id="no_prompter"),
    ],
)
def test_prompt_user_skips_prompt_and_aborts_when_non_interactive(self, ctx_kwargs, warning_substr):
    """Skip the prompt and abort with a warning when the context is non-interactive."""
    raise NotImplementedError
```

---

## Step 7 — Verify compliance checklist

Verify before presenting output:

| Check | Rule |
|---|---|
| Every test class maps 1:1 to a public symbol | TEST-002, TEST-003 |
| No test class named after a private function | TEST-001 |
| Every docstring is a single imperative line with the exact contract, and absent when the name already says it | PLAN-018, CODE-003 |
| Planning mode (`--plan`) pairs each test with a row in `### Tests` summary table | PLAN-018 |
| Private helpers verified through public caller tests | TEST-001 |
| Interaction/ordering tests included where Step 5 identified sequences | (this skill) |
| Test doubles injected at protocol boundaries only | ENCAP-002 |
| Sibling contract variations parameterised | TEST-006 |
| Method names follow `test_<condition>_<outcome>` with no banned names | TEST-003 |
| No assertions on call counts, private state, or console layout | TEST-001 |
| Model assertions check domain invariants; dedicated contract tests pin schema | TEST-007 |
| Test file path mirrors source under `tests/` | TEST-002 |

---

## Output format

### Planning mode (`--plan`)

In planning mode, emit the `### Tests` section containing the summary table and companion stubs carrying imperative contract docstrings, explicitly qualified with the target test file path:

````markdown
### Tests

#### Target file: `tests/engine/test_engine.py`

| Test | Outcome |
|---|---|
| `RunStepsExecutionTests::test_two_sequential_steps_return_completed_outcome_with_both_results` | steps succeed → COMPLETED, both results |
| `RunStepsExecutionTests::test_empty_step_list_returns_completed_outcome_with_no_results` | steps=[] → COMPLETED, empty results |
| `RunStepsExecutionTests::test_observer_receives_lifecycle_callbacks_in_order` | observer receives callbacks in order |

```python
# tests/engine/test_engine.py


class RunStepsExecutionTests:
    def test_two_sequential_steps_return_completed_outcome_with_both_results(self):
        """Return COMPLETED with both results and their captured stdout when two steps succeed."""
        raise NotImplementedError

    def test_empty_step_list_returns_completed_outcome_with_no_results(self):
        """Return COMPLETED with step_results=[], errors=[] and warnings=[] for steps=[]."""
        raise NotImplementedError

    def test_observer_receives_lifecycle_callbacks_in_order(self):
        """Notify worktree_ready, step_start, step_done, then worktree_cleanup, in that order."""
        raise NotImplementedError
```
````

### Existing-code mode

Present stubs as a fenced Python block explicitly qualified with the target test file path. Group methods into classes named after the public symbol. A stub carries a single-line imperative docstring only when it adds a fact the name lacks. Do not include import blocks or fixture bodies.

```python
# Target file: tests/engine/test_engine.py


class IsPidAliveTests:
    def test_zero_pid_returns_false(self):
        """Return False for pid <= 0 without calling os.kill."""
        raise NotImplementedError

    def test_dead_pid_returns_false(self):
        """Return False when os.kill raises ProcessLookupError."""
        raise NotImplementedError

    def test_alive_pid_returns_true(self):
        """Return True when os.kill(pid, 0) succeeds."""
        raise NotImplementedError


class RunStepsExecutionTests:
    def test_two_sequential_steps_return_completed_outcome_with_both_results(self):
        """Return COMPLETED with both results and their captured stdout when two steps succeed."""
        raise NotImplementedError

    def test_observer_receives_lifecycle_callbacks_in_order(self):
        """Notify worktree_ready, step_start, step_done, then worktree_cleanup, in that order."""
        raise NotImplementedError

    @pytest.mark.parametrize(
        ("decision", "expected_status"),
        [
            pytest.param("abort", "FAILED", id="abort"),
            pytest.param("continue", "COMPLETED", id="continue"),
            pytest.param("retry", "COMPLETED", id="retry-then-succeeds"),
        ],
    )
    def test_prompt_user_decision_maps_to_correct_outcome(self, decision, expected_status):
        """Map each FailurePromptDecision the prompter returns to the matching outcome status."""
        raise NotImplementedError
```
