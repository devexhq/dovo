---
name: issue-plan
description: >-
  Experimental slice-flow copy of dovo-plan, used only by the issue-plan
  blueprint or when asked for by name; use dovo-plan otherwise. Plan a Dovo CLI
  issue or epic before any code is written: on an epic, write the feature
  contract; ground every artifact in the tree, size the work, split it into
  ordered phases and group the phases into slices of one PR each, and write the
  plan to .agentic/plan.md and its contract, slices and open questions to
  .agentic/plan.json. Invoked as /issue-plan [<issue-number>].
disable-model-invocation: true
---

Forked from `dovo-plan` at `944de0ce`. This copy is temporary: once the slice flow works, it replaces `dovo-plan`.

# issue-plan

Produce a plan precise enough that a different agent with no memory of the issue could implement each phase correctly, and a reviewer could check each phase's diff against it. Do not write or modify implementation code; the plan is the entire deliverable.

[docs/agents/planning.md](../../../docs/agents/planning.md) is the authority for plan content: its steps, artifact checklist, phasing rules, code sample rules and template are not repeated here.

Plan when the issue adds or changes a command, DTO/`*Result` model, status enum, domain exception, service, entrypoint method, formatter, config key, schema field, or `core/db/` table. Skip only for a single-file change with no new surface.

## Slices and epics

- **A slice is one PR.** A phase stays what planning.md says: a reviewable chunk inside a slice. Where planning.md says the phases land in one PR, read "the phases of one slice land in one PR".
- **An epic** is an issue whose body holds, or will hold, `## Feature contract` and `## Work plan`. Epics never nest: an epic is never a slice of another epic.
- **A plain issue** is any other issue. It gets a one-slice plan with `contract: null`, and its one slice's child is the issue itself. That plan behaves like a `dovo-plan` plan.
- **Each slice is tracked by a child issue**, a native sub-issue of the epic. A child that does not exist yet is created just in time from the slice's `stub`, when the slice comes up, not by this skill.
- **Closing keywords:** every slice PR ends with `Fixes #<child>` then `Refs #<epic>`; the closing docs slice ends with `Fixes #<child>` then `Fixes #<epic>`; a one-slice plan on a plain issue uses only `Fixes #<issue>`.

## Hard boundaries

- **Never run tooling that mutates or validates.** Read-only `git` and read-only `gh` are the only commands you need.
- **Never edit `src/` or `tests/`, commit, push or touch PR state.**
- **Never plan from memory.** Every path, symbol and signature in the plan comes from a file read this session.
- **Never plan a test whose contract is already pinned elsewhere.** Name what each new test uniquely pins.

## 1. Check the issue and reset the workspace

Decide whether the issue is an epic or a plain issue before touching `.agentic/`:

```bash
gh issue view <n> --json body,milestone,subIssues --jq '.subIssues.nodes[] | "#\(.number) \(.state) \(.title)"'
```

The issue is planned as an epic when it lists sub-issues, when its body already holds `## Feature contract`, or when the user names it as the container for a milestone's group of issues. Otherwise it is a plain issue. Read each related issue's blockers with `gh issue view <m> --json blockedBy --jq '.blockedBy.nodes[] | "#\(.number) \(.state)"'`. Then reset:

```bash
rm -rf .agentic && mkdir -p .agentic
```

A stale plan or review must never be read as current.

## 2. Ground

Follow planning.md Steps 1-2: read the issue's contract (or the user's request, absent an issue number), read [AGENTS.md](../../../AGENTS.md) for the rules and exemplars, and ground every touched surface in the current default branch with `file:line` citations. Read the callers of everything the change touches: changed behaviour for existing users, existing bugs on the path, and the compatibility of persisted formats are the findings a reviewer most needs and the issue never states.

## 2a. Write the feature contract (epic only)

Skip this on a plain issue. On an epic, write the contract before any phase or slice, following [contract-template.md](contract-template.md): its sections in order, the rules every section follows, its section guidance, and "Mapping existing issues", which gives every sub-issue and related open issue a role (epic, child, split, superseded container, prerequisite or sibling epic) before any slice is written. Ground the code inventory from commands run at the grounding sha, never estimates. Each open question carries a recommended answer and the slices it blocks.

## 3. Size and phase

Follow planning.md Step 3. Estimate the changed lines per FR and group FRs into phases of one concern each, roughly 400 changed lines or less, each passing the quality gate on its own. Replacing an internal is phased as add, switch callers, delete, with the deletion phase planned now. A change that fits one phase stays one phase. The phases of one slice land in one PR, so together they serve that slice's one responsibility. When grounding shows a plain issue mixes kinds of change (a foundation and its adoption, or a behaviour change and a refactor), say so first under What you're approving and recommend planning it as an epic.

## 3a. Group the phases into slices

On a plain issue, all phases form one slice, `S1`, whose `child` is the issue itself. On an epic:

