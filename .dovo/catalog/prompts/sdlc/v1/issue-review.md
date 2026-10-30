Forked from `dovo-review` at `de256118`. This copy is temporary: once the slice flow works, it replaces `dovo-review`. Its output files and the `<!-- dovo-review -->` comment marker stay in `dovo-review`'s format, byte for byte, because the scripts that publish and read reviews parse them.

# issue-review

Review a change set in `Dovo CLI`, in this order of importance:

1. **Correctness**: bugs, regressions and broken edge cases in what the diff does.
2. **Slice fidelity**: does the code implement the slice's child issue and its phases of `.agentic/plan.md`, within the epic contract, no more and no less. Later slices' work is never a finding.
3. **Rules**: does the diff break any of the 18 rules in [AGENTS.md](../../../../../AGENTS.md).
4. **Docs**: did the change update the docs it had to, and nothing else.

Output goes to `.agentic/review.md`, which `/issue-code review` consumes, paired with `.agentic/review.json` and `.agentic/verdict.txt`. A human reviews the PR after you; your job is to make that review short.

## Hard boundaries

- **Never run the quality gate, and never run anything in the reviewed checkout.** No `dovo run quality`, `ruff` or `ty`. To verify a blocker in the user's own code you may run scoped tests or a mutation in a scratch worktree at the reviewed commit (section 4), then remove it. Read-only `git`, read-only `gh`, `rg` and file reads cover everything else.
- **Never edit, stage, commit or push.** Report findings and hand off.
- **Re-read from disk.** Every file you reason about is read fresh in this session.
- **Every finding cites a `path:line`** and says what goes wrong, not just which rule it touches.
- **Never read the author's report.** `.agentic/implementation-report.md` and `.agentic/slice-report.md` are not inputs: the slice's child issue, the epic contract and the code are.

## 1. Resolve the diff scope

| Input | Scope | Inspect with |
|---|---|---|
| PR number given | that PR's range | `gh pr view <n> --json baseRefName,headRefName,title,body`, then `git fetch origin` and `git diff origin/<base>...origin/<head>` |
| Working tree dirty | uncommitted work | `git status --porcelain`, `git diff`, `git diff --staged`, plus untracked files in scope |
| Clean tree | commits on this branch | `gh repo view --json defaultBranchRef --jq .defaultBranchRef.name` for `<default>`, then `git diff origin/<default>...HEAD` |

Record the changed-file list and `git diff --stat`. If the scope is empty, say so and stop.

Then resolve the slice from `.agentic/plan.json`: `--slice <id>` when given, otherwise the plan's only slice; when it has several and none is given, stop and name them. The slice's `child` is its child issue and `plan.json` `issue` is the epic. Without `plan.json`, there is no slice: review against the PR's linked issue as the contract, and say so.

## 2. Review from a fresh context

A reviewer that shares the author's context inherits its blind spots. If this session planned or wrote the code, delegate the review to a fresh subagent with a strong reasoning model, preferably from a different family than the author. Give it the Hard boundaries above as its first lines, the scope from section 1, and nothing else: no rationale and no "I already checked X". Set `provenance` to `fresh_session` when the review ran in a fresh context, `same_session` otherwise. If no subagent can be launched, say so and do not approve.

## 3. Load the context

Read [AGENTS.md](../../../../../AGENTS.md), [docs/agents/code-conventions.md](../../../../../docs/agents/code-conventions.md), the [docs/agents/architecture/](../../../../../docs/agents/architecture/README.md) files for the areas the diff touches, and the exemplar nearest to each changed file, since R5 asks new code to look like it. Do not open `.agentic/plan.md`, the child issue or the epic contract yet: the bug hunt reads the diff alone, so the plan's choices cannot pass for correctness. Section 5 reads them.

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

