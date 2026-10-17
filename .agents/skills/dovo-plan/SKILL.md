---
name: dovo-plan
description: >-
  Plan a Dovo CLI change before any code is written: trace the issue's
  contract, ground every artifact in the tree, size the work and split it into
  phases that each become one PR, declare per-phase test stubs and what each phase
  deletes, and write the plan to .agentic/plan.md (with citations split into
  .agentic/evidence.md when non-trivial). Invoked as /dovo-plan [<issue-number>].
disable-model-invocation: true
---

# dovo-plan

Produce a plan precise enough that a different agent with no memory of the issue could implement each phase correctly, and a reviewer could check each phase's diff against it. Do not write or modify implementation code; the plan is the entire deliverable.

[docs/agents/planning.md](../../../docs/agents/planning.md) is the authority for plan content: its steps, artifact checklist, phasing rules, code sample rules and template are not repeated here.

Plan when the issue adds or changes a command, DTO/`*Result` model, status enum, domain exception, service, entrypoint method, formatter, config key, schema field, or `core/db/` table. Skip only for a single-file change with no new surface.

## Hard boundaries

- **Never run tooling that mutates or validates.** Read-only `git` and read-only `gh` are the only commands you need.
- **Never edit `src/` or `tests/`, commit, push or touch PR state.**
- **Never plan from memory.** Every path, symbol and signature in the plan comes from a file read this session.
- **Never plan a test whose contract is already pinned elsewhere.** Name what each new test uniquely pins.

## 1. Check the issue and reset the workspace

A parent issue is never planned: its sub-issues are. Before touching `.agentic/`, check:

```bash
gh issue view <n> --json subIssues --jq '.subIssues.nodes[] | "#\(.number) \(.state) \(.title)"'
```

If this lists anything, stop: report the sub-issues, and name as the one to plan next the first open sub-issue whose blockers are all closed (`gh issue view <m> --json blockedBy --jq '.blockedBy.nodes[] | "#\(.number) \(.state)"'`). Otherwise reset:

```bash
rm -rf .agentic && mkdir -p .agentic
```

A stale plan or review must never be read as current.

## 2. Ground

Follow planning.md Steps 1-2: read the issue's contract (or the user's request, absent an issue number), read [AGENTS.md](../../../AGENTS.md) for the rules and exemplars, and ground every touched surface in the current default branch with `file:line` citations. Read the callers of everything the change touches: changed behaviour for existing users, existing bugs on the path, and the compatibility of persisted formats are the findings a reviewer most needs and the issue never states.

## 3. Size and phase

Follow planning.md Step 3. Estimate the changed lines per FR and group FRs into phases of one concern each, roughly 400 changed lines or less, each passing the quality gate on its own. Replacing an internal is phased as add, switch callers, delete, with the deletion phase planned now. A change that fits one phase stays one phase.

## 4. Write the plan

Structure `.agentic/plan.md` per planning.md Step 4, in this order: What you're approving (open questions with their assumed answers, changes to existing behaviour, what stays unchanged), Traps, GWT Scenarios, the Phases table with its deletion ledger, Traceability, Artifact inventory, one section per phase, and cross-cutting doc updates. Link the issue rather than copying its prose; copy only literal contracts, into the phase that implements them.

When the issue leaves a detail unspecified, choose the option consistent with the nearest existing pattern, record the rejected alternative, and add a numbered Open Question stating the assumed answer rather than stalling. Ask at most one clarifying question when a tradeoff needs confirmation first.

## 5. Hand off

Run planning.md Step 5. Report the paths written, the Phases table, a one-paragraph summary, every numbered Open Question restated in chat, and plainly that this was planning only: nothing was implemented, tested, committed or pushed.

**A human reviews the plan before implementation.** Stop and wait. `/dovo-plan-review` reviews it in a fresh session first; `/dovo-code --phase 1` implements the first phase.
