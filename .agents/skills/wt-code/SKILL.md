---
name: wt-code
description: >-
  Implement the approved plan in .agentic/plan.md for worktree-cli consulting
  domain-scoped RULES.md, running only scoped tests while building and the full gate
  suite (tests with coverage, ruff, basedpyright over src and tests, complexity) once
  implementation is complete, reporting observed gate numbers rather than
  documented thresholds, and never committing or pushing. Invoked as /wt-code to implement
  the plan, or /wt-code review [--fix blockers|warnings|suggestions|all] to address the
  findings in .agentic/review.md. Use when asked to implement a plan, write the code for a
  planned change, or fix review findings.
disable-model-invocation: true
---

# wt-code

Turn `.agentic/plan.md` into working code, or in review mode, turn `.agentic/review.md` findings into fixes.

## Modes

| Invocation | Input | Job |
|---|---|---|
| `/wt-code` | `.agentic/plan.md` | Implement the plan |
| `/wt-code review [--fix <scope>]` | `.agentic/review.md` | Fix review findings (`blockers`, `warnings`, `suggestions`, `all`) |

If the input file is missing, stop and say so. Do not reconstruct a plan from the conversation, and do not implement from memory of what was discussed: run `/wt-plan` first.

## Hard boundaries

- **Never commit, stage, push, or touch PR state.** Not at the end, not "to be safe", not even when the gates pass. `/wt-push` owns that.
- **Never run the full gate suite mid-implementation.** Scoped tests only until the work is complete.
- **Run quality gates one at a time, never in parallel.** No concurrent tool calls, background jobs, or subagents for gate commands.
- **The plan's contracts are binding.** Field names, types, defaults, flag names, exit codes, and message strings written as literal code in the plan are normative. Do not improve, rename, or extend them.
- **Do not implement anything the plan marks out of scope or flags as a trap.**
- **Never create a test file the plan's `### Tests` stubs do not list**, and never skip a deletion the deletion ledger lists (`PLAN-009`). Both directions are contract breaches, and the additive direction is the one that slips through unnoticed.
- **A green gate is not evidence that a check ran.** Report what each gate observed, never what a doc says it enforces.

## Implementation loop

Before writing or refactoring any code in a domain, load its `RULES.md`, which carries both cross-cutting repo rules and package-specific invariants:

| Editing | Load |
|---|---|
| `core/` | [src/worktree/core/docs/RULES.md](../../../src/worktree/core/docs/RULES.md) |
| `common/` | [src/worktree/common/docs/RULES.md](../../../src/worktree/common/docs/RULES.md) |
| `cli/` | [src/worktree/cli/docs/RULES.md](../../../src/worktree/cli/docs/RULES.md) |
| `tests/` | [tests/docs/RULES.md](../../../tests/docs/RULES.md) |

If `.agentic/plan.md`'s Ground truth and Artifact inventory sections point to `.agentic/evidence.md`, that file holds the citations and mirrored patterns behind the plan's Instructions — not additional instructions of its own. Consult it when you want to verify a claim or a cited symbol looks stale; don't treat it as required reading before every FR, since each FR's own `### Instructions` already carries the file:line detail it needs.

Work one FR (or one testable clause) at a time, in the plan's order. For each:

1. Re-read the plan section, then re-read the current contents of every file you are about to touch.
2. Write the production code, following the artifact inventory for exact paths.
3. Write the tests the ledger names, at the path it names, asserting the exact contract it states — follow [docs/agents/testing.md](../../../docs/agents/testing.md) and `tests/docs/RULES.md` for tier, mocking policy, and assertion style; do not restate those rules here.
4. Run only the scoped tests for what you just touched:

   ```bash
   python -m pytest tests/<mirrored-path> -q          # one file or directory
   uv run inv test --no-parallel --fast-fail          # only when a whole-suite signal is genuinely needed
   ```

5. Fix what fails before moving to the next FR. Do not accumulate red tests across FRs.
6. Re-read the docstring of every function, class, and module you added or changed, and of any whose behavior your change altered, and keep each accurate (`CODE-003`): a one-line title, plus a description only for a hidden contract or safety behavior the code cannot show. A docstring your change made false is a defect, so fix or delete the sentence in the same change.

Execute the deletion ledger as you go, in the same change set as the code that supersedes each entry. Do not leave a compatibility shim, alias, or dual path behind unless the plan demanded one (`COMPAT-002`, `COMPAT-003`), and never leave a negative existence test behind as the record of a removal (`TEST-009`).

