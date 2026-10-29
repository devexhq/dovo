---
name: dovo-plan-review
description: >-
  Review a Dovo CLI plan before any code is written: re-ground it against the
  current tree, hunt for existing behaviour and persisted formats it breaks or
  relies on, check that every part of every requirement maps to a phase and a test
  line, and check phasing and the AGENTS.md rules, then write findings to
  .agentic/plan-review.md and .agentic/plan-review.json. Invoked as
  /dovo-plan-review [<plan-path>].
disable-model-invocation: true
---

# dovo-plan-review

Review a plan written by `/dovo-plan`, in this order of importance:

1. **Grounding**: does the plan describe the code as it is today.
2. **Missed behaviour**: what the planned change breaks or depends on that the plan does not say.
3. **Coverage**: does every requirement land in a phase and a test, and nothing more.
4. **Shape and rules**: phasing per [planning.md](../../../docs/agents/planning.md), and the design against R1–R18 in [AGENTS.md](../../../AGENTS.md).

Output goes to `.agentic/plan-review.md`, paired with `.agentic/plan-review.json`. The plan's author applies the findings, and a human approves the plan before `/dovo-code` runs. Your job is to make that approval short. The findings that matter most come from reading the code, not from re-reading the plan.

## Hard boundaries

- **Never edit the plan, `src/` or `tests/`, and never commit or push.** Report findings and hand off.
- **Never run tests or tooling.** Read-only `git`, read-only `gh`, `rg` and file reads are enough.
- **Re-read from disk.** Every file you reason about is read fresh in this session, never recalled.
- **Every finding cites the plan section it concerns and the `path:line` or requirement ID behind it**, and says what goes wrong if the plan is implemented as written.

## 1. Resolve the input

The plan is `<plan-path>` when given, otherwise `.agentic/plan.md`. If it is missing, stop and say so. Read it in full, then every file it links to (usually `.agentic/evidence.md`), and `plan.json` beside it. Take the issue number from the `# Issue #<n>:` header and read the issue with `gh issue view <n> --json number,title,body`; it is the contract the plan answers to. Without an issue, the plan's `## Requirements` list is the contract.

## 2. Review from a fresh context

A reviewer that shares the planner's context inherits its blind spots. If this session wrote the plan, delegate the review to a fresh subagent. Give it the Hard boundaries above as its first lines, the plan path and the issue number, and nothing else: no rationale and no "I already checked X". If no subagent can be launched, say so in the report's **Provenance** line and do not approve.

## 3. Check grounding

- **Base commit:** run `git merge-base --is-ancestor <sha> HEAD` on the sha in the "Grounded against" line. If it is not an ancestor, or `git log --oneline <sha>..HEAD -- <planned paths>` shows commits, the citations may be stale; say so.
- **Citations:** open every `path:line` in Traps, the artifact inventory and the Phases table, and sample the phase sections. A citation that points at the wrong symbol, or a symbol that no longer exists, is a finding.
- **Links:** open every file the plan links to and confirm it belongs to this issue. A link to another issue's evidence is a Blocking finding: the implementer will trust it.

## 4. Hunt for what the plan missed

Read every function the plan changes, and its callers (`rg -n '\b<symbol>\b' src tests`). Look for:

- **Existing behaviour** the change alters for current users, such as an existing command that starts behaving differently. The plan must name it.
- **Existing bugs on the path** the plan builds on, which the new code would inherit or make worse.
- **Persisted formats** (session state and manifests, `data.db`, `config.json`, catalog `index.json`, blueprint and step YAML): can data written before the change still be read after it? Strict models with `extra="forbid"`, required new fields and renamed keys all break existing data.
- **Exhaustive iterations** over an enum or type list that gains a member: every one must be listed or found by a planned `rg`.
- **Merge, override and default semantics** that treat fields one at a time when the plan makes two fields depend on each other.
- **Callers the plan does not list** for a signature it changes.
- **Conditionals left to the implementer**, such as "delete X after grep confirms": run the check now and report the answer.
- **Issue shapes the code contradicts**: the plan adopts an internal shape from the issue (a helper name, module, type alias or scope boundary) that the code shows is wrong, such as a symbol that duplicates or shadows an existing one, or an injected dependency the change holds but bypasses. A Trap that tells the implementer to work around the issue's shape is the usual sign. Take a position: report it as a Warning whose Fix is the better design and names the departure from the issue (planning.md Step 1). Never leave it as a neutral "confirm" under **For the human**.
- **Guards that exclude the adopted foundation**: the plan follows an issue guard ("same message as today") that forbids behaviour of the foundation the issue adopts, or freezes wording nothing relies on (planning.md Step 1). Report it as a Warning whose Fix is the departure, and recommend it under **For the human**.

A miss is Blocking when implementing the plan as written produces a bug, data loss, a crash or a broken public surface; otherwise a Warning.

## 5. Check coverage

