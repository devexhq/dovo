---
name: dovo-plan-review
description: >-
  Review a Dovo CLI plan before any code is written: re-ground it against the
  current tree, hunt for existing behaviour and persisted formats it breaks or
  relies on, check that every requirement maps to a phase and a test, and check
  phasing and the AGENTS.md rules, then write findings to
  .agentic/plan-review.md. Invoked as /dovo-plan-review [<plan-path>].
disable-model-invocation: true
---

# dovo-plan-review

Review a plan written by `/dovo-plan`, in this order of importance:

1. **Grounding**: does the plan describe the code as it is today.
2. **Missed behaviour**: what the planned change breaks or depends on that the plan does not say.
3. **Coverage**: does every requirement land in a phase and a test, and nothing more.
4. **Shape and rules**: phasing per [planning.md](../../../docs/agents/planning.md), and the design against R1–R17 in [AGENTS.md](../../../AGENTS.md).

Output goes to `.agentic/plan-review.md`. The plan's author applies the findings, and a human approves the plan before `/dovo-code` runs. Your job is to make that approval short. The findings that matter most come from reading the code, not from re-reading the plan.

## Hard boundaries

- **Never edit the plan, `src/` or `tests/`, and never commit or push.** Report findings and hand off.
- **Never run tests or tooling.** Read-only `git`, read-only `gh`, `rg` and file reads are enough.
- **Re-read from disk.** Every file you reason about is read fresh in this session, never recalled.
- **Every finding cites the plan section it concerns and the `path:line` or requirement ID behind it**, and says what goes wrong if the plan is implemented as written.

## 1. Resolve the input

The plan is `<plan-path>` when given, otherwise `.agentic/plan.md`. If it is missing, stop and say so. Read it in full, then every file it links to (usually `.agentic/evidence.md`). Take the issue number from the `# Issue #<n>:` header and read the issue with `gh issue view <n> --json number,title,body`; it is the contract the plan answers to. Without an issue, the plan's `## Requirements` list is the contract.

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

A miss is Blocking when implementing the plan as written produces a bug, data loss, a crash or a broken public surface; otherwise a Warning.

## 5. Check coverage

- Every FR, NFR, numbered error case and Definition of Done item maps to a phase and to at least one planned test. List the gaps.
- Every command the plan adds or changes has the R13 runner matrix: success, a failure path, `--format json` and each option it binds.
- Nothing out of scope, or named under Traps as not touched, is planned.
- Each planned test pins a contract no other test pins (R12), and sibling cases are planned as one parametrized test.
- Each Open Question states the answer the plan assumes. A Decision that changes public surface or a persisted format without an Open Question is a finding.

## 6. Check shape and rules

- The plan follows the template in [planning.md](../../../docs/agents/planning.md) Step 4, including the Phases table.
- Each phase is one concern, roughly 400 changed lines or less, passes the gate on its own and ships something. A phase over budget states why it cannot be split.
- Phases are ordered by dependency, lower layers first, and replacing an internal is phased add, switch callers, delete, with the deletion phase in the ledger (R10).
- The planned design against R1–R17: report **only violations**, each with the rule ID and the plan section that breaks it.
- Numbers agree everywhere they appear: line estimates, totals, the Definition of Done.

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
- <each decision only a human can make, with the plan's current assumption>
```

An empty section keeps its heading with the line `none`. IDs are the tier letter plus a number from 1 within the tier, in row order. Say `APPROVE` only with zero Blocking findings.

## 8. Hand off

Report the report path and the verdict. On `CHANGES REQUIRED`, the plan's author applies the findings and this skill runs once more in a fresh session. **Cap at 2 rounds**; after that, the human decides on whatever is still open. Do not start `/dovo-code`: a human approves the plan first.
