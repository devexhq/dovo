# Feature contract template

Copied from the slice-flow design at `944de0ce`; the design was revised 2026-10-28 after a dry run on the milestone 35 envelope group (#925). This is the template `issue-plan` fills when it runs on an epic, followed by the `plan.json` shape that carries it. `workplan.py render` writes the contract into the issue's work plan comment, followed by the work plan, which it writes from `plan.json`; `issue-plan` writes neither.

**Where it comes from:** #1214 (run by hand), #925's "Target contract" and "Order" sections, and the dry run, which found findings F1–F14 and conflicts C1–C12 between the issues. The same rules apply to every section:
- **Every sentence has a stated truth.** It is true on `origin/main` at the named sha, true once a named prerequisite lands (`after #n`), or a target the slices deliver.
- **Cite, don't restate.** A rule already in `docs/agents/architecture/` is cited by anchor, never re-decided. A target surface already described there states only the delta.
- **Ownership is explicit.** Nothing is deferred without an owner (#1227).
- **Only what has something to say.** Problem, Target contract and Shared-surface ownership are required. Every other section is left out when it has nothing to say, never filled with "none" (see "Section guidance").

---

```markdown
## Feature contract
Grounded at `main@<sha>`.

### Problem
<One paragraph: what is wrong today, with evidence (issue numbers, a review report,
`path:line` counts). Name who is affected: users, script authors or contributors.>

### Existing issues
<Every open issue the feature absorbs, depends on or sits beside, with its role. Built by
sorting each issue into container or work (see "Mapping existing issues" below).>
| Issue | Role | Becomes |
|---|---|---|
| #n | epic | this issue |
| #n | child | Sx |
| #n | split | Sx (keeps the issue), Sy (a slice of the epic, with no issue of its own) |
| #n | superseded container | closed once its decisions are copied here |
| #n | prerequisite | blocks Sx; must close first |
| #n | sibling epic | shares <surface>; coordinated, not absorbed |

### Prerequisites
<Open issues outside this epic that a slice needs merged first. The work plan refuses
a slice until its prerequisites are closed.>
- #n: needed by Sx, because <the fact it makes true>.

### Non-goals
- **Owned:** <X>, owned by #n.
- **Excluded:** <X>, because <reason>. A deliberate choice, not a deferral.
- **Deferred:** <X>, owned by #n. A deferral without an owner fails ready.

### Target contract
<The end state the slices deliver together, one bullet per surface. No slice specifies an
in-between shape that a later slice reverses.>
- **<Surface>** (`docs/agents/architecture/<file>.md#<heading>` if one exists): <the end
  state, or only the delta from that entry, as field lists, value tables or examples>.
  - Baseline (`main@sha` or `after #n`): <what is true before the slices>.
  - Touched by: Sx, Sy.

### Conventions
<The rules every slice follows. Cite the architecture entry when one exists; otherwise
state the rule here and say which slice writes its entry.>
- <Rule> (`docs/agents/architecture/<file>.md#<heading>`), or "new; written by Sx".
- **Exemplars:** <path>, conforms / does not yet conform (fixed by Sx). <Why it qualifies>.

### Overrides
<Sentences in existing issues that this contract makes wrong. The sweep applies
them to each issue's body; the ready check never does.>
- #n, "<quoted sentence>" → <what is true instead>, per <section or decision>.

### Decisions
| ID | Decision | Why | Settled by |
|---|---|---|---|
| D1 | <the choice> | <the concrete reason> | user <date> / plan-challenge Cn / inherited from #n (confirmed <date>) |

Open questions, each with a recommended answer until the user settles it:
- **Q1** (blocks Sx): <the question>. **Recommended:** <answer>, because <reason>.

### Code inventory
<Counts and locations of every site the feature touches, at the grounding sha. One line per
kind of site, with the count, the slice that changes it, and the command that finds it.
The full path:line list goes in a collapsed block. Each row's command is re-run when its
slice comes up, to catch drift.>
- <kind of site>: <count>, Sx (`<command>`)

### Shared-surface ownership
| Surface | Aspect | Owner | Extended by |
|---|---|---|---|
| <an enum, a header, a rule, a doc page> | <create / a field / a value> | Sx or #n | Sy, Sz ("after Sx", never restated) |

### Lint gates
| Check | Enforces | Lands with | Cites |
|---|---|---|---|
| `tests/lint/<file>.py` (new or extended) | <convention> | Sx (the slice whose fix makes it pass) | `<architecture entry>` |

### Checkpoints
- After the pilot (Sx).
- After <Sx>, <Sy>: where slices that share an owned surface have landed.
- Baseline: before the closing docs slice, across every landed slice and any open slice PR.
```

---

## Mapping existing issues

When `issue-plan` runs on an epic that already has issues (the usual case), it sorts each one before writing slices:
- **Container or work.** A container (a parent or tracking issue) never becomes a PR. A work issue does.
- **Exactly one container becomes the epic.** Prefer the one other issues and architecture entries already cite. Every other container is closed as superseded, after its decisions, non-goals and user stories are copied into the epic.
- **A work issue maps to one slice, or is split.** The slice that keeps the issue records it as its `child` in `plan.json`, and the work plan records it on the slice's row. The flow still works that slice, and its PR ends with `Closes #<child>` above the epic's closing line. When split, the first part keeps the issue, and later parts are slices of the epic with no issue of their own.
- **A true duplicate of another work issue** is merged into the older one.
- **Epics never nest.** An existing epic or container that belongs to another epic is never made a slice. Either it is closed as superseded, or it stays a sibling epic and its slice issues become prerequisites.
- **An issue outside the group** that a slice needs is a prerequisite. One that shares a surface without being needed is a sibling epic.
- **No issue is created or reparented** when the epic is published. Re-ready each kept child when its slice comes up. A slice a person hands to someone outside the flow is marked with `workplan.py split-out --handed-off`, which gives it an issue when it has none. The flow never picks it, and the epic's last PR closes the epic only once that issue is closed.

In the dry run this made #925 the epic, closed #926 and #924 as superseded containers, kept #1131, #1132 and #1058 as one slice each, and split #1135 and #1060 into two slices each.

## Section guidance

The contract scales with how far the shared surfaces spread. An epic whose slices share a surface in one area needs only a short contract: the required sections plus the slices. A section marked "when it has something to say" is left out entirely when it has nothing to say, and its `plan.json` field is `[]` (`null` for `inventory`).

| Section | Written | It must | It must not |
|---|---|---|---|
| Problem | required | cite evidence and name who is affected | propose a solution |
| Existing issues | when another open issue relates | give every related open issue a role | leave a container as a slice |
| Prerequisites | when a slice needs an outside issue first | name the fact each one makes true | list a slice of this epic |
| Non-goals | when something is owned elsewhere, excluded or deferred | give each a kind; give owned and deferred items an owner | defer with no owner |
| Target contract | required | give the end state or the delta per surface, with a baseline and `touched_by` | describe how the slices get there, or restate an architecture entry |
| Conventions | when the slices follow a rule no architecture entry states, or a named exemplar | cite entries by anchor; mark whether each exemplar conforms | restate an entry's text |
| Overrides | when an existing issue says something this contract makes wrong | quote the sentence it replaces | paraphrase it |
| Decisions | when a choice would otherwise be made twice, or an open question remains | give a reason and a source for each; confirm inherited ones; recommend an answer to each open question | leave a decision implicit in a slice, or an inherited one unconfirmed; re-decide a rule an architecture entry already states |
| Code inventory | when slice sizing rests on counted sites | be reproducible from a command at the grounding sha; assign each row a slice | be a hand-made estimate |
| Ownership | required | give exactly one owner per surface and aspect | let two slices define the same aspect |
| Lint gates | when a convention becomes a `tests/lint/` check | land each check with the fix that makes it pass | land a check that fails on main |
| Checkpoints | with three or more slices | place the pilot, shared-surface and baseline reviews | be skipped for a feature of three or more slices |

**Choosing the pilot.** The pilot is the slice whose conventions most other slices reuse, not necessarily the first in dependency order. Independent slices (in the dry run, catalog `item_type`) may run in parallel lanes after it.

---

## `plan.json` shape

`issue-plan` writes `contract` alongside the existing `plan.json` fields. It only adds fields, so `answer-questions` and the fold prompts keep working. The work-plan script builds the markdown above from it.

```json
"contract": {
  "grounded_at": "944de0ce",
  "problem": "…",
  "existing_issues": [{ "issue": 1135, "role": "split", "slices": ["S3", "S4"] }],
  "prerequisites": [{ "issue": 1192, "for": ["S2", "S3"], "makes_true": "…" }],
  "non_goals": [{ "text": "…", "kind": "owned | excluded | deferred", "owners": [999], "reason": null }],
  "target": [{ "surface": "…", "entry": "docs/agents/architecture/results.md#…", "end_state": "…",
               "baseline": { "as_of": "main@944de0ce | after #1192", "text": "…" }, "touched_by": ["S1", "S2"] }],
  "conventions": [{ "rule": "…", "entry": "…", "new_in": null }],
  "exemplars": [{ "path": "…", "why": "…", "conforms": false, "fixed_by": "S3" }],
  "overrides": [{ "issue": 1060, "quote": "…", "replacement": "…", "per": "D4" }],
  "decisions": [{ "id": "D1", "decision": "…", "why": "…",
                  "settled_by": { "kind": "user | challenge | inherited", "ref": "…", "confirmed": "2026-10-28" } }],
  "inventory": { "sha": "…", "items": [{ "kind": "…", "count": 0, "slice": "S3", "command": "…", "sites": ["path:line"] }] },
  "ownership": [{ "surface": "…", "aspect": "create", "owner": "S2", "extended_by": ["S6"] }],
  "lint_gates": [{ "check": "tests/lint/…", "extends": false, "enforces": "…", "slice": "S3", "entry": "…" }],
  "checkpoints": [{ "after": ["S1"], "kind": "pilot" }, { "after": ["S3", "S4"], "kind": "shared-surface" },
                  { "before": "S8", "kind": "baseline" }]
},
"open_questions": [ … existing field; each item gains an optional "blocks": ["S3"] … ]
```

`slices[]` (its full shape is in `issue-plan.md` Step 5) carries:
- `outcomes`: the issue's outcome ids (`O-n`) the slice delivers
- `child`: the existing issue the slice keeps, only on a slice an existing issue maps to
- `blocked_by`: prerequisite issue numbers, alongside `depends_on` for slice ids
- `owns`: the surfaces and aspects the slice owns
- `lint_gates`
- `estimate`: `{ "src_docs": 350, "tests": 400, "basis": "inventory rows …" }`
- `pilot: true` on one slice, and `closing_docs: true` on the last

An epic plan meets these ready criteria, which the planner checks from the fields before handing off: no review runs on an issue plan, and `plan.py check-slices` checks only each slice's outcomes. An optional section left out is never a failure on its own:
- `problem`, `target` and `ownership` are non-empty
- one owner per surface and aspect
- an acyclic graph, with every `blocked_by` issue an open or closed issue
- every slice's estimate is within budget
- one pilot, and the last slice is the closing docs slice
- checkpoints are placed when there are three or more slices
- every `deferred` non-goal has an owner
- no inherited decision is unconfirmed
- every target surface with two or more `touched_by` slices has an ownership row (the "no in-between shape a later slice reverses" rule)
