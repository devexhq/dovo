Forked from `dovo-plan` at `de256118`. This copy is temporary: once the slice flow works, it replaces `dovo-plan`.

# issue-plan

Produce a plan precise enough that a different agent holding only the issue and the plan, with no memory of this session, could implement each phase correctly, and a reviewer could check each phase's diff against it. Do not write or modify implementation code; the plan is the entire deliverable.

[docs/agents/planning.md](../../../../../docs/agents/planning.md) is the authority for plan content: its steps, artifact checklist, phasing rules, code sample rules and template are not repeated here.

Plan when the issue adds or changes a command, DTO/`*Result` model, status enum, domain exception, service, entrypoint method, formatter, config key, schema field, or `core/db/` table. Skip only for a single-file change with no new surface.

## Modes

| Invocation | Input | Job |
|---|---|---|
| `/issue-plan [<issue-number>]` | the issue | **Issue mode.** On an epic, write the feature contract and the slice table; on a plain issue, write one slice. Every slice gets its scope, dependencies, child or stub, ownership, estimate and size, and **no phases**. Steps 1, 2, 2a, 3, 3a, 4, 5 and 6. |
| `/issue-plan --slice <id>` | `.agentic/epic-plan.md` and `.agentic/epic-plan.json`, the epic plan as downloaded from the work plan's plan session; the epic's body; the slice's child issue | **Slice mode.** Phase that one slice, against `origin/main` as it is now, and nothing else. Steps 1b, 2, 3, 4, 5 and 6. |

Detail is written just in time: a slice's phases are planned when its `issue-code` run starts, after the slices before it have landed and their "as landed" reports have updated the contract, never when the issue is sliced. A phase written early goes stale as slices land.

## Slices and epics

- **A slice is one PR.** A phase stays what planning.md says: a reviewable chunk inside a slice. Where planning.md says the phases land in one PR, read "the phases of one slice land in one PR".
- **An epic** is an issue whose body holds, or will hold, `## Feature contract` and `## Work plan`. Epics never nest: an epic is never a slice of another epic.
- **A plain issue** is any other issue. It gets a one-slice plan with `contract: null`, and its one slice's child is the issue itself. Once slice mode phases it, that plan behaves like a `dovo-plan` plan.
- **Each slice is tracked by a child issue**, a native sub-issue of the epic. A child that does not exist yet is created just in time from the slice's `stub`, labelled with the slice's `size`, when the slice comes up, not by this prompt.
- **Closing keywords:** every slice PR ends with `Fixes #<child>` then `Refs #<epic>`; the closing docs slice ends with `Fixes #<child>` then `Fixes #<epic>`; a one-slice plan on a plain issue uses only `Fixes #<issue>`.

## Hard boundaries

- **Never run tooling that mutates or validates.** Read-only `git` and read-only `gh` are the only commands you need. The only exceptions are the `.agentic/` workspace reset in Step 1 and the epic-plan copy and stale-file cleanup in Step 1b.
- **Never edit `src/` or `tests/`, commit, push or touch PR state.**
- **Never plan from memory.** Every path, symbol and signature in the plan comes from a file read this session.
- **Never plan a test whose contract is already pinned elsewhere.** Name what each new test uniquely pins.

## 1. Check the issue and reset the workspace (issue mode)

Decide whether the issue is an epic or a plain issue before touching `.agentic/`:

```bash
gh issue view <n> --json body,milestone --jq '"milestone: \(.milestone.title // "none")", .body'
gh issue view <n> --json subIssues --jq '.subIssues.nodes[] | "#\(.number) \(.state) \(.title)"'
```

The plan's shape scales with how far the shared surfaces spread, not with how many issues there are. Read the issue, its sub-issues, and any group the user names (such as a milestone's issues), and sort them:

- **One issue, or issues that share no surface:** plain issues. Plan the issue as a plain issue; related issues get **Owned elsewhere** pointers, not a contract.
- **Several issues sharing a surface in one area:** one epic with a short contract (Step 2a).
- **Issues changing a surface other areas depend on, or setting rules other work will cite:** several sibling epics, with a foundation epic whose pilot runs first. Plan only the epic asked for, and name the siblings and the foundation under What you're approving. Epics never nest.

