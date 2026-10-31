---
name: dovo-plan
description: >-
  Plan a Dovo CLI change before any code is written: trace the issue's
  contract, ground every artifact in the tree, size the work and split it into
  ordered phases that land together in one PR, declare per-phase test lines and what each phase
  deletes, and write the plan to .agentic/plan.md (with citations split into
  .agentic/evidence.md when non-trivial) and its open questions to
  .agentic/plan.json. Invoked as /dovo-plan [<issue-number>].
disable-model-invocation: true
---

# dovo-plan

Produce a plan precise enough that a different agent holding only the issue and the plan, with no memory of this session, could implement each phase correctly, and a reviewer could check each phase's diff against it. Do not write or modify implementation code; the plan is the entire deliverable.

[docs/agents/planning.md](../../../docs/agents/planning.md) is the authority for plan content: its steps, artifact checklist, phasing rules, code sample rules and template are not repeated here.

Plan when the issue adds or changes a command, DTO/`*Result` model, status enum, domain exception, service, entrypoint method, formatter, config key, schema field, or `core/db/` table. Skip only for a single-file change with no new surface.

## Hard boundaries

- **Never run tooling that mutates or validates.** Read-only `git` and read-only `gh` are the only commands you need. The only exception is the `.agentic/` workspace reset in Step 1.
- **Never edit `src/` or `tests/`, commit, push or touch PR state.**
- **Never plan from memory.** Every path, symbol and signature in the plan comes from a file read this session.
- **Never plan a test whose contract is already pinned elsewhere.** Name what each new test uniquely pins.

## 1. Check the issue and reset the workspace

A parent issue is never planned here: an epic is planned with `issue-plan-v2`, and a plain parent's sub-issues are planned one at a time. Before touching `.agentic/`, check:

```bash
gh issue view <n> --json labels --jq '.labels[].name'
gh issue view <n> --json subIssues --jq '.subIssues.nodes[] | "#\(.number) \(.state) \(.title)"'
```

If the labels include `type:epic`, stop and say it is planned with `dovo run issue-plan-v2 -i <n>`. If the sub-issues list anything, stop: report the sub-issues, and name as the one to plan next the first open sub-issue whose blockers are all closed (`gh issue view <m> --json blockedBy --jq '.blockedBy.nodes[] | "#\(.number) \(.state)"'`). Otherwise reset:

```bash
rm -rf .agentic && mkdir -p .agentic
```

A stale plan or review must never be read as current.

## 2. Ground

Follow planning.md Steps 1-2: read the issue's contract (or the user's request, absent an issue number), read [AGENTS.md](../../../AGENTS.md) for the rules and exemplars, and ground every touched surface in the current default branch with `file:line` citations. Read the callers of everything the change touches: changed behaviour for existing users, existing bugs on the path, and the compatibility of persisted formats are the findings a reviewer most needs and the issue never states.

## 3. Size and phase

Follow planning.md Step 3. Estimate the changed lines per FR and group FRs into phases of one concern each, roughly 400 changed lines or less, each passing the quality gate on its own. Replacing an internal is phased as add, switch callers, delete, with the deletion phase planned now. A change that fits one phase stays one phase. All phases land in one PR, so together they serve the issue's one responsibility. When grounding shows the issue mixes kinds of change (a foundation and its adoption, or a behaviour change and a refactor), say so first under What you're approving and recommend splitting the issue with `/dovo-issue`.

## 4. Write the plan

Structure `.agentic/plan.md` per planning.md Step 4, in this order: What you're approving (open questions with their assumed answers, and changes to existing behaviour), Traps (edge cases included), Owned elsewhere, the Phases table with its deletion ledger, Artifact inventory (every file and symbol each phase touches), one section per phase, and cross-cutting doc updates. Each phase section holds `### Code` (only new public surface the issue does not pin literally), `### Decisions` (only departures from the issue or the nearest existing pattern) and `### Tests` (one line per test, citing the requirements it pins, with the R13 matrix for each command). Link the issue rather than copying its prose, and cite the issue section that holds a literal contract rather than copying it. There is no Traceability, GWT Scenarios or per-phase Instructions section: the Phases table's FRs column and the test lines map every requirement. A requirement whose parts land in different phases is listed in each of those phases' FRs cells with its part in parentheses (`FR-7 (reply)`, `FR-7 (fixer)`), and each part is cited by a test line; before finishing, split each FR, NFR and error case into its obligations and check that each lands in a phase.

When grounding shows one of the issue's internal shapes is wrong, follow planning.md Step 1: plan the better design, assume it in an Open Question with the `path:line` that shows why, and name the departure. Only the issue's observable contracts are binding; an issue sentence is not by itself a reason to plan a duplicate symbol or a dependency the code holds but never calls. A guard ("same message as today") binds only what a user or caller relies on, and planning.md Step 1 allows a named departure from it in the same way.

When the issue leaves a detail unspecified, choose the option consistent with the nearest existing pattern, record the rejected alternative, and add a numbered Open Question stating the assumed answer rather than stalling. Ask at most one clarifying question when a tradeoff needs confirmation first.

## 5. Emit the machine-readable open questions

Write `.agentic/plan.json` alongside the markdown. `.dovo/catalog/blueprints/feature-dev.yml` reads it to ask the human which open questions to answer, so it must agree with the plan's **Open questions** block entry for entry.

```json
{
  "issue": 914,
  "plan": ".agentic/plan.md",
  "grounded_at": "8eb591cd",
  "phases": 2,
  "open_questions": [
    {
      "id": "OQ-1",
      "summary": "Should `dovo session list` include discarded sessions by default? (assumed: no)",
      "assumed": "No: discarded sessions are hidden unless `--all` is passed.",
      "why": "Matches `dovo worktree list`, which hides removed worktrees (`src/dovo/cli/worktree/commands/worktree_list.py`).",
      "alternatives": ["Show them with a `discarded` status column."],
      "phases": [1]
    }
  ]
}
```

- `issue` is the issue number, or `null` when planning from a plain-language request. `grounded_at` is the short sha from the plan's "Grounded against" line. `phases` is the number of rows in the Phases table.
- `open_questions` lists every numbered Open Question in order, each with the `OQ-<n>` id from `plan.md`. `summary` is the one-line question followed by ` (assumed: <short answer>)`, since it is the only text the blueprint's pick-list shows for the question; `assumed` is the full answer the plan is written against, `why` the reason with its `path:line`, `alternatives` the rejected answers (`[]` when none), and `phases` the phase numbers whose plan changes if the answer changes.
- `open_questions` is `[]` when the plan says "None."

## 6. Hand off

Run planning.md Step 5. Report the paths written, the Phases table, a one-paragraph summary, every numbered Open Question restated in chat, and plainly that this was planning only: nothing was implemented, tested, committed or pushed.

**A human reviews the plan before implementation.** Stop and wait. `/dovo-plan-review` reviews it and `/dovo-plan-challenge` challenges its design, each in a fresh session, first, and `/dovo-plan-walkthrough` explains the result to the approver; `/dovo-code` then implements every phase, in order.