**Verify each blocker before reporting it.** Re-read the cited `path:line` and the code it depends on. When a scoped test or a one-line mutation can prove or disprove it, the scope is a commit (not uncommitted work), and the code is the user's own (a local branch, or a PR whose `gh pr view <n> --json isCrossRepository,author` shows `isCrossRepository: false` and an `author.login` equal to the login `gh api user --jq .login` prints), run it in a scratch worktree. Running tests executes the code under review, so for any other PR verify by reading only, and say so under Review coverage:

```bash
scratch=$(mktemp -d) && git worktree add -q --detach "$scratch" <reviewed-sha>
(cd "$scratch" && uv sync -q --frozen --all-extras && uv run dovo run tests --path <test-file>)
git worktree remove --force "$scratch"
```

Record every command and its one-line result for Review coverage. A finding the evidence disproves goes under Rejected findings with the reason; it is never silently dropped and never reported as a finding.

## 5. Check plan fidelity

Skip this when `.agentic/plan.md` is absent, and say so. Otherwise, now read the slice's child issue (`gh issue view <child> --json number,title,body`), `plan.json` `contract` (none when it is `null`), which is binding, and the slice's phases in `.agentic/plan.md`. Compare `contract` with the `## Feature contract` section of the epic's body (`gh issue view <epic> --json body`). The open questions the body lists under Decisions are the epic plan's, not part of `contract`, so they are never drift. When the body has that section and it says something else `contract` does not, report the drift as a Blocking finding located at `.agentic/plan.json` with `rule` `null`, citing each differing section, and set `action` to `needs_human`: only a person can say which version holds. Review against `contract` meanwhile. Together they are the contract: the child's FRs, NFRs, guards and Definition of done, the epic's target contract, conventions, decisions and shared-surface ownership, and the slice's phases. Other slices' phases and requirements are not part of it: work they own that has not landed is never a finding.

A plan decision is not evidence of correctness. When code that follows the plan is still wrong, keep the section 4 finding, cite the plan decision behind it, and set `action` to `replan` when fixing it means changing that decision. Contract coverage `met` says the code does what the plan says, not that what the plan says is right.

- **Contract coverage.** Give every FR, NFR, numbered error case, guard and Definition of done item of the child issue a row, one per part (`FR-7 (fixer)`) when the plan's Phases table splits it, and check each row obligation by obligation (each sentence a test could observe) against the slice's phases and the test lines that cite it. A row is `met` only when every obligation is. Give each row a verdict against the whole change at the reviewed commit: `met` with the evidence (the test and the assertion that proves it, read from the assertion, not the test name, or the code path), `unmet` with what is missing, or `deferred` with the later step that owns it, such as CI. A row whose cited test stubs out what the requirement names is `unmet`. So is a row whose observable contract landed differently from the literal the child issue's Pre-determined data or a phase's `### Code` pins: CLI behaviour and output, flags and help text, exit codes, `error_code` and status values, JSON fields, persisted formats, entrypoint signatures and result model fields, with their types and defaults ([planning.md](../../../../../docs/agents/planning.md) Step 1). There a "better" name than the plan's is still `unmet`. Every `unmet` row is also a BLOCKER finding with `rule` `null`, citing the requirement id. An internal shape the plan writes as code (a helper, private type or module name, a module's placement, a private signature) is the planner's best guess, not the contract: when it landed differently and the requirement's observable contract is met, the row is `met`, and the difference is a deviation (below), never a Blocking or Warning finding on its own.
- The change meets the slice's child issue and the epic contract's FRs, NFRs, guards and Definition of done; contract coverage above records each one. Later slices' work is not a finding.
- Nothing landed that an issue the plan lists under `## Owned elsewhere` owns, unless the plan names it as a departure, or it is a sentence the change made false (R17). The same holds for work another slice owns, or an issue the child names under **Owned elsewhere**.
- Unrequested surface is surface that serves no FR, NFR, guard or Definition of done item. It gets no finding when a rule requires it (R10 callers, R17 sentences) or the plan names it as a departure with an Open Question; otherwise it is a Warning when public and a Suggestion when internal.
- Each test line in every phase of the slice names a test that exists and asserts the outcome the line states, and every new test file traces back to a test line or is a characterization test of behaviour the slice must leave unchanged.
- Every deletion ledger entry of the slice's phases is executed.
- Judge each difference from the slice's phases on its own merits, without the author's account of it. One that breaks a rule, an observable contract or the epic contract is a finding: a Warning when it changes observable behaviour or lands outside the slice, unless it is already a Blocking coverage row. One that changes only an internal shape is not a finding; list it under `Documented deviations` as accepted.

