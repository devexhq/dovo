# Planning Before Implementation

Read this **before writing any code** for a GitHub issue. The plan document (`.agentic/plan.md`) is the deliverable.
While planning, remain **read-only**: do not edit `src/` or `tests/`, do not run tests or quality gates, and do not commit or push.

A plan has two readers. The human approving it needs to see, at the top, what changes for existing users and what is still undecided. The agent implementing a phase needs exact instructions and literal contracts for that phase alone. Write for both, and repeat nothing the issue already says.

## Step 1: Extract the contract from the issue

```bash
gh issue view <number> --json number,title,body
```

- Link the issue; do not copy its prose. Every `FR-*`, `NFR-*`, numbered error case and Definition of Done item appears by ID in the `## Traceability` table instead, as does every GWT scenario (`GWT-<n>`).
- Copy **literal** contracts verbatim into the `### Code` block of the phase that implements them: pre-determined data, models, enums, flags, JSON, and exact error and output strings. The implementing agent does not read the issue.
- Turn each **Out of scope** item into a bullet under `## Traps`.
- Absent an issue, quote the request in the opening paragraph and number the requirements you derive from it (`FR-1`, …) in a `## Requirements` list after `## What you're approving`.
- Do not open or consult sibling or parent issues. A parent issue (one with sub-issues) is never planned; plan its sub-issues one at a time.
- Plan zero backwards-compatibility shims, aliases, or dual code paths unless the issue explicitly demands them (R10).
- If a detail is unspecified, choose the option matching the nearest existing pattern from Step 2, record the choice and rejected alternative in `### Decisions`, and add a numbered Open Question stating the assumed answer.

## Step 2: Ground yourself in the current code

1. Read [AGENTS.md](../../AGENTS.md) (rules R1–R18 and exemplars).
2. Ground against the current default branch and record its short sha and the date. Never ground against a branch whose base is not an ancestor of it.
3. Read every function the change touches **and its callers**, and the mirrored tests. Name the closest existing implementation to mirror with `file:line` citations (signature + docstring only).
4. Look for what the change alters or depends on beyond the issue's text, and record each finding in the plan:
   - existing behaviour that changes for current users, and existing bugs on the path the change builds on → `## What you're approving`
   - persisted formats (session state and manifests, `data.db`, `config.json`, catalog `index.json`, blueprint and step YAML) and whether data written before the change still loads → `## What you're approving`
   - exhaustive iterations over an enum or type list, field-by-field merge or override logic, lookalike symbols, stale docs → `## Traps`
5. When the citations behind the plan are too many to keep inline, put a ground-truth table (`Surface`, `Location`, `What exists`) in `.agentic/evidence.md` and link it from the plan's opening paragraph. Every link in the plan must resolve to a file about this issue.

## Step 3: Enumerate every artifact and split into phases

Write the file-by-file manifest into `## Artifact inventory` in the plan itself; a reviewer reads it to see what the change touches.
Group FRs into phases of one concern each (~400 changed lines or less, tests included):
- Each phase passes `uv run dovo run quality` on its own, ships something and leaves no dead code.
- Replacing an internal is phased as add, switch callers, delete, with the deletion phase planned up front in `### Deletion ledger` (R10).
- Order phases by dependency, lower layers first (R1). A fix to existing behaviour that the change depends on is its own phase, first.
- A phase over budget states, under the Phases table, why it cannot be split.
- `/dovo-code` lands every phase in one PR, so the phases together serve the issue's one responsibility. A plan whose phases mix kinds of change (a foundation and its adoption, or a behaviour change and a refactor) says so first under **What you're approving** and recommends splitting the issue with `/dovo-issue`.
- Resolve every check now: no instruction or ledger entry may be conditional on a `grep` the implementer runs later.

## Step 4: Write `.agentic/plan.md`

