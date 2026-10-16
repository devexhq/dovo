---
name: dovo-review
description: >-
  Meticulous compliance review agent for Dovo CLI. Audits diffs against
  docs/agents/REVIEW_CHECKLIST.json, checking plan fidelity, implementation quality,
  architectural invariants, redundancy, and whether the change's own enforcement code
  can actually fail. Rejects with line-level findings if any BLOCKER clause is breached.
  Invoked as /dovo-review [<pr-number>] [--post].
disable-model-invocation: true
---

# dovo-review

Review a change set in `Dovo CLI` on five axes:

1. **Plan fidelity**: does the code implement `.agentic/plan.md`'s contracts, no more and no less.
2. **Implementation and invariant compliance**: does the code hold up (correctness, layering, typing, tests, performance, DB hygiene).
3. **Doc adherence**: does it obey every directive in `AGENTS.md` and `docs/agents/*.md`, and does it update the docs this change was required to update.
4. **Redundancy and subtraction**: does anything here duplicate a contract already pinned, or survive with no consumer.
5. **Enforcement integrity**: can every check this change adds or relies on actually fail.

Output goes to `.agentic/review.md`, which `/dovo-code review` consumes, paired with `.agentic/review.json` and `.agentic/verdict.txt`. The checklist sweep is recorded in `.agentic/review-audit.json`, which `scripts/change_audit.py` generates and validates.

## Hard boundaries

- **Never run tests or tooling.** No `dovo run tests`, no `dovo run quality`, no `pytest`, no `ruff`, no `basedpyright`. `/dovo-code` owns the gate; a review that leans on tool output stops reading the code. Read-only `git`, read-only `gh`, `rg`, and file reads are what you need.
- **Never edit, stage, commit, or push.** Report findings and hand off. `/dovo-code review` applies the fixes.
- **Never approve on absence of evidence.** A rule you did not check is not a rule that passed.
- **Never treat a passing suite as a verified invariant.** The checks most likely to be broken are the ones reporting success, because a scanner that inspects nothing is indistinguishable from a clean tree.
- **Re-read from disk.** Every file you reason about is read fresh in this session, never recalled from earlier context.
- **Never open `.agentic/coding-audit.json` until your own audit validates.** The author's self-audit is a claim to compare against, never an input to your verdicts. Read it only through `change_audit.py compare` and `validate --role coding` in section 8, after `validate --role review` has exited 0, and never to decide a verdict you have not yet recorded.
- **Write the audit only through the script.** `.agentic/review-audit.json` is created by `change_audit.py generate` and filled by `change_audit.py answer`; do not hand-edit it.

## 1. Resolve the diff scope

| Input | Scope | Inspect with |
|---|---|---|
| PR number given | that PR's range | `gh pr view <n> --json baseRefName,headRefName,title,body`, then `git fetch origin` and `git diff origin/<base>...origin/<head>` |
| Working tree dirty | uncommitted work | `git status --porcelain`, `git diff`, `git diff --staged`, plus full contents of untracked files in scope |
| Clean tree | commits on this branch | `git diff origin/<default>...HEAD` (resolve the default with `gh repo view --json defaultBranchRef`) |

Record the changed-file list and the diff size (`git diff --stat`). If the scope is empty, say so and stop.

## 2. Delegate for independence

In a single-agent setup, the author and reviewer share one context, so the review inherits the author's assumptions and blind spots. **A same-session review is a weak control and must declare itself**: if this session planned or wrote the code, set `provenance` to `same_session` in the eventual report, re-derive every finding from the diff and the docs, and discard in-session claims that something was already verified or intentional. The quoted-evidence requirement in section 5 exists precisely for this case: in same-session mode, treat any clause you cannot support with a quoted line as `FAIL`, not `PASS`.

**If this session wrote any of the code under review, delegate the audit to a fresh subagent** instead of reviewing it yourself. Give it a strong reasoning model, preferably a different family than the one that wrote the code. A spawned subagent has no read-only switch of its own, so restate the Hard boundaries above verbatim in its instructions and make them the first thing it reads. Hand it the resolved scope from section 1, the standards to load in section 3, and nothing else: no rationale, no "I already verified X". Let it read the standards and run the sweep itself, and have it set `provenance` to `fresh_session` in its report. Tell it, in the same first lines, never to open `.agentic/coding-audit.json` before its own audit validates; it generates, answers and validates `.agentic/review-audit.json` itself and returns the comparison from section 8 with its findings.

If a subagent cannot be launched, say so and do not self-approve. Fail closed unless the user explicitly overrides.

When this review is already running in a fresh session with no author bias to correct for, run it inline — there is no one to delegate to.

## 3. Load the standards yourself

