# Planning Before Implementation

Read this **before writing any code** for a GitHub issue. The plan document (`.agentic/plan.md`) is the deliverable.
While planning, remain **read-only**: do not edit `src/` or `tests/`, do not run tests or quality gates, and do not commit or push.

## Step 1: Extract the contract from the issue

```bash
gh issue view <number> --json number,title,body
```

- Copy every `FR-*`/`NFR-*` verbatim with its ID, plus **Pre-determined data**, **Out of scope**, and **Definition of Done** verbatim.
- Do not open or consult sibling issues.
- Plan zero backwards-compatibility shims, aliases, or dual code paths unless the issue explicitly demands them (R10).
- If a detail is unspecified, choose the option matching the nearest existing pattern from Step 2, record the choice and rejected alternative in `### Decisions`, and append `🚨` with a matching numbered entry in `## Open Questions`.

## Step 2: Ground yourself in the current code

1. Read [AGENTS.md](../../AGENTS.md) (rules R1–R17 and exemplars).
2. Read the existing code for every domain touched and its mirrored tests.
3. Name the closest existing implementation to mirror with `file:line` citations (signature + docstring only).
4. Record findings as a ground-truth table (`Surface`, `Location`, `What exists`) in `.agentic/evidence.md`. Keep **Traps** (dead code, lookalike symbols, stale docs) in `.agentic/plan.md` under `## Traps`.

## Step 3: Enumerate every artifact and split into phases

Write the file-by-file manifest (`Artifact | Kind | Path | New or changed | Requirement`) to `.agentic/evidence.md`.
Group FRs into phases of one concern each (~400 changed lines or less, tests included):
- Each phase passes `uv run dovo run quality` on its own and leaves no dead code.
- Replacing an internal is phased as add, switch callers, delete, with the deletion phase planned up front in `## Deletion ledger` (R10).
- Order phases by dependency, lower layers first (R1).

## Step 4: Write `.agentic/plan.md` and `.agentic/evidence.md`

- **Literal contracts:** Write exact code for model/enum definitions, full signatures with type hints and docstrings, Typer flags, formatter views, literal JSON dicts, and error strings.
- **Production stubs:** Exact signature + one-line intent docstring + `raise NotImplementedError`.
- **Test stubs:** Signature + single-line imperative docstring stating the exact outcome contract + `raise NotImplementedError` (use `/dovo-test-planner --plan`).
- Keep every planned function under cognitive complexity $\le 10$.

### Plan document template (`.agentic/plan.md`)

````markdown
# Issue #<n>: <title>

Planning only. Grounded against `<base branch>` at `<short sha>`.

## Open Questions

> **Open Question 1**: <one-line question>

<2-3 sentences: why it matters, what the plan assumes, what confirming or rejecting it changes.>

## GWT Scenarios

1. **GIVEN** <condition>,\
   **WHEN** <action>,\
   **THEN** <result>.

## Contract

<Verbatim FR-*/NFR-* list with IDs, Out of scope, Pre-determined data, and Definition of Done.>

---

## Phases

| Phase | FRs | Ships | Deletes | Est. lines |
|---|---|---|---|---|
| 1 | FR-1, FR-2 | <what a reviewer can check after this PR> | <ledger entries, or "nothing"> | <n> |

---

## Traps

Explicitly not touched by this issue:
- <dead code, lookalike symbol, stale doc>

---

## Ground truth & Artifact inventory

See [`.agentic/evidence.md`](evidence.md).

## Deletion ledger

<Deletion table or 'Deletes nothing: greenfield.'>

---

## FR-<n>: <name> (phase <p>)

> <verbatim requirement text>

### Instructions

1. <imperative action: exact file, exact symbol, exact change>

### Code

<literal contracts; production stubs ending with raise NotImplementedError>

### Decisions

- **<decision point>:** <choice>, because <one clause>. Rejected: <alternative>. [🚨]

### Edge cases

- <genuine implementation gotcha not in Traps or Decisions>

### Tests

```python
class <TestClass>:
    def <test_method>(self, ...):
        """<Imperative verb> <exact outcome contract>."""
        raise NotImplementedError
```

---

## Cross-cutting

- **Docs gates that fire:** <specific docs per `docs/agents/documentation.md`, or "none">
- **Validation:** `uv run dovo run tests --path <tests>`, then `uv run dovo run quality`
````

## Step 5: Save and hand off

1. Verify all planned paths, imports, stubs, and phases against R1–R17 in [AGENTS.md](../../AGENTS.md).
2. Write `.agentic/plan.md` and `.agentic/evidence.md` (deleting any stale `.agentic/review*`).
3. Report the plan path, the Phases table, and every numbered Open Question.
