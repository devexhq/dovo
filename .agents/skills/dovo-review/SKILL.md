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
2. **Plan fidelity**: does the code implement every phase of `.agentic/plan.md`, no more and no less.
3. **Rules**: does the diff break any of the 17 rules in [AGENTS.md](../../../AGENTS.md).
4. **Docs**: did the change update the docs it had to, and nothing else.

Output goes to `.agentic/review.md`, which `/dovo-code review` consumes, paired with `.agentic/review.json` and `.agentic/verdict.txt`. A human reviews the PR after you; your job is to make that review short.

## Hard boundaries

- **Never run the quality gate, and never run anything in the reviewed checkout.** No `dovo run quality`, `ruff` or `ty`. To verify a blocker in the user's own code you may run scoped tests or a mutation in a scratch worktree at the reviewed commit (section 4), then remove it. Read-only `git`, read-only `gh`, `rg` and file reads cover everything else.
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

Read [AGENTS.md](../../../AGENTS.md), [docs/agents/code-conventions.md](../../../docs/agents/code-conventions.md), and the exemplar nearest to each changed file, since R5 asks new code to look like it. Do not open `.agentic/plan.md` or `.agentic/implementation-report.md` yet: the bug hunt reads the diff alone, so the plan's choices cannot pass for correctness. Section 5 reads them.

## 4. Hunt for bugs

Read every changed function and its callers. Look for wrong behaviour, not style:

- inputs the code mishandles (empty, missing, duplicate, concurrent, very large, non-UTF-8, Windows paths)
- every form an argument or input accepts (a number, a URL, a branch name, a path): follow each form through the code, not only the one the caller uses today
- an identity checked in one place and acted on in another: the PR, repository, commit, path or user that was verified must be the one that is written to
- data from a producer (a skill's JSON, a config file, another step's output) trusted as consistent: check that the fields the code relies on cannot contradict each other
- error paths that lose the error, exit 0 on failure, or leave partial state on disk or in the database, including a failure policy that turns a failure into success (`on_failure: continue`, `|| true`, `check=False` with the result ignored)
- a changed result, flag or JSON field that a caller, formatter, blueprint or doc still expects in its old shape
- tests that pass without exercising the change: wrong fixture, an assertion on a value the code does not produce, a check whose input set can be empty
- missing tests for a branch the diff adds

A bug is Blocking when it can produce a wrong result, data loss, a crash or a broken public surface; otherwise a Warning.

**Verify each blocker before reporting it.** Re-read the cited `path:line` and the code it depends on. When a scoped test or a one-line mutation can prove or disprove it, the scope is a commit (not uncommitted work), and the code is the user's own (a local branch, or a PR whose `gh pr view <n> --json isCrossRepository,author` shows `false` and the login `gh api user --jq .login` prints), run it in a scratch worktree. Running tests executes the code under review, so for any other PR verify by reading only, and say so under Review coverage:

```bash
scratch=$(mktemp -d) && git worktree add -q --detach "$scratch" <reviewed-sha>
(cd "$scratch" && uv sync -q --frozen --all-extras && uv run dovo run tests --path <test-file>)
git worktree remove --force "$scratch"
```

Record every command and its one-line result for Review coverage. A finding the evidence disproves goes under Rejected findings with the reason; it is never silently dropped and never reported as a finding.

## 5. Check plan fidelity

Skip this when `.agentic/plan.md` is absent, and say so. Otherwise read it now, with `.agentic/implementation-report.md` if it exists: all of the plan's phases together are the contract, and the report's **Plan deviations** are the author's claims to verify, never evidence that the code is correct.

A plan decision is not evidence of correctness. When code that follows the plan is still wrong, keep the section 4 finding, cite the plan decision behind it, and set `action` to `replan` when fixing it means changing that decision. Contract coverage `met` says the code does what the plan says, not that what the plan says is right.

- **Contract coverage.** Give every row of the plan's `## Traceability` table (each FR, NFR, numbered error case and GWT scenario) a verdict against the whole change at the reviewed commit: `met` with the evidence (the test and the assertion that proves it, read from the assertion, not the test name, or the code path), `unmet` with what is missing, or `deferred` with the later step that owns it, such as CI. A row whose cited test stubs out what the requirement names is `unmet`. So is a row whose literal contract (the field names, types, defaults, status values, flags, help text, exit codes and messages the plan writes as code) landed differently: a "better" name than the plan's is still `unmet`. Every `unmet` row is also a BLOCKER finding with `rule` `null`, citing the requirement id.
- Nothing out of scope or named as a trap landed.
- Each test stub in every phase exists and asserts its stated contract, and every new test file traces back to a stub.
- Every phase's deletion ledger is executed.
- Each difference from the plan appears under the report's Plan deviations. A documented deviation that is compliant goes under `Documented deviations`; one that breaks a rule or a literal contract is still a finding; an undocumented one is a finding for silent deviation.

