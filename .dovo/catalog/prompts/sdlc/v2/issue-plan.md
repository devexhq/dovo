Forked from `dovo-plan` at `de256118`. This copy is temporary: once the slice flow works, it replaces `dovo-plan`.

# issue-plan

Produce a plan precise enough that a different agent holding only the issue and the plan, with no memory of this session, could implement each phase correctly, and a reviewer could check each phase's diff against it. Do not write or modify implementation code; the plan is the entire deliverable.

[docs/agents/planning.md](../../../../../docs/agents/planning.md) is the authority for plan content: its steps, artifact checklist, phasing rules, code sample rules and template are not repeated here.

Plan when the issue adds or changes a command, DTO/`*Result` model, status enum, domain exception, service, entrypoint method, formatter, config key, schema field, or `core/db/` table. Skip only for a single-file change with no new surface.

## Modes

This file is the core both modes share. Each mode adds its own steps in its own file beside it, numbered as one sequence with this file's: read this file and your mode's file, and never the other mode's.

| Invocation | Input | Job |
|---|---|---|
| `/issue-plan [<issue-number>]` | the issue | **Issue mode**, [issue-plan-issue.md](issue-plan-issue.md). On an epic, write the feature contract and the slice table; on a plain issue, write one slice. Every slice gets its scope, dependencies, outcomes, ownership, estimate and size, and **no phases**. Steps 1, 2, 2a, 3, 3a, 4, 5 and 6. |
| `/issue-plan --slice <id>` | `.agentic/epic-plan.md` and `.agentic/epic-plan.json`, the epic plan as downloaded from the work plan's plan session, or as the same `issue-plan-v2` run settled it for a plan of one slice; the earlier slices' "as landed" reports; the slice's issue | **Slice mode**, [issue-plan-slice.md](issue-plan-slice.md). Phase that one slice, against `origin/main` as it is now, and nothing else. Steps 1b, 2, 3, 4, 5 and 6. |

Detail is written just in time: a slice's phases are planned when its `issue-code-v2` run starts, after the slices before it have landed and their "as landed" reports have updated the contract, never when the issue is sliced. A phase written early goes stale as slices land. The one exception is a plan of one slice: no slice lands before it, so the `issue-plan-v2` run that sliced the issue phases it in slice mode straight away, and asks for one approval of both plans.

## Slices and epics

- **A slice is one PR.** A phase stays what planning.md says: a reviewable chunk inside a slice. Where planning.md says the phases land in one PR, read "the phases of one slice land in one PR".
- **An epic** is an issue planned into two or more slices; its plan has a feature contract. Epics never nest: an epic is never a slice of another epic.
- **A plain issue** is any other issue. It gets a one-slice plan with `contract: null`. Once slice mode phases it, that plan behaves like a `dovo-plan` plan.

## Hard boundaries

- **Never run tooling that mutates or validates.** Read-only `git` and read-only `gh` are the only commands you need. The only exceptions are the `.agentic/` workspace reset in Step 1, the epic-plan copy and stale-file cleanup in Step 1b, and `plan.py questions` in Step 5.
- **Never edit `src/` or `tests/`, commit, push or touch PR state.**
- **Never plan from memory.** Every path, symbol and signature in the plan comes from a file read this session.
- **Never plan a test whose contract is already pinned elsewhere.** Name what each new test uniquely pins.

## 2. Ground

Follow planning.md Steps 1-2: read the issue's contract (or the user's request, absent an issue number; in slice mode, the slice's issue and the epic's binding contract from Step 1b), read [AGENTS.md](../../../../../AGENTS.md) for the rules and exemplars, and ground every touched surface in the current default branch with `file:line` citations. Read the callers of everything the change touches: changed behaviour for existing users, existing bugs on the path, and the compatibility of persisted formats are the findings a reviewer most needs and the issue never states.

## 3. Size and phase

Your mode's file says first what this step covers in that mode. Then:

Follow planning.md Step 3. Estimate the changed lines per requirement and group requirements into phases of one concern each, roughly 400 changed lines or less, each passing the quality gate on its own. Replacing an internal is phased as add, switch callers, delete, with the deletion phase planned now. A change that fits one phase stays one phase. The phases of one slice land in one PR, so together they serve that slice's one responsibility. When grounding shows a plain issue mixes kinds of change (a foundation and its adoption, or a behaviour change and a refactor), raise an open question recommending that it be planned as an epic.

