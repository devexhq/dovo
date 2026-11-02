# issue-plan: issue mode

`/issue-plan [<issue-number>]` slices an issue. Read [issue-plan.md](issue-plan.md) first: it holds the modes, the hard boundaries and the steps both modes share (2, 3, 4, 5 and 6). This file adds issue mode's Steps 1, 2a and 3a, its part of Steps 3 and 4, and how the slice flow tracks slices on the issue.

## Slices on the issue

- **The work plan comment is the only comment the flow posts on the issue.** It is the one comment whose first line is exactly `<!-- dovo:workplan schema_version=1 -->` (`gh issue view <n> --json comments`), created when the plan is approved and edited in place after. It lists each slice with its status, its own issue, its pull request and its code session, and names the session whose artifacts hold the plan. The plan, its decisions, its contract and each slice's "as landed" report stay in the planning and coding sessions' artifacts, never on the issue. The issue's description stays what a person wrote.
- **A slice gets its own issue when its work starts.** On a plan of two or more slices, when `issue-code-v2` starts a slice, `workplan.py start` creates a sub-issue of the epic for it in the short issue format, built from the epic's description and `plan.json`, and records it as the slice's `child`; the slice's PR closes it. Planning never creates an issue. An existing sub-issue mapped to a slice (Step 2a) is the slice's `child` from the start. A plan of one slice gets no sub-issue: its PR closes the issue. `workplan.py split-out --handed-off` gives a slice its own issue and marks it handed to someone outside the flow, which `workplan.py next` never picks.
- **Closing keywords:** a slice with its own issue ends its PR with `Closes #<child>`. Every slice PR then ends with `Closes #<issue>` when every other slice has merged, or was handed off to an issue that is now closed, and with `Part of #<issue> (slice <id> of <count>)` otherwise. `workplan.py start` writes the lines, and `land` writes them again from the plan as landed.

## 1. Check the issue and reset the workspace (issue mode)

Decide whether the issue is an epic or a plain issue before touching `.agentic/`:

```bash
gh issue view <n> --json body,milestone --jq '"milestone: \(.milestone.title // "none")", .body'
gh issue view <n> --json subIssues --jq '.subIssues.nodes[] | "#\(.number) \(.state) \(.title)"'
```

The plan's shape scales with how far the shared surfaces spread, not with how many issues there are. Read the issue, its sub-issues, and any group the user names (such as a milestone's issues), and sort them:

- **One issue, or issues that share no surface:** plain issues. Plan the issue as a plain issue; related issues get **Owned elsewhere** pointers, not a contract.
- **Several issues sharing a surface in one area:** one epic with a short contract (Step 2a).
- **Issues changing a surface other areas depend on, or setting rules other work will cite:** several sibling epics, with a foundation epic whose pilot runs first. Plan only the epic asked for, and give the siblings and the foundation their roles in the contract's existing issues (Step 2a). Epics never nest.
- **One issue whose slices would each apply the same rule to a different package or surface:** raise an open question (Step 5) recommending that a person splits it, as [templates/issue.md](templates/issue.md) describes, into a foundation issue (the rule in the architecture docs, shared code and checks, one pilot) and one issue per remaining part, each depending on the foundation. Plan it as asked; the split is the person's call.

Slice the issue when it needs more than one PR, or when its changes share a surface with other open work, such as its sub-issues or the issues the user names; that work becomes its slices or gets a role (Step 2a). A sliced issue is an epic, with a contract as short as what its slices share: a plan of two or more slices always has one, since its Shared-surface ownership names the one owner of each surface the slices share. Otherwise plan a plain issue, one slice. The `type:epic` label follows from the plan: the blueprint's `label-epic` step adds it once the plan has two or more slices. An issue already labelled `type:epic` (`gh issue view <n> --json labels --jq '.labels[].name'`), or whose work plan comment already lists two or more slices, is an epic.

Read each related issue in full before giving it a role in `existing_issues`, not only its title and blockers:

```bash
gh issue view <m> --json title,body,labels,milestone,state
gh issue view <m> --json blockedBy --jq '.blockedBy.nodes[] | "#\(.number) \(.state)"'
```

Then reset, keeping only the prior decisions:

```bash
mkdir -p .agentic && find .agentic -mindepth 1 -maxdepth 1 ! -name prior-decisions.json -exec rm -rf {} +
```