## 5a. Account for the previous round

From round 2, read the previous `.agentic/review.json` before you overwrite it. Do not read the author's report for their claims. Give every previous finding an outcome from the code at the reviewed commit: `fixed` when it resolves the finding, `disputed` when re-checking the evidence shows the finding did not hold (record why under Rejected findings), or `still_open`, which you report again under its new id. A Blocking or Warning finding still open after a fix round that changed none of the lines it cites (`git diff <previous head> <reviewed> -- <path>`) has reached a disagreement another fix round will not settle: set `action` to `needs_human` and name the disagreement in the one-sentence reason.

On a pull request scope, before writing findings, read every review thread whose first comment is by you (`gh api user --jq .login`) and whose last line is a `<!-- dovo-finding … -->` marker, resolved threads included, with all its replies: `gh api graphql` over `reviewThreads`, with each thread's `id`, `isResolved`, `path`, `line`, `originalStartLine` and `originalLine`, and each comment's author login, `body`, `url` and `originalCommit { oid }`. Do not report a finding that is the same problem as such a thread when the thread holds a dismissal reply: a reply by you whose first line starts with `<!-- dovo-pr-fix` and whose second line starts with `Not changed:`. The exception is when the lines at that location changed between the thread's first comment's `originalCommit` and the reviewed commit: when a hunk of `git diff <oid> <reviewed> -- <path>` covers, on its old side, a line from the thread's `originalStartLine` (or `originalLine` when that is null) through its `originalLine`. A change elsewhere in the file does not count.

Also read your `pr-fix` outcome comments (`gh api repos/<owner>/<repo>/issues/<n>/comments`, each by you with the first line `<!-- dovo-pr-fix-outcomes -->`). Each later line reads `` `<finding id>` · `<location>` · <reviewed commit>: <outcome line> ``, where `<location>` may be left out. A line whose outcome starts with `Not changed:` dismisses an out-of-diff finding that is the same problem at that location, unless the lines there changed between that line's reviewed commit and this review's reviewed commit: when a hunk of `git diff <that commit> <reviewed> -- <path>` covers, on its old side, a line of the location's `path:line` or `path:<start>-<end>`, or any hunk at all when the location is `path` alone. When a commit either check needs is not in this checkout (`git cat-file -e <commit>^{commit}` fails), fetch it with `git fetch -q origin <commit>`. When it still cannot be read, treat the lines at that location as changed and report the finding: a kept duplicate is dismissed again by the next triage, but a wrongly dropped finding is never seen. Match outcome lines by location, never by finding id, which every review reassigns. List each finding left out this way under Rejected findings with the thread's or the comment's URL. A deferred finding that still holds is reported again; it is posted as a new thread, and the next `pr-fix` triage dismisses the duplicate.

A `review.json` on disk is the previous round only when it reviewed this same change: it parses, its `round` is a positive integer, its `head` is a commit in `origin/<default>..<reviewed>` (`git merge-base --is-ancestor <head> <reviewed>` succeeds and `git merge-base --is-ancestor <head> origin/<default>` fails), and, when both it and this review are pull request scopes, its `scope.ref` is this pull request's `#<n>`. This round is then its `round` plus one. Otherwise, including when the file is missing or unreadable, its `head` is `null`, or its `head` is already on `<default>` because its pull request merged, this is round 1: Earlier rounds reads `none` and `history` is `[]`. In every scope, `<default>` is the repository's default branch (`gh repo view --json defaultBranchRef --jq .defaultBranchRef.name`, then `git fetch origin <default>`), never a pull request's base, and `<reviewed>` is the reviewed commit, or `HEAD` for an uncommitted scope.