Do not review from memory of this repo. Read [AGENTS.md](../../../AGENTS.md) — the authority and the doc index for what governs what — the docs it names for the areas the diff touches, the domain-scoped `RULES.md` files, and [REVIEW_CHECKLIST.json](../../../docs/agents/REVIEW_CHECKLIST.json). Read `.agentic/plan.md` if it exists, as the change's contract. Also read `.agentic/implementation-report.md` if it exists: its **Plan deviations** section is the author's declared list of departures from the plan. It is a claim to verify against the diff, never evidence that the code is correct. It gives only counts for the author's self-audit; the audit file itself stays closed until section 8.

When a doc's claim about a model, field list, or enum drives a finding, spot-check the source first. Source wins. **A stale doc is a finding, not a condition to route around**: a "enforced by" claim naming a file that doesn't exist, a documented helper that was never written, or a rule example that teaches the violation it forbids. Resolve every such claim that bears on this change (`rg --files -g '<name>'`) rather than working around a wrong sentence and leaving it for the next agent to trust.

## 4. Check plan fidelity

Skip this axis only when `.agentic/plan.md` is absent, and say so in the report.

- Every FR has landed, and every artifact row has its file.
- Contracts match exactly: field names, types, defaults, `status` values, flag names, help copy, exit codes, and error, warning, and fix strings. A "better" name than the plan's is a finding, since the plan was human-reviewed.
- Nothing landed that the plan marked out of scope or named as a trap.
- **Test stub fidelity, one by one.** Each `### Tests` stub the plan wrote exists at its planned test file, asserting the docstring's stated contract. Then check the other direction: every test file in the diff traces back to a stub. An unplanned test file is a scope breach that no gate catches.
- **Deletion ledger fidelity.** Every entry is gone.
- **Budget.** Compare the diff size against the plan's estimate.
- Where the code deviates, the deviation was surfaced rather than absorbed silently. Match each difference you find against the implementation report's Plan deviations:
  - **Documented and still compliant**: do not raise a plan-fidelity finding. List it under `Documented deviations` in the report so the human sees it.
  - **Documented but it breaches a repo rule, a checklist clause, or a literal plan contract** (field name, message, flag, exit code) the plan marks binding: still a finding, cited under that rule. Documenting a breach does not waive it.
  - **Documented but the diff does not match the description**: a finding; the report is inaccurate.
  - **Not documented**: a plan-fidelity finding for silent deviation, as before.
  - **Report absent**: say so; every deviation is undocumented.
  Also verify the report's Changed files and gate numbers against the diff; a mismatch is a Warning.

## 5. Sweep the mechanical rules and invariant checklist

The rules that get missed are the ones no linter enforces, and they are missed because reviewers read for design and skim identifiers. Do this as an explicit pass, not a byproduct.

1. **Generate your own audit.** Start every round from a fresh file, so no answer from an earlier round survives into a diff it was not made against, then generate it for the scope from section 1. The script matches each changed path to the rules whose `scope` encompasses it (`src/dovo/core/` matches `ARCH-*`, formatters match `RENDER-*`, `**/models.py` matches `MODEL-*`, `tests/` matches `TEST-*`) and writes one unanswered entry per criterion:

   ```bash
   rm -f .agentic/review-audit.json
   uv run python scripts/change_audit.py generate --role review                       # uncommitted work, or the branch when the tree is clean
   uv run python scripts/change_audit.py generate --role review --range origin/<base>...origin/<head>   # a PR scope
   ```

   Rules whose scope names no path (commit and PR process rules) are listed under `skipped_rules`; check those by hand where they bear on the change.
2. **Answer every criterion** against the diff and the code, line by line. `verdict` is `PASS`, `FAIL` or `N/A`. `evidence` is never empty: quote the `path:line` you read. A `FAIL` must cite a `path:line`, and an `N/A` states why the entry's `na_when` holds. Record answers in batches; `answer` keeps every answer you did not name:

   ```bash
   uv run python scripts/change_audit.py answer --role review --from - <<'JSON'
   {"TEST-011.Q01": {"verdict": "FAIL", "evidence": "tests/engine/executors/test_process_group.py:27 `time.sleep(0.05)`"}}
   JSON
   ```

3. **Validate before you write anything else.** `uv run python scripts/change_audit.py validate --role review` must exit 0: no criterion unanswered, none missing, none reworded. An unanswered criterion is a rule you did not check, and the report cannot say `APPROVE` over it.
4. **Classify severity:**
    - `BLOCKER` (architectural drift, concurrency risk, boundary leaks, runtime crashes, `assert` in `src/`, assertions on human-rendered output, an enforcement check that cannot fail, a breached plan contract, a missing required doc update),
    - `WARNING` (high-impact convention or type degradation),
    - `SUGGESTION` (constructive improvement not required now),
    - `NIT` (cosmetic, no rule behind it).