## 4. Write the plan

`.agentic/plan.md` is read only by agents: the review, challenge, breakdown, code, report and walkthrough steps, and `plan.py check-breakdown`. A person reads the summary the blueprint builds from `plan.json`, and the walkthrough. The plan leaves out planning.md's What you're approving section: open questions live only in `plan.json` `open_questions` and decisions only in `plan.json` `decisions` and the contract's Decisions (Step 5). Never restate either in the markdown. The changes to existing behaviour that section held get their own `## Changes to existing behaviour` section, in planning.md's numbered form, or "None: new surface only."

Your mode's file says which sections `.agentic/plan.md` holds, and in which order.

When grounding shows one of the issue's internal shapes is wrong, follow planning.md Step 1: plan the better design and name the departure in its phase's `### Decisions`, with the `path:line` that shows why, as a `planner` decision (Step 5). Raise it as an open question instead only when the human must weigh a trade-off, and then write no `planner` decision for it. Only the issue's observable contracts are binding; an issue sentence is not by itself a reason to plan a duplicate symbol or a dependency the code holds but never calls. A guard ("same message as today") binds only what a user or caller relies on, and planning.md Step 1 allows a named departure from it in the same way.

When the issue leaves a detail unspecified, choose the option consistent with the nearest existing pattern, record the rejected alternative, and add an open question to `plan.json` stating the assumed answer rather than stalling. A phase whose plan depends on it cites its id (`[See OQ-<n>.]`). Ask at most one clarifying question when a tradeoff needs confirmation first.

## 5. Write plan.json

Write `.agentic/plan.json` alongside the markdown. It is the only place the open questions and decisions are written. The blueprints build the summary a person reads from it and ask the person which open questions to answer. The slice flow also reads its `slices` to write the work plan comment and its `decisions` to write each slice's own issue, and the next run reads its `contract`, so they must agree with the plan's `## Feature contract` and `## Slices` sections. The example below is a slice-mode plan for a plain issue.

