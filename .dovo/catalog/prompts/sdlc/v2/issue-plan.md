Forked from `dovo-plan` at `de256118`. This copy is temporary: once the slice flow works, it replaces `dovo-plan`.

# issue-plan

Produce a plan precise enough that a different agent holding only the issue and the plan, with no memory of this session, could implement each phase correctly, and a reviewer could check each phase's diff against it. Do not write or modify implementation code; the plan is the entire deliverable.

[docs/agents/planning.md](../../../../../docs/agents/planning.md) is the authority for plan content: its steps, artifact checklist, phasing rules, code sample rules and template are not repeated here.

Plan when the issue adds or changes a command, DTO/`*Result` model, status enum, domain exception, service, entrypoint method, formatter, config key, schema field, or `core/db/` table. Skip only for a single-file change with no new surface.

## Modes

| Invocation | Input | Job |
|---|---|---|
| `/issue-plan [<issue-number>]` | the issue | **Issue mode.** On an epic, write the feature contract and the slice table; on a plain issue, write one slice. Every slice gets its scope, dependencies, outcomes, ownership, estimate and size, and **no phases**. Steps 1, 2, 2a, 3, 3a, 4, 5 and 6. |
| `/issue-plan --slice <id>` | `.agentic/epic-plan.md` and `.agentic/epic-plan.json`, the epic plan as downloaded from the work plan's plan session, or as the same `issue-plan-v2` run settled it for a plan of one slice; the epic's work plan comment; the slice's issue | **Slice mode.** Phase that one slice, against `origin/main` as it is now, and nothing else. Steps 1b, 2, 3, 4, 5 and 6. |

Detail is written just in time: a slice's phases are planned when its `issue-code` run starts, after the slices before it have landed and their "as landed" reports have updated the contract, never when the issue is sliced. A phase written early goes stale as slices land. The one exception is a plan of one slice: no slice lands before it, so the `issue-plan-v2` run that sliced the issue phases it in slice mode straight away, and asks for one approval of both plans.

## Slices and epics

- **A slice is one PR.** A phase stays what planning.md says: a reviewable chunk inside a slice. Where planning.md says the phases land in one PR, read "the phases of one slice land in one PR".
- **An epic** is an issue whose work plan comment holds, or will hold, `## Feature contract` and `## Work plan`. The work plan comment is the one comment on the issue whose first line is exactly `<!-- dovo:workplan schema_version=1 -->` (`gh issue view <n> --json comments`); the issue's description stays what a person wrote. Epics never nest: an epic is never a slice of another epic.
- **A plain issue** is any other issue. It gets a one-slice plan with `contract: null`. Once slice mode phases it, that plan behaves like a `dovo-plan` plan.
- **No slice gets a new issue.** Every slice is tracked by its row in the work plan comment, and its PR references the issue. An existing sub-issue mapped to a slice (Step 2a) is recorded as that slice's `child`, and the flow still works the slice. `workplan.py split-out` gives one slice its own issue only when a person asks, for a slice needing its own thread; `split-out --handed-off` also marks a slice handed to someone outside the flow, which `workplan.py next` never picks. This prompt never plans either.
- **Closing keywords:** a slice with its own issue ends its PR with `Closes #<child>`. Every slice PR then ends with `Closes #<issue>` when every other slice has merged, or was handed off to an issue that is now closed, and with `Part of #<issue> (slice <id> of <count>)` otherwise. `workplan.py next` writes the lines, and `land` writes them again from the plan as landed.

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

Slice the issue when it needs more than one PR, or when its changes share a surface with other open work, such as its sub-issues or the issues the user names; that work becomes its slices or gets a role (Step 2a). A sliced issue is an epic, with a contract as short as what its slices share: a plan of two or more slices always has one, since its Shared-surface ownership names the one owner of each surface the slices share. Otherwise plan a plain issue, one slice. The `type:epic` label follows from the plan: the blueprint's `label-epic` step adds it once the plan has two or more slices. An issue already labelled `type:epic` (`gh issue view <n> --json labels --jq '.labels[].name'`), or whose work plan comment already holds `## Feature contract`, is an epic.