Every naming, model, placement, typing, and test convention a reviewer must check by eye — because no linter here enforces it — is a rule in `REVIEW_CHECKLIST.json`: step 1 of this pass is what puts them in scope, there is no separate checklist to consult.

Each `FAIL` in your audit becomes a finding at its criterion's `severity`, and each finding names the rule ID and the doc it comes from. If you cannot cite a rule, it is a Suggestion or a Nit, not Blocking.

## 6. Check doc adherence

Work through [doc-adherence.md](doc-adherence.md) with the changed-file list. It maps each kind of change to the directive it must satisfy and the doc that must have been updated in the same change.

A missing required doc update is Blocking. A doc update that was not required is a Suggestion to delete it. A doc paragraph describing behavior this change did not build is Blocking too.

When this change edits `docs/agents/rules_spec.yaml`, confirm the compiled artifacts were regenerated and committed in the same change (`uv run python scripts/compile_rules.py`), since the pre-commit parity hook fails on drift.

## 7. Judge what you cannot run

You are not running the gates, so reason about them from the diff and mark each as a risk rather than a result:

- Functions whose nesting or `elif` chains look likely to exceed cognitive complexity 10.
- Suppressions and `Any` annotations that would let `basedpyright --level error` pass while hiding a real error.
- Branches with no covering test, especially in a factory or dispatch chain.

Say `not run by this skill` for anything you are inferring, and keep it distinct from `not enforced by this repo`, which is a finding from section 7. Never report an inference as a gate result.

## 8. Compare with the coding audit

Only now, with every other axis done and `validate --role review` passing, open the author's self-audit:

```bash
uv run python scripts/change_audit.py compare        # exits 2 unless the review audit validates; lists every criterion where the two differ
uv run python scripts/change_audit.py validate --role coding
```

`compare` prints each criterion where the verdicts differ or only one audit has the entry, with the start of each side's evidence. Treat the coding audit as a second opinion, not a source of truth:

- **Coding `PASS`, review `FAIL`**: your finding stands. Note it in the report as a self-audit miss.
- **Coding `FAIL`, review `PASS`**: re-inspect the cited `path:line` in the coding evidence. If the defect is real, correct your answer with `answer --role review`, re-run `validate --role review` and raise the finding. If your `PASS` holds, say why in the comparison.
- **`N/A` against anything else**: re-read the criterion's `na_when` and the diff, and settle which side is right.
- **A criterion in only one audit**: the two audits scoped the change differently; check the changed-file list and say which side was wrong.

Never lower a verdict you recorded to agree with the author without a quoted line that shows it was wrong.

The coding audit being absent, or `validate --role coding` failing, is a Warning with rule `-`: the author did not complete the self-audit (`/dovo-code` requires it). It never changes a verdict you recorded.

## 9. Write the report

Write this to `.agentic/review.md` (create `.agentic/` if needed), overwriting the previous round, and echo the verdict plus the Blocking list in chat:

```markdown
## dovo-review - round <n>

**Verdict:** APPROVE | CHANGES REQUIRED
**Scope:** <PR #n | uncommitted | origin/<default>...HEAD> (<k> files, <+a/-d> lines)
**Plan:** `.agentic/plan.md` <sha or "absent">
**Provenance:** fresh session | same session that wrote the code

### Blocking

| ID | Location | Rule | Issue | Fix |
|---|---|---|---|---|
| B1 | `path:line` | `ARCH-001` (Strict Layered Import Flow) | What is wrong and the rule it breaks (`<package>/docs/RULES.md#ARCH-001` or `<doc>#<section>`). | The concrete fix. |

### Warnings

| ID | Location | Rule | Issue | Fix |
|---|---|---|---|---|
| W1 | `path:line` | `TEST-016` | Same shape as Blocking. | The concrete fix. |

### Suggestions

| ID | Location | Rule | Issue | Fix |
|---|---|---|---|---|
| S1 | `path:line` | - | Issue, no rule ID required. | The concrete fix. |

### Nits

| ID | Location | Rule | Issue | Fix |
|---|---|---|---|---|
| N1 | `path:line` | - | Minor style or wording observation. | The concrete fix. |

| Rule ID | Clause | Severity | Status | Evidence (quoted) |
|---|---|---|---|---|
| `TEST-007` | assert relevant domain invariants | SUGGESTION | FAIL | `tests/cli/worktree/test_diff.py:88` mechanically asserts empty default envelope fields instead of targeted domain invariants |
| `TEST-011` | no wall-clock sleeps | BLOCKER | FAIL | `tests/engine/executors/test_process_group.py:27` reads `time.sleep(0.05)` |