```json
{
  "issue": 914,
  "plan": ".agentic/plan.md",
  "grounded_at": "8eb591cd",
  "phases": 2,
  "open_questions": [
    {
      "id": "OQ-1",
      "summary": "Should `dovo session list` include discarded sessions by default?",
      "assumed": "No: discarded sessions are hidden unless `--all` is passed.",
      "details": [
        "`dovo worktree list` hides removed worktrees (`worktree_list.py:41`).",
        "Scripts that count sessions would change their totals if they were shown."
      ],
      "challenged": false,
      "challenge_answer": "",
      "challenge_details": [],
      "recommendation": "assumed",
      "alternatives": ["Show them with a `discarded` status column."],
      "phases": [1],
      "blocks": []
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
- `open_questions` lists every open question in order, ids `OQ-1`, `OQ-2` and so on, or `[]` when there is none. Each has exactly these fields:
  - `summary`: the question, one line of at most 120 characters. It is the label the blueprint's pick-list shows.
  - `assumed`: the answer the plan is written against, at most 200 characters.
  - `details`: context and why the answer matters, as at most 5 one-line items of at most 140 characters each, with `path:line` where it applies. No paragraph prose.
  - `challenged`: `false` when the planner writes the question. The `fold-challenges` step sets it to `true` when the plan challenge disputes the assumed answer.
  - `challenge_answer`: `""`, until `fold-challenges` writes the answer the challenge proposes, one line of at most 200 characters.
  - `challenge_details`: `[]`, until `fold-challenges` writes the challenger's case here in the same caps as `details`.
  - `recommendation`: `"assumed"`, until `fold-challenges` weighs a challenge and may set it to `"challenge"`. A question nobody answers settles to the recommended answer.
  - `alternatives`: the other options, one line each (`[]` when none), never the assumed or the challenge's answer.
  - `phases`: the phase numbers whose plan changes if the answer changes. `blocks`: the slice ids whose scope changes, on an epic, and otherwise `[]`.
  - `replaces`, only on a question raised from a `to_confirm` prior decision (Step 1 or 1b): `{"id": "<its id>", "session": "<its session>"}`, copied from `.agentic/prior-decisions.json`.
- Before you hand off, run `uv run --quiet python .dovo/catalog/scripts/sdlc/v2/plan.py questions` and fix every problem it names: it refuses a question that breaks this shape or its caps, naming the question and the field.
- `decisions` lists every choice the plan is written against that the issue does not make, `[]` when there is none. The planner writes one per departure in a phase's `### Decisions` section, with `settled_by` `"planner"` (the human approves it with the plan) and `was` `null`; a departure raised as an open question (Step 4) gets none, since `fold-answers` settles it. The blueprint's `fold-answers` step later settles each open question as a decision with `settled_by` `"human"` (adding `answer`, the human's words), or, for a question the human left to its recommendation, `"assumed_accepted"` or `"challenge_accepted"`, and `was` the question's id. `id` is `D-<n>`, continuing after the highest decision id in the issue or in `.agentic/prior-decisions.json`, and in slice mode in `.agentic/epic-plan.json` `decisions`; `decision` is one sentence stating the choice; `rejected` the alternatives not taken (`[]` when none); `phases` the phase numbers it shapes; `date` the day it was settled, as `YYYY-MM-DD`; `replaces` the `replaces` of the question it settles, only when that question has one. Name the decision's id in its `### Decisions` bullet. The decisions stay in plan.json, which the session publishes; nothing posts them to the issue.
- `contract` is `null` on a plain issue. On an epic it is exactly the `contract` object in [contract-template.md](templates/contract-template.md) under "`plan.json` shape", agreeing with the plan's `## Feature contract`. A section left out of the markdown keeps its key, as `[]` (or `null` for `inventory`).
- `slices` lists every slice in dependency order, one per row of the plan's `## Slices` table. `id` is `S<n>`; `phases` the phase numbers it lands; `depends_on` slice ids; `blocked_by` prerequisite issue numbers outside the epic; `outcomes` the issue's outcome ids it delivers (Step 3a); `owns` the surfaces and aspects the slice owns in the ownership table; `lint_gates` the `tests/lint/` checks it lands; `estimate` the `src_docs` and `tests` changed-line estimates with their `basis`; `size` the slice's review size, `s`, `m` or `l` (Step 3a), which the work plan stores; `child`, only on a slice with an issue of its own (an existing sub-issue mapped to it, or one `workplan.py start` or `split-out` gave it), that issue's number, which the work plan records on the slice's row.
- On an epic, exactly one slice has `pilot: true` and only the last has `closing_docs: true`. A plain issue's one slice has both `false`, `outcomes` listing every outcome of the issue and `size` its review size.
- These fields only add to the shape above: `issue`, `plan`, `grounded_at`, `phases`, `open_questions` and `decisions` keep their meaning.
- **Issue mode** writes no phases: `phases` is `0`, every slice's `phases` is `[]`, and each open question's `phases` is `[]`, with `blocks` naming the slices whose scope changes if the answer changes.
- **Slice mode** keeps `issue`, `contract` and `slices` exactly as in `.agentic/epic-plan.json`, except that this slice's `phases` lists the phase numbers of this plan's Phases table and every other slice's `phases` is `[]`. `grounded_at`, `phases`, `open_questions` and `decisions` are this slice's own: `open_questions` lists only the questions this plan raises, and `decisions` only this plan's, replacing any an earlier slice left in `.agentic/epic-plan.json`. The epic plan's own open questions were all settled as decisions before it was approved, and stay in `.agentic/epic-plan.json`.

## 6. Hand off

Run planning.md Step 5. Report the paths written, the Slices table (issue mode) or the Phases table (slice mode), a one-paragraph summary, each open question's summary and assumed answer, and plainly that this was planning only: nothing was implemented, tested, committed or pushed.

**A human reviews the plan before implementation.** Stop and wait. An issue-mode plan is challenged by `plan-challenge` in a fresh session and never reviewed, so check the epic ready criteria in [contract-template.md](templates/contract-template.md) before handing off. A slice-mode plan is reviewed by `issue-plan-review` in a fresh session, and never challenged. `plan-walkthrough` then explains the result to the approver. A plan of one slice is phased in the same `issue-plan-v2` run and approved once with the issue plan; its `issue-code` run codes the approved phase files without planning. Otherwise the approved issue-mode plan becomes the issue's work plan, and each `issue-code` run phases its slice with `/issue-plan --slice <id>`, has that plan reviewed, broken into one file per phase and approved. Either way the slice is implemented with `/issue-code --slice <id> --phase <n>`, one phase per agent session, committing each phase, one slice per run, in order.