## 5a. Account for the previous round

From round 2, read the previous `.agentic/review.json` before you overwrite it, and `.agentic/implementation-report.md`'s latest `### Review round <n>` or `### Feedback round <n>`, which lists the author's claims. In a `### Feedback round <n>`, each `R#` maps to the previous finding its `.agentic/feedback.json` `body` names (`B1: ...`); `T#`, `C#` and `K#` items are review threads, comments and failed checks, not previous findings. Give every previous finding an outcome: `fixed` when the code at the reviewed commit resolves it, `disputed` when the author disputed it and the evidence supports them, or `still_open`, which you report again under its new id. An author's claim is a claim to verify, not evidence.

Treat a `review.json` on disk as the previous round only when its `round` is one less than this round and its `head` is an ancestor of the reviewed commit (`git merge-base --is-ancestor <head> <reviewed-sha>`); a file left by an earlier, unrelated run makes this round 1.

## 6. Check the rules

Go through R1–R17 in [AGENTS.md](../../../AGENTS.md) and the conventions in [docs/agents/code-conventions.md](../../../docs/agents/code-conventions.md) (naming, model placement, `Any` and type-ignore rules) against the diff. Report **only violations**, each as a finding with the rule id (or `conventions` for a conventions-doc item) and the `path:line` that breaks it. Do not list rules that pass, and do not report a rule the diff cannot touch. A rule violation is Blocking unless it is cosmetic in context, in which case say why it is a Warning; a naming violation is a Nit. Copying a neighbour does not excuse a convention violation in new code: R5 asks new work to follow its neighbours' layout and idiom, not their mistakes.

Do not re-check what tooling enforces: import direction, output routing, direct file writes, model strictness, ignore reasons and docstring presence are covered by `tests/lint/`, ruff and ty. Ruff `ANN401` rejects `Any` as a parameter or return annotation, and `tests/lint/test_explicit_any.py` caps the remaining explicit `Any` per file, so a change that raises an `ANY_BUDGET` entry must justify each new `Any` against the permitted list in `docs/agents/code-conventions.md`. Do flag a change that weakens that tooling: a new allowlist or baseline entry, a new per-file ignore, or a suppression.

## 7. Check the docs

Work through [doc-adherence.md](doc-adherence.md) with the changed-file list. A missing required doc update is Blocking, and so is a doc sentence describing behaviour that was not built (R17). A doc update no gate required is a Suggestion to remove it.

## 8. Write the report

Write `.agentic/review.md` (create `.agentic/` if needed), overwriting the previous round, and echo the verdict and the Blocking list in chat:

```markdown
## dovo-review - round <n>

**Verdict:** APPROVE | CHANGES REQUIRED · **Action:** none | fix | replan | needs_human
**Reviewed commit:** `<full sha>` | uncommitted changes
**Scope:** <PR #n | uncommitted | origin/<default>...HEAD> (<k> files, <+a/-d> lines)
**Plan:** `.agentic/plan.md` (<n> phases) | absent
**Provenance:** fresh session | same session that wrote the code

<one sentence: why this verdict>

### Contract coverage

| Requirement | Verdict | Evidence |
|---|---|---|
| FR-1 <short name> | met | `tests/cli/x/test_y.py::test_z` asserts exit 2 and `{"status": "not_found"}` |

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

### Earlier rounds

| Round | ID | Finding | Outcome |
|---|---|---|---|
| 1 | B1 | <summary> | fixed \| disputed \| still open as B2 |

### Rejected findings

| Location | Finding | Why it does not hold |
|---|---|---|

### Plan fidelity
- Documented deviations: <item> - accepted | still flagged (<rule>) | none
- Unplanned test files: <paths or "none">
- Deletion ledger: executed | <outstanding>

### Review coverage
- Checks run: `<command>` - <result>, one per line, or "none"
- Not run: the quality gate (owned by `/dovo-code` and CI). <anything else you could not inspect>
```

Contract coverage reads "No contract: `.agentic/plan.md` absent" when there is no plan. Earlier rounds reads `none` in round 1.

