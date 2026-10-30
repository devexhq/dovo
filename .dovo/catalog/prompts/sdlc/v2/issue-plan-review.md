Forked from `dovo-plan-review` at `de256118`. This copy is temporary: once the slice flow works, it replaces `dovo-plan-review`.

# issue-plan-review

Review a **slice plan**, `/issue-plan-review --slice <id>`: the phases of that one slice, written by `/issue-plan --slice <id>` against the issue plan's contract and slices when the slice comes up. An issue plan, which slices an issue and has no phases, is not reviewed: `plan-challenge` challenges it, and `plan.py check-questions` and `check-slices` check its mechanics. Without `--slice`, stop and say so. Review in this order of importance:

1. **Grounding**: does the plan describe the code as it is today.
2. **Missed behaviour**: what the planned change breaks or depends on that the plan does not say.
3. **Coverage**: does every requirement land in a phase and a test, and nothing more.
4. **Shape and rules**: phasing per [planning.md](../../../../../docs/agents/planning.md), the slice against the issue plan per [issue-plan](issue-plan.md), and the design against R1–R18 in [AGENTS.md](../../../../../AGENTS.md).

Output goes to `.agentic/plan-review.md`, paired with `.agentic/plan-review.json`. The plan's author applies the findings, and a human approves the plan before `/issue-code` runs. Your job is to make that approval short. The findings that matter most come from reading the code, not from re-reading the plan.

## Hard boundaries

- **Never edit the plan, `src/` or `tests/`, and never commit or push.** Report findings and hand off.
- **Never run tests or tooling.** Read-only `git`, read-only `gh`, `rg` and file reads are enough.
- **Re-read from disk.** Every file you reason about is read fresh in this session, never recalled.
- **Every finding cites the plan section it concerns and the `path:line` or requirement ID behind it**, and says what goes wrong if the plan is implemented as written.

## 1. Resolve the input

The plan is `<plan-path>` when given, otherwise `.agentic/plan.md`. If it is missing, stop and say so. Read it in full, then every file it links to (usually `.agentic/evidence.md`), and `plan.json` beside it. Take the issue number from the `# Issue #<n>:` header and read the issue with `gh issue view <n> --json number,title,body`; it is the contract the plan answers to. Without an issue, the plan's `## Requirements` list is the contract. The header names the slice's issue, the slice's `child` when it has an issue of its own and otherwise the epic; the slice is the `plan.json` `slices` entry whose `id` is `<id>` (an id not there stops the review and names the valid ids), and `plan.json` `issue` is the epic. Read the epic's work plan comment too (`gh issue view <epic> --json comments`, the comment whose first line is `<!-- dovo:workplan schema_version=1 -->`): its `## Feature contract` and `## Work plan`, with the "as landed" reports under it, bind the slice alongside the slice's issue.

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
- **Issue shapes the code contradicts**: the plan adopts an internal shape from the issue (a helper name, module, type alias or scope boundary) that the code shows is wrong, such as a symbol that duplicates or shadows an existing one, or an injected dependency the change holds but bypasses. A Trap that tells the implementer to work around the issue's shape is the usual sign. Take a position and report it as a Warning whose Fix is the better design and names the departure from the issue (planning.md Step 1). Never leave it as a neutral "confirm" under **For the human**.
- **Guards that exclude the adopted foundation**: the plan follows an issue guard ("same message as today") that forbids behaviour of the foundation the issue adopts, or freezes wording nothing relies on (planning.md Step 1). Report it as a Warning whose Fix is the departure, and recommend it under **For the human**.

A miss is Blocking when implementing the plan as written produces a bug, data loss, a crash or a broken public surface; otherwise a Warning.

## 5. Check coverage

The requirements are the slice's numbered items in `.agentic/requirements.json`: when the slice is worked on the plan's issue and the plan has two or more slices, only the outcomes the slice's `outcomes` lists (the issue's decisions bind the slice without being its requirements); otherwise every outcome and decision of the slice's issue. For an outcome whose entry has a `part`, judge only the coverage of the part named in `part`; the outcome's other parts belong to other slices and are never a finding.

- The plan has no Traceability or GWT section: every obligation of every requirement (an outcome `O-n` or decision `D-n`, or on a legacy issue an FR, NFR or numbered error case; each sentence a test could observe) is covered by a phase's FRs cell and by at least one test line under that phase's `### Tests`. An id that appears somewhere is not enough. A requirement with an uncovered part is a Warning that quotes the issue sentence and names the phase that should carry it. List the gaps.
- Every command the plan adds or changes has the R13 runner matrix: success, a failure path, `--format json` and each option it binds.
- Nothing an issue under `## Owned elsewhere` owns is planned, unless the plan names it as a departure (a `planner` decision or an Open Question), or it is a sentence the change makes false (R17).
- Unrequested surface is surface that serves no FR, NFR, guard or Definition of done item. It gets no finding when a rule requires it (R10 callers, R17 sentences) or the plan names it as a departure (a `planner` decision or an Open Question); otherwise it is a Warning when public and a Suggestion when internal.
- Each planned test pins a contract no other test pins (R12), and sibling cases are planned as one parametrized test, on one line.
- Each Open Question states the answer the plan assumes, and `.agentic/plan.json` lists the same Open Questions with the same ids and assumed answers. A missing or disagreeing `plan.json` is a Warning: the blueprint asks the human from it. A Decision that changes public surface or a persisted format without an Open Question is a finding.

## 6. Check shape and rules