Read each related issue in full before giving it a role in `existing_issues`, not only its title and blockers:

```bash
gh issue view <m> --json title,body,labels,milestone,state
gh issue view <m> --json blockedBy --jq '.blockedBy.nodes[] | "#\(.number) \(.state)"'
```

Then reset, keeping only the prior decisions:

```bash
mkdir -p .agentic && find .agentic -mindepth 1 -maxdepth 1 ! -name prior-decisions.json -exec rm -rf {} +
```

A stale plan or review must never be read as current. Then read the prior decisions, `.agentic/prior-decisions.json`, the decisions earlier planning sessions posted to the issue. The blueprint's `prior-decisions` step writes it before this one; when run by hand, write it after the reset with `uv run python .dovo/catalog/scripts/sdlc/v2/plan.py prior-decisions --issue <n> --out .agentic/prior-decisions.json`. A decision marked `given` binds this plan like the issue does: plan against it and never raise it again as an open question. A decision marked `to_confirm` was settled before the issue's description last changed: raise it as an open question whose `assumed` answer is the earlier decision, with `replaces` naming it (Step 5).

## 1b. Load the slice (slice mode)

Never reset `.agentic/` in slice mode: the epic plan, `.agentic/epic-plan.md` and `.agentic/epic-plan.json`, is this mode's input. The `issue-code-v2` blueprint copies both from the downloaded plan, recording the slice's own issue as its `child` when the work plan's row has one the downloaded plan lacks; the `issue-plan-v2` blueprint copies both from the issue plan it has just settled, when that plan has one slice. If either is missing, stop and say so. When run by hand, copy the downloaded `.agentic/plan.md` and `.agentic/plan.json` to them first, setting the slice's `child` in the same way. Never edit the epic plan. Start every run from it, so a retry never builds on its own partial output, and delete the files an earlier planning round in this workspace could leave behind, so none is read as current:

```bash
cp .agentic/epic-plan.md .agentic/plan.md && cp .agentic/epic-plan.json .agentic/plan.json
rm -f .agentic/plan-review.md .agentic/plan-review.json .agentic/plan-challenge.md .agentic/plan-challenge.json .agentic/plan-walkthrough.md .agentic/evidence.md
```

Then read, from the epic plan, and keep in view for the whole run:

- **The slice:** the `slices` entry whose `id` is `<id>`. An id not in `slices` stops the run and names the valid ids. `plan.json` `issue` is the epic, or the plain issue itself. The slice's issue is its `child`, an issue of its own, when it has one, and otherwise `plan.json` `issue`: the slice is worked on it, its decisions are posted to it, and its PR references it.
- **The epic's binding contract:** `plan.json` `contract` (none when it is `null`) and the plan's `## Feature contract`, with the ownership rows of this slice and of the slices it depends on.
- **The "as landed" reports:** the `### <id> as landed (#<pr>)` sections under `## Work plan` in the epic's work plan comment (`gh issue view <epic> --json comments`). They record how earlier slices departed from the plan and which later slices they affect. A slice whose landing changed the contract amended it when it landed, so a report that still conflicts with `contract` is raised as an Open Question, never settled silently.
- **The slice's issue:** `gh issue view <n> --json number,title,body`, read live, since it was readied just before this step. When it is the slice's own issue, or the plan has one slice, its outcomes (`O-n`) and decisions (`D-n`) are the requirements this slice's phases answer. When it is `plan.json` `issue` and the plan has two or more slices, only the outcomes the slice's `outcomes` lists are; the issue's decisions bind every slice, so plan against them, but they are not this slice's requirements. Its binding values are the literals the phases keep, and its **Out of scope** items are its collision checks; on a legacy issue, its FRs, NFRs, guards and Definition of done are the requirements and its **Owned elsewhere** pointers the collision checks.
- **The requirements:** `.agentic/requirements.json`, the slice's numbered items (`O-n` and `D-n`, or `FR-n`, `NFR-n` and `EC-n` on a legacy issue) with their exact text, as the item above scopes them. An outcome the slice delivers only part of carries that part as its `part`; plan only that part. The blueprint's `requirements` step writes it just before this one; when run by hand, write it first with `uv run python .dovo/catalog/scripts/sdlc/v2/plan.py requirements --issue <n> --slice <id> --plan .agentic/epic-plan.json --out .agentic/requirements.json`. Cite each requirement by its id there, in the Phases table and the test lines: the `breakdown` step quotes them from it into each phase's file, and its check refuses an item no phase answers.
- **The prior decisions:** `.agentic/prior-decisions.json`, the decisions earlier planning sessions posted to the slice's issue: on a sliced issue, the slicing's and earlier slices'. The blueprint's `prior-decisions` step writes it before this one; when run by hand, write it first with `uv run python .dovo/catalog/scripts/sdlc/v2/plan.py prior-decisions --issue <n> --out .agentic/prior-decisions.json`. A decision marked `given` binds this plan like the issue does: plan against it and never raise it again as an open question. A decision marked `to_confirm` was settled before the issue's description last changed: raise it as an open question whose `assumed` answer is the earlier decision, with `replaces` naming it (Step 5).
- **The issue plan's decisions:** `.agentic/epic-plan.json` `decisions` and `contract.decisions`, settled when the issue was planned, and, for a slice worked on its own issue, the `given` decisions in `.agentic/prior-decisions-parent.json`, those posted to the issue whose work plan holds the slice. They bind this plan like decisions marked `given`, also when the same `issue-plan-v2` run has not posted them yet: plan against them and never raise them again as open questions, except a `to_confirm` one, which you raise with `replaces` as Step 1 says. The plan review reports a phase that contradicts one.

The downloaded plan may come from an earlier slice's code session, since `land` repoints the work plan there when that slice's report amends the plan. Its phases, tests and open questions belong to that earlier slice: never carry them into this plan.

## 2. Ground

Follow planning.md Steps 1-2: read the issue's contract (or the user's request, absent an issue number; in slice mode, the slice's issue and the epic's binding contract from Step 1b), read [AGENTS.md](../../../../../AGENTS.md) for the rules and exemplars, and ground every touched surface in the current default branch with `file:line` citations. Read the callers of everything the change touches: changed behaviour for existing users, existing bugs on the path, and the compatibility of persisted formats are the findings a reviewer most needs and the issue never states.

## 2a. Write the feature contract (epic, issue mode only)

Skip this on a plain issue and in slice mode, which never changes the contract. On an epic, write the contract before any phase or slice, following [contract-template.md](templates/contract-template.md): its sections in order, the rules every section follows, its section guidance, and "Mapping existing issues", which gives every sub-issue and related open issue a role (epic, child, split, superseded container, prerequisite or sibling epic) before any slice is written.

Keep the contract as short as the surface spread allows. Problem, Target contract and Shared-surface ownership are required, alongside the slices. Every other section is written only when it has something to say, and left out otherwise, never filled with "none". Cite rules already in `docs/agents/architecture/` instead of re-deciding them. When written, the code inventory comes from commands run at the grounding sha, never estimates. Each open question carries a recommended answer and the slices it blocks.

## 3. Size and phase

In issue mode, size only: ground enough to estimate the changed lines each slice needs, `src/` plus docs and tests separately, and write no phases. In slice mode, phase this slice only; when grounding shows the slice mixes kinds of change, or is over its budget, say so first under What you're approving and recommend re-slicing the epic with `/issue-plan <epic>`:

