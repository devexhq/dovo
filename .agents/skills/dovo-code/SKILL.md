---
name: dovo-code
description: >-
  Implement the approved plan in .agentic/plan.md for Dovo CLI, working through
  its phases one by one in order, following the rules and exemplars in
  AGENTS.md, running scoped tests while building and the full quality gate once
  every phase is complete, and never committing or pushing. Invoked as
  /dovo-code to implement the plan, or /dovo-code review [--tiers blockers,warnings,suggestions,nits|all |
  --fix B1,W2,...] to address the findings in .agentic/review.md. Use when asked
  to implement a plan, write the code for a planned change, or fix review findings.
disable-model-invocation: true
---

# dovo-code

Turn every phase of `.agentic/plan.md` into working code, one phase after another, or in review mode, turn `.agentic/review.md` findings into fixes. Every run ends by writing or updating `.agentic/implementation-report.md`, which `/dovo-review` reads to tell documented plan deviations from silent ones.

## Modes

| Invocation | Input | Job |
|---|---|---|
| `/dovo-code` | `.agentic/plan.md` | Implement every phase in the plan's `## Phases` table, in table order. Start at the first phase whose artifacts are not yet in the tree, so an interrupted run resumes where it stopped; say which phases you skipped as already present. |
| `/dovo-code review [--tiers <tiers> \| --fix <ids>]` | `.agentic/review.md`, `.agentic/review.json` | Fix review findings by tier or by id |

If the input file is missing, stop and say so. Do not reconstruct a plan from the conversation: run `/dovo-plan` first.

## Hard boundaries

- **Never commit, stage, push, or touch PR state.** `/dovo-push` owns that.
- **All phases, one at a time.** Finish a phase (its FRs, tests and deletion ledger, with its scoped tests green) before starting the next, and never interleave FRs across phases. The phases still order the work and bound each step; they all land in this one run and one PR.
- **The plan's contracts are binding.** Field names, types, defaults, flag names, exit codes and message strings written as literal code in the plan are normative. Do not rename or extend them.
- **Do not implement anything the plan marks out of scope or names as a trap**, and do not add test files the plan's `### Tests` stubs do not list.
- **Never edit `.agentic/plan.md`, `.agentic/evidence.md`, `.agentic/review.md`, `.agentic/review.json` or `.agentic/verdict.txt`.** This skill writes only `.agentic/implementation-report.md` and, in review mode, `.agentic/fix-commit-msg`.

## Before writing code

Read [AGENTS.md](../../../AGENTS.md): its rules R1–R17 are what the reviewer will check, and its exemplars are what new code should look like. Then read the code you are about to change and its nearest exemplar. The plan's `.agentic/evidence.md`, when present, holds citations behind the plan; consult it to verify a claim, not as required reading.

## Implementation loop

Work phase by phase, in the Phases table's order. Within a phase, work one FR at a time, in the plan's order. For each FR:

1. Re-read the plan section, then the current contents of every file you are about to touch.
2. Write the production code at the paths the plan names.
3. Write the tests the plan's stubs name, asserting the contract each stub states (R12–R16, [testing.md](../../../docs/agents/testing.md)).
4. Run only the scoped tests for what you touched, and fix failures before the next FR:

   ```bash
   uv run dovo run tests --path tests/<mirrored-path> --fast-fail
   ```

5. Keep every docstring and doc sentence your change affects accurate (R17).

Execute each phase's deletion ledger in the same change as the code that supersedes each entry, with no shim or alias left behind (R10).

Before moving to the next phase, re-run the scoped tests for everything the phase touched and `uv run ty check` on its changed files, and fix failures there rather than carrying them forward. A later phase may depend on an earlier one, so do not start it on a broken base.

A check you add under `tests/lint/` must be able to fail: assert its input set is non-empty, and before calling it done, introduce the violation once, watch the check fail, then revert. An allowlist of known violations only ever shrinks.

### When the plan is wrong

If a contract cannot be implemented as written, or a cited symbol has moved or does not exist, stop that FR, report what the plan says against what the tree shows, flag it 🚨 and implement the rest. Never redesign a contract on your own or widen scope to make one fit.

## Completion gate

Run once every phase is complete, never between phases or mid-implementation, and never concurrently with another gate or test command:

```bash
uv run ruff format .
uv run ruff check --fix .
uv run dovo run quality
```

Fix failures in the code, never with a suppression, a baseline entry or a lowered threshold, and re-run from the start. Report each gate with the number it produced, and the coverage against `fail_under` in `pyproject.toml`.

## Implementation report (`.agentic/implementation-report.md`)

Write it after the gate is green (or as green as it will get, with failures stated). Sections, in order:

1. **Header**: plan title, the phases implemented (and any skipped as already present), and one line stating nothing was committed or pushed.
2. **Plan deviations**: every place the code differs from the plan, one numbered item each naming its phase, with the reason, including files touched outside the plan, unplanned helpers, and every 🚨. Write `none` only if you checked. A deviation not listed here is silent to the reviewer.
3. **Implementation details**: one subsection per phase: what was built per package, and the deletions executed.
4. **Quality gates**: each command with the number it observed.
5. **Changed files**: from `git status --short`.

## Review mode (`/dovo-code review`)

1. Read `.agentic/review.md` for the detail and `.agentic/review.json` for the ids. If either is absent, stop and say so.
2. Choose the findings. Ids are `B#` Blocking, `W#` Warnings, `S#` Suggestions and `N#` Nits, renumbered every review round, so resolve them against the `review.json` on disk. `review.json` `coverage` rows marked `unmet` arrive as BLOCKER findings; fix them like any other. Ignore `history`, `rejected` and `checks`: they are the reviewer's record.
   - **`--tiers <list>`**: comma-separated `blockers`, `warnings`, `suggestions`, `nits`, or `all`. Fix every finding in those tiers.
   - **`--fix <ids>`**: a comma-separated list (`B1,W2`) or a JSON array. Fix exactly those. An id not in `review.json` stops the run before any edit and names the valid ids.
   - `--tiers` and `--fix` cannot be combined. With neither, the default is `--tiers blockers`.
3. Re-read each cited file from disk before editing it.
4. Fix a "delete this" finding by deleting, never by adding a comment that explains why the duplicate is fine.
5. Dispute rather than comply when a finding is wrong: state why it does not hold and flag it 🚨 for the human.
6. Run the completion gate above.
7. Update `.agentic/implementation-report.md` in place: add a `### Review round <n>` subsection listing each finding by id as fixed, disputed or deferred with the `path:line` changed, add new deviations, and refresh Quality gates and Changed files. Do not edit `review.md` or `review.json`.
8. When this round changed any file, write `.agentic/fix-commit-msg`, overwriting any earlier one: a Conventional Commits title (`type(scope): imperative summary`, a type `scripts/check_commit_msg.py` accepts) naming what the fixes change in the code, never "address review findings"; a blank line; then one line per finding fixed, `- <id> (review round <n>): <what changed, with path>`. Findings disputed or deferred are not listed. When nothing changed, do not write the file.

   ```text
   fix(worktree): hold the workspace lock around the state write

   - B1 (review round 1): src/dovo/core/worktree/worktree.py takes WorkspaceLock before atomic_write_json
   - W2 (review round 1): tests/core/worktree/test_worktree.py asserts the lock timeout result
   ```

Report each addressed finding by id as fixed, disputed or deferred, with the `path:line` you changed.
