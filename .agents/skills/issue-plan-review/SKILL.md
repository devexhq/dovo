---
name: issue-plan-review
description: >-
  Experimental slice-flow copy of dovo-plan-review, used only by the
  issue-plan blueprint or when asked for by name; use dovo-plan-review
  otherwise. Review a plan written by /issue-plan before any code is written:
  re-ground it against the current tree, hunt for existing behaviour and
  persisted formats it breaks or relies on, check that every requirement maps
  to a phase and a test, check phasing, slicing, the epic ready criteria and
  the AGENTS.md rules, then write findings to .agentic/plan-review.md and
  .agentic/plan-review.json. Invoked as /issue-plan-review [<plan-path>].
disable-model-invocation: true
---

Forked from `dovo-plan-review` at `944de0ce`. This copy is temporary: once the slice flow works, it replaces `dovo-plan-review`.

# issue-plan-review

Review a plan written by `/issue-plan`, in this order of importance:

1. **Grounding**: does the plan describe the code as it is today.
2. **Missed behaviour**: what the planned change breaks or depends on that the plan does not say.
3. **Coverage**: does every requirement land in a phase and a test, and nothing more.
4. **Shape and rules**: phasing per [planning.md](../../../docs/agents/planning.md), slicing and the epic ready criteria per [issue-plan](../issue-plan/SKILL.md), and the design against R1–R18 in [AGENTS.md](../../../AGENTS.md).

Output goes to `.agentic/plan-review.md`, paired with `.agentic/plan-review.json`. The plan's author applies the findings, and a human approves the plan before `/issue-code` runs. Your job is to make that approval short. The findings that matter most come from reading the code, not from re-reading the plan.

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
- **Citations:** open every `path:line` in Traps, the artifact inventory and the Phases table, and sample the instruction sections. A citation that points at the wrong symbol, or a symbol that no longer exists, is a finding.
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

- Every FR, NFR, numbered error case, Definition of Done item and GWT scenario maps to a phase and to at least one planned test in `## Traceability`. List the gaps.
- Every command the plan adds or changes has the R13 runner matrix: success, a failure path, `--format json` and each option it binds.
- Nothing out of scope, or named under Traps as not touched, is planned, except a departure the plan names under What you're approving with an Open Question.
- Each planned test pins a contract no other test pins (R12), and sibling cases are planned as one parametrized test.
- Each Open Question states the answer the plan assumes, and `.agentic/plan.json` lists the same Open Questions with the same ids and assumed answers. A missing or disagreeing `plan.json` is a Warning: the blueprint asks the human from it. A Decision that changes public surface or a persisted format without an Open Question is a finding.

## 6. Check shape and rules

- The plan follows the template in [planning.md](../../../docs/agents/planning.md) Step 4, including the Phases table.
- Each phase is one concern, roughly 400 changed lines or less, passes the gate on its own and ships something. A phase over budget states why it cannot be split.
- The phases of each slice together serve one responsibility, since each slice lands in one PR. A slice whose phases mix kinds of change (a foundation and its adoption, or a behaviour change and a refactor), not flagged under What you're approving with a recommendation to split the slice, is a Warning.
- `.agentic/plan.json` `slices` agrees with the plan's `## Slices` table, every phase belongs to exactly one slice, and a plain issue (`contract` is `null`) has one slice whose `child` is the issue.
- Phases are ordered by dependency, lower layers first, and replacing an internal is phased add, switch callers, delete, with the deletion phase in the ledger (R10).
- The planned design against R1–R18: report **only violations**, each with the rule ID and the plan section that breaks it.
- Numbers agree everywhere they appear: line estimates, totals, the Definition of Done.

## 6a. Check the epic ready criteria

Skip this when `plan.json` `contract` is `null`, and say so. Otherwise check each criterion from the `plan.json` fields, mechanically where the fields allow, and report each failure as a Blocking finding citing the field (`contract.ownership`, `slices[2].estimate`):

- **One owner per surface and aspect:** no two `contract.ownership` rows share a `surface` and `aspect`, and no slice `stub` restates a rule another slice owns.
- **No in-between shape a later slice reverses:** every `contract.target` surface with two or more `touched_by` slices has an ownership row.
- **A sound slice graph:** `depends_on` names only slice ids, the graph is acyclic, and every `blocked_by` number is an existing issue outside the epic (`gh issue view <n> --json state`).
- **Every related issue has a role:** each sub-issue of the epic and each issue the contract names appears in `contract.existing_issues`, and no container is a slice (contract-template.md, "Mapping existing issues").
- **Size:** every slice's `estimate.src_docs` is under about 400 and `src_docs` plus `tests` under about 900, or the plan says why the slice cannot be split.
- **Pilot and closing docs:** exactly one slice has `pilot: true`, and the last slice, and only it, has `closing_docs: true`.
- **Checkpoints:** `contract.checkpoints` places one after the pilot and a baseline before the closing docs slice.
- **No ownerless deferral:** every `non_goals` item of kind `deferred` or `owned` has `owners`, and no `stub` defers work without naming the slice or issue that does it (#1227).
- **Decisions confirmed:** no `decisions` item with `settled_by.kind` `inherited` lacks `confirmed`, and every open question that blocks a slice carries a recommended answer.
- **Plan-challenge:** each `/dovo-plan-challenge` finding is resolved in the plan or recorded in `contract.decisions`. Before the challenge has run, list this under **For the human** instead of as a finding.

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
| B1 | Phase 2 instructions | `path:line` or FR-n | What goes wrong if implemented as written. | The concrete change to the plan. |

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
      "section": "Phase 2 instructions",
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

Report the report path and the verdict. On `CHANGES REQUIRED`, the plan's author applies the findings and this skill runs once more in a fresh session. **Cap at 2 rounds**; after that, the human decides on whatever is still open. Do not start `/issue-code`: a human approves the plan first.
