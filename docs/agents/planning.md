# Planning Before Implementation

Read this **before writing any code** for a GitHub issue. The plan document (`.agentic/plan.md`) is the deliverable.

While planning, remain **read-only**: do not edit `src/` or `tests/`, do not run `inv test`, do not commit, push, or touch PR state.

## When to plan

Plan when an issue adds or changes: a command or subcommand, a DTO/`*Result` model, a status enum, a domain exception, a service, a facade method, a formatter, a config key, a JSON/YAML schema field, or a `core/db/` table.

Skip planning only for a single-file, no-new-surface change (a typo, a message string, a one-line branch fix).

---

## Step 1: Extract the contract from the issue

```bash
gh issue view <number> --json number,title,body
```

Extract the contract directly from the issue body (see [github-issues.md](github-issues.md); PLAN-003 through PLAN-006):

- Copy every `FR-*`/`NFR-*` verbatim with its ID, plus **Pre-determined data**, **Out of scope**, and **Definition of Done** verbatim. No paraphrasing, no "Goal" or "As a user" prose.
- Do not open or consult sibling issues.
- Plan zero backwards-compatibility shims, aliases, dual code paths, or deprecation windows unless the issue explicitly demands them.

If a detail is unspecified, choose the option matching the nearest existing pattern from Step 2, record the choice and rejected alternative in `### Decisions`, and append `🚨`.

---

## Step 2: Ground yourself in the current code

Read the codebase directly before planning; never plan from memory (PLAN-007, PLAN-008):

1. Read the always-on docs listed in [AGENTS.md](../../AGENTS.md).
2. Read the existing code for every domain touched: `core/<domain>/{models,exceptions,facade}.py`, `services/`, `cli/<name>/app.py` and `commands/`, `cli/ui/formatters/<domain>/`, and mirrored tests.
3. **Name the closest existing implementation to mirror**, with `file:line` citations (e.g. `dovo config set` -> `Config.set` in `src/dovo/core/config/mutate.py` -> `ConfigSetResult` -> `config_set_command` -> `ConfigSetFormatter`). Quote a mirrored symbol as signature + docstring only, never its body.
4. **Verify doc field lists against source code**; record stale doc claims as traps.
5. If the issue's description of current state differs from the codebase, state the discrepancy and the corrected state.
6. **Name every trap**: dead code, lookalike symbols, duplicate implementations, stale docs. Mark each out of scope. (Use `/dovo-test-planner` Steps 1b-1d to audit dead status values and existing coverage.)

Record findings as a ground-truth table (`Surface`, `Location`, `What exists`), one clause per cell, `file:line` citations, no narrative. Write the ground-truth table and **Pattern to mirror** to `.agentic/evidence.md`, under a `## Ground truth` heading matching `plan.md`'s — they are citations an implementing agent consults on demand (to verify a claim, or when a cited symbol seems stale), not instructions it needs loaded up front, since each FR's own Instructions already embed the specific file:line detail it needs. **Traps** stay in `.agentic/plan.md` itself, as their own top-level `## Traps` section, not nested under Ground truth: traps are the one thing that actively prevents a bad implementation choice, so a human or implementing agent must see them without opening a second file. Do not repeat Ground truth, Pattern to mirror, or Traps content in Instructions, Decisions, or Edge cases.

---

## Step 3: Enumerate every artifact

Produce an inventory with one row per file touched (exact path, exact identifier; no "e.g." or "etc."):

| Artifact | Kind | Path | New or changed | Requirement |
|---|---|---|---|---|
| `SandboxPruneResult` | DTO | `src/dovo/core/sandbox/models.py` | new | FR-2 |

Write this table to `.agentic/evidence.md` under an `## Artifact inventory` heading, together with the **"none"** list below it — `plan.md`'s own `## Artifact inventory` heading holds only a one-line pointer to that file (see the Plan document template). State **"none"** for each kind the issue does not need. Path per kind:

- **DTO / Result / Outcome / Status enum** -> `core/<domain>/models.py`
- **Domain exception** -> `core/<domain>/exceptions.py`
- **Service** -> `core/<domain>/services/<verb>.py` (public models never live here)
- **Facade method** -> `core/<domain>/facade.py`
- **Command handler** -> `cli/<name>/commands/<action>.py`
- **Typer registration** -> `cli/<name>/app.py` (and `cli/cli.py` for new top-level groups)
- **Formatter** -> `cli/ui/formatters/<domain>/<name>.py`; view models in `<domain>_views.py` or `<name>_view.py`; register in `register_<domain>_formatters` and `__all__`
- **Config key** -> `core/config/models.py` + `schemas/v1/config.json` + defaults generator
- **JSON / YAML schema** -> `src/dovo/schemas/v1/*.json`
- **DB model or migration** -> `core/db/models.py` + Alembic migration
- **Tests** -> mirrored `tests/` path, tier named (see `docs/agents/testing.md#the-four-execution-tiers`)
- **Docs** -> only docs matching [docs/agents/documentation.md](documentation.md) gates (`docs/cli/`, `schemas.md`, `architecture.md`, `README.md`)