An issue labelled `type:epic` (`gh issue view <n> --json labels --jq '.labels[].name'`) is the user's explicit choice: plan it as an epic whatever the trigger below counts, with a contract as short as what its issues share. The trigger decides the shape only for an unlabelled issue. Write an epic contract only when three or more issues share an owned surface, or a decision would otherwise be made twice. Below that, plan a plain issue. An unlabelled parent issue below the trigger is not planned itself: report its sub-issues, name the first open one whose blockers are all closed as the one to plan next, as a plain issue, one at a time, and stop. An issue whose body already holds `## Feature contract` is an epic.

Read each related issue in full before giving it a role in `existing_issues`, not only its title and blockers:

```bash
gh issue view <m> --json title,body,labels,milestone,state
gh issue view <m> --json blockedBy --jq '.blockedBy.nodes[] | "#\(.number) \(.state)"'
```

Then reset:

```bash
rm -rf .agentic && mkdir -p .agentic
```

A stale plan or review must never be read as current.

## 1b. Load the slice (slice mode)

Never reset `.agentic/` in slice mode: the epic plan, `.agentic/epic-plan.md` and `.agentic/epic-plan.json`, is this mode's input. The `issue-code` blueprint copies both from the downloaded plan and records on the slice the child issue its `next` step picked or created. If either is missing, stop and say so. When run by hand, copy the downloaded `.agentic/plan.md` and `.agentic/plan.json` to them first, setting the slice's `child` to its child issue and its `stub` to `null` when the work plan records a child the downloaded plan does not. Never edit the epic plan. Start every run from it, so a retry never builds on its own partial output, and delete the files an earlier planning round in this workspace could leave behind, so none is read as current:

```bash
cp .agentic/epic-plan.md .agentic/plan.md && cp .agentic/epic-plan.json .agentic/plan.json
rm -f .agentic/plan-review.md .agentic/plan-review.json .agentic/plan-challenge.md .agentic/plan-challenge.json .agentic/plan-walkthrough.md .agentic/evidence.md
```

Then read, from the epic plan, and keep in view for the whole run:

- **The slice:** the `slices` entry whose `id` is `<id>`. An id not in `slices` stops the run and names the valid ids. Its `child` is the slice's child issue; `plan.json` `issue` is the epic, or the plain issue itself.
- **The epic's binding contract:** `plan.json` `contract` (none when it is `null`) and the plan's `## Feature contract`, with the ownership rows of this slice and of the slices it depends on.
- **The "as landed" reports:** the `### <id> as landed (#<pr>)` sections under `## Work plan` in the epic's body (`gh issue view <epic> --json body`). They record how earlier slices departed from the plan and which later slices they affect. A slice whose landing changed the contract amended it when it landed, so a report that still conflicts with `contract` is raised as an Open Question, never settled silently.
- **The child issue:** `gh issue view <child> --json number,title,body`, read live, since it was readied just before this step. Its outcomes (`O-n`), decisions (`D-n`), binding values and Done when are the requirements this slice's phases answer, and its **Out of scope** items are its collision checks; on a legacy issue, its FRs, NFRs, guards and Definition of done are the requirements and its **Owned elsewhere** pointers the collision checks.
- **The prior decisions:** `.agentic/prior-decisions.json`, the decisions earlier planning sessions posted to the child issue. The blueprint's `prior-decisions` step writes it just before this one; when run by hand, write it first with `uv run python .dovo/catalog/scripts/sdlc/v2/plan.py prior-decisions --issue <child> --out .agentic/prior-decisions.json`. A decision marked `given` binds this plan like the child issue does: plan against it and never raise it again as an open question. A decision marked `to_confirm` was settled before the child issue's description last changed: raise it as an open question whose `assumed` answer is the earlier decision, with `replaces` naming it (Step 5).

The downloaded plan may come from an earlier slice's code session, since `land` repoints the work plan there when that slice's report amends the plan. Its phases, tests and open questions belong to that earlier slice: never carry them into this plan.

## 2. Ground

Follow planning.md Steps 1-2: read the issue's contract (or the user's request, absent an issue number; in slice mode, the child issue and the epic's binding contract from Step 1b), read [AGENTS.md](../../../../../AGENTS.md) for the rules and exemplars, and ground every touched surface in the current default branch with `file:line` citations. Read the callers of everything the change touches: changed behaviour for existing users, existing bugs on the path, and the compatibility of persisted formats are the findings a reviewer most needs and the issue never states.

## 2a. Write the feature contract (epic, issue mode only)

