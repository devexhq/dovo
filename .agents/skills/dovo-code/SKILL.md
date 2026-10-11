---
name: dovo-code
description: >-
  Implement the approved plan in .agentic/plan.md for Dovo CLI consulting
  domain-scoped RULES.md, running only scoped tests while building and the full gate
  suite (tests with coverage, ruff, basedpyright over src and tests, complexity) once
  implementation is complete, reporting observed gate numbers rather than
  documented thresholds, and never committing or pushing. Invoked as /dovo-code to implement
  the plan, or /dovo-code review [--fix blockers|warnings|suggestions|all] to address the
  findings in .agentic/review.md. Use when asked to implement a plan, write the code for a
  planned change, or fix review findings.
disable-model-invocation: true
---

# dovo-code

Turn `.agentic/plan.md` into working code, or in review mode, turn `.agentic/review.md` findings into fixes. Every run ends by writing or updating `.agentic/implementation-report.md`, which `/dovo-review` reads to tell documented plan deviations from silent ones.

## Modes

| Invocation | Input | Job |
|---|---|---|
| `/dovo-code` | `.agentic/plan.md` | Implement the plan |
| `/dovo-code review [--fix <scope>]` | `.agentic/review.md` | Fix review findings (`blockers`, `warnings`, `suggestions`, `all`) |

If the input file is missing, stop and say so. Do not reconstruct a plan from the conversation, and do not implement from memory of what was discussed: run `/dovo-plan` first.

## Hard boundaries

- **Never commit, stage, push, or touch PR state.** Not at the end, not "to be safe", not even when the gates pass. `/dovo-push` owns that.
- **Never run the full gate suite mid-implementation.** Scoped tests only until the work is complete.
- **Run quality gates one at a time, never in parallel.** No concurrent tool calls, background jobs, or subagents for gate commands.
- **The plan's contracts are binding.** Field names, types, defaults, flag names, exit codes, and message strings written as literal code in the plan are normative. Do not improve, rename, or extend them.
- **Do not implement anything the plan marks out of scope or flags as a trap.**
- **Never create a test file the plan's `### Tests` stubs do not list**, and never skip a deletion the deletion ledger lists (`PLAN-009`). Both directions are contract breaches, and the additive direction is the one that slips through unnoticed.
- **A green gate is not evidence that a check ran.** Report what each gate observed, never what a doc says it enforces.
- **Never edit `.agentic/plan.md`, `.agentic/evidence.md`, `.agentic/review.md`, or `.agentic/review.json`.** The only `.agentic/` file this skill writes is `.agentic/implementation-report.md`.

## Implementation loop

Before writing or refactoring any code in a domain, load its `RULES.md`, which carries both cross-cutting repo rules and package-specific invariants:

| Editing | Load |
|---|---|
| `core/` | [src/dovo/core/docs/RULES.md](../../../src/dovo/core/docs/RULES.md) |
| `common/` | [src/dovo/common/docs/RULES.md](../../../src/dovo/common/docs/RULES.md) |
| `cli/` | [src/dovo/cli/docs/RULES.md](../../../src/dovo/cli/docs/RULES.md) |
| `tests/` | [tests/docs/RULES.md](../../../tests/docs/RULES.md) |

If `.agentic/plan.md`'s Ground truth and Artifact inventory sections point to `.agentic/evidence.md`, that file holds the citations and mirrored patterns behind the plan's Instructions — not additional instructions of its own. Consult it when you want to verify a claim or a cited symbol looks stale; don't treat it as required reading before every FR, since each FR's own `### Instructions` already carries the file:line detail it needs.

Work one FR (or one testable clause) at a time, in the plan's order. For each:

1. Re-read the plan section, then re-read the current contents of every file you are about to touch.
2. Write the production code, following the artifact inventory for exact paths.
3. Write the tests the ledger names, at the path it names, asserting the exact contract it states — follow [docs/agents/testing.md](../../../docs/agents/testing.md) and `tests/docs/RULES.md` for tier, mocking policy, and assertion style; do not restate those rules here.
4. Run only the scoped tests for what you just touched:

   ```bash
   python -m pytest tests/<mirrored-path> -q          # one file or directory
   uv run dovo run tests --path tests/<mirrored-path> --fast-fail   # same, via the tests blueprint
   uv run dovo run tests --fast-fail                  # only when a whole-suite signal is genuinely needed
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

Run this once, after implementation is complete. Apply formatting first (the gate only checks it), then run the blueprint, which runs every gate in order and stops at the first failure. Fix the failure in the code, never in a suppression or a lowered threshold, and re-run it from the start, since a fix can invalidate earlier results. Never run it concurrently with another gate or test command. [docs/agents/ci-and-tooling.md](../../../docs/agents/ci-and-tooling.md) has what each gate actually checks, and [code-conventions.md](../../../docs/agents/code-conventions.md#type-checker-suppressions) has the only permitted suppressions:

```bash
uv run ruff format .                                                  # apply formatting; the gate only checks it
uv run ruff check --fix .                                             # safe fixes; the gate only reports
uv run dovo run quality                                               # format check, lint, basedpyright (0 errors, tests included), docs build, complexity on changed files (none over 10), full suite with coverage
```

Then read what the gates actually enforce, rather than what the docs claim:

```bash
uv run python -c "import tomllib, pathlib; print(tomllib.loads(pathlib.Path('pyproject.toml').read_text())['tool']['coverage']['report']['fail_under'])"
git diff --stat -- tests/ | tail -1
```

Report the observed coverage percentage against the configured `fail_under` — if it is `0`, say the coverage gate is switched off rather than reporting a pass, and never add tests or lower the floor to make a number look better. Report the `tests/` line delta against the plan's budget (`PLAN-017`): growth is expected when the ledger says so and suspicious when it does not.

Loop until every gate is green, then report, per gate, the command and the number it produced — for anything the repo does not actually enforce, say so instead of listing it as a pass. Close with: what was implemented per FR, the deletions executed, the observed gate numbers, any 🚨 deviations, and confirmation that nothing was committed or pushed.

## Implementation report (`.agentic/implementation-report.md`)

Write it after the completion gate is green (or as green as it will get, with failures stated), overwriting any previous report from a plan run. Do not write it mid-implementation; keep a running list of deviations as you go so none are reconstructed from memory.

Required sections, in this order:

1. **Header**: issue or plan title, and one line stating nothing was committed or pushed.
2. **Plan deviations**: every place the code differs from the plan, one numbered item each, with the reason. This includes judgement calls the plan left open, extra files or docs touched outside the plan's lists, renamed test classes, fixtures or helpers the plan did not name, and every 🚨 raised during the run (plan wrong, stale grounding, duplicate contract). Write `none` only if you checked. Also record how each plan Open Question was resolved in the code. A deviation not listed here counts as silent to the reviewer, so a missing entry is worse than a verbose one.
3. **Implementation details**: what was built per package (production, tests, docs), and the deletions executed against the ledger.
4. **Quality gates**: each command with the number it observed, the configured `fail_under`, and the `tests/` line delta against the plan's budget. State anything the repo does not enforce.
5. **Changed files**: modified and new, from `git status --short`.

Deviations must be factual. Do not use the report to pre-justify a contract change the plan forbids: a deviation that alters a literal contract (field name, message, flag, exit code) is a 🚨 for the human, not an accepted variance.

## Review mode (`/dovo-code review`)

1. Read `.agentic/review.md`. If it is absent, stop and say so.
2. Determine which findings to address from `--fix [blockers, warnings, suggestions, all]` or user instruction:
   - **`blockers`** (default): fix every `BLOCKER`. **`warnings`**: also every `WARNING`. **`suggestions`**: also every `SUGGESTION`. **`all`**: all four tiers.
3. Re-read each cited file from disk before editing it, since the review may describe a state that has since changed.
4. A finding that says "delete this" is fixed by deleting it — redundant tests (`TEST-004`), dead harness symbols (`TEST-013`), and duplicated contracts are resolved by subtraction, never by adding a comment explaining why the duplicate is fine.
5. Dispute rather than comply when a finding is wrong: state the finding, why it does not hold, and flag it 🚨 for the human.
6. For a fix to an enforcement defect (a check that did not check), watch it fail before you call it fixed: reproduce the violation the check missed, confirm the repaired check flags it, then revert the reproduction.
7. Run the same completion gate above once the fixes are in.
8. Do not edit or delete `.agentic/review.md` or `.agentic/review.json`. They are the reviewer's artifacts, and the next round is compared against them.
9. Edit `.agentic/implementation-report.md` in place after the gate is green; if it is absent, create it per the section above. Keep earlier content that is still true, and:
   - add a `### Review round <n>` subsection under Implementation details listing each finding as fixed, disputed, or deferred with the `path:line` changed;
   - add any new deviation (including each disputed finding, with why it does not hold) to Plan deviations;
   - refresh Quality gates and Changed files to the post-fix state;
   - remove or correct any statement the fixes made false.

Report each finding as fixed, disputed, or deferred, with the `path:line` you changed.