- **Literal contracts:** Write exact code for model/enum definitions, full signatures with type hints and docstrings, Typer flags, formatter views, literal JSON dicts, and error strings.
- **Production stubs:** Exact signature + one-line intent docstring + `raise NotImplementedError`.
- **Test stubs:** Signature + single-line imperative docstring stating the exact outcome contract + `raise NotImplementedError` (use `/dovo-test-planner --plan`). Plan sibling cases as one parametrized test.
- **Commands:** every command a phase adds or changes gets an R13 matrix (success, failure, `--format json`, each option) in that phase's `### Tests`.
- **Numbers:** a line estimate or total appears once, or agrees everywhere it appears.
- Keep every planned function under cognitive complexity $\le 10$.

### Plan document template (`.agentic/plan.md`)

````markdown
# Issue #<n>: <title>

Planning only. Grounded against `<default branch>` at `<short sha>` (<YYYY-MM-DD>).

The issue's contract is not repeated here; read it on #<n>. This plan covers how each requirement lands, in which phase, and how it is tested. [Traceability](#traceability) maps every requirement to its phase and test. [<Citations: `.agentic/evidence.md`>]

## What you're approving

**Open questions:**

> **OQ-1: <one-line question>**
> Assumed: **<answer>.** <1-2 sentences: why, and what the other answer changes.> Reply "<word>" to change.

<Or "None.">

**Changes to existing behaviour:**

1. **<what changes for whom> (phase <p>).** <What happens today, with `path:line`, and what happens after.>

<Or "None: new surface only.">

**Unchanged:** <migrations, `config.json` keys, the JSON event envelope, CLI flags, as applicable>.

---

## Traps

- **<trap>** (`path:line`). <What goes wrong, and what to do instead.>
- **Out of scope** (from the issue): <items>.

## GWT Scenarios

1. **GIVEN** <condition>,\
   **WHEN** <action>,\
   **THEN** <result>.

---

## Phases

| Phase | Ships | FRs | Depends on | Est. changed lines | Risk, and where to look |
|---|---|---|---|---|---|
| 1 | <what a reviewer can check after this phase> | FR-1, FR-2 | none | <n> | **Low/Med/High.** <what to check> |

<Why an over-budget phase cannot be split; which phases are independent.>

### Deletion ledger

<Table of `Phase | Deletes`, or "Deletes nothing: greenfield.">

---

## Traceability

| Requirement | Phase | Test (class → method) |
|---|---|---|
| FR-1 <short name> | 1 | `<TestClass>.<test_method>` |
| Error 1 <short name> | 2 | `<test_file>::<test_method>` |
| GWT-1 <short name> | 1 | `<TestClass>.<test_method>` |

---

## Artifact inventory

| Artifact | Path | New or changed | Phase |
|---|---|---|---|
| `<symbol>` | `<path:line>` | new / changed | <p> |

Not needed: <surfaces checked and left alone, e.g. migration, result model, config key>.

---

## Phase <p>: <name>

### Instructions

1. <imperative action: exact file, exact symbol, exact change>

### Code

<literal contracts; production stubs ending with raise NotImplementedError>

### Decisions

- **<decision point>:** <choice>, because <one clause>. Rejected: <alternative>. [See OQ-<n>.]

### Edge cases

- <genuine implementation gotcha not in Traps or Decisions>

### Tests

```python
class <TestClass>:  # <test file path>
    def <test_method>(self, ...):
        """<Imperative verb> <exact outcome contract>."""
        raise NotImplementedError
```

<For commands: the R13 matrix.>

| Command | Success | Failure | `--format json` | Options |
|---|---|---|---|---|

---

## Cross-cutting

- **Docs gates that fire:** <specific docs per `docs/agents/documentation.md`, and the phase each lands in, or "none">
- **Validation:** `uv run dovo run tests --path <tests>`, then `uv run dovo run quality`
````

## Step 5: Save and hand off

1. Verify all planned paths, imports, stubs, and phases against R1–R18 in [AGENTS.md](../../AGENTS.md), and that every requirement ID and GWT scenario appears in `## Traceability`.
2. Write `.agentic/plan.md` (and `.agentic/evidence.md` when used) and its open questions to `.agentic/plan.json` (schema in the `dovo-plan` skill), deleting any stale `.agentic/review*` and `.agentic/plan-review*`.
3. Report the plan path, the Phases table, and every numbered Open Question.