Skip this on a plain issue and in slice mode, which never changes the contract. On an epic, write the contract before any phase or slice, following [contract-template.md](templates/contract-template.md): its sections in order, the rules every section follows, its section guidance, and "Mapping existing issues", which gives every sub-issue and related open issue a role (epic, child, split, superseded container, prerequisite or sibling epic) before any slice is written.

Keep the contract as short as the surface spread allows. Problem, Target contract and Shared-surface ownership are required, alongside the slices. Every other section is written only when it has something to say, and left out otherwise, never filled with "none". Cite rules already in `docs/agents/architecture/` instead of re-deciding them. When written, the code inventory comes from commands run at the grounding sha, never estimates. Each open question carries a recommended answer and the slices it blocks.

## 3. Size and phase

In issue mode, size only: ground enough to estimate the changed lines each slice needs, `src/` plus docs and tests separately, and write no phases. In slice mode, phase this slice only; when grounding shows the slice mixes kinds of change, or is over its budget, say so first under What you're approving and recommend re-slicing the epic with `/issue-plan <epic>`:

Follow planning.md Step 3. Estimate the changed lines per FR and group FRs into phases of one concern each, roughly 400 changed lines or less, each passing the quality gate on its own. Replacing an internal is phased as add, switch callers, delete, with the deletion phase planned now. A change that fits one phase stays one phase. The phases of one slice land in one PR, so together they serve that slice's one responsibility. When grounding shows a plain issue mixes kinds of change (a foundation and its adoption, or a behaviour change and a refactor), say so first under What you're approving and recommend planning it as an epic.

## 3a. Slice the issue (issue mode)

On a plain issue, the whole issue is one slice, `S1`, whose `child` is the issue itself. On an epic:

- **Size each slice.** `src/` plus docs stay under about 400 changed lines, and the whole PR, tests included, under about 900. Tests are estimated separately. A slice over budget says why it cannot be split.
- **Give each slice a review size**, its `size`, by the descriptions of the repository's `dovo-size-*` labels: `s` for one area and a few files, `m` for a couple of areas, `l` for many areas (packages, formats, command groups, skills, catalog or scripts). `workplan.py next` labels the slice's child issue `dovo-size-<size>` when it creates it, and labels an existing child that has no size label, so the child passes the ready check's size gate; it never overrides a size label a person set. A plain issue's one slice takes the size of the issue's own `dovo-size-*` label.
- **One responsibility per slice.** A slice never delivers a shape another slice must reverse, and every surface two slices touch has one owner in the contract's Shared-surface ownership table; the other slice says "after Sx" and does not restate it.
- **Order by dependency.** `depends_on` names slice ids, `blocked_by` names prerequisite issues outside the epic, and the graph is acyclic.
- **Slice 1 is the pilot**, unless another slice's conventions are reused by more slices; then that slice is the pilot. Its "as landed" report updates the conventions and decision log before later slices are written in full.
- **The last slice is the closing docs slice.** It reviews every doc the feature touched against `main` and carries `Fixes #<epic>`.
- **Place the checkpoints** when the epic has three or more slices: after the pilot, after slices that share an owned surface, and a baseline before the closing docs slice.
- **Map each slice to a child issue.** An existing work issue the contract maps to the slice is its `child`. Otherwise `child` is `null` and the slice carries a `stub`: the markdown body of its child issue. `workplan.py next` creates the child from it as written, and the ready check then checks it against the short issue format before the slice is planned alone from it, so write it to [templates/issue.md](templates/issue.md), its rules and its sections in that order:
  - One opening line naming the slice and the epic, `Slice <id> of #<epic>.`, then `## Why`.
  - `## Depends on`, naming each child issue of a slice in `depends_on` and each `blocked_by` issue, with the assumption to check (a slice whose child issue does not exist yet is named through the epic: `#<epic> slice S2`).
  - `## Outcomes`, the slice's observable behaviour, numbered `O-<n>`.
  - `## Decisions`, numbered `D-<n>`, holding each contract decision the slice's outcomes rest on, copied with its contract ID (`#<epic> D4`); a citation never replaces the copy.
  - `## Binding values`, holding every literal the contract fixes that the slice uses (keys, codes, messages), copied exactly.
  - `## Out of scope`, from the ownership table: each item another slice or issue owns, naming its owner (`#n`, or `#<epic> slice S3`). No ownerless deferral: work left undone names the slice or issue that does it.
  - `## Done when`, naming the outcomes by id.
  - Nothing the template leaves out (paths, symbols, revision numbers, column lists, test names, counts), and no other issue link apart from the opening line, **Depends on**, **Out of scope** and the contract IDs of copied decisions.