Follow planning.md Step 3. Estimate the changed lines per FR and group FRs into phases of one concern each, roughly 400 changed lines or less, each passing the quality gate on its own. Replacing an internal is phased as add, switch callers, delete, with the deletion phase planned now. A change that fits one phase stays one phase. The phases of one slice land in one PR, so together they serve that slice's one responsibility. When grounding shows a plain issue mixes kinds of change (a foundation and its adoption, or a behaviour change and a refactor), say so first under What you're approving and recommend planning it as an epic.

## 3a. Slice the issue (issue mode)

On a plain issue, the whole issue is one slice, `S1`, delivering every outcome of the issue. On an epic:

- **Size each slice.** `src/` plus docs stay under about 400 changed lines, and the whole PR, tests included, under about 900. Tests are estimated separately. A slice over budget says why it cannot be split.
- **Give each slice a review size**, its `size`, by the descriptions of the repository's `dovo-size-*` labels: `s` for one area and a few files, `m` for a couple of areas, `l` for many areas (packages, formats, command groups, skills, catalog or scripts). The work plan stores it, and `workplan.py split-out` labels a slice's own issue `dovo-size-<size>` when a person splits one out. A plain issue's one slice takes the size of the issue's own `dovo-size-*` label.
- **One responsibility per slice.** A slice never delivers a shape another slice must reverse, and every surface two slices touch has one owner in the contract's Shared-surface ownership table; the other slice says "after Sx" and does not restate it.
- **Order by dependency.** `depends_on` names slice ids, `blocked_by` names prerequisite issues outside the epic, and the graph is acyclic.
- **Slice 1 is the pilot**, unless another slice's conventions are reused by more slices; then that slice is the pilot. Its "as landed" report updates the conventions and decision log before later slices are written in full.
- **The last slice is the closing docs slice.** It reviews every doc the feature touched against `main`, and its PR, the last to land, closes the issue.
- **Place the checkpoints** when the epic has three or more slices: after the pilot, after slices that share an owned surface, and a baseline before the closing docs slice.
- **List each slice's outcomes**, its `outcomes`: the ids of the issue's outcomes (`O-<n>`) the slice delivers, which its PR is reviewed against. Every outcome belongs to exactly one slice. An outcome whose parts land in different slices is listed in each of them with its part in parentheses (`O-7 (reply)`, `O-7 (fixer)`), as Step 4 does for a requirement split across phases. `plan.py check-slices` refuses a plan that leaves an outcome to no slice or lists it in two without that marker. A legacy issue, which numbers no outcomes, gives every slice `[]`.

## 4. Write the plan

In issue mode, `.agentic/plan.md` holds, in this order: the header and grounding line from planning.md Step 4 (its opening paragraph naming Traceability, not the Phases table, as the requirement map), What you're approving (open questions with their assumed answers, changes to existing behaviour, what stays unchanged), on an epic the `## Feature contract` from Step 2a, Owned elsewhere, a `## Slices` table (id, title, outcomes, depends on, blocked by, pilot or closing docs, estimate, size), and Traceability mapping each requirement of the issue to the slice that lands it. It has no Phases table, Artifact inventory or phase sections; say under What you're approving that each slice is phased and approved in its own `issue-code` run, or, for a plan of one slice, later in this same run.

In slice mode, the header names the slice's issue, `# Issue #<n>: <its title>`, and the line after it names the slice and the epic: `Slice <id> of #<epic>.` Copy the epic plan's `## Feature contract` and `## Slices` sections, and its `## Instructions from the approver` when present, verbatim after What you're approving: they are the epic's, and this plan never changes them. The rest follows the paragraph below, for this slice alone.