- The plan follows the template in [planning.md](../../../../../docs/agents/planning.md) Step 4, including the Phases table. That template has no Traceability, GWT Scenarios, per-phase Instructions, Edge cases or **Unchanged:** section, so their absence is never a finding. A phase's `### Code` that copies a literal the issue's Pre-determined data already holds, or stubs a private helper, is a Suggestion whose Fix is to cite the issue section or drop the stub.
- Each phase is one concern, roughly 400 changed lines or less, passes the gate on its own and ships something. A phase over budget states why it cannot be split.
- The phases of each slice together serve one responsibility, since each slice lands in one PR. A slice whose phases mix kinds of change (a foundation and its adoption, or a behaviour change and a refactor), not flagged under What you're approving with a recommendation to split the slice, is a Warning.
- `.agentic/plan.json` `slices` agrees with the plan's `## Slices` table, every phase belongs to the slice under review, and a plain issue (`contract` is `null`) has one slice.
- Phases are ordered by dependency, lower layers first, and replacing an internal is phased add, switch callers, delete, with the deletion phase in the ledger (R10).
- The planned design against R1–R18: report **only violations**, each with the rule ID and the plan section that breaks it.
- Numbers agree everywhere they appear: line estimates, totals, the Definition of Done.

## 6a. Check the slice against the epic

Report each failure as a Blocking finding citing the field or section:

- **The epic's plan is unchanged:** diff `plan.json` against `.agentic/epic-plan.json`, the epic plan this run started from, field by field. `contract` is equal, and `slices` is equal with every slice's `phases` left out, so a change to any field (`title`, `depends_on`, `blocked_by`, `outcomes`, `owns`, `lint_gates`, `estimate`, `size`, `pilot`, `closing_docs`) is caught. The plan's `## Feature contract` and `## Slices` sections are `.agentic/epic-plan.md`'s, verbatim. A slice plan that changes the contract or another slice is a Blocking finding: the contract changes only when a slice lands. When `.agentic/epic-plan.json` is missing, say so and fall back to the epic's work plan comment: `contract` says what its `## Feature contract` says, apart from the open questions listed under Decisions, which are the epic plan's.
- **Only this slice is phased:** this slice's `phases` lists exactly the Phases table's phase numbers, and every other slice's `phases` is `[]`.
- **The slice's requirements are covered:** every requirement in `.agentic/requirements.json` (Step 5) is in the Phases table's FRs column and cited by at least one test line. For one with a `part`, check only the part named in `part`; the outcome's other parts belong to other slices and are never a finding. A requirement only part of which is covered is the Warning Step 5 describes, not a Blocking finding.
- **The issue's decisions are kept:** no phase contradicts a decision (`D-n`) of the slice's issue or a decision its `dovo:decisions` comments record (`.agentic/prior-decisions.json`, `given` ones), unless the plan names the departure as an Open Question.
- **The decisions settled when the issue was planned are kept:** no phase contradicts a decision in `.agentic/epic-plan.json` `decisions` or `contract.decisions`, or a `given` decision in `.agentic/prior-decisions-parent.json`, the decisions posted to the issue whose work plan holds a slice worked on its own issue (present only then). When the same run planned the issue and this slice, as `issue-plan-v2` does for a one-slice plan, those decisions are not posted to the issue yet, so `.agentic/prior-decisions.json` does not list them. No challenge runs on a slice plan, so this is the one check that stops a slice from reopening the design settled at the issue level. An Open Question that reopens one of these decisions is a finding too, and its Fix keeps the decision; an Open Question that carries `replaces` for a `to_confirm` prior decision (the description changed since it was posted) is the plan asking for it to be confirmed again, and is never a finding.
- **No other slice's work:** no phase does work the contract's Shared-surface ownership gives to another slice, or an outcome another slice's `outcomes` lists, or that the slice's issue names under **Out of scope** or **Owned elsewhere**, unless the plan names it as a departure (a `planner` decision or an Open Question).
- **The "as landed" reports are heeded:** every report under the epic's `## Work plan` that names this slice under Later slices affected, or records a convention this slice follows, is reflected in the phases or raised as an Open Question.
- **Size:** the slice's phases together stay within its budget: `src/` plus docs under about 400 changed lines, and the whole PR under about 900, or the plan says why the slice cannot be split.

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

Write `.agentic/plan-review.json` alongside the markdown, overwriting any earlier one. The blueprint that phases the slice (`issue-code-v2`, or `issue-plan-v2` for a one-slice plan) folds every finding it lists into the plan, so it must agree with `plan-review.md` row for row.

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

- `verdict` is exactly `APPROVE` or `CHANGES_REQUIRED`, and is `APPROVE` iff `counts.blocking` is `0`. `round` counts this prompt's runs on the plan, from 1.
- `provenance` is `fresh_session` or `same_session`. `grounded_at` and `grounded_is_ancestor` come from the **Plan** line.
- `findings` lists every finding, Blocking first, then Warnings, then Suggestions, each with the `id` from its `plan-review.md` row. `severity` is `BLOCKER`, `WARNING` or `SUGGESTION`. `section`, `evidence` and `fix` copy the row's columns; `summary` is the row's Issue in one line. `rule` is an R-id when the finding is a rule violation, otherwise `null`.
- `coverage` lists the IDs from the **Coverage** section (`"<command>: <case>"` strings for R13 gaps), `[]` for "none". `for_human` copies the **For the human** bullets, `[]` for "none".

## 9. Hand off

Report the report path and the verdict. On `CHANGES REQUIRED`, the plan's author applies the findings and this prompt runs once more in a fresh session. **Cap at 2 rounds**; after that, the human decides on whatever is still open. Do not start `/issue-code`: a human approves the plan first.