## 4. Write the plan

In issue mode, `.agentic/plan.md` holds, in this order: the header and grounding line from planning.md Step 4 (its opening paragraph naming Traceability, not the Phases table, as the requirement map), What you're approving (open questions with their assumed answers, changes to existing behaviour, what stays unchanged), on an epic the `## Feature contract` from Step 2a, Owned elsewhere, a `## Slices` table (id, title, depends on, blocked by, child, pilot or closing docs, estimate, size), and Traceability mapping each requirement of the issue to the slice that lands it. It has no Phases table, Artifact inventory or phase sections; say under What you're approving that each slice is phased and approved in its own `issue-code` run.

In slice mode, the header names the child issue, `# Issue #<child>: <child title>`, and the line after it names the slice and the epic: `Slice <id> of #<epic>.` Copy the epic plan's `## Feature contract` and `## Slices` sections, and its `## Instructions from the approver` when present, verbatim after What you're approving: they are the epic's, and this plan never changes them. The rest follows the paragraph below, for this slice alone.

Structure `.agentic/plan.md` per planning.md Step 4, in this order: What you're approving (open questions with their assumed answers, and changes to existing behaviour), Traps (edge cases included), Owned elsewhere, the Phases table with its deletion ledger, Artifact inventory (every file and symbol each phase touches), one section per phase, and cross-cutting doc updates. Each phase section holds `### Code` (only new public surface the issue does not pin literally), `### Decisions` (only departures from the issue or the nearest existing pattern) and `### Tests` (one line per test, citing the requirements it pins, with the R13 matrix for each command). Link the issue rather than copying its prose, and cite the issue section that holds a literal contract rather than copying it. There is no Traceability, GWT Scenarios or per-phase Instructions section: the Phases table's FRs column and the test lines map every requirement. A requirement whose parts land in different phases is listed in each of those phases' FRs cells with its part in parentheses (`FR-7 (reply)`, `FR-7 (fixer)`), and each part is cited by a test line; before finishing, split each FR, NFR and error case into its obligations and check that each lands in a phase.

When grounding shows one of the issue's internal shapes is wrong, follow planning.md Step 1: plan the better design, assume it in an Open Question with the `path:line` that shows why, and name the departure. Only the issue's observable contracts are binding; an issue sentence is not by itself a reason to plan a duplicate symbol or a dependency the code holds but never calls. A guard ("same message as today") binds only what a user or caller relies on, and planning.md Step 1 allows a named departure from it in the same way.

When the issue leaves a detail unspecified, choose the option consistent with the nearest existing pattern, record the rejected alternative, and add a numbered Open Question stating the assumed answer rather than stalling. Ask at most one clarifying question when a tradeoff needs confirmation first.

## 5. Emit the machine-readable open questions