A stale plan or review must never be read as current. Then read the prior decisions, `.agentic/prior-decisions.json`, the decisions of the issue's latest approved plan, from the session its work plan names. The blueprint's `prior-decisions` step writes it before this one; when run by hand, write it after the reset with `uv run python .dovo/catalog/scripts/sdlc/v2/plan.py prior-decisions --issue <n> --out .agentic/prior-decisions.json`, adding `--plan <plan.json> --session <session> --planned-sha <body_sha>` for the plan.json that session published, with the session and body_sha `workplan.py show --issue <n>` prints, when the issue has a work plan. A decision marked `given` binds this plan like the issue does: plan against it and never raise it again as an open question. A decision marked `to_confirm` was settled before the issue's description last changed: raise it as an open question whose `assumed` answer is the earlier decision, with `replaces` naming it (Step 5).

## 2a. Write the feature contract (epic, issue mode only)

Skip this on a plain issue and in slice mode, which never changes the contract. On an epic, write the contract before any phase or slice, following [contract-template.md](templates/contract-template.md): its sections in order, the rules every section follows, its section guidance, and "Mapping existing issues", which gives every sub-issue and related open issue a role (epic, child, split, superseded container, prerequisite or sibling epic) before any slice is written.

Keep the contract as short as the surface spread allows. Problem, Target contract and Shared-surface ownership are required, alongside the slices. Every other section is written only when it has something to say, and left out otherwise, never filled with "none". Cite rules already in `docs/agents/architecture/` instead of re-deciding them. When written, the code inventory comes from commands run at the grounding sha, never estimates. Each open question goes in plan.json with its assumed answer and the slices it blocks (Step 5).

## 3. Size and phase (issue mode)

In issue mode, size only: ground enough to estimate the changed lines each slice needs, `src/` plus docs and tests separately, and write no phases. Then follow Step 3 of [issue-plan.md](issue-plan.md).

## 3a. Slice the issue (issue mode)

On a plain issue, the whole issue is one slice, `S1`, delivering every outcome of the issue. On an epic:

- **Size each slice.** `src/` plus docs stay under about 400 changed lines, and the whole PR, tests included, under about 900. Tests are estimated separately. A slice over budget says why it cannot be split.
- **Give each slice a review size**, its `size`, by how much a reviewer must cover: `s` for one area and a few files, `m` for a couple of areas, `l` for many areas (packages, formats, command groups, skills, catalog or scripts). The work plan stores it as plain data; no issue is labelled with it, and `workplan.py start` creates a slice's own issue without a label. A plain issue's one slice is sized the same way.
- **One responsibility per slice.** A slice never delivers a shape another slice must reverse, and every surface two slices touch has one owner in the contract's Shared-surface ownership table; the other slice says "after Sx" and does not restate it.
- **Order by dependency.** `depends_on` names slice ids, `blocked_by` names prerequisite issues outside the epic, and the graph is acyclic.
- **Slice 1 is the pilot**, unless another slice's conventions are reused by more slices; then that slice is the pilot. Its "as landed" report updates the conventions and decision log before later slices are written in full.
- **The last slice is the closing docs slice.** It reviews every doc the feature touched against `main`, and its PR, the last to land, closes the issue.
- **Place the checkpoints** when the epic has three or more slices: after the pilot, after slices that share an owned surface, and a baseline before the closing docs slice.
- **List each slice's outcomes**, its `outcomes`: the ids of the issue's outcomes (`O-<n>`) the slice delivers, which its PR is reviewed against. Every outcome belongs to exactly one slice. An outcome whose parts land in different slices is listed in each of them with its part in parentheses (`O-7 (reply)`, `O-7 (fixer)`), as slice mode's Step 4 does for a requirement split across phases. `plan.py check-slices` refuses a plan that leaves an outcome to no slice or lists it in two without that marker.

## 4. Write the plan (issue mode)

In issue mode, `.agentic/plan.md` holds, in this order: the header and grounding line from planning.md Step 4 (its opening paragraph naming the Slices table's outcomes as the requirement map), Changes to existing behaviour, on an epic the `## Feature contract` from Step 2a, Owned elsewhere, and a `## Slices` table (id, title, outcomes, depends on, blocked by, pilot or closing docs, estimate, size). It has no Phases table, Artifact inventory, phase sections or Traceability section: `plan.py check-slices` maps each outcome to its slice.