Decompose every planned function so it stays below cognitive complexity <= 10 (PLAN-012).

---

## Step 4: Write the plan

The document has two document-level sections that lead everything else, then per-FR sections.

### Open Questions and GWT Scenarios lead the document

Place these immediately after the intro line, before `## Contract`. They are the only sections a human reviewer is expected to read in full; everything from `## Contract` onward (including `## Traps`, which stays readable without a second file — see Step 2) is reference and build material primarily for the implementing agent.

**Open Questions** — one numbered entry per genuinely unresolved judgment call, not a restatement of every `### Decisions` bullet in the plan:
1. `> **Open Question N**: <one-line question>` as a blockquote.
2. 2-3 sentences of plain prose below the blockquote (outside it, not inside): why it matters, what the plan currently assumes, what confirming or rejecting it changes.
3. Every 🚨 elsewhere in the plan (PLAN-014) must have a matching numbered entry here — 🚨 on a `### Decisions` bullet marks *where* a judgment call was made; the Open Questions entry is *the* place a reviewer resolves it, not one of several places the same question is repeated.
4. A short, unnumbered **Resolved during review** line (no blockquote) records anything settled during the planning conversation itself — kept for traceability, explicitly marked as needing no further sign-off. Do not give a resolved item the full blockquote treatment; that visual weight is reserved for what still needs an answer.
5. If nothing is genuinely open, say so in one line rather than omitting the section.

**GWT Scenarios** — a numbered list translating the FR/NFR/Error cases into Given/When/Then form, roughly one scenario per FR, NFR, error case, and non-obvious edge case (not a mechanical one-per-sentence transform of the whole plan):
1. **GIVEN**, **WHEN**, **THEN** are bold and fully capitalized.
2. Each clause is its own line within the same numbered item: end each line but the last with a backslash for a markdown hard break, not trailing spaces — this repo's pre-commit whitespace-trimming hook strips trailing spaces, silently collapsing the clauses back onto one line. Indent continuation lines to match the marker width, so double-digit items still align.
3. State the same outcome the matching FR or test stub states, in the same terms — a GWT scenario and its test stub's docstring should read as the same fact in two forms, not two different claims.

### Per-FR subheadings

Structure each FR (or testable group of FRs) under these subheadings:
1. `> <verbatim requirement text>`
2. `### Instructions`
3. `### Code`
4. `### Decisions`
5. `### Edge cases`
6. `### Tests`

> [!TIP]
> Run `/dovo-test-planner --plan` to generate the `### Tests` stubs.

### Instructions: Imperative verbs only

Strictly imperative verbs ("Create `...`", "Assert `...`"). State what to do, not why — rationale goes in `### Decisions`. Do not repeat what code samples or test stubs already show.

### Code sample rules

**Literal contracts:** write exact code for anything that is a contract — model/enum definitions with every field and default, full signatures with type hints and docstrings, Typer flags and help text, formatter shells, literal JSON dicts, error/warning strings, fixtures, regex patterns (PLAN-010).

```python
class SandboxPruneStatus(StrEnum):
    """Outcome states for a sandbox prune operation."""

    OK = "ok"
    NOTHING_TO_PRUNE = "nothing_to_prune"
    FAILED = "failed"


class SandboxPruneResult(BaseResult):
    """Result of pruning stale sandboxes and orphaned directories."""

    model_config = {"extra": "forbid", "strict": True}

    status: SandboxPruneStatus
    pruned_items: list[str] = []
```

**Production stubs:** exact signature + one-line intent docstring + `raise NotImplementedError`. No numbered steps, no body code (PLAN-011).

```python
def prune_sandboxes(context: CliContext, dry_run: bool = False) -> SandboxPruneResult:
    """Delete stale sandbox records, orphaned directories, and temporary branches."""
    raise NotImplementedError
```

**Test stubs:** signature + single-line docstring starting `[<tier>/<type>]` (per `docs/agents/testing.md#the-four-execution-tiers`), naming the public symbol exercised and the exact outcome contract, ending `raise NotImplementedError` (PLAN-018). The docstring alone is the assertion contract — no separate index table (see Tests below).

```python
class SandboxPruneCliIntegrationTests:
    def test_prune_empty_returns_nothing_to_prune(self, cli_runner: CliRunner, sandbox_workspace: Path) -> None:
        """[tier-3/integration] dovo sandbox prune: empty sandboxes directory prints 'Nothing to prune'; exit 0."""
        raise NotImplementedError
```

Use absolute `dovo.*` imports at module top level and reference only symbols verified in Step 2.

### Decisions: The sole home for rationale

Format: `- **<decision point>:** <choice>, because <one clause>. Rejected: <alternative>. [🚨]`. Append `🚨` when a decision needs confirmation.