Write `.agentic/plan.json` alongside the markdown. `.dovo/catalog/blueprints/feature-dev.yml` reads it to ask the human which open questions to answer, so it must agree with the plan's **Open questions** block entry for entry. The slice flow also reads its `contract` and `slices` to write the epic's contract and work plan, so they must agree with the plan's `## Feature contract` and `## Slices` sections. The example below is a slice-mode plan for a plain issue.

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
  "decisions": [
    {
      "id": "D-1",
      "was": null,
      "decision": "The list reuses `dovo worktree list`'s empty-state line rather than the issue's wording.",
      "settled_by": "planner",
      "rejected": ["The issue's \"No sessions.\" line."],
      "phases": [2],
      "date": "2026-10-29"
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
      "size": "s",
      "stub": null
    }
  ]
}
```

- `issue` is the issue number, or `null` when planning from a plain-language request. `grounded_at` is the short sha from the plan's "Grounded against" line. `phases` is the number of rows in the Phases table.
- `open_questions` lists every numbered Open Question in order, each with the `OQ-<n>` id from `plan.md`. `summary` is the one-line question followed by ` (assumed: <short answer>)`, since it is the only text the blueprint's pick-list shows for the question; `assumed` is the full answer the plan is written against, `why` the reason with its `path:line`, `alternatives` the rejected answers (`[]` when none), and `phases` the phase numbers whose plan changes if the answer changes.
- `open_questions` is `[]` when the plan says "None." On an epic, each item may add `"blocks": ["S3"]`, the slices it blocks. In slice mode, a question raised from a `to_confirm` prior decision (Step 1b) adds `"replaces": {"id": "<its id>", "session": "<its session>"}`, copied from `.agentic/prior-decisions.json`.
- `decisions` lists every choice the plan is written against that the issue does not make, `[]` when there is none. The planner writes one per departure in a phase's `### Decisions` section, with `settled_by` `"planner"` (the human approves it with the plan) and `was` `null`. The blueprint's `fold-answers` step later settles each open question as a decision with `settled_by` `"human"` (adding `answer`, the human's words) or `"assumed_accepted"`, and `was` the question's id. `id` is `D-<n>`, continuing after the highest decision id in the issue or in `.agentic/prior-decisions.json`; `decision` is one sentence stating the choice; `rejected` the alternatives not taken (`[]` when none); `phases` the phase numbers it shapes; `date` the day it was settled, as `YYYY-MM-DD`; `replaces` the `replaces` of the question it settles, only when that question has one. Name the decision's id in its `### Decisions` bullet. Once the plan is approved, the blueprint's `decisions-comment` step posts the decisions to the child issue as one comment.
- `contract` is `null` on a plain issue. On an epic it is exactly the `contract` object in [contract-template.md](templates/contract-template.md) under "`plan.json` shape", agreeing with the plan's `## Feature contract`. A section left out of the markdown keeps its key, as `[]` (or `null` for `inventory`).
- `slices` lists every slice in dependency order, one per row of the plan's `## Slices` table. `id` is `S<n>`, unless the issue's body already holds a hand-maintained `## Work plan` table (one without a `dovo:workplan` comment): then every row of that table keeps its id (such as `A1`) on the slice it becomes, since the work plan script adopts the table by id, carrying each row's child and status, and refuses a row no slice matches; `phases` the phase numbers it lands; `depends_on` slice ids; `blocked_by` prerequisite issue numbers outside the epic; `child` the child issue number, or `null` until it is created; `owns` the surfaces and aspects the slice owns in the ownership table; `lint_gates` the `tests/lint/` checks it lands; `estimate` the `src_docs` and `tests` changed-line estimates with their `basis`; `size` the slice's review size, `s`, `m` or `l` (Step 3a), which the work plan stores so `workplan.py next` can label the child; `stub` the child issue's markdown body when `child` is `null`, otherwise `null`.
- On an epic, exactly one slice has `pilot: true` and only the last has `closing_docs: true`. A plain issue's one slice has both `false`, `child` equal to `issue`, `size` matching the issue's `dovo-size-*` label and `stub` `null`.
- These fields only add to the shape above: `issue`, `plan`, `grounded_at`, `phases`, `open_questions` and `decisions` keep their meaning.
- **Issue mode** writes no phases: `phases` is `0`, every slice's `phases` is `[]`, and each open question's `phases` is `[]`, with `blocks` naming the slices whose scope changes if the answer changes.
- **Slice mode** keeps `issue`, `contract` and `slices` exactly as in `.agentic/epic-plan.json`, except that this slice's `phases` lists the phase numbers of this plan's Phases table and every other slice's `phases` is `[]`. `grounded_at`, `phases`, `open_questions` and `decisions` are this slice's own: `open_questions` lists only the questions this plan raises, and `decisions` only this plan's, replacing any an earlier slice left in `.agentic/epic-plan.json`. The epic's open questions belong to the epic plan and stay listed on the epic's body, open with their assumed answers, until a slice's report settles one by adding a `contract.decisions` entry with the question's id, or the epic is re-sliced.

## 6. Hand off

Run planning.md Step 5. Report the paths written, the Slices table (issue mode) or the Phases table (slice mode), a one-paragraph summary, every numbered Open Question restated in chat, and plainly that this was planning only: nothing was implemented, tested, committed or pushed.

**A human reviews the plan before implementation.** Stop and wait. First `issue-plan-review` reviews it and `plan-challenge` challenges its design, each in a fresh session; then `plan-walkthrough` explains the result to the approver. An approved issue-mode plan becomes the issue's work plan; each `issue-code` run then phases its slice with `/issue-plan --slice <id>`, has that plan reviewed and approved the same way, and implements it with `/issue-code --slice <id> --phase <n>`, one phase per agent session, committing each phase, one slice per run, in order.
