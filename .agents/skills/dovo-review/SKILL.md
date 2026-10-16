---
name: dovo-review
description: >-
  Review a Dovo CLI change for bugs and regressions first, then plan fidelity,
  then violations of the AGENTS.md rules R1–R17, and write line-level findings
  to .agentic/review.md and a machine-readable verdict to .agentic/review.json.
  Invoked as /dovo-review [<pr-number>] [--post].
disable-model-invocation: true
---

# dovo-review

Review a change set in `Dovo CLI`, in this order of importance:

1. **Correctness**: bugs, regressions and broken edge cases in what the diff does.
2. **Plan fidelity**: does the code implement `.agentic/plan.md`'s current phase, no more and no less.
3. **Rules**: does the diff break any of the 17 rules in [AGENTS.md](../../../AGENTS.md).
4. **Docs**: did the change update the docs it had to, and nothing else.

Output goes to `.agentic/review.md`, which `/dovo-code review` consumes, paired with `.agentic/review.json` and `.agentic/verdict.txt`. A human reviews the PR after you; your job is to make that review short.

## Hard boundaries

- **Never run tests or tooling.** No `dovo run`, `pytest`, `ruff` or `basedpyright`; `/dovo-code` owns the gate. Read-only `git`, read-only `gh`, `rg` and file reads are enough.
- **Never edit, stage, commit or push.** Report findings and hand off.
- **Re-read from disk.** Every file you reason about is read fresh in this session.
- **Every finding cites a `path:line`** and says what goes wrong, not just which rule it touches.

## 1. Resolve the diff scope

| Input | Scope | Inspect with |
|---|---|---|
| PR number given | that PR's range | `gh pr view <n> --json baseRefName,headRefName,title,body`, then `git fetch origin` and `git diff origin/<base>...origin/<head>` |
| Working tree dirty | uncommitted work | `git status --porcelain`, `git diff`, `git diff --staged`, plus untracked files in scope |
| Clean tree | commits on this branch | `gh repo view --json defaultBranchRef --jq .defaultBranchRef.name` for `<default>`, then `git diff origin/<default>...HEAD` |

Record the changed-file list and `git diff --stat`. If the scope is empty, say so and stop.

## 2. Review from a fresh context

A reviewer that shares the author's context inherits its blind spots. If this session planned or wrote the code, delegate the review to a fresh subagent with a strong reasoning model, preferably from a different family than the author. Give it the Hard boundaries above as its first lines, the scope from section 1, and nothing else: no rationale and no "I already checked X". Set `provenance` to `fresh_session` when the review ran in a fresh context, `same_session` otherwise. If no subagent can be launched, say so and do not approve.

## 3. Load the context

Read [AGENTS.md](../../../AGENTS.md), `.agentic/plan.md` if it exists (the phase under review is the contract), and `.agentic/implementation-report.md` if it exists. The report's **Plan deviations** are the author's claims to verify, never evidence that the code is correct. Read the exemplar nearest to each changed file, since R5 asks new code to look like it.

## 4. Hunt for bugs

Read every changed function and its callers. Look for wrong behaviour, not style:

- inputs the code mishandles (empty, missing, duplicate, concurrent, very large, non-UTF-8, Windows paths)
- error paths that lose the error, exit 0 on failure, or leave partial state on disk or in the database
- a changed result, flag or JSON field that a caller, formatter, blueprint or doc still expects in its old shape
- tests that pass without exercising the change: wrong fixture, an assertion on a value the code does not produce, a check whose input set can be empty
- missing tests for a branch the diff adds

A bug is Blocking when it can produce a wrong result, data loss, a crash or a broken public surface; otherwise a Warning.

## 5. Check plan fidelity

Skip this when `.agentic/plan.md` is absent, and say so.

- Every FR in the phase landed, with the contracts written exactly: field names, types, defaults, status values, flags, help text, exit codes and messages. A "better" name than the plan's is a finding.
- Nothing from a later phase, out of scope, or named as a trap landed.
- Each test stub in the phase exists and asserts its stated contract, and every new test file traces back to a stub.
- The phase's deletion ledger is executed.
- Each difference from the plan appears under the report's Plan deviations. A documented deviation that is compliant goes under `Documented deviations`; one that breaks a rule or a literal contract is still a finding; an undocumented one is a finding for silent deviation.

## 6. Check the rules