- **Size each slice.** `src/` plus docs stay under about 400 changed lines, and the whole PR, tests included, under about 900. Tests are estimated separately. A slice over budget says why it cannot be split.
- **One responsibility per slice.** A slice never holds a phase another slice's phase must reverse, and every surface two slices touch has one owner in the contract's Shared-surface ownership table; the other slice says "after Sx" and does not restate it.
- **Order by dependency.** `depends_on` names slice ids, `blocked_by` names prerequisite issues outside the epic, and the graph is acyclic.
- **Slice 1 is the pilot**, unless another slice's conventions are reused by more slices; then that slice is the pilot. Its "as landed" report updates the conventions and decision log before later slices are written in full.
- **The last slice is the closing docs slice.** It reviews every doc the feature touched against `main` and carries `Fixes #<epic>`.
- **Place the checkpoints:** after the pilot, after slices that share an owned surface, and a baseline before the closing docs slice.
- **Map each slice to a child issue.** An existing work issue the contract maps to the slice is its `child`. Otherwise `child` is `null` and the slice carries a `stub`: the markdown body of its child issue, written to the #1227 shape:
  - the slice's FRs, NFRs and guards, and its Definition of done, citing the epic's contract sections by heading instead of restating them
  - **Owned elsewhere**: `#n owns <X>` or `Sx owns <X>` pointers from the ownership table, for collision checks, never bans
  - **Notes for the planner**: one line each, for things noticed but not asked for
  - no Out of scope section and no ownerless deferral: work left undone names the slice or issue that does it
  - "unchanged" stated only as an NFR or guard naming the contract, never "keep X as today" for incidental wording

## 4. Write the plan

Structure `.agentic/plan.md` per planning.md Step 4, in this order: What you're approving (open questions with their assumed answers, changes to existing behaviour, what stays unchanged), on an epic the `## Feature contract` from Step 2a, Traps, GWT Scenarios, the Phases table with its deletion ledger, a `## Slices` table (id, title, phases, depends on, blocked by, child, pilot or closing docs, estimate), Traceability, Artifact inventory, one section per phase, and cross-cutting doc updates. Link the issue rather than copying its prose; copy only literal contracts, into the phase that implements them.

When grounding shows one of the issue's internal shapes is wrong, follow planning.md Step 1: plan the better design, assume it in an Open Question with the `path:line` that shows why, and name the departure. Only the issue's observable contracts are binding; an issue sentence is not by itself a reason to plan a duplicate symbol or a dependency the code holds but never calls. A guard ("same message as today") binds only what a user or caller relies on, and planning.md Step 1 allows a named departure from it in the same way.

When the issue leaves a detail unspecified, choose the option consistent with the nearest existing pattern, record the rejected alternative, and add a numbered Open Question stating the assumed answer rather than stalling. Ask at most one clarifying question when a tradeoff needs confirmation first.

## 5. Emit the machine-readable open questions

Write `.agentic/plan.json` alongside the markdown. `.dovo/catalog/blueprints/feature-dev.yml` reads it to ask the human which open questions to answer, so it must agree with the plan's **Open questions** block entry for entry. The slice flow also reads its `contract` and `slices` to write the epic's contract and work plan, so they must agree with the plan's `## Feature contract` and `## Slices` sections.

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
  ],
  "contract": null,
  "slices": [
    {
      "id": "S1",
      "title": "List discarded sessions",
      "phases": [1, 2],
      "depends_on": [],
      "blocked_by": [],
      "child": 914,
      "owns": [],
      "lint_gates": [],
      "pilot": false,
      "closing_docs": false,
      "estimate": { "src_docs": 180, "tests": 220, "basis": "Phases table estimates" },
      "stub": null
    }
  ]
}
```

- `issue` is the issue number, or `null` when planning from a plain-language request. `grounded_at` is the short sha from the plan's "Grounded against" line. `phases` is the number of rows in the Phases table.
- `open_questions` lists every numbered Open Question in order, each with the `OQ-<n>` id from `plan.md`. `summary` is the one-line question followed by ` (assumed: <short answer>)`, since it is the only text the blueprint's pick-list shows for the question; `assumed` is the full answer the plan is written against, `why` the reason with its `path:line`, `alternatives` the rejected answers (`[]` when none), and `phases` the phase numbers whose instructions change if the answer changes.
- `open_questions` is `[]` when the plan says "None." On an epic, each item may add `"blocks": ["S3"]`, the slices it blocks.
- `contract` is `null` on a plain issue. On an epic it is exactly the `contract` object in [contract-template.md](contract-template.md) under "`plan.json` shape", agreeing with the plan's `## Feature contract`.
- `slices` lists every slice in dependency order, one per row of the plan's `## Slices` table. `id` is `S<n>`; `phases` the phase numbers it lands; `depends_on` slice ids; `blocked_by` prerequisite issue numbers outside the epic; `child` the child issue number, or `null` until it is created; `owns` the surfaces and aspects the slice owns in the ownership table; `lint_gates` the `tests/lint/` checks it lands; `estimate` the `src_docs` and `tests` changed-line estimates with their `basis`; `stub` the child issue's markdown body when `child` is `null`, otherwise `null`.
- On an epic, exactly one slice has `pilot: true` and only the last has `closing_docs: true`. A plain issue's one slice has both `false`, `child` equal to `issue` and `stub` `null`.
- These fields only add to the shape above: `issue`, `plan`, `grounded_at`, `phases` and `open_questions` keep their meaning.

## 6. Hand off

Run planning.md Step 5. Report the paths written, the Phases table, the Slices table, a one-paragraph summary, every numbered Open Question restated in chat, and plainly that this was planning only: nothing was implemented, tested, committed or pushed.

**A human reviews the plan before implementation.** Stop and wait. `/issue-plan-review` reviews it and `/dovo-plan-challenge` challenges its design, each in a fresh session, first, and `/dovo-plan-walkthrough` explains the result to the approver; `/issue-code --slice <id>` then implements one slice per run, in order.