- Every obligation of every FR, NFR and numbered error case (each sentence a test could observe) is covered by a phase's FRs cell in the Phases table and by at least one test line under that phase's `### Tests`; an id that appears somewhere is not enough. A requirement with an uncovered part is a Warning that quotes the issue sentence and names the phase that should carry it. List the gaps.
- Every command the plan adds or changes has the R13 runner matrix: success, a failure path, `--format json` and each option it binds.
- Nothing an issue under `## Owned elsewhere` owns is planned, unless the plan names it as a departure under What you're approving with an Open Question, or it is a sentence the change makes false (R17).
- Unrequested surface is surface that serves no FR, NFR, guard or Definition of done item. It gets no finding when a rule requires it (R10 callers, R17 sentences) or the plan names it as a departure with an Open Question; otherwise it is a Warning when public and a Suggestion when internal.
- Each planned test pins a contract no other test pins (R12), and sibling cases are planned as one parametrized test, on one line.
- Each Open Question states the answer the plan assumes, and `.agentic/plan.json` lists the same Open Questions with the same ids and assumed answers. A missing or disagreeing `plan.json` is a Warning: the blueprint asks the human from it. A Decision that changes public surface or a persisted format without an Open Question is a finding.

## 6. Check shape and rules

- The plan follows the template in [planning.md](../../../docs/agents/planning.md) Step 4, including the Phases table. That template has no Traceability, GWT Scenarios, per-phase Instructions, Edge cases or **Unchanged:** section, so their absence is never a finding. A phase's `### Code` that copies a literal the issue's Pre-determined data already holds, or stubs a private helper, is a Suggestion whose Fix is to cite the issue section or drop the stub.
- Each phase is one concern, roughly 400 changed lines or less, passes the gate on its own and ships something. A phase over budget states why it cannot be split.
- The phases together serve one responsibility, since they land in one PR. A plan whose phases mix kinds of change (a foundation and its adoption, or a behaviour change and a refactor), not flagged under What you're approving with a recommendation to split the issue, is a Warning.
- Phases are ordered by dependency, lower layers first, and replacing an internal is phased add, switch callers, delete, with the deletion phase in the ledger (R10).
- The planned design against R1–R18: report **only violations**, each with the rule ID and the plan section that breaks it.
- Numbers agree everywhere they appear: line estimates, totals, the Definition of Done.

## 7. Write the report

Write `.agentic/plan-review.md`, overwriting any earlier one, and echo the verdict, the Blocking list and the questions for the human in chat:

```markdown
## dovo-plan-review

**Verdict:** APPROVE | CHANGES REQUIRED
**Plan:** `<plan path>`, grounded at `<sha>` (ancestor of HEAD: yes | no)
**Provenance:** fresh session | same session that wrote the plan

### Blocking

| ID | Plan section | Evidence | Issue | Fix |
|---|---|---|---|---|
| B1 | Phase 2 Code | `path:line` or FR-n | What goes wrong if implemented as written. | The concrete change to the plan. |

### Warnings

| ID | Plan section | Evidence | Issue | Fix |
|---|---|---|---|---|

### Suggestions

| ID | Plan section | Evidence | Issue | Fix |
|---|---|---|---|---|

### Coverage
- Unmapped requirements: <FR/NFR/error/DoD IDs, or "none">
- Commands missing R13 cases: <command: case, or "none">

### For the human
- <each decision only a human can make, with the plan's current assumption and your recommendation>
```

An empty section keeps its heading with the line `none`. IDs are the tier letter plus a number from 1 within the tier, in row order. Say `APPROVE` only with zero Blocking findings.

## 8. Emit the machine-readable findings

Write `.agentic/plan-review.json` alongside the markdown, overwriting any earlier one. `.dovo/catalog/blueprints/feature-dev.yml` folds every finding it lists into the plan, so it must agree with `plan-review.md` row for row.

```json
{
  "verdict": "CHANGES_REQUIRED",
  "round": 1,
  "provenance": "fresh_session",
  "plan": ".agentic/plan.md",
  "grounded_at": "8eb591cd",
  "grounded_is_ancestor": true,
  "counts": { "blocking": 1, "warnings": 0, "suggestions": 0 },
  "findings": [
    {
      "id": "B1",
      "severity": "BLOCKER",
      "section": "Phase 2 Code",
      "evidence": "src/dovo/core/sessions/models.py:68",
      "rule": null,
      "summary": "SessionLogEvent is extra=\"forbid\", so logs written before the new field is added fail to load.",
      "fix": "Give the field a default and plan a test that reads a log written without it."
    }
  ],
  "coverage": { "unmapped_requirements": [], "commands_missing_r13": [] },
  "for_human": ["OQ-1: the plan assumes discarded sessions stay hidden by default."]
}
```

- `verdict` is exactly `APPROVE` or `CHANGES_REQUIRED`, and is `APPROVE` iff `counts.blocking` is `0`. `round` counts this skill's runs on the plan, from 1.
- `provenance` is `fresh_session` or `same_session`. `grounded_at` and `grounded_is_ancestor` come from the **Plan** line.
- `findings` lists every finding, Blocking first, then Warnings, then Suggestions, each with the `id` from its `plan-review.md` row. `severity` is `BLOCKER`, `WARNING` or `SUGGESTION`. `section`, `evidence` and `fix` copy the row's columns; `summary` is the row's Issue in one line. `rule` is an R-id when the finding is a rule violation, otherwise `null`.
- `coverage` lists the IDs from the **Coverage** section (`"<command>: <case>"` strings for R13 gaps), `[]` for "none". `for_human` copies the **For the human** bullets, `[]` for "none".

## 9. Hand off

Report the report path and the verdict. On `CHANGES REQUIRED`, the plan's author applies the findings and this skill runs once more in a fresh session. **Cap at 2 rounds**; after that, the human decides on whatever is still open. Do not start `/dovo-code`: a human approves the plan first.