## 6. Check the rules

Check the diff against R1–R18 in [AGENTS.md](../../../../../AGENTS.md), the conventions in [docs/agents/code-conventions.md](../../../../../docs/agents/code-conventions.md) (naming, model placement, `Any` and type-ignore rules) and the entries in [docs/agents/architecture/](../../../../../docs/agents/architecture/README.md) for the areas the diff touches. Every entry binds new and changed code; existing code the diff does not change may predate it. Report **only violations**, each as a finding with the rule id (or `conventions` for a conventions-doc item, `architecture` for an architecture entry, naming the entry's file and heading) and the `path:line` that breaks it. Do not list rules that pass, and do not report a rule the diff cannot touch. A rule violation is Blocking unless it is cosmetic in context, in which case say why it is a Warning; a naming violation is a Nit. Copying a neighbour does not excuse a convention violation in new code: R5 asks new work to follow its neighbours' layout and idiom, not their mistakes.

Do not re-check what tooling enforces: import direction, output routing, direct file writes, model strictness, ignore reasons and docstring presence are covered by `tests/lint/`, ruff and ty. Ruff `ANN401` rejects `Any` as a parameter or return annotation, and `tests/lint/test_explicit_any.py` caps the remaining explicit `Any` per file, so a change that raises an `ANY_BUDGET` entry must justify each new `Any` against the permitted list in `docs/agents/code-conventions.md`. Do flag a change that weakens that tooling: a new allowlist or baseline entry, a new per-file ignore, or a suppression.

## 7. Check the docs

Work through [doc-adherence.md](issue-review-doc-adherence.md) with the changed-file list. A missing required doc update is Blocking, and so is a doc sentence describing behaviour that was not built (R17). A doc update no gate required is a Suggestion to remove it.

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
- Not run: the quality gate (owned by `/issue-code` and CI). <anything else you could not inspect>
```

A Location is `path:line`, or `path:<start>-<end>` for a range of lines.

Contract coverage reads "No contract: `.agentic/plan.md` absent" when there is no plan. Earlier rounds reads `none` in round 1.

**Action** follows from the verdict and what is left. On `APPROVE` it is `none`, or `needs_human` when a Warning is left that only a person can settle. On `CHANGES REQUIRED` it is `fix` when every Blocking finding can be fixed inside the plan, `replan` when a Blocking finding needs a different design from the plan's, and `needs_human` when only a person can settle what is left. Only a person can settle a finding whose fix needs a decision the plan does not make, goes back on a decision the plan made, or needs evidence no agent here can obtain; name that decision in the one-sentence reason. A finding you send to a person in the report always sets `needs_human`, because the `issue-code` blueprint stops fixing only on `needs_human` or `replan`.

`Rule` is an R-id, `conventions` for a `docs/agents/code-conventions.md` item, `architecture` for a `docs/agents/architecture/` entry, or `-` for a bug or plan finding with no rule behind it. An empty section keeps its heading with the line `none`. IDs are the tier letter plus a number from 1 within the tier, in row order, reassigned every round; `/issue-code review --fix` consumes them.

Say `APPROVE` only with zero Blocking findings.

With `--post` and a PR scope, run `uv run python scripts/publish_review.py --pr <n>`. It posts this round's findings as one pull request review: each finding goes inline on its line or line range of the diff, with its severity, a suggested fix and a prompt for an AI agent, or in the review body's outside-diff block. It never replies on or resolves a review thread, and it reuses the review on the reviewed commit only when that review is identical to this post. It then posts or edits this PR's one dovo-review comment, which holds `review.md` without its finding sections. It refuses when the PR head is not the reviewed commit. Never `gh pr review --approve` or `--request-changes`. A blueprint's `post-review` step adds `--session <id>`, which stamps a `Dovo session:` header line naming the run whose state `pr-fix` restores. A post without `--session` rewrites the header without that line, so the next `pr-fix` run stops until a blueprint posts again.

On `CHANGES REQUIRED`, hand off to `/issue-code review`, then re-run this prompt as round n+1. **Cap at 3 rounds**; at the cap, list the id of every unresolved finding, comma-separated on one line and never as a table, under a `### Residual` heading and in `review.json` under `residual`, for the human.

## 9. Emit the machine-readable verdict

Write `.agentic/review.json` and `.agentic/verdict.txt` alongside the markdown, and print `verdict`, `action`, `round`, `head` and `counts` as one minified JSON object on the last line of stdout. `.dovo/catalog/blueprints/issue-code.yml` fixes until `review.json` lists no `BLOCKER` or `WARNING` finding, `scripts/publish_review.py` posts `review.json`'s findings to the pull request, and `/issue-code review` reads the ids.

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
    { "id": "B1", "severity": "BLOCKER", "path": "src/dovo/core/sessions/sessions.py", "start_line": null, "line": 146, "rule": "R4", "summary": "Session.details lets SessionNotFound escape instead of returning a NOT_FOUND result.", "issue": "`Session.details` lets `SessionNotFound` propagate, so `dovo session show <unknown id>` exits with a traceback instead of exit 2.", "fix": "Catch the failure once with `catch_failure` in `Session.details` and return a result whose `error_code` is `NOT_FOUND`.", "also_affects": [] }
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

- `verdict` is exactly `APPROVE` or `CHANGES_REQUIRED`, and is `APPROVE` iff `counts.blocking` is `0`. `action` is `none` or `needs_human` when `verdict` is `APPROVE`, and `fix`, `replan` or `needs_human` otherwise.
- `head` is the full SHA of the reviewed commit (`git rev-parse HEAD` for a clean branch, the PR's head commit for a PR scope), or `null` when the scope is uncommitted.
- `provenance` is `fresh_session` or `same_session`. `scope.kind` is `pr`, `uncommitted` or `branch`, and `scope.ref` is `#<n>` for a `pr` scope and `origin/<default>...HEAD` for a `branch` scope. `plan` is the plan path or `null`.
- `findings` lists every finding, Blocking first, each with the `id` from its `review.md` row. `severity` is `BLOCKER`, `WARNING`, `SUGGESTION` or `NIT`; `rule` is an R-id, `conventions`, `architecture`, or `null`. `issue` and `fix` are the text of the row's Issue and Fix cells, with any `\|` a table cell needs written as `|`, each a non-empty string. `fix` is one or two sentences, since it is posted under Suggested fix. `line` and `start_line` come from the row's Location: `path:<n>` sets `line` to `<n>` and `start_line` to `null`, and `path:<start>-<end>` sets `start_line` to `<start>` and `line` to `<end>`, with `<start>` below `<end>`. `path` alone, or no location, sets both `line` and `start_line` to `null`. `also_affects` lists the other `path:line` or `path:<start>-<end>` locations the same problem appears at, and is `[]` when there are none. Every key is present. A finding has no `thread` and no `prompt`: `scripts/publish_review.py` renders each inline finding's prompt.
- `coverage` has one entry per Contract coverage row; `verdict` is `met`, `unmet` or `deferred`. It is `[]` without a plan.
- `history` has one entry per previous-round finding; `outcome` is `fixed`, `disputed` or `still_open`, and `now` is its new id when `still_open`, otherwise `null`. It is `[]` in round 1.
- `rejected` and `checks` mirror Rejected findings and Review coverage's checks; each is `[]` when empty.
- `.agentic/verdict.txt` holds exactly the `verdict` value and a trailing newline.
- `residual` stays `[]` when there is none.