### Plan fidelity
- Implementation report: `.agentic/implementation-report.md` read | absent
- Documented deviations: <report item> - accepted (compliant) | still flagged (<rule>) | report inaccurate | none
- <FR-n> - implemented as specified | deviates: <what> (documented | silent) | missing
- Test stubs: <k> planned stubs landed at path and marker | <deviations>
- Unplanned test files: <paths or "none">
- Deletion ledger: executed | <outstanding>
- Budget: planned <n> lines, actual <m>

### Redundancy
- <path> - duplicates <path> (<contract>); recommend delete | none

### Enforcement integrity
- <check> - can fail (negative fixture at <path:line>) | vacuous: <why> | scope narrower than claim
- Unenforced gates relied on by this change: <gate and where the gap is, or "none">

### Doc adherence
- <gate tripped> - satisfied | missing update to <doc> | not applicable
- Unresolved enforcement claims: <doc:line naming a nonexistent file, or "none">

### Audit comparison
- Review audit: `.agentic/review-audit.json` validated, <k> criteria, <f> FAIL
- Coding audit: validated | invalid: <why> | absent
- <criterion id> - coding <verdict>, review <verdict>: <resolution, with the quoted line that settled it> | none

### Unverified
- Gates were not run by this skill. Risks read from the diff: <complexity, coverage, typing risks, or "none">

### Residual (rounds exhausted only)
- <finding> - unresolved after round 3, handed to the human
```

Say `APPROVE` only with zero Blocking items and a review audit that `validate --role review` accepted. An empty section keeps its heading with the line `none` in place of the table. Each finding is one table row: `Location` is `path:line` (or `path` for a file-level finding) and `Rule` is `-` when no rule applies.

IDs are the tier letter plus a number starting at 1 within the tier, in the order the rows appear: `B#` for Blocking, `W#` for Warnings, `S#` for Suggestions, `N#` for Nits. They are reassigned every round, and `/dovo-code review --fix` consumes them.

With `--post` and a PR scope, post the same content as a comment review: `gh pr review <n> --comment --body-file .agentic/review.md`. Never `--approve` or `--request-changes`, and do not add reviewers.

On `CHANGES REQUIRED`, hand off to `/dovo-code review`, then re-run this skill as round n+1 against the updated scope. **Cap at 3 rounds.** At the cap, every unresolved finding goes in the `Residual` section, in `review.json` under `residual`, and, when the scope is a PR, into a posted comment.

## 10. Emit the machine-readable verdict

Write `.agentic/review.json` and `.agentic/verdict.txt` alongside the markdown and print the same object as the last line of stdout, minified onto one line. A caller decides the next step from these files alone.

```json
{
  "verdict": "CHANGES_REQUIRED",
  "round": 1,
  "provenance": "same_session",
  "scope": { "kind": "pr", "ref": "364", "files": 7 },
  "plan": ".agentic/plan.md",
  "counts": { "blocking": 2, "warnings": 1, "suggestions": 3, "nits": 1 },
  "findings": [
    { "id": "B1", "severity": "BLOCKER", "path": "src/dovo/core/sessions/sessions.py", "line": 42, "rule": "ARCH-001", "summary": "Core service imports CliContext directly." }
  ],
  "unenforced_gates": ["coverage fail_under=0 in pyproject.toml"],
  "audit": { "file": ".agentic/review-audit.json", "criteria": 182, "fail": 2, "coding": "validated", "disagreements": 3 },
  "residual": []
}
```

Field contracts, since this is what a loop branches on:

- `verdict` is exactly `APPROVE` or `CHANGES_REQUIRED` (underscored, so it survives shell and condition matching), and is `APPROVE` iff `counts.blocking` is `0`.
- `provenance` is `fresh_session` or `same_session`. `scope.kind` is `pr`, `uncommitted`, or `branch`.
- `plan` is the plan path, or `null` when absent.
- `findings` carries every finding of all four tiers with line-level detail, Blocking first, then Warnings, Suggestions and Nits; `counts` always reflects the full report. `severity` is `BLOCKER`, `WARNING`, `SUGGESTION` or `NIT`, and `rule` is `null` when none applies.
- Each finding's `id` is the one in the `review.md` table row it comes from (`B#`, `W#`, `S#` or `N#`) and is unique within the file, so a caller can offer the findings for selection and pass the chosen ids to `/dovo-code review --fix`.
- `.agentic/verdict.txt` holds exactly the `verdict` value (`APPROVE` or `CHANGES_REQUIRED`) and a trailing newline, overwritten every round, so a shell condition can test it without parsing JSON.
- `unenforced_gates` and `residual` stay `[]` when there are none.
- `audit` summarises the two audits: `criteria` and `fail` count the review audit, `coding` is `validated`, `invalid` or `absent`, and `disagreements` is the number `compare` listed (`null` when the coding audit is absent or invalid).

Changes to this shape are additive only. It is the contract a driver script consumes today, and the `outputs` condition a `dovo` blueprint will branch on later, so a renamed field breaks both.