### Edge cases: Genuine implementation gotchas only

Non-obvious traps, execution gotchas, or sequencing constraints not already in Ground truth Traps or Decisions. Do not restate standard branch logic.

### Tests: contract stubs only, no index table

Just the stubs from Code sample rules — no separate index table alongside them. A stub's docstring already leads with `[tier-N/type]` and states the exact outcome contract in one line (PLAN-018); a `Test | Tier | Outcome` table next to it restates the same fact in a second place that can drift from the first when one is edited without the other. Assert exact contracts (literal JSON dict, exit code, filesystem state, or `*Result` fields) in the docstring. Ban vague phrasing (`"Verifies pruning works"`), piecewise assertions (`"Checks exit code is 0"`), and omitted fields.

### Plan document template

````markdown
# Issue #<n>: <title>

Planning only. Grounded against `<base branch>` at `<short sha>`.

## Open Questions

> **Open Question 1**: <one-line question>

<2-3 sentences: why it matters, what the plan assumes, what confirming or rejecting it changes.>

**Resolved during review** (kept for record, no sign-off needed): <short list of judgment calls already settled in conversation, if any>

## GWT Scenarios

1. **GIVEN** <condition>,\
   **WHEN** <action>,\
   **THEN** <result>.
2. ...

## Contract

<Verbatim FR-*/NFR-* list with IDs. Pre-determined data reproduced exactly.>

**Out of scope:**
- ...

**Pre-determined data:**
- ...

**Definition of Done:**
- [ ] ...

---

## Traps

Explicitly not touched by this issue:
- <dead code, lookalike symbol, stale doc>

---

## Ground truth

Read [`.agentic/evidence.md`](evidence.md) before implementing any FR below — it holds the file:line citations and mirrored-pattern references each FR's Instructions assume.

---

## Artifact inventory

Full file-by-file manifest, including what's explicitly not needed for this issue, in [`.agentic/evidence.md`](evidence.md).

## Deletion ledger

<Deletion table or 'Deletes nothing: greenfield.'>

---

## FR-<n>: <name>

> <verbatim requirement text>

### Instructions

1. <imperative action: exact file, exact symbol, exact change; no inline rationale>

### Code

<literal contracts, fixtures, constants; production stubs as signature + one-line intent docstring + raise NotImplementedError>

### Decisions

- **<decision point>:** <choice>, because <one clause>. Rejected: <alternative>. <🚨 if a reviewer should confirm this — and add a matching numbered entry to Open Questions.>

### Edge cases

- <genuine implementation gotcha not in Traps or Decisions>

### Tests

```python
class <TestClass>:
    def <test_method>(self, ...):
        """[<tier>/<type>] <public_symbol>: <exact outcome contract>."""
        raise NotImplementedError
```

---

## Cross-cutting

- **Docs gates that fire:** <specific docs, or "none" with the reason>
- **Validation:** `uv run inv test`, `uv run ruff format .`, `uv run ruff check .`, `uv run basedpyright src tests --level error`, `inv complexity --paths <files> --plain --failed`
- **Open questions:** See Open Questions at the top of this document.
````

### Evidence document template

Written alongside `plan.md` whenever Ground truth or the Artifact inventory is non-trivial. A short plan may skip it and inline both tables directly under their `plan.md` headings instead of a pointer — use judgment; don't create an near-empty second file for a one-row table.

````markdown
# Evidence: Issue #<n>

Supporting citations for `.agentic/plan.md`. Not build instructions — each FR's `### Instructions` already embeds the specific file:line detail it needs. Read this when you want to verify a claim the plan makes, not as a prerequisite for every FR.

## Ground truth

| Surface | Location | What exists |
|---|---|---|
| <surface> | `path:line` | <one clause> |

**Pattern to mirror:** <domain path chain, with citations; reproduced symbols shown as signature + docstring only>

## Artifact inventory

| Artifact | Kind | Path | New or changed | Requirement |
|---|---|---|---|---|

**Not needed for this issue:** <kinds from the Step 3 checklist that are "none">
````

---

## Step 5: Save and hand off

1. Verify the plan against `docs/agents/REVIEW_CHECKLIST.json` and `docs/agents/PLANNER_RULES.md` for every rule matching touched paths or domains, ensuring zero BLOCKER violations. Reread the plan's actual paths, imports, and stubs against each clause — do not just restate the rule. No compliance table in the plan.
2. Write the plan to `.agentic/plan.md` and, when used, the evidence document to `.agentic/evidence.md` (overwriting any previous versions of both and deleting `.agentic/review.md`). A stale `evidence.md` left next to a freshly rewritten `plan.md` is a trap of its own — never leave one behind.
3. Report the plan path and state that planning is complete (no code implemented, tested, committed, or pushed). Restate every numbered Open Questions entry in the handoff message — that list is now the single source for what needs sign-off; do not also enumerate every `🚨` `### Decisions` bullet separately.