Go through R1–R17 in [AGENTS.md](../../../AGENTS.md) against the diff. Report **only violations**, each as a finding with the rule id and the `path:line` that breaks it. Do not list rules that pass, and do not report a rule the diff cannot touch. A rule violation is Blocking unless it is cosmetic in context, in which case say why it is a Warning.

Do not re-check what tooling enforces: import direction, output routing, direct file writes, model strictness, explicit `Any`, ignore reasons and docstring presence are covered by `tests/lint/`, ruff and basedpyright. Do flag a change that weakens that tooling: a new allowlist or baseline entry, a new per-file ignore, or a suppression.

## 7. Check the docs

Work through [doc-adherence.md](doc-adherence.md) with the changed-file list. A missing required doc update is Blocking, and so is a doc sentence describing behaviour that was not built (R17). A doc update no gate required is a Suggestion to remove it.

## 8. Write the report

Write `.agentic/review.md` (create `.agentic/` if needed), overwriting the previous round, and echo the verdict and the Blocking list in chat:

```markdown
## dovo-review - round <n>

**Verdict:** APPROVE | CHANGES REQUIRED
**Scope:** <PR #n | uncommitted | origin/<default>...HEAD> (<k> files, <+a/-d> lines)
**Plan:** `.agentic/plan.md` phase <p> | absent
**Provenance:** fresh session | same session that wrote the code

### Blocking

| ID | Location | Rule | Issue | Fix |
|---|---|---|---|---|
| B1 | `path:line` | `R6` | What goes wrong, and when. | The concrete fix. |

### Warnings

| ID | Location | Rule | Issue | Fix |
|---|---|---|---|---|

### Suggestions

| ID | Location | Rule | Issue | Fix |
|---|---|---|---|---|

### Nits

| ID | Location | Rule | Issue | Fix |
|---|---|---|---|---|

### Plan fidelity
- <FR-n> - as specified | deviates: <what> (documented | silent) | missing
- Documented deviations: <item> - accepted | still flagged (<rule>) | none
- Unplanned test files: <paths or "none">
- Deletion ledger: executed | <outstanding>

### Not verified
- Gates were not run by this skill. Risks read from the diff: <complexity, coverage or typing risks, or "none">
```

`Rule` is an R-id, or `-` for a bug or plan finding with no rule behind it. An empty section keeps its heading with the line `none`. IDs are the tier letter plus a number from 1 within the tier, in row order, reassigned every round; `/dovo-code review --fix` consumes them.

Say `APPROVE` only with zero Blocking findings.

With `--post` and a PR scope, post the report as a comment review: `gh pr review <n> --comment --body-file .agentic/review.md`. Never `--approve` or `--request-changes`.

On `CHANGES REQUIRED`, hand off to `/dovo-code review`, then re-run this skill as round n+1. **Cap at 3 rounds**; at the cap, list every unresolved finding under a `### Residual` heading and in `review.json` under `residual`, for the human.

## 9. Emit the machine-readable verdict

Write `.agentic/review.json` and `.agentic/verdict.txt` alongside the markdown, and print the JSON minified as the last line of stdout. `bin/dovo-loop.py` and `.dovo/catalog/blueprints/feature-dev.yml` branch on these files.

```json
{
  "verdict": "CHANGES_REQUIRED",
  "round": 1,
  "provenance": "fresh_session",
  "scope": { "kind": "pr", "ref": "364", "files": 7 },
  "plan": ".agentic/plan.md",
  "counts": { "blocking": 1, "warnings": 1, "suggestions": 0, "nits": 0 },
  "findings": [
    { "id": "B1", "severity": "BLOCKER", "path": "src/dovo/core/sessions/sessions.py", "line": 42, "rule": "R4", "summary": "Raises SessionNotFound instead of returning a NOT_FOUND result." }
  ],
  "residual": []
}
```

- `verdict` is exactly `APPROVE` or `CHANGES_REQUIRED`, and is `APPROVE` iff `counts.blocking` is `0`.
- `provenance` is `fresh_session` or `same_session`. `scope.kind` is `pr`, `uncommitted` or `branch`. `plan` is the plan path or `null`.
- `findings` lists every finding, Blocking first, each with the `id` from its `review.md` row. `severity` is `BLOCKER`, `WARNING`, `SUGGESTION` or `NIT`; `rule` is an R-id or `null`.
- `.agentic/verdict.txt` holds exactly the `verdict` value and a trailing newline.
- `residual` stays `[]` when there is none.