Structure `.agentic/plan.md` per planning.md Step 4, in this order: What you're approving (open questions with their assumed answers, and changes to existing behaviour), Traps (edge cases included), Owned elsewhere, the Phases table with its deletion ledger, Artifact inventory (every file and symbol each phase touches), one section per phase, and cross-cutting doc updates. Each phase section holds `### Code` (only new public surface the issue does not pin literally), `### Decisions` (only departures from the issue or the nearest existing pattern) and `### Tests` (one line per test, citing the requirements it pins, with the R13 matrix for each command). Link the issue rather than copying its prose, and cite the issue section that holds a literal contract rather than copying it. There is no Traceability, GWT Scenarios or per-phase Instructions section: the Phases table's FRs column and the test lines map every requirement. A requirement whose parts land in different phases is listed in each of those phases' FRs cells with its part in parentheses (`FR-7 (reply)`, `FR-7 (fixer)`), and each part is cited by a test line; before finishing, split each outcome and decision (on a legacy issue, each FR, NFR and error case) into its obligations and check that each lands in a phase.

When grounding shows one of the issue's internal shapes is wrong, follow planning.md Step 1: plan the better design and name the departure in its phase's `### Decisions`, with the `path:line` that shows why, as a `planner` decision (Step 5). Raise it as an Open Question instead only when the human must weigh a trade-off, and then write no `planner` decision for it. Only the issue's observable contracts are binding; an issue sentence is not by itself a reason to plan a duplicate symbol or a dependency the code holds but never calls. A guard ("same message as today") binds only what a user or caller relies on, and planning.md Step 1 allows a named departure from it in the same way.

When the issue leaves a detail unspecified, choose the option consistent with the nearest existing pattern, record the rejected alternative, and add a numbered Open Question stating the assumed answer rather than stalling. Ask at most one clarifying question when a tradeoff needs confirmation first.

## 5. Emit the machine-readable open questions