**Action** follows from the verdict: `none` exactly when the verdict is `APPROVE`; `fix` when every Blocking finding can be fixed inside the plan; `replan` when a Blocking finding needs a different design from the plan's; `needs_human` when only a person can settle what is left (a decision the plan does not make, or evidence no agent here can obtain), named in the one-sentence reason.

`Rule` is an R-id, `conventions` for a `docs/agents/code-conventions.md` item, or `-` for a bug or plan finding with no rule behind it. An empty section keeps its heading with the line `none`. IDs are the tier letter plus a number from 1 within the tier, in row order, reassigned every round; `/dovo-code review --fix` consumes them.

Say `APPROVE` only with zero Blocking findings.

With `--post` and a PR scope, run `uv run python scripts/publish_review.py --pr <n>`. It posts or edits this PR's one review comment, and refuses when the PR head is not the reviewed commit. Never `gh pr review --approve` or `--request-changes`. A blueprint's `post-review` step adds `--session <id>`, which stamps a `Dovo session:` header line naming the run whose state `pr-fix` restores. A post without `--session` rewrites the header without that line, so the next `pr-fix` run stops until a blueprint posts again.

On `CHANGES REQUIRED`, hand off to `/dovo-code review`, then re-run this skill as round n+1. **Cap at 3 rounds**; at the cap, list every unresolved finding under a `### Residual` heading and in `review.json` under `residual`, for the human.

## 9. Emit the machine-readable verdict

Write `.agentic/review.json` and `.agentic/verdict.txt` alongside the markdown, and print `verdict`, `action`, `round`, `head` and `counts` as one minified JSON object on the last line of stdout. `.dovo/catalog/blueprints/feature-dev.yml` fixes until `review.json` lists no `BLOCKER` or `WARNING` finding, `scripts/pr_feedback.py` reads `review.json`'s findings, and `/dovo-code review` reads the ids.

```json
{
  "verdict": "CHANGES_REQUIRED",
  "action": "fix",
  "round": 2,
  "head": "1ee14986c0ffee0000000000000000000000beef",
  "provenance": "fresh_session",
  "scope": { "kind": "branch", "ref": "origin/main...HEAD", "files": 7 },
  "plan": ".agentic/plan.md",
  "counts": { "blocking": 1, "warnings": 1, "suggestions": 0, "nits": 0 },
  "findings": [
    { "id": "B1", "severity": "BLOCKER", "path": "src/dovo/core/sessions/sessions.py", "line": 42, "rule": "R4", "summary": "Raises SessionNotFound instead of returning a NOT_FOUND result." }
  ],
  "coverage": [
    { "requirement": "FR-1", "verdict": "met", "evidence": "tests/cli/session/test_session_show.py::test_missing asserts exit 2" }
  ],
  "history": [
    { "round": 1, "id": "B1", "summary": "Lock not held around the state write.", "outcome": "fixed", "now": null }
  ],
  "rejected": [
    { "path": "src/dovo/core/x.py", "line": 10, "summary": "Empty list crashes.", "reason": "Guarded at src/dovo/core/x.py:4; test_empty passes." }
  ],
  "checks": [
    { "command": "uv run dovo run tests --path tests/core/x/test_x.py", "result": "12 passed" }
  ],
  "residual": []
}
```

- `verdict` is exactly `APPROVE` or `CHANGES_REQUIRED`, and is `APPROVE` iff `counts.blocking` is `0`. `action` is `none`, `fix`, `replan` or `needs_human`, and is `none` iff `verdict` is `APPROVE`.
- `head` is the full SHA of the reviewed commit (`git rev-parse HEAD` for a clean branch, the PR's head commit for a PR scope), or `null` when the scope is uncommitted.
- `provenance` is `fresh_session` or `same_session`. `scope.kind` is `pr`, `uncommitted` or `branch`. `plan` is the plan path or `null`.
- `findings` lists every finding, Blocking first, each with the `id` from its `review.md` row. `severity` is `BLOCKER`, `WARNING`, `SUGGESTION` or `NIT`; `rule` is an R-id, `conventions`, or `null`.
- `coverage` has one entry per Contract coverage row; `verdict` is `met`, `unmet` or `deferred`. It is `[]` without a plan.
- `history` has one entry per previous-round finding; `outcome` is `fixed`, `disputed` or `still_open`, and `now` is its new id when `still_open`, otherwise `null`. It is `[]` in round 1.
- `rejected` and `checks` mirror Rejected findings and Review coverage's checks; each is `[]` when empty.
- `.agentic/verdict.txt` holds exactly the `verdict` value and a trailing newline.
- `residual` stays `[]` when there is none.