Apply the doc updates the plan's cross-cutting section lists, and only those.

### Writing enforcement code

Required by `CI-004`. A test whose assertion is "no violations found" passes when it inspects nothing, so it must prove it can fail:

1. Assert the collected input set is non-empty before asserting the violations list is empty.
2. Ship the negative fixture test the plan names: construct a violating sample matched to the real shape it polices, assert the checker flags it.
3. Before declaring the check done, break it on purpose once: introduce the violation into a real file in the tree, confirm the check fails, then revert.
4. A burn-down allowlist of known violators only ever shrinks; an entry added in this change to make a new violation pass defeats the check.

### When the plan is wrong

The plan was reviewed by a human, so a contradiction is worth surfacing rather than silently resolving. If a contract cannot be implemented as written, or grounding turns out to be stale (a cited symbol moved or does not exist), stop that FR, report what the plan says versus what the tree shows, and flag it with 🚨. Implement the rest. Never redesign a contract on your own and never widen scope to make one fit.

If the plan's ledger asks for a test whose contract you find already pinned elsewhere while implementing, say so with both paths and 🚨 rather than writing the duplicate.

## Completion gate (`CI-001`)

Run this once, after implementation is complete, **strictly sequentially, in this order**: one command per tool call, wait for it to finish, and never issue two gate commands in the same message, chain them in the background, or launch them concurrently. A failure stops the sequence: fix it, then resume from the failed gate and re-run the later ones, since a fix can invalidate earlier results (re-run from `ruff format` if formatting-affecting code changed). Each command's fix belongs in the code, never in a suppression or a lowered threshold — [docs/agents/ci-and-tooling.md](../../../docs/agents/ci-and-tooling.md) has what each gate actually checks, and [code-conventions.md](../../../docs/agents/code-conventions.md#type-checker-suppressions) has the only permitted suppressions:

```bash
ruff format .
ruff check .                                                          # ruff check --fix . for safe fixes
basedpyright src tests --level error                                  # must be 0 errors, tests included
uv run inv complexity --paths <changed-py-files> --plain --failed     # no touched function over 10
uv run inv test -c                                                    # full suite with coverage
```

Then read what the gates actually enforce, rather than what the docs claim:

```bash
uv run python -c "import tomllib, pathlib; print(tomllib.loads(pathlib.Path('pyproject.toml').read_text())['tool']['coverage']['report']['fail_under'])"
git diff --stat -- tests/ | tail -1
```

Report the observed coverage percentage against the configured `fail_under` — if it is `0`, say the coverage gate is switched off rather than reporting a pass, and never add tests or lower the floor to make a number look better. Report the `tests/` line delta against the plan's budget (`PLAN-017`): growth is expected when the ledger says so and suspicious when it does not.

Loop until every gate is green, then report, per gate, the command and the number it produced — for anything the repo does not actually enforce, say so instead of listing it as a pass. Close with: what was implemented per FR, the deletions executed, the observed gate numbers, any 🚨 deviations, and confirmation that nothing was committed or pushed.

## Review mode (`/wt-code review`)

1. Read `.agentic/review.md`. If it is absent, stop and say so.
2. Determine which findings to address from `--fix [blockers, warnings, suggestions, all]` or user instruction:
   - **`blockers`** (default): fix every `BLOCKER`. **`warnings`**: also every `WARNING`. **`suggestions`**: also every `SUGGESTION`. **`all`**: all four tiers.
3. Re-read each cited file from disk before editing it, since the review may describe a state that has since changed.
4. A finding that says "delete this" is fixed by deleting it — redundant tests (`TEST-004`), dead harness symbols (`TEST-013`), and duplicated contracts are resolved by subtraction, never by adding a comment explaining why the duplicate is fine.
5. Dispute rather than comply when a finding is wrong: state the finding, why it does not hold, and flag it 🚨 for the human.
6. For a fix to an enforcement defect (a check that did not check), watch it fail before you call it fixed: reproduce the violation the check missed, confirm the repaired check flags it, then revert the reproduction.
7. Run the same completion gate above once the fixes are in.
8. Do not edit or delete `.agentic/review.md` or `.agentic/review.json`. They are the reviewer's artifacts, and the next round is compared against them.

Report each finding as fixed, disputed, or deferred, with the `path:line` you changed.