Write `.agentic/plan.json` alongside the markdown. `.dovo/catalog/blueprints/issue-code-v2.yml` reads it to ask the human which open questions to answer, so it must agree with the plan's **Open questions** block entry for entry. The slice flow also reads its `contract` and `slices` to write the contract and work plan into the issue's work plan comment, so they must agree with the plan's `## Feature contract` and `## Slices` sections. The example below is a slice-mode plan for a plain issue.

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
      "outcomes": ["O-1", "O-2"],
      "owns": [],
      "lint_gates": [],
      "pilot": false,
      "closing_docs": false,
      "estimate": { "src_docs": 180, "tests": 220, "basis": "Phases table estimates" },
      "size": "s"
    }
  ]
}
```

- `issue` is the issue number, or `null` when planning from a plain-language request. `grounded_at` is the short sha from the plan's "Grounded against" line. `phases` is the number of rows in the Phases table.
- `open_questions` lists every numbered Open Question in order, each with the `OQ-<n>` id from `plan.md`. `summary` is the one-line question followed by ` (assumed: <short answer>)`, since it is the only text the blueprint's pick-list shows for the question; `assumed` is the full answer the plan is written against, `why` the reason with its `path:line`, `alternatives` the rejected answers (`[]` when none), and `phases` the phase numbers whose plan changes if the answer changes.
- `open_questions` is `[]` when the plan says "None." On an epic, each item may add `"blocks": ["S3"]`, the slices it blocks. A question raised from a `to_confirm` prior decision (Step 1 or 1b) adds `"replaces": {"id": "<its id>", "session": "<its session>"}`, copied from `.agentic/prior-decisions.json`.
- `decisions` lists every choice the plan is written against that the issue does not make, `[]` when there is none. The planner writes one per departure in a phase's `### Decisions` section, with `settled_by` `"planner"` (the human approves it with the plan) and `was` `null`; a departure raised as an Open Question (Step 4) gets none, since `fold-answers` settles it. The blueprint's `fold-answers` step later settles each open question as a decision with `settled_by` `"human"` (adding `answer`, the human's words) or `"assumed_accepted"`, and `was` the question's id. `id` is `D-<n>`, continuing after the highest decision id in the issue or in `.agentic/prior-decisions.json`, and in slice mode in `.agentic/epic-plan.json` `decisions`; `decision` is one sentence stating the choice; `rejected` the alternatives not taken (`[]` when none); `phases` the phase numbers it shapes; `date` the day it was settled, as `YYYY-MM-DD`; `replaces` the `replaces` of the question it settles, only when that question has one. Name the decision's id in its `### Decisions` bullet. Once the plan is approved, the blueprint's `decisions-comment` step posts the decisions as one comment: to the issue in issue mode, and to the slice's issue in slice mode.
- `contract` is `null` on a plain issue. On an epic it is exactly the `contract` object in [contract-template.md](templates/contract-template.md) under "`plan.json` shape", agreeing with the plan's `## Feature contract`. A section left out of the markdown keeps its key, as `[]` (or `null` for `inventory`).
- `slices` lists every slice in dependency order, one per row of the plan's `## Slices` table. `id` is `S<n>`; `phases` the phase numbers it lands; `depends_on` slice ids; `blocked_by` prerequisite issue numbers outside the epic; `outcomes` the issue's outcome ids it delivers (Step 3a); `owns` the surfaces and aspects the slice owns in the ownership table; `lint_gates` the `tests/lint/` checks it lands; `estimate` the `src_docs` and `tests` changed-line estimates with their `basis`; `size` the slice's review size, `s`, `m` or `l` (Step 3a), which the work plan stores; `child`, only on a slice with an issue of its own (an existing sub-issue mapped to it, or one `workplan.py split-out` gave it), that issue's number, which the work plan records on the slice's row.
- On an epic, exactly one slice has `pilot: true` and only the last has `closing_docs: true`. A plain issue's one slice has both `false`, `outcomes` listing every outcome of the issue and `size` matching the issue's `dovo-size-*` label.
- These fields only add to the shape above: `issue`, `plan`, `grounded_at`, `phases`, `open_questions` and `decisions` keep their meaning.
- **Issue mode** writes no phases: `phases` is `0`, every slice's `phases` is `[]`, and each open question's `phases` is `[]`, with `blocks` naming the slices whose scope changes if the answer changes.
- **Slice mode** keeps `issue`, `contract` and `slices` exactly as in `.agentic/epic-plan.json`, except that this slice's `phases` lists the phase numbers of this plan's Phases table and every other slice's `phases` is `[]`. `grounded_at`, `phases`, `open_questions` and `decisions` are this slice's own: `open_questions` lists only the questions this plan raises, and `decisions` only this plan's, replacing any an earlier slice left in `.agentic/epic-plan.json`. The epic plan's own open questions were all settled as decisions before it was approved. A plan of two or more slices posted them to the epic when it was approved; a one-slice plan phased in the same `issue-plan-v2` run posts them with this slice's once both are approved.

## 6. Hand off

Run planning.md Step 5. Report the paths written, the Slices table (issue mode) or the Phases table (slice mode), a one-paragraph summary, every numbered Open Question restated in chat, and plainly that this was planning only: nothing was implemented, tested, committed or pushed.

**A human reviews the plan before implementation.** Stop and wait. An issue-mode plan is challenged by `plan-challenge` in a fresh session and never reviewed, so check the epic ready criteria in [contract-template.md](templates/contract-template.md) before handing off. A slice-mode plan is reviewed by `issue-plan-review` in a fresh session, and never challenged. `plan-walkthrough` then explains the result to the approver. A plan of one slice is phased in the same `issue-plan-v2` run and approved once with the issue plan; its `issue-code` run codes the approved phase files without planning. Otherwise the approved issue-mode plan becomes the issue's work plan, and each `issue-code` run phases its slice with `/issue-plan --slice <id>`, has that plan reviewed, broken into one file per phase and approved. Either way the slice is implemented with `/issue-code --slice <id> --phase <n>`, one phase per agent session, committing each phase, one slice per run, in order.
